"""Corporate Actions Adjustment Engine
===================================
Detects splits, bonuses, and capital actions using Bhavcopy PREV_CLOSE comparisons,
calculates backwards cumulative adjustment factors, and reconstructs historical
unadjusted OHLCV price series into smooth continuous curves.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from src.db.models import AdjFactor, DailyPrice
from src.db.session import engine as default_engine
from src.db.session import get_db
from src.db.upsert import upsert_rows

logger = logging.getLogger("src.etl.adjust")

REPORTS_DIR = Path("reports")


def detect_events(
    df: pd.DataFrame,
    global_trading_days: Sequence[date] | None = None,
    non_trading_days: set[date] | None = None,
    total_db_symbols: int | None = None,
) -> pd.DataFrame:
    """Detect corporate actions (splits, bonuses) by comparing PREV_CLOSE to prior CLOSE.

    Parameters
    ----------
    df: pd.DataFrame
        DataFrame containing at least:
        ``['symbol', 'date', 'close', 'prev_close']`` sorted by symbol and date.
    global_trading_days: Sequence[date] | None
        Ordered sequence of all valid market trading dates.
    non_trading_days: set[date] | None
        Set of known market holidays / non-trading weekdays.
    total_db_symbols: int | None
        Total number of active symbols in the database, used as the denominator for
        cross-sectional suppression guard.

    Returns
    -------
    pd.DataFrame
        Detected events with columns:
        ``['symbol', 'date', 'ratio', 'factor', 'ratio_kind', 'close_prev', 'prev_close']``
    """
    keep_cols = ["symbol", "date", "ratio", "factor", "ratio_kind", "close_prev", "prev_close"]
    if df.empty or "prev_close" not in df.columns or "close" not in df.columns:
        return pd.DataFrame(columns=keep_cols)

    work_df = df.copy()
    if not pd.api.types.is_datetime64_any_dtype(work_df["date"]):
        work_df["date"] = pd.to_datetime(work_df["date"])

    work_df = work_df.sort_values(by=["symbol", "date"]).reset_index(drop=True)

    # Calculate previous row's raw close and date per symbol
    work_df["close_prev"] = work_df.groupby("symbol")["close"].shift(1)
    work_df["date_prev"] = work_df.groupby("symbol")["date"].shift(1)

    # First row of each symbol has close_prev NaN — ignored
    valid_mask = work_df["close_prev"].notna() & work_df["prev_close"].notna()
    work = work_df[valid_mask].copy()

    if work.empty:
        return pd.DataFrame(columns=keep_cols)

    # 1. Trading-day continuity check
    cur_dates = work["date"].dt.date.to_numpy()
    prev_dates = work["date_prev"].dt.date.to_numpy()

    holidays = non_trading_days or set()

    if global_trading_days is not None and len(global_trading_days) > 1:
        sorted_g_days = sorted(set(global_trading_days))
        prev_g_map = {sorted_g_days[i]: sorted_g_days[i - 1] for i in range(1, len(sorted_g_days))}

        consecutive = np.array(
            [prev_dates[i] == prev_g_map.get(cur_dates[i]) for i in range(len(cur_dates))],
            dtype=bool,
        )
    else:
        consecutive = np.zeros(len(cur_dates), dtype=bool)
        for i in range(len(cur_dates)):
            d_prev = prev_dates[i]
            d_curr = cur_dates[i]
            bus_count = int(np.busday_count(d_prev, d_curr))
            if bus_count <= 1:
                consecutive[i] = True
            elif holidays:
                days_between = pd.date_range(
                    d_prev + pd.Timedelta(days=1), d_curr - pd.Timedelta(days=1)
                )
                intermediate_weekdays = [d.date() for d in days_between if d.weekday() < 5]
                if all(d in holidays for d in intermediate_weekdays):
                    consecutive[i] = True

    skipped_count = int((~consecutive).sum())
    if skipped_count > 0:
        logger.info(
            "Skipped ratio check for %d rows with missing preceding trading day", skipped_count
        )
        work = work[consecutive].copy()

    if work.empty:
        return pd.DataFrame(columns=keep_cols)

    # ratio_t = prev_close_t / close_(t-1)
    close_prev_nonzero = work["close_prev"].replace(0, np.nan)
    work["ratio"] = (work["prev_close"] / close_prev_nonzero).astype(float)
    work = work[work["ratio"].notna()].copy()

    # Events where abs(ratio - 1) > 0.001
    event_mask = (work["ratio"] - 1.0).abs() > 0.001
    events = work[event_mask].copy()

    if events.empty:
        return pd.DataFrame(columns=keep_cols)

    # 2. Cross-sectional guard:
    # Denominator must be total symbols in DB (Seed E).
    # If > 15% of total active symbols show an event on the same date, treat as market-wide gap.
    # Minimum universe size threshold is 20 symbols. If total symbols < 20, suppression is skipped.
    events["event_date"] = events["date"].dt.date
    n_total_symbols = (
        total_db_symbols if total_db_symbols is not None else work_df["symbol"].nunique()
    )

    if n_total_symbols >= 5:
        date_counts = events.groupby("event_date")["symbol"].nunique()
        bad_dates = date_counts[date_counts / n_total_symbols > 0.15].index.tolist()

        if bad_dates:
            for bad_d in bad_dates:
                pct = (date_counts[bad_d] / n_total_symbols) * 100.0
                logger.warning(
                    "Cross-sectional guard: %d / %d symbols (%.1f%%) showed events on %s. "
                    "Treating as data problem and skipping all events on this date.",
                    date_counts[bad_d],
                    n_total_symbols,
                    pct,
                    bad_d,
                )
            events = events[~events["event_date"].isin(bad_dates)].copy()

    if events.empty:
        return pd.DataFrame(columns=keep_cols)

    # Material if abs(ratio - 1) >= 0.02, else minor
    events["ratio_kind"] = np.where(
        (events["ratio"] - 1.0).abs() >= 0.02,
        "material",
        "minor",
    )
    events["factor"] = events["ratio"]
    events["date"] = events["date"].dt.date

    return events[keep_cols].reset_index(drop=True)


def cumulative_factors(dates: Sequence[date], events_df: pd.DataFrame) -> np.ndarray:
    """Compute cumulative backward adjustment factor for each date in a symbol series."""
    sorted_dates = pd.to_datetime(pd.Series(dates)).reset_index(drop=True)
    if events_df.empty:
        return np.ones(len(sorted_dates), dtype=np.float64)

    mat_events = events_df[events_df["ratio_kind"] == "material"].copy()
    if mat_events.empty:
        return np.ones(len(sorted_dates), dtype=np.float64)

    mat_events["date"] = pd.to_datetime(mat_events["date"])
    event_dates = mat_events["date"].to_numpy()
    event_factors = mat_events["factor"].to_numpy()

    date_matrix = sorted_dates.to_numpy()[:, None]  # Shape (N, 1)
    event_matrix = event_dates[None, :]  # Shape (1, E)

    applies = event_matrix > date_matrix  # Shape (N, E)
    factors_grid = np.where(applies, event_factors[None, :], 1.0)
    cum_factors = np.prod(factors_grid, axis=1)

    return cum_factors


def save_events_report(events_df: pd.DataFrame, file_path: Path | None = None) -> None:
    """Save detected events to a CSV report for review."""
    target_path = file_path or (REPORTS_DIR / "adj_events_review.csv")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    events_df.to_csv(target_path, index=False)
    logger.info("Saved %d detected events to %s", len(events_df), target_path)


def rebuild_adjusted(
    symbols: list[str] | None = None,
    session: Session | None = None,
    engine: Any = None,
) -> int:
    """Recompute corporate action adjustment factors and update daily_prices.

    Idempotent: updates adj_open, adj_high, adj_low, adj_close, adj_volume.
    Updates only rows whose adjusted values changed or are NULL.
    """
    target_engine = engine or default_engine
    if target_engine is None and session is None:
        logger.warning("Database engine offline. Skipping adjusted price rebuild.")
        return 0

    logger.info(
        "Rebuilding adjusted prices (symbols=%s)...", "all" if symbols is None else len(symbols)
    )

    if session is not None:
        conn = session.connection()
        close_conn = False
    else:
        conn = target_engine.connect()
        close_conn = True

    try:
        # Determine total symbol count in database for the cross-sectional guard
        total_syms_stmt = select(func.count(func.distinct(DailyPrice.symbol)))
        total_db_symbols = conn.execute(total_syms_stmt).scalar() or 0

        columns_to_select = [
            DailyPrice.symbol,
            DailyPrice.date,
            DailyPrice.open,
            DailyPrice.high,
            DailyPrice.low,
            DailyPrice.close,
            DailyPrice.prev_close,
            DailyPrice.volume,
            DailyPrice.adj_close,
        ]

        if symbols:
            stmt = (
                select(*columns_to_select)
                .where(DailyPrice.symbol.in_(symbols))
                .order_by(DailyPrice.symbol, DailyPrice.date)
            )
        else:
            stmt = select(*columns_to_select).order_by(DailyPrice.symbol, DailyPrice.date)

        df = pd.read_sql(stmt, conn)

        # Global trading days
        g_stmt = select(DailyPrice.date).distinct().order_by(DailyPrice.date)
        g_dates_df = pd.read_sql(g_stmt, conn)
        global_trading_days = (
            pd.to_datetime(g_dates_df["date"]).dt.date.tolist() if not g_dates_df.empty else None
        )
    finally:
        if close_conn:
            conn.close()

    if df.empty:
        logger.info("No prices to adjust.")
        return 0

    # 2. Detect events across the dataset with total database symbols denominator
    events_df = detect_events(
        df,
        global_trading_days=global_trading_days,
        total_db_symbols=total_db_symbols,
    )

    def _sync_db(active_session: Session) -> None:
        target_symbols = symbols or df["symbol"].unique().tolist()
        if target_symbols:
            del_stmt = delete(AdjFactor).where(AdjFactor.symbol.in_(target_symbols))
            active_session.execute(del_stmt)

        if not events_df.empty:
            ev_records = [
                {
                    "symbol": ev["symbol"],
                    "date": ev["date"],
                    "factor": float(ev["factor"]),
                    "ratio_kind": ev["ratio_kind"],
                }
                for ev in events_df.to_dict("records")
            ]
            upsert_rows(active_session, AdjFactor, ev_records)

    if session is not None:
        _sync_db(session)
    else:
        with get_db() as s:
            _sync_db(s)

    save_events_report(events_df)

    # 3. Vectorized adjusted price calculation per symbol, updating only changed/NULL rows
    updated_rows = 0
    grouped = df.groupby("symbol")
    update_sql = text("""
        UPDATE daily_prices
        SET adj_open = :b_adj_open,
            adj_high = :b_adj_high,
            adj_low = :b_adj_low,
            adj_close = :b_adj_close,
            adj_volume = :b_adj_volume
        WHERE symbol = :b_symbol AND date = :b_date
    """)

    def _apply_updates(active_session: Session) -> int:
        nonlocal updated_rows
        all_update_records = []

        for sym, sym_df in grouped:
            sym_events = (
                events_df[events_df["symbol"] == sym] if not events_df.empty else pd.DataFrame()
            )
            dates_list = sym_df["date"].tolist()
            cum_facts = cumulative_factors(dates_list, sym_events)

            open_vals = sym_df["open"].to_numpy(dtype=float)
            high_vals = sym_df["high"].to_numpy(dtype=float)
            low_vals = sym_df["low"].to_numpy(dtype=float)
            close_vals = sym_df["close"].to_numpy(dtype=float)
            vol_vals = sym_df["volume"].to_numpy(dtype=float)

            adj_open = np.round(open_vals * cum_facts, 4)
            adj_high = np.round(high_vals * cum_facts, 4)
            adj_low = np.round(low_vals * cum_facts, 4)
            adj_close = np.round(close_vals * cum_facts, 4)

            cum_nonzero = np.where(cum_facts != 0, cum_facts, 1.0)
            adj_volume = np.round(vol_vals / cum_nonzero).astype("int64")

            sym_dates = [
                d.date() if isinstance(d, pd.Timestamp) else d for d in sym_df["date"].tolist()
            ]

            existing_adj = sym_df["adj_close"].to_numpy() if "adj_close" in sym_df.columns else None

            for i in range(len(sym_dates)):
                target_c = float(adj_close[i]) if not np.isnan(adj_close[i]) else None
                # Update only rows whose values changed or are NULL
                if (
                    existing_adj is not None
                    and (ex_val := existing_adj[i]) is not None
                    and not np.isnan(ex_val)
                    and target_c is not None
                    and abs(float(ex_val) - target_c) < 1e-4
                ):
                    continue

                all_update_records.append(
                    {
                        "b_symbol": sym,
                        "b_date": sym_dates[i],
                        "b_adj_open": None if np.isnan(adj_open[i]) else float(adj_open[i]),
                        "b_adj_high": None if np.isnan(adj_high[i]) else float(adj_high[i]),
                        "b_adj_low": None if np.isnan(adj_low[i]) else float(adj_low[i]),
                        "b_adj_close": target_c,
                        "b_adj_volume": int(adj_volume[i]),
                    }
                )

        updated_rows = len(all_update_records)
        for b_start in range(0, len(all_update_records), 1000):
            batch = all_update_records[b_start : b_start + 1000]
            active_session.execute(update_sql, batch)

        logger.info("Adjusted prices update complete: modified %d rows.", updated_rows)
        return updated_rows

    if session is not None:
        return _apply_updates(session)
    else:
        with get_db() as s:
            return _apply_updates(s)


def adjust_new_rows(
    new_df: pd.DataFrame,
    session: Session | None = None,
    engine: Any = None,
) -> int:
    """Incrementally adjust newly loaded price rows.

    1. Checks if newly loaded rows contain any new material events by comparing
       against each symbol's last stored close in the database (Seed D).
    2. If a symbol has a new event, triggers a full rebuild for that symbol.
    3. If no new events exist, populates adj_* = raw for the new rows.
    """
    if new_df.empty:
        return 0

    target_engine = engine or default_engine
    if target_engine is None and session is None:
        logger.warning("Database engine offline. Skipping incremental price adjustment.")
        return 0

    work_new = new_df.copy()
    if not pd.api.types.is_datetime64_any_dtype(work_new["date"]):
        work_new["date"] = pd.to_datetime(work_new["date"])

    symbols = work_new["symbol"].unique().tolist()
    min_date = work_new["date"].dt.date.min()

    def _fetch_prior_and_total(active_conn: Any) -> tuple[pd.DataFrame, int]:
        total_syms = (
            active_conn.execute(select(func.count(func.distinct(DailyPrice.symbol)))).scalar() or 0
        )

        sub = (
            select(DailyPrice.symbol, func.max(DailyPrice.date).label("max_date"))
            .where(DailyPrice.symbol.in_(symbols), DailyPrice.date < min_date)
            .group_by(DailyPrice.symbol)
            .subquery()
        )
        prior_stmt = (
            select(
                DailyPrice.symbol,
                DailyPrice.date,
                DailyPrice.close,
                DailyPrice.prev_close,
            )
            .join(
                sub,
                (DailyPrice.symbol == sub.c.symbol) & (DailyPrice.date == sub.c.max_date),
            )
            .order_by(DailyPrice.symbol, DailyPrice.date)
        )
        prior_df = pd.read_sql(prior_stmt, active_conn)
        return prior_df, total_syms

    if session is not None:
        prior_df, total_db_symbols = _fetch_prior_and_total(session.connection())
    else:
        with target_engine.connect() as conn:
            prior_df, total_db_symbols = _fetch_prior_and_total(conn)

    if not prior_df.empty:
        combined_df = pd.concat([prior_df, work_new], ignore_index=True)
    else:
        combined_df = work_new

    new_events = detect_events(combined_df, total_db_symbols=total_db_symbols)

    if not new_events.empty:
        event_dates = pd.to_datetime(new_events["date"]).dt.date
        new_events = new_events[event_dates >= min_date].copy()

    material_events = (
        new_events[new_events["ratio_kind"] == "material"]
        if not new_events.empty
        else pd.DataFrame()
    )

    if not material_events.empty:
        affected_symbols = material_events["symbol"].unique().tolist()
        logger.info(
            "New material corporate actions detected for symbols: %s. Triggering full rebuild.",
            affected_symbols,
        )
        return rebuild_adjusted(symbols=affected_symbols, session=session, engine=engine)

    # No new corporate actions: set adj_* = raw
    update_sql = text("""
        UPDATE daily_prices
        SET adj_open = open,
            adj_high = high,
            adj_low = low,
            adj_close = close,
            adj_volume = volume
        WHERE symbol = :b_symbol AND date = :b_date
    """)

    sym_list = work_new["symbol"].tolist()
    date_list = [d.date() if isinstance(d, pd.Timestamp) else d for d in work_new["date"].tolist()]
    records = [{"b_symbol": s, "b_date": d} for s, d in zip(sym_list, date_list, strict=False)]

    def _run_adj(active_session: Session) -> None:
        for b_start in range(0, len(records), 1000):
            active_session.execute(update_sql, records[b_start : b_start + 1000])

    if session is not None:
        _run_adj(session)
    else:
        with get_db() as s:
            _run_adj(s)

    return len(records)
