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
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from config.settings import PROJECT_ROOT, setup_logging
from src.db.data_service import get_universe_cap_buckets
from src.db.models import BenchmarkPrice, DailyPrice, StockMaster, SymbolAlias
from src.db.session import check_connection, get_db, init_db
from src.db.upsert import upsert_rows
from src.etl.adjust import adjust_new_rows, rebuild_adjusted
from src.ingestion.benchmark import (
    clear_non_trading_days as clear_benchmark_non_trading_days,
)
from src.ingestion.benchmark import (
    load_benchmarks,
    record_non_trading_day,
)
from src.ingestion.benchmark import (
    load_non_trading_days as load_benchmark_non_trading_days,
)
from src.ingestion.downloader import NSEBhavcopyDownloader, parse_trade_date
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


def create_github_issue(title: str, body: str, label: str = "needs-user-input") -> bool:
    """Create a GitHub issue via `gh issue create`.

    If `gh` CLI is unauthenticated or fails, writes to reports/issues_draft.md as fallback.
    """
    import shutil
    import subprocess

    gh_bin = shutil.which("gh")
    if gh_bin:
        try:
            # Check for existing issue with the same title
            search_proc = subprocess.run(
                [gh_bin, "issue", "list", "--state", "all", "--search", title],
                capture_output=True,
                text=True,
                check=False,
            )
            if title in search_proc.stdout:
                logger.info("GitHub issue already exists: %s", title)
                return True

            proc = subprocess.run(
                [gh_bin, "issue", "create", "--title", title, "--label", label, "--body", body],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode == 0:
                logger.info("Created GitHub issue: %s", proc.stdout.strip())
                return True
            logger.warning("gh issue create returned %d: %s", proc.returncode, proc.stderr)
        except Exception as exc:
            logger.warning("Failed to invoke gh CLI: %s", exc)

    draft_file = PROJECT_ROOT / "reports" / "issues_draft.md"
    draft_file.parent.mkdir(parents=True, exist_ok=True)
    with draft_file.open("a", encoding="utf-8") as f:
        f.write(f"\n\n# {title} [Label: {label}]\n\n{body}\n")
    logger.info("Drafted issue to %s", draft_file)
    return False


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

    @property
    def downloader(self) -> NSEBhavcopyDownloader:
        """Return the underlying downloader instance."""
        return self._downloader

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

    def _get_renamed_symbols(self, session: Session | None = None) -> set[str]:
        """Return set of symbols that have been renamed to prevent recreating old history."""

        def _query(s: Session) -> set[str]:
            renamed: set[str] = set()
            try:
                stmt1 = select(StockMaster.symbol).where(
                    (StockMaster.status == "renamed") | (StockMaster.renamed_to.is_not(None))
                )
                for sym in s.scalars(stmt1).all():
                    if sym:
                        renamed.add(str(sym).strip().upper())
            except Exception:
                pass
            try:
                stmt2 = select(SymbolAlias.old_symbol).where(
                    SymbolAlias.status.in_(["applied", "verified_exact", "verified_close"])
                )
                for sym in s.scalars(stmt2).all():
                    if sym:
                        renamed.add(str(sym).strip().upper())
            except Exception:
                pass
            return renamed

        if session is not None:
            return _query(session)
        try:
            if not check_connection():
                return set()
            with get_db() as s:
                return _query(s)
        except Exception:
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

    def get_stored_dates(self, session: Session | None = None) -> set[date]:
        """Retrieve distinct dates already stored in daily_prices."""
        query = text("SELECT DISTINCT date FROM daily_prices")

        def _fetch(s: Session) -> set[date]:
            rows = s.execute(query).fetchall()
            result: set[date] = set()
            for r in rows:
                if not r or not r[0]:
                    continue
                val = r[0]
                if isinstance(val, date) and not isinstance(val, pd.Timestamp):
                    result.add(val)
                elif isinstance(val, pd.Timestamp):
                    result.add(val.date())
                elif isinstance(val, str):
                    result.add(date.fromisoformat(val[:10]))
                else:
                    result.add(date.fromisoformat(str(val)[:10]))
            return result

        try:
            if session is not None:
                return _fetch(session)
            with get_db() as s:
                return _fetch(s)
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

            # Validation: ensure file's TradeDate matches candidate date d
            if "TradeDate" in df.columns:
                td_series = df["TradeDate"].dropna()
                if not td_series.empty:
                    file_td = parse_trade_date(td_series.iloc[0])
                    if file_td is not None and file_td != d:
                        logger.warning(
                            "collect_new_rows: Trade date mismatch for %s (file has %s) "
                            "— rejecting rows",
                            d,
                            file_td,
                        )
                        if not self._is_recent_date(d):
                            self.record_non_trading_days([d])
                            non_trading_days.add(d)
                        time.sleep(0.05)
                        continue

            trading_days += 1

            # Exclude symbols marked 'renamed' to prevent recreating old history
            renamed_symbols = self._get_renamed_symbols()
            if renamed_symbols and "Symbol" in df.columns:
                df = df[~df["Symbol"].astype(str).str.strip().str.upper().isin(renamed_symbols)]

            # Optionally filter to a known symbol universe
            if symbol_filter is not None:
                df = df[df["Symbol"].isin(symbol_filter)]

            records = df.to_dict("records")
            for row in records:
                sym = row["Symbol"]
                if sym not in stock_rows:
                    stock_rows[sym] = []
                idx_name = (symbol_index_map.get(sym) or "Unknown") if symbol_index_map else None
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
            universe_map = get_universe_cap_buckets()
            if universe_map:
                logger.info(
                    "Using stock_master universe filter (%d symbols)",
                    len(universe_map),
                )
                symbol_filter = set(universe_map.keys())
                idx_map = symbol_index_map or universe_map
            else:
                logger.warning(
                    "stock_master table is empty; falling back to NIFTY-constituent filter."
                )
                try:
                    fetcher = NSEIndexConstituents()
                    result = fetcher.fetch_all()
                    symbol_filter = result.symbols
                    idx_map = dict(result)
                    logger.info(
                        "Auto-fetched constituent filter: %d symbols (Large/Mid/Small Cap)",
                        len(symbol_filter),
                    )
                    # Snapshot constituents to index_membership table
                    try:
                        with get_db() as session:
                            snapshot_index_constituents(
                                session, result.per_index, as_of_date=fetch_end
                            )
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
            universe_map = get_universe_cap_buckets()
            if universe_map:
                logger.info(
                    "Using stock_master universe filter (%d symbols)",
                    len(universe_map),
                )
                symbol_filter = set(universe_map.keys())
                idx_map = symbol_index_map or universe_map
            else:
                logger.warning(
                    "stock_master table is empty; falling back to NIFTY-constituent filter."
                )
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

                # Exclude symbols marked 'renamed' to prevent recreating old history
                renamed_symbols = self._get_renamed_symbols()
                if renamed_symbols and "Symbol" in df.columns:
                    df = df[~df["Symbol"].astype(str).str.strip().str.upper().isin(renamed_symbols)]

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
            universe_map = get_universe_cap_buckets()
            if universe_map:
                logger.info(
                    "Using stock_master universe filter (%d symbols)",
                    len(universe_map),
                )
                symbol_filter = set(universe_map.keys())
                idx_map = symbol_index_map or universe_map
            else:
                logger.warning(
                    "stock_master table is empty; falling back to NIFTY-constituent filter."
                )
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

    # ------------------------------------------------------------------
    # Step 6 — Purge bogus duplicate sessions
    # ------------------------------------------------------------------
    def find_bogus_sessions(
        self,
        start: date | None = None,
        end: date | None = None,
        session: Session | None = None,
    ) -> list[dict]:
        """Identify stored trading dates that are duplicate copies of the previous session.

        A stored date is 'bogus' when >= 90% of the symbols it shares with the
        previous stored date have identical close AND identical volume (minimum 100
        common symbols). Genuine special sessions have different prices/volumes
        and are not flagged.

        Parameters
        ----------
        start, end:
            Optional date range bounds.
        session:
            Optional SQLAlchemy Session (e.g. for testing).

        Returns
        -------
        list of dict with keys:
            'date', 'weekday', 'pct_identical', 'common_count', 'identical_count', 'prev_date'
        """

        def _to_date(val: object) -> date:
            if isinstance(val, date) and not isinstance(val, pd.Timestamp):
                return val
            if isinstance(val, pd.Timestamp):
                return val.date()
            return date.fromisoformat(str(val).split("T")[0])

        def _execute(s: Session) -> list[dict]:
            stmt = select(DailyPrice.date).distinct().order_by(DailyPrice.date.asc())
            raw_dates = s.execute(stmt).scalars().all()
            all_stored_dates = [_to_date(d) for d in raw_dates]
            unique_stored_dates = sorted(set(all_stored_dates))
            if len(unique_stored_dates) < 2:
                return []

            date_to_idx = {d: idx for idx, d in enumerate(unique_stored_dates)}
            candidate_pairs: list[tuple[date, date]] = []
            for d in unique_stored_dates:
                if start and d < start:
                    continue
                if end and d > end:
                    continue
                idx = date_to_idx[d]
                if idx > 0:
                    prev_d = unique_stored_dates[idx - 1]
                    candidate_pairs.append((d, prev_d))

            if not candidate_pairs:
                return []

            needed_dates = sorted({d for pair in candidate_pairs for d in pair})
            day_data: dict[date, dict[str, tuple[float | None, int | None]]] = {
                d: {} for d in needed_dates
            }

            chunk_size = 50
            for i in range(0, len(needed_dates), chunk_size):
                chunk = needed_dates[i : i + chunk_size]
                q = select(
                    DailyPrice.symbol, DailyPrice.date, DailyPrice.close, DailyPrice.volume
                ).where(DailyPrice.date.in_(chunk))
                rows = s.execute(q).all()
                for sym, dt, c, v in rows:
                    dt_obj = _to_date(dt)
                    c_val = float(c) if c is not None else None
                    v_val = int(v) if v is not None else None
                    if dt_obj in day_data:
                        day_data[dt_obj][str(sym)] = (c_val, v_val)

            bogus: list[dict] = []
            for d, prev_d in candidate_pairs:
                curr_map = day_data.get(d, {})
                prev_map = day_data.get(prev_d, {})

                common_syms = set(curr_map.keys()) & set(prev_map.keys())
                if len(common_syms) < 100:
                    continue

                identical_count = 0
                for sym in common_syms:
                    c1, v1 = curr_map[sym]
                    c2, v2 = prev_map[sym]
                    if (
                        c1 is not None
                        and c2 is not None
                        and v1 is not None
                        and v2 is not None
                        and abs(c1 - c2) < 1e-4
                        and v1 == v2
                    ):
                        identical_count += 1

                pct_identical = (identical_count / len(common_syms)) * 100.0
                if pct_identical >= 90.0:
                    bogus.append(
                        {
                            "date": d,
                            "weekday": d.strftime("%A"),
                            "pct_identical": pct_identical,
                            "common_count": len(common_syms),
                            "identical_count": identical_count,
                            "prev_date": prev_d,
                        }
                    )

            return bogus

        if session is not None:
            return _execute(session)
        with get_db() as s:
            return _execute(s)

    def purge_bogus_sessions(
        self,
        start: date | None = None,
        end: date | None = None,
        dry_run: bool = True,
        confirm: bool = False,
        reports_dir: Path | None = None,
        benchmark_cache_dir: Path | None = None,
        session: Session | None = None,
    ) -> dict:
        """Detect and optionally purge bogus duplicated weekend/holiday sessions.

        Parameters
        ----------
        start, end:
            Optional date range bounds.
        dry_run:
            If True, only reports/bogus_sessions.csv is written; no data is deleted.
        confirm:
            Must be True (via --yes) to execute row deletion, cache purging,
            and adjusted price rebuild.
        reports_dir:
            Directory to write CSV reports to (defaults to PROJECT_ROOT / 'reports').
        session:
            Optional SQLAlchemy Session.
        """
        r_dir = reports_dir or (PROJECT_ROOT / "reports")
        r_dir.mkdir(parents=True, exist_ok=True)
        bogus_csv = r_dir / "bogus_sessions.csv"

        bogus_list = self.find_bogus_sessions(start=start, end=end, session=session)

        # Write reports/bogus_sessions.csv (date, weekday, %identical)
        df_bogus = pd.DataFrame(
            [
                {
                    "date": b["date"].isoformat(),
                    "weekday": b["weekday"],
                    "%identical": f"{b['pct_identical']:.2f}%",
                }
                for b in bogus_list
            ],
            columns=["date", "weekday", "%identical"],
        )
        df_bogus.to_csv(bogus_csv, index=False)
        logger.info("Wrote %d bogus sessions to %s", len(bogus_list), bogus_csv)

        for b in bogus_list:
            logger.info(
                "  %s (%s): %.2f%% identical (%d/%d common symbols) to %s",
                b["date"],
                b["weekday"],
                b["pct_identical"],
                b["identical_count"],
                b["common_count"],
                b["prev_date"],
            )

        if dry_run or not confirm:
            logger.info(
                "Dry-run mode: %d bogus sessions flagged. "
                "No database rows or caches were modified.",
                len(bogus_list),
            )
            return {
                "bogus_count": len(bogus_list),
                "purged_dates": [b["date"] for b in bogus_list],
                "dry_run": True,
                "report_file": str(bogus_csv),
            }

        if not bogus_list:
            logger.info("No bogus sessions to purge.")
            return {
                "bogus_count": 0,
                "purged_dates": [],
                "dry_run": False,
                "report_file": str(bogus_csv),
            }

        bogus_dates = [b["date"] for b in bogus_list]

        # Purge in one transaction
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        purged_backup_file = r_dir / f"purged_rows_{timestamp_str}.csv"

        def _apply_purge(active_session: Session) -> int:
            daily_rows = (
                active_session.execute(select(DailyPrice).where(DailyPrice.date.in_(bogus_dates)))
                .scalars()
                .all()
            )

            bench_rows = (
                active_session.execute(
                    select(BenchmarkPrice).where(BenchmarkPrice.date.in_(bogus_dates))
                )
                .scalars()
                .all()
            )

            backup_records = []
            for r in daily_rows:
                backup_records.append(
                    {
                        "table": "daily_prices",
                        "symbol": r.symbol,
                        "date": r.date.isoformat() if isinstance(r.date, date) else str(r.date),
                        "open": float(r.open) if r.open is not None else None,
                        "high": float(r.high) if r.high is not None else None,
                        "low": float(r.low) if r.low is not None else None,
                        "close": float(r.close) if r.close is not None else None,
                        "volume": int(r.volume) if r.volume is not None else None,
                        "prev_close": float(r.prev_close) if r.prev_close is not None else None,
                        "index_name": getattr(r, "index_name", None),
                        "adj_open": float(r.adj_open) if r.adj_open is not None else None,
                        "adj_high": float(r.adj_high) if r.adj_high is not None else None,
                        "adj_low": float(r.adj_low) if r.adj_low is not None else None,
                        "adj_close": float(r.adj_close) if r.adj_close is not None else None,
                        "adj_volume": int(r.adj_volume) if r.adj_volume is not None else None,
                    }
                )
            for b in bench_rows:
                backup_records.append(
                    {
                        "table": "benchmark_prices",
                        "symbol": b.index_code,
                        "date": b.date.isoformat() if isinstance(b.date, date) else str(b.date),
                        "open": None,
                        "high": None,
                        "low": None,
                        "close": float(b.close) if b.close is not None else None,
                        "volume": None,
                        "prev_close": None,
                        "index_name": None,
                        "adj_open": None,
                        "adj_high": None,
                        "adj_low": None,
                        "adj_close": None,
                        "adj_volume": None,
                    }
                )

            df_backup = pd.DataFrame(backup_records)
            df_backup.to_csv(purged_backup_file, index=False)
            logger.info(
                "Backed up %d deleted rows to %s",
                len(backup_records),
                purged_backup_file,
            )

            active_session.execute(delete(DailyPrice).where(DailyPrice.date.in_(bogus_dates)))
            active_session.execute(
                delete(BenchmarkPrice).where(BenchmarkPrice.date.in_(bogus_dates))
            )
            active_session.commit()
            return len(backup_records)

        if session is not None:
            total_backed = _apply_purge(session)
        else:
            with get_db() as s:
                total_backed = _apply_purge(s)

        # 3. Delete parquet cache files for each date
        target_cache = (
            self.cache_dir
            if self.cache_dir is not None
            else getattr(self._downloader, "_cache_dir", RAW_DIR / "bhavcopy_cache")
        )
        from src.ingestion import benchmark

        bench_cache = benchmark_cache_dir or benchmark.BENCHMARK_CACHE_DIR
        for d in bogus_dates:
            f = target_cache / f"{d.isoformat()}.parquet"
            f.unlink(missing_ok=True)
            bf = bench_cache / f"{d.isoformat()}.parquet"
            bf.unlink(missing_ok=True)

        # 4. Record dates in both non_trading_days caches
        self.record_non_trading_days(bogus_dates)
        for d in bogus_dates:
            record_non_trading_day(d)

        # 5. Run rebuild_adjusted for all symbols
        logger.info("Rebuilding adjusted prices after purging bogus sessions...")
        rebuild_adjusted(session=session)

        # 6. Refresh pre-computed indicator cache
        logger.info("Refreshing indicator cache after purging bogus sessions...")
        try:
            from src.db.data_service import load_indicator_snapshot

            load_indicator_snapshot(use_adjusted=True, force_refresh=True)
        except Exception as exc:
            logger.warning("Could not refresh indicator cache: %s", exc)

        logger.info("Purge completed successfully for %d dates.", len(bogus_dates))
        return {
            "bogus_count": len(bogus_dates),
            "purged_dates": bogus_dates,
            "dry_run": False,
            "backup_file": str(purged_backup_file),
            "report_file": str(bogus_csv),
            "deleted_rows": total_backed,
        }

    # ------------------------------------------------------------------
    # Step 6 — Universe Import (Stock Master)
    # ------------------------------------------------------------------
    def import_universe(
        self,
        file_path: Path | str,
        add_threshold: float = 500.0,
        keep_threshold: float = 490.0,
        dry_run: bool = True,
        session: Session | None = None,
        reports_dir: Path | None = None,
        overrides_path: Path | str | None = None,
    ) -> dict[str, Any]:
        """Import stock master and universe from an Excel file with hysteresis.

        Hysteresis Rules:
        - First import (stock_master empty): every stock in file with
          market_cap >= keep_threshold (default 490) becomes in_universe=True, status='active'.
          Stocks with market_cap < keep_threshold become in_universe=False,
          status='below_threshold'.
        - Later imports: a stock NOT yet in the universe is added only if
          market_cap >= add_threshold (default 500); a stock already in the universe stays
          while >= keep_threshold, else status='below_threshold', in_universe=False,
          flagged_on=today.
        - Symbols present in price history but missing from the file:
          status='not_in_file', in_universe=False.
        - Idempotent: re-running with the same file changes nothing.
        - Price rows are NEVER deleted when status changes.

        Writes reports/universe_import_report.csv: counts by status, sector, and cap_bucket;
        Excel symbols that never appear in any cached Bhavcopy; stored symbols not in file.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Universe file not found: {path}")

        df = pd.read_excel(path)
        if df.empty:
            raise ValueError(f"Universe Excel file is empty: {path}")

        cols_lower = {str(c).lower().strip(): c for c in df.columns}
        sym_key = next((k for k in cols_lower if k == "symbol" or "symbol" in k), None)
        name_key = next(
            (
                k
                for k in cols_lower
                if k in ("stock name", "stock_name", "company name", "company_name")
            ),
            None,
        )
        sector_key = next((k for k in cols_lower if k == "sector"), None)
        ind_key = next((k for k in cols_lower if k == "industry"), None)
        cap_key = next((k for k in cols_lower if k in ("marketcapname", "cap_bucket")), None)
        mcap_key = next(
            (k for k in cols_lower if "market_cap" in k or "market cap" in k or "m_cap" in k),
            None,
        )

        if not sym_key:
            raise ValueError(f"Could not find Symbol column in {path}. Columns: {list(df.columns)}")

        today = date.today()
        asof = today

        def _execute_import(s: Session) -> dict[str, Any]:
            existing_stocks = {sm.symbol: sm for sm in s.scalars(select(StockMaster)).all()}
            is_first_import = len(existing_stocks) == 0

            stored_symbols = set(s.scalars(select(DailyPrice.symbol).distinct()).all())

            excel_symbols: set[str] = set()
            planned_records: dict[str, dict[str, Any]] = {}

            for _, row in df.iterrows():
                raw_sym = row[cols_lower[sym_key]]
                if pd.isna(raw_sym):
                    continue
                sym = str(raw_sym).strip().upper()
                excel_symbols.add(sym)

                raw_name = row[cols_lower[name_key]] if name_key else sym
                company_name = str(raw_name).strip() if pd.notna(raw_name) else sym

                sector = (
                    str(row[cols_lower[sector_key]]).strip()
                    if sector_key and pd.notna(row[cols_lower[sector_key]])
                    else None
                )
                industry = (
                    str(row[cols_lower[ind_key]]).strip()
                    if ind_key and pd.notna(row[cols_lower[ind_key]])
                    else None
                )
                cap_bucket = (
                    str(row[cols_lower[cap_key]]).strip()
                    if cap_key and pd.notna(row[cols_lower[cap_key]])
                    else None
                )

                raw_mcap = row[cols_lower[mcap_key]] if mcap_key else 0.0
                try:
                    market_cap = float(raw_mcap) if pd.notna(raw_mcap) else 0.0
                except (ValueError, TypeError):
                    market_cap = 0.0

                prev = existing_stocks.get(sym)

                if is_first_import:
                    if market_cap >= keep_threshold:
                        in_universe = True
                        status = "active"
                        added_on = today
                        flagged_on = None
                    else:
                        in_universe = False
                        status = "below_threshold"
                        added_on = today
                        flagged_on = today
                else:
                    if prev is not None and prev.in_universe:
                        if market_cap >= keep_threshold:
                            in_universe = True
                            status = "active"
                            added_on = prev.added_on
                            flagged_on = None
                        else:
                            in_universe = False
                            status = "below_threshold"
                            added_on = prev.added_on
                            flagged_on = prev.flagged_on or today
                    else:
                        if market_cap >= add_threshold:
                            in_universe = True
                            status = "active"
                            added_on = today
                            flagged_on = None
                        else:
                            in_universe = False
                            status = "below_threshold"
                            added_on = prev.added_on if prev else today
                            flagged_on = prev.flagged_on if (prev and prev.flagged_on) else today

                planned_records[sym] = {
                    "symbol": sym,
                    "company_name": company_name,
                    "sector": sector,
                    "industry": industry,
                    "cap_bucket": cap_bucket,
                    "market_cap_cr": market_cap,
                    "market_cap_asof": asof,
                    "in_universe": in_universe,
                    "status": status,
                    "added_on": added_on,
                    "flagged_on": flagged_on,
                    "benchmark_index": prev.benchmark_index if prev else None,
                    "source": path.name,
                }

            # Symbols present in price history but missing from the file
            missing_stored = stored_symbols - excel_symbols
            for sym in missing_stored:
                prev = existing_stocks.get(sym)
                planned_records[sym] = {
                    "symbol": sym,
                    "company_name": prev.company_name if prev else sym,
                    "sector": prev.sector if prev else None,
                    "industry": prev.industry if prev else None,
                    "cap_bucket": prev.cap_bucket if prev else None,
                    "market_cap_cr": float(prev.market_cap_cr)
                    if prev and prev.market_cap_cr is not None
                    else None,
                    "market_cap_asof": prev.market_cap_asof if prev else None,
                    "in_universe": False,
                    "status": "not_in_file",
                    "added_on": prev.added_on if prev else today,
                    "flagged_on": prev.flagged_on if (prev and prev.flagged_on) else today,
                    "benchmark_index": prev.benchmark_index if prev else None,
                    "source": prev.source if prev else "price_history",
                }

            # Handle any stock in existing_stocks missing from Excel and price history
            for sym, prev in existing_stocks.items():
                if sym not in planned_records:
                    planned_records[sym] = {
                        "symbol": sym,
                        "company_name": prev.company_name,
                        "sector": prev.sector,
                        "industry": prev.industry,
                        "cap_bucket": prev.cap_bucket,
                        "market_cap_cr": float(prev.market_cap_cr)
                        if prev.market_cap_cr is not None
                        else None,
                        "market_cap_asof": prev.market_cap_asof,
                        "in_universe": False,
                        "status": "not_in_file",
                        "added_on": prev.added_on,
                        "flagged_on": prev.flagged_on or today,
                        "benchmark_index": prev.benchmark_index,
                        "source": prev.source,
                    }

            # Apply Universe Overrides (force_include / force_exclude)
            applied_overrides: list[dict[str, str]] = []
            target_overrides_path = Path(overrides_path) if overrides_path else None
            if target_overrides_path and target_overrides_path.exists():
                try:
                    df_overrides = pd.read_csv(target_overrides_path)
                    ov_cols = {str(c).lower().strip(): c for c in df_overrides.columns}
                    ov_sym_col = ov_cols.get("symbol")
                    ov_act_col = ov_cols.get("action")
                    ov_reason_col = ov_cols.get("reason")
                    ov_added_col = ov_cols.get("added_on")

                    if ov_sym_col and ov_act_col:
                        for _, ov_row in df_overrides.iterrows():
                            raw_osym = ov_row[ov_sym_col]
                            if pd.isna(raw_osym):
                                continue
                            osym = str(raw_osym).strip().upper()
                            action = str(ov_row[ov_act_col]).strip().lower()
                            reason = (
                                str(ov_row[ov_reason_col]).strip()
                                if ov_reason_col and pd.notna(ov_row[ov_reason_col])
                                else ""
                            )
                            ov_added_raw = (
                                str(ov_row[ov_added_col]).strip()
                                if ov_added_col and pd.notna(ov_row[ov_added_col])
                                else None
                            )
                            ov_added_date = today
                            if ov_added_raw:
                                try:
                                    ov_added_date = date.fromisoformat(ov_added_raw)
                                except ValueError:
                                    ov_added_date = today

                            if action == "force_include":
                                if osym not in planned_records:
                                    prev = existing_stocks.get(osym)
                                    planned_records[osym] = {
                                        "symbol": osym,
                                        "company_name": prev.company_name if prev else osym,
                                        "sector": prev.sector if prev else None,
                                        "industry": prev.industry if prev else None,
                                        "cap_bucket": (
                                            prev.cap_bucket
                                            if prev and prev.cap_bucket
                                            else "Unknown"
                                        ),
                                        "market_cap_cr": (
                                            float(prev.market_cap_cr)
                                            if prev and prev.market_cap_cr is not None
                                            else None
                                        ),
                                        "market_cap_asof": prev.market_cap_asof if prev else None,
                                        "in_universe": True,
                                        "status": "active",
                                        "added_on": prev.added_on if prev else ov_added_date,
                                        "flagged_on": None,
                                        "benchmark_index": prev.benchmark_index if prev else None,
                                        "source": "override",
                                    }
                                else:
                                    planned_records[osym]["in_universe"] = True
                                    planned_records[osym]["status"] = "active"
                                    planned_records[osym]["source"] = "override"
                                    if not planned_records[osym].get("cap_bucket"):
                                        planned_records[osym]["cap_bucket"] = "Unknown"
                                    planned_records[osym]["flagged_on"] = None
                                applied_overrides.append(
                                    {
                                        "symbol": osym,
                                        "action": action,
                                        "reason": reason,
                                    }
                                )
                            elif action == "force_exclude":
                                if osym in planned_records:
                                    planned_records[osym]["in_universe"] = False
                                    if planned_records[osym].get("status") == "active":
                                        planned_records[osym]["status"] = "below_threshold"
                                        planned_records[osym]["flagged_on"] = (
                                            planned_records[osym].get("flagged_on") or today
                                        )
                                    applied_overrides.append(
                                        {
                                            "symbol": osym,
                                            "action": action,
                                            "reason": reason,
                                        }
                                    )
                except Exception as ov_exc:
                    logger.warning(
                        "Error reading universe overrides from %s: %s",
                        target_overrides_path,
                        ov_exc,
                    )

            # Report data
            status_counts: dict[str, int] = {}
            for r in planned_records.values():
                st = r["status"]
                status_counts[st] = status_counts.get(st, 0) + 1

            cap_bucket_counts: dict[str, int] = {}
            for r in planned_records.values():
                cb = r["cap_bucket"] or "Unknown"
                cap_bucket_counts[cb] = cap_bucket_counts.get(cb, 0) + 1

            sector_counts: dict[str, int] = {}
            for r in planned_records.values():
                sec = r["sector"] or "Unknown"
                sector_counts[sec] = sector_counts.get(sec, 0) + 1

            # Excel symbols that never appear in any cached Bhavcopy
            bhav_symbols: set[str] = set()
            target_cache = (
                self.cache_dir
                if self.cache_dir is not None
                else getattr(self._downloader, "_cache_dir", RAW_DIR / "bhavcopy_cache")
            )
            if target_cache.exists():
                import pyarrow.parquet as pq

                for cf in target_cache.glob("*.parquet"):
                    try:
                        col = pq.read_table(cf, columns=["Symbol"]).column("Symbol")
                        bhav_symbols.update(col.to_pylist())
                    except Exception:
                        pass

            excel_not_in_bhav = sorted(excel_symbols - bhav_symbols) if bhav_symbols else []
            stored_not_in_file = sorted(
                sym
                for sym in missing_stored
                if planned_records.get(sym, {}).get("status") == "not_in_file"
            )

            r_dir = reports_dir or (PROJECT_ROOT / "reports")
            r_dir.mkdir(parents=True, exist_ok=True)
            report_csv = r_dir / "universe_import_report.csv"

            report_rows = []
            for st, cnt in sorted(status_counts.items()):
                report_rows.append(
                    {
                        "section": "status_counts",
                        "category": "status",
                        "name": st,
                        "count_or_value": str(cnt),
                        "notes": "",
                    }
                )
            for cb, cnt in sorted(cap_bucket_counts.items()):
                report_rows.append(
                    {
                        "section": "cap_bucket_counts",
                        "category": "cap_bucket",
                        "name": cb,
                        "count_or_value": str(cnt),
                        "notes": "",
                    }
                )
            for sec, cnt in sorted(sector_counts.items()):
                report_rows.append(
                    {
                        "section": "sector_counts",
                        "category": "sector",
                        "name": sec,
                        "count_or_value": str(cnt),
                        "notes": "",
                    }
                )
            for ov in applied_overrides:
                report_rows.append(
                    {
                        "section": "applied_overrides",
                        "category": ov["action"],
                        "name": ov["symbol"],
                        "count_or_value": ov["action"],
                        "notes": ov.get("reason", ""),
                    }
                )
            for sym in excel_not_in_bhav:
                report_rows.append(
                    {
                        "section": "excel_symbols_never_in_bhavcopy",
                        "category": "symbol_mismatch",
                        "name": sym,
                        "count_or_value": "",
                        "notes": "Never appeared in any cached Bhavcopy",
                    }
                )
            for sym in stored_not_in_file:
                report_rows.append(
                    {
                        "section": "stored_symbols_not_in_file",
                        "category": "not_in_file",
                        "name": sym,
                        "count_or_value": "",
                        "notes": "Present in price history but missing from Excel file",
                    }
                )

            df_report = pd.DataFrame(
                report_rows,
                columns=["section", "category", "name", "count_or_value", "notes"],
            )
            df_report.to_csv(report_csv, index=False)
            logger.info("Wrote universe import report to %s", report_csv)

            if not dry_run:
                records_list = list(planned_records.values())
                upsert_rows(s, StockMaster, records_list, index_elements=["symbol"])
                s.commit()
                logger.info(
                    "Saved %d stock_master records (active: %d, below: %d, not_in_file: %d)",
                    len(records_list),
                    status_counts.get("active", 0),
                    status_counts.get("below_threshold", 0),
                    status_counts.get("not_in_file", 0),
                )
            else:
                logger.info(
                    "[DRY RUN] Would save %d stock_master records "
                    "(active: %d, below: %d, not_in_file: %d)",
                    len(planned_records),
                    status_counts.get("active", 0),
                    status_counts.get("below_threshold", 0),
                    status_counts.get("not_in_file", 0),
                )

            return {
                "status": "success",
                "dry_run": dry_run,
                "total_records": len(planned_records),
                "active_count": status_counts.get("active", 0),
                "below_threshold_count": status_counts.get("below_threshold", 0),
                "not_in_file_count": status_counts.get("not_in_file", 0),
                "report_path": str(report_csv),
                "status_counts": status_counts,
                "sector_counts": sector_counts,
                "cap_bucket_counts": cap_bucket_counts,
                "excel_not_in_bhav_count": len(excel_not_in_bhav),
                "stored_not_in_file_count": len(stored_not_in_file),
                "applied_overrides_count": len(applied_overrides),
                "applied_overrides": applied_overrides,
            }

        if session is not None:
            return _execute_import(session)
        with get_db() as s:
            return _execute_import(s)

    # ------------------------------------------------------------------
    # Step 7 — Fill Missing Universe Symbols from Cache
    # ------------------------------------------------------------------
    def fill_missing_symbols(
        self,
        start: date | None = None,
        end: date | None = None,
        session: Session | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Fill missing universe symbols across stored trading dates from Bhavcopy cache.

        For every stored trading date in [start, end] and every universe symbol lacking a row,
        takes the row from data/raw/bhavcopy_cache/<date>.parquet.
        Cache files from before T64b have no TradeDate: they are accepted for stored dates.
        If the cache file is missing, it is downloaded with the validated downloader.
        Processes date by date, bulk upserts, resumable and idempotent.
        Symbols listed later (IPOs) simply have fewer rows.
        After all dates: rebuilds adjusted prices for the added symbols once, then refreshes
        the indicator cache.

        With dry_run=True nothing is written or downloaded: it only counts the rows that
        would be added, and the dates whose cache file is missing (those would need a
        download). Why: a preview must be safe to run on the real database (issue #46).
        """

        def _get_universe(s: Session) -> dict[str, str]:
            return get_universe_cap_buckets(session=s)

        def _get_alias_maps(s: Session) -> tuple[dict[str, list[str]], set[str]]:
            rev_map: dict[str, list[str]] = {}
            renamed_set: set[str] = set()
            try:
                stmt1 = select(StockMaster.symbol, StockMaster.renamed_to).where(
                    StockMaster.renamed_to.is_not(None)
                )
                for old_s, new_s in s.execute(stmt1).all():
                    if old_s and new_s:
                        old_str = str(old_s).strip().upper()
                        new_str = str(new_s).strip().upper()
                        rev_map.setdefault(new_str, []).append(old_str)
                        renamed_set.add(old_str)
            except Exception:
                pass
            try:
                stmt2 = select(SymbolAlias.old_symbol, SymbolAlias.new_symbol).where(
                    SymbolAlias.status.in_(["applied", "verified_exact", "verified_close"])
                )
                for old_s, new_s in s.execute(stmt2).all():
                    if old_s and new_s:
                        old_str = str(old_s).strip().upper()
                        new_str = str(new_s).strip().upper()
                        if old_str not in rev_map.get(new_str, []):
                            rev_map.setdefault(new_str, []).append(old_str)
                        renamed_set.add(old_str)
            except Exception:
                pass
            return rev_map, renamed_set

        if session is not None:
            universe_map = _get_universe(session)
            rev_alias_map, renamed_symbols = _get_alias_maps(session)
        else:
            with get_db() as s:
                universe_map = _get_universe(s)
                rev_alias_map, renamed_symbols = _get_alias_maps(s)

        universe_symbols = {s for s in universe_map if s not in renamed_symbols}

        if not universe_symbols:
            logger.warning(
                "No active universe symbols in stock_master. Run --import-universe first."
            )
            return {
                "status": "warning",
                "message": "No active universe symbols found",
                "dates_processed": 0,
                "rows_added": 0,
                "symbols_added": 0,
            }

        stored_dates = self.get_stored_dates(session=session)
        if start:
            stored_dates = [d for d in stored_dates if d >= start]
        if end:
            stored_dates = [d for d in stored_dates if d <= end]
        stored_dates = sorted(stored_dates)

        if not stored_dates:
            logger.info("No stored dates in range %s → %s to fill.", start, end)
            return {
                "status": "noop",
                "dates_processed": 0,
                "rows_added": 0,
                "symbols_added": 0,
            }

        logger.info(
            "Filling missing universe symbols across %d stored dates (%s → %s)...",
            len(stored_dates),
            stored_dates[0],
            stored_dates[-1],
        )

        all_added_symbols: set[str] = set()
        total_rows_added = 0
        dates_with_additions = 0
        dates_needing_download = 0

        target_cache = (
            self.cache_dir
            if self.cache_dir is not None
            else getattr(self._downloader, "_cache_dir", RAW_DIR / "bhavcopy_cache")
        )

        for idx, d in enumerate(stored_dates, 1):

            def _get_existing_syms(s: Session, current_d: date = d) -> set[str]:
                stmt = select(DailyPrice.symbol).where(DailyPrice.date == current_d)
                return set(s.scalars(stmt).all())

            if session is not None:
                existing_on_d = _get_existing_syms(session)
            else:
                with get_db() as s:
                    existing_on_d = _get_existing_syms(s)

            missing_on_d = universe_symbols - existing_on_d
            if not missing_on_d:
                continue

            cache_file = target_cache / f"{d.isoformat()}.parquet"
            if not cache_file.exists() and dry_run:
                # A dry run must not download (that would also write cache files).
                dates_needing_download += 1
                logger.info("[DRY RUN] Cache file missing for %s; a real run would download it.", d)
                continue
            if not cache_file.exists():
                logger.info(
                    "Cache file missing for stored date %s; downloading via downloader...",
                    d,
                )
                df_downloaded = self.downloader.fetch(d)
                if df_downloaded is None or df_downloaded.empty:
                    logger.warning("Could not download bhavcopy for stored date %s; skipping.", d)
                    continue

            try:
                import pyarrow.parquet as pq

                tbl = pq.read_table(cache_file)
                df_bhav = tbl.to_pandas()
            except Exception as exc:
                logger.warning("Failed to read parquet cache for %s (%s); skipping.", d, exc)
                continue

            cols_lower = {str(c).lower().strip(): c for c in df_bhav.columns}
            sym_col = cols_lower.get("symbol") or cols_lower.get("tckrsymb")
            if not sym_col:
                logger.warning("No Symbol column in cache file %s", cache_file)
                continue

            open_col = cols_lower.get("open")
            high_col = cols_lower.get("high")
            low_col = cols_lower.get("low")
            close_col = cols_lower.get("close")
            vol_col = cols_lower.get("volume")
            prev_col = cols_lower.get("prevclose") or cols_lower.get("prev_close")

            # Map symbols in bhavcopy file to their row dict
            bhav_by_sym: dict[str, Any] = {}
            for _, r in df_bhav.iterrows():
                s_val = r.get(sym_col)
                if pd.notna(s_val):
                    bhav_by_sym[str(s_val).strip().upper()] = r

            stock_rows: dict[str, list[dict]] = {}
            for sym in missing_on_d:
                target_row = bhav_by_sym.get(sym)
                if target_row is None:
                    # Check if symbol has an old alias in bhavcopy
                    for old_s in rev_alias_map.get(sym, []):
                        if old_s in bhav_by_sym:
                            target_row = bhav_by_sym[old_s]
                            break

                if target_row is not None:
                    open_val = target_row.get(open_col)
                    high_val = target_row.get(high_col)
                    low_val = target_row.get(low_col)
                    close_val = target_row.get(close_col)
                    vol_val = target_row.get(vol_col)
                    prev_close_val = target_row.get(prev_col)

                    row_dict = {
                        "symbol": sym,
                        "date": d,
                        "open": float(open_val) if pd.notna(open_val) else None,
                        "high": float(high_val) if pd.notna(high_val) else None,
                        "low": float(low_val) if pd.notna(low_val) else None,
                        "close": float(close_val) if pd.notna(close_val) else None,
                        "volume": int(vol_val) if pd.notna(vol_val) else None,
                        "prev_close": (float(prev_close_val) if pd.notna(prev_close_val) else None),
                        "index_name": universe_map.get(sym, "Unknown"),
                    }
                    stock_rows[sym] = [row_dict]
                    all_added_symbols.add(sym)

            if stock_rows and dry_run:
                total_rows_added += len(stock_rows)
                dates_with_additions += 1
                logger.info(
                    "  [%3d/%d]  %s  [DRY RUN] would fill %d symbols",
                    idx,
                    len(stored_dates),
                    d,
                    len(stock_rows),
                )
            elif stock_rows:
                if session is not None:
                    all_rows = [r for rows in stock_rows.values() for r in rows]
                    upsert_rows(session, DailyPrice, all_rows, index_elements=["symbol", "date"])
                    session.commit()
                    success_count = len(stock_rows)
                else:
                    success, _ = self.load_to_db(stock_rows)
                    success_count = len(success)

                total_rows_added += success_count
                dates_with_additions += 1
                logger.info(
                    "  [%3d/%d]  %s  ✓  Filled %d symbols",
                    idx,
                    len(stored_dates),
                    d,
                    success_count,
                )

        if dry_run:
            logger.info(
                "[DRY RUN] Nothing was written: %d rows would be added across %d symbols; "
                "%d dates would need a download.",
                total_rows_added,
                len(all_added_symbols),
                dates_needing_download,
            )
            return {
                "status": "dry_run",
                "dry_run": True,
                "dates_processed": len(stored_dates),
                "dates_with_additions": dates_with_additions,
                "dates_needing_download": dates_needing_download,
                "rows_added": total_rows_added,
                "symbols_added": len(all_added_symbols),
            }

        if all_added_symbols:
            logger.info(
                "Finished filling missing symbols. Rebuilding adjusted prices for %d symbols...",
                len(all_added_symbols),
            )
            self.rebuild_adjusted_prices(symbols=sorted(all_added_symbols))
            logger.info("Refreshing indicator cache...")
            from src.db.data_service import load_indicator_snapshot

            load_indicator_snapshot(use_adjusted=True, force_refresh=True)
            logger.info("Indicator cache refreshed successfully.")
        else:
            logger.info("No missing symbol rows were added across all stored dates.")

        return {
            "status": "success",
            "dates_processed": len(stored_dates),
            "dates_with_additions": dates_with_additions,
            "rows_added": total_rows_added,
            "symbols_added": len(all_added_symbols),
        }

    # ------------------------------------------------------------------
    # Step 8 — Import Symbol Aliases
    # ------------------------------------------------------------------
    def import_symbol_aliases(
        self,
        file_path: Path | str | None = None,
        session: Session | None = None,
    ) -> dict[str, Any]:
        """Import symbol aliases from CSV file into symbol_alias table.

        Expected columns: old_symbol, new_symbol, effective_date, status, implied_ratio, note.
        """
        target_path = (
            Path(file_path) if file_path else (PROJECT_ROOT / "config" / "symbol_aliases.csv")
        )
        if not target_path.exists():
            raise FileNotFoundError(f"Symbol aliases file not found: {target_path}")

        df = pd.read_csv(target_path)
        if df.empty:
            logger.warning("Symbol aliases file is empty: %s", target_path)
            return {"status": "success", "imported": 0, "records": []}

        cols_lower = {str(c).lower().strip(): c for c in df.columns}
        old_col = cols_lower.get("old_symbol") or cols_lower.get("old")
        new_col = cols_lower.get("new_symbol") or cols_lower.get("new")
        if not old_col or not new_col:
            raise ValueError(
                f"CSV must contain 'old_symbol' and 'new_symbol' columns: {list(df.columns)}"
            )

        eff_col = cols_lower.get("effective_date")
        st_col = cols_lower.get("status")
        ratio_col = cols_lower.get("implied_ratio")
        note_col = cols_lower.get("note")

        records: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            raw_old = row[old_col]
            raw_new = row[new_col]
            if pd.isna(raw_old) or pd.isna(raw_new):
                continue
            old_sym = str(raw_old).strip().upper()
            new_sym = str(raw_new).strip().upper()
            if not old_sym or not new_sym:
                continue

            eff_date = None
            if eff_col and pd.notna(row[eff_col]):
                try:
                    eff_date = date.fromisoformat(str(row[eff_col]).strip())
                except ValueError:
                    eff_date = None

            status_val = (
                str(row[st_col]).strip().lower()
                if st_col and pd.notna(row[st_col]) and str(row[st_col]).strip()
                else "candidate"
            )

            ratio_val = None
            if ratio_col and pd.notna(row[ratio_col]):
                try:
                    ratio_val = round(float(row[ratio_col]), 4)
                except (ValueError, TypeError):
                    ratio_val = None

            note_val = str(row[note_col]).strip() if note_col and pd.notna(row[note_col]) else None

            records.append(
                {
                    "old_symbol": old_sym,
                    "new_symbol": new_sym,
                    "effective_date": eff_date,
                    "status": status_val,
                    "implied_ratio": ratio_val,
                    "note": note_val,
                }
            )

        def _execute(s: Session) -> dict[str, Any]:
            if records:
                upsert_rows(s, SymbolAlias, records, index_elements=["old_symbol", "new_symbol"])
                s.commit()
            logger.info(
                "Imported %d symbol alias candidate records from %s", len(records), target_path
            )
            return {"status": "success", "imported": len(records), "records": records}

        if session is not None:
            return _execute(session)
        with get_db() as s:
            return _execute(s)

    # ------------------------------------------------------------------
    # Step 9 — Continuity Check for Symbol Aliases
    # ------------------------------------------------------------------
    def check_alias_continuity(
        self,
        old_symbol: str,
        new_symbol: str,
        cache_dir: Path | None = None,
    ) -> dict[str, Any]:
        """Verify continuity for a symbol alias using RAW cache values (read-only).

        Continuity rules:
        - overlap: both symbols have volume > 0 on same date with different prices (>0.05).
        - date_gap: OLD has no row on last trading day before NEW's first date.
        - verified_exact: |NEW.first_prev_close - OLD.close_that_day| <= 0.05.
        - verified_close: within 1% of unity.
        - ratio_mismatch: otherwise.
        """
        target_cache = (
            Path(cache_dir)
            if cache_dir is not None
            else Path(self.cache_dir if self.cache_dir is not None else RAW_DIR / "bhavcopy_cache")
        )
        cache_files = sorted(target_cache.glob("*.parquet"))
        if not cache_files:
            return {
                "classification": "date_gap",
                "status": "date_gap",
                "implied_ratio": None,
                "old_symbol": old_symbol,
                "new_symbol": new_symbol,
                "reason": "No cache files found",
            }

        all_trading_dates = [date.fromisoformat(f.stem) for f in cache_files]
        symbols_to_find = {old_symbol.strip().upper(), new_symbol.strip().upper()}
        old_sym = old_symbol.strip().upper()
        new_sym = new_symbol.strip().upper()

        old_hist: dict[date, dict[str, Any]] = {}
        new_hist: dict[date, dict[str, Any]] = {}

        import pyarrow.parquet as pq

        for f in cache_files:
            d = date.fromisoformat(f.stem)
            try:
                tbl = pq.read_table(f)
                df_bhav = tbl.to_pandas()
            except Exception:
                continue

            cols_lower = {str(c).lower().strip(): c for c in df_bhav.columns}
            sym_col = cols_lower.get("symbol") or cols_lower.get("tckrsymb")
            if not sym_col:
                continue

            sym_series = df_bhav[sym_col].astype(str).str.strip().str.upper()
            matching = df_bhav[sym_series.isin(symbols_to_find)]
            if matching.empty:
                continue

            close_col = cols_lower.get("close") or cols_lower.get("clgprc")
            vol_col = cols_lower.get("volume") or cols_lower.get("ttltrdqty")
            prev_col = (
                cols_lower.get("prevclose")
                or cols_lower.get("prev_close")
                or cols_lower.get("pvsclgprc")
            )
            open_col = cols_lower.get("open") or cols_lower.get("opnprc")
            high_col = cols_lower.get("high") or cols_lower.get("hghprc")
            low_col = cols_lower.get("low") or cols_lower.get("lwprc")

            for _, r in matching.iterrows():
                s_name = str(r[sym_col]).strip().upper()
                c_val = r.get(close_col)
                v_val = r.get(vol_col)
                p_val = r.get(prev_col)
                o_val = r.get(open_col)
                h_val = r.get(high_col)
                l_val = r.get(low_col)

                row_dict = {
                    "open": float(o_val) if pd.notna(o_val) else None,
                    "high": float(h_val) if pd.notna(h_val) else None,
                    "low": float(l_val) if pd.notna(l_val) else None,
                    "close": float(c_val) if pd.notna(c_val) else None,
                    "prev_close": float(p_val) if pd.notna(p_val) else None,
                    "volume": int(v_val) if pd.notna(v_val) else 0,
                }
                if s_name == old_sym:
                    old_hist[d] = row_dict
                elif s_name == new_sym:
                    new_hist[d] = row_dict

        # 1. Overlap check: both symbols have volume > 0 on same date with different prices
        common_dates = set(old_hist.keys()) & set(new_hist.keys())
        for cd in common_dates:
            old_vol = old_hist[cd].get("volume") or 0
            new_vol = new_hist[cd].get("volume") or 0
            old_close = old_hist[cd].get("close")
            new_close = new_hist[cd].get("close")
            if (
                old_vol > 0
                and new_vol > 0
                and old_close is not None
                and new_close is not None
                and abs(float(old_close) - float(new_close)) > 0.05
            ):
                return {
                    "classification": "overlap",
                    "status": "overlap",
                    "implied_ratio": None,
                    "old_symbol": old_sym,
                    "new_symbol": new_sym,
                    "overlap_date": cd,
                    "reason": (
                        f"Both symbols trade on {cd} with volume > 0 and different "
                        f"prices: {old_sym}={old_close}, {new_sym}={new_close}"
                    ),
                    "old_hist": old_hist,
                    "new_hist": new_hist,
                }

        # 2. Check new symbol exists in cache
        if not new_hist:
            return {
                "classification": "date_gap",
                "status": "date_gap",
                "implied_ratio": None,
                "old_symbol": old_sym,
                "new_symbol": new_sym,
                "reason": f"New symbol {new_sym} has no rows in cache",
                "old_hist": old_hist,
                "new_hist": new_hist,
            }

        new_dates = sorted(new_hist.keys())
        new_first_date = new_dates[0]
        new_first_row = new_hist[new_first_date]
        first_prev_close = new_first_row.get("prev_close")

        # Trading days before new_first_date across cache
        trading_dates_before = [d for d in all_trading_dates if d < new_first_date]
        if not trading_dates_before:
            return {
                "classification": "date_gap",
                "status": "date_gap",
                "implied_ratio": None,
                "old_symbol": old_sym,
                "new_symbol": new_sym,
                "reason": f"No trading day found before {new_first_date}",
                "old_hist": old_hist,
                "new_hist": new_hist,
            }
        prev_trading_day = trading_dates_before[-1]

        # 3. Check old symbol has a row on that trading day
        if prev_trading_day not in old_hist:
            return {
                "classification": "date_gap",
                "status": "date_gap",
                "implied_ratio": None,
                "old_symbol": old_sym,
                "new_symbol": new_sym,
                "prev_trading_day": prev_trading_day,
                "new_first_date": new_first_date,
                "reason": (
                    f"Old symbol {old_sym} has no row on trading day {prev_trading_day} "
                    f"before {new_first_date}"
                ),
                "old_hist": old_hist,
                "new_hist": new_hist,
            }

        # 4. Compare prices
        old_close = old_hist[prev_trading_day].get("close")
        if first_prev_close is None or old_close is None or float(old_close) <= 0:
            return {
                "classification": "ratio_mismatch",
                "status": "ratio_mismatch",
                "implied_ratio": None,
                "old_symbol": old_sym,
                "new_symbol": new_sym,
                "prev_trading_day": prev_trading_day,
                "new_first_date": new_first_date,
                "old_close": old_close,
                "new_prev_close": first_prev_close,
                "reason": "Missing or non-positive close/prev_close price",
                "old_hist": old_hist,
                "new_hist": new_hist,
            }

        old_close_f = float(old_close)
        first_prev_close_f = float(first_prev_close)
        diff = abs(first_prev_close_f - old_close_f)
        implied_ratio = round(first_prev_close_f / old_close_f, 4)

        if diff <= 0.05:
            classification = "verified_exact"
        elif abs(implied_ratio - 1.0) <= 0.01:
            classification = "verified_close"
        else:
            classification = "ratio_mismatch"

        return {
            "classification": classification,
            "status": classification,
            "implied_ratio": implied_ratio,
            "old_symbol": old_sym,
            "new_symbol": new_sym,
            "old_close": old_close_f,
            "new_prev_close": first_prev_close_f,
            "diff": diff,
            "prev_trading_day": prev_trading_day,
            "new_first_date": new_first_date,
            "old_hist": old_hist,
            "new_hist": new_hist,
        }

    # ------------------------------------------------------------------
    # Step 10 — Apply Symbol Aliases
    # ------------------------------------------------------------------
    def apply_symbol_aliases(
        self,
        dry_run: bool = True,
        session: Session | None = None,
        reports_dir: Path | None = None,
        alias_filter: list[tuple[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Apply verified symbol renames in one transaction.

        For each alias:
        1. Continuity check (read-only from raw cache).
        2. If verified (exact or close):
           - Back up moved rows to reports/
           - Move daily_prices rows from OLD to NEW
           - Insert cached OLD rows under NEW if not in DB
           - Fill NEW's own missing dates from cache
           - Copy sector/industry/cap_bucket to NEW if missing
           - Set stock_master OLD to status 'renamed' with renamed_to=NEW, in_universe=False
           - Run rebuild_adjusted for NEW
           - Refresh indicator cache
        3. If ratio_mismatch: do not merge; print ratio and file issue labelled needs-user-input.
        """
        r_dir = reports_dir or (PROJECT_ROOT / "reports")
        r_dir.mkdir(parents=True, exist_ok=True)

        def _execute(s: Session) -> dict[str, Any]:
            if alias_filter is not None:
                aliases_to_check = alias_filter
            else:
                stmt = select(SymbolAlias.old_symbol, SymbolAlias.new_symbol).order_by(
                    SymbolAlias.old_symbol.asc()
                )
                aliases_to_check = [(str(r[0]), str(r[1])) for r in s.execute(stmt).all()]

            if not aliases_to_check:
                logger.info("No symbol aliases to apply.")
                return {
                    "status": "noop",
                    "dry_run": dry_run,
                    "applied_count": 0,
                    "summaries": [],
                }

            logger.info(
                "Checking and applying %d symbol aliases (dry_run=%s)...",
                len(aliases_to_check),
                dry_run,
            )

            summaries = []
            applied_count = 0

            for old_sym, new_sym in aliases_to_check:
                old_sym = old_sym.strip().upper()
                new_sym = new_sym.strip().upper()

                cont = self.check_alias_continuity(old_sym, new_sym)
                classification = cont["classification"]
                implied_ratio = cont.get("implied_ratio")

                # Update status in symbol_alias table
                alias_row = s.get(SymbolAlias, (old_sym, new_sym))
                if alias_row is not None:
                    alias_row.status = classification
                    alias_row.implied_ratio = implied_ratio
                    if cont.get("new_first_date"):
                        alias_row.effective_date = cont["new_first_date"]

                if classification == "ratio_mismatch":
                    logger.warning(
                        "Alias %s -> %s: RATIO MISMATCH detected (implied_ratio=%s, old_close=%s, "
                        "new_prev_close=%s). Merge rejected.",
                        old_sym,
                        new_sym,
                        implied_ratio,
                        cont.get("old_close"),
                        cont.get("new_prev_close"),
                    )
                    issue_title = (
                        f"Symbol alias ratio mismatch for {old_sym} -> {new_sym} "
                        f"(ratio {implied_ratio})"
                    )
                    issue_body = (
                        "## Context\n"
                        "Command: run_pipeline.py --apply-symbol-aliases\n"
                        f"Alias: {old_sym} -> {new_sym}\n\n"
                        "## What happened\n"
                        "The continuity check failed with ratio_mismatch:\n"
                        f"- Old close on {cont.get('prev_trading_day')}: {cont.get('old_close')}\n"
                        f"- New first prev_close on {cont.get('new_first_date')}: "
                        f"{cont.get('new_prev_close')}\n"
                        f"- Implied ratio: {implied_ratio}\n\n"
                        "## Expected\n"
                        "Owner decision on corporate action ratio before merging.\n\n"
                        "## Impact\n"
                        f"Symbols {old_sym} and {new_sym} remain unmerged to avoid distortion.\n\n"
                        "## Suggested fix\n"
                        f"Verify corporate action adjustment factor for {old_sym} -> {new_sym}."
                    )
                    create_github_issue(issue_title, issue_body, label="needs-user-input")

                if classification not in ("verified_exact", "verified_close"):
                    logger.info(
                        "Skipping alias %s -> %s (classification=%s, implied_ratio=%s)",
                        old_sym,
                        new_sym,
                        classification,
                        implied_ratio,
                    )
                    summaries.append(
                        {
                            "old_symbol": old_sym,
                            "new_symbol": new_sym,
                            "classification": classification,
                            "implied_ratio": implied_ratio,
                            "rows_moved": 0,
                            "rows_added": 0,
                            "status": "rejected",
                        }
                    )
                    continue

                # Verified: collect old rows from DB and cache
                old_db_rows = s.scalars(
                    select(DailyPrice)
                    .where(DailyPrice.symbol == old_sym)
                    .order_by(DailyPrice.date.asc())
                ).all()

                old_hist = cont.get("old_hist", {})
                new_hist = cont.get("new_hist", {})

                old_dates = sorted({r.date for r in old_db_rows} | set(old_hist.keys()))
                old_range = f"{old_dates[0]} to {old_dates[-1]}" if old_dates else "None"

                new_db_dates = set(
                    s.scalars(select(DailyPrice.date).where(DailyPrice.symbol == new_sym)).all()
                )
                all_new_dates = sorted(new_db_dates | set(new_hist.keys()) | set(old_dates))
                new_range = (
                    f"{all_new_dates[0]} to {all_new_dates[-1]}" if all_new_dates else "None"
                )

                if dry_run:
                    rows_moved = len(old_db_rows)
                    rows_to_add = len(
                        set(old_hist.keys()) - {r.date for r in old_db_rows} - new_db_dates
                    )
                    rows_to_fill = len(set(new_hist.keys()) - new_db_dates)
                    total_added = rows_to_add + rows_to_fill
                    logger.info(
                        "[DRY RUN] Alias %s -> %s verified (%s, ratio=%s). "
                        "Would move %d DB rows, add %d cached rows (old range: %s, new range: %s).",
                        old_sym,
                        new_sym,
                        classification,
                        implied_ratio,
                        rows_moved,
                        total_added,
                        old_range,
                        new_range,
                    )
                    summaries.append(
                        {
                            "old_symbol": old_sym,
                            "new_symbol": new_sym,
                            "classification": classification,
                            "implied_ratio": implied_ratio,
                            "old_range": old_range,
                            "new_range": new_range,
                            "rows_moved": rows_moved,
                            "rows_added": total_added,
                            "status": "dry_run",
                        }
                    )
                    continue

                # --- Write mode: execute in one transaction ---
                # 1. Back up moved rows
                backup_file = r_dir / f"alias_backup_{old_sym}_to_{new_sym}.csv"
                if old_db_rows:
                    backup_data = [
                        {
                            "symbol": r.symbol,
                            "date": r.date.isoformat(),
                            "open": float(r.open) if r.open is not None else None,
                            "high": float(r.high) if r.high is not None else None,
                            "low": float(r.low) if r.low is not None else None,
                            "close": float(r.close) if r.close is not None else None,
                            "volume": int(r.volume) if r.volume is not None else None,
                            "prev_close": (
                                float(r.prev_close) if r.prev_close is not None else None
                            ),
                            "index_name": getattr(r, "index_name", None),
                        }
                        for r in old_db_rows
                    ]
                    pd.DataFrame(backup_data).to_csv(backup_file, index=False)
                    logger.info(
                        "Backed up %d rows for %s to %s",
                        len(old_db_rows),
                        old_sym,
                        backup_file,
                    )

                # 2. Move existing daily_prices rows from OLD to NEW
                rows_moved = 0
                if old_db_rows:
                    for old_row in old_db_rows:
                        if old_row.date not in new_db_dates:
                            old_row.symbol = new_sym
                            new_db_dates.add(old_row.date)
                            rows_moved += 1
                        else:
                            # Already present in NEW, remove duplicate row from OLD
                            s.delete(old_row)
                    s.flush()

                # 3. Retrieve stock_master records to copy metadata
                old_stock = s.get(StockMaster, old_sym)
                new_stock = s.get(StockMaster, new_sym)
                cap_bucket_val = (
                    new_stock.cap_bucket
                    if new_stock and new_stock.cap_bucket and new_stock.cap_bucket != "Unknown"
                    else (old_stock.cap_bucket if old_stock and old_stock.cap_bucket else "Unknown")
                )

                # 4. Insert cached OLD rows under NEW for dates missing in DB
                # and fill NEW's own missing dates from cache
                rows_to_insert = []
                for d_val, r_data in sorted(old_hist.items()):
                    if d_val not in new_db_dates:
                        rows_to_insert.append(
                            {
                                "symbol": new_sym,
                                "date": d_val,
                                "open": r_data.get("open"),
                                "high": r_data.get("high"),
                                "low": r_data.get("low"),
                                "close": r_data.get("close"),
                                "volume": r_data.get("volume"),
                                "prev_close": r_data.get("prev_close"),
                                "index_name": cap_bucket_val,
                            }
                        )
                        new_db_dates.add(d_val)

                for d_val, r_data in sorted(new_hist.items()):
                    if d_val not in new_db_dates:
                        rows_to_insert.append(
                            {
                                "symbol": new_sym,
                                "date": d_val,
                                "open": r_data.get("open"),
                                "high": r_data.get("high"),
                                "low": r_data.get("low"),
                                "close": r_data.get("close"),
                                "volume": r_data.get("volume"),
                                "prev_close": r_data.get("prev_close"),
                                "index_name": cap_bucket_val,
                            }
                        )
                        new_db_dates.add(d_val)

                if rows_to_insert:
                    upsert_rows(s, DailyPrice, rows_to_insert, index_elements=["symbol", "date"])
                rows_added = len(rows_to_insert)

                # 5. Copy sector/industry/cap_bucket to NEW if missing
                if new_stock is not None and old_stock is not None:
                    if not new_stock.sector and old_stock.sector:
                        new_stock.sector = old_stock.sector
                    if not new_stock.industry and old_stock.industry:
                        new_stock.industry = old_stock.industry
                    if (
                        not new_stock.cap_bucket or new_stock.cap_bucket == "Unknown"
                    ) and old_stock.cap_bucket:
                        new_stock.cap_bucket = old_stock.cap_bucket
                elif old_stock is not None and new_stock is None:
                    new_stock = StockMaster(
                        symbol=new_sym,
                        company_name=old_stock.company_name,
                        sector=old_stock.sector,
                        industry=old_stock.industry,
                        cap_bucket=old_stock.cap_bucket or "Unknown",
                        market_cap_cr=old_stock.market_cap_cr,
                        market_cap_asof=old_stock.market_cap_asof,
                        in_universe=old_stock.in_universe,
                        status="active" if old_stock.in_universe else "below_threshold",
                        added_on=old_stock.added_on,
                        benchmark_index=old_stock.benchmark_index,
                        source="alias",
                    )
                    s.add(new_stock)

                # 6. Set stock_master OLD to status 'renamed', renamed_to=NEW, in_universe=False
                if old_stock is not None:
                    old_stock.status = "renamed"
                    old_stock.renamed_to = new_sym
                    old_stock.in_universe = False
                    old_stock.flagged_on = old_stock.flagged_on or date.today()

                # 7. Update symbol_alias status to applied
                if alias_row is not None:
                    alias_row.status = "applied"

                s.commit()
                applied_count += 1

                # 8. Rebuild adjusted prices and refresh indicator cache for NEW
                logger.info("Rebuilding adjusted prices for %s after alias merge...", new_sym)
                self.rebuild_adjusted_prices(symbols=[new_sym])

                logger.info("Refreshing indicator cache...")
                from src.db.data_service import load_indicator_snapshot

                load_indicator_snapshot(use_adjusted=True, force_refresh=True)

                logger.info(
                    "✓ Alias %s -> %s successfully applied: moved %d rows, added %d rows "
                    "(old range: %s, new range: %s, classification: %s, ratio: %s).",
                    old_sym,
                    new_sym,
                    rows_moved,
                    rows_added,
                    old_range,
                    new_range,
                    classification,
                    implied_ratio,
                )

                summaries.append(
                    {
                        "old_symbol": old_sym,
                        "new_symbol": new_sym,
                        "classification": classification,
                        "implied_ratio": implied_ratio,
                        "old_range": old_range,
                        "new_range": new_range,
                        "rows_moved": rows_moved,
                        "rows_added": rows_added,
                        "status": "applied",
                    }
                )

            return {
                "status": "success",
                "dry_run": dry_run,
                "applied_count": applied_count,
                "summaries": summaries,
            }

        if session is not None:
            return _execute(session)
        with get_db() as s:
            return _execute(s)

    # ------------------------------------------------------------------
    # Step 11 — Detect Symbol Renames from Cache (Read-Only)
    # ------------------------------------------------------------------
    def detect_renames(
        self,
        start: date | str | None = None,
        end: date | str | None = None,
        reports_dir: Path | None = None,
    ) -> dict[str, Any]:
        """Scan raw bhavcopy cache to detect symbol rename candidates.

        Pairs where OLD stops on a date, NEW first appears within 5 trading days,
        no overlap, and continuity check passes.
        Writes reports/rename_candidates.csv. Never modifies the database.
        """
        target_cache = (
            Path(self.cache_dir) if self.cache_dir is not None else Path(RAW_DIR / "bhavcopy_cache")
        )
        cache_files = sorted(target_cache.glob("*.parquet"))
        start_d = date.fromisoformat(str(start)) if start else None
        end_d = date.fromisoformat(str(end)) if end else None
        if start_d:
            cache_files = [f for f in cache_files if date.fromisoformat(f.stem) >= start_d]
        if end_d:
            cache_files = [f for f in cache_files if date.fromisoformat(f.stem) <= end_d]

        if not cache_files:
            logger.info("No cache files found in date range %s -> %s", start, end)
            return {"candidates": [], "ambiguous": [], "report_path": ""}

        all_trading_dates = [date.fromisoformat(f.stem) for f in cache_files]
        date_to_idx = {d: idx for idx, d in enumerate(all_trading_dates)}
        latest_date = all_trading_dates[-1]

        logger.info(
            "Scanning %d cache files (%s -> %s) for rename candidates...",
            len(cache_files),
            all_trading_dates[0],
            all_trading_dates[-1],
        )

        import pyarrow.parquet as pq

        sym_summary: dict[str, dict[str, Any]] = {}

        for f in cache_files:
            d = date.fromisoformat(f.stem)
            try:
                tbl = pq.read_table(f)
                df = tbl.to_pandas()
            except Exception:
                continue

            cols_lower = {str(c).lower().strip(): c for c in df.columns}
            sym_col = cols_lower.get("symbol") or cols_lower.get("tckrsymb")
            if not sym_col:
                continue
            close_col = cols_lower.get("close") or cols_lower.get("clgprc")
            prev_col = (
                cols_lower.get("prevclose")
                or cols_lower.get("prev_close")
                or cols_lower.get("pvsclgprc")
            )
            vol_col = cols_lower.get("volume") or cols_lower.get("ttltrdqty")

            sym_idx = df.columns.get_loc(sym_col)
            close_idx = df.columns.get_loc(close_col) if close_col in df.columns else None
            prev_idx = df.columns.get_loc(prev_col) if prev_col and prev_col in df.columns else None
            vol_idx = df.columns.get_loc(vol_col) if vol_col in df.columns else None

            for row in df.itertuples(index=False):
                raw_sym = row[sym_idx]
                if pd.isna(raw_sym):
                    continue
                sym = str(raw_sym).strip().upper()
                c = (
                    float(row[close_idx])
                    if close_idx is not None and pd.notna(row[close_idx])
                    else 0.0
                )
                v = int(row[vol_idx]) if vol_idx is not None and pd.notna(row[vol_idx]) else 0
                p = (
                    float(row[prev_idx])
                    if prev_idx is not None and pd.notna(row[prev_idx])
                    else None
                )

                data = sym_summary.get(sym)
                if data is None:
                    sym_summary[sym] = {
                        "first_date": d,
                        "last_date": d,
                        "first_prev_close": p,
                        "last_close": c,
                        "dates": {d: (c, v)},
                    }
                else:
                    data["last_date"] = d
                    data["last_close"] = c
                    data["dates"][d] = (c, v)
                    if data["first_prev_close"] is None and p is not None:
                        data["first_prev_close"] = p

        logger.info(
            "Indexed %d symbols across cache files. Analyzing candidate pairs...",
            len(sym_summary),
        )

        raw_pairs: list[dict[str, Any]] = []
        for old_sym, old_data in sym_summary.items():
            old_last = old_data["last_date"]
            if old_last >= latest_date:
                continue
            old_last_idx = date_to_idx[old_last]

            for new_sym, new_data in sym_summary.items():
                if old_sym == new_sym:
                    continue
                new_first = new_data["first_date"]
                new_first_idx = date_to_idx[new_first]
                days_apart = new_first_idx - old_last_idx
                if not (1 <= days_apart <= 5):
                    continue

                # Check overlap: both have volume > 0 on same date with different prices
                common_dates = set(old_data["dates"].keys()) & set(new_data["dates"].keys())
                overlap = False
                for cd in common_dates:
                    oc, ov = old_data["dates"][cd]
                    nc, nv = new_data["dates"][cd]
                    if ov > 0 and nv > 0 and abs(oc - nc) > 0.05:
                        overlap = True
                        break
                if overlap:
                    continue

                # Continuity check
                prev_trading_day = all_trading_dates[new_first_idx - 1]
                if prev_trading_day not in old_data["dates"]:
                    continue

                old_close, _ = old_data["dates"][prev_trading_day]
                new_prev_close = new_data["first_prev_close"]
                if new_prev_close is None or old_close is None or old_close <= 0:
                    continue

                diff = abs(new_prev_close - old_close)
                implied_ratio = round(new_prev_close / old_close, 4)

                if diff <= 0.05:
                    classification = "verified_exact"
                elif abs(implied_ratio - 1.0) <= 0.01:
                    classification = "verified_close"
                else:
                    continue

                score = (
                    0 if classification == "verified_exact" else 1,
                    abs(implied_ratio - 1.0),
                    days_apart,
                )

                raw_pairs.append(
                    {
                        "old_symbol": old_sym,
                        "new_symbol": new_sym,
                        "old_last_date": old_last,
                        "new_first_date": new_first,
                        "trading_days_apart": days_apart,
                        "old_close": old_close,
                        "new_prev_close": new_prev_close,
                        "implied_ratio": implied_ratio,
                        "classification": classification,
                        "score": score,
                    }
                )

        # Disambiguate:
        old_groups: dict[str, list[dict]] = {}
        for p in raw_pairs:
            old_groups.setdefault(p["old_symbol"], []).append(p)

        new_groups: dict[str, list[dict]] = {}
        for p in raw_pairs:
            new_groups.setdefault(p["new_symbol"], []).append(p)

        final_rows: list[dict[str, Any]] = []
        for p in raw_pairs:
            old_s = p["old_symbol"]
            new_s = p["new_symbol"]
            old_cands = sorted(old_groups[old_s], key=lambda x: x["score"])
            new_cands = sorted(new_groups[new_s], key=lambda x: x["score"])

            is_best_old = (len(old_cands) == 1) or (
                old_cands[0] == p and old_cands[0]["score"] < old_cands[1]["score"]
            )
            is_best_new = (len(new_cands) == 1) or (
                new_cands[0] == p and new_cands[0]["score"] < new_cands[1]["score"]
            )

            status = "candidate" if (is_best_old and is_best_new) else "ambiguous"
            final_rows.append(
                {
                    "old_symbol": p["old_symbol"],
                    "new_symbol": p["new_symbol"],
                    "old_last_date": p["old_last_date"].isoformat(),
                    "new_first_date": p["new_first_date"].isoformat(),
                    "trading_days_apart": p["trading_days_apart"],
                    "old_close": p["old_close"],
                    "new_prev_close": p["new_prev_close"],
                    "implied_ratio": p["implied_ratio"],
                    "classification": p["classification"],
                    "status": status,
                }
            )

        r_dir = reports_dir or (PROJECT_ROOT / "reports")
        r_dir.mkdir(parents=True, exist_ok=True)
        report_csv = r_dir / "rename_candidates.csv"

        df_out = pd.DataFrame(
            final_rows,
            columns=[
                "old_symbol",
                "new_symbol",
                "old_last_date",
                "new_first_date",
                "trading_days_apart",
                "old_close",
                "new_prev_close",
                "implied_ratio",
                "classification",
                "status",
            ],
        )
        df_out.to_csv(report_csv, index=False)
        logger.info("Wrote %d rename candidate rows to %s", len(final_rows), report_csv)

        return {
            "status": "success",
            "total_pairs": len(final_rows),
            "candidates_count": sum(1 for r in final_rows if r["status"] == "candidate"),
            "ambiguous_count": sum(1 for r in final_rows if r["status"] == "ambiguous"),
            "report_path": str(report_csv),
            "rows": final_rows,
        }
