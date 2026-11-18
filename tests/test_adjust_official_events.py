"""Tests for adjusting prices from OFFICIAL corporate actions (task CA2).

The official ``corporate_actions`` table gives exact factors. ``build_events`` keeps the
ones that fall inside the stored price history, and ``rebuild_adjusted`` turns them into
adjusted prices. Real cases from the CA1 checks: COCHINSHIP 2:1 (price jump said 0.6),
PREMEXPLN 5:1 (price jump missed it), IDEA 2020 (a real crash, no official action).
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.db.models import AdjFactor, Base, DailyPrice
from src.etl.adjust import (
    CorporateActionsMissingError,
    build_events,
    read_official_events,
    rebuild_adjusted,
)
from tests.official_actions import add_official_action, add_placeholder_action, add_prices


def _official(rows: list[tuple[str, date, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"symbol": s, "date": d, "factor": f, "kinds": "SPLIT", "n_parts": 1} for s, d, f in rows]
    )


def _prices(symbol: str, days: list[date]) -> pd.DataFrame:
    return pd.DataFrame({"symbol": symbol, "date": days, "close": 100.0})


def _adjusted_closes(session: Session) -> list[float]:
    rows = session.scalars(select(DailyPrice).order_by(DailyPrice.date)).all()
    return [float(row.adj_close) for row in rows]


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def test_official_event_inside_history_is_kept_with_its_exact_factor():
    prices = _prices("COCHINSHIP", [date(2024, 1, 9), date(2024, 1, 10), date(2024, 1, 11)])
    events = build_events(_official([("COCHINSHIP", date(2024, 1, 10), 0.5)]), prices)
    assert len(events) == 1
    row = events.iloc[0]
    assert (row["factor"], row["source"], row["ratio_kind"]) == (0.5, "official", "material")


def test_future_ex_date_is_not_applied_yet():
    """The table runs into the future; adjusting for an event that has not happened is wrong."""
    prices = _prices("ABC", [date(2024, 1, 9), date(2024, 1, 10)])
    assert build_events(_official([("ABC", date(2024, 2, 1), 0.5)]), prices).empty


def test_event_before_the_first_stored_price_is_skipped():
    prices = _prices("ABC", [date(2024, 1, 9), date(2024, 1, 10)])
    assert build_events(_official([("ABC", date(2024, 1, 9), 0.5)]), prices).empty
    assert build_events(_official([("ABC", date(2023, 5, 1), 0.5)]), prices).empty


def test_event_on_a_day_without_a_price_row_still_applies():
    """A halted stock has no row on the ex-date, but later prices are at the new level."""
    prices = _prices("ABC", [date(2024, 1, 9), date(2024, 1, 12)])
    assert len(build_events(_official([("ABC", date(2024, 1, 10), 0.5)]), prices)) == 1


def test_symbol_without_prices_is_skipped():
    prices = _prices("ABC", [date(2024, 1, 9), date(2024, 1, 10)])
    assert build_events(_official([("OTHER", date(2024, 1, 10), 0.5)]), prices).empty


def test_official_table_empty_stops_with_a_clear_message(session):
    with pytest.raises(CorporateActionsMissingError, match="--download-corporate-actions"):
        read_official_events(session)
    add_prices(session, "ABC", [100.0, 100.0])
    with pytest.raises(CorporateActionsMissingError):
        rebuild_adjusted(session=session)


def test_rebuild_uses_the_official_factor_not_the_price_jump(session):
    """COCHINSHIP: official 2:1 (0.5) while the price only fell to 0.6 of the day before."""
    add_official_action(session, "COCHINSHIP", date(2024, 1, 3), factor=0.5)
    add_prices(session, "COCHINSHIP", [1000.0, 1000.0, 600.0, 610.0])
    session.commit()

    rebuild_adjusted(session=session)
    session.commit()

    assert _adjusted_closes(session) == [500.0, 500.0, 600.0, 610.0]
    assert float(session.scalars(select(AdjFactor)).one().factor) == 0.5


def test_rebuild_applies_a_split_that_prices_alone_would_miss(session):
    """PREMEXPLN: 5:1 split on a day when the price also rose, so no clean ratio shows."""
    add_official_action(session, "PREMEXPLN", date(2024, 1, 3), factor=0.2)
    add_prices(session, "PREMEXPLN", [500.0, 510.0, 130.0, 132.0])
    session.commit()

    rebuild_adjusted(session=session)
    session.commit()

    assert _adjusted_closes(session) == [100.0, 102.0, 130.0, 132.0]


def test_same_day_split_and_bonus_are_multiplied_once(session):
    """AHCL: bonus 1:1 (0.5) and split 5:1 (0.2) on one ex-date make a single factor of 0.1."""
    add_official_action(session, "AHCL", date(2024, 1, 3), factor=0.5, action_type="BONUS")
    add_official_action(session, "AHCL", date(2024, 1, 3), 0.2, action_type="SPLIT", part_no=1)
    add_prices(session, "AHCL", [1000.0, 1000.0, 100.0, 101.0])
    session.commit()

    rebuild_adjusted(session=session)
    session.commit()

    assert float(session.scalars(select(AdjFactor)).one().factor) == pytest.approx(0.1)
    assert _adjusted_closes(session)[0] == pytest.approx(100.0)


def test_a_crash_without_an_official_action_is_not_adjusted(session):
    """IDEA 2020: the price fell by half, but it was a real crash. No event, no adjustment."""
    add_placeholder_action(session)
    add_prices(session, "IDEA", [10.0, 10.0, 5.0, 5.1])
    session.commit()

    rebuild_adjusted(session=session)
    session.commit()

    assert session.scalars(select(AdjFactor)).all() == []
    assert _adjusted_closes(session) == [10.0, 10.0, 5.0, 5.1]


def test_rebuild_does_not_store_future_events(session):
    add_official_action(session, "ABC", date(2030, 1, 1), factor=0.5)
    add_prices(session, "ABC", [100.0, 101.0, 102.0])
    session.commit()

    rebuild_adjusted(session=session)
    session.commit()

    assert session.scalars(select(AdjFactor)).all() == []
    assert _adjusted_closes(session)[0] == 100.0
