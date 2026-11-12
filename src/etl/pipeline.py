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

import json
import logging
import time
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from config.settings import PROJECT_ROOT, setup_logging
from src.db.models import DailyPrice
from src.db.session import check_connection, get_db, init_db
from src.db.upsert import upsert_rows
from src.etl.adjust import adjust_new_rows, rebuild_adjusted
from src.ingestion.benchmark import (
    clear_non_trading_days as clear_benchmark_non_trading_days,
)
from src.ingestion.benchmark import (
    load_benchmarks,
)
from src.ingestion.benchmark import (
    load_non_trading_days as load_benchmark_non_trading_days,
)
from src.ingestion.downloader import NSEBhavcopyDownloader
from src.ingestion.index_constituents import (
    NSEIndexConstituents,
    snapshot_index_constituents,
)

# ---------------------------------------------------------------------------
# Initialise centralized logging
# ---------------------------------------------------------------------------
setup_logging()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
RAW_DIR = PROJECT_ROOT / "data" / "raw"
INITIAL_DAYS_BACK = 1100  # ~3 year lookback on the very first run
DOWNLOAD_DELAY = 0.8  # polite gap between daily downloads
DB_BATCH_SIZE = 1000  # rows per INSERT batch


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
        cache_dir: Path | None = None,
    ) -> None:
        self.initial_days_back = initial_days_back
        self.download_delay = download_delay
        self.batch_size = batch_size
        self._downloader = NSEBhavcopyDownloader()
        self.cache_dir = (
            cache_dir
            if cache_dir is not None
            else getattr(self._downloader, "_cache_dir", RAW_DIR / "bhavcopy_cache")
        )

        # Ensure the bhavcopy cache directory exists
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        if isinstance(self.cache_dir, Path):
            self.cache_dir.mkdir(parents=True, exist_ok=True)

        target_cache = self.cache_dir if self.cache_dir is not None else RAW_DIR / "bhavcopy_cache"
        self.non_trading_path = target_cache / "non_trading_days.json"

        # Ensure DB tables exist if MySQL is reachable
        try:
            init_db()
        except Exception as exc:
            logger.warning("Database init_db skipped during ETL setup: %s", exc)

    RECENT_NON_TRADING_BUFFER_DAYS = 3  # Dates within this window are never cached as non-trading

    @classmethod
    def _is_recent_date(cls, d: date, ref_date: date | None = None) -> bool:
        """Check if date `d` is within the last 3 calendar days of `ref_date` (defaults to today).

        Dates within the last 3 calendar days must never be recorded as non-trading or skipped
        based on the cache, because NSE may publish Bhavcopy later in the day.
        """
        today = ref_date or date.today()
        return d >= today - timedelta(days=cls.RECENT_NON_TRADING_BUFFER_DAYS)

    def get_non_trading_days(self) -> set[date]:
        """Load confirmed non-trading dates (holidays, 404s) from JSON cache."""
        if not self.non_trading_path.exists():
            return set()
        try:
            raw = json.loads(self.non_trading_path.read_text(encoding="utf-8"))
            return {date.fromisoformat(d) for d in raw}
        except Exception as exc:
            logger.warning("Could not read non_trading_days.json: %s", exc)
            return set()

    def record_non_trading_days(self, dates: set[date] | list[date]) -> None:
        """Atomically persist confirmed non-trading dates to JSON cache."""
        if not dates:
            return
        existing = self.get_non_trading_days()
        merged = existing.union(dates)
        iso_list = sorted(d.isoformat() for d in merged)
        tmp_path = self.non_trading_path.with_suffix(".json.tmp")
        try:
            self.non_trading_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(json.dumps(iso_list, indent=2), encoding="utf-8")
            tmp_path.replace(self.non_trading_path)
        except Exception as exc:
            logger.warning("Could not write non_trading_days.json: %s", exc)
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    def clear_non_trading_days(self, start: date, end: date) -> list[date]:
        """Atomically remove cached non-trading dates in [start, end] from JSON cache.

        Returns the list of removed dates.
        """
        existing = self.get_non_trading_days()
        to_remove = {d for d in existing if start <= d <= end}
        if not to_remove:
            return []
        remaining = existing - to_remove
        iso_list = sorted(d.isoformat() for d in remaining)
        tmp_path = self.non_trading_path.with_suffix(".json.tmp")
        try:
            self.non_trading_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(json.dumps(iso_list, indent=2), encoding="utf-8")
            tmp_path.replace(self.non_trading_path)
        except Exception as exc:
            logger.warning("Could not write non_trading_days.json: %s", exc)
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
        return sorted(to_remove)

    def get_stored_dates(self) -> set[date]:
        """Retrieve distinct dates already stored in daily_prices."""
        query = text("SELECT DISTINCT date FROM daily_prices")
        try:
            with get_db() as session:
                rows = session.execute(query).fetchall()
                result: set[date] = set()
                for r in rows:
                    if not r or not r[0]:
                        continue
                    val = r[0]
                    if isinstance(val, date):
                        result.add(val)
                    elif isinstance(val, str):
                        result.add(date.fromisoformat(val[:10]))
                    else:
                        result.add(date.fromisoformat(str(val)[:10]))
                return result
        except Exception as exc:
            logger.warning("Could not fetch stored dates from daily_prices: %s", exc)
            return set()

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

        # MySQL is the authority for "last loaded" when available.
        # Only fall back to the Parquet cache date when MySQL is empty or
        # offline — this prevents a desync where cached files exist but
        # were never upserted into MySQL.
        if db_last_stored is not None:
            last_stored = db_last_stored
        elif cache_last_stored is not None:
            last_stored = cache_last_stored
        else:
            last_stored = None

        if last_stored is None:
            start = today - timedelta(days=self.initial_days_back)
            logger.info("Mode: FIRST RUN  (no historical data found in DB or cache)")
            logger.info("Range: %s → %s  (%d days back)", start, today, self.initial_days_back)
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
    def _calendar_days(start: date, end: date):
        """Yield every calendar day (including weekends/holidays) in ``[start, end]``.

        NSE occasionally conducts special trading sessions on weekends or
        holidays (e.g. Diwali Muhurat trading on Sunday, Budget Day on Saturday,
        or disaster recovery live trading sessions). Probing every calendar day
        ensures these special bars are captured, while ordinary non-trading
        weekends and holidays return 404 and are cached in non_trading_days.json.
        """
        cur = start
        while cur <= end:
            yield cur
            cur += timedelta(days=1)

    # Backward-compatibility alias
    _weekdays = _calendar_days

    def collect_new_rows(
        self,
        start: date,
        end: date,
        symbol_filter: set[str] | None = None,
        symbol_index_map: dict[str, str] | None = None,
        dates: Sequence[date] | None = None,
    ) -> dict[str, list[dict]]:
        """Download Bhavcopy files for candidate calendar days and bucket rows by symbol.

        Parameters
        ----------
        start, end:
            Inclusive date range to scan.
        symbol_filter:
            Optional set of symbols to keep. When ``None`` all EQ-series
            stocks are retained.
        symbol_index_map:
            Optional mapping from symbol to index name ("NIFTY 100", etc.).
        dates:
            Optional explicit sequence of dates to process. When omitted,
            all calendar days in ``[start, end]`` are probed.

        Returns
        -------
        ``{symbol: [row_dict, …]}`` where each dict has keys
        ``date, symbol, open, high, low, close, volume, index_name``.
        """
        # Weekends are probed because NSE holds special sessions (e.g. Diwali Muhurat, Budget day).
        all_dates = list(dates) if dates is not None else list(self._calendar_days(start, end))
        total = len(all_dates)
        stock_rows: dict[str, list[dict]] = {}
        trading_days = 0

        if not all_dates:
            logger.info("No candidate calendar days to process in range %s → %s.", start, end)
            return stock_rows

        non_trading_days = self.get_non_trading_days()
        logger.info("Scanning %d potential trading days (%s → %s) …", total, start, end)

        for idx, d in enumerate(all_dates, 1):
            # Skip known non-trading days cached from previous 404/holiday checks,
            # but never skip a date within the last 3 calendar days (NSE may publish late).
            if d in non_trading_days and not self._is_recent_date(d):
                logger.debug("  [%3d/%d]  %s  — skipped (cached non-trading day)", idx, total, d)
                continue

            try:
                df = self._downloader.download_bhavcopy(d)
            except Exception as exc:
                # Network / server errors must NOT be recorded as non-trading days
                logger.error("Failed to download Bhavcopy for %s: %s", d, exc)
                continue

            if idx % 50 == 0 or idx == total:
                logger.info(
                    "  Backfill progress: processed %d / %d days (%.1f%%)",
                    idx,
                    total,
                    (idx / total) * 100,
                )

            if df is None or df.empty:
                logger.debug("  [%3d/%d]  %s  — holiday / weekend / 404", idx, total, d)
                # Never record a date within the last 3 calendar days as non-trading
                if not self._is_recent_date(d):
                    self.record_non_trading_days([d])
                    non_trading_days.add(d)
                else:
                    logger.debug(
                        "  [%3d/%d]  %s  — within last 3 days; not recording as non-trading",
                        idx,
                        total,
                        d,
                    )
                time.sleep(0.05)
                continue

            trading_days += 1

            # Optionally filter to a known symbol universe
            if symbol_filter is not None:
                df = df[df["Symbol"].isin(symbol_filter)]

            records = df.to_dict("records")
            for row in records:
                sym = row["Symbol"]
                if sym not in stock_rows:
                    stock_rows[sym] = []
                idx_name = symbol_index_map.get(sym) if symbol_index_map else None
                stock_rows[sym].append(
                    {
                        "symbol": sym,
                        "date": d,
                        "open": row.get("Open"),
                        "high": row.get("High"),
                        "low": row.get("Low"),
                        "close": row.get("Close"),
                        "prev_close": row.get("PrevClose"),
                        "volume": row.get("Volume"),
                        "index_name": idx_name,
                    }
                )

            logger.info("  [%3d/%d]  %s  ✓  (%d rows)", idx, total, d, len(df))
            time.sleep(self.download_delay)

        logger.info("%d trading days processed.", trading_days)
        return stock_rows

    # ------------------------------------------------------------------
    # Step 3 — Load to DB (upsert)
    # ------------------------------------------------------------------
    def load_to_db(
        self,
        stock_rows: dict[str, list[dict]],
    ) -> tuple[list[str], list[str]]:
        """Batch-insert rows into ``daily_prices`` with upsert semantics.

        Uses dialect-agnostic upsert (MySQL ON DUPLICATE KEY UPDATE,
        SQLite ON CONFLICT DO UPDATE) so re-runs never violate the
        ``(symbol, date)`` unique constraint.

        Returns
        -------
        ``(success_symbols, failed_symbols)``
        """
        success: list[str] = []
        failed: list[str] = []
        total_inserted = 0

        logger.info("Loading data into DB (daily_prices) …")

        if not stock_rows:
            logger.warning("No rows to load.")
            return success, failed

        total_rows = sum(len(v) for v in stock_rows.values())
        logger.info("  Total rows to upsert: %d across %d symbols", total_rows, len(stock_rows))

        def _upsert_batch(session, batch: list[dict]) -> None:
            """Execute a single dialect-agnostic upsert batch."""
            upsert_rows(session, DailyPrice, batch, index_elements=["symbol", "date"])

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
            if any(
                k in err_msg
                for k in [
                    "uninitialized",
                    "can't connect",
                    "connection refused",
                    "operation not permitted",
                    "2003",
                ]
            ):
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
            len(success),
            len(failed),
        )
        return sorted(success), sorted(failed)

    # ------------------------------------------------------------------
    # Orchestrator
    # ------------------------------------------------------------------
    def run(
        self,
        symbol_filter: set[str] | None = None,
        symbol_index_map: dict[str, str] | None = None,
        skip_constituent_filter: bool = False,
        backfill: bool = False,
        chunk_days: int = 100,
    ) -> tuple[list[str], list[str]]:
        """Execute the ETL pipeline.

        If `backfill=True`, executes backwards backfill mode scanning missing
        historical dates in chunked batches. Otherwise executes standard update.
        """
        if backfill:
            return self.run_backfill(
                chunk_days=chunk_days,
                symbol_filter=symbol_filter,
                symbol_index_map=symbol_index_map,
                skip_constituent_filter=skip_constituent_filter,
            )

        # 1. Decide the date window first (queries MySQL & local cache)
        fetch_start, fetch_end, is_update = self.resolve_date_range()

        if fetch_start > fetch_end:
            logger.info(
                "All data is up to date (%s > %s). Nothing to fetch.", fetch_start, fetch_end
            )
            return [], []

        # 2. Build the symbol filter and index map (auto-fetch unless overridden)
        if symbol_filter is not None:
            logger.info(
                "Using caller-supplied symbol filter (%d symbols)",
                len(symbol_filter),
            )
            idx_map = symbol_index_map or {}
        elif skip_constituent_filter:
            logger.info("Constituent filter SKIPPED — all EQ stocks will be retained")
            symbol_filter = None
            idx_map = {}
        else:
            try:
                fetcher = NSEIndexConstituents()
                result = fetcher.fetch_all()
                symbol_filter = result.symbols
                idx_map = dict(result)
                logger.info(
                    "Auto-fetched constituent filter: %d symbols (Large Cap + Mid Cap + Small Cap)",
                    len(symbol_filter),
                )
                # Snapshot constituents to index_membership table
                try:
                    with get_db() as session:
                        snapshot_index_constituents(session, result.per_index, as_of_date=fetch_end)
                except Exception as snap_exc:
                    logger.warning("Could not snapshot index constituents: %s", snap_exc)
            except Exception as exc:
                logger.warning(
                    "Could not fetch index constituents (%s) — proceeding with all symbols",
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
            logger.info(
                "No new data found in range %s → %s (closed or current).",
                fetch_start,
                fetch_end,
            )
            return [], []

        # 3. Upsert into MySQL
        success, failed = self.load_to_db(stock_rows)

        if success:
            # 4. Incrementally adjust prices for newly loaded rows
            try:
                all_new_rows = [row for rows in stock_rows.values() for row in rows]
                new_df = pd.DataFrame(all_new_rows)
                adjust_new_rows(new_df)
            except Exception as adj_exc:
                logger.warning("Could not incrementally adjust new price rows: %s", adj_exc)

            # 5. Ingest benchmarks for newly downloaded dates
            try:
                all_dates = sorted({row["date"] for rows in stock_rows.values() for row in rows})
                if all_dates:
                    load_benchmarks(all_dates)
            except Exception as b_exc:
                logger.warning("Could not load benchmarks: %s", b_exc)

            # 6. Refresh pre-computed indicator cache
            try:
                from src.db.data_service import load_indicator_snapshot

                load_indicator_snapshot(use_adjusted=True, force_refresh=True)
            except Exception as cache_exc:
                logger.warning("Could not refresh indicator cache: %s", cache_exc)

        # 7. Summary
        logger.info("=" * 62)
        if is_update:
            logger.info(
                "  UPDATE COMPLETE  ✓ %d stocks loaded  |  ✗ %d failed",
                len(success),
                len(failed),
            )
        else:
            logger.info(
                "  FIRST RUN DONE   ✓ %d stocks loaded  |  ✗ %d failed",
                len(success),
                len(failed),
            )
        logger.info("=" * 62)

        return success, failed

    def run_backfill(
        self,
        days_back: int | None = None,
        chunk_days: int = 100,
        as_of_date: date | None = None,
        symbol_filter: set[str] | None = None,
        symbol_index_map: dict[str, str] | None = None,
        skip_constituent_filter: bool = False,
    ) -> tuple[list[str], list[str]]:
        """Backwards historical backfill in bounded chunks.

        Computes all candidate weekdays in [today - days_back, today],
        subtracts dates already stored in daily_prices and dates confirmed
        as non-trading (holidays/404), and downloads missing dates in chunks of
        `chunk_days`. After each chunk, rows are committed to the DB, ensuring
        bounded memory and safe resumes on failure.

        Post-backfill hooks (rebuild_adjusted, load_benchmarks, refresh_indicator_cache)
        are executed once at the end of the entire backfill.
        """
        lookback = days_back if days_back is not None else self.initial_days_back
        today = as_of_date or date.today()
        start = today - timedelta(days=lookback)

        logger.info(
            "Initializing backwards backfill (lookback=%d days, chunk_days=%d)",
            lookback,
            chunk_days,
        )

        # Probe every calendar day (including weekends/holidays) so special sessions
        # (e.g. Diwali Muhurat trading on Sunday, Budget Day Saturday) are ingested.
        all_candidate_days = list(self._calendar_days(start, today))
        stored_dates = self.get_stored_dates()
        non_trading_days = self.get_non_trading_days()

        # Never skip a date within the last 3 calendar days because of the cache.
        missing_dates = sorted(
            [
                d
                for d in all_candidate_days
                if d not in stored_dates and (self._is_recent_date(d) or d not in non_trading_days)
            ]
        )

        logger.info(
            "Backfill scan: %d total calendar days in window (%s -> %s). "
            "Already stored: %d, Known non-trading: %d, Missing to download: %d.",
            len(all_candidate_days),
            start,
            today,
            len(stored_dates),
            len(non_trading_days),
            len(missing_dates),
        )

        if not missing_dates:
            logger.info(
                "All dates in range are already stored or known non-trading. Nothing to backfill."
            )
            return [], []

        if symbol_filter is not None:
            idx_map = symbol_index_map or {}
        elif skip_constituent_filter:
            symbol_filter = None
            idx_map = {}
        else:
            try:
                fetcher = NSEIndexConstituents()
                result = fetcher.fetch_all()
                symbol_filter = result.symbols
                idx_map = dict(result)
                try:
                    with get_db() as session:
                        snapshot_index_constituents(session, result.per_index, as_of_date=today)
                except Exception as snap_exc:
                    logger.warning("Could not snapshot index constituents: %s", snap_exc)
            except Exception as exc:
                logger.warning(
                    "Could not fetch index constituents (%s) - proceeding with all symbols", exc
                )
                symbol_filter = None
                idx_map = {}

        all_success_symbols: set[str] = set()
        all_failed_symbols: set[str] = set()
        all_loaded_dates: set[date] = set()

        total_chunks = (len(missing_dates) + chunk_days - 1) // chunk_days

        for chunk_idx in range(total_chunks):
            chunk_slice = missing_dates[chunk_idx * chunk_days : (chunk_idx + 1) * chunk_days]
            logger.info(
                "--- Processing Backfill Chunk %d / %d (%d dates: %s -> %s) ---",
                chunk_idx + 1,
                total_chunks,
                len(chunk_slice),
                chunk_slice[0],
                chunk_slice[-1],
            )

            chunk_stock_rows: dict[str, list[dict]] = {}
            chunk_non_trading: set[date] = set()

            for d in chunk_slice:
                try:
                    df = self._downloader.download_bhavcopy(d)
                except Exception as dl_exc:
                    logger.error(
                        "Download failed for %s (network/error): %s - will retry on next run",
                        d,
                        dl_exc,
                    )
                    continue

                if df is None or df.empty:
                    logger.debug("%s is a confirmed holiday / non-trading date", d)
                    # Never record a date within the last 3 calendar days as non-trading
                    if not self._is_recent_date(d):
                        chunk_non_trading.add(d)
                    else:
                        logger.debug(
                            "%s is within last 3 days; skipping recording to non-trading cache", d
                        )
                    time.sleep(0.05)
                    continue

                all_loaded_dates.add(d)

                if symbol_filter is not None:
                    df = df[df["Symbol"].isin(symbol_filter)]

                records = df.to_dict("records")
                for row in records:
                    sym = row["Symbol"]
                    if sym not in chunk_stock_rows:
                        chunk_stock_rows[sym] = []
                    idx_name = idx_map.get(sym)
                    chunk_stock_rows[sym].append(
                        {
                            "symbol": sym,
                            "date": d,
                            "open": row.get("Open"),
                            "high": row.get("High"),
                            "low": row.get("Low"),
                            "close": row.get("Close"),
                            "prev_close": row.get("PrevClose"),
                            "volume": row.get("Volume"),
                            "index_name": idx_name,
                        }
                    )
                time.sleep(self.download_delay)

            if chunk_non_trading:
                self.record_non_trading_days(chunk_non_trading)

            if chunk_stock_rows:
                success, failed = self.load_to_db(chunk_stock_rows)
                all_success_symbols.update(success)
                all_failed_symbols.update(failed)
                logger.info(
                    "✓ Chunk %d/%d committed: %d symbols loaded (%d rows)",
                    chunk_idx + 1,
                    total_chunks,
                    len(success),
                    sum(len(v) for v in chunk_stock_rows.values()),
                )

        if all_loaded_dates and all_success_symbols:
            logger.info("Executing post-backfill rebuilds...")
            try:
                rebuild_adjusted()
            except Exception as adj_exc:
                logger.warning("Could not rebuild adjusted prices: %s", adj_exc)

            try:
                load_benchmarks(sorted(all_loaded_dates))
            except Exception as b_exc:
                logger.warning("Could not load benchmarks: %s", b_exc)

            try:
                from src.db.data_service import load_indicator_snapshot

                load_indicator_snapshot(use_adjusted=True, force_refresh=True)
            except Exception as cache_exc:
                logger.warning("Could not refresh indicator cache: %s", cache_exc)

        logger.info(
            "Backfill completed: %d total symbols loaded across %d new trading dates.",
            len(all_success_symbols),
            len(all_loaded_dates),
        )
        return sorted(all_success_symbols), sorted(all_failed_symbols)

    def run_incremental(self, lookback_days: int = 5) -> dict:
        """Incremental update trigger: fetches only the most recent Bhavcopy data.

        Returns a dictionary summary with counts and status.
        """
        # Pre-flight: check MySQL reachability *before* running so we can
        # distinguish "already up-to-date" from "database is offline" when
        # run() returns ([], []).
        db_online = check_connection()

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
                    "message": f"DB load failed for {len(failed)} symbols. Check logs.",
                    "symbols_updated": 0,
                    "failed_count": len(failed),
                }

            # No rows returned — use the pre-flight check to classify
            if not db_online:
                return {
                    "status": "offline",
                    "message": "Parquet cache current. MySQL offline — syncs when online.",
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

    def rebuild_adjusted_prices(self, symbols: list[str] | None = None) -> int:
        """Standalone helper to recompute corporate action adjustments."""
        return rebuild_adjusted(symbols=symbols)

    def recheck_non_trading(
        self,
        start: date,
        end: date,
        symbol_filter: set[str] | None = None,
        symbol_index_map: dict[str, str] | None = None,
        skip_constituent_filter: bool = False,
    ) -> tuple[list[str], list[str]]:
        """Remove cached non-trading days in [start, end] and re-probe them.

        Earlier runs may have cached a weekend or holiday special trading session
        as a non-trading day. This clears the cache for the date range and re-probes
        Bhavcopy data. Any special session data found is upserted into MySQL and
        incrementally price-adjusted. Ordinary non-trading days (404) are re-recorded.
        """
        cleared = self.clear_non_trading_days(start, end)
        try:
            clear_benchmark_non_trading_days(start, end)
        except Exception as bench_exc:
            logger.debug("Benchmark non-trading cache clear skipped: %s", bench_exc)

        logger.info(
            "Rechecking non-trading days in range %s → %s (cleared %d cached dates)",
            start,
            end,
            len(cleared),
        )

        stored_dates = self.get_stored_dates()
        dates_to_probe = [d for d in self._calendar_days(start, end) if d not in stored_dates]

        if not dates_to_probe:
            logger.info(
                "All dates in range %s → %s are already stored. Nothing to probe.", start, end
            )
            return [], []

        if symbol_filter is not None:
            idx_map = symbol_index_map or {}
        elif skip_constituent_filter:
            symbol_filter = None
            idx_map = {}
        else:
            try:
                fetcher = NSEIndexConstituents()
                result = fetcher.fetch_all()
                symbol_filter = result.symbols
                idx_map = dict(result)
            except Exception as exc:
                logger.warning(
                    "Could not fetch index constituents (%s) — proceeding with all symbols",
                    exc,
                )
                symbol_filter = None
                idx_map = {}

        stock_rows = self.collect_new_rows(
            start=start,
            end=end,
            symbol_filter=symbol_filter,
            symbol_index_map=idx_map,
            dates=dates_to_probe,
        )

        if not stock_rows:
            logger.info("No trading data found when re-checking range %s → %s.", start, end)
            return [], []

        success, failed = self.load_to_db(stock_rows)
        if success:
            try:
                # Rebuild corporate action adjustments across full history, ensuring historical
                # sessions inserted before a split/bonus receive correct cumulative factors.
                affected_symbols = sorted(stock_rows.keys())
                rebuild_adjusted(symbols=affected_symbols)
            except Exception as adj_exc:
                logger.warning("Could not rebuild adjusted prices for rechecked rows: %s", adj_exc)

        return success, failed

    def ingest_benchmarks(
        self,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> int:
        """Standalone helper to ingest benchmark index history.

        NSE occasionally conducts special trading sessions on weekends or
        holidays (e.g. Diwali Muhurat trading on Sunday, Budget Day on Saturday).
        Probing every calendar day ensures benchmark indices for these sessions
        are ingested, while missing index files (404s) are skipped and recorded
        in the benchmark non-trading days cache using existing benchmark helpers.
        """
        fetch_start = start_date or (date.today() - timedelta(days=self.initial_days_back))
        fetch_end = end_date or date.today()

        # Probe every calendar day (including weekends/holidays) so special sessions are included
        all_candidate_days = list(self._calendar_days(fetch_start, fetch_end))
        cached_non_trading = load_benchmark_non_trading_days()

        dates = [d for d in all_candidate_days if d.isoformat() not in cached_non_trading]

        logger.info(
            "Ingesting benchmark history for %d dates (%s -> %s)",
            len(dates),
            fetch_start,
            fetch_end,
        )
        return load_benchmarks(dates)
