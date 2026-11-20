"""
Tests for Symbol Renames and Read-Time Continuity (Task D8)
===========================================================
Tests rename chains, cycle detection, blank company filtering, ISIN verification,
and read-only continuity price querying. All tests run offline with SQLite.
"""

from __future__ import annotations

import argparse
from collections.abc import Generator
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import Base, DailyPrice, Security, SymbolChange
from src.etl.dry_run import dry_run_is_supported
from src.ingestion.renames import (
    build_rename_chains,
    current_symbol,
    former_names,
    load_renames,
    parse_symbol_changes_csv,
    prices_for,
    rename_date,
    rename_history,
    verify_rename_by_isin,
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


def test_chain_a_b_c() -> None:
    """A renamed B on 2019-01-01, B renamed C on 2021-01-01.

    Gives current_symbol(A) == C and former_names(C) == [A, B].
    """
    changes = [
        {"old_symbol": "A", "new_symbol": "B", "effective_date": date(2019, 1, 1)},
        {"old_symbol": "B", "new_symbol": "C", "effective_date": date(2021, 1, 1)},
    ]
    chains = build_rename_chains(changes)

    assert current_symbol("A", chains=chains) == "C"
    assert current_symbol("B", chains=chains) == "C"
    assert current_symbol("C", chains=chains) == "C"

    # Former names of the latest symbol (oldest first)
    assert former_names("C", chains=chains) == ["A", "B"]
    assert former_names("B", chains=chains) == ["A"]
    assert former_names("A", chains=chains) == []

    # Exact date and timeline retrieval
    hist = rename_history("C", chains=chains)
    assert len(hist) == 2
    assert hist[0]["old_symbol"] == "A"
    assert hist[0]["new_symbol"] == "B"
    assert hist[0]["effective_date"] == date(2019, 1, 1)
    assert hist[1]["old_symbol"] == "B"
    assert hist[1]["new_symbol"] == "C"
    assert hist[1]["effective_date"] == date(2021, 1, 1)

    assert rename_date("A", chains=chains) == date(2019, 1, 1)
    assert rename_date("B", chains=chains) == date(2021, 1, 1)
    assert rename_date("C", chains=chains) is None


def test_rename_keeps_isin_same_security(sqlite_session: Session) -> None:
    """A rename (TATAGLOBAL to TATACONSUM) preserves the underlying ISIN."""
    isin_tata = "INE192A01025"

    # Seed securities / daily_prices
    sec_old = Security(
        symbol="TATAGLOBAL",
        isin=isin_tata,
        name="Tata Global Beverages Limited",
        series="EQ",
        status="active",
    )
    sec_new = Security(
        symbol="TATACONSUM",
        isin=isin_tata,
        name="Tata Consumer Products Limited",
        series="EQ",
        status="active",
    )
    sqlite_session.add_all([sec_old, sec_new])
    sqlite_session.commit()

    res = verify_rename_by_isin("TATAGLOBAL", "TATACONSUM", session=sqlite_session)
    assert res["status"] == "CONFIRMED_MATCH"
    assert res["match"] is True
    assert res["old_isin"] == isin_tata
    assert res["new_isin"] == isin_tata


def test_blank_company_rows_ignored() -> None:
    """Rows with a blank or whitespace company name (bonds/debentures) are ignored."""
    csv_data = (
        "NAME OF THE COMPANY,OLD SYMBOL,NEW SYMBOL,EFFECTIVE DATE\n"
        " ,BONDA,BONDB,01-JAN-2020\n"
        "   ,DEBA,DEBB,01-JAN-2020\n"
        "Tata Global Beverages Limited,TATAGLOBAL,TATACONSUM,10-FEB-2020\n"
    )
    records = parse_symbol_changes_csv(csv_data)
    assert len(records) == 1
    assert records[0]["old_symbol"] == "TATAGLOBAL"
    assert records[0]["new_symbol"] == "TATACONSUM"


def test_cycle_raises_clear_error() -> None:
    """Cyclic rename (A -> B -> A) raises a clear ValueError instead of looping."""
    cyclic_changes = [
        {"old_symbol": "A", "new_symbol": "B", "effective_date": date(2019, 1, 1)},
        {"old_symbol": "B", "new_symbol": "A", "effective_date": date(2020, 1, 1)},
    ]
    with pytest.raises(ValueError, match="Cycle detected in symbol rename chain"):
        build_rename_chains(cyclic_changes)


def test_no_rename_returns_same_symbol() -> None:
    """Symbol that was never renamed returns itself for current_symbol and empty former_names."""
    chains = build_rename_chains([])
    assert current_symbol("INFY", chains=chains) == "INFY"
    assert former_names("INFY", chains=chains) == []


def test_prices_for_joins_old_and_new_rows_in_date_order_and_price_rows_never_modified(
    sqlite_session: Session,
) -> None:
    """prices_for joins old and new rows in date order, and leaves daily_prices unmodified."""
    # Seed symbol change: TATAGLOBAL -> TATACONSUM on 2020-02-10
    sc = SymbolChange(
        old_symbol="TATAGLOBAL",
        new_symbol="TATACONSUM",
        effective_date=date(2020, 2, 10),
        company="Tata Consumer Products Limited",
    )
    sqlite_session.add(sc)

    # Seed daily_prices rows with historical symbols
    p1 = DailyPrice(
        symbol="TATAGLOBAL",
        date=date(2020, 2, 7),
        series="EQ",
        close=Decimal("380.00"),
        volume=100000,
    )
    p2 = DailyPrice(
        symbol="TATACONSUM",
        date=date(2020, 2, 10),
        series="EQ",
        close=Decimal("385.50"),
        volume=120000,
    )
    p3 = DailyPrice(
        symbol="TATACONSUM",
        date=date(2020, 2, 11),
        series="EQ",
        close=Decimal("390.00"),
        volume=115000,
    )
    sqlite_session.add_all([p1, p2, p3])
    sqlite_session.commit()

    # Pre-check row count and symbols
    initial_rows = sqlite_session.scalars(select(DailyPrice).order_by(DailyPrice.date)).all()
    assert len(initial_rows) == 3
    assert initial_rows[0].symbol == "TATAGLOBAL"
    assert initial_rows[1].symbol == "TATACONSUM"
    assert initial_rows[2].symbol == "TATACONSUM"

    # Query via prices_for with either name
    prices_from_new = prices_for("TATACONSUM", session=sqlite_session)
    assert len(prices_from_new) == 3
    assert [p.symbol for p in prices_from_new] == ["TATAGLOBAL", "TATACONSUM", "TATACONSUM"]
    assert [p.date for p in prices_from_new] == [
        date(2020, 2, 7),
        date(2020, 2, 10),
        date(2020, 2, 11),
    ]

    prices_from_old = prices_for("TATAGLOBAL", session=sqlite_session)
    assert len(prices_from_old) == 3
    assert [p.symbol for p in prices_from_old] == ["TATAGLOBAL", "TATACONSUM", "TATACONSUM"]

    # Verify price rows in the DB are NEVER modified (audit and continuity guarantee)
    after_rows = sqlite_session.scalars(select(DailyPrice).order_by(DailyPrice.date)).all()
    assert len(after_rows) == 3
    assert after_rows[0].symbol == "TATAGLOBAL"
    assert after_rows[1].symbol == "TATACONSUM"
    assert after_rows[2].symbol == "TATACONSUM"


def test_dry_run_flag_refused_for_load_renames() -> None:
    """Refuses --dry-run when --load-renames is invoked."""
    args = argparse.Namespace(
        load_securities=False,
        load_renames=True,
        dry_run=True,
        import_universe=None,
        apply_symbol_aliases=False,
        purge_bogus_sessions=False,
        fill_missing_symbols=False,
        detect_renames=False,
    )
    assert not dry_run_is_supported(args)


def test_load_renames_idempotent(sqlite_session: Session) -> None:
    """Running load_renames multiple times produces identical state without duplicates."""
    csv_data = (
        "NAME OF THE COMPANY,OLD SYMBOL,NEW SYMBOL,EFFECTIVE DATE\n"
        "Tata Global Beverages Limited,TATAGLOBAL,TATACONSUM,10-FEB-2020\n"
        "Cadila Healthcare Limited,CADILAHC,ZYDUSLIFE,07-MAR-2022\n"
    )
    count1 = load_renames(session=sqlite_session, csv_content=csv_data)
    assert count1 == 2
    assert len(sqlite_session.scalars(select(SymbolChange)).all()) == 2

    # Rerun
    count2 = load_renames(session=sqlite_session, csv_content=csv_data)
    assert count2 == 2
    assert len(sqlite_session.scalars(select(SymbolChange)).all()) == 2
