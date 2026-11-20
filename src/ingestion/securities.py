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

import io
import logging
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from src.db.models import Security
from src.db.session import get_db
from src.db.upsert import upsert_rows
from src.ingestion.nse_files import EQUITY_L_URL, check_not_html, fetch

logger = logging.getLogger(__name__)

# Required columns in EQUITY_L.csv (normalized to stripped uppercase)
REQUIRED_EQUITY_L_COLUMNS: tuple[str, ...] = (
    "SYMBOL",
    "NAME OF COMPANY",
    "SERIES",
    "DATE OF LISTING",
    "ISIN NUMBER",
)


def _parse_listing_date(val: Any) -> date | None:
    """Parse NSE listing date in formats like '19-SEP-2019' or '19-09-2019'."""
    if val is None or pd.isna(val):
        return None
    s = str(val).strip()
    if not s or s in ("-", "NA", "NULL"):
        return None

    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue

    logger.warning("Could not parse listing date '%s'", s)
    return None


def parse_equity_l(content: bytes | str) -> list[dict[str, Any]]:
    """Parse raw bytes or text of EQUITY_L.csv into a list of row dicts for `securities`.

    Parameters
    ----------
    content : bytes or str
        Raw content of EQUITY_L.csv.

    Returns
    -------
    list[dict[str, Any]]
        List of dicts ready for insertion into the `securities` table.
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

    # Normalize column names: stripped uppercase
    df.columns = [str(c).strip().upper() for c in df.columns]

    missing = [c for c in REQUIRED_EQUITY_L_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            f"EQUITY_L.csv missing required columns: {missing}; found: {list(df.columns)}"
        )

    logger.info("Parsed EQUITY_L header: %s (%d rows)", list(df.columns), len(df))

    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        sym = str(row["SYMBOL"]).strip().upper()
        if not sym:
            continue

        isin = str(row["ISIN NUMBER"]).strip() or None
        name = str(row["NAME OF COMPANY"]).strip() or None
        series = str(row["SERIES"]).strip().upper() or None
        listing_date = _parse_listing_date(row["DATE OF LISTING"])

        rows.append(
            {
                "symbol": sym,
                "isin": isin,
                "name": name,
                "series": series,
                "listing_date": listing_date,
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
        Database session. If None, acquires one via get_db().
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

    records = parse_equity_l(raw_data)
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
        s.commit()
        logger.info("Successfully imported/upserted %d securities into `securities`", count)
        return count

    if session is not None:
        return _execute_import(session)

    with get_db() as managed_session:
        return _execute_import(managed_session)
