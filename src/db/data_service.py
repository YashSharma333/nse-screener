"""Market Data Service Layer.

=========================
Handles querying market data from MySQL with proactive error handling,
caching with pre-computed Parquet files, technical indicator computations,
corporate action adjustments, and benchmark data retrieval.
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text

from config.settings import setup_logging
from src.computation.indicators import TechnicalCalculator
from src.db.session import check_connection, engine
from src.ingestion.benchmark import canonical_index_code
from src.ingestion.index_constituents import get_index_constituents

setup_logging()
logger = logging.getLogger(__name__)

INDICATORS_CACHE_PATH = Path("data/processed/indicators.parquet")


def load_raw_market_data(use_adjusted: bool = True) -> tuple[pd.DataFrame, bool, str]:
    """Fetch daily prices from MySQL.

    Args:
        use_adjusted: Whether to coalesce split/bonus adjusted price columns.

    Returns:
        tuple of (DataFrame, is_live_boolean, status_message)
    """
    if not check_connection():
        logger.warning("Database unavailable. MySQL offline.")
        return pd.DataFrame(), False, "MySQL offline — database connection failed"

    try:
        if use_adjusted:
            query = text("""
                SELECT
                    symbol,
                    date,
                    COALESCE(adj_open, open) AS open,
                    COALESCE(adj_high, high) AS high,
                    COALESCE(adj_low, low) AS low,
                    COALESCE(adj_close, close) AS close,
                    COALESCE(adj_volume, volume) AS volume,
                    prev_close,
                    index_name
                FROM daily_prices
                ORDER BY symbol, date ASC
            """)
        else:
            query = text("""
                SELECT symbol, date, open, high, low, close, volume, prev_close, index_name
                FROM daily_prices
                ORDER BY symbol, date ASC
            """)

        with engine.connect() as conn:
            df = pd.read_sql(query, conn)

        if df.empty:
            logger.warning("daily_prices table exists but contains 0 records.")
            return (
                pd.DataFrame(),
                True,
                "daily_prices table is empty — run ETL pipeline to ingest data",
            )

        # Ensure correct data types
        df["date"] = pd.to_datetime(df["date"]).dt.date
        for col in ["close", "open", "high", "low", "volume", "prev_close"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        logger.info(
            "Loaded %d daily price records across %d symbols from MySQL.",
            len(df),
            df["symbol"].nunique(),
        )
        return (
            df,
            True,
            f"Live MySQL: {len(df):,} records ({df['symbol'].nunique()} symbols)",
        )

    except Exception as exc:
        logger.error("Error reading from daily_prices: %s", exc, exc_info=True)
        return pd.DataFrame(), False, f"Database error ({exc})"


def get_benchmark_returns(
    index_code: str = "NIFTY_500",
    start: date | None = None,
    end: date | None = None,
) -> pd.DataFrame:
    """Retrieve benchmark closing prices and daily returns for a given index.

    Args:
        index_code: Standard index code (e.g. 'NIFTY_500', 'NIFTY_100')
        start: Optional start date filter
        end: Optional end date filter

    Returns:
        DataFrame with columns ['date', 'close', 'returns']
    """
    if not check_connection():
        return pd.DataFrame()

    code = canonical_index_code(index_code)
    clauses = ["index_code = :index_code"]
    params: dict[str, Any] = {"index_code": code}

    if start:
        clauses.append("date >= :start")
        params["start"] = start
    if end:
        clauses.append("date <= :end")
        params["end"] = end

    where_str = " AND ".join(clauses)
    query = text(f"""
        SELECT date, close
        FROM benchmark_prices
        WHERE {where_str}
        ORDER BY date ASC
    """)

    try:
        with engine.connect() as conn:
            df = pd.read_sql(query, conn, params=params)

        if df.empty:
            return pd.DataFrame()

        df["date"] = pd.to_datetime(df["date"]).dt.date
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df["returns"] = df["close"].pct_change()
        return df
    except Exception as exc:
        logger.warning("Error fetching benchmark prices for %s: %s", index_code, exc)
        return pd.DataFrame()


def save_indicator_parquet_cache(
    df: pd.DataFrame,
    cache_path: Path = INDICATORS_CACHE_PATH,
) -> None:
    """Atomically write processed indicator DataFrame to Parquet cache."""
    if df.empty:
        return

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(cache_path.parent),
        prefix="indicators_",
        suffix=".tmp",
    )
    os.close(tmp_fd)

    try:
        tmp_file = Path(tmp_path)
        df.to_parquet(tmp_file, index=False, engine="pyarrow")
        tmp_file.replace(cache_path)
        logger.info(
            "Saved %d indicator rows to Parquet cache: %s",
            len(df),
            cache_path,
        )
    except Exception as exc:
        logger.error("Failed to write indicators Parquet cache: %s", exc)
        tmp_file = Path(tmp_path)
        if tmp_file.exists():
            tmp_file.unlink()


def compute_market_indicators(
    raw_df: pd.DataFrame,
    benchmark_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Computes all technical and quantitative indicators grouped by stock symbol."""
    if raw_df.empty:
        return raw_df

    if benchmark_df is None:
        benchmark_df = get_benchmark_returns("NIFTY_500")

    return TechnicalCalculator.apply_all_indicators(raw_df, benchmark_df=benchmark_df)


def get_latest_market_snapshot(with_indicators_df: pd.DataFrame) -> pd.DataFrame:
    """Extracts the most recent trading session row for each symbol."""
    if with_indicators_df.empty:
        return pd.DataFrame()

    latest = (
        with_indicators_df.sort_values(by=["symbol", "date"])
        .groupby("symbol", as_index=False)
        .last()
    )
    return latest


def load_indicator_snapshot(
    use_adjusted: bool = True,
    force_refresh: bool = False,
    cache_path: Path = INDICATORS_CACHE_PATH,
) -> tuple[pd.DataFrame, pd.DataFrame, bool, str]:
    """Unified data loader returning the latest market snapshot and full history.

    Checks the local pre-computed Parquet cache first. If fresh against MySQL
    max(date), reads directly from Parquet. Otherwise queries MySQL, computes
    indicators, updates the Parquet cache atomically, and returns.

    Returns:
        tuple of (snapshot_df, full_indicators_df, is_live, status_msg)
    """
    # 1. Check if cache exists and is fresh
    if not force_refresh and cache_path.exists():
        try:
            cached_df = pd.read_parquet(cache_path)
            if not cached_df.empty and "date" in cached_df.columns:
                cached_df["date"] = pd.to_datetime(cached_df["date"]).dt.date
                max_cache_date = cached_df["date"].max()

                # Check MySQL latest date
                if check_connection():
                    with engine.connect() as conn:
                        res = conn.execute(text("SELECT MAX(date) FROM daily_prices")).scalar()
                    db_max_date = pd.to_datetime(res).date() if res else None

                    if db_max_date is None or max_cache_date >= db_max_date:
                        snapshot = get_latest_market_snapshot(cached_df)
                        msg = f"Parquet Cache: {len(snapshot)} symbols (as of {max_cache_date})"
                        return snapshot, cached_df, True, msg
                else:
                    snapshot = get_latest_market_snapshot(cached_df)
                    msg = (
                        f"Parquet Cache (Offline): {len(snapshot)} symbols (as of {max_cache_date})"
                    )
                    return snapshot, cached_df, False, msg
        except Exception as exc:
            logger.warning("Error reading Parquet cache (%s), recomputing...", exc)

    # 2. Query MySQL, compute indicators, and save cache
    raw_df, is_live, status_msg = load_raw_market_data(use_adjusted=use_adjusted)
    if raw_df.empty:
        return pd.DataFrame(), pd.DataFrame(), is_live, status_msg

    benchmark_df = get_benchmark_returns("NIFTY_500")
    with_indicators = compute_market_indicators(raw_df, benchmark_df=benchmark_df)
    save_indicator_parquet_cache(with_indicators, cache_path=cache_path)
    snapshot = get_latest_market_snapshot(with_indicators)

    return snapshot, with_indicators, is_live, status_msg


def ensure_data_initialized() -> bool:
    """Check if database has data; if empty and DB is online, automatically run ETL pipeline.

    Also ensures the pre-computed indicator Parquet cache exists so the
    Dashboard can display data even when the ETL pipeline reports
    "up_to_date" (no new rows downloaded).

    Returns True when data is available (either already present or freshly loaded).
    """
    if not check_connection():
        return False

    try:
        from src.db.session import init_db

        init_db()

        with engine.connect() as conn:
            cnt = conn.execute(text("SELECT COUNT(*) FROM daily_prices")).scalar() or 0

        if cnt == 0:
            logger.info(
                "daily_prices is empty on startup. Automatically running ETL pipeline backfill..."
            )
            from src.etl.pipeline import BhavcopyETL

            etl = BhavcopyETL(initial_days_back=30)
            etl.run()
            return True

        # MySQL has data but indicator cache is missing — build it now so
        # the Dashboard has something to show without needing another ETL run.
        if not INDICATORS_CACHE_PATH.exists():
            logger.info("MySQL has %d rows but indicator cache is missing — rebuilding...", cnt)
            load_indicator_snapshot(use_adjusted=True, force_refresh=True)

        return True
    except Exception as exc:
        logger.warning("Error during automatic database initialization: %s", exc)
    return False


def get_market_data(use_adjusted: bool = True) -> dict[str, Any]:
    """Unified cached market data provider for Streamlit UI."""
    ensure_data_initialized()
    snapshot, with_indicators, is_live, status_msg = load_indicator_snapshot(
        use_adjusted=use_adjusted
    )
    return {
        "raw": with_indicators,
        "indicators": with_indicators,
        "snapshot": snapshot,
        "is_live": is_live,
        "status_msg": status_msg,
    }


def clear_market_data_cache() -> None:
    """Clear market data cache on ETL updates."""
    try:
        import streamlit as st

        st.cache_data.clear()
    except Exception:
        pass


__all__ = [
    "INDICATORS_CACHE_PATH",
    "clear_market_data_cache",
    "compute_market_indicators",
    "ensure_data_initialized",
    "get_benchmark_returns",
    "get_index_constituents",
    "get_latest_market_snapshot",
    "get_market_data",
    "load_indicator_snapshot",
    "load_raw_market_data",
    "save_indicator_parquet_cache",
]
