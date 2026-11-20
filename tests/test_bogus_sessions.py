"""Tests for the read-only bogus-session check (src/etl/bogus_sessions.py)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.db.models import Base, DailyPrice
from src.etl.bogus_sessions import find_bogus_sessions, report_bogus_sessions


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _add_day(db: Session, day: date, n_symbols: int = 120, shift: float = 0.0) -> None:
    """Add one session; ``shift`` changes every close so the day is NOT a copy."""
    for i in range(n_symbols):
        db.add(
            DailyPrice(
                symbol=f"SYM{i}",
                date=day,
                open=10.0,
                high=11.0,
                low=9.0,
                close=10.0 + i + shift,
                volume=1000 + i,
            )
        )
    db.flush()


def test_copy_of_previous_day_is_flagged(db: Session) -> None:
    _add_day(db, date(2026, 11, 13))
    _add_day(db, date(2026, 11, 14))  # a Saturday served as an exact copy
    flagged = find_bogus_sessions(session=db)
    assert [b["date"] for b in flagged] == [date(2026, 11, 14)]
    assert flagged[0]["weekday"] == "Saturday"


def test_genuine_special_session_is_not_flagged(db: Session) -> None:
    _add_day(db, date(2026, 11, 13))
    _add_day(db, date(2026, 11, 14), shift=0.5)  # different prices: a real session
    assert find_bogus_sessions(session=db) == []


def test_too_few_common_symbols_is_ignored(db: Session) -> None:
    _add_day(db, date(2026, 11, 13), n_symbols=50)
    _add_day(db, date(2026, 11, 14), n_symbols=50)
    assert find_bogus_sessions(session=db) == []


def test_report_writes_csv_and_deletes_nothing(db: Session, tmp_path: Path) -> None:
    _add_day(db, date(2026, 11, 13))
    _add_day(db, date(2026, 11, 14))
    before = db.query(DailyPrice).count()

    result = report_bogus_sessions(reports_dir=tmp_path, session=db)

    assert result["bogus_count"] == 1
    assert result["flagged_dates"] == [date(2026, 11, 14)]
    assert (tmp_path / "bogus_sessions.csv").read_text().count("\n") == 2  # header + 1 row
    assert db.query(DailyPrice).count() == before
