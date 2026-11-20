"""
Official NSE Bhavcopy Price Loader (Task D5)
============================================
Loads all raw Bhavcopy prices from 2016-11-21 to today into daily_prices.

Key principles:
1. Resumable: queries distinct stored dates in daily_prices; already loaded dates are skipped.
2. Raw data as published: stores all regular trading series (EQ, BE, BZ, SM, ST) without
   symbol alteration or survivorship bias.
3. Polite downloading: 0.3s pause between downloads, reuse existing non-trading days JSON cache.
4. Non-trading detection: 404s within the last 3 calendar days are NOT cached as holidays
   (file may still be published later).
5. Monthly batch upsert: chunked by calendar month via src/db/upsert.py with
   unique key (symbol, date).
6. Dependency injection: fetch_fn, parse_fn, cache_dir, and non_trading_path can be injected
   so tests remain 100% offline.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import groupby
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from config.settings import (
    BHAVCOPY_ALL_DIR,
    NSE_HISTORY_START,
    PROJECT_ROOT,
)
from src.db.models import DailyPrice
from src.db.session import get_db
from src.db.upsert import upsert_rows
from src.ingestion.bhavcopy_parser import parse_bhavcopy, write_cache
from src.ingestion.nse_files import BadFile, NoFile, bhavcopy_url, fetch

logger = logging.getLogger(__name__)

# Default location for confirmed non-trading days (reused from existing ETL cache)
DEFAULT_NON_TRADING_PATH = (
    PROJECT_ROOT / "data" / "raw" / "bhavcopy_cache" / "non_trading_days.json"
)

# Recent buffer: dates within the last 3 days are never permanently cached as holidays
RECENT_NON_TRADING_BUFFER_DAYS: int = 3


@dataclass
class LoadPricesSummary:
    """Summary of operations performed during raw price loading."""

    downloaded: int = 0
    cached: int = 0
    no_file: int = 0
    bad_file: int = 0
    rows_inserted: int = 0
    first_date: date | None = None
    last_date: date | None = None


def load_non_trading_days(path: Path | None = None) -> set[date]:
    """Read confirmed non-trading dates (holidays, weekend 404s) from JSON cache.

    Why: Avoids issuing thousands of known-failing HTTP requests to NSE on holidays.
    """
    target = path or DEFAULT_NON_TRADING_PATH
    if not target.exists():
        return set()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        return {date.fromisoformat(d) for d in raw}
    except Exception as exc:
        logger.warning("Could not read non-trading days from %s: %s", target, exc)
        return set()


def record_non_trading_day(d: date, path: Path | None = None) -> None:
    """Atomically record a confirmed non-trading date into JSON cache.

    Why: Uses a .tmp file replacement to guarantee atomic updates without corrupting JSON.
    """
    target = path or DEFAULT_NON_TRADING_PATH
    existing = load_non_trading_days(target)
    if d in existing:
        return
    existing.add(d)
    iso_list = sorted(x.isoformat() for x in existing)
    tmp_path = target.with_suffix(".json.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(json.dumps(iso_list, indent=2), encoding="utf-8")
        tmp_path.replace(target)
    except Exception as exc:
        logger.warning("Could not write non-trading day %s to %s: %s", d, target, exc)
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def is_recent_date(d: date, ref_date: date | None = None) -> bool:
    """Check if date d is within the last 3 calendar days.

    Why: NSE often uploads Bhavcopy files in the late evening or next day.
    A 404 within the last 3 days could be a temporary publishing delay, so we
    must NOT permanently mark it as a non-trading holiday.
    """
    today = ref_date or date.today()
    return d >= today - timedelta(days=RECENT_NON_TRADING_BUFFER_DAYS)


def load_raw_prices(
    start: date | str | None = None,
    end: date | str | None = None,
    *,
    session: Session | None = None,
    cache_dir: Path | None = None,
    non_trading_path: Path | None = None,
    fetch_fn: Callable[..., bytes] = fetch,
    parse_fn: Callable[[bytes, date | str], pd.DataFrame] = parse_bhavcopy,
    write_cache_fn: Callable[..., Path] = write_cache,
    sleep_fn: Callable[[float], Any] = time.sleep,
    pause_seconds: float = 0.3,
    ref_date: date | None = None,
) -> LoadPricesSummary:
    """Load raw Bhavcopy files into Parquet cache and upsert into daily_prices.

    Parameters
    ----------
    start : date | str | None
        Earliest calendar day to process (default: 2016-11-21).
    end : date | str | None
        Latest calendar day to process (default: yesterday).
    session : Session | None
        SQLAlchemy database session. If provided, uses it as given without closing.
        If None, creates and commits within a managed get_db() session.
    cache_dir : Path | None
        Directory for storing full-universe parquet cache (data/raw/nse/bhavcopy_all).
    non_trading_path : Path | None
        Path to non_trading_days.json (reused from existing ETL cache).
    fetch_fn : Callable
        Function downloading raw URL bytes (default: src.ingestion.nse_files.fetch).
    parse_fn : Callable
        Function parsing zip bytes into standardized DataFrame.
    write_cache_fn : Callable
        Function saving parsed DataFrame to Parquet cache.
    sleep_fn : Callable
        Sleep function for politeness between network downloads.
    pause_seconds : float
        Polite delay in seconds between download requests (default: 0.3).
    ref_date : date | None
        Reference date for determining recent 3-day buffer (default: date.today()).

    Returns
    -------
    LoadPricesSummary
        Summary statistics of the loading process.
    """
    today = ref_date or date.today()
    start_date: date = (
        (date.fromisoformat(start) if isinstance(start, str) else start)
        if start is not None
        else NSE_HISTORY_START
    )
    end_date: date = (
        (date.fromisoformat(end) if isinstance(end, str) else end)
        if end is not None
        else today - timedelta(days=1)
    )

    summary = LoadPricesSummary()
    if start_date > end_date:
        logger.info(
            "Start date (%s) is after end date (%s); nothing to load.",
            start_date,
            end_date,
        )
        return summary

    target_cache_dir = cache_dir or BHAVCOPY_ALL_DIR
    target_cache_dir.mkdir(parents=True, exist_ok=True)
    target_nt_path = non_trading_path or DEFAULT_NON_TRADING_PATH

    # Load known non-trading days once into memory for fast lookup
    non_trading_days = load_non_trading_days(target_nt_path)

    # Build full sequence of calendar days in range (every single day, including weekends)
    calendar_days: list[date] = []
    curr = start_date
    while curr <= end_date:
        calendar_days.append(curr)
        curr += timedelta(days=1)

    def _execute_load(s: Session) -> None:
        # Pre-query dates already in database to guarantee fast, safe resume after interruption
        stmt = (
            select(DailyPrice.date)
            .distinct()
            .where(DailyPrice.date >= start_date, DailyPrice.date <= end_date)
        )
        db_dates: set[date] = set(s.scalars(stmt).all())

        # Group calendar days by year and month to insert in bounded monthly transactions
        for (year, month), month_group in groupby(calendar_days, key=lambda d: (d.year, d.month)):
            month_days = list(month_group)
            month_records: list[dict[str, Any]] = []

            for d in month_days:
                # 1. Skip if already confirmed non-trading day (holiday, weekend 404)
                if d in non_trading_days:
                    continue

                # 2. Skip if already in database (resume capability)
                if d in db_dates:
                    continue

                # 3. Check if Parquet cache already exists for this day
                parquet_path = target_cache_dir / f"{d.isoformat()}.parquet"
                df: pd.DataFrame | None = None
                source: str = "cached"

                if parquet_path.exists():
                    try:
                        df = pd.read_parquet(parquet_path)
                        summary.cached += 1
                    except Exception as exc:
                        logger.warning(
                            "Corrupt parquet cache for %s (%s); will re-download.", d, exc
                        )
                        df = None

                if df is None:
                    url = bhavcopy_url(d)
                    try:
                        raw = fetch_fn(url)
                        df = parse_fn(raw, d)
                        write_cache_fn(df, d, cache_dir=target_cache_dir)
                        summary.downloaded += 1
                        source = "downloaded"

                        # Polite pause after successful network download
                        if pause_seconds > 0:
                            sleep_fn(pause_seconds)

                    except NoFile:
                        summary.no_file += 1
                        # Only record permanently as holiday if outside the recent 3-day buffer
                        if not is_recent_date(d, ref_date=today):
                            record_non_trading_day(d, target_nt_path)
                            non_trading_days.add(d)
                        continue

                    except BadFile as exc:
                        logger.warning("Bad file on %s (%s); skipping to avoid bad data.", d, exc)
                        summary.bad_file += 1
                        continue

                # If we have a valid parsed DataFrame for this trading day, accumulate records
                if df is not None and not df.empty:
                    # Ensure date column consists of standard datetime.date objects
                    if not isinstance(df["date"].iloc[0], date):
                        df["date"] = pd.to_datetime(df["date"]).dt.date

                    if summary.first_date is None or d < summary.first_date:
                        summary.first_date = d
                    if summary.last_date is None or d > summary.last_date:
                        summary.last_date = d

                    month_records.extend(df.to_dict(orient="records"))
                    logger.info("Day %s: %d rows (%s)", d, len(df), source)

            # End of calendar month: upsert accumulated records for this month
            if month_records:
                upsert_rows(
                    session=s,
                    model=DailyPrice,
                    rows=month_records,
                    index_elements=["symbol", "date"],
                )
                s.commit()
                summary.rows_inserted += len(month_records)
                logger.info(
                    "Month %04d-%02d: upserted %d rows into daily_prices (cumulative: %d rows)",
                    year,
                    month,
                    len(month_records),
                    summary.rows_inserted,
                )

    # Use caller-provided session if passed, otherwise manage a clean session
    if session is not None:
        _execute_load(session)
    else:
        with get_db() as managed_session:
            _execute_load(managed_session)

    # Final summary output
    logger.info(
        "Load complete: %d downloaded, %d cached, %d no-file, %d bad-file, "
        "%d rows inserted. Date range: %s to %s.",
        summary.downloaded,
        summary.cached,
        summary.no_file,
        summary.bad_file,
        summary.rows_inserted,
        summary.first_date,
        summary.last_date,
    )

    return summary
