"""Unit tests for index membership normalization, tracking, and point-in-time querying."""

from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base
from src.ingestion.index_constituents import (
    get_index_constituents,
    normalize_index_code,
    snapshot_index_constituents,
)


def test_normalize_index_code():
    assert normalize_index_code("NIFTY 100") == "NIFTY_100"
    assert normalize_index_code("nifty midcap 150") == "NIFTY_MIDCAP_150"
    assert normalize_index_code("NIFTY Smallcap 250 ") == "NIFTY_SMALLCAP_250"


def test_snapshot_and_query_index_constituents_in_memory():
    """Test point-in-time index constituent changes using SQLite in-memory."""
    sqlite_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(sqlite_engine)
    testing_session = sessionmaker(bind=sqlite_engine)

    with testing_session() as session:
        # Initial snapshot on Jan 1
        per_index_t1 = {
            "NIFTY 100": {"RELIANCE", "TCS", "INFY"},
        }
        res1 = snapshot_index_constituents(session, per_index_t1, as_of_date=date(2024, 1, 1))
        session.commit()
        assert res1["added"] == 3

        # Query on Jan 15
        active_t1 = get_index_constituents("NIFTY 100", on_date=date(2024, 1, 15), session=session)
        assert set(active_t1) == {"RELIANCE", "TCS", "INFY"}

        # Rebalance on Feb 1: INFY dropped, HDFCBANK added
        per_index_t2 = {
            "NIFTY 100": {"RELIANCE", "TCS", "HDFCBANK"},
        }
        res2 = snapshot_index_constituents(session, per_index_t2, as_of_date=date(2024, 2, 1))
        session.commit()
        assert res2["added"] == 1
        assert res2["retired"] == 1

        # Query on Feb 10: HDFCBANK is in, INFY is out
        active_t2 = get_index_constituents("NIFTY 100", on_date=date(2024, 2, 10), session=session)
        assert set(active_t2) == {"RELIANCE", "TCS", "HDFCBANK"}

        # Point-in-time query for Jan 15: INFY was in, HDFCBANK was not
        active_hist = get_index_constituents(
            "NIFTY 100", on_date=date(2024, 1, 15), session=session
        )
        assert set(active_hist) == {"RELIANCE", "TCS", "INFY"}

    sqlite_engine.dispose()
