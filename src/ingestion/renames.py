"""
Symbol Renames & Continuity Resolution (Task D8)
================================================
Manages official NSE symbol renames from ``symbolchange.csv``.

Architectural Rules:
1. No raw price row in ``daily_prices`` is ever relabelled or updated.
2. Symbol renames are resolved dynamically at read time.
3. Builds rename chains (A -> B -> C) sorted chronologically by effective date.
4. Protects against cycles with explicit error raising.
5. Uses ISIN cross-verification where available to confirm security identity.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.common.dates import is_blank_date, parse_nse_date
from src.common.db import use_session
from src.common.symbols import normalise_symbol
from src.db.models import DailyPrice, Security, SymbolChange
from src.db.upsert import upsert_rows
from src.ingestion.nse_files import SYMBOLCHANGE_URL, check_not_html, fetch

logger = logging.getLogger(__name__)

REQUIRED_SYMBOLCHANGE_COLUMNS: tuple[str, ...] = (
    "NAME OF THE COMPANY",
    "OLD SYMBOL",
    "NEW SYMBOL",
    "EFFECTIVE DATE",
)


def _parse_effective_date(val: Any) -> date | None:
    """Parse effective date in formats like '30-OCT-2019' or '30-10-2019' (warns if unreadable)."""
    parsed = parse_nse_date(val)
    if parsed is None and not is_blank_date(val):
        logger.warning("Could not parse symbol change effective date '%s'", str(val).strip())
    return parsed


@dataclass
class RenameChains:
    """Represents resolved symbol rename chains and historical name lineages."""

    # Maps any symbol (old or intermediate) to its current/latest symbol
    latest_map: dict[str, str] = field(default_factory=dict)
    # Maps a symbol to its list of earlier names (oldest first)
    former_map: dict[str, list[str]] = field(default_factory=dict)
    # Raw transition events: (old, new, date, company)
    transitions: list[tuple[str, str, date, str | None]] = field(default_factory=list)

    def get_current(self, symbol: str) -> str:
        """Return the latest active symbol for a given symbol."""
        sym = normalise_symbol(symbol)
        return self.latest_map.get(sym, sym)

    def get_former(self, symbol: str) -> list[str]:
        """Return all former names for a symbol (oldest first)."""
        sym = normalise_symbol(symbol)
        return list(self.former_map.get(sym, []))

    def get_history(self, symbol: str) -> list[dict[str, Any]]:
        """Return all rename events for this security in chronological order."""
        sym = normalise_symbol(symbol)
        curr = self.get_current(sym)
        formers = self.get_former(curr)
        all_syms = {*formers, curr, sym}
        return [
            {
                "old_symbol": old_s,
                "new_symbol": new_s,
                "effective_date": dt,
                "company": comp,
            }
            for old_s, new_s, dt, comp in self.transitions
            if old_s in all_syms or new_s in all_syms
        ]


def parse_symbol_changes_csv(content: bytes | str) -> list[dict[str, Any]]:
    """Parse raw bytes or text of symbolchange.csv into valid change records.

    Rules applied:
    - Skips rows with a blank company name (filters out bonds/debentures).
    - Skips rows where old_symbol == new_symbol.
    - Normalizes symbol text to uppercase.
    """
    check_not_html(content)

    if isinstance(content, bytes):
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("latin-1")
    else:
        text = content

    df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    df.columns = [str(c).strip().upper() for c in df.columns]

    missing = [c for c in REQUIRED_SYMBOLCHANGE_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            f"symbolchange.csv missing required columns: {missing}; found: {list(df.columns)}"
        )

    records: list[dict[str, Any]] = []
    skipped_blank_comp = 0
    skipped_same_sym = 0

    for _, row in df.iterrows():
        comp = str(row["NAME OF THE COMPANY"]).strip()
        # Rule: skip rows with a blank company name (bonds / debt instruments)
        if not comp:
            skipped_blank_comp += 1
            continue

        old_sym = normalise_symbol(row["OLD SYMBOL"])
        new_sym = normalise_symbol(row["NEW SYMBOL"])
        if not old_sym or not new_sym:
            continue

        # Rule: skip old == new
        if old_sym == new_sym:
            skipped_same_sym += 1
            continue

        eff_date = _parse_effective_date(row["EFFECTIVE DATE"])
        if eff_date is None:
            logger.warning("Skipping row with invalid effective date: %s -> %s", old_sym, new_sym)
            continue

        records.append(
            {
                "old_symbol": old_sym,
                "new_symbol": new_sym,
                "effective_date": eff_date,
                "company": comp,
            }
        )

    logger.info(
        "Parsed %d symbol changes (skipped %d blank company rows, %d same-symbol rows)",
        len(records),
        skipped_blank_comp,
        skipped_same_sym,
    )
    return records


def load_renames(
    session: Session | None = None,
    fetch_fn: Callable[[str], bytes] | None = None,
    csv_content: bytes | str | None = None,
    url: str = SYMBOLCHANGE_URL,
) -> int:
    """Download and upsert symbol changes from symbolchange.csv into symbol_changes.

    Parameters
    ----------
    session : Session, optional
        Database session.
    fetch_fn : callable, optional
        Fetch function for testing.
    csv_content : bytes or str, optional
        Raw CSV content if already downloaded.
    url : str, default SYMBOLCHANGE_URL
        URL to fetch from.

    Returns
    -------
    int
        Number of symbol change records upserted.
    """
    if csv_content is not None:
        raw_data = csv_content
    elif fetch_fn is not None:
        raw_data = fetch_fn(url)
    else:
        raw_data = fetch(url)

    records = parse_symbol_changes_csv(raw_data)
    if not records:
        logger.warning("No valid symbol change records to store.")
        return 0

    def _execute_load(s: Session) -> int:
        count = upsert_rows(
            session=s,
            model=SymbolChange,
            rows=records,
            index_elements=["old_symbol", "new_symbol", "effective_date"],
        )
        logger.info("Upserted %d symbol changes into `symbol_changes` table", count)
        return count

    with use_session(session) as s:
        return _execute_load(s)


def build_rename_chains(rows: Sequence[dict[str, Any] | SymbolChange]) -> RenameChains:
    """Build directed rename chains from symbol change events with cycle detection.

    Parameters
    ----------
    rows : Sequence[dict | SymbolChange]
        List of symbol changes containing old_symbol, new_symbol, effective_date.

    Returns
    -------
    RenameChains
        Object holding latest_map and former_map.

    Raises
    ------
    ValueError
        If a cyclic rename chain (e.g. A -> B -> A) is detected.
    """
    normalized_events: list[tuple[str, str, date, str | None]] = []
    for r in rows:
        if isinstance(r, dict):
            old_s = normalise_symbol(r["old_symbol"])
            new_s = normalise_symbol(r["new_symbol"])
            dt = r["effective_date"]
            if isinstance(dt, str):
                dt = _parse_effective_date(dt)
            comp = r.get("company")
        else:
            old_s = normalise_symbol(r.old_symbol)
            new_s = normalise_symbol(r.new_symbol)
            dt = r.effective_date
            comp = r.company

        if not old_s or not new_s or old_s == new_s or dt is None:
            continue
        normalized_events.append((old_s, new_s, dt, comp))

    # Sort events chronologically by effective date
    normalized_events.sort(key=lambda x: x[2])

    # Direct mapping: old -> new
    # If multiple renames exist for the same old symbol, later date takes precedence
    forward_map: dict[str, str] = {}
    for old_s, new_s, _, _ in normalized_events:
        forward_map[old_s] = new_s

    latest_map: dict[str, str] = {}
    former_map: dict[str, list[str]] = {}

    # Follow chains and detect cycles
    for old_s in forward_map:
        curr = old_s
        path: list[str] = [curr]
        visited: set[str] = {curr}

        while curr in forward_map:
            nxt = forward_map[curr]
            if nxt in visited:
                cycle_idx = path.index(nxt)
                cycle_str = " -> ".join([*path[cycle_idx:], nxt])
                raise ValueError(f"Cycle detected in symbol rename chain: {cycle_str}")
            visited.add(nxt)
            path.append(nxt)
            curr = nxt

        latest = curr
        latest_map[old_s] = latest

        # Build former lineage for each intermediate node
        # path is [A, B, C] -> for B, former is [A]; for C, former is [A, B]
        for i in range(1, len(path)):
            node = path[i]
            prefix_formers = path[:i]
            if node not in former_map:
                former_map[node] = list(prefix_formers)
            else:
                # Merge maintaining chronological order without duplicates
                current_list = former_map[node]
                for p in prefix_formers:
                    if p not in current_list:
                        current_list.append(p)

    return RenameChains(
        latest_map=latest_map,
        former_map=former_map,
        transitions=normalized_events,
    )


def load_chains_from_db(session: Session) -> RenameChains:
    """Fetch all rows from symbol_changes and construct active RenameChains."""
    stmt = select(SymbolChange).order_by(SymbolChange.effective_date.asc())
    records = session.scalars(stmt).all()
    return build_rename_chains(records)


def current_symbol(
    symbol: str,
    chains: RenameChains | None = None,
    session: Session | None = None,
) -> str:
    """Return the latest active symbol for a security.

    If the symbol has never undergone a rename, returns the symbol itself.
    """
    sym = normalise_symbol(symbol)
    if chains is not None:
        return chains.get_current(sym)

    with use_session(session) as s:
        return load_chains_from_db(s).get_current(sym)


def former_names(
    symbol: str,
    chains: RenameChains | None = None,
    session: Session | None = None,
) -> list[str]:
    """Return every earlier name of a security in chronological order (oldest first).

    If the symbol has no earlier names, returns an empty list.
    """
    sym = normalise_symbol(symbol)
    if chains is not None:
        return chains.get_former(sym)

    with use_session(session) as s:
        return load_chains_from_db(s).get_former(sym)


def rename_history(
    symbol: str,
    chains: RenameChains | None = None,
    session: Session | None = None,
) -> list[dict[str, Any]]:
    """Return all rename events (with exact effective dates) for a security in date order."""
    sym = normalise_symbol(symbol)
    if chains is not None:
        return chains.get_history(sym)

    with use_session(session) as s:
        return load_chains_from_db(s).get_history(sym)


def rename_date(
    old_symbol: str,
    new_symbol: str | None = None,
    chains: RenameChains | None = None,
    session: Session | None = None,
) -> date | None:
    """Return the exact effective date when old_symbol was renamed.

    Parameters
    ----------
    old_symbol : str
        The earlier symbol.
    new_symbol : str, optional
        Target new symbol. If omitted, returns the date of the first rename away from old_symbol.
    """
    history = rename_history(old_symbol, chains=chains, session=session)
    old_s = normalise_symbol(old_symbol)
    new_s = normalise_symbol(new_symbol) if new_symbol else None

    for event in history:
        if event["old_symbol"] == old_s and (new_s is None or event["new_symbol"] == new_s):
            return event["effective_date"]
    return None


def verify_rename_by_isin(
    old_symbol: str,
    new_symbol: str,
    session: Session,
) -> dict[str, Any]:
    """Cross-verify a rename pair using ISIN records from `securities` or `daily_prices`.

    When a legitimate company renames, its 12-character ISIN remains identical.
    """
    old_s = normalise_symbol(old_symbol)
    new_s = normalise_symbol(new_symbol)

    # 1. Check securities table
    old_sec = session.get(Security, old_s)
    new_sec = session.get(Security, new_s)

    old_isin = old_sec.isin if old_sec else None
    new_isin = new_sec.isin if new_sec else None

    # 2. Check daily_prices if ISIN not found in securities
    if not old_isin:
        stmt = (
            select(DailyPrice.isin)
            .where(DailyPrice.symbol == old_s, DailyPrice.isin.isnot(None))
            .limit(1)
        )
        old_isin = session.execute(stmt).scalar()

    if not new_isin:
        stmt = (
            select(DailyPrice.isin)
            .where(DailyPrice.symbol == new_s, DailyPrice.isin.isnot(None))
            .limit(1)
        )
        new_isin = session.execute(stmt).scalar()

    if old_isin and new_isin:
        match = old_isin == new_isin
        status = "CONFIRMED_MATCH" if match else "ISIN_MISMATCH"
        if not match:
            logger.warning(
                "ISIN Mismatch for rename %s -> %s: old_isin=%s, new_isin=%s",
                old_s,
                new_s,
                old_isin,
                new_isin,
            )
        return {
            "status": status,
            "old_isin": old_isin,
            "new_isin": new_isin,
            "match": match,
        }

    return {
        "status": "INSUFFICIENT_ISIN_DATA",
        "old_isin": old_isin,
        "new_isin": new_isin,
        "match": None,
    }


def prices_for(
    symbol: str,
    session: Session | None = None,
    chains: RenameChains | None = None,
) -> list[DailyPrice]:
    """Retrieve all daily price records for a security across all its historical symbols.

    Guarantees:
    - Queries all names: current name + every former name.
    - Ordered chronologically by date ASC.
    - Read-only: daily_prices is never updated, modified, or relabelled.
    """
    sym = normalise_symbol(symbol)

    def _query(s: Session) -> list[DailyPrice]:
        nonlocal chains
        if chains is None:
            chains = load_chains_from_db(s)

        curr = chains.get_current(sym)
        formers = chains.get_former(curr)
        all_symbols = list(dict.fromkeys([*formers, curr, sym]))

        stmt = (
            select(DailyPrice)
            .where(DailyPrice.symbol.in_(all_symbols))
            .order_by(DailyPrice.date.asc(), DailyPrice.symbol.asc())
        )
        return list(s.scalars(stmt).all())

    with use_session(session) as s:
        return _query(s)
