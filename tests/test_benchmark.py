"""Unit tests for benchmark index ingestion layer (src/ingestion/benchmark.py)."""

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.db.data_service import get_benchmark_returns
from src.db.models import Base, BenchmarkPrice
from src.ingestion.benchmark import (
    NIFTY_50,
    NIFTY_100,
    NIFTY_500,
    NIFTY_MIDCAP_150,
    NIFTY_SMALLCAP_250,
    NSEIndexSource,
    canonical_index_code,
    load_benchmarks,
    load_non_trading_days,
    record_non_trading_day,
)

FIXTURE_PATH = Path("tests/fixtures/ind_close_all_sample.csv")


def test_nse_index_source_parses_sample_fixture(tmp_path: Path):
    """NSEIndexSource must parse all 5 required indices from sample CSV and cache them."""
    with FIXTURE_PATH.open() as f:
        csv_text = f.read()

    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = csv_text
    mock_session.get.return_value = mock_resp

    with patch("src.ingestion.benchmark._ensure_cache_dir", return_value=tmp_path):
        source = NSEIndexSource(session=mock_session)
        d = date(2024, 10, 25)
        prices = source.fetch_prices(d)

    assert prices is not None
    assert "NIFTY 50" in prices
    assert prices["NIFTY 50"] == 24180.8
    assert "NIFTY 100" in prices
    assert prices["NIFTY 100"] == 25022.2
    assert "NIFTY MIDCAP 150" in prices
    assert prices["NIFTY MIDCAP 150"] == 20465.5
    assert "NIFTY SMALLCAP 250" in prices
    assert prices["NIFTY SMALLCAP 250"] == 17250.0
    assert "NIFTY 500" in prices
    assert prices["NIFTY 500"] == 22499.05
    assert (tmp_path / "2024-10-25.parquet").exists()


def test_nse_index_source_handles_404_gracefully(tmp_path: Path):
    """When NSE archive returns 404 (e.g. holiday), source returns None and caches marker."""
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_session.get.return_value = mock_resp

    test_file = tmp_path / "non_trading_days.json"
    with patch("src.ingestion.benchmark.NON_TRADING_DAYS_FILE", test_file):
        source = NSEIndexSource(session=mock_session)
        d = date(2024, 10, 26)
        prices = source.fetch_prices(d)
        assert prices is None
        assert d.isoformat() in load_non_trading_days()


def test_seed_b_benchmark_code_mismatch():
    """Seed B: Benchmark rows stored as 'NIFTY 500' but queried as 'NIFTY_500'.

    Expected: returns the benchmark rows, normalized to stored form.
    """
    sqlite_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(sqlite_engine)

    with Session(sqlite_engine) as session:
        session.add(
            BenchmarkPrice(
                index_code="NIFTY 500",
                date=date(2024, 1, 1),
                close=22000.0,
                source="NSE",
            )
        )
        session.commit()

    with (
        patch("src.db.data_service.engine", sqlite_engine),
        patch("src.db.data_service.check_connection", return_value=True),
    ):
        df = get_benchmark_returns("NIFTY_500")

    assert not df.empty
    assert len(df) == 1
    assert df.iloc[0]["close"] == 22000.0


def test_canonical_index_code_normalizations():
    """Verify various casing and underscore formats resolve to canonical names."""
    assert canonical_index_code("NIFTY_500") == NIFTY_500
    assert canonical_index_code("nifty 500") == NIFTY_500
    assert canonical_index_code("nifty_100") == NIFTY_100
    assert canonical_index_code("NIFTY 100") == NIFTY_100
    assert canonical_index_code("nifty_midcap_150") == NIFTY_MIDCAP_150
    assert canonical_index_code("NIFTY SMALLCAP 250") == NIFTY_SMALLCAP_250
    assert canonical_index_code("nifty 50") == NIFTY_50


def test_load_benchmarks_sqlite():
    """Test load_benchmarks ingesting data directly into SQLite with upsert_rows."""
    sqlite_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(sqlite_engine)

    d1 = date(2024, 1, 1)
    d2 = date(2024, 1, 2)

    mock_source = MagicMock()
    mock_source.fetch_prices.side_effect = lambda d: (
        {NIFTY_500: 22000.0, NIFTY_50: 24000.0}
        if d == d1
        else {NIFTY_500: 22100.0, NIFTY_50: 24100.0}
    )

    with Session(sqlite_engine) as session:
        total = load_benchmarks([d1, d2], source=mock_source, delay=0.0, session=session)
        session.commit()
        assert total == 4

        stored = session.scalars(select(BenchmarkPrice).order_by(BenchmarkPrice.date)).all()
        assert len(stored) == 4
        assert stored[0].index_code in (NIFTY_500, NIFTY_50)


def test_record_and_load_non_trading_day(tmp_path: Path):
    """Test JSON serialization and loading of non-trading days."""
    test_file = tmp_path / "non_trading_days.json"
    with patch("src.ingestion.benchmark.NON_TRADING_DAYS_FILE", test_file):
        d = date(2024, 1, 26)
        record_non_trading_day(d)
        res = load_non_trading_days()
        assert "2024-01-26" in res


def test_nse_index_source_cache_hit(tmp_path: Path):
    """Test reading benchmark prices from cached Parquet file."""
    import pandas as pd

    d = date(2024, 1, 15)
    cache_file = tmp_path / f"{d.isoformat()}.parquet"
    cache_df = pd.DataFrame([{"index_code": NIFTY_500, "close": 21500.0}])
    cache_df.to_parquet(cache_file, index=False)

    with patch("src.ingestion.benchmark._ensure_cache_dir", return_value=tmp_path):
        source = NSEIndexSource()
        prices = source.fetch_prices(d)
        assert prices is not None
        assert prices[NIFTY_500] == 21500.0
