"""
Official NSE Market Cap Loader (Task D6)
========================================
Fills daily_prices.market_cap from official NSE PR bundles (PRDDMMYY.zip)
from 2024-02-01 onward.

Key principles:
1. Exact published values: Market cap is parsed as exact Decimal (no float rounding).
2. Historical boundary: Only applies to trading dates >= 2024-02-01 (FIRST_MCAP_DATE).
   Dates prior to 2024-02-01 stay strictly NULL (never computed or estimated).
3. Resilient parsing: Discovers mcap member case-insensitively
   (MCAP01022024.csv vs mcap17112026.csv).
4. Date verification: Confirms Trade Date inside the file matches the expected date.
5. Matching & reporting: Updates daily_prices for existing (symbol, series) pairs;
   unmatched symbols are counted and reported (never inserted).
6. Parquet cache: Stores parsed market cap in data/raw/nse/mcap/<YYYY-MM-DD>.parquet.
"""

from __future__ import annotations

import io
import logging
import time
import zipfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import bindparam, select
from sqlalchemy.orm import Session

from config import settings
from config.settings import FIRST_MCAP_DATE
from src.common.dates import date_or_default, parse_nse_date
from src.common.db import use_session
from src.common.trading_calendar import get_trading_dates
from src.db.models import DailyPrice
from src.ingestion import nse_files
from src.ingestion.nse_files import BadFile, NoFile, check_date, check_zip

logger = logging.getLogger(__name__)

# Track whether official headers have been logged once
_HEADER_LOGGED: bool = False


@dataclass
class McapSummary:
    """Summary of operations performed during market cap loading."""

    downloaded: int = 0
    cached: int = 0
    missing_bundle: int = 0
    bad_file: int = 0
    updated_rows: int = 0
    unmatched_symbols: int = 0
    first_date: date | None = None
    last_date: date | None = None


# '01 FEB 2024', '01-Feb-2024', '2024-02-01' or '01/02/2024'.
MCAP_DATE_FORMATS = ("%d %b %Y", "%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y")


def _read_mcap_date(text: str) -> date:
    """Read a date written in a market cap file; a date nobody can read means a bad file."""
    return parse_nse_date(
        text, MCAP_DATE_FORMATS, strict=True, error=BadFile, label="Trade date in mcap file"
    )


def _to_decimal(val: Any) -> Decimal | None:
    """An exact Decimal from a cell, None for blank/NULL/NaN; unreadable text is a bad file."""
    if pd.isna(val) or val is None:
        return None
    s = str(val).strip()
    if not s or s.upper() == "NULL" or s.upper() == "NAN":
        return None
    try:
        return Decimal(s)
    except Exception as exc:
        raise BadFile(f"Cannot parse market cap value {s!r} as Decimal") from exc


def parse_mcap_file(raw_zip_bytes: bytes, expected_date: date | str) -> pd.DataFrame:
    """Extract and parse market cap CSV from PR bundle zip bytes.

    Parameters
    ----------
    raw_zip_bytes : bytes
        Downloaded PR bundle zip file bytes.
    expected_date : date | str
        Expected trading date for verification.

    Returns
    -------
    pd.DataFrame
        DataFrame with columns: [date, symbol, series, market_cap]
    """
    check_zip(raw_zip_bytes)
    exp_date = (
        expected_date if isinstance(expected_date, date) else _read_mcap_date(str(expected_date))
    )

    try:
        with zipfile.ZipFile(io.BytesIO(raw_zip_bytes)) as zf:
            mcap_names = [
                n
                for n in zf.namelist()
                if n.lower().startswith("mcap") and n.lower().endswith(".csv")
            ]
            if not mcap_names:
                raise BadFile("PR bundle zip does not contain any mcap*.csv file")

            with zf.open(mcap_names[0]) as f:
                df = pd.read_csv(f, dtype=str)
    except zipfile.BadZipFile as exc:
        raise BadFile("Corrupt PR bundle zip archive") from exc

    # Clean whitespace from column names and cell strings
    df.columns = df.columns.str.strip()
    for c in df.columns:
        df[c] = df[c].astype(str).str.strip()

    global _HEADER_LOGGED
    if not _HEADER_LOGGED:
        logger.info("Official PR bundle MCAP headers: %s", list(df.columns))
        _HEADER_LOGGED = True

    # Identify columns by normalized name
    cols_norm = {c.lower(): c for c in df.columns}
    sym_col = next((cols_norm[k] for k in cols_norm if k in ("symbol", "tckrsymb")), None)
    srs_col = next((cols_norm[k] for k in cols_norm if k in ("series", "sctysrs")), None)
    date_col = next((cols_norm[k] for k in cols_norm if "trade date" in k or k == "date"), None)
    mcap_col = next(
        (cols_norm[k] for k in cols_norm if "market cap" in k or "m_cap" in k or k == "mcap"),
        None,
    )

    if not sym_col or not srs_col or not date_col or not mcap_col:
        raise BadFile(
            f"Missing required columns in mcap CSV: sym={sym_col}, srs={srs_col}, "
            f"date={date_col}, mcap={mcap_col}. Available: {list(df.columns)}"
        )

    # Date check: file must match expected trading date
    raw_dates = df[date_col].unique()
    if len(raw_dates) == 0:
        raise BadFile("Mcap file contains 0 rows")
    file_date = _read_mcap_date(str(raw_dates[0]))
    check_date(file_date, exp_date)

    # Standardize symbols, series, and parse exact Decimal market cap
    df["symbol"] = df[sym_col].str.upper()
    df["series"] = df[srs_col].str.upper()
    df["date"] = file_date

    df["market_cap"] = df[mcap_col].apply(_to_decimal)

    # Drop rows without market cap
    clean_df = df.dropna(subset=["market_cap"]).copy()
    return clean_df[["date", "symbol", "series", "market_cap"]].reset_index(drop=True)


def write_mcap_cache(
    df: pd.DataFrame,
    d: date | str,
    *,
    cache_dir: Path | None = None,
) -> Path:
    """Save parsed market cap DataFrame to Parquet cache."""
    target_dir = cache_dir or settings.MCAP_DIR
    target_date = d if isinstance(d, date) else _read_mcap_date(str(d))
    target_path = target_dir / f"{target_date.isoformat()}.parquet"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(target_path, index=False, engine="pyarrow")
    return target_path


def read_cached_mcap(d: date) -> pd.DataFrame | None:
    """The parsed market caps of ``d`` from the Parquet cache, or None (missing or corrupt)."""
    path = settings.MCAP_DIR / f"{d.isoformat()}.parquet"
    if not path.exists():
        return None
    try:
        return pd.read_parquet(path)
    except (OSError, ValueError) as exc:
        logger.warning("Corrupt mcap parquet cache for %s (%s); will re-download.", d, exc)
        return None


def download_mcap(d: date) -> pd.DataFrame:
    """Download, check and cache one PR bundle. Raises NoFile or BadFile; the caller decides."""
    raw = nse_files.fetch(nse_files.pr_bundle_url(d))
    df = parse_mcap_file(raw, d)  # raises BadFile when the date inside the file is not ``d``
    write_mcap_cache(df, d)
    if settings.PRICE_DOWNLOAD_PAUSE_SECONDS > 0:
        time.sleep(settings.PRICE_DOWNLOAD_PAUSE_SECONDS)  # be polite to NSE
    return df


def load_one_mcap_day(d: date, summary: McapSummary) -> pd.DataFrame | None:
    """Market caps of one day (cache first, then NSE), or None when there is nothing to use."""
    df = read_cached_mcap(d)
    if df is not None:
        summary.cached += 1
    else:
        try:
            df = download_mcap(d)
        except NoFile:
            summary.missing_bundle += 1
            logger.info("PR bundle not found for %s (leave market_cap NULL)", d)
            return None
        except BadFile as exc:
            summary.bad_file += 1
            logger.warning("Bad PR bundle / mcap file on %s (%s); skipping", d, exc)
            return None
        summary.downloaded += 1
    return None if df.empty else df


def match_to_stored_rows(df: pd.DataFrame, d: date, session: Session | None) -> pd.DataFrame:
    """Add a ``matched`` column: True where (symbol, series) is already stored for day ``d``.

    Why: market cap only ever UPDATES existing price rows; it never inserts a row.
    """
    with use_session(session) as s:
        stored = pd.DataFrame(
            s.execute(
                select(DailyPrice.symbol, DailyPrice.series).where(DailyPrice.date == d)
            ).all(),
            columns=["symbol", "series"],
        )
    stored["matched"] = True
    merged = df.merge(stored, on=["symbol", "series"], how="left")
    merged["matched"] = merged["matched"].fillna(False).astype(bool)
    return merged


def update_market_cap(
    session: Session | None, d: date, df: pd.DataFrame, summary: McapSummary
) -> None:
    """Write the market caps of day ``d`` onto the stored price rows and count the leftovers."""
    merged = match_to_stored_rows(df, d, session)
    matched = merged[merged["matched"]]
    summary.unmatched_symbols += len(merged) - len(matched)

    if not matched.empty:
        table = DailyPrice.__table__
        update_stmt = (
            table.update()
            .where(
                table.c.symbol == bindparam("b_symbol"),
                table.c.date == bindparam("b_date"),
                table.c.series == bindparam("b_series"),
            )
            .values(market_cap=bindparam("b_market_cap"))
        )
        params = [
            {"b_symbol": sym, "b_date": d, "b_series": srs, "b_market_cap": cap}
            for sym, srs, cap in zip(
                matched["symbol"], matched["series"], matched["market_cap"], strict=True
            )
        ]
        with use_session(session) as s:  # own session per date unless one is given
            s.execute(update_stmt, params)
        summary.updated_rows += len(params)

    logger.info(
        "Date %s: updated market_cap on %d rows (%d symbols not in daily_prices)",
        d,
        len(matched),
        len(merged) - len(matched),
    )


def load_market_caps(
    start: date | str | None = None,
    end: date | str | None = None,
    *,
    session: Session | None = None,
) -> McapSummary:
    """Load market cap values from PR bundles into daily_prices for dates >= 2024-02-01.

    ``start`` is clamped to FIRST_MCAP_DATE (earlier dates stay NULL, never estimated);
    ``end`` defaults to today. Only trading dates already in daily_prices are visited.
    ``session``: used as given and never committed; without one, each date has its own session.
    """
    start_date = max(date_or_default(start, FIRST_MCAP_DATE), FIRST_MCAP_DATE)
    end_date = date_or_default(end, date.today())

    summary = McapSummary()
    if start_date > end_date:
        logger.info(
            "Start date (%s) is after end date (%s); nothing to load.", start_date, end_date
        )
        return summary

    with use_session(session) as s:
        trading_dates = get_trading_dates(s, start_date, end_date)
    logger.info("Found %d trading dates for market cap load.", len(trading_dates))

    for d in trading_dates:
        df = load_one_mcap_day(d, summary)
        if df is None:
            continue
        summary.first_date = min(summary.first_date or d, d)
        summary.last_date = max(summary.last_date or d, d)
        update_market_cap(session, d, df, summary)

    logger.info(
        "Market cap load complete: %d downloaded, %d cached, %d missing bundles, "
        "%d bad files, %d rows updated, %d unmatched symbols. Range: %s to %s.",
        summary.downloaded,
        summary.cached,
        summary.missing_bundle,
        summary.bad_file,
        summary.updated_rows,
        summary.unmatched_symbols,
        summary.first_date,
        summary.last_date,
    )
    return summary
