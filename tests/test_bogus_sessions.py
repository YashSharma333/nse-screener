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


def test_only_dates_limits_the_check_to_those_dates(db: Session) -> None:
    """An old copy is ignored when only a newer date is asked for."""
    _add_day(db, date(2026, 11, 13))
    _add_day(db, date(2026, 11, 14))  # old copy of the 13th
    _add_day(db, date(2026, 11, 16), shift=0.5)  # a normal new day

    assert find_bogus_sessions(session=db, only_dates=[date(2026, 11, 16)]) == []
    flagged = find_bogus_sessions(session=db, only_dates=[date(2026, 11, 14)])
    assert [b["date"] for b in flagged] == [date(2026, 11, 14)]


def test_incremental_report_appends_and_keeps_old_findings(db: Session, tmp_path: Path) -> None:
    """Each run adds its new findings under the old ones; the header is written once."""
    _add_day(db, date(2026, 11, 13))
    _add_day(db, date(2026, 11, 14))  # copy
    _add_day(db, date(2026, 11, 16), shift=1.0)
    _add_day(db, date(2026, 11, 17), shift=1.0)  # copy of the 16th

    report_bogus_sessions(reports_dir=tmp_path, session=db, only_dates=[date(2026, 11, 14)])
    report_bogus_sessions(reports_dir=tmp_path, session=db, only_dates=[date(2026, 11, 17)])

    lines = (tmp_path / "bogus_sessions.csv").read_text().splitlines()
    assert lines[0].startswith("date,")
    assert [line.split(",")[0] for line in lines[1:]] == ["2026-11-14", "2026-11-17"]


def test_incremental_report_leaves_file_alone_when_nothing_is_flagged(
    db: Session, tmp_path: Path
) -> None:
    """No new dates, or clean new dates: an existing report is not touched or created."""
    _add_day(db, date(2026, 11, 13))
    _add_day(db, date(2026, 11, 14))  # an old copy

    result = report_bogus_sessions(reports_dir=tmp_path, session=db, only_dates=[])

    assert result["bogus_count"] == 0
    assert not (tmp_path / "bogus_sessions.csv").exists()
