"""
Bhavcopy ETL Pipeline
=====================
Orchestrates the end-to-end workflow:

1. **Resolve date range** — query the ``daily_prices`` MySQL table to find
   the earliest per-symbol maximum date.  If the table is empty, fall back
   to ``initial_days_back``.
2. **Collect new rows** — iterate over each trading day in the range,
   download the Bhavcopy via :class:`NSEBhavcopyDownloader`, and bucket
   rows by symbol.
3. **Load to DB** — batch-insert rows into ``daily_prices`` using
   ``INSERT … ON DUPLICATE KEY UPDATE`` for safe, idempotent upserts.

Parquet caching under ``data/raw/bhavcopy_cache/`` is preserved so that
repeated downloads for the same date are instant.
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.dialects.mysql import insert as mysql_insert

from config.settings import setup_logging, PROJECT_ROOT
from src.db.models import DailyPrice
from src.db.session import get_db, init_db, engine, check_connection
from src.ingestion.downloader import NSEBhavcopyDownloader
from src.ingestion.index_constituents import NSEIndexConstituents

# ---------------------------------------------------------------------------
# Initialise centralized logging
# ---------------------------------------------------------------------------
setup_logging()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
RAW_DIR = PROJECT_ROOT / "data" / "raw"
INITIAL_DAYS_BACK = 1100   # ~3 year lookback on the very first run
DOWNLOAD_DELAY = 0.8       # polite gap between daily downloads
DB_BATCH_SIZE = 1000       # rows per INSERT batch


class BhavcopyETL:
    """Full ingest-transform-load pipeline for NSE Bhavcopy data.

    Usage::

        etl = BhavcopyETL()
        etl.run()
    """

    def __init__(
        self,
        initial_days_back: int = INITIAL_DAYS_BACK,
        download_delay: float = DOWNLOAD_DELAY,
        batch_size: int = DB_BATCH_SIZE,
        cache_dir: Optional[Path] = None,
    ) -> None:
        self.initial_days_back = initial_days_back
        self.download_delay = download_delay
        self.batch_size = batch_size
        self._downloader = NSEBhavcopyDownloader()
        self.cache_dir = cache_dir if cache_dir is not None else getattr(self._downloader, "_cache_dir", RAW_DIR / "bhavcopy_cache")

        # Ensure the bhavcopy cache directory exists
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        if isinstance(self.cache_dir, Path):
            self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Ensure DB tables exist if MySQL is reachable
        try:
            init_db()
        except Exception as exc:
            logger.warning("Database init_db skipped during ETL setup: %s", exc)

    # ------------------------------------------------------------------
    # Step 1 — Resolve date range (MySQL-backed)
    # ------------------------------------------------------------------
    def resolve_date_range(self) -> tuple[date, date, bool]:
        """Decide which date window to fetch by querying MySQL.

        Executes::

            SELECT MIN(max_date) FROM (
                SELECT MAX(date) AS max_date
                FROM   daily_prices
                GROUP  BY symbol
            ) AS t

        Returns
        -------
        (fetch_start, fetch_end, is_update)

        * **First run** (table empty):
          ``fetch_start = today − initial_days_back``, ``is_update = False``.
        * **Update run**:
          ``fetch_start = last_stored + 1 day``, ``is_update = True``.

        Raises
        ------
        SystemExit
            If all data is already up-to-date.
        """
        today = date.today()

        query = text("""
            SELECT MAX(date) AS latest_date
            FROM   daily_prices
        """)

        db_last_stored = None
        try:
            with get_db() as session:
                row = session.execute(query).fetchone()
                db_last_stored = row[0] if row and row[0] else None
        except Exception as exc:
            logger.warning(
                "Could not query daily_prices — checking local parquet cache: %s",
                exc,
            )
            db_last_stored = None

        # Inspect local Parquet cache as fallback or offline telemetry
        cache_last_stored = None
        target_cache = self.cache_dir if self.cache_dir is not None else RAW_DIR / "bhavcopy_cache"
        if isinstance(target_cache, Path) and target_cache.exists():
            parquet_dates = []
            for f in target_cache.glob("*.parquet"):
                try:
                    parquet_dates.append(date.fromisoformat(f.stem))
                except ValueError:
                    continue
            if parquet_dates:
                cache_last_stored = max(parquet_dates)

        if cache_last_stored is not None:
            logger.info("Found latest date in local Parquet cache: %s", cache_last_stored)

        available_dates = [d for d in [db_last_stored, cache_last_stored] if d is not None]
        last_stored = max(available_dates) if available_dates else None

        if last_stored is None:
            start = today - timedelta(days=self.initial_days_back)
            logger.info("Mode: FIRST RUN  (no historical data found in DB or cache)")
            logger.info(
                "Range: %s → %s  (%d days back)", start, today, self.initial_days_back
            )
            return start, today, False

        start = last_stored + timedelta(days=1)

        if start > today:
            logger.info(
                "All data is already up to date (last date: %s). Nothing to fetch.",
                last_stored,
            )
            return start, today, True

        logger.info("Mode: UPDATE RUN  (last stored date: %s)", last_stored)
        logger.info("Fetching new days: %s → %s", start, today)
        return start, today, True

    # ------------------------------------------------------------------
    # Step 2 — Collect new rows
    # ------------------------------------------------------------------
    @staticmethod
    def _weekdays(start: date, end: date):
        """Yield every weekday (Mon–Fri) in ``[start, end]``."""
        cur = start
        while cur <= end:
            if cur.weekday() < 5:
                yield cur
            cur += timedelta(days=1)

    def collect_new_rows(
        self,
        start: date,
        end: date,
        symbol_filter: Optional[set[str]] = None,
        symbol_index_map: Optional[dict[str, str]] = None,
    ) -> dict[str, list[dict]]:
        """Download Bhavcopy files for each weekday and bucket rows by symbol.

        Parameters
        ----------
        start, end:
            Inclusive date range to scan.
        symbol_filter:
            Optional set of symbols to keep. When ``None`` all EQ-series
            stocks are retained.
        symbol_index_map:
            Optional mapping from symbol to index name ("NIFTY 100", etc.).

        Returns
        -------
        ``{symbol: [row_dict, …]}`` where each dict has keys
        ``date, symbol, open, high, low, close, volume, index_name``.
        """
        all_dates = list(self._weekdays(start, end))
        total = len(all_dates)
        stock_rows: dict[str, list[dict]] = {}
        trading_days = 0

        if not all_dates:
            logger.info("No trading weekdays to process in range %s → %s.", start, end)
            return stock_rows

        logger.info(
            "Scanning %d potential trading days (%s → %s) …", total, start, end
        )

        for idx, d in enumerate(all_dates, 1):
            try:
                df = self._downloader.download_bhavcopy(d)
            except Exception as exc:
                logger.error("Failed to download Bhavcopy for %s: %s", d, exc)
                continue

            if df is None or df.empty:
                logger.info("  [%3d/%d]  %s  — holiday / weekend", idx, total, d)
                time.sleep(0.2)
                continue

            trading_days += 1

            # Optionally filter to a known symbol universe
            if symbol_filter is not None:
                df = df[df["Symbol"].isin(symbol_filter)]

            for _, row in df.iterrows():
                sym = row["Symbol"]
                if sym not in stock_rows:
                    stock_rows[sym] = []
                idx_name = symbol_index_map.get(sym) if symbol_index_map else None
                stock_rows[sym].append(
                    {
                        "symbol":     sym,
                        "date":       d,
                        "open":       row.get("Open"),
                        "high":       row.get("High"),
                        "low":        row.get("Low"),
                        "close":      row.get("Close"),
                        "volume":     row.get("Volume"),
                        "index_name": idx_name,
                    }
                )

            logger.info(
                "  [%3d/%d]  %s  ✓  (%d rows)", idx, total, d, len(df)
            )
            time.sleep(self.download_delay)

        logger.info("%d trading days processed.", trading_days)
        return stock_rows

    # ------------------------------------------------------------------
    # Step 3 — Load to MySQL (upsert)
    # ------------------------------------------------------------------
    def load_to_db(
        self,
        stock_rows: dict[str, list[dict]],
    ) -> tuple[list[str], list[str]]:
        """Batch-insert rows into ``daily_prices`` with upsert semantics.

        Uses MySQL's ``INSERT … ON DUPLICATE KEY UPDATE`` via the
        SQLAlchemy MySQL dialect so re-runs never violate the
        ``(symbol, date)`` unique constraint.

        Returns
        -------
        ``(success_symbols, failed_symbols)``
        """
        success: list[str] = []
        failed: list[str] = []
        total_inserted = 0

        logger.info("Loading data into MySQL (daily_prices) …")

        if not stock_rows:
            logger.warning("No rows to load.")
            return success, failed

        total_rows = sum(len(v) for v in stock_rows.values())
        logger.info("  Total rows to upsert: %d across %d symbols", total_rows, len(stock_rows))

        def _upsert_batch(session, batch: list[dict]) -> None:
            """Execute a single INSERT … ON DUPLICATE KEY UPDATE batch."""
            stmt = mysql_insert(DailyPrice).values(batch)
            session.execute(
                stmt.on_duplicate_key_update(
                    open=stmt.inserted.open,
                    high=stmt.inserted.high,
                    low=stmt.inserted.low,
                    close=stmt.inserted.close,
                    volume=stmt.inserted.volume,
                    index_name=stmt.inserted.index_name,
                )
            )

        # --- Fast path: commit all symbols in one transaction ---
        all_rows: list[dict] = [r for rows in stock_rows.values() for r in rows]
        bulk_ok = False
        try:
            with get_db() as session:
                for batch_start in range(0, len(all_rows), self.batch_size):
                    _upsert_batch(session, all_rows[batch_start : batch_start + self.batch_size])
            success = sorted(stock_rows.keys())
            total_inserted = len(all_rows)
            bulk_ok = True
            logger.info("  ✓ %d rows upserted for %d symbols", total_inserted, len(success))
        except Exception as bulk_exc:
            err_msg = str(bulk_exc).lower()
            if any(k in err_msg for k in ["uninitialized", "can't connect", "connection refused", "operation not permitted", "2003"]):
                logger.warning(
                    "MySQL is unreachable — Bhavcopy data is preserved in local Parquet cache. "
                    "Skipping DB load. Rows will be upserted once MySQL is available.",
                )
                return [], []
            logger.warning(
                "Bulk upsert failed (%s) — retrying symbol-by-symbol for isolation.",
                bulk_exc,
            )

        if bulk_ok:
            return success, failed

        # --- Slow path: per-symbol isolation so one bad ticker doesn't block others ---
        for sym, rows in stock_rows.items():
            try:
                with get_db() as session:
                    for batch_start in range(0, len(rows), self.batch_size):
                        _upsert_batch(session, rows[batch_start : batch_start + self.batch_size])
                success.append(sym)
                total_inserted += len(rows)
                logger.debug("  ✓ %s — %d rows", sym, len(rows))
            except Exception as sym_exc:
                logger.error("  ✗ %s — skipped: %s", sym, sym_exc)
                failed.append(sym)

        logger.info(
            "  Symbol-isolated load complete: ✓ %d succeeded  ✗ %d failed",
            len(success), len(failed),
        )
        return sorted(success), sorted(failed)

    # ------------------------------------------------------------------
    # Orchestrator
    # ------------------------------------------------------------------
    def run(
        self,
        symbol_filter: Optional[set[str]] = None,
        symbol_index_map: Optional[dict[str, str]] = None,
        skip_constituent_filter: bool = False,
    ) -> tuple[list[str], list[str]]:
        """Execute the full ETL pipeline."""
        # 1. Decide the date window first (queries MySQL & local cache)
        fetch_start, fetch_end, is_update = self.resolve_date_range()

        if fetch_start > fetch_end:
            logger.info("All data is up to date (%s > %s). Nothing to fetch.", fetch_start, fetch_end)
            return [], []

        # 2. Build the symbol filter and index map (auto-fetch unless overridden)
        if symbol_filter is not None:
            logger.info(
                "Using caller-supplied symbol filter (%d symbols)",
                len(symbol_filter),
            )
            idx_map = symbol_index_map or {}
        elif skip_constituent_filter:
            logger.info(
                "Constituent filter SKIPPED — all EQ stocks will be retained"
            )
            symbol_filter = None
            idx_map = {}
        else:
            try:
                fetcher = NSEIndexConstituents()
                result = fetcher.fetch_all()
                symbol_filter = result.symbols
                idx_map = dict(result)
                logger.info(
                    "Auto-fetched constituent filter: %d symbols "
                    "(Large Cap + Mid Cap + Small Cap)",
                    len(symbol_filter),
                )
            except Exception as exc:
                logger.warning(
                    "Could not fetch index constituents (%s) — proceeding with all available symbols",
                    exc,
                )
                symbol_filter = None
                idx_map = {}

        logger.info("=" * 62)
        logger.info("  Fetch range    : %s → %s", fetch_start, fetch_end)
        logger.info(
            "  Symbol filter  : %s",
            f"{len(symbol_filter)} symbols" if symbol_filter else "ALL",
        )
        logger.info("  Persistence    : MySQL (daily_prices)")
        logger.info("=" * 62)

        # 2. Download daily Bhavcopy files and bucket rows by symbol
        stock_rows = self.collect_new_rows(
            fetch_start, fetch_end, symbol_filter=symbol_filter, symbol_index_map=idx_map
        )

        if not stock_rows:
            logger.info("No new trading day data found in range %s → %s (market closed or already current).", fetch_start, fetch_end)
            return [], []

        # 3. Upsert into MySQL
        success, failed = self.load_to_db(stock_rows)

        # 4. Summary
        logger.info("=" * 62)
        if is_update:
            logger.info(
                "  UPDATE COMPLETE  ✓ %d stocks loaded  |  ✗ %d failed",
                len(success), len(failed),
            )
        else:
            logger.info(
                "  FIRST RUN DONE   ✓ %d stocks loaded  |  ✗ %d failed",
                len(success), len(failed),
            )
        logger.info("=" * 62)

        return success, failed

    def run_incremental(self, lookback_days: int = 5) -> dict:
        """Incremental update trigger: fetches only the most recent Bhavcopy data.

        Returns a dictionary summary with counts and status.
        """
        orig_days_back = self.initial_days_back
        try:
            # For incremental updates with no prior data, limit scan window to lookback_days
            self.initial_days_back = lookback_days
            success, failed = self.run()

            # New data downloaded and successfully upserted into MySQL
            if success:
                return {
                    "status": "success",
                    "message": f"Successfully updated market data for {len(success)} symbols.",
                    "symbols_updated": len(success),
                    "failed_count": len(failed),
                }

            # Load failed for some symbols but DB was reachable
            if failed:
                return {
                    "status": "error",
                    "message": f"Database load failed for {len(failed)} symbols. Check database connection/logs.",
                    "symbols_updated": 0,
                    "failed_count": len(failed),
                }

            # No rows: either up-to-date, holiday, or MySQL was unreachable (load_to_db returned ([], []))
            if not check_connection():
                return {
                    "status": "offline",
                    "message": "Local Parquet cache is current. MySQL is offline — data will sync when MySQL is available.",
                    "symbols_updated": 0,
                }
            return {
                "status": "up_to_date",
                "message": "Market data is already up to date with the latest trading session.",
                "symbols_updated": 0,
            }
        except Exception as exc:
            logger.error("Incremental update failed: %s", exc, exc_info=True)
            return {
                "status": "error",
                "message": str(exc),
                "symbols_updated": 0,
            }
        finally:
            self.initial_days_back = orig_days_back
