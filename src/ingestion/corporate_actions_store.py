"""
Corporate-actions storage and the adjustment-ready reader
=========================================================
Glue between the downloader (``corporate_actions.py``) and the database:

* :func:`run_corporate_actions_download` fetches the three NSE reports, keeps the raw
  responses on disk, parses them, runs the safety checks and only then writes to MySQL.
* :func:`get_price_adjustment_events` is the reader the price-adjustment code will use
  later: one exact factor per ``(symbol, date)`` plus a list of events that need review.

Files written to ``data/corporate_actions/`` (git-ignored):

* ``corporate_actions_raw.json``: the untouched API rows (kept so a format change never
  loses data and the text can be re-parsed later);
* ``symbol_changes_raw.csv``: the untouched ``symbolchange.csv``;
* ``equity_listing.parquet``: parsed ``EQUITY_L.csv`` (symbol, ISIN, face value, listing date).
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Callable, Sequence
from contextlib import nullcontext
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from config.settings import (
    CORP_ACTIONS_DIR,
    CORP_ACTIONS_FORWARD_DAYS,
    CORP_ACTIONS_HISTORY_YEARS,
    CORP_ACTIONS_MAX_UNPARSED_SHARE,
)
from src.db.models import CorporateAction, DailyPrice, Security, SymbolChange
from src.db.upsert import upsert_rows
from src.ingestion.corporate_actions import (
    NSEFormatChangedError,
    NSEReportsDownloader,
    build_action_rows,
    default_window,
)
from src.ingestion.corporate_actions_parser import (
    PRICE_ACTION_TYPES,
    STATUS_EXACT,
    STATUS_REVIEW,
    unparsed_price_share,
)
from src.ingestion.renames import load_chains_from_db

logger = logging.getLogger(__name__)

# Columns that identify a row (the table's unique key). Everything else is refreshed.
_KEY_COLUMNS = ("symbol", "ex_date", "series", "subject_hash", "part_no")


# ---------------------------------------------------------------------------
# Raw files
# ---------------------------------------------------------------------------
def save_raw_files(
    output_dir: Path,
    api_rows: list[dict[str, Any]],
    symbol_change_bytes: bytes,
    listing_frame: pd.DataFrame,
) -> None:
    """Write the raw API rows, the raw symbol-change CSV and the parsed listing master."""
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "corporate_actions_raw.json").write_text(
        json.dumps(api_rows, ensure_ascii=False), encoding="utf-8"
    )
    (output_dir / "symbol_changes_raw.csv").write_bytes(symbol_change_bytes)
    listing_frame.to_parquet(output_dir / "equity_listing.parquet", index=False)


# ---------------------------------------------------------------------------
# Database writes
# ---------------------------------------------------------------------------
def store_corporate_actions(session: Session, rows: list[dict[str, Any]]) -> int:
    """Upsert corporate-action rows. Safe to run again: the same key updates in place."""
    return upsert_rows(
        session,
        CorporateAction,
        rows,
        index_elements=list(_KEY_COLUMNS),
        coalesce_columns=(),
    )


def store_symbol_changes(session: Session, frame: pd.DataFrame) -> int:
    """Upsert the official symbol changes."""
    rows = frame[["old_symbol", "new_symbol", "effective_date", "company"]].to_dict("records")
    return upsert_rows(
        session,
        SymbolChange,
        rows,
        index_elements=["old_symbol", "new_symbol", "effective_date"],
        coalesce_columns=(),
    )


def _count_rows_not_seen(
    session: Session, new_rows: list[dict[str, Any]], start: date, end: date
) -> int:
    """Count stored rows inside the window that NSE no longer lists (not deleted, only logged).

    Why only log: NSE sometimes moves or cancels an ex-date. A person should look at it
    before anything is removed, and the adjustment reader ignores nothing silently.
    """
    stmt = select(
        CorporateAction.symbol,
        CorporateAction.ex_date,
        CorporateAction.series,
        CorporateAction.subject_hash,
        CorporateAction.part_no,
    ).where(CorporateAction.ex_date >= start, CorporateAction.ex_date <= end)
    stored = {tuple(row) for row in session.execute(stmt).all()}
    fresh = {tuple(row[name] for name in _KEY_COLUMNS) for row in new_rows}
    gone = stored - fresh
    if gone:
        logger.warning(
            "%d stored corporate-action rows in %s..%s are no longer listed by NSE "
            "(kept, not deleted). Example: %s",
            len(gone),
            start,
            end,
            sorted(gone)[:3],
        )
    return len(gone)


def _symbol_changes_touching_securities(session: Session, frame: pd.DataFrame) -> int:
    """How many official symbol changes involve a symbol in our ``securities`` table."""
    ours = set(session.execute(select(Security.symbol)).scalars().all())
    touching = frame["old_symbol"].isin(ours) | frame["new_symbol"].isin(ours)
    return int(touching.sum())


# ---------------------------------------------------------------------------
# The reader for the price-adjustment code
# ---------------------------------------------------------------------------
def get_price_adjustment_events(
    session: Session,
    start: date | Sequence[str] | None = None,
    end: date | None = None,
    symbols: Sequence[str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read official corporate actions in the shape the adjustment code needs.

    Maps events under former names to the stock's stored price symbols so adjustments
    apply across rename boundaries.

    Returns ``(events, review)``:

    * ``events``: one row per ``(symbol, date)`` with columns ``symbol, date, factor, kinds,
      n_parts``. ``factor`` is the PRODUCT of every exact split, bonus and consolidation
      factor on that ex-date, and is what you multiply earlier prices by.
    * ``review``: ``symbol, date, action_type, subject`` for actions that may move the price
      but have no exact factor (rights, mergers, demergers, unreadable text). They are never
      applied automatically.
    """
    actual_symbols = symbols
    actual_start: date | None = None
    actual_end: date | None = end

    if isinstance(start, (list, tuple, set)):
        actual_symbols = list(start)
    elif isinstance(start, str):
        actual_symbols = [start]
    elif isinstance(start, date):
        actual_start = start

    chains = load_chains_from_db(session)

    target_symbols: set[str] = set()
    if actual_symbols is not None:
        for s in actual_symbols:
            sym_norm = s.strip().upper()
            curr = chains.get_current(sym_norm)
            target_symbols.add(sym_norm)
            target_symbols.add(curr)
            target_symbols.update(chains.get_former(curr))

    if target_symbols:
        stored_stmt = (
            select(DailyPrice.symbol).where(DailyPrice.symbol.in_(target_symbols)).distinct()
        )
    else:
        stored_stmt = select(DailyPrice.symbol).distinct()
    stored_symbols = set(session.execute(stored_stmt).scalars().all())

    def _window(stmt: Any) -> Any:
        if actual_start is not None:
            stmt = stmt.where(CorporateAction.ex_date >= actual_start)
        if actual_end is not None:
            stmt = stmt.where(CorporateAction.ex_date <= actual_end)
        return stmt

    exact_stmt = _window(
        select(
            CorporateAction.symbol,
            CorporateAction.ex_date,
            CorporateAction.action_type,
            CorporateAction.subject_hash,
            CorporateAction.part_no,
            CorporateAction.price_factor,
        ).where(
            CorporateAction.parse_status == STATUS_EXACT,
            CorporateAction.action_type.in_(PRICE_ACTION_TYPES),
        )
    )
    if actual_symbols is not None and target_symbols:
        exact_stmt = exact_stmt.where(CorporateAction.symbol.in_(target_symbols))

    raw_exact_rows = session.execute(exact_stmt).all()
    mapped_exact: list[tuple[str, date, str, str, int, float]] = []
    for row in raw_exact_rows:
        sym = row.symbol.strip().upper()
        curr = chains.get_current(sym)
        formers = chains.get_former(curr)
        family = {curr, sym, *formers}
        stock_stored = family & stored_symbols if stored_symbols else {sym}
        targets = stock_stored if stock_stored else {sym}
        for target_sym in targets:
            mapped_exact.append(
                (
                    target_sym,
                    row.ex_date,
                    row.action_type,
                    row.subject_hash,
                    row.part_no,
                    float(row.price_factor),
                )
            )

    exact = pd.DataFrame(
        mapped_exact,
        columns=["symbol", "date", "kind", "subject_hash", "part_no", "factor"],
    )
    if exact.empty:
        events = pd.DataFrame(columns=["symbol", "date", "factor", "kinds", "n_parts"])
    else:
        # The same action can be listed under two series (for example EQ and BE); count it once.
        exact = exact.drop_duplicates(["symbol", "date", "subject_hash", "part_no"])
        exact["factor"] = exact["factor"].astype(float)
        events = (
            exact.groupby(["symbol", "date"])
            .agg(
                factor=("factor", "prod"),
                kinds=("kind", lambda kinds: "+".join(sorted(set(kinds)))),
                n_parts=("factor", "size"),
            )
            .reset_index()
        )
        events["factor"] = events["factor"].round(8)
        events = events.sort_values(["symbol", "date"]).reset_index(drop=True)

    review_stmt = _window(
        select(
            CorporateAction.symbol,
            CorporateAction.ex_date,
            CorporateAction.action_type,
            CorporateAction.subject,
        ).where(CorporateAction.parse_status == STATUS_REVIEW)
    )
    if actual_symbols is not None and target_symbols:
        review_stmt = review_stmt.where(CorporateAction.symbol.in_(target_symbols))

    raw_review_rows = session.execute(review_stmt).all()
    mapped_review: list[tuple[str, date, str, str]] = []
    for row in raw_review_rows:
        sym = row.symbol.strip().upper()
        curr = chains.get_current(sym)
        formers = chains.get_former(curr)
        family = {curr, sym, *formers}
        stock_stored = family & stored_symbols if stored_symbols else {sym}
        targets = stock_stored if stock_stored else {sym}
        for target_sym in targets:
            mapped_review.append((target_sym, row.ex_date, row.action_type, row.subject))

    review = pd.DataFrame(
        mapped_review,
        columns=["symbol", "date", "action_type", "subject"],
    )
    if not review.empty:
        review = review.drop_duplicates(["symbol", "date", "action_type", "subject"])
    review = review.sort_values(["symbol", "date"]).reset_index(drop=True)
    return events, review


# ---------------------------------------------------------------------------
# The whole job
# ---------------------------------------------------------------------------
def run_corporate_actions_download(
    start: date | None = None,
    end: date | None = None,
    downloader: NSEReportsDownloader | None = None,
    session: Session | None = None,
    output_dir: Path | None = None,
    today: date | None = None,
    fetch_fn: Callable[..., bytes] | None = None,
) -> dict[str, Any]:
    """Download the three NSE reports, check them, keep the raw files, and store them.

    Order matters: everything is downloaded and checked BEFORE the first database write,
    so a failure in any report leaves the database exactly as it was.
    Returns a summary dictionary (also logged).
    """
    today = today or date.today()
    default_start, default_end = default_window(
        today, CORP_ACTIONS_HISTORY_YEARS, CORP_ACTIONS_FORWARD_DAYS
    )
    start = start or default_start
    end = end or default_end
    out_dir = output_dir or CORP_ACTIONS_DIR
    fetcher = downloader or NSEReportsDownloader(fetch_fn=fetch_fn)
    fetched_at = datetime.now().replace(microsecond=0)

    # 1. Download everything (no database yet).
    api_rows = fetcher.fetch_corporate_actions(start, end, today)
    symbol_change_bytes, symbol_changes = fetcher.fetch_symbol_changes()
    _, listing = fetcher.fetch_listing_master()

    # 2. Keep the untouched responses before interpreting them.
    save_raw_files(out_dir, api_rows, symbol_change_bytes, listing)

    # 3. Parse and run the wording canary.
    action_rows, parts = build_action_rows(api_rows, fetched_at)
    unparsed_share = unparsed_price_share(parts)
    if unparsed_share > CORP_ACTIONS_MAX_UNPARSED_SHARE:
        raise NSEFormatChangedError(
            f"{unparsed_share:.1%} of split/bonus/consolidation actions could not be read "
            f"(limit {CORP_ACTIONS_MAX_UNPARSED_SHARE:.0%}). NSE probably changed its wording. "
            "Nothing was written to the database; the raw data is in "
            f"{out_dir / 'corporate_actions_raw.json'}."
        )

    # 4. Write to the database.
    with nullcontext(session) if session is not None else _open_session() as db:
        not_seen = _count_rows_not_seen(db, action_rows, start, end)
        stored = store_corporate_actions(db, action_rows)
        changes_stored = store_symbol_changes(db, symbol_changes)
        touching = _symbol_changes_touching_securities(db, symbol_changes)

    by_type = Counter(row["action_type"] for row in action_rows)
    exact_events = {
        (row["symbol"], row["ex_date"]) for row in action_rows if row["parse_status"] == "exact"
    }
    summary = {
        "window": (start, end),
        "api_rows": len(api_rows),
        "rows_stored": stored,
        "by_type": dict(by_type),
        "exact_factor_events": len(exact_events),
        "needs_review": sum(1 for row in action_rows if row["parse_status"] == STATUS_REVIEW),
        "unparsed_price_share": round(unparsed_share, 4),
        "not_seen_in_window": not_seen,
        "symbol_changes_stored": changes_stored,
        "symbol_changes_touching_securities": touching,
        "listing_rows": len(listing),
    }
    logger.info("Corporate actions download complete: %s", summary)
    return summary


def _open_session() -> Any:
    """Open the project's database session (commits on success, rolls back on error)."""
    from src.db.session import get_db

    return get_db()
