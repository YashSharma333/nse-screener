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
    JUMP_REPORT_MAX_BAD_DAY_SHARE,
    JUMP_REPORT_MIN_RATIO,
    JUMP_REPORT_MIN_SYMBOLS,
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
    with use_session(session) as s:
        stored = s.execute(select(func.count()).select_from(CorporateAction)).scalar() or 0
        if stored == 0:
            raise CorporateActionsMissingError(
                "The corporate_actions table is empty. "
                "Run: python run_pipeline.py --download-corporate-actions"
            )
        return get_price_adjustment_events(s, symbols=symbols)


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


def find_jumps(
    df: pd.DataFrame,
    global_trading_days: Sequence[date] | None = None,
    non_trading_days: set[date] | None = None,
) -> pd.DataFrame:
    """Every overnight move of ``JUMP_REPORT_MIN_RATIO`` or more, in either direction.

    ``df`` needs ``symbol``, ``date`` and ``close``. A move is ``previous close / close``,
    compared with the previous stored row of the same symbol. Rows after a data gap are
    left out (the move may hide several normal days). Returns ``symbol, day, close_prev,
    close, ratio``; ``ratio`` is ``close_prev / close`` (above 1 = price fell).

    Why one function: the review report and the gap report use the same candidates and
    only differ in what they add (explained-by-event check or move type).
    """
    columns = ["symbol", "day", "close_prev", "close", "ratio"]
    if df.empty or "close" not in df.columns:
        return pd.DataFrame(columns=columns)

    work = df[["symbol", "date", "close"]].copy()
    work["date"] = pd.to_datetime(work["date"])
    work = work.sort_values(["symbol", "date"]).reset_index(drop=True)
    work["close_prev"] = work.groupby("symbol")["close"].shift(1)  # shift inside each symbol
    work["date_prev"] = work.groupby("symbol")["date"].shift(1)

    usable = (work["close_prev"] > 0) & (work["close"] > 0)
    ratio = work["close_prev"] / work["close"].where(work["close"] > 0)
    size = np.maximum(ratio, 1.0 / ratio)
    candidates = work[usable & (size >= JUMP_REPORT_MIN_RATIO)].copy()
    candidates["ratio"] = ratio[candidates.index]
    if candidates.empty:
        return pd.DataFrame(columns=columns)

    consecutive = _has_preceding_trading_day(candidates, global_trading_days, non_trading_days)
    candidates = candidates[consecutive].copy()
    candidates["day"] = candidates["date"].dt.date
    return candidates[columns].reset_index(drop=True)


def _review_types(official_review: pd.DataFrame | None) -> dict[tuple[str, date], list[str]]:
    """Map (symbol, date) to the NSE action types that need a person's review."""
    types: dict[tuple[str, date], list[str]] = {}
    if official_review is not None and not official_review.empty:
        for row in official_review.itertuples():
            types.setdefault((row.symbol, row.date), []).append(row.action_type)
    return types


def _drop_market_wide_jump_days(jumps: pd.DataFrame, total_symbols: int) -> pd.DataFrame:
    """Cross-sectional guard: many symbols jumping on one date means bad data, not actions."""
    if total_symbols < JUMP_REPORT_MIN_SYMBOLS or jumps.empty:
        return jumps
    per_day = jumps.groupby("day")["symbol"].nunique()
    bad_days = per_day[per_day / total_symbols > JUMP_REPORT_MAX_BAD_DAY_SHARE].index.tolist()
    for bad_day in bad_days:
        logger.warning(
            "Cross-sectional guard: %d / %d symbols jumped on %s. "
            "Treating as a data problem and leaving this date out of the review report.",
            per_day[bad_day],
            total_symbols,
            bad_day,
        )
    return jumps[~jumps["day"].isin(bad_days)]


def find_unexplained_jumps(
    df: pd.DataFrame,
    events: pd.DataFrame,
    official_review: pd.DataFrame | None = None,
    global_trading_days: Sequence[date] | None = None,
    non_trading_days: set[date] | None = None,
    total_db_symbols: int | None = None,
) -> pd.DataFrame:
    """List big overnight price moves that no applied event explains. Report only.

    Why report it: a split or bonus missing from NSE's list (or a wrong ex-date) shows up
    here, and the owner can approve it in ``config/adjustment_overrides.csv``. Nothing in
    this report changes a price.

    ``reason`` is ``official action needs review: <type>`` when NSE lists a rights issue,
    demerger or similar on that date, otherwise ``no official action``. Rows after a data
    gap and market-wide jumps (bad data) are skipped.
    """
    jumps = find_jumps(df, global_trading_days, non_trading_days)
    if jumps.empty:
        return pd.DataFrame(columns=JUMP_COLUMNS)

    total_symbols = total_db_symbols if total_db_symbols is not None else df["symbol"].nunique()
    jumps = _drop_market_wide_jump_days(jumps, total_symbols)

    explained = (
        set(zip(events["symbol"], events["date"], strict=True)) if not events.empty else set()
    )
    review_types = _review_types(official_review)

    rows = []
    for jump in jumps.itertuples():
        key = (jump.symbol, jump.day)
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
                "symbol": jump.symbol,
                "date": jump.day,
                "ratio": round(float(jump.ratio), 4),
                "implied_factor": round(1.0 / float(jump.ratio), 4),
                "close_prev": float(jump.close_prev),
                "close": float(jump.close),
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
    """Classify every big overnight gap-up and gap-down (a move of 1.25x or more).

    GAP_DOWN: the price fell; GAP_UP: it rose. Each row says whether an official corporate
    action or review item sits on that date. Report only.
    """
    jumps = find_jumps(df, global_trading_days, non_trading_days)
    if jumps.empty:
        return pd.DataFrame(columns=GAP_COLUMNS)

    explained: dict[tuple[str, date], str] = {}
    if events is not None and not events.empty:
        for event in events.itertuples():
            explained[(event.symbol, event.date)] = str(getattr(event, "kinds", "corporate_action"))
    review_types = _review_types(official_review)

    jumps = jumps.assign(
        pct_change=((jumps["close"] - jumps["close_prev"]) / jumps["close_prev"] * 100).round(2),
        gap_ratio=(jumps["close_prev"] / jumps["close"]).round(4),
    )
    rows = []
    for jump in jumps.itertuples():
        key = (jump.symbol, jump.day)
        action_parts = []
        if key in explained:
            action_parts.append(explained[key])
        action_parts.extend(review_types.get(key, []))
        rows.append(
            {
                "symbol": jump.symbol,
                "date": jump.day,
                "move_type": "GAP_DOWN" if jump.close < jump.close_prev else "GAP_UP",
                "pct_change": float(jump.pct_change),
                "close_prev": float(jump.close_prev),
                "close": float(jump.close),
                "ratio": float(jump.gap_ratio),
                "has_corp_action": key in explained or key in review_types,
                "action_type": ", ".join(sorted(set(action_parts))) if action_parts else "NONE",
            }
        )
    gaps = pd.DataFrame(rows, columns=GAP_COLUMNS)
    return gaps.sort_values(["date", "symbol"]).reset_index(drop=True)


def cumulative_factors(dates: Sequence[date], events_df: pd.DataFrame) -> np.ndarray:
    """The backward adjustment factor for each date of one symbol's price history.

    A price is multiplied by the factor of every event that happens AFTER its date
    (a 2:1 split on June 1 halves every price before June 1). Events are applied oldest
    first, so the multiplication order is always the same.
    """
    price_dates = pd.to_datetime(pd.Series(dates)).reset_index(drop=True).to_numpy()
    factors = np.ones(len(price_dates), dtype=np.float64)
    if events_df.empty:
        return factors

    material = events_df[events_df["ratio_kind"] == "material"].copy()
    material["date"] = pd.to_datetime(material["date"])
    for event in material.sort_values("date").itertuples():
        factors[price_dates < event.date.to_datetime64()] *= float(event.factor)
    return factors


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


UPDATE_ADJUSTED_SQL = text("""
    UPDATE daily_prices
    SET adj_open = :b_adj_open,
        adj_high = :b_adj_high,
        adj_low = :b_adj_low,
        adj_close = :b_adj_close,
        adj_volume = :b_adj_volume
    WHERE symbol = :b_symbol AND date = :b_date
""")
UPDATE_BATCH_SIZE = 1000
COPY_RAW_TO_ADJUSTED_SQL = text("""
    UPDATE daily_prices
    SET adj_open = open,
        adj_high = high,
        adj_low = low,
        adj_close = close,
        adj_volume = volume
    WHERE symbol = :b_symbol AND date = :b_date
""")


def read_prices(
    connection: Any, symbols: list[str] | None
) -> tuple[pd.DataFrame, int, list[date] | None]:
    """Read the raw prices to adjust, how many symbols the database has, and the market's days.

    Returns ``(prices, total_symbols, trading_days)``. ``total_symbols`` and ``trading_days``
    always cover the whole database (the jump reports need the full picture even when only a
    few symbols are rebuilt).
    """
    total_symbols = connection.execute(
        select(func.count(func.distinct(DailyPrice.symbol)))
    ).scalar()
    stmt = select(
        DailyPrice.symbol,
        DailyPrice.date,
        DailyPrice.open,
        DailyPrice.high,
        DailyPrice.low,
        DailyPrice.close,
        DailyPrice.prev_close,
        DailyPrice.volume,
        DailyPrice.adj_close,
    ).order_by(DailyPrice.symbol, DailyPrice.date)
    if symbols:
        stmt = stmt.where(DailyPrice.symbol.in_(symbols))
    prices = pd.read_sql(stmt, connection)

    days = pd.read_sql(select(DailyPrice.date).distinct().order_by(DailyPrice.date), connection)
    trading_days = pd.to_datetime(days["date"]).dt.date.tolist() if not days.empty else None
    return prices, total_symbols or 0, trading_days


def load_events(
    session: Session | None,
    prices: pd.DataFrame,
    symbols: list[str] | None,
    overrides_path: Path | None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The events that adjust prices (official + owner overrides) and the actions to review."""
    official, official_review = read_official_events(session, symbols=symbols)
    events = build_events(official, prices, overrides=load_adjustment_overrides(overrides_path))
    return events, official_review


def write_factors(session: Session | None, events: pd.DataFrame, symbols: list[str]) -> None:
    """Replace the stored ``adj_factors`` rows of ``symbols`` with the applied ``events``."""
    with use_session(session) as s:
        if symbols:
            s.execute(delete(AdjFactor).where(AdjFactor.symbol.in_(symbols)))
        if not events.empty:
            records = [
                {
                    "symbol": ev["symbol"],
                    "date": ev["date"],
                    "factor": float(ev["factor"]),
                    "ratio_kind": ev["ratio_kind"],
                }
                for ev in events.to_dict("records")
            ]
            upsert_rows(s, AdjFactor, records)


def _none_if_nan(value: float) -> float | None:
    return None if np.isnan(value) else float(value)


def compute_adjusted_rows(prices: pd.DataFrame, events: pd.DataFrame) -> list[dict[str, Any]]:
    """The ``adj_*`` values of every price row whose adjusted close changed (or is still NULL).

    Prices are multiplied by the backward factor; volume is divided by it (a split gives
    more shares). Rows already holding the right value (within 0.0001) are left out so a
    re-run writes almost nothing.
    """
    records: list[dict[str, Any]] = []
    for symbol, rows in prices.groupby("symbol"):
        symbol_events = events[events["symbol"] == symbol] if not events.empty else events
        factor = cumulative_factors(rows["date"].tolist(), symbol_events)

        adj_open = np.round(rows["open"].to_numpy(dtype=float) * factor, 4)
        adj_high = np.round(rows["high"].to_numpy(dtype=float) * factor, 4)
        adj_low = np.round(rows["low"].to_numpy(dtype=float) * factor, 4)
        adj_close = np.round(rows["close"].to_numpy(dtype=float) * factor, 4)
        adj_volume = np.round(
            rows["volume"].to_numpy(dtype=float) / np.where(factor != 0, factor, 1.0)
        ).astype("int64")

        stored_close = rows["adj_close"].astype(float).to_numpy()
        unchanged = (
            ~np.isnan(stored_close)
            & ~np.isnan(adj_close)
            & (np.abs(stored_close - adj_close) < 1e-4)
        )
        days = pd.to_datetime(rows["date"]).dt.date.to_numpy()
        for i in np.flatnonzero(~unchanged):
            records.append(
                {
                    "b_symbol": symbol,
                    "b_date": days[i],
                    "b_adj_open": _none_if_nan(adj_open[i]),
                    "b_adj_high": _none_if_nan(adj_high[i]),
                    "b_adj_low": _none_if_nan(adj_low[i]),
                    "b_adj_close": _none_if_nan(adj_close[i]),
                    "b_adj_volume": int(adj_volume[i]),
                }
            )
    return records


def update_prices(session: Session | None, records: list[dict[str, Any]]) -> None:
    """Write the computed ``adj_*`` values to daily_prices, 1000 rows per statement."""
    with use_session(session) as s:
        run_in_batches(s, UPDATE_ADJUSTED_SQL, records)
    logger.info("Adjusted prices update complete: modified %d rows.", len(records))


def run_in_batches(session: Session, statement: Any, records: list[dict[str, Any]]) -> None:
    """Execute ``statement`` for ``records`` in batches of ``UPDATE_BATCH_SIZE``."""
    for start in range(0, len(records), UPDATE_BATCH_SIZE):
        session.execute(statement, records[start : start + UPDATE_BATCH_SIZE])


def rebuild_adjusted(
    symbols: list[str] | None = None,
    session: Session | None = None,
    engine: Any = None,
    overrides_path: Path | None = None,
) -> int:
    """Recompute corporate action adjustment factors and update daily_prices.

    Idempotent: updates adj_open, adj_high, adj_low, adj_close, adj_volume, only on rows
    whose adjusted values changed or are NULL. The owner's corrections in
    config/adjustment_overrides.csv are applied on top of the official events
    (``overrides_path`` replaces that file, mainly for tests). Returns the rows updated.
    """
    logger.info(
        "Rebuilding adjusted prices (symbols=%s)...", "all" if symbols is None else len(symbols)
    )
    if session is not None:
        prices, total_symbols, trading_days = read_prices(session.connection(), symbols)
    else:
        with (engine or default_engine).connect() as connection:
            prices, total_symbols, trading_days = read_prices(connection, symbols)
    if prices.empty:
        logger.info("No prices to adjust.")
        return 0

    # Official events + owner overrides adjust prices; unexplained jumps are only reported.
    events, official_review = load_events(session, prices, symbols, overrides_path)
    review = find_unexplained_jumps(
        prices,
        events,
        official_review,
        global_trading_days=trading_days,
        total_db_symbols=total_symbols,
    )
    gaps = find_big_gaps(prices, events, official_review, global_trading_days=trading_days)

    write_factors(session, events, symbols or prices["symbol"].unique().tolist())
    save_events_report(events, review_df=review, gaps_df=gaps, only_symbols=symbols or None)

    records = compute_adjusted_rows(prices, events)
    update_prices(session, records)
    return len(records)


def _last_rows_before(connection: Any, symbols: list[str], day: date) -> pd.DataFrame:
    """Each symbol's last stored row before ``day`` (an event needs prices before it)."""
    latest = (
        select(DailyPrice.symbol, func.max(DailyPrice.date).label("max_date"))
        .where(DailyPrice.symbol.in_(symbols), DailyPrice.date < day)
        .group_by(DailyPrice.symbol)
        .subquery()
    )
    stmt = (
        select(DailyPrice.symbol, DailyPrice.date)
        .join(
            latest, (DailyPrice.symbol == latest.c.symbol) & (DailyPrice.date == latest.c.max_date)
        )
        .order_by(DailyPrice.symbol, DailyPrice.date)
    )
    return pd.read_sql(stmt, connection)


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

    work_new = new_df.copy()
    if not pd.api.types.is_datetime64_any_dtype(work_new["date"]):
        work_new["date"] = pd.to_datetime(work_new["date"])

    symbols = work_new["symbol"].unique().tolist()
    min_date = work_new["date"].dt.date.min()

    if session is not None:
        prior_df = _last_rows_before(session.connection(), symbols, min_date)
    else:
        with (engine or default_engine).connect() as connection:
            prior_df = _last_rows_before(connection, symbols, min_date)

    combined_df = (
        pd.concat([prior_df, work_new], ignore_index=True) if not prior_df.empty else work_new
    )

    # Only events on or after the first new day matter here; older ones were handled by the
    # last full rebuild (run --rebuild-adjusted after every --download-corporate-actions).
    official, _official_review = read_official_events(session, symbols=symbols)
    new_events = build_events(official, combined_df, overrides=load_adjustment_overrides())
    material_events = new_events[
        (new_events["date"] >= min_date) & (new_events["ratio_kind"] == "material")
    ]

    if not material_events.empty:
        affected_symbols = material_events["symbol"].unique().tolist()
        logger.info(
            "New material corporate actions detected for symbols: %s. Triggering full rebuild.",
            affected_symbols,
        )
        return rebuild_adjusted(symbols=affected_symbols, session=session, engine=engine)

    # No new corporate actions: set adj_* = raw
    days = work_new["date"].dt.date.tolist()
    records = [
        {"b_symbol": sym, "b_date": day} for sym, day in zip(work_new["symbol"], days, strict=True)
    ]
    with use_session(session) as s:
        run_in_batches(s, COPY_RAW_TO_ADJUSTED_SQL, records)
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
