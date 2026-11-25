"""Corporate Actions Adjustment Engine
===================================
Turns NSE's official corporate actions (splits, bonuses, consolidations) into backward
adjustment factors, and rebuilds the adjusted OHLCV columns from them.

One simple method (task CA2):

1. Read the official exact factors from the ``corporate_actions`` table
   (``get_price_adjustment_events``; fill the table with ``--download-corporate-actions``).
2. Keep only events that fall inside the stock's price history (``build_events``).
3. Apply the owner's corrections from ``config/adjustment_overrides.csv`` (final word).
4. ``cumulative_factors`` multiplies the older prices by those factors.

Prices are never used to GUESS a ratio. A big overnight price move that no applied event
explains is only written to a review report (``find_unexplained_jumps``), never applied.
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

from config import settings
from config.settings import (
    ADJUSTMENT_OVERRIDES_FILE,
    SPLIT_GUARD_FRACTION,
    SPLIT_GUARD_MIN_SYMBOLS,
    SPLIT_REVIEW_MIN_RATIO,
)
from src.common.db import use_session
from src.common.symbols import normalise_symbol
from src.db.models import AdjFactor, CorporateAction, DailyPrice
from src.db.session import engine as default_engine
from src.db.upsert import upsert_rows
from src.ingestion.corporate_actions_store import get_price_adjustment_events

logger = logging.getLogger("src.etl.adjust")

# Columns of the applied-events table (one row per symbol and ex-date).
EVENT_COLUMNS = ["symbol", "date", "factor", "ratio_kind", "source", "kinds"]
# Columns of the review report (big price moves that no applied event explains).
JUMP_COLUMNS = ["symbol", "date", "ratio", "implied_factor", "close_prev", "close", "reason"]
# Columns of the gap report (all large overnight price dislocations).
GAP_COLUMNS = [
    "symbol",
    "date",
    "move_type",
    "pct_change",
    "close_prev",
    "close",
    "ratio",
    "has_corp_action",
    "action_type",
]


class CorporateActionsMissingError(RuntimeError):
    """The ``corporate_actions`` table is empty, so there is nothing official to adjust with."""


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
                "symbol": normalise_symbol(row["symbol"]),
                "date": event_date,
                "action": action,
                "factor": factor,
                "reason": row["reason"].strip(),
            }
        )
    return pd.DataFrame(records, columns=columns)


def read_official_events(
    session: Session | None = None, symbols: Sequence[str] | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read the official exact events and the actions that need review from the database.

    Why it stops on an empty table: with no official data every price would silently be
    "not adjusted" and the old adjustment would be wiped. A clear error is safer.
    """

    def _read(active: Session) -> tuple[pd.DataFrame, pd.DataFrame]:
        stored = active.execute(select(func.count()).select_from(CorporateAction)).scalar() or 0
        if stored == 0:
            raise CorporateActionsMissingError(
                "The corporate_actions table is empty. "
                "Run: python run_pipeline.py --download-corporate-actions"
            )
        return get_price_adjustment_events(active, symbols=symbols)

    with use_session(session) as s:
        return _read(s)


def _keep_events_inside_price_history(official: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    """Keep official events whose ex-date lies inside the symbol's stored price history.

    Why: the official table also holds FUTURE ex-dates (the downloader looks ahead) and
    actions from before a stock was listed here. ``cumulative_factors`` would apply a future
    event to every stored price, which is wrong until the ex-date has actually happened.
    An event needs prices before it (to adjust) and a price on or after it (it happened).
    """
    if official.empty:
        return official
    stored_dates = pd.DataFrame({"symbol": df["symbol"], "day": pd.to_datetime(df["date"]).dt.date})
    span = stored_dates.groupby("symbol")["day"].agg(first="min", last="max")
    first = official["symbol"].map(span["first"])
    last = official["symbol"].map(span["last"])
    stored = first.notna()  # symbols with no stored prices at all are skipped
    inside = pd.Series(False, index=official.index)
    inside[stored] = (official.loc[stored, "date"] > first[stored]) & (
        official.loc[stored, "date"] <= last[stored]
    )
    skipped = len(official) - int(inside.sum())
    if skipped:
        logger.info(
            "Skipped %d official events outside the stored price history "
            "(future ex-dates, before listing, or symbol not stored).",
            skipped,
        )
    return official[inside]


def _apply_overrides(
    events: pd.DataFrame, overrides: pd.DataFrame, df: pd.DataFrame
) -> pd.DataFrame:
    """Apply the owner's ``apply`` and ``ignore`` rows on top of the official events.

    An override replaces the official event of the same symbol and date (``apply`` puts the
    owner's factor in its place, ``ignore`` just removes it). If the owner's factor differs
    from the official one it is logged, so a stale override never hides in the CSV.
    """
    if overrides.empty:
        return events

    # Only look at the symbols named in the overrides: the full table has millions of rows.
    subset = df[df["symbol"].isin(overrides["symbol"])]
    price_days = {
        (row.symbol, pd.Timestamp(row.date).date())
        for row in subset[["symbol", "date"]].itertuples()
    }
    official_factor = {(row.symbol, row.date): float(row.factor) for row in events.itertuples()}

    handled = set()
    new_rows = []
    for ov in overrides.to_dict("records"):
        key = (ov["symbol"], ov["date"])
        if key not in price_days:
            logger.warning("Override for %s on %s ignored: no price row for that date.", *key)
            continue
        handled.add(key)
        known = official_factor.get(key)
        if ov["action"] == "apply":
            if known is not None and not np.isclose(known, ov["factor"], rtol=1e-6):
                logger.warning(
                    "Override for %s on %s uses factor %s but the official factor is %s.",
                    *key,
                    ov["factor"],
                    known,
                )
            new_rows.append(
                {
                    "symbol": ov["symbol"],
                    "date": ov["date"],
                    "factor": ov["factor"],
                    "ratio_kind": "material",
                    "source": "override",
                    "kinds": "",
                }
            )

    if not events.empty:
        taken = pd.Series(
            list(zip(events["symbol"], events["date"], strict=True)), index=events.index
        )
        events = events[~taken.isin(handled)]
    if new_rows:
        events = pd.concat([events, pd.DataFrame(new_rows, columns=EVENT_COLUMNS)])
    return events.sort_values(["symbol", "date"]).reset_index(drop=True)


def build_events(
    official: pd.DataFrame, df: pd.DataFrame, overrides: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Build the events that adjust prices: official factors, then the owner's overrides.

    ``official`` comes from ``read_official_events``; ``df`` holds the stored prices
    (``symbol`` and ``date`` columns). Returns one row per symbol and ex-date with
    ``EVENT_COLUMNS``. ``factor`` is what older prices are multiplied by.
    """
    events = _keep_events_inside_price_history(official, df)
    events = events.assign(ratio_kind="material", source="official")
    events = events.reindex(columns=EVENT_COLUMNS)
    events["factor"] = events["factor"].astype(float)
    if overrides is not None:
        events = _apply_overrides(events, overrides, df)
    return events.sort_values(["symbol", "date"]).reset_index(drop=True)


def find_unexplained_jumps(
    df: pd.DataFrame,
    events: pd.DataFrame,
    official_review: pd.DataFrame | None = None,
    global_trading_days: Sequence[date] | None = None,
    non_trading_days: set[date] | None = None,
    total_db_symbols: int | None = None,
) -> pd.DataFrame:
    """List big overnight price moves that no applied event explains. Report only.

    A move is big when ``previous close / close`` is at least ``SPLIT_REVIEW_MIN_RATIO``
    in either direction. Why report it: a split or bonus missing from NSE's list (or a
    wrong ex-date) shows up here, and the owner can approve it in
    ``config/adjustment_overrides.csv``. Nothing in this report changes a price.

    ``reason`` is ``official action needs review: <type>`` when NSE lists a rights issue,
    demerger or similar on that date, otherwise ``no official action``. Rows after a data
    gap and market-wide jumps (bad data) are skipped.
    """
    empty = pd.DataFrame(columns=JUMP_COLUMNS)
    if df.empty or "close" not in df.columns:
        return empty

    work = df[["symbol", "date", "close"]].copy()
    work["date"] = pd.to_datetime(work["date"])
    work = work.sort_values(["symbol", "date"]).reset_index(drop=True)
    work["close_prev"] = work.groupby("symbol")["close"].shift(1)
    work["date_prev"] = work.groupby("symbol")["date"].shift(1)

    usable = (work["close_prev"] > 0) & (work["close"] > 0)
    ratio = work["close_prev"] / work["close"].where(work["close"] > 0)
    size = np.maximum(ratio, 1.0 / ratio)
    candidates = work[usable & (size >= SPLIT_REVIEW_MIN_RATIO)].copy()
    candidates["ratio"] = ratio[candidates.index]
    if candidates.empty:
        return empty

    consecutive = _has_preceding_trading_day(candidates, global_trading_days, non_trading_days)
    candidates = candidates[consecutive].copy()
    candidates["day"] = candidates["date"].dt.date

    # Cross-sectional guard: many symbols jumping on one date means bad data.
    n_total = total_db_symbols if total_db_symbols is not None else work["symbol"].nunique()
    if n_total >= SPLIT_GUARD_MIN_SYMBOLS and not candidates.empty:
        per_day = candidates.groupby("day")["symbol"].nunique()
        bad_days = per_day[per_day / n_total > SPLIT_GUARD_FRACTION].index.tolist()
        for bad_day in bad_days:
            logger.warning(
                "Cross-sectional guard: %d / %d symbols jumped on %s. "
                "Treating as a data problem and leaving this date out of the review report.",
                per_day[bad_day],
                n_total,
                bad_day,
            )
        candidates = candidates[~candidates["day"].isin(bad_days)]

    keys = list(zip(candidates["symbol"], candidates["day"], strict=True))
    explained = (
        set(zip(events["symbol"], events["date"], strict=True)) if not events.empty else set()
    )
    review_types: dict[tuple[str, date], list[str]] = {}
    if official_review is not None and not official_review.empty:
        for row in official_review.itertuples():
            review_types.setdefault((row.symbol, row.date), []).append(row.action_type)

    rows = []
    for key, (_, cand) in zip(keys, candidates.iterrows(), strict=True):
        if key in explained:
            continue
        types = review_types.get(key)
        reason = (
            f"official action needs review: {', '.join(sorted(set(types)))}"
            if types
            else "no official action"
        )
        rows.append(
            {
                "symbol": key[0],
                "date": key[1],
                "ratio": round(float(cand["ratio"]), 4),
                "implied_factor": round(1.0 / float(cand["ratio"]), 4),
                "close_prev": float(cand["close_prev"]),
                "close": float(cand["close"]),
                "reason": reason,
            }
        )
    return pd.DataFrame(rows, columns=JUMP_COLUMNS)


def find_big_gaps(
    df: pd.DataFrame,
    events: pd.DataFrame | None = None,
    official_review: pd.DataFrame | None = None,
    global_trading_days: Sequence[date] | None = None,
    non_trading_days: set[date] | None = None,
) -> pd.DataFrame:
    """Detect and classify big overnight gap-ups and gap-downs across stocks.

    - GAP_DOWN: overnight drop >= 20% (pct_change <= -20% or ratio >= 1.25)
    - GAP_UP: overnight surge >= 25% (pct_change >= +25% or 1/ratio >= 1.25)

    Cross-references whether an official corporate action or review item occurred on that date.
    Report only.
    """
    empty = pd.DataFrame(columns=GAP_COLUMNS)
    if df.empty or "close" not in df.columns:
        return empty

    work = df[["symbol", "date", "close"]].copy()
    work["date"] = pd.to_datetime(work["date"])
    work = work.sort_values(["symbol", "date"]).reset_index(drop=True)
    work["close_prev"] = work.groupby("symbol")["close"].shift(1)
    work["date_prev"] = work.groupby("symbol")["date"].shift(1)

    usable = (work["close_prev"] > 0) & (work["close"] > 0)
    ratio = work["close_prev"] / work["close"].where(work["close"] > 0)
    size = np.maximum(ratio, 1.0 / ratio)
    candidates = work[usable & (size >= SPLIT_REVIEW_MIN_RATIO)].copy()
    if candidates.empty:
        return empty

    consecutive = _has_preceding_trading_day(candidates, global_trading_days, non_trading_days)
    candidates = candidates[consecutive].copy()
    if candidates.empty:
        return empty

    candidates["day"] = candidates["date"].dt.date
    candidates["ratio"] = (candidates["close_prev"] / candidates["close"]).round(4)
    candidates["pct_change"] = (
        (candidates["close"] - candidates["close_prev"]) / candidates["close_prev"] * 100
    ).round(2)

    explained: dict[tuple[str, date], str] = {}
    if events is not None and not events.empty:
        for ev_row in events.itertuples():
            explained[(ev_row.symbol, ev_row.date)] = str(
                getattr(ev_row, "kinds", "corporate_action")
            )

    review_types: dict[tuple[str, date], list[str]] = {}
    if official_review is not None and not official_review.empty:
        for rev_row in official_review.itertuples():
            review_types.setdefault((rev_row.symbol, rev_row.date), []).append(rev_row.action_type)

    gap_rows = []
    for _, cand in candidates.iterrows():
        key = (cand["symbol"], cand["day"])
        is_down = cand["close"] < cand["close_prev"]
        move_type = "GAP_DOWN" if is_down else "GAP_UP"

        has_ca = key in explained or key in review_types
        act_parts = []
        if key in explained:
            act_parts.append(explained[key])
        if key in review_types:
            act_parts.extend(review_types[key])
        action_type = ", ".join(sorted(set(act_parts))) if act_parts else "NONE"

        gap_rows.append(
            {
                "symbol": cand["symbol"],
                "date": cand["day"],
                "move_type": move_type,
                "pct_change": float(cand["pct_change"]),
                "close_prev": float(cand["close_prev"]),
                "close": float(cand["close"]),
                "ratio": float(cand["ratio"]),
                "has_corp_action": has_ca,
                "action_type": action_type,
            }
        )

    res_df = pd.DataFrame(gap_rows, columns=GAP_COLUMNS)
    return res_df.sort_values(["date", "symbol"]).reset_index(drop=True)


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


def _write_report(df: pd.DataFrame, path: Path, only_symbols: list[str] | None) -> None:
    """Write one report CSV. A partial run replaces only its own symbols' rows.

    Why: an incremental run sees just a few symbols. Writing only those would erase every
    other symbol from the owner's report, so their old rows are kept and the new ones added.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if only_symbols and path.exists():
        old = pd.read_csv(path)
        if "symbol" in old.columns:
            kept = old[~old["symbol"].isin(only_symbols)]
            df = pd.concat([kept, df], ignore_index=True)
    df.to_csv(path, index=False)


def save_events_report(
    events_df: pd.DataFrame,
    file_path: Path | None = None,
    review_df: pd.DataFrame | None = None,
    review_path: Path | None = None,
    gaps_df: pd.DataFrame | None = None,
    gaps_path: Path | None = None,
    only_symbols: list[str] | None = None,
) -> None:
    """Save applied events, unexplained jumps, and big gap moves to CSV reports for the owner.

    ``only_symbols`` is set by a partial rebuild: the reports then keep the rows of every
    other symbol. ``None`` (a full rebuild) writes the reports from scratch.
    """
    target_path = file_path or (settings.REPORTS_DIR / "adj_events_review.csv")
    _write_report(events_df, target_path, only_symbols)
    logger.info("Saved %d applied events to %s", len(events_df), target_path)

    if review_df is not None:
        uncertain_path = review_path or (settings.REPORTS_DIR / "adj_events_uncertain.csv")
        _write_report(review_df, uncertain_path, only_symbols)
        logger.info(
            "Saved %d unexplained price jumps (NOT applied) to %s", len(review_df), uncertain_path
        )

    if gaps_df is not None:
        target_gaps = gaps_path or (settings.REPORTS_DIR / "adj_gaps.csv")
        _write_report(gaps_df, target_gaps, only_symbols)
        logger.info("Saved %d big gap moves to %s", len(gaps_df), target_gaps)


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

    # 2. Official events + owner overrides adjust prices; unexplained jumps are only reported.
    official, official_review = read_official_events(session, symbols=symbols)
    events_df = build_events(official, df, overrides=load_adjustment_overrides(overrides_path))
    review_df = find_unexplained_jumps(
        df,
        events_df,
        official_review,
        global_trading_days=global_trading_days,
        total_db_symbols=total_db_symbols,
    )
    gaps_df = find_big_gaps(
        df,
        events_df,
        official_review,
        global_trading_days=global_trading_days,
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

    with use_session(session) as s:
        _sync_db(s)

    save_events_report(
        events_df, review_df=review_df, gaps_df=gaps_df, only_symbols=symbols or None
    )

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

    with use_session(session) as s:
        return _apply_updates(s)


def adjust_new_rows(
    new_df: pd.DataFrame,
    session: Session | None = None,
    engine: Any = None,
) -> int:
    """Incrementally adjust newly loaded price rows.

    1. Looks for official corporate actions whose ex-date is on or after the first new day.
    2. If a symbol has one, triggers a full rebuild for that symbol.
    3. If no new events exist, populates adj_* = raw for the new rows.
    """
    if new_df.empty:
        return 0

    target_engine = engine or default_engine
    work_new = new_df.copy()
    if not pd.api.types.is_datetime64_any_dtype(work_new["date"]):
        work_new["date"] = pd.to_datetime(work_new["date"])

    symbols = work_new["symbol"].unique().tolist()
    min_date = work_new["date"].dt.date.min()

    def _fetch_prior_rows(active_conn: Any) -> pd.DataFrame:
        """Each symbol's last stored row before the new days (an event needs prices before it)."""
        sub = (
            select(DailyPrice.symbol, func.max(DailyPrice.date).label("max_date"))
            .where(DailyPrice.symbol.in_(symbols), DailyPrice.date < min_date)
            .group_by(DailyPrice.symbol)
            .subquery()
        )
        prior_stmt = (
            select(DailyPrice.symbol, DailyPrice.date)
            .join(
                sub,
                (DailyPrice.symbol == sub.c.symbol) & (DailyPrice.date == sub.c.max_date),
            )
            .order_by(DailyPrice.symbol, DailyPrice.date)
        )
        return pd.read_sql(prior_stmt, active_conn)

    if session is not None:
        prior_df = _fetch_prior_rows(session.connection())
    else:
        with target_engine.connect() as conn:
            prior_df = _fetch_prior_rows(conn)

    if not prior_df.empty:
        combined_df = pd.concat([prior_df, work_new], ignore_index=True)
    else:
        combined_df = work_new

    # Only events on or after the first new day matter here; older ones were handled by the
    # last full rebuild (run --rebuild-adjusted after every --download-corporate-actions).
    official, _official_review = read_official_events(session, symbols=symbols)
    new_events = build_events(official, combined_df, overrides=load_adjustment_overrides())
    if not new_events.empty:
        new_events = new_events[new_events["date"] >= min_date].copy()

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

    with use_session(session) as s:
        _run_adj(s)

    return len(records)


def adjust_rows_with_missing_adjusted(session: Session) -> int:
    """Fully re-adjust every symbol that has any row with adj_close NULL; return rows updated.

    Why: if a daily update failed after loading prices, those rows keep NULL adj_* and the
    next run sees "no new dates", so nothing would ever fix them. A full per-symbol rebuild
    (not adj = raw) is used because the old rows may lie before a split.
    """
    symbols = list(
        session.scalars(
            select(DailyPrice.symbol).where(DailyPrice.adj_close.is_(None)).distinct()
        ).all()
    )
    if not symbols:
        return 0
    logger.warning("%d symbols have rows with missing adjusted prices; re-adjusting.", len(symbols))
    return rebuild_adjusted(symbols=symbols, session=session)
