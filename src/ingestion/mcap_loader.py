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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import bindparam, select
from sqlalchemy.orm import Session

from config.settings import FIRST_MCAP_DATE, MCAP_DIR
from src.db.models import DailyPrice
from src.db.session import get_db
from src.ingestion.nse_files import BadFile, NoFile, check_zip, fetch, pr_bundle_url

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


def _parse_mcap_date(val: str) -> date:
    """Parse date from mcap file ('01 FEB 2024', '01-Feb-2024', or '2024-02-01')."""
    clean_val = val.strip().upper()
    for fmt in ("%d %b %Y", "%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(clean_val, fmt).date()
        except ValueError:
            continue
    raise BadFile(f"Cannot parse trade date in mcap file: {val!r}")


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
        expected_date if isinstance(expected_date, date) else _parse_mcap_date(str(expected_date))
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
    file_date = _parse_mcap_date(str(raw_dates[0]))
    if file_date != exp_date:
        raise BadFile(
            f"Trade date inside mcap file ({file_date}) does not match expected ({exp_date})"
        )

    # Standardize symbols, series, and parse exact Decimal market cap
    df["symbol"] = df[sym_col].str.upper()
    df["series"] = df[srs_col].str.upper()
    df["date"] = file_date

    def _to_decimal(val: Any) -> Decimal | None:
        if pd.isna(val) or val is None:
            return None
        s = str(val).strip()
        if not s or s.upper() == "NULL" or s.upper() == "NAN":
            return None
        try:
            return Decimal(s)
        except Exception as exc:
            raise BadFile(f"Cannot parse market cap value {s!r} as Decimal") from exc

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
    target_dir = cache_dir or MCAP_DIR
    target_date = d if isinstance(d, date) else _parse_mcap_date(str(d))
    target_path = target_dir / f"{target_date.isoformat()}.parquet"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(target_path, index=False, engine="pyarrow")
    return target_path


def load_market_caps(
    start: date | str | None = None,
    end: date | str | None = None,
    *,
    session: Session | None = None,
    cache_dir: Path | None = None,
    fetch_fn: Callable[..., bytes] = fetch,
    parse_fn: Callable[[bytes, date | str], pd.DataFrame] = parse_mcap_file,
    write_cache_fn: Callable[..., Path] = write_mcap_cache,
    sleep_fn: Callable[[float], Any] = time.sleep,
    pause_seconds: float = 0.3,
) -> McapSummary:
    """Load market cap values from PR bundles into daily_prices for dates >= 2024-02-01.

    Parameters
    ----------
    start : date | str | None
        Earliest date to load. Clamped to FIRST_MCAP_DATE (2024-02-01).
    end : date | str | None
        Latest date to load (default: yesterday).
    session : Session | None
        SQLAlchemy database session. If provided, uses it as given without closing.
    cache_dir : Path | None
        Directory for storing parquet cache (data/raw/nse/mcap).
    fetch_fn : Callable
        Function downloading raw URL bytes (default: fetch).
    parse_fn : Callable
        Function parsing zip bytes into DataFrame (default: parse_mcap_file).
    write_cache_fn : Callable
        Function caching DataFrame as Parquet.
    sleep_fn : Callable
        Sleep function for politeness between downloads.
    pause_seconds : float
        Delay in seconds between requests (default: 0.3).

    Returns
    -------
    McapSummary
        Summary statistics of the loading process.
    """
    # Boundary: market cap files start on 2024-02-01
    req_start = (
        (date.fromisoformat(start) if isinstance(start, str) else start)
        if start is not None
        else FIRST_MCAP_DATE
    )
    start_date = max(req_start, FIRST_MCAP_DATE)

    req_end = (
        (date.fromisoformat(end) if isinstance(end, str) else end)
        if end is not None
        else date.today() - timedelta(days=1)
    )
    end_date = req_end

    summary = McapSummary()
    if start_date > end_date:
        logger.info(
            "Start date (%s) is after end date (%s); nothing to load.",
            start_date,
            end_date,
        )
        return summary

    target_cache_dir = cache_dir or MCAP_DIR
    target_cache_dir.mkdir(parents=True, exist_ok=True)

    def _execute(s: Session) -> None:
        # Query distinct trading dates present in daily_prices in the target range
        stmt = (
            select(DailyPrice.date)
            .distinct()
            .where(DailyPrice.date >= start_date, DailyPrice.date <= end_date)
            .order_by(DailyPrice.date)
        )
        trading_dates = list(s.scalars(stmt).all())
        logger.info(
            "Found %d trading dates in daily_prices between %s and %s for market cap load.",
            len(trading_dates),
            start_date,
            end_date,
        )

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

        for d in trading_dates:
            parquet_path = target_cache_dir / f"{d.isoformat()}.parquet"
            df: pd.DataFrame | None = None

            if parquet_path.exists():
                try:
                    df = pd.read_parquet(parquet_path)
                    summary.cached += 1
                except Exception as exc:
                    logger.warning(
                        "Corrupt mcap parquet cache for %s (%s); will re-download.", d, exc
                    )
                    df = None

            if df is None:
                url = pr_bundle_url(d)
                try:
                    raw = fetch_fn(url)
                    df = parse_fn(raw, d)
                    write_cache_fn(df, d, cache_dir=target_cache_dir)
                    summary.downloaded += 1

                    if pause_seconds > 0:
                        sleep_fn(pause_seconds)
                except NoFile:
                    summary.missing_bundle += 1
                    logger.info("PR bundle not found for %s (leave market_cap NULL)", d)
                    continue
                except BadFile as exc:
                    summary.bad_file += 1
                    logger.warning("Bad PR bundle / mcap file on %s (%s); skipping", d, exc)
                    continue

            if df is not None and not df.empty:
                if summary.first_date is None or d < summary.first_date:
                    summary.first_date = d
                if summary.last_date is None or d > summary.last_date:
                    summary.last_date = d

                # Query existing (symbol, series) pairs for date d in daily_prices
                existing_pairs_stmt = select(DailyPrice.symbol, DailyPrice.series).where(
                    DailyPrice.date == d
                )
                existing_pairs = set(s.execute(existing_pairs_stmt).all())

                update_params: list[dict[str, Any]] = []
                unmatched_count = 0

                for _, row in df.iterrows():
                    sym = row["symbol"]
                    srs = row["series"]
                    mcap_val = row["market_cap"]

                    if (sym, srs) in existing_pairs:
                        update_params.append(
                            {
                                "b_market_cap": mcap_val,
                                "b_symbol": sym,
                                "b_date": d,
                                "b_series": srs,
                            }
                        )
                    else:
                        unmatched_count += 1

                if update_params:
                    s.execute(update_stmt, update_params)
                    s.commit()
                    summary.updated_rows += len(update_params)

                summary.unmatched_symbols += unmatched_count

                logger.info(
                    "Date %s: updated market_cap on %d rows (%d symbols not in daily_prices)",
                    d,
                    len(update_params),
                    unmatched_count,
                )

    if session is not None:
        _execute(session)
    else:
        with get_db() as managed_session:
            _execute(managed_session)

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
