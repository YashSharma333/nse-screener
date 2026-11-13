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
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from config.settings import INDICATOR_HISTORY_YEARS, INDICATOR_WARMUP_BARS, setup_logging
from src.computation.indicators import INDICATORS_SCHEMA_VERSION, TechnicalCalculator
from src.db.models import StockMaster
from src.db.session import check_connection, engine, get_db
from src.ingestion.benchmark import canonical_index_code
from src.ingestion.index_constituents import get_index_constituents

setup_logging()
logger = logging.getLogger(__name__)

INDICATORS_CACHE_PATH = Path("data/processed/indicators.parquet")
INDICATORS_META_PATH = Path("data/processed/indicators.meta.json")


def get_universe_symbols(session: Session | None = None) -> set[str]:
    """Retrieve active universe symbols from ``stock_master``.

    Returns
    -------
    set[str]
        Set of ticker symbols where ``in_universe == True``. If ``stock_master``
        table is empty (0 records), logs a warning and returns an empty set.
    """
    if not check_connection():
        logger.warning("Database offline; cannot query stock_master universe.")
        return set()

    def _query(s: Session) -> set[str]:
        total_count = s.scalar(select(func.count()).select_from(StockMaster)) or 0
        if total_count == 0:
            logger.warning("stock_master table is empty; falling back to NIFTY-constituent filter.")
            return set()

        stmt = select(StockMaster.symbol).where(StockMaster.in_universe.is_(True))
        return set(s.scalars(stmt).all())

    try:
        if session is not None:
            return _query(session)
        with get_db() as s:
            return _query(s)
    except Exception as exc:
        logger.warning("Error querying stock_master universe (%s); falling back.", exc)
        return set()


def load_raw_market_data(
    use_adjusted: bool = True,
    start_date: date | None = None,
    end_date: date | None = None,
    symbols: Sequence[str] | None = None,
) -> tuple[pd.DataFrame, bool, str]:
    """Fetch daily prices from MySQL with optional date range and symbol filtering.

    Args:
        use_adjusted: Whether to coalesce split/bonus adjusted price columns.
        start_date: Optional inclusive start date.
        end_date: Optional inclusive end date.
        symbols: Optional collection of ticker symbols to filter.

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

    where_clauses = []
    params: dict[str, Any] = {}
    if start_date is not None:
        where_clauses.append("date >= :start_date")
        params["start_date"] = start_date
    if end_date is not None:
        where_clauses.append("date <= :end_date")
        params["end_date"] = end_date
    if symbols is not None and len(symbols) > 0:
        where_clauses.append("symbol IN :symbols")
        params["symbols"] = tuple(symbols)

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    query = text(f"""
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
        {where_sql}
        ORDER BY symbol ASC, date ASC
    """)

    try:
        with engine.connect() as conn:
            df = pd.read_sql(query, conn, params=params if params else None)

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
            "indicator_history_years": INDICATOR_HISTORY_YEARS,
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
        sorted_df = (
            df.sort_values(by=["symbol", "date"]).reset_index(drop=True)
            if {"symbol", "date"}.issubset(df.columns)
            else df
        )
        sorted_df.to_parquet(
            tmp_file,
            index=False,
            engine="pyarrow",
            row_group_size=50000,
        )
        tmp_file.replace(cache_path)

        # Build and write metadata sidecar
        meta = meta_dict.copy() if meta_dict else get_db_cache_metadata()
        meta["row_count"] = len(df)
        meta["indicators_schema_version"] = INDICATORS_SCHEMA_VERSION
        if "indicator_history_years" not in meta:
            meta["indicator_history_years"] = INDICATOR_HISTORY_YEARS

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


def _date_literal(schema: Any, value: Any) -> Any:
    """Convert ``value`` to an Arrow scalar matching the type of the cache's ``date`` column.

    Arrow does not compare ``date32`` columns with strings, so filters must use a value of the
    same type as the stored column (``date32`` normally; timestamp or string for older caches).
    """
    import pyarrow as pa

    day = pd.Timestamp(value).date()
    field_type = schema.field("date").type
    if pa.types.is_date(field_type):
        return pa.scalar(day, type=field_type)
    if pa.types.is_timestamp(field_type):
        return pa.scalar(pd.Timestamp(day), type=field_type)
    return pa.scalar(day.isoformat(), type=pa.string())


def load_indicator_snapshot(
    use_adjusted: bool = True,
    force_refresh: bool = False,
    cache_path: Path = INDICATORS_CACHE_PATH,
    snapshot_only: bool = False,
    history_years: int | None = INDICATOR_HISTORY_YEARS,
) -> tuple[pd.DataFrame, pd.DataFrame, bool, str]:
    """Unified data loader returning the latest market snapshot and full history.

    Verifies freshness against indicators.meta.json and current database metrics
    (db_max_date, adj_factors_count, adj_factors_max_detected_at, benchmark_max_date,
    indicators_schema_version, indicator_history_years).
    Recomputes and updates cache if any metric differs.
    When snapshot_only=True, loads ONLY the latest date rows via PyArrow dataset filter.
    """
    meta_path = cache_path.with_suffix(".meta.json")

    # 1. Check if cache and metadata exist and are fresh
    if not force_refresh and cache_path.exists() and meta_path.exists():
        try:
            with meta_path.open("r", encoding="utf-8") as f:
                meta = json.load(f)

            schema_ver = meta.get("indicators_schema_version")
            cached_years = meta.get("indicator_history_years")
            years_match = (
                (history_years is None) or (cached_years is None) or (cached_years == history_years)
            )
            if schema_ver == INDICATORS_SCHEMA_VERSION and years_match:
                is_fresh = False
                is_live = False
                if check_connection():
                    is_live = True
                    db_meta = get_db_cache_metadata()
                    years_fresh = (
                        (cached_years is None)
                        or (db_meta.get("indicator_history_years") is None)
                        or (db_meta.get("indicator_history_years") == cached_years)
                    )
                    is_fresh = (
                        db_meta.get("db_max_date") == meta.get("db_max_date")
                        and db_meta.get("adj_factors_count") == meta.get("adj_factors_count")
                        and db_meta.get("adj_factors_max_detected_at")
                        == meta.get("adj_factors_max_detected_at")
                        and db_meta.get("benchmark_max_date") == meta.get("benchmark_max_date")
                        and years_fresh
                    )
                else:
                    # Offline: return cache as best-effort
                    is_fresh = True
                    is_live = False

                if is_fresh:
                    max_cache_date = meta.get("db_max_date")

                    if snapshot_only:
                        import pyarrow.dataset as ds
                        import pyarrow.parquet as pq

                        if not max_cache_date:
                            dates_tbl = pq.read_table(cache_path, columns=["date"])
                            max_cache_date = dates_tbl["date"].to_pandas().max()

                        dataset = ds.dataset(cache_path, format="parquet")
                        latest = _date_literal(dataset.schema, max_cache_date)
                        table = dataset.to_table(filter=ds.field("date") == latest)
                        snapshot = table.to_pandas()
                        if not snapshot.empty and "date" in snapshot.columns:
                            snapshot["date"] = pd.to_datetime(snapshot["date"]).dt.date
                        tag = " (Offline)" if not is_live else ""
                        msg = (
                            f"Parquet Cache{tag}: {len(snapshot)} symbols (as of {max_cache_date})"
                        )
                        return snapshot, pd.DataFrame(), is_live, msg

                    cached_df = pd.read_parquet(cache_path)
                    if not cached_df.empty and "date" in cached_df.columns:
                        cached_df["date"] = pd.to_datetime(cached_df["date"]).dt.date
                        max_cache_date = cached_df["date"].max()
                        snapshot = get_latest_market_snapshot(cached_df)
                        tag = " (Offline)" if not is_live else ""
                        msg = (
                            f"Parquet Cache{tag}: {len(snapshot)} symbols (as of {max_cache_date})"
                        )
                        return snapshot, cached_df, is_live, msg
        except Exception as exc:
            logger.warning("Error reading Parquet cache or metadata (%s), recomputing...", exc)

    # 2. Query MySQL, compute indicators, and save cache
    cutoff_date: date | None = None
    warmup_start_date: date | None = None
    actual_warmup_bars = 0
    max_date: date | None = None

    if check_connection():
        try:
            with engine.connect() as conn:
                max_d_val = conn.execute(text("SELECT MAX(date) FROM daily_prices")).scalar()
                if max_d_val is not None:
                    max_date = pd.Timestamp(max_d_val).date()

            if max_date is not None and history_years is not None and history_years > 0:
                try:
                    cutoff_date = max_date.replace(year=max_date.year - history_years)
                except ValueError:
                    cutoff_date = max_date.replace(year=max_date.year - history_years, day=28)

                with engine.connect() as conn:
                    dates_res = conn.execute(
                        text(
                            "SELECT DISTINCT date FROM daily_prices "
                            "WHERE date <= :max_date ORDER BY date ASC"
                        ),
                        {"max_date": max_date},
                    ).fetchall()
                all_dates = [pd.Timestamp(r[0]).date() for r in dates_res]

                import bisect

                idx = bisect.bisect_left(all_dates, cutoff_date)
                needed_warmup = max(INDICATOR_WARMUP_BARS, 300)
                if idx > 0:
                    warmup_idx = max(0, idx - needed_warmup)
                    warmup_start_date = all_dates[warmup_idx]
                    actual_warmup_bars = idx - warmup_idx
                elif all_dates:
                    warmup_start_date = all_dates[0]
                    actual_warmup_bars = 0
                else:
                    warmup_start_date = cutoff_date
                    actual_warmup_bars = 0
        except Exception as exc:
            logger.warning("Could not determine window dates (%s); falling back to all dates", exc)

    raw_df, is_live, status_msg = load_raw_market_data(
        use_adjusted=use_adjusted,
        start_date=warmup_start_date,
        end_date=max_date,
    )
    if raw_df.empty:
        return pd.DataFrame(), pd.DataFrame(), is_live, status_msg

    benchmark_df = get_benchmark_returns("NIFTY 500", start=warmup_start_date, end=max_date)
    with_indicators = compute_market_indicators(raw_df, benchmark_df=benchmark_df)

    # Filter to keep only the last N years of rows
    if cutoff_date is not None and not with_indicators.empty and "date" in with_indicators.columns:
        kept_indicators = with_indicators[with_indicators["date"] >= cutoff_date].copy()
    else:
        kept_indicators = with_indicators

    meta = get_db_cache_metadata()
    if cutoff_date is not None:
        meta["indicator_history_years"] = history_years
        meta["history_window_start"] = str(cutoff_date)
        meta["history_window_end"] = str(max_date)
        meta["warmup_bars"] = actual_warmup_bars

    save_indicator_parquet_cache(kept_indicators, cache_path=cache_path, meta_dict=meta)
    snapshot = get_latest_market_snapshot(kept_indicators)

    return snapshot, kept_indicators, is_live, status_msg


def refresh_indicator_cache(
    use_adjusted: bool = True,
    cache_path: Path = INDICATORS_CACHE_PATH,
    history_years: int | None = INDICATOR_HISTORY_YEARS,
) -> tuple[pd.DataFrame, pd.DataFrame, bool, str]:
    """Force recomputing and saving indicators and sidecar metadata."""
    logger.info("Refreshing indicators cache (use_adjusted=%s)...", use_adjusted)
    return load_indicator_snapshot(
        use_adjusted=use_adjusted,
        force_refresh=True,
        cache_path=cache_path,
        history_years=history_years,
    )
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


def load_indicator_history(
    columns: Sequence[str] | None = None,
    symbols: Sequence[str] | None = None,
    start: date | None = None,
    end: date | None = None,
    cache_path: Path = INDICATORS_CACHE_PATH,
) -> pd.DataFrame:
    """Load indicator history with PyArrow column projection and predicate pushdown filters.

    Ensures the backend queries only the requested columns and symbols/dates without
    loading the entire multi-year historical dataset into memory.
    """
    if not cache_path.exists():
        _snapshot, full, _live, _msg = load_indicator_snapshot(cache_path=cache_path)
        if full.empty:
            return pd.DataFrame()

    import pyarrow.dataset as ds

    dataset = ds.dataset(cache_path, format="parquet")

    filter_expr = None
    if symbols:
        sym_filter = ds.field("symbol").isin(list(symbols))
        filter_expr = sym_filter if filter_expr is None else (filter_expr & sym_filter)
    if start:
        start_filter = ds.field("date") >= _date_literal(dataset.schema, start)
        filter_expr = start_filter if filter_expr is None else (filter_expr & start_filter)
    if end:
        end_filter = ds.field("date") <= _date_literal(dataset.schema, end)
        filter_expr = end_filter if filter_expr is None else (filter_expr & end_filter)

    proj_columns = None
    if columns is not None:
        proj_columns = list(columns)
        if "symbol" not in proj_columns:
            proj_columns.insert(0, "symbol")
        if "date" not in proj_columns:
            proj_columns.insert(1, "date")

    table = dataset.to_table(columns=proj_columns, filter=filter_expr)
    df = table.to_pandas()
    if not df.empty and "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def clear_market_data_cache() -> None:
    """Reset market data cache metadata state (UI cache clearing resides in ui/data.py)."""
    logger.debug("Market data cache clear requested.")


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
    "get_universe_symbols",
    "load_indicator_history",
    "load_indicator_snapshot",
    "load_raw_market_data",
    "refresh_indicator_cache",
    "save_indicator_parquet_cache",
]
