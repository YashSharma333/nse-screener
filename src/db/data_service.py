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
from sqlalchemy import text

from config import settings
from config.settings import INDICATOR_HISTORY_YEARS, INDICATOR_WARMUP_BARS, setup_logging
from src.computation.cache_reader import date_literal, fill_missing_market_cap
from src.computation.indicators import INDICATORS_SCHEMA_VERSION
from src.computation.indicators.resolver import ensure_columns
from src.db.session import check_connection, engine

setup_logging()
logger = logging.getLogger(__name__)


def load_raw_market_data(
    start_date: date | None = None,
    end_date: date | None = None,
    symbols: Sequence[str] | None = None,
) -> tuple[pd.DataFrame, bool, str]:
    """Fetch daily prices from MySQL with optional date range and symbol filtering.

    Args:
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
            series,
            isin,
            open,
            high,
            low,
            close,
            volume,
            prev_close,
            market_cap,
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

        # Rows without adjusted prices fall back to RAW below: wrong around splits, so say so.
        missing_adjusted = df.loc[df["adj_close"].isna(), "symbol"].nunique()
        if missing_adjusted:
            logger.warning(
                "%d symbols have rows without adjusted prices; using RAW prices for them. "
                "Run `python run_pipeline.py --update` or --rebuild-adjusted.",
                missing_adjusted,
            )
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


def get_db_cache_metadata(connection: Any = None) -> dict[str, Any]:
    """Query current database state for cache validation."""
    if connection is None and not check_connection():
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
    cache_path: Path | None = None,
    meta_dict: dict[str, Any] | None = None,
) -> None:
    """Atomically write processed indicator DataFrame and sidecar metadata JSON."""
    cache_path = cache_path or settings.INDICATORS_CACHE_PATH
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


def compute_market_indicators(raw_df: pd.DataFrame) -> pd.DataFrame:
    """The cache table: the price columns plus the approved indicator list, nothing else.

    Why: every other indicator is computed when it is asked for (``cache_reader``), so the cache
    stays small. Rows are sorted by symbol, then date, because the indicator functions never sort.
    """
    if raw_df.empty:
        return raw_df

    base = [name for name in settings.CACHE_BASE_COLUMNS if name in raw_df.columns]
    table = raw_df[base].sort_values(["symbol", "date"], kind="stable").reset_index(drop=True)
    return ensure_columns(table, settings.PRECOMPUTED_INDICATORS)


def save_benchmarks_parquet(path: Path | None = None) -> None:
    """Write every stored index close (index_code, date, close) to the small benchmarks file.

    Why: the dashboard and the condition engine may not open MySQL, but an index part such as
    "NIFTY 500 close" needs those closes. The file is tiny, so all indexes are written.
    """
    path = path or settings.BENCHMARKS_CACHE_PATH
    try:
        with engine.connect() as conn:
            query = text("SELECT index_code, date, close FROM benchmark_prices")
            benchmarks = pd.read_sql(query, conn)
    except Exception as exc:  # the benchmarks are optional: the stock cache is still good
        logger.warning("Could not read benchmark prices for %s: %s", path, exc)
        return
    if benchmarks.empty:
        return

    benchmarks["date"] = pd.to_datetime(benchmarks["date"]).dt.date
    benchmarks["close"] = pd.to_numeric(benchmarks["close"], errors="coerce")
    benchmarks = benchmarks.sort_values(["index_code", "date"]).reset_index(drop=True)

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    benchmarks.to_parquet(tmp_path, index=False, engine="pyarrow")
    tmp_path.replace(path)
    logger.info("Saved %d benchmark rows to %s", len(benchmarks), path)


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
    force_refresh: bool = False,
    cache_path: Path | None = None,
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
    cache_path = cache_path or settings.INDICATORS_CACHE_PATH
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
                        latest = date_literal(dataset.schema, max_cache_date)
                        table = dataset.to_table(filter=ds.field("date") == latest)
                        snapshot = table.to_pandas()
                        if not snapshot.empty and "date" in snapshot.columns:
                            snapshot["date"] = pd.to_datetime(snapshot["date"]).dt.date
                        if "market_cap" in snapshot.columns and snapshot["market_cap"].isna().any():
                            # Only 3 narrow columns are read, so this stays light on memory.
                            mcap_history = pq.read_table(
                                cache_path, columns=["symbol", "date", "market_cap"]
                            ).to_pandas()
                            snapshot = fill_missing_market_cap(snapshot, mcap_history)
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
        start_date=warmup_start_date,
        end_date=max_date,
    )
    if raw_df.empty:
        return pd.DataFrame(), pd.DataFrame(), is_live, status_msg

    with_indicators = compute_market_indicators(raw_df)

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
    save_benchmarks_parquet()
    snapshot = get_latest_market_snapshot(kept_indicators)

    return snapshot, kept_indicators, is_live, status_msg
