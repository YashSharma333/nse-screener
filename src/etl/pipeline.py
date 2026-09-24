"""
Bhavcopy ETL Pipeline
=====================
Orchestrates the end-to-end workflow:

1. **Resolve date range** — detect whether this is a first run or an
   incremental update by scanning existing per-stock CSVs.
2. **Collect new rows** — iterate over each trading day in the range,
   download the Bhavcopy via :class:`NSEBhavcopyDownloader`, and bucket
   rows by symbol.
3. **Merge & save** — append new rows to existing per-stock CSVs without
   duplicating or overwriting historical records.

Output files live under ``data/raw/<SYMBOL>.csv``.
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from config.settings import setup_logging, PROJECT_ROOT
from src.ingestion.downloader import NSEBhavcopyDownloader

# ---------------------------------------------------------------------------
# Initialise centralized logging
# ---------------------------------------------------------------------------
setup_logging()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
RAW_DIR = PROJECT_ROOT / "data" / "raw"
INITIAL_DAYS_BACK = 366   # ~1 year lookback on the very first run
DOWNLOAD_DELAY = 0.8      # polite gap between daily downloads


class BhavcopyETL:
    """Full ingest-transform-load pipeline for NSE Bhavcopy data.

    Usage::

        etl = BhavcopyETL()
        etl.run()
    """

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        initial_days_back: int = INITIAL_DAYS_BACK,
        download_delay: float = DOWNLOAD_DELAY,
    ) -> None:
        self.output_dir = output_dir or RAW_DIR
        self.initial_days_back = initial_days_back
        self.download_delay = download_delay
        self._downloader = NSEBhavcopyDownloader()

        # Ensure output directory exists
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Step 1 — Resolve date range
    # ------------------------------------------------------------------
    def resolve_date_range(self) -> tuple[date, date, bool]:
        """Decide which date window to fetch.

        Returns
        -------
        (fetch_start, fetch_end, is_update)

        * **First run** (no CSVs exist yet):
          ``fetch_start = today − initial_days_back``, ``is_update = False``.
        * **Update run** (CSVs already exist):
          Scans every existing CSV, finds the *earliest* per-file maximum
          ``Date``, and sets ``fetch_start`` to the day after. This ensures
          even the most-behind file gets caught up.

        Raises
        ------
        SystemExit
            If all data is already up-to-date (nothing to fetch).
        """
        today = date.today()
        csv_files = list(self.output_dir.glob("*.csv"))

        if not csv_files:
            start = today - timedelta(days=self.initial_days_back)
            logger.info("Mode: FIRST RUN  (no existing data found)")
            logger.info("Range: %s → %s  (%d days back)", start, today, self.initial_days_back)
            return start, today, False

        # ── Update run — scan existing CSVs for the latest stored date ───
        logger.info("Mode: UPDATE RUN  (existing data detected — scanning last dates …)")
        latest_dates: list[date] = []

        for path in csv_files:
            try:
                df = pd.read_csv(path, usecols=["Date"], parse_dates=["Date"])
                if not df.empty:
                    latest_dates.append(df["Date"].max().date())
            except Exception as exc:
                logger.debug("Skipping unreadable CSV %s: %s", path.name, exc)

        if not latest_dates:
            start = today - timedelta(days=self.initial_days_back)
            logger.warning(
                "Could not read any existing CSVs — falling back to full download."
            )
            return start, today, False

        # Use min so every file gets the missing days
        last_stored = min(latest_dates)
        start = last_stored + timedelta(days=1)

        if start > today:
            logger.info(
                "All data is already up to date (last date: %s). Nothing to fetch.",
                last_stored,
            )
            raise SystemExit(0)

        logger.info(
            "Last stored date: %s  (across %d files)", last_stored, len(csv_files)
        )
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
    ) -> dict[str, list[dict]]:
        """Download Bhavcopy files for each weekday and bucket rows by symbol.

        Parameters
        ----------
        start, end:
            Inclusive date range to scan.
        symbol_filter:
            Optional set of symbols to keep. When ``None`` all EQ-series
            stocks are retained.

        Returns
        -------
        ``{symbol: [row_dict, …]}`` where each dict has keys
        ``Date, Open, High, Low, Close, Volume``.
        """
        all_dates = list(self._weekdays(start, end))
        total = len(all_dates)
        stock_rows: dict[str, list[dict]] = {}
        trading_days = 0

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
                stock_rows[sym].append(
                    {
                        "Date": d.isoformat(),
                        "Open": row.get("Open"),
                        "High": row.get("High"),
                        "Low": row.get("Low"),
                        "Close": row.get("Close"),
                        "Volume": row.get("Volume"),
                    }
                )

            logger.info(
                "  [%3d/%d]  %s  ✓  (%d rows)", idx, total, d, len(df)
            )
            time.sleep(self.download_delay)

        logger.info("%d trading days processed.", trading_days)
        return stock_rows

    # ------------------------------------------------------------------
    # Step 3 — Merge & save
    # ------------------------------------------------------------------
    def merge_and_save(
        self,
        stock_rows: dict[str, list[dict]],
        is_update: bool,
    ) -> tuple[list[str], list[str]]:
        """Merge newly collected rows into per-stock CSVs.

        * On **update** runs the existing CSV is loaded first and new rows are
          appended.  Rows are de-duplicated on ``Date`` (keeping the latest
          value) so historical data is never lost.
        * On **first** runs the new rows are written directly.

        Returns
        -------
        ``(success_symbols, failed_symbols)``
        """
        success: list[str] = []
        failed: list[str] = []

        logger.info("Merging and saving CSV files …")

        for symbol in sorted(stock_rows):
            new_rows = stock_rows[symbol]
            path = self.output_dir / f"{symbol}.csv"

            try:
                # ── Load existing data (update mode) ─────────────────
                existing_df = pd.DataFrame()
                if is_update and path.exists():
                    try:
                        existing_df = pd.read_csv(
                            path, parse_dates=["Date"]
                        )
                    except Exception as exc:
                        logger.warning(
                            "%s: could not read existing CSV (%s) — will overwrite",
                            symbol, exc,
                        )

                # ── Build new-rows DataFrame ──────────────────────────
                if new_rows:
                    new_df = pd.DataFrame(new_rows)
                    new_df["Date"] = pd.to_datetime(new_df["Date"])
                else:
                    new_df = pd.DataFrame(
                        columns=["Date", "Open", "High", "Low", "Close", "Volume"]
                    )

                # ── Combine + deduplicate + sort ──────────────────────
                combined = pd.concat(
                    [existing_df, new_df], ignore_index=True
                )
                combined.drop_duplicates(
                    subset=["Date"], keep="last", inplace=True
                )
                combined.sort_values("Date", ascending=False, inplace=True)
                combined.reset_index(drop=True, inplace=True)

                if combined.empty:
                    logger.warning("  ✗ %-20s — 0 rows total (no data)", symbol)
                    failed.append(symbol)
                    continue

                combined.to_csv(path, index=False)

                # Logging
                old_count = len(existing_df)
                new_count = len(combined) - old_count
                if is_update:
                    logger.info(
                        "  ✓ %-20s  +%3d new rows  |  %3d total  →  %s",
                        symbol, new_count, len(combined), path.name,
                    )
                else:
                    logger.info(
                        "  ✓ %-20s  %3d rows  →  %s",
                        symbol, len(combined), path.name,
                    )
                success.append(symbol)

            except Exception as exc:
                logger.error(
                    "  ✗ %-20s — error during merge: %s",
                    symbol, exc,
                    exc_info=True,
                )
                failed.append(symbol)

        return success, failed

    # ------------------------------------------------------------------
    # Orchestrator
    # ------------------------------------------------------------------
    def run(
        self,
        symbol_filter: Optional[set[str]] = None,
    ) -> tuple[list[str], list[str]]:
        """Execute the full ETL pipeline.

        Parameters
        ----------
        symbol_filter:
            Optional set of symbols to keep. Pass ``None`` to retain every
            EQ-series stock from the Bhavcopy.

        Returns
        -------
        ``(success_symbols, failed_symbols)``
        """
        # 1. Decide the date window
        fetch_start, fetch_end, is_update = self.resolve_date_range()

        logger.info("=" * 62)
        logger.info("  Fetch range : %s → %s", fetch_start, fetch_end)
        logger.info("  Output dir  : %s", self.output_dir.resolve())
        logger.info("=" * 62)

        # 2. Download daily Bhavcopy files and bucket rows by symbol
        stock_rows = self.collect_new_rows(
            fetch_start, fetch_end, symbol_filter=symbol_filter
        )

        # 3. Merge into (or create) per-stock CSVs
        success, failed = self.merge_and_save(stock_rows, is_update)

        # 4. Summary
        logger.info("=" * 62)
        if is_update:
            logger.info(
                "  UPDATE COMPLETE  ✓ %d stocks updated  |  ✗ %d no new data",
                len(success), len(failed),
            )
        else:
            logger.info(
                "  FIRST RUN DONE   ✓ %d stocks written  |  ✗ %d no data",
                len(success), len(failed),
            )
        logger.info("=" * 62)

        return success, failed
