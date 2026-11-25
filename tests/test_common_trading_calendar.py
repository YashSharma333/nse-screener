"""Tests for src/common/trading_calendar.py (CL-21): one non-trading-days cache, date queries."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from config import settings
from src.common.trading_calendar import (
    BENCHMARK,
    PRICES,
    get_trading_dates,
    is_recent_date,
    latest_date,
    load_non_trading_days,
    non_trading_days_path,
    record_missing_file,
    record_non_trading_day,
)
from src.db.models import Base, BenchmarkPrice, DailyPrice


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_paths_come_from_settings_per_source() -> None:
    assert non_trading_days_path(PRICES) == settings.PRICE_NON_TRADING_DAYS_FILE
    assert non_trading_days_path(BENCHMARK) == settings.BENCHMARK_NON_TRADING_DAYS_FILE
    assert non_trading_days_path(PRICES) != non_trading_days_path(BENCHMARK)
    with pytest.raises(ValueError, match="Unknown"):
        non_trading_days_path("other")


def test_missing_file_means_no_days() -> None:
    assert load_non_trading_days(PRICES) == set()


def test_record_then_load_round_trip_is_sorted_json_list() -> None:
    record_non_trading_day(PRICES, date(2024, 1, 26))
    record_non_trading_day(PRICES, date(2024, 1, 1))
    record_non_trading_day(PRICES, date(2024, 1, 26))  # twice: stored once
    assert load_non_trading_days(PRICES) == {date(2024, 1, 1), date(2024, 1, 26)}
    raw = json.loads(non_trading_days_path(PRICES).read_text(encoding="utf-8"))
    assert raw == ["2024-01-01", "2024-01-26"]


def test_sources_are_kept_apart() -> None:
    record_non_trading_day(PRICES, date(2024, 1, 26))
    record_non_trading_day(BENCHMARK, date(2024, 3, 25))
    assert load_non_trading_days(PRICES) == {date(2024, 1, 26)}
    assert load_non_trading_days(BENCHMARK) == {date(2024, 3, 25)}


def test_write_is_atomic_and_leaves_no_temp_file() -> None:
    record_non_trading_day(BENCHMARK, date(2024, 1, 26))
    folder = non_trading_days_path(BENCHMARK).parent
    assert [p.name for p in folder.iterdir()] == ["non_trading_days.json"]


def test_failed_write_keeps_the_old_file_intact(monkeypatch) -> None:
    record_non_trading_day(PRICES, date(2024, 1, 1))
    target = non_trading_days_path(PRICES)

    def broken_replace(self, other):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", broken_replace)
    record_non_trading_day(PRICES, date(2024, 1, 2))  # must not raise
    assert json.loads(target.read_text(encoding="utf-8")) == ["2024-01-01"]
    assert not target.with_suffix(".json.tmp").exists()


def test_corrupt_file_reads_as_empty_not_a_crash(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_non_trading_days(PRICES, bad) == set()


def test_both_old_file_layouts_read_without_losing_an_entry(tmp_path: Path) -> None:
    """The price file and the benchmark file were both plain lists of ISO dates."""
    days = ["2016-11-20", "2016-11-26", "2023-04-07"]
    for name in ("prices.json", "benchmark.json"):
        (tmp_path / name).write_text(json.dumps(days), encoding="utf-8")
    for name, source in (("prices.json", PRICES), ("benchmark.json", BENCHMARK)):
        loaded = load_non_trading_days(source, tmp_path / name)
        assert sorted(d.isoformat() for d in loaded) == days


def test_three_day_rule() -> None:
    today = date(2024, 1, 10)
    assert is_recent_date(date(2024, 1, 9), today)
    assert is_recent_date(today - timedelta(days=3), today)
    assert not is_recent_date(today - timedelta(days=4), today)


def test_record_missing_file_skips_recent_days_only() -> None:
    today = date(2024, 1, 10)
    assert record_missing_file(PRICES, date(2024, 1, 9), today) is False
    assert record_missing_file(PRICES, date(2024, 1, 1), today) is True
    assert load_non_trading_days(PRICES) == {date(2024, 1, 1)}


def _add_prices(session: Session, days: list[date]) -> None:
    for day in days:
        # Two symbols per day: the date must still be listed once.
        session.add(DailyPrice(symbol="AAA", series="EQ", date=day, close=10.0))
        session.add(DailyPrice(symbol="BBB", series="EQ", date=day, close=20.0))
    session.commit()


def test_get_trading_dates_distinct_sorted_and_ranged(db: Session) -> None:
    days = [date(2024, 3, 6), date(2024, 3, 4), date(2024, 3, 5), date(2024, 3, 7)]
    _add_prices(db, days)
    assert get_trading_dates(db) == sorted(days)
    assert get_trading_dates(db, start=date(2024, 3, 5)) == [
        date(2024, 3, 5),
        date(2024, 3, 6),
        date(2024, 3, 7),
    ]
    assert get_trading_dates(db, end=date(2024, 3, 5)) == [date(2024, 3, 4), date(2024, 3, 5)]
    assert get_trading_dates(db, date(2024, 3, 5), date(2024, 3, 6)) == [
        date(2024, 3, 5),
        date(2024, 3, 6),
    ]
    assert get_trading_dates(db, start=date(2030, 1, 1)) == []


def test_latest_date_for_prices_and_benchmark(db: Session) -> None:
    assert latest_date(db) is None
    _add_prices(db, [date(2024, 3, 4), date(2024, 3, 7)])
    db.add(BenchmarkPrice(index_code="NIFTY 500", date=date(2024, 3, 5), close=1.0, source="NSE"))
    db.commit()
    assert latest_date(db) == date(2024, 3, 7)
    assert latest_date(db, BenchmarkPrice) == date(2024, 3, 5)


def test_only_the_calendar_module_names_the_non_trading_files() -> None:
    """One module reads and writes these files (CL-21): nobody else may mention them."""
    root = Path(__file__).resolve().parent.parent
    allowed = {root / "src" / "common" / "trading_calendar.py", root / "config" / "settings.py"}
    offenders = []
    for folder in ("src", "scripts", "ui"):
        for path in (root / folder).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if path not in allowed and (
                "NON_TRADING_DAYS_FILE" in text or "non_trading_days.json" in text
            ):
                offenders.append(str(path.relative_to(root)))
    assert offenders == []
