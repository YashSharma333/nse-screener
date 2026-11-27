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
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from config import settings
from config.settings import (
    CORP_ACTIONS_FORWARD_DAYS,
    CORP_ACTIONS_HISTORY_YEARS,
    CORP_ACTIONS_MAX_UNPARSED_SHARE,
)
from src.common.db import use_session
from src.common.symbols import normalise_symbol
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
from src.ingestion.renames import family, keep_held_symbols, load_chains_from_db

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
    """Upsert the official symbol changes of stocks we hold (fund renames are skipped)."""
    rows = frame[["old_symbol", "new_symbol", "effective_date", "company"]].to_dict("records")
    rows = keep_held_symbols(session, rows)
    if not rows:
        return 0
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
def _stored_targets(symbol: str, chains: Any, stored_symbols: set[str]) -> set[str]:
    """The stored price symbols an NSE action applies to (all names of the same stock).

    Why: an event listed under a former name must adjust prices stored under the new name
    (and the other way round). With no stored match the action keeps its own symbol.
    """
    sym = normalise_symbol(symbol)
    family_in_prices = family(sym, chains) & stored_symbols if stored_symbols else {sym}
    return family_in_prices or {sym}


def _in_window(stmt: Any, start: date | None, end: date | None) -> Any:
    """Limit a corporate-actions query to ex-dates from ``start`` to ``end`` (both optional)."""
    if start is not None:
        stmt = stmt.where(CorporateAction.ex_date >= start)
    if end is not None:
        stmt = stmt.where(CorporateAction.ex_date <= end)
    return stmt


def _combine_exact_parts(exact: pd.DataFrame) -> pd.DataFrame:
    """One row per (symbol, date): the product of every exact factor on that ex-date."""
    if exact.empty:
        return pd.DataFrame(columns=["symbol", "date", "factor", "kinds", "n_parts"])
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
    return events.sort_values(["symbol", "date"]).reset_index(drop=True)


def get_price_adjustment_events(
    session: Session,
    symbols: Sequence[str] | None = None,
    start: date | None = None,
    end: date | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read official corporate actions in the shape the adjustment code needs.

    ``symbols`` limits the result to those stocks (all their names); ``start`` and ``end``
    limit the ex-dates. Events under former names are mapped to the stock's stored price
    symbols, so adjustments apply across rename boundaries.

    Returns ``(events, review)``:

    * ``events``: one row per ``(symbol, date)`` with columns ``symbol, date, factor, kinds,
      n_parts``. ``factor`` is the PRODUCT of every exact split, bonus and consolidation
      factor on that ex-date, and is what you multiply earlier prices by.
    * ``review``: ``symbol, date, action_type, subject`` for actions that may move the price
      but have no exact factor (rights, mergers, demergers, unreadable text). They are never
      applied automatically.
    """
    chains = load_chains_from_db(session)
    wanted: set[str] = set()
    for symbol in symbols or []:
        wanted.update(family(symbol, chains))

    stored_stmt = select(DailyPrice.symbol).distinct()
    if wanted:
        stored_stmt = stored_stmt.where(DailyPrice.symbol.in_(wanted))
    stored_symbols = set(session.execute(stored_stmt).scalars().all())

    exact_stmt = _in_window(
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
        ),
        start,
        end,
    )
    review_stmt = _in_window(
        select(
            CorporateAction.symbol,
            CorporateAction.ex_date,
            CorporateAction.action_type,
            CorporateAction.subject,
        ).where(CorporateAction.parse_status == STATUS_REVIEW),
        start,
        end,
    )
    if wanted:
        exact_stmt = exact_stmt.where(CorporateAction.symbol.in_(wanted))
        review_stmt = review_stmt.where(CorporateAction.symbol.in_(wanted))

    exact_parts = [
        (
            target,
            row.ex_date,
            row.action_type,
            row.subject_hash,
            row.part_no,
            float(row.price_factor),
        )
        for row in session.execute(exact_stmt).all()
        for target in _stored_targets(row.symbol, chains, stored_symbols)
    ]
    exact = pd.DataFrame(
        exact_parts, columns=["symbol", "date", "kind", "subject_hash", "part_no", "factor"]
    )

    review_items = [
        (target, row.ex_date, row.action_type, row.subject)
        for row in session.execute(review_stmt).all()
        for target in _stored_targets(row.symbol, chains, stored_symbols)
    ]
    review = pd.DataFrame(review_items, columns=["symbol", "date", "action_type", "subject"])
    if not review.empty:
        review = review.drop_duplicates(["symbol", "date", "action_type", "subject"])
    review = review.sort_values(["symbol", "date"]).reset_index(drop=True)
    return _combine_exact_parts(exact), review


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
    out_dir = output_dir or settings.CORP_ACTIONS_DIR
    fetcher = downloader or NSEReportsDownloader()
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
    with use_session(session) as db:
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
