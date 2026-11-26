"""
Unit tests for src/ingestion/price_loader.py (Task D5)
======================================================
Tests run strictly offline with in-memory SQLite, tmp_path, and injected mock fetch.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.common.trading_calendar import PRICES, load_non_trading_days
from src.db.models import Base, DailyPrice
from src.etl.daily_update import run_daily_update
from src.ingestion.nse_files import BadFile, NoFile
from src.ingestion.price_loader import (
    find_price_gaps,
    load_raw_prices,
)
from tests.test_bhavcopy_parser import make_old_zip, make_udiff_zip
from tests.update_helpers import stub_steps


@pytest.fixture
def sqlite_session():
    """In-memory SQLite database session with clean tables."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def make_day_udiff_zip(d: date) -> bytes:
    """Build valid UDiFF Bhavcopy zip bytes for a given date."""
    records = [
        {
            "TradDt": d.isoformat(),
            "BizDt": d.isoformat(),
            "Sgmt": "CM",
            "Src": "NSE",
            "FinInstrmTp": "STK",
            "FinInstrmId": "1",
            "ISIN": "INE009A01021",
            "TckrSymb": "INFY",
            "SctySrs": "EQ",
            "OpnPric": "1500.00",
            "HghPric": "1520.00",
            "LwPric": "1490.00",
            "ClsPric": "1510.00",
            "PrvsClsgPric": "1495.00",
            "TtlTradgVol": "1000000",
        },
        {
            "TradDt": d.isoformat(),
            "BizDt": d.isoformat(),
            "Sgmt": "CM",
            "Src": "NSE",
            "FinInstrmTp": "STK",
            "FinInstrmId": "2",
            "ISIN": "INE002A01018",
            "TckrSymb": "RELIANCE",
            "SctySrs": "BE",
            "OpnPric": "2500.00",
            "HghPric": "2550.00",
            "LwPric": "2490.00",
            "ClsPric": "2540.00",
            "PrvsClsgPric": "2510.00",
            "TtlTradgVol": "2000000",
        },
    ]
    filename = f"BhavCopy_NSE_CM_0_0_0_{d.strftime('%Y%m%d')}_F_0000.csv"
    return make_udiff_zip(records, filename=filename)


def make_day_old_zip(d: date) -> bytes:
    """Build valid Old Bhavcopy zip bytes for a historical date."""
    # Month abbreviations in uppercase e.g. 21-NOV-2016
    timestamp_str = d.strftime("%d-%b-%Y").upper()
    records = [
        {
            "SYMBOL": "TCS",
            "SERIES": "EQ",
            "OPEN": "2100.00",
            "HIGH": "2150.00",
            "LOW": "2090.00",
            "CLOSE": "2140.00",
            "LAST": "2140.00",
            "PREVCLOSE": "2095.00",
            "TOTTRDQTY": "500000",
            "TOTTRDVAL": "1070000000.00",
            "TIMESTAMP": timestamp_str,
            "TOTALTRADES": "15000",
            "ISIN": "INE467B01029",
        }
    ]
    filename = f"cm{d.strftime('%d%b%Y').upper()}bhav.csv"
    return make_old_zip(records, filename=filename)


# ---------------------------------------------------------------------------
# Required test cases
# ---------------------------------------------------------------------------


def test_loads_two_days_and_rows_match_parsed_frame(sqlite_session: Session, tmp_path: Path):
    """Loads two trading days; inserted database rows match parsed frame."""
    day1 = date(2024, 1, 1)
    day2 = date(2024, 1, 2)
    cache_dir = tmp_path / "bhavcopy_all"
    nt_path = tmp_path / "non_trading_days.json"

    def fake_fetch(url: str) -> bytes:
        if "20240101" in url:
            return make_day_udiff_zip(day1)
        if "20240102" in url:
            return make_day_udiff_zip(day2)
        raise NoFile(url)

    summary = load_raw_prices(
        start=day1,
        end=day2,
        session=sqlite_session,
        cache_dir=cache_dir,
        non_trading_path=nt_path,
        fetch_fn=fake_fetch,
        sleep_fn=lambda _: None,
        pause_seconds=0,
    )

    assert summary.downloaded == 2
    assert summary.rows_inserted == 4  # 2 rows per day

    # Verify rows in SQLite match
    stmt = select(DailyPrice).order_by(DailyPrice.date, DailyPrice.symbol)
    rows = sqlite_session.scalars(stmt).all()
    assert len(rows) == 4
    assert rows[0].date == day1
    assert rows[0].symbol == "INFY"
    assert rows[0].series == "EQ"
    assert float(rows[0].close) == 1510.00
    assert rows[1].date == day1
    assert rows[1].symbol == "RELIANCE"
    assert rows[1].series == "BE"

    assert rows[2].date == day2
    assert rows[2].symbol == "INFY"
    assert rows[3].date == day2
    assert rows[3].symbol == "RELIANCE"

    # Parquet cache files were created
    assert (cache_dir / "2024-01-01.parquet").exists()
    assert (cache_dir / "2024-01-02.parquet").exists()


def test_resume_skips_dates_already_in_db(sqlite_session: Session, tmp_path: Path):
    """Resuming skips dates already stored in daily_prices (fetch call count is 0)."""
    day1 = date(2024, 1, 1)
    day2 = date(2024, 1, 2)
    cache_dir = tmp_path / "bhavcopy_all"
    nt_path = tmp_path / "non_trading_days.json"

    calls: list[str] = []

    def fake_fetch(url: str) -> bytes:
        calls.append(url)
        if "20240101" in url:
            return make_day_udiff_zip(day1)
        if "20240102" in url:
            return make_day_udiff_zip(day2)
        raise NoFile(url)

    # First load only day1
    load_raw_prices(
        start=day1,
        end=day1,
        session=sqlite_session,
        cache_dir=cache_dir,
        non_trading_path=nt_path,
        fetch_fn=fake_fetch,
        sleep_fn=lambda _: None,
        pause_seconds=0,
    )
    assert len(calls) == 1
    calls.clear()

    # Second load: run across both day1 and day2
    summary = load_raw_prices(
        start=day1,
        end=day2,
        session=sqlite_session,
        cache_dir=cache_dir,
        non_trading_path=nt_path,
        fetch_fn=fake_fetch,
        sleep_fn=lambda _: None,
        pause_seconds=0,
    )

    # Day 1 is in DB, so fake_fetch must NOT be called for day 1
    assert len(calls) == 1
    assert "20240102" in calls[0]
    assert summary.rows_inserted == 2  # Only day 2 rows inserted


def test_404_records_non_trading_and_inserts_nothing(sqlite_session: Session, tmp_path: Path):
    """HTTP 404 outside 3-day buffer records date as non-trading and inserts nothing."""
    holiday = date(2024, 1, 26)  # Republic Day holiday
    cache_dir = tmp_path / "bhavcopy_all"
    nt_path = tmp_path / "non_trading_days.json"

    def fake_fetch(url: str) -> bytes:
        raise NoFile(url)

    summary = load_raw_prices(
        start=holiday,
        end=holiday,
        session=sqlite_session,
        cache_dir=cache_dir,
        non_trading_path=nt_path,
        fetch_fn=fake_fetch,
        sleep_fn=lambda _: None,
        pause_seconds=0,
        ref_date=date(2024, 2, 1),  # holiday is > 3 days in the past
    )

    assert summary.no_file == 1
    assert summary.rows_inserted == 0

    # Date must be recorded in non_trading_days.json
    stored_nt = load_non_trading_days(PRICES, nt_path)
    assert holiday in stored_nt


def test_404_in_last_3_days_not_recorded_as_holiday(sqlite_session: Session, tmp_path: Path):
    """HTTP 404 within the last 3 days is NOT permanently recorded as holiday."""
    ref_today = date(2024, 1, 10)
    recent_date = date(2024, 1, 9)  # 1 day ago (within 3-day window)
    cache_dir = tmp_path / "bhavcopy_all"
    nt_path = tmp_path / "non_trading_days.json"

    def fake_fetch(url: str) -> bytes:
        raise NoFile(url)

    summary = load_raw_prices(
        start=recent_date,
        end=recent_date,
        session=sqlite_session,
        cache_dir=cache_dir,
        non_trading_path=nt_path,
        fetch_fn=fake_fetch,
        sleep_fn=lambda _: None,
        pause_seconds=0,
        ref_date=ref_today,
    )

    assert summary.no_file == 1
    assert summary.rows_inserted == 0

    # Date must NOT be recorded as non-trading
    stored_nt = load_non_trading_days(PRICES, nt_path)
    assert recent_date not in stored_nt


def test_bad_file_not_inserted_and_counted(sqlite_session: Session, tmp_path: Path):
    """Corrupt or bad file is logged, counted, and never inserted into database."""
    target_d = date(2024, 1, 5)
    cache_dir = tmp_path / "bhavcopy_all"
    nt_path = tmp_path / "non_trading_days.json"

    def fake_fetch(url: str) -> bytes:
        raise BadFile("HTML error block returned with 200 OK")

    summary = load_raw_prices(
        start=target_d,
        end=target_d,
        session=sqlite_session,
        cache_dir=cache_dir,
        non_trading_path=nt_path,
        fetch_fn=fake_fetch,
        sleep_fn=lambda _: None,
        pause_seconds=0,
    )

    assert summary.bad_file == 1
    assert summary.rows_inserted == 0

    rows = sqlite_session.scalars(select(DailyPrice)).all()
    assert len(rows) == 0


def test_weekend_special_session_is_loaded(sqlite_session: Session, tmp_path: Path):
    """Weekend special trading session (e.g. Sunday Diwali Muhurat trading) is loaded."""
    sunday = date(2024, 1, 7)  # Calendar Sunday
    assert sunday.weekday() == 6  # Sunday

    cache_dir = tmp_path / "bhavcopy_all"
    nt_path = tmp_path / "non_trading_days.json"

    def fake_fetch(url: str) -> bytes:
        return make_day_udiff_zip(sunday)

    summary = load_raw_prices(
        start=sunday,
        end=sunday,
        session=sqlite_session,
        cache_dir=cache_dir,
        non_trading_path=nt_path,
        fetch_fn=fake_fetch,
        sleep_fn=lambda _: None,
        pause_seconds=0,
    )

    assert summary.downloaded == 1
    assert summary.rows_inserted == 2

    # Verify Sunday is in database
    dates_in_db = sqlite_session.scalars(select(DailyPrice.date)).all()
    assert sunday in dates_in_db


def test_rerun_is_idempotent(sqlite_session: Session, tmp_path: Path):
    """Running load_raw_prices twice over the same window leaves row counts identical."""
    day1 = date(2024, 1, 1)
    day2 = date(2024, 1, 2)
    cache_dir = tmp_path / "bhavcopy_all"
    nt_path = tmp_path / "non_trading_days.json"

    def fake_fetch(url: str) -> bytes:
        if "20240101" in url:
            return make_day_udiff_zip(day1)
        if "20240102" in url:
            return make_day_udiff_zip(day2)
        raise NoFile(url)

    # First run
    load_raw_prices(
        start=day1,
        end=day2,
        session=sqlite_session,
        cache_dir=cache_dir,
        non_trading_path=nt_path,
        fetch_fn=fake_fetch,
        sleep_fn=lambda _: None,
        pause_seconds=0,
    )
    count1 = len(sqlite_session.scalars(select(DailyPrice)).all())
    assert count1 == 4

    # Second run
    s2 = load_raw_prices(
        start=day1,
        end=day2,
        session=sqlite_session,
        cache_dir=cache_dir,
        non_trading_path=nt_path,
        fetch_fn=fake_fetch,
        sleep_fn=lambda _: None,
        pause_seconds=0,
    )
    count2 = len(sqlite_session.scalars(select(DailyPrice)).all())
    assert count2 == 4
    assert s2.rows_inserted == 0  # Pre-query skipped already loaded days


def test_dry_run_flag_is_not_accepted():
    """--dry-run no longer exists: the CLI refuses it instead of silently writing."""
    proc = subprocess.run(
        [sys.executable, "run_pipeline.py", "--load-prices", "--dry-run"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "unrecognized arguments: --dry-run" in proc.stderr


# ---------------------------------------------------------------------------
# CL-11: price gap check
# ---------------------------------------------------------------------------


def _add_price_row(session: Session, d: date) -> None:
    session.add(
        DailyPrice(
            symbol="INFY",
            date=d,
            series="EQ",
            open=1.0,
            high=1.0,
            low=1.0,
            close=1.0,
            volume=1,
        )
    )
    session.commit()


def test_find_price_gaps_reports_missing_day(sqlite_session: Session):
    """D1 and D3 stored, D2 missing and not a known holiday: D2 is a gap."""
    _add_price_row(sqlite_session, date(2024, 1, 1))
    _add_price_row(sqlite_session, date(2024, 1, 3))

    assert find_price_gaps(sqlite_session, non_trading_days=set()) == [date(2024, 1, 2)]


def test_find_price_gaps_ignores_confirmed_non_trading_day(sqlite_session: Session):
    """The same hole is not a gap when D2 is recorded as a non-trading day."""
    _add_price_row(sqlite_session, date(2024, 1, 1))
    _add_price_row(sqlite_session, date(2024, 1, 3))

    assert find_price_gaps(sqlite_session, non_trading_days={date(2024, 1, 2)}) == []


def test_find_price_gaps_empty_table(sqlite_session: Session):
    """No stored prices means nothing to compare: no gaps, no crash."""
    assert find_price_gaps(sqlite_session, non_trading_days=set()) == []


def _only_load_prices_step(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every daily-update step except the price load a no-op."""
    stub_steps(monkeypatch, keep=("step_load_prices",))


def test_update_retries_gap_day_and_fills_it(
    sqlite_session: Session, monkeypatch: pytest.MonkeyPatch
):
    """A day missing between two stored days is downloaded again by --update."""
    _only_load_prices_step(monkeypatch)
    _add_price_row(sqlite_session, date(2026, 11, 2))
    _add_price_row(sqlite_session, date(2026, 11, 4))
    gap_day = date(2026, 11, 3)

    def fake_fetch(url: str) -> bytes:
        if "20261103" in url:
            return make_day_udiff_zip(gap_day)
        raise NoFile(url)

    summary = run_daily_update(
        session=sqlite_session,
        fetch_fn=fake_fetch,
        today=date(2026, 11, 5),
    )

    stored = set(sqlite_session.scalars(select(DailyPrice.date)).all())
    assert gap_day in stored
    assert summary.price_gaps == []


def test_update_reports_gap_that_still_fails(
    sqlite_session: Session, monkeypatch: pytest.MonkeyPatch
):
    """A gap day that fails again stays in the summary and in reports/price_gaps.csv."""
    from config import settings

    _only_load_prices_step(monkeypatch)
    _add_price_row(sqlite_session, date(2026, 11, 2))
    _add_price_row(sqlite_session, date(2026, 11, 4))

    def bad_fetch(url: str) -> bytes:
        raise BadFile("html block page")

    summary = run_daily_update(
        session=sqlite_session,
        fetch_fn=bad_fetch,
        today=date(2026, 11, 5),
    )

    assert summary.price_gaps == [date(2026, 11, 3)]
    assert "Price gaps remaining     : 1" in summary.format_text()
    assert "2026-11-03" in (settings.REPORTS_DIR / "price_gaps.csv").read_text()


def test_default_end_date_is_today(sqlite_session: Session, tmp_path: Path) -> None:
    """With no end date, today's file is tried too (like --update); it used to stop at yesterday."""
    asked: list[str] = []

    def not_published_yet(url: str) -> bytes:
        asked.append(url)
        raise NoFile(url)

    load_raw_prices(
        start=date(2026, 11, 19),
        session=sqlite_session,
        cache_dir=tmp_path / "cache",
        non_trading_path=tmp_path / "nt.json",
        fetch_fn=not_published_yet,
        sleep_fn=lambda _: None,
        ref_date=date(2026, 11, 20),
    )

    assert any("20261120" in url for url in asked)
