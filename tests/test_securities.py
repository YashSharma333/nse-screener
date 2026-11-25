"""
Tests for Securities Listing Master Ingestion (Task D8)
======================================================
Tests import_equity_l, idempotency, SME/ETF filtering, demerger handling,
and schema defaults. All tests run offline using in-memory SQLite and mock data.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import Base, Security
from src.ingestion.corporate_actions import NSEFormatChangedError
from src.ingestion.securities import import_equity_l

# Sample EQUITY_L.csv with typical leading header spaces and formatting
SAMPLE_EQUITY_L_CSV = (
    "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, "
    "MARKET LOT, ISIN NUMBER, FACE VALUE\n"
    "RELIANCE,Reliance Industries Limited,EQ,29-NOV-1995,10,1,INE002A01018,10\n"
    "INFY,Infosys Limited,EQ,08-FEB-1995,5,1,INE009A01021,5\n"
    "TMCV,Tata Motors Commercial Vehicles Limited,EQ,01-JAN-2025,2,1,INE155A01022,2\n"
    "TMPV,Tata Motors Passenger Vehicles Limited,EQ,01-JAN-2025,2,1,INE155A01030,2\n"
)


@pytest.fixture
def sqlite_session() -> Generator[Session, None, None]:
    """Provide an isolated in-memory SQLite database session."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


def test_equity_l_import_idempotent(sqlite_session: Session) -> None:
    """Running import_equity_l twice with the same data produces the exact same row count."""
    count1 = import_equity_l(session=sqlite_session, csv_content=SAMPLE_EQUITY_L_CSV)
    assert count1 == 4

    rows1 = sqlite_session.scalars(select(Security)).all()
    assert len(rows1) == 4

    # Run a second time
    count2 = import_equity_l(session=sqlite_session, csv_content=SAMPLE_EQUITY_L_CSV)
    assert count2 == 4

    rows2 = sqlite_session.scalars(select(Security)).all()
    assert len(rows2) == 4

    rel = sqlite_session.get(Security, "RELIANCE")
    assert rel is not None
    assert rel.isin == "INE002A01018"
    assert rel.series == "EQ"
    assert rel.listing_date == date(1995, 11, 29)
    assert rel.status == "active"


def test_etf_and_sme_not_in_securities_because_not_in_file(sqlite_session: Session) -> None:
    """ETFs and SME stocks not published in EQUITY_L.csv are not inserted into securities."""
    import_equity_l(session=sqlite_session, csv_content=SAMPLE_EQUITY_L_CSV)

    # NIFTYBEES (ETF) and SMESYMBOL (SME) were not in EQUITY_L.csv
    niftybees = sqlite_session.get(Security, "NIFTYBEES")
    sme_stock = sqlite_session.get(Security, "SMESYMBOL")

    assert niftybees is None
    assert sme_stock is None


def test_demerger_two_symbols_stay_separate(sqlite_session: Session) -> None:
    """Demerged entities (e.g. TMCV and TMPV) are inserted as two separate securities.

    Company name prefix is never used as the primary key; symbol is the key.
    """
    import_equity_l(session=sqlite_session, csv_content=SAMPLE_EQUITY_L_CSV)

    tmcv = sqlite_session.get(Security, "TMCV")
    tmpv = sqlite_session.get(Security, "TMPV")

    assert tmcv is not None
    assert tmpv is not None
    assert tmcv.symbol == "TMCV"
    assert tmpv.symbol == "TMPV"
    assert tmcv.isin == "INE155A01022"
    assert tmpv.isin == "INE155A01030"
    assert tmcv.symbol != tmpv.symbol


def test_securities_default_status_active(sqlite_session: Session) -> None:
    """Newly imported securities have default status active."""
    import_equity_l(session=sqlite_session, csv_content=SAMPLE_EQUITY_L_CSV)

    infy = sqlite_session.get(Security, "INFY")
    assert infy is not None
    assert infy.status == "active"


def test_missing_required_columns_raises_error(sqlite_session: Session) -> None:
    """EQUITY_L.csv missing critical columns stops the import and stores nothing."""
    bad_csv = "SYMBOL,SERIES\nINFY,EQ\n"
    with pytest.raises(NSEFormatChangedError, match="missing columns"):
        import_equity_l(session=sqlite_session, csv_content=bad_csv)
    assert sqlite_session.scalars(select(Security)).all() == []


def test_blank_symbol_rows_are_skipped(sqlite_session: Session) -> None:
    """A row without a symbol cannot be a security; the other rows still load."""
    csv = SAMPLE_EQUITY_L_CSV + " ,No Symbol Co,EQ,01-JAN-2020,1,1,INE000000000,1\n"
    assert import_equity_l(session=sqlite_session, csv_content=csv) == 4


def test_bad_listing_date_stops_the_import(sqlite_session: Session) -> None:
    """A date NSE writes in a new format must not be stored as a wrong or empty date."""
    csv = SAMPLE_EQUITY_L_CSV.replace("29-NOV-1995", "1995/11/29")
    with pytest.raises(NSEFormatChangedError):
        import_equity_l(session=sqlite_session, csv_content=csv)
    assert sqlite_session.scalars(select(Security)).all() == []


def test_import_does_not_commit_a_given_session(sqlite_session: Session) -> None:
    """The caller owns the transaction: a rollback by the caller undoes the import."""
    import_equity_l(session=sqlite_session, csv_content=SAMPLE_EQUITY_L_CSV)
    sqlite_session.rollback()

    assert sqlite_session.scalars(select(Security)).all() == []
