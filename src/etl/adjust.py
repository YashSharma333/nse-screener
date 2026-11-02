"""
Corporate Actions Adjustment Engine
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

import numpy as np
import pandas as pd
from sqlalchemy import select, text
from sqlalchemy.dialects.mysql import insert as mysql_insert

from src.db.models import AdjFactor, DailyPrice
from src.db.session import engine, get_db

logger = logging.getLogger("src.etl.adjust")

REPORTS_DIR = Path("reports")


def detect_events(df: pd.DataFrame) -> pd.DataFrame:
    """Detect corporate actions (splits, bonuses) by comparing PREV_CLOSE to prior CLOSE.

    Parameters
    ----------
    df: pd.DataFrame
        DataFrame containing at least:
        ``['symbol', 'date', 'close', 'prev_close']`` sorted by symbol and date.

    Returns
    -------
    pd.DataFrame
        Detected events with columns:
        ``['symbol', 'date', 'ratio', 'factor', 'ratio_kind', 'close_prev', 'prev_close']``
    """
    if df.empty or "prev_close" not in df.columns or "close" not in df.columns:
        return pd.DataFrame(
            columns=["symbol", "date", "ratio", "factor", "ratio_kind", "close_prev", "prev_close"]
        )

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
        return pd.DataFrame(
            columns=["symbol", "date", "ratio", "factor", "ratio_kind", "close_prev", "prev_close"]
        )

    # Check for trading gap: date_diff > 7 calendar days
    date_diff_days = (work["date"] - work["date_prev"]).dt.days
    gap_mask = date_diff_days > 7
    if gap_mask.any():
        for _, gap_row in work[gap_mask].iterrows():
            logger.debug(
                "Data gap of %d days detected for %s between %s and %s — skipping ratio check",
                (gap_row["date"] - gap_row["date_prev"]).days,
                gap_row["symbol"],
                gap_row["date_prev"].date(),
                gap_row["date"].date(),
            )
        work = work[~gap_mask].copy()

    # ratio_t = prev_close_t / close_(t-1)
    close_prev_nonzero = work["close_prev"].replace(0, np.nan)
    work["ratio"] = (work["prev_close"] / close_prev_nonzero).astype(float)
    work = work[work["ratio"].notna()].copy()

    # Events where abs(ratio - 1) > 0.001
    event_mask = (work["ratio"] - 1.0).abs() > 0.001
    events = work[event_mask].copy()

    if events.empty:
        return pd.DataFrame(
            columns=["symbol", "date", "ratio", "factor", "ratio_kind", "close_prev", "prev_close"]
        )

    # Material if abs(ratio - 1) >= 0.02, else minor
    events["ratio_kind"] = np.where(
        (events["ratio"] - 1.0).abs() >= 0.02,
        "material",
        "minor",
    )
    events["factor"] = events["ratio"]

    # Convert timestamp back to datetime.date
    events["date"] = events["date"].dt.date

    keep_cols = ["symbol", "date", "ratio", "factor", "ratio_kind", "close_prev", "prev_close"]
    return events[keep_cols].reset_index(drop=True)


def cumulative_factors(dates: Sequence[date], events_df: pd.DataFrame) -> np.ndarray:
    """Compute cumulative backward adjustment factor for each date in a symbol series.

    For date t:
        cum_t = product of factor_e for all material events e with date_e > t

    If there are no subsequent material events, cum_t = 1.0.
    """
    sorted_dates = pd.to_datetime(pd.Series(dates)).reset_index(drop=True)
    if events_df.empty:
        return np.ones(len(sorted_dates), dtype=np.float64)

    # Only material events are applied
    mat_events = events_df[events_df["ratio_kind"] == "material"].copy()
    if mat_events.empty:
        return np.ones(len(sorted_dates), dtype=np.float64)

    mat_events["date"] = pd.to_datetime(mat_events["date"])
    event_dates = mat_events["date"].to_numpy()
    event_factors = mat_events["factor"].to_numpy()

    # Vectorized cumulative product across event thresholds
    date_matrix = sorted_dates.to_numpy()[:, None]  # Shape (N, 1)
    event_matrix = event_dates[None, :]  # Shape (1, E)

    # Condition: event date > trading date t
    applies = event_matrix > date_matrix  # Shape (N, E)

    # Broadcast factor where applied, 1.0 elsewhere, then take product along axis 1
    factors_grid = np.where(applies, event_factors[None, :], 1.0)
    cum_factors = np.prod(factors_grid, axis=1)

    return cum_factors


def save_events_report(events_df: pd.DataFrame, file_path: Path | None = None) -> None:
    """Save detected events to a CSV report for review."""
    target_path = file_path or (REPORTS_DIR / "adj_events_review.csv")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    events_df.to_csv(target_path, index=False)
    logger.info("Saved %d detected events to %s", len(events_df), target_path)


def rebuild_adjusted(symbols: list[str] | None = None) -> int:
    """Recompute corporate action adjustment factors and update daily_prices.

    Idempotent: updates adj_open, adj_high, adj_low, adj_close, adj_volume.
    """
    if engine is None:
        logger.warning("Database engine offline. Skipping adjusted price rebuild.")
        return 0

    logger.info(
        "Rebuilding adjusted prices (symbols=%s)...", "all" if symbols is None else len(symbols)
    )

    with engine.connect() as conn:
        # Load raw daily prices
        if symbols:
            stmt = (
                select(
                    DailyPrice.symbol,
                    DailyPrice.date,
                    DailyPrice.open,
                    DailyPrice.high,
                    DailyPrice.low,
                    DailyPrice.close,
                    DailyPrice.prev_close,
                    DailyPrice.volume,
                )
                .where(DailyPrice.symbol.in_(symbols))
                .order_by(DailyPrice.symbol, DailyPrice.date)
            )
        else:
            stmt = select(
                DailyPrice.symbol,
                DailyPrice.date,
                DailyPrice.open,
                DailyPrice.high,
                DailyPrice.low,
                DailyPrice.close,
                DailyPrice.prev_close,
                DailyPrice.volume,
            ).order_by(DailyPrice.symbol, DailyPrice.date)

        df = pd.read_sql(stmt, conn)

    if df.empty:
        logger.info("No prices to adjust.")
        return 0

    # 1. Detect events across the dataset
    events_df = detect_events(df)
    if not events_df.empty:
        # Persist detected events to adj_factors table
        with get_db() as session:
            for _, ev in events_df.iterrows():
                ins_stmt = mysql_insert(AdjFactor).values(
                    symbol=ev["symbol"],
                    date=ev["date"],
                    factor=ev["factor"],
                    ratio_kind=ev["ratio_kind"],
                )
                session.execute(
                    ins_stmt.on_duplicate_key_update(
                        factor=ins_stmt.inserted.factor,
                        ratio_kind=ins_stmt.inserted.ratio_kind,
                    )
                )

        save_events_report(events_df)
    else:
        save_events_report(
            pd.DataFrame(
                columns=[
                    "symbol",
                    "date",
                    "ratio",
                    "factor",
                    "ratio_kind",
                    "close_prev",
                    "prev_close",
                ]
            )
        )

    # 2. Compute adjusted prices per symbol
    updated_rows = 0
    grouped = df.groupby("symbol")

    for sym, sym_df in grouped:
        sym_events = (
            events_df[events_df["symbol"] == sym] if not events_df.empty else pd.DataFrame()
        )
        dates_list = sym_df["date"].tolist()
        cum_facts = cumulative_factors(dates_list, sym_events)

        sym_df = sym_df.copy()
        # Adjusted price = raw price * cum_t
        sym_df["adj_open"] = (sym_df["open"].astype(float) * cum_facts).round(4)
        sym_df["adj_high"] = (sym_df["high"].astype(float) * cum_facts).round(4)
        sym_df["adj_low"] = (sym_df["low"].astype(float) * cum_facts).round(4)
        sym_df["adj_close"] = (sym_df["close"].astype(float) * cum_facts).round(4)

        # Adjusted volume = raw volume / cum_t (rounded to nearest integer)
        cum_facts_nonzero = np.where(cum_facts != 0, cum_facts, 1.0)
        sym_df["adj_volume"] = np.round(sym_df["volume"].astype(float) / cum_facts_nonzero).astype(
            "Int64"
        )

        # Update MySQL in batches
        update_records = []
        for _, row in sym_df.iterrows():
            update_records.append(
                {
                    "b_symbol": sym,
                    "b_date": row["date"],
                    "b_adj_open": None if pd.isna(row["adj_open"]) else float(row["adj_open"]),
                    "b_adj_high": None if pd.isna(row["adj_high"]) else float(row["adj_high"]),
                    "b_adj_low": None if pd.isna(row["adj_low"]) else float(row["adj_low"]),
                    "b_adj_close": None if pd.isna(row["adj_close"]) else float(row["adj_close"]),
                    "b_adj_volume": None if pd.isna(row["adj_volume"]) else int(row["adj_volume"]),
                }
            )

        update_sql = text("""
            UPDATE daily_prices
            SET adj_open = :b_adj_open,
                adj_high = :b_adj_high,
                adj_low = :b_adj_low,
                adj_close = :b_adj_close,
                adj_volume = :b_adj_volume
            WHERE symbol = :b_symbol AND date = :b_date
        """)

        with get_db() as session:
            for b_start in range(0, len(update_records), 500):
                session.execute(update_sql, update_records[b_start : b_start + 500])
        updated_rows += len(update_records)

    logger.info(
        "Adjusted price rebuild complete. %d records updated across %d symbols.",
        updated_rows,
        len(grouped),
    )
    return updated_rows
