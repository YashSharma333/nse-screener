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
6. Folders and the pause come from config/settings.py when the loader runs; tests replace
   ``nse_files.fetch`` and the settings instead of passing functions in.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from config import settings
from config.settings import NSE_HISTORY_START
from src.common.dates import date_or_default
from src.common.db import use_session
from src.common.symbols import drop_funds
from src.common.trading_calendar import (
    PRICES,
    get_trading_dates,
    load_non_trading_days,
    record_missing_file,
)
from src.db.models import DailyPrice
from src.db.upsert import upsert_rows
from src.ingestion import nse_files
from src.ingestion.bhavcopy_parser import parse_bhavcopy, write_cache
from src.ingestion.nse_files import BadFile, NoFile

logger = logging.getLogger(__name__)


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


def find_price_gaps(
    session: Session,
    non_trading_days: set[date] | None = None,
) -> list[date]:
    """List calendar days between the first and last stored date that have no prices.

    Why: the daily update starts at max(date)+1, so a day that failed once (for example
    an HTML block page served with HTTP 200) would otherwise never be tried again.
    A day is NOT a gap when it is recorded as a confirmed non-trading day.
    """
    stored_dates = get_trading_dates(session)
    if not stored_dates:
        return []
    first_day, last_day = stored_dates[0], stored_dates[-1]

    if non_trading_days is None:
        non_trading_days = load_non_trading_days(PRICES)
    stored_days = set(stored_dates)

    gaps: list[date] = []
    day = first_day
    while day <= last_day:
        if day not in stored_days and day not in non_trading_days:
            gaps.append(day)
        day += timedelta(days=1)
    return gaps


def write_price_gaps_report(gaps: list[date], path: Path | None = None) -> Path:
    """Write the gap days to reports/price_gaps.csv (overwritten every run)."""
    target = path or settings.REPORTS_DIR / "price_gaps.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"date": [d.isoformat() for d in gaps]}).to_csv(target, index=False)
    return target


def dates_to_load(
    start: date, end: date, session: Session | None, non_trading_days: set[date]
) -> list[date]:
    """Calendar days from ``start`` to ``end`` that still need a download.

    Every calendar day is a candidate (weekends too: special sessions exist). Days already
    stored in daily_prices (resume after an interruption) and confirmed non-trading days are
    left out.
    """
    with use_session(session) as s:
        stored = set(get_trading_dates(s, start, end))
    every_day = (start + timedelta(days=i) for i in range((end - start).days + 1))
    return [d for d in every_day if d not in stored and d not in non_trading_days]


def split_by_month(days: list[date]) -> list[list[date]]:
    """Group an ordered list of days into one list per calendar month."""
    months: dict[tuple[int, int], list[date]] = {}
    for d in days:
        months.setdefault((d.year, d.month), []).append(d)
    return list(months.values())


def read_cached_day(d: date) -> pd.DataFrame | None:
    """The parsed prices of ``d`` from the Parquet cache, or None (missing or corrupt file)."""
    path = settings.BHAVCOPY_ALL_DIR / f"{d.isoformat()}.parquet"
    if not path.exists():
        return None
    try:
        # Drop funds again on read: a cache file written before the ETF rule must never re-add them.
        return drop_funds(pd.read_parquet(path))
    except (OSError, ValueError) as exc:
        logger.warning("Corrupt parquet cache for %s (%s); will re-download.", d, exc)
        return None


def download_day(d: date) -> pd.DataFrame:
    """Download, check and cache one day. Raises NoFile or BadFile; the caller decides."""
    raw = nse_files.fetch(nse_files.bhavcopy_url(d))
    df = parse_bhavcopy(raw, d)  # raises BadFile when the date inside the file is not ``d``
    write_cache(df, d)
    if settings.PRICE_DOWNLOAD_PAUSE_SECONDS > 0:
        time.sleep(settings.PRICE_DOWNLOAD_PAUSE_SECONDS)  # be polite to NSE
    return df


def load_one_day(d: date, today: date, summary: LoadPricesSummary) -> pd.DataFrame | None:
    """Prices of one day (cache first, then NSE), or None when there is nothing to store.

    A 404 is remembered as a holiday only when ``d`` is older than 3 days; a bad file is
    skipped and counted.
    """
    df = read_cached_day(d)
    source = "cached"
    if df is not None:
        summary.cached += 1
    else:
        try:
            df = download_day(d)
        except NoFile:
            summary.no_file += 1
            record_missing_file(PRICES, d, today)
            return None
        except BadFile as exc:
            logger.warning("Bad file on %s (%s); skipping to avoid bad data.", d, exc)
            summary.bad_file += 1
            return None
        summary.downloaded += 1
        source = "downloaded"
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["date"]).dt.date  # plain dates for the database
    logger.info("Day %s: %d rows (%s)", d, len(df), source)
    return df


def write_month(
    session: Session | None, rows: list[dict[str, Any]], summary: LoadPricesSummary
) -> None:
    """Upsert one month of rows (own session unless one is given; a given one is not committed)."""
    with use_session(session) as s:
        upsert_rows(session=s, model=DailyPrice, rows=rows, index_elements=["symbol", "date"])
    summary.rows_inserted += len(rows)
    logger.info("Upserted %d rows (cumulative: %d)", len(rows), summary.rows_inserted)


def load_raw_prices(
    start: date | str | None = None,
    end: date | str | None = None,
    *,
    session: Session | None = None,
    ref_date: date | None = None,
) -> LoadPricesSummary:
    """Load raw Bhavcopy files into the Parquet cache and upsert them into daily_prices.

    ``start`` defaults to 2016-11-21, ``end`` to today (NSE may not have published it yet).
    ``session``: used as given and never committed; without one, each month is saved in its
    own session. ``ref_date`` is "today" for the 3-day rule on 404s (default: date.today()).
    """
    today = ref_date or date.today()
    start_date = date_or_default(start, NSE_HISTORY_START)
    end_date = date_or_default(end, today)

    summary = LoadPricesSummary()
    if start_date > end_date:
        logger.info(
            "Start date (%s) is after end date (%s); nothing to load.", start_date, end_date
        )
        return summary

    days = dates_to_load(start_date, end_date, session, load_non_trading_days(PRICES))
    for month_days in split_by_month(days):
        rows: list[dict[str, Any]] = []
        for d in month_days:
            df = load_one_day(d, today, summary)
            if df is None:
                continue
            summary.first_date = min(summary.first_date or d, d)
            summary.last_date = max(summary.last_date or d, d)
            rows.extend(df.to_dict(orient="records"))
        if rows:
            write_month(session, rows, summary)

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
