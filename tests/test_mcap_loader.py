"""
Unit tests for src/ingestion/mcap_loader.py (Task D6)
=====================================================
Tests run strictly offline with in-memory SQLite and tmp_path.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.db.models import Base, DailyPrice
from src.ingestion.mcap_loader import (
    load_market_caps,
    parse_mcap_file,
)
from src.ingestion.nse_files import BadFile, NoFile


@pytest.fixture
def sqlite_session():
    """In-memory SQLite database session with clean tables."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def make_mcap_zip(
    records: list[dict[str, Any]],
    filename: str = "MCAP01022024.csv",
) -> bytes:
    """Create in-memory zip bytes containing a single MCAP CSV file."""
    df = pd.DataFrame(records)
    csv_text = df.to_csv(index=False)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(filename, csv_text)
    return buf.getvalue()


def sample_records(
    trade_date_str: str = "01 FEB 2024",
    mcap_val: str = "12345678901.25",
) -> list[dict[str, Any]]:
    """Return standard sample records mimicking official NSE PR bundle mcap CSV."""
    return [
        {
            "Trade Date": trade_date_str,
            "Symbol": "INFY",
            "Series": "EQ",
            "Security Name": "INFOSYS LIMITED",
            "Category": "Listed",
            "Last Trade Date": trade_date_str,
            "Face Value(Rs.)": "5.00",
            "Issue Size": "4150000000",
            "Close Price/Paid up value(Rs.)": "1510.00",
            "Market Cap(Rs.)": mcap_val,
        },
        {
            "Trade Date": trade_date_str,
            "Symbol": "RELIANCE",
            "Series": "EQ",
            "Security Name": "RELIANCE IND LTD",
            "Category": "Listed",
            "Last Trade Date": trade_date_str,
            "Face Value(Rs.)": "10.00",
            "Issue Size": "6760000000",
            "Close Price/Paid up value(Rs.)": "2540.00",
            "Market Cap(Rs.)": "98765432100.50",
        },
    ]


# ---------------------------------------------------------------------------
# Required test cases
# ---------------------------------------------------------------------------


def test_member_name_match_is_case_insensitive():
    """Matches mcap member file case-insensitively (MCAP01022024.csv vs mcap01022024.csv)."""
    d = date(2024, 2, 1)

    # Test uppercase member name
    raw_upper = make_mcap_zip(sample_records(), filename="MCAP01022024.csv")
    df_upper = parse_mcap_file(raw_upper, d)
    assert len(df_upper) == 2
    assert "INFY" in df_upper["symbol"].values

    # Test lowercase member name
    raw_lower = make_mcap_zip(sample_records(), filename="mcap01022024.csv")
    df_lower = parse_mcap_file(raw_lower, d)
    assert len(df_lower) == 2
    assert "INFY" in df_lower["symbol"].values


def test_market_cap_stored_as_published(sqlite_session: Session, tmp_path: Path):
    """Value 12345678901.25 round-trips without float truncation or rounding."""
    d = date(2024, 2, 1)

    # Pre-populate DailyPrice
    row = DailyPrice(
        symbol="INFY",
        date=d,
        series="EQ",
        close=Decimal("1510.00"),
        market_cap=None,
    )
    sqlite_session.add(row)
    sqlite_session.commit()

    raw_zip = make_mcap_zip(sample_records(mcap_val="12345678901.25"))

    load_market_caps(
        start=d,
        end=d,
        session=sqlite_session,
        cache_dir=tmp_path / "mcap",
        fetch_fn=lambda _: raw_zip,
        sleep_fn=lambda _: None,
        pause_seconds=0,
    )

    db_row = sqlite_session.scalar(select(DailyPrice).where(DailyPrice.symbol == "INFY"))
    assert db_row is not None
    assert Decimal(str(db_row.market_cap)) == Decimal("12345678901.25")

    # Parquet cache also preserves Decimal
    parquet_file = tmp_path / "mcap" / "2024-02-01.parquet"
    assert parquet_file.exists()
    cached_df = pd.read_parquet(parquet_file)
    assert cached_df["market_cap"].iloc[0] == Decimal("12345678901.25")


def test_dates_before_2024_02_01_are_skipped_and_stay_null(sqlite_session: Session, tmp_path: Path):
    """Dates before 2024-02-01 are not queried and keep market_cap as NULL."""
    old_date = date(2024, 1, 15)

    row = DailyPrice(
        symbol="INFY",
        date=old_date,
        series="EQ",
        close=Decimal("1500.00"),
        market_cap=None,
    )
    sqlite_session.add(row)
    sqlite_session.commit()

    fetch_called = False

    def fake_fetch(_url: str) -> bytes:
        nonlocal fetch_called
        fetch_called = True
        return b""

    summary = load_market_caps(
        start=old_date,
        end=old_date,
        session=sqlite_session,
        cache_dir=tmp_path / "mcap",
        fetch_fn=fake_fetch,
        sleep_fn=lambda _: None,
        pause_seconds=0,
    )

    # Clamping start to FIRST_MCAP_DATE means old_date > end_date -> skipped
    assert fetch_called is False
    assert summary.updated_rows == 0

    db_row = sqlite_session.scalar(select(DailyPrice).where(DailyPrice.symbol == "INFY"))
    assert db_row is not None
    assert db_row.market_cap is None


def test_missing_bundle_leaves_null(sqlite_session: Session, tmp_path: Path):
    """Missing PR bundle (NoFile) leaves market_cap as NULL and counts missing_bundle."""
    d = date(2024, 2, 5)

    row = DailyPrice(
        symbol="INFY",
        date=d,
        series="EQ",
        close=Decimal("1500.00"),
        market_cap=None,
    )
    sqlite_session.add(row)
    sqlite_session.commit()

    def fake_fetch(url: str) -> bytes:
        raise NoFile(url)

    summary = load_market_caps(
        start=d,
        end=d,
        session=sqlite_session,
        cache_dir=tmp_path / "mcap",
        fetch_fn=fake_fetch,
        sleep_fn=lambda _: None,
        pause_seconds=0,
    )

    assert summary.missing_bundle == 1
    assert summary.updated_rows == 0

    db_row = sqlite_session.scalar(select(DailyPrice).where(DailyPrice.symbol == "INFY"))
    assert db_row is not None
    assert db_row.market_cap is None


def test_wrong_date_in_mcap_file_rejected():
    """Wrong Trade Date inside mcap file raises BadFile."""
    expected_d = date(2024, 2, 1)
    # File contains 02 FEB 2024 instead of 01 FEB 2024
    raw_zip = make_mcap_zip(sample_records(trade_date_str="02 FEB 2024"))

    with pytest.raises(BadFile, match="does not match expected"):
        parse_mcap_file(raw_zip, expected_d)


def test_unmatched_symbol_is_counted_not_inserted(sqlite_session: Session, tmp_path: Path):
    """Symbols in mcap file not present in daily_prices are counted and NOT inserted."""
    d = date(2024, 2, 1)

    # DB only has INFY, not RELIANCE
    row = DailyPrice(
        symbol="INFY",
        date=d,
        series="EQ",
        close=Decimal("1510.00"),
        market_cap=None,
    )
    sqlite_session.add(row)
    sqlite_session.commit()

    raw_zip = make_mcap_zip(sample_records())

    summary = load_market_caps(
        start=d,
        end=d,
        session=sqlite_session,
        cache_dir=tmp_path / "mcap",
        fetch_fn=lambda _: raw_zip,
        sleep_fn=lambda _: None,
        pause_seconds=0,
    )

    assert summary.updated_rows == 1  # INFY updated
    assert summary.unmatched_symbols == 1  # RELIANCE not in daily_prices

    # Ensure RELIANCE was not inserted as a new row
    all_rows = sqlite_session.scalars(select(DailyPrice)).all()
    assert len(all_rows) == 1
    assert all_rows[0].symbol == "INFY"


def test_rerun_overwrites_with_same_value(sqlite_session: Session, tmp_path: Path):
    """Running load_market_caps twice produces identical idempotent results."""
    d = date(2024, 2, 1)

    row = DailyPrice(
        symbol="INFY",
        date=d,
        series="EQ",
        close=Decimal("1510.00"),
        market_cap=None,
    )
    sqlite_session.add(row)
    sqlite_session.commit()

    raw_zip = make_mcap_zip(sample_records(mcap_val="5000000.00"))

    # First run
    load_market_caps(
        start=d,
        end=d,
        session=sqlite_session,
        cache_dir=tmp_path / "mcap",
        fetch_fn=lambda _: raw_zip,
        sleep_fn=lambda _: None,
        pause_seconds=0,
    )
    row_after1 = sqlite_session.scalar(select(DailyPrice).where(DailyPrice.symbol == "INFY"))
    assert Decimal(str(row_after1.market_cap)) == Decimal("5000000.00")

    # Second run
    load_market_caps(
        start=d,
        end=d,
        session=sqlite_session,
        cache_dir=tmp_path / "mcap",
        fetch_fn=lambda _: raw_zip,
        sleep_fn=lambda _: None,
        pause_seconds=0,
    )
    row_after2 = sqlite_session.scalar(select(DailyPrice).where(DailyPrice.symbol == "INFY"))
    assert Decimal(str(row_after2.market_cap)) == Decimal("5000000.00")


def test_default_end_date_is_today(sqlite_session: Session, tmp_path: Path):
    """No end date means today: today's bundle is tried too, like --update (was yesterday)."""
    today = date.today()
    sqlite_session.add(
        DailyPrice(
            symbol="INFY", date=today, series="EQ", close=Decimal("1500.00"), market_cap=None
        )
    )
    sqlite_session.commit()
    asked: list[str] = []

    def not_published_yet(url: str) -> bytes:
        asked.append(url)
        raise NoFile(url)

    summary = load_market_caps(
        start=today,
        session=sqlite_session,
        cache_dir=tmp_path / "mcap",
        fetch_fn=not_published_yet,
        sleep_fn=lambda _: None,
        pause_seconds=0,
    )

    assert asked
    assert summary.missing_bundle == 1
