"""scripts/drop_funds.py: removes ETFs from the database and the Parquet caches, dry run first."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from config import settings
from scripts.drop_funds import drop_funds_everywhere, fund_symbols
from src.db.models import AdjFactor, Base, CorporateAction, DailyPrice, Security, SymbolChange

DAY = date(2024, 1, 1)


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def price(symbol: str, isin: str) -> DailyPrice:
    return DailyPrice(symbol=symbol, date=DAY, isin=isin, open=1, high=1, low=1, close=1)


@pytest.fixture
def mixed(session):
    """One company (TCS) and one ETF (GOLDBEES) with rows in every table, plus both caches."""
    session.add_all([price("TCS", "INE467B01029"), price("GOLDBEES", "INF204KB14I2")])
    session.add_all([AdjFactor(symbol="TCS", date=DAY, factor=0.5, ratio_kind="x")])
    session.add_all([AdjFactor(symbol="GOLDBEES", date=DAY, factor=0.1, ratio_kind="x")])
    session.add(SymbolChange(old_symbol="OLDETF", new_symbol="GOLDBEES", effective_date=DAY))
    session.add(SymbolChange(old_symbol="OLDTCS", new_symbol="TCS", effective_date=DAY))
    session.add(Security(symbol="GOLDBEES", isin="INF204KB14I2"))
    session.add(Security(symbol="TCS", isin="INE467B01029"))
    session.flush()

    settings.BHAVCOPY_ALL_DIR.mkdir(parents=True, exist_ok=True)
    settings.MCAP_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {"symbol": ["TCS", "GOLDBEES"], "isin": ["INE467B01029", "INF204KB14I2"]}
    ).to_parquet(settings.BHAVCOPY_ALL_DIR / "2024-01-01.parquet", index=False)
    pd.DataFrame(
        {"symbol": ["TCS", "GOLDBEES"], "series": ["EQ", "EQ"], "market_cap": [1, 2]}
    ).to_parquet(settings.MCAP_DIR / "2024-01-01.parquet", index=False)


def count(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def test_fund_symbols_come_from_the_isin(session, mixed) -> None:
    assert fund_symbols(session) == ["GOLDBEES"]


def test_dry_run_counts_and_changes_nothing(session, mixed) -> None:
    result = drop_funds_everywhere(apply=False, session=session)

    assert result["daily_prices"] == 1
    assert result["bhavcopy_rows"] == 1 and result["mcap_rows"] == 1
    assert count(session, DailyPrice) == 2
    assert len(pd.read_parquet(settings.BHAVCOPY_ALL_DIR / "2024-01-01.parquet")) == 2


def test_apply_removes_only_the_fund_everywhere(session, mixed) -> None:
    drop_funds_everywhere(apply=True, session=session)

    assert session.scalars(select(DailyPrice.symbol)).all() == ["TCS"]
    assert session.scalars(select(AdjFactor.symbol)).all() == ["TCS"]
    assert session.scalars(select(Security.symbol)).all() == ["TCS"]
    assert session.scalars(select(SymbolChange.old_symbol)).all() == ["OLDTCS"]
    assert count(session, CorporateAction) == 0
    assert pd.read_parquet(settings.BHAVCOPY_ALL_DIR / "2024-01-01.parquet")["symbol"].tolist() == [
        "TCS"
    ]
    assert pd.read_parquet(settings.MCAP_DIR / "2024-01-01.parquet")["symbol"].tolist() == ["TCS"]
