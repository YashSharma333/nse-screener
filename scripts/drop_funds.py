"""
One-time cleanup: remove ETFs / fund units (ISIN starts with INF) from everything we store.

New loads already skip funds (``src.common.symbols.drop_funds``). This script removes the
funds that were loaded BEFORE that rule: rows in MySQL (daily_prices, adj_factors,
corporate_actions, symbol_changes, securities) and rows in the Parquet caches (Bhavcopy per day,
market cap per day). It is a DRY RUN unless you pass ``--apply``.

    python scripts/drop_funds.py            # count only, changes nothing
    python scripts/drop_funds.py --apply    # delete (run scripts/backup_db.sh first)

No company row changes, so ``--rebuild-adjusted`` is not needed. The indicator cache still holds
the funds until it is rebuilt: run ``python run_pipeline.py --update`` afterwards.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Iterator, Sequence
from pathlib import Path

import pandas as pd
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from config import settings
from src.common.db import use_session
from src.common.symbols import drop_funds
from src.db.models import AdjFactor, CorporateAction, DailyPrice, Security, SymbolChange

logger = logging.getLogger(__name__)

SYMBOL_BATCH = 50  # symbols per DELETE, so no single statement is huge


def fund_symbols(session: Session) -> list[str]:
    """Every stored symbol whose ISIN marks it as a fund unit (delisted ones included)."""
    is_fund = or_(*(DailyPrice.isin.like(f"{p}%") for p in settings.EXCLUDED_ISIN_PREFIXES))
    return sorted(session.scalars(select(DailyPrice.symbol).where(is_fund).distinct()).all())


def batches(items: Sequence[str]) -> Iterator[list[str]]:
    """The items in lists of at most SYMBOL_BATCH."""
    for start in range(0, len(items), SYMBOL_BATCH):
        yield list(items[start : start + SYMBOL_BATCH])


def _fund_filters(symbols: list[str]) -> dict[str, object]:
    """The WHERE condition of each table for one batch of fund symbols."""
    is_fund_isin = or_(
        *(CorporateAction.isin.like(f"{p}%") for p in settings.EXCLUDED_ISIN_PREFIXES)
    )
    security_isin = or_(*(Security.isin.like(f"{p}%") for p in settings.EXCLUDED_ISIN_PREFIXES))
    return {
        "daily_prices": DailyPrice.symbol.in_(symbols),
        "adj_factors": AdjFactor.symbol.in_(symbols),
        "corporate_actions": or_(CorporateAction.symbol.in_(symbols), is_fund_isin),
        "symbol_changes": or_(
            SymbolChange.old_symbol.in_(symbols), SymbolChange.new_symbol.in_(symbols)
        ),
        "securities": or_(Security.symbol.in_(symbols), security_isin),
    }


TABLES = {
    "daily_prices": DailyPrice,
    "adj_factors": AdjFactor,
    "corporate_actions": CorporateAction,
    "symbol_changes": SymbolChange,
    "securities": Security,
}


def clean_database(session: Session, symbols: list[str], apply: bool) -> dict[str, int]:
    """Count (and with ``apply`` delete) fund rows per table. The caller commits.

    The count is one query per table over all symbols, so a row that names two funds (a rename)
    is counted once. Deleting works in batches of symbols; each row is deleted only once.
    """
    counts = dict.fromkeys(TABLES, 0)
    if not apply:
        for name, condition in _fund_filters(symbols).items():
            counts[name] = session.scalar(
                select(func.count()).select_from(TABLES[name]).where(condition)
            )
        return counts
    for batch in batches(symbols):
        for name, condition in _fund_filters(batch).items():
            counts[name] += session.execute(delete(TABLES[name]).where(condition)).rowcount
    return counts


def _rewrite(path: Path, frame: pd.DataFrame) -> None:
    """Replace a Parquet file safely: write a temp file next to it, then swap."""
    temp = path.with_suffix(".tmp")
    frame.to_parquet(temp, index=False, engine="pyarrow")
    temp.replace(path)


def clean_bhavcopy_cache(apply: bool) -> tuple[int, int]:
    """Funds out of the per-day Bhavcopy Parquet files. Returns (files changed, rows removed)."""
    files_changed = rows_removed = 0
    for path in sorted(settings.BHAVCOPY_ALL_DIR.glob("*.parquet")):
        frame = pd.read_parquet(path)
        kept = drop_funds(frame)
        if len(kept) != len(frame):
            files_changed += 1
            rows_removed += len(frame) - len(kept)
            if apply:
                _rewrite(path, kept)
    return files_changed, rows_removed


def clean_mcap_cache(symbols: list[str], apply: bool) -> tuple[int, int]:
    """Fund symbols out of the per-day market cap Parquet files (they carry no ISIN)."""
    fund_set = set(symbols)
    files_changed = rows_removed = 0
    for path in sorted(settings.MCAP_DIR.glob("*.parquet")):
        frame = pd.read_parquet(path)
        kept = frame[~frame["symbol"].isin(fund_set)]
        if len(kept) != len(frame):
            files_changed += 1
            rows_removed += len(frame) - len(kept)
            if apply:
                _rewrite(path, kept.reset_index(drop=True))
    return files_changed, rows_removed


def drop_funds_everywhere(apply: bool = False, session: Session | None = None) -> dict[str, int]:
    """Remove (or with ``apply=False`` only count) fund units from MySQL and the Parquet caches.

    ``session``: used as given and not committed; without one a session is opened (and commits
    when it closes, see ``use_session``).
    """
    with use_session(session) as s:
        symbols = fund_symbols(s)
        result = clean_database(s, symbols, apply)
    result["fund_symbols"] = len(symbols)
    result["bhavcopy_files"], result["bhavcopy_rows"] = clean_bhavcopy_cache(apply)
    result["mcap_files"], result["mcap_rows"] = clean_mcap_cache(symbols, apply)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove ETFs / fund units from the database and caches."
    )
    parser.add_argument("--apply", action="store_true", help="delete (default is a dry run)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    result = drop_funds_everywhere(apply=args.apply)
    verb = "Removed" if args.apply else "Would remove (dry run)"
    print(verb + ":")
    for name, number in result.items():
        print(f"  {name:20s} {number:>10,}")


if __name__ == "__main__":
    main()
