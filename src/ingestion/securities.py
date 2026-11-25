"""
Securities Listing Master Importer (Task D8)
============================================
Imports the official equity listing master (EQUITY_L.csv) from NSE into the
``securities`` table.

Columns in securities table:
- symbol (VARCHAR(20), PRIMARY KEY)
- isin (VARCHAR(12), NULL)
- name (VARCHAR(200), NULL)
- series (VARCHAR(4), NULL)
- listing_date (DATE, NULL)
- first_seen (DATE, NULL)   -- populated in D9
- last_seen (DATE, NULL)    -- populated in D9
- status (VARCHAR(10), NOT NULL, default 'active')

Key rules:
- Reads CSV header first to verify columns.
- Uses official NSE file helper (fetch) from src/ingestion/nse_files.py.
- Upsert into securities with symbol as key; status stays "active" for new rows.
- Never deletes a row here; symbols that left the file are handled in D9.
- Idempotent: repeated runs produce identical table state.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from src.common.db import use_session
from src.common.symbols import normalise_symbol
from src.db.models import Security
from src.db.upsert import upsert_rows
from src.ingestion.corporate_actions import parse_listing_master
from src.ingestion.nse_files import EQUITY_L_URL, fetch

logger = logging.getLogger(__name__)


def listing_rows(raw: bytes | str) -> list[dict[str, Any]]:
    """Turn EQUITY_L.csv into rows for the ``securities`` table.

    Reading the file is done by ``parse_listing_master`` (the one EQUITY_L parser: strict dates,
    a changed format stops the run). Here we only pick the table columns and skip blank symbols.
    """
    content = raw.encode("utf-8") if isinstance(raw, str) else raw
    frame = parse_listing_master(content)
    rows: list[dict[str, Any]] = []
    for item in frame.itertuples(index=False):
        symbol = normalise_symbol(item.symbol)
        if not symbol:
            continue
        rows.append(
            {
                "symbol": symbol,
                "isin": item.isin or None,
                "name": item.company or None,
                "series": normalise_symbol(item.series) or None,
                "listing_date": item.listing_date,
                "status": "active",
            }
        )
    return rows


def import_equity_l(
    session: Session | None = None,
    fetch_fn: Callable[[str], bytes] | None = None,
    csv_content: bytes | str | None = None,
    url: str = EQUITY_L_URL,
) -> int:
    """Download and upsert EQUITY_L.csv into the securities table.

    Parameters
    ----------
    session : Session, optional
        Database session. If None, opens its own (committed on success).
    fetch_fn : callable, optional
        Custom fetch function (url -> bytes) for testing.
    csv_content : bytes or str, optional
        Pre-loaded CSV content, bypassing network download.
    url : str, default EQUITY_L_URL
        URL to fetch from.

    Returns
    -------
    int
        Number of security rows upserted.
    """
    if csv_content is not None:
        raw_data = csv_content
    elif fetch_fn is not None:
        raw_data = fetch_fn(url)
    else:
        raw_data = fetch(url)

    records = listing_rows(raw_data)
    if not records:
        logger.warning("No valid securities parsed from EQUITY_L.csv")
        return 0

    def _execute_import(s: Session) -> int:
        count = upsert_rows(
            session=s,
            model=Security,
            rows=records,
            index_elements=["symbol"],
            coalesce_columns=["status"],
        )
        logger.info("Successfully imported/upserted %d securities into `securities`", count)
        return count

    with use_session(session) as s:
        return _execute_import(s)
