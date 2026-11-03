"""Market Data Service Layer.

=========================
Handles querying market data from MySQL with proactive error handling,
caching with pre-computed Parquet files and sidecar metadata,
technical indicator computations, corporate action adjustments,
and benchmark data retrieval.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text

from config.settings import setup_logging
from src.computation.indicators import INDICATORS_SCHEMA_VERSION, TechnicalCalculator
from src.db.session import check_connection, engine
from src.ingestion.benchmark import canonical_index_code
from src.ingestion.index_constituents import get_index_constituents

setup_logging()
logger = logging.getLogger(__name__)

INDICATORS_CACHE_PATH = Path("data/processed/indicators.parquet")
INDICATORS_META_PATH = Path("data/processed/indicators.meta.json")


def load_raw_market_data(use_adjusted: bool = True) -> tuple[pd.DataFrame, bool, str]:
    """Fetch daily prices from MySQL.

    Args:
        use_adjusted: Whether to coalesce split/bonus adjusted price columns.

    Returns:
        tuple of (DataFrame, is_live_boolean, status_message)
    """
    if not check_connection():
        logger.warning("MySQL connection unavailable. Returning empty dataset.")
        return (
            pd.DataFrame(),
            False,
            "MySQL offline. Verify database credentials and connection.",
        )

    query = text("""
        SELECT
            id,
            symbol,
            date,
            open,
            high,
            low,
            close,
            volume,
            prev_close,
            index_name,
            adj_open,
            adj_high,
            adj_low,
            adj_close,
            adj_volume
        FROM daily_prices
        ORDER BY symbol ASC, date ASC
    """)

    try:
        with engine.connect() as conn:
            df = pd.read_sql(query, conn)

        if df.empty:
            logger.info("daily_prices table exists but contains 0 records.")
            return (
                pd.DataFrame(),
                True,
                "MySQL connected, but table 'daily_prices' is empty.",
            )

        df["date"] = pd.to_datetime(df["date"]).dt.date
        numeric_cols = [
            "open",
            "high",
            "low",
            "close",
            "volume",
            "prev_close",
            "adj_open",
            "adj_high",
            "adj_low",
            "adj_close",
            "adj_volume",
        ]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        if use_adjusted:
            df["open"] = df["adj_open"].combine_first(df["open"])
            df["high"] = df["adj_high"].combine_first(df["high"])
            df["low"] = df["adj_low"].combine_first(df["low"])
            df["close"] = df["adj_close"].combine_first(df["close"])
            df["volume"] = df["adj_volume"].combine_first(df["volume"])

        symbols_count = df["symbol"].nunique()
        dates_count = df["date"].nunique()
        status_msg = (
            f"MySQL live: {len(df):,} records ({symbols_count} symbols across {dates_count} dates)"
        )
        return df, True, status_msg

    except Exception as exc:
        logger.error("MySQL query failed: %s", exc, exc_info=True)
        return (
            pd.DataFrame(),
            False,
            f"Database query error: {exc}. Verify database schema.",
        )


def get_benchmark_returns(
    index_code: str = "NIFTY 500",
    start: date | None = None,
    end: date | None = None,
) -> pd.DataFrame:
    """Fetch benchmark index prices and calculate daily returns.

    Args:
        index_code: Target index name (e.g., 'NIFTY 500', 'NIFTY_500')
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


def get_db_cache_metadata(connection: Any = None) -> dict[str, Any]:
    """Query current database state for cache validation."""
    if not check_connection():
        return {}

    def _query(conn: Any) -> dict[str, Any]:
        db_max = conn.execute(text("SELECT MAX(date) FROM daily_prices")).scalar()
        adj_res = conn.execute(
            text("SELECT COUNT(*), MAX(detected_at) FROM adj_factors")
        ).fetchone()
        adj_count = adj_res[0] if adj_res else 0
        adj_max_detected = str(adj_res[1]) if adj_res and adj_res[1] else None

        bench_max = conn.execute(
            text("SELECT MAX(date) FROM benchmark_prices WHERE index_code = 'NIFTY 500'")
        ).scalar()

        return {
            "db_max_date": str(db_max) if db_max else None,
            "adj_factors_count": int(adj_count),
            "adj_factors_max_detected_at": adj_max_detected,
            "benchmark_max_date": str(bench_max) if bench_max else None,
            "indicators_schema_version": INDICATORS_SCHEMA_VERSION,
        }

    try:
        if connection is not None:
            return _query(connection)
        with engine.connect() as conn:
            return _query(conn)
    except Exception as exc:
        logger.warning("Could not query DB cache metadata: %s", exc)
        return {}


def save_indicator_parquet_cache(
    df: pd.DataFrame,
    cache_path: Path = INDICATORS_CACHE_PATH,
    meta_dict: dict[str, Any] | None = None,
) -> None:
    """Atomically write processed indicator DataFrame and sidecar metadata JSON."""
    if df.empty:
        return

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path = cache_path.with_suffix(".meta.json")

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

        # Build and write metadata sidecar
        meta = meta_dict.copy() if meta_dict else get_db_cache_metadata()
        meta["row_count"] = len(df)
        meta["indicators_schema_version"] = INDICATORS_SCHEMA_VERSION

        tmp_meta = meta_path.with_suffix(".tmp.json")
        with tmp_meta.open("w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
        tmp_meta.replace(meta_path)

        logger.info(
            "Saved %d indicator rows to Parquet cache with sidecar metadata: %s",
            len(df),
            cache_path,
        )
    except Exception as exc:
        logger.error("Failed to write indicators Parquet cache or metadata: %s", exc)
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
        benchmark_df = get_benchmark_returns("NIFTY 500")

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

    Verifies freshness against indicators.meta.json and current database metrics
    (db_max_date, adj_factors_count, adj_factors_max_detected_at, benchmark_max_date,
    indicators_schema_version). Recomputes and updates cache if any metric differs.
    """
    meta_path = cache_path.with_suffix(".meta.json")

    # 1. Check if cache and metadata exist and are fresh
    if not force_refresh and cache_path.exists() and meta_path.exists():
        try:
            with meta_path.open("r", encoding="utf-8") as f:
                meta = json.load(f)

            schema_ver = meta.get("indicators_schema_version")
            if schema_ver == INDICATORS_SCHEMA_VERSION:
                cached_df = pd.read_parquet(cache_path)
                if not cached_df.empty and "date" in cached_df.columns:
                    cached_df["date"] = pd.to_datetime(cached_df["date"]).dt.date
                    max_cache_date = cached_df["date"].max()

                    if check_connection():
                        db_meta = get_db_cache_metadata()
                        # Verify multi-metric alignment
                        is_fresh = (
                            db_meta.get("db_max_date") == meta.get("db_max_date")
                            and db_meta.get("adj_factors_count") == meta.get("adj_factors_count")
                            and db_meta.get("adj_factors_max_detected_at")
                            == meta.get("adj_factors_max_detected_at")
                            and db_meta.get("benchmark_max_date") == meta.get("benchmark_max_date")
                            and meta.get("row_count") == len(cached_df)
                        )

                        if is_fresh:
                            snapshot = get_latest_market_snapshot(cached_df)
                            msg = f"Parquet Cache: {len(snapshot)} symbols (as of {max_cache_date})"
                            return snapshot, cached_df, True, msg
                    else:
                        # Offline: return cache as best-effort
                        snapshot = get_latest_market_snapshot(cached_df)
                        msg = (
                            f"Parquet Cache (Offline): {len(snapshot)} symbols "
                            f"(as of {max_cache_date})"
                        )
                        return snapshot, cached_df, False, msg
        except Exception as exc:
            logger.warning("Error reading Parquet cache or metadata (%s), recomputing...", exc)

    # 2. Query MySQL, compute indicators, and save cache
    raw_df, is_live, status_msg = load_raw_market_data(use_adjusted=use_adjusted)
    if raw_df.empty:
        return pd.DataFrame(), pd.DataFrame(), is_live, status_msg

    benchmark_df = get_benchmark_returns("NIFTY 500")
    with_indicators = compute_market_indicators(raw_df, benchmark_df=benchmark_df)
    save_indicator_parquet_cache(with_indicators, cache_path=cache_path)
    snapshot = get_latest_market_snapshot(with_indicators)

    return snapshot, with_indicators, is_live, status_msg


def refresh_indicator_cache(
    use_adjusted: bool = True,
    cache_path: Path = INDICATORS_CACHE_PATH,
) -> tuple[pd.DataFrame, pd.DataFrame, bool, str]:
    """Force recomputing and saving indicators and sidecar metadata."""
    logger.info("Refreshing indicators cache (use_adjusted=%s)...", use_adjusted)
    return load_indicator_snapshot(
        use_adjusted=use_adjusted, force_refresh=True, cache_path=cache_path
    )


def ensure_data_initialized() -> dict[str, Any]:
    """Inspect data initialization status without triggering blocking downloads."""
    if not check_connection():
        return {"online": False, "has_db_data": False, "has_cache": False}

    has_db_data = False
    try:
        from src.db.session import init_db

        init_db()
        with engine.connect() as conn:
            cnt = conn.execute(text("SELECT COUNT(*) FROM daily_prices")).scalar() or 0
        has_db_data = cnt > 0
    except Exception as exc:
        logger.warning("Error checking DB status: %s", exc)

    has_cache = INDICATORS_CACHE_PATH.exists() and INDICATORS_META_PATH.exists()

    return {
        "online": True,
        "has_db_data": has_db_data,
        "has_cache": has_cache,
    }


def get_market_data(use_adjusted: bool = True) -> dict[str, Any]:
    """Unified cached market data provider for Streamlit UI."""
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
    "INDICATORS_META_PATH",
    "clear_market_data_cache",
    "compute_market_indicators",
    "ensure_data_initialized",
    "get_benchmark_returns",
    "get_db_cache_metadata",
    "get_index_constituents",
    "get_latest_market_snapshot",
    "get_market_data",
    "load_indicator_snapshot",
    "load_raw_market_data",
    "refresh_indicator_cache",
    "save_indicator_parquet_cache",
]
