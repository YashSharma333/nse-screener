"""
Unit tests for scripts/verify_prices.py (Task D7)
=================================================
Tests run strictly offline with in-memory SQLite and tmp_path.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scripts.verify_prices import select_check_dates, verify_prices
from src.db.models import Base, DailyPrice
from tests.test_load_prices import make_day_udiff_zip


@pytest.fixture
def sqlite_session():
    """In-memory SQLite database session with clean tables."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def seed_day(
    session: Session,
    d: date,
    close_infy: str = "1510.00",
    include_reliance: bool = True,
    extra_symbol: str | None = None,
) -> None:
    """Populate sample daily_prices rows for date d."""
    rows = [
        DailyPrice(
            symbol="INFY",
            date=d,
            series="EQ",
            open=Decimal("1500.00"),
            high=Decimal("1520.00"),
            low=Decimal("1490.00"),
            close=Decimal(close_infy),
            prev_close=Decimal("1495.00"),
            volume=1000000,
            isin="INE009A01021",
        )
    ]
    if include_reliance:
        rows.append(
            DailyPrice(
                symbol="RELIANCE",
                date=d,
                series="BE",
                open=Decimal("2500.00"),
                high=Decimal("2550.00"),
                low=Decimal("2490.00"),
                close=Decimal("2540.00"),
                prev_close=Decimal("2510.00"),
                volume=2000000,
                isin="INE002A01018",
            )
        )
    if extra_symbol:
        rows.append(
            DailyPrice(
                symbol=extra_symbol,
                date=d,
                series="EQ",
                open=Decimal("100.00"),
                high=Decimal("105.00"),
                low=Decimal("95.00"),
                close=Decimal("102.00"),
                prev_close=Decimal("100.00"),
                volume=50000,
                isin="INE999A01099",
            )
        )
    session.add_all(rows)
    session.commit()


# ---------------------------------------------------------------------------
# Required test cases
# ---------------------------------------------------------------------------


def test_identical_data_gives_zero_differences(sqlite_session: Session, tmp_path: Path):
    """When DB records match fresh download exactly, 0 differences are reported."""
    d = date(2024, 1, 1)
    seed_day(sqlite_session, d)

    raw_zip = make_day_udiff_zip(d)
    rep_path = tmp_path / "verify.csv"

    diff_count, report_df = verify_prices(
        check_dates=[d],
        session=sqlite_session,
        fetch_fn=lambda _: raw_zip,
        report_path=rep_path,
        pause_seconds=0,
        sleep_fn=lambda _: None,
    )

    assert diff_count == 0
    assert len(report_df) == 1
    assert report_df["status"].iloc[0] == "PASS"
    assert rep_path.exists()


def test_changed_close_is_reported_with_symbol_and_date(
    sqlite_session: Session, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """Changed close price is flagged as a discrepancy with date and symbol logged."""
    d = date(2024, 1, 1)
    # File has close 1510.00, DB has 1599.99
    seed_day(sqlite_session, d, close_infy="1599.99")

    raw_zip = make_day_udiff_zip(d)
    rep_path = tmp_path / "verify.csv"

    diff_count, report_df = verify_prices(
        check_dates=[d],
        session=sqlite_session,
        fetch_fn=lambda _: raw_zip,
        report_path=rep_path,
        pause_seconds=0,
        sleep_fn=lambda _: None,
    )

    assert diff_count >= 1
    assert report_df["status"].iloc[0] == "FAIL"
    assert report_df["changed_values"].iloc[0] == 1
    assert "INFY.close" in report_df["details"].iloc[0]
    assert "INFY.close" in caplog.text


def test_missing_and_extra_rows_reported(sqlite_session: Session, tmp_path: Path):
    """Detects when a symbol is missing in DB or when extra symbol exists in DB."""
    d = date(2024, 1, 1)
    # File has INFY + RELIANCE. DB has INFY + EXTRA_STOCK (missing RELIANCE, extra EXTRA_STOCK)
    seed_day(sqlite_session, d, include_reliance=False, extra_symbol="EXTRA_STOCK")

    raw_zip = make_day_udiff_zip(d)
    rep_path = tmp_path / "verify.csv"

    diff_count, report_df = verify_prices(
        check_dates=[d],
        session=sqlite_session,
        fetch_fn=lambda _: raw_zip,
        report_path=rep_path,
        pause_seconds=0,
        sleep_fn=lambda _: None,
    )

    assert diff_count >= 2
    assert report_df["missing_in_db"].iloc[0] == 1  # RELIANCE missing
    assert report_df["extra_in_db"].iloc[0] == 1  # EXTRA_STOCK extra
    assert report_df["status"].iloc[0] == "FAIL"


def test_exit_code_is_1_on_any_difference():
    """CLI exit code is 1 when verify-prices finds any difference."""
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from scripts.verify_prices import main; import sys; "
            "from unittest.mock import patch; "
            "patch('scripts.verify_prices.verify_prices', return_value=(1, None)).start(); "
            "main()",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1


def test_must_include_dates_always_selected_and_selection_is_seeded():
    """Mandatory dates are always included and calling select_check_dates
    twice returns identical lists.
    """
    # Build a simulated list of 100 trading dates including landmark dates
    all_dates = [
        date(2016, 11, 21),  # landmark
        date(2023, 7, 12),  # landmark
        date(2024, 1, 1),  # landmark
        date(2024, 7, 5),  # landmark
        date(2024, 7, 8),  # landmark
        date(2020, 2, 1),  # Budget Saturday
        date(2023, 11, 12),  # Diwali Sunday
        date(2026, 11, 19),  # latest
    ]
    # Add dummy weekday dates
    from datetime import timedelta

    curr = date(2024, 1, 2)
    while len(all_dates) < 80:
        if curr.weekday() < 5 and curr not in all_dates:
            all_dates.append(curr)
        curr += timedelta(days=1)

    all_dates.sort()

    selection1 = select_check_dates(all_dates, sample_size=20, seed=7)
    selection2 = select_check_dates(all_dates, sample_size=20, seed=7)

    # Must be 100% deterministic
    assert selection1 == selection2

    # Mandatory dates must all be in selection
    assert date(2016, 11, 21) in selection1
    assert date(2023, 7, 12) in selection1
    assert date(2024, 1, 1) in selection1
    assert date(2024, 7, 5) in selection1
    assert date(2024, 7, 8) in selection1
    assert date(2020, 2, 1) in selection1
    assert date(2023, 11, 12) in selection1
    assert max(all_dates) in selection1
