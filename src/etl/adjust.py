"""Corporate Actions Adjustment Engine
===================================
Detects splits, bonuses, and capital actions, calculates backwards cumulative
adjustment factors, and reconstructs historical unadjusted OHLCV price series into
smooth continuous curves.

Two detectors are combined (see ``detect_all_events``):
1. ``detect_events``: compares Bhavcopy PREV_CLOSE with the previous close. Only works
   when NSE has already adjusted PREV_CLOSE, which the real files do not do (issue #48).
2. ``detect_price_jump_events``: looks for a big overnight price change that matches a
   clean split or bonus ratio. This is the one that finds real splits and bonuses.
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

from config.settings import (
    ADJUSTMENT_OVERRIDES_FILE,
    SPLIT_CLEAN_RATIOS,
    SPLIT_GUARD_FRACTION,
    SPLIT_GUARD_MIN_SYMBOLS,
    SPLIT_MIN_AUTO_RATIO,
    SPLIT_REVIEW_MIN_RATIO,
    SPLIT_SNAP_TOLERANCE,
    SPLIT_VOLUME_LOOKBACK_DAYS,
)
from src.db.models import AdjFactor, DailyPrice
from src.db.session import engine as default_engine
from src.db.session import get_db
from src.db.upsert import upsert_rows

logger = logging.getLogger("src.etl.adjust")

REPORTS_DIR = Path("reports")


def _has_preceding_trading_day(
    work: pd.DataFrame,
    global_trading_days: Sequence[date] | None,
    non_trading_days: set[date] | None,
) -> np.ndarray:
    """Return True for rows whose previous stored row is the market's previous trading day.

    Why: a price change across a data gap may hide several days of normal moves, so such
    rows must not be used to detect a corporate action. ``work`` needs the columns
    ``date`` and ``date_prev`` (datetimes).
    """
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
    return consecutive


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
    consecutive = _has_preceding_trading_day(work, global_trading_days, non_trading_days)

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


JUMP_COLUMNS = [
    "symbol",
    "date",
    "ratio",
    "factor",
    "ratio_kind",
    "close_prev",
    "prev_close",
    "source",
    "volume_multiple",
    "status",
    "suggested_factor",
]


def _nearest_clean_ratio(jump: float) -> float:
    """Return the clean split ratio closest to ``jump`` (a number >= 1), however far away."""
    return float(min(SPLIT_CLEAN_RATIOS, key=lambda clean: abs(jump / clean - 1.0)))


def _snap_to_clean_ratio(jump: float) -> float | None:
    """Return the clean split ratio closest to ``jump`` (a number >= 1), or None.

    Why: on the ex-date the market also moves, so a 1:1 bonus shows up as 1.98 or 2.03,
    not exactly 2. Snapping to the clean ratio gives an exact adjustment factor.
    """
    best = min(SPLIT_CLEAN_RATIOS, key=lambda clean: abs(jump / clean - 1.0))
    if abs(jump / best - 1.0) <= SPLIT_SNAP_TOLERANCE:
        return float(best)
    return None


def _volume_multiple(volumes: np.ndarray, position: int, symbol_start: int) -> float:
    """Ex-date volume divided by the median volume of the days BEFORE it (no look-ahead)."""
    first = max(symbol_start, position - SPLIT_VOLUME_LOOKBACK_DAYS)
    earlier = volumes[first:position]
    earlier = earlier[~np.isnan(earlier)]
    if earlier.size == 0 or np.median(earlier) <= 0 or np.isnan(volumes[position]):
        return float("nan")
    return float(volumes[position] / np.median(earlier))


def detect_price_jump_events(
    df: pd.DataFrame,
    global_trading_days: Sequence[date] | None = None,
    non_trading_days: set[date] | None = None,
    total_db_symbols: int | None = None,
) -> pd.DataFrame:
    """Detect splits and bonuses from big overnight price changes (issue #48).

    For every row the jump ratio is ``previous close / close``. A drop to half gives 2.0
    (a 1:1 bonus or 2:1 split), a rise to ten times gives 0.1 (a 1-for-10 consolidation).

    Result column ``status``:
    - ``confirmed``: the price FELL by a large amount (see ``SPLIT_MIN_AUTO_RATIO``) AND
      the drop matches a clean ratio. ``factor`` is exact, ``ratio_kind`` is ``material``,
      so it is applied.
    - ``review``: unusual but unclear (a 20 percent drop, a 45 percent crash, a drop that
      is not a clean ratio, or any price RISE). Listed for the owner, never applied.
      ``ratio_kind`` is ``review`` and ``suggested_factor`` shows the nearest clean ratio.

    Why rises are never applied: genuine rallies of 45 percent or more do happen (PNB in
    Oct 2017, YES Bank in Mar 2020), while reverse splits are rare.

    Only the previous day, the ex-date and earlier volumes are used, so a decision never
    depends on later prices. Rows after a data gap and market-wide jumps are skipped.
    """
    if df.empty or "close" not in df.columns:
        return pd.DataFrame(columns=JUMP_COLUMNS)

    work_df = df.copy()
    if not pd.api.types.is_datetime64_any_dtype(work_df["date"]):
        work_df["date"] = pd.to_datetime(work_df["date"])
    work_df = work_df.sort_values(by=["symbol", "date"]).reset_index(drop=True)

    work_df["close_prev"] = work_df.groupby("symbol")["close"].shift(1)
    work_df["date_prev"] = work_df.groupby("symbol")["date"].shift(1)
    usable = (work_df["close_prev"] > 0) & (work_df["close"] > 0)
    jump_ratio = work_df["close_prev"] / work_df["close"].where(work_df["close"] > 0)
    # q is the size of the move whether the price fell (split) or rose (consolidation).
    size = np.maximum(jump_ratio, 1.0 / jump_ratio)
    candidates = work_df[usable & (size >= SPLIT_REVIEW_MIN_RATIO)].copy()
    if candidates.empty:
        return pd.DataFrame(columns=JUMP_COLUMNS)

    consecutive = _has_preceding_trading_day(candidates, global_trading_days, non_trading_days)
    candidates = candidates[consecutive].copy()
    if candidates.empty:
        return pd.DataFrame(columns=JUMP_COLUMNS)

    # Cross-sectional guard: many symbols jumping on one date means bad data.
    candidates["event_date"] = candidates["date"].dt.date
    n_total = total_db_symbols if total_db_symbols is not None else work_df["symbol"].nunique()
    if n_total >= SPLIT_GUARD_MIN_SYMBOLS:
        per_date = candidates.groupby("event_date")["symbol"].nunique()
        bad_dates = per_date[per_date / n_total > SPLIT_GUARD_FRACTION].index.tolist()
        for bad_date in bad_dates:
            logger.warning(
                "Cross-sectional guard: %d / %d symbols jumped on %s. "
                "Treating as a data problem and skipping price-jump events on this date.",
                per_date[bad_date],
                n_total,
                bad_date,
            )
        candidates = candidates[~candidates["event_date"].isin(bad_dates)].copy()
    if candidates.empty:
        return pd.DataFrame(columns=JUMP_COLUMNS)

    volumes = (
        work_df["volume"].to_numpy(dtype=float)
        if "volume" in work_df.columns
        else np.full(len(work_df), np.nan)
    )
    position_in_symbol = work_df.groupby("symbol").cumcount().to_numpy()

    records = []
    for row_index, row in candidates.iterrows():
        jump = float(row["close_prev"] / row["close"])
        price_fell = jump > 1.0
        move_size = max(jump, 1.0 / jump)
        clean = _snap_to_clean_ratio(move_size) if price_fell else None
        confirmed = clean is not None and move_size >= SPLIT_MIN_AUTO_RATIO
        # Prices BEFORE the ex-date are multiplied by the factor to line up with after it.
        used_size = clean if confirmed else move_size
        factor = 1.0 / used_size if price_fell else used_size
        suggested = np.nan
        if price_fell and move_size >= SPLIT_MIN_AUTO_RATIO:
            suggested = 1.0 / _nearest_clean_ratio(move_size)
        records.append(
            {
                "symbol": row["symbol"],
                "date": row["event_date"],
                "ratio": jump,
                "factor": factor,
                "ratio_kind": "material" if confirmed else "review",
                "close_prev": float(row["close_prev"]),
                "prev_close": row.get("prev_close", np.nan),
                "source": "price_jump",
                "volume_multiple": _volume_multiple(
                    volumes, row_index, row_index - int(position_in_symbol[row_index])
                ),
                "status": "confirmed" if confirmed else "review",
                "suggested_factor": suggested,
            }
        )
    return pd.DataFrame(records, columns=JUMP_COLUMNS).reset_index(drop=True)


OVERRIDE_ACTIONS = ("apply", "ignore")


def load_adjustment_overrides(path: Path | None = None) -> pd.DataFrame:
    """Read the owner-approved corrections from a CSV file (default: settings path).

    Columns: ``symbol, date, action, factor, reason``.
    - ``apply``: use exactly this ``factor`` for the event on that date (for example
      0.5 for a 1:1 bonus). Use it to approve an unclear jump from the review report.
    - ``ignore``: cancel an event the detector applied by mistake.

    A missing file means "no overrides". A bad row raises ``ValueError`` that names the
    file and the row, so a typo can never silently change adjusted prices.
    """
    target = path or ADJUSTMENT_OVERRIDES_FILE
    columns = ["symbol", "date", "action", "factor", "reason"]
    if not Path(target).exists():
        return pd.DataFrame(columns=columns)

    raw = pd.read_csv(target, dtype=str, keep_default_na=False)
    missing = [col for col in columns if col not in raw.columns]
    if missing:
        raise ValueError(f"adjustment_overrides {target}: missing columns {missing}")

    records = []
    for position, row in raw.iterrows():
        label = f"adjustment_overrides {target} row {position + 2}"
        action = row["action"].strip().lower()
        if action not in OVERRIDE_ACTIONS:
            raise ValueError(f"{label}: action must be one of {OVERRIDE_ACTIONS}, got '{action}'")
        try:
            event_date = date.fromisoformat(row["date"].strip())
        except ValueError as exc:
            raise ValueError(f"{label}: bad date '{row['date']}' (use YYYY-MM-DD)") from exc
        factor = np.nan
        if action == "apply":
            try:
                factor = float(row["factor"])
            except ValueError as exc:
                raise ValueError(f"{label}: 'apply' needs a numeric factor") from exc
            if not factor > 0:
                raise ValueError(f"{label}: factor must be above 0, got {factor}")
        records.append(
            {
                "symbol": row["symbol"].strip().upper(),
                "date": event_date,
                "action": action,
                "factor": factor,
                "reason": row["reason"].strip(),
            }
        )
    return pd.DataFrame(records, columns=columns)


def _apply_overrides(
    applied: pd.DataFrame,
    review: pd.DataFrame,
    overrides: pd.DataFrame,
    df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Apply the owner's ``apply`` and ``ignore`` rows to the detected events."""
    if overrides.empty:
        return applied, review

    # Only look at the symbols named in the overrides: the full table has millions of rows.
    subset = df[df["symbol"].isin(overrides["symbol"])]
    close_by_key = {
        (row.symbol, pd.Timestamp(row.date).date()): row.close
        for row in subset[["symbol", "date", "close"]].itertuples()
    }
    price_rows = set(close_by_key)

    def _key(frame: pd.DataFrame) -> pd.Series:
        return pd.Series(list(zip(frame["symbol"], frame["date"], strict=True)), index=frame.index)

    handled = set()
    new_rows = []
    for ov in overrides.to_dict("records"):
        key = (ov["symbol"], ov["date"])
        if key not in price_rows:
            logger.warning("Override for %s on %s ignored: no price row for that date.", *key)
            continue
        handled.add(key)
        if ov["action"] == "apply":
            new_rows.append(
                {
                    "symbol": ov["symbol"],
                    "date": ov["date"],
                    "ratio": 1.0 / ov["factor"],
                    "factor": ov["factor"],
                    "ratio_kind": "material",
                    "close_prev": np.nan,
                    "prev_close": close_by_key.get(key, np.nan),
                    "source": "override",
                    "volume_multiple": np.nan,
                    "status": "confirmed",
                    "suggested_factor": np.nan,
                }
            )

    if not applied.empty:
        applied = applied[~_key(applied).isin(handled)]
    if not review.empty:
        review = review[~_key(review).isin(handled)]
    if new_rows:
        applied = pd.concat([applied, pd.DataFrame(new_rows, columns=JUMP_COLUMNS)])
    applied = applied.sort_values(["symbol", "date"]).reset_index(drop=True)
    if not applied.empty:
        applied["factor"] = applied["factor"].astype(float)
    return applied, review.reset_index(drop=True)


def detect_all_events(
    df: pd.DataFrame,
    global_trading_days: Sequence[date] | None = None,
    non_trading_days: set[date] | None = None,
    total_db_symbols: int | None = None,
    overrides: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run both detectors. Returns ``(applied_events, review_events)``.

    ``applied_events`` feed the adjustment. If NSE's PREV_CLOSE already shows the same
    split on the same date, the exact PREV_CLOSE event wins and the price-jump event is
    dropped, so one action is never applied twice. ``overrides`` (from
    ``load_adjustment_overrides``) are the owner's final word and are applied last.
    """
    prev_close_events = detect_events(
        df,
        global_trading_days=global_trading_days,
        non_trading_days=non_trading_days,
        total_db_symbols=total_db_symbols,
    )
    prev_close_events = prev_close_events.assign(
        source="prev_close", volume_multiple=np.nan, status="confirmed", suggested_factor=np.nan
    )

    jump_events = detect_price_jump_events(
        df,
        global_trading_days=global_trading_days,
        non_trading_days=non_trading_days,
        total_db_symbols=total_db_symbols,
    )
    confirmed = jump_events[jump_events["status"] == "confirmed"]
    review = jump_events[jump_events["status"] == "review"].reset_index(drop=True)

    material_prev = prev_close_events[prev_close_events["ratio_kind"] == "material"]
    exact_factor = {
        (ev["symbol"], ev["date"]): float(ev["factor"]) for ev in material_prev.to_dict("records")
    }

    def _already_covered(ev: dict) -> bool:
        known = exact_factor.get((ev["symbol"], ev["date"]))
        return known is not None and abs(known / float(ev["factor"]) - 1.0) <= SPLIT_SNAP_TOLERANCE

    # np.array(..., dtype=bool) so an empty list is still read as a row mask, not as columns.
    keep = np.array([not _already_covered(ev) for ev in confirmed.to_dict("records")], dtype=bool)
    confirmed = confirmed[keep]

    parts = [part[JUMP_COLUMNS] for part in (prev_close_events, confirmed) if not part.empty]
    if parts:
        applied = pd.concat(parts, ignore_index=True)
        # Plain floats: cumulative_factors does math on this column and needs a numeric dtype.
        applied["factor"] = applied["factor"].astype(float)
        applied = applied.sort_values(["symbol", "date"]).reset_index(drop=True)
    else:
        applied = pd.DataFrame(columns=JUMP_COLUMNS)

    if overrides is not None:
        applied, review = _apply_overrides(applied, review, overrides, df)
    return applied, review


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


def save_events_report(
    events_df: pd.DataFrame,
    file_path: Path | None = None,
    review_df: pd.DataFrame | None = None,
    review_path: Path | None = None,
) -> None:
    """Save applied events (and, if given, unclear events) to CSV reports for the owner."""
    target_path = file_path or (REPORTS_DIR / "adj_events_review.csv")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    events_df.to_csv(target_path, index=False)
    logger.info("Saved %d detected events to %s", len(events_df), target_path)

    if review_df is not None:
        uncertain_path = review_path or (REPORTS_DIR / "adj_events_uncertain.csv")
        review_df.to_csv(uncertain_path, index=False)
        logger.info(
            "Saved %d unclear price jumps (NOT applied) to %s", len(review_df), uncertain_path
        )


def rebuild_adjusted(
    symbols: list[str] | None = None,
    session: Session | None = None,
    engine: Any = None,
    overrides_path: Path | None = None,
) -> int:
    """Recompute corporate action adjustment factors and update daily_prices.

    Idempotent: updates adj_open, adj_high, adj_low, adj_close, adj_volume.
    Updates only rows whose adjusted values changed or are NULL.
    The owner's corrections in config/adjustment_overrides.csv are applied on top of the
    detected events (``overrides_path`` replaces that file, mainly for tests).
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
    events_df, review_df = detect_all_events(
        df,
        global_trading_days=global_trading_days,
        total_db_symbols=total_db_symbols,
        overrides=load_adjustment_overrides(overrides_path),
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

    save_events_report(events_df, review_df=review_df)

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

    new_events, _review = detect_all_events(combined_df, total_db_symbols=total_db_symbols)

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
