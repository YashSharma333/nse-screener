"""Unit tests for benchmark index ingestion layer (src/ingestion/benchmark.py)."""

from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.db.data_service import get_benchmark_returns
from src.db.models import Base, BenchmarkPrice, DailyPrice
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
    missing_benchmark_dates,
    record_non_trading_day,
)
from src.ingestion.nse_files import BadFile, FetchFailed, NoFile

FIXTURE_PATH = Path("tests/fixtures/ind_close_all_sample.csv")


def serve(monkeypatch, content: str | bytes | Exception) -> list[str]:
    """Make nse_files.fetch answer with `content` (or raise it); returns the URLs asked for."""
    asked: list[str] = []

    def fake_fetch(url, **kwargs):
        asked.append(url)
        if isinstance(content, Exception):
            raise content
        return content.encode("utf-8") if isinstance(content, str) else content

    monkeypatch.setattr("src.ingestion.nse_files.fetch", fake_fetch)
    return asked


def test_nse_index_source_parses_sample_fixture(tmp_path: Path, monkeypatch):
    """NSEIndexSource must parse all 5 required indices from sample CSV and cache them."""
    with FIXTURE_PATH.open() as f:
        csv_text = f.read()

    asked = serve(monkeypatch, csv_text)

    with patch("src.ingestion.benchmark._ensure_cache_dir", return_value=tmp_path):
        source = NSEIndexSource()
        d = date(2024, 10, 25)
        prices = source.fetch_prices(d)

    assert asked == ["https://nsearchives.nseindia.com/content/indices/ind_close_all_25102024.csv"]
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


def test_nse_index_source_handles_404_gracefully(tmp_path: Path, monkeypatch):
    """When NSE archive returns 404 (e.g. holiday), source returns None and caches marker."""
    serve(monkeypatch, NoFile("404"))

    test_file = tmp_path / "non_trading_days.json"
    with patch("config.settings.BENCHMARK_NON_TRADING_DAYS_FILE", test_file):
        source = NSEIndexSource()
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
    with patch("config.settings.BENCHMARK_NON_TRADING_DAYS_FILE", test_file):
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


def test_benchmark_loader_rejects_mismatched_index_date(tmp_path: Path, monkeypatch):
    """Benchmark loader must reject index close file if Index Date does not match requested date."""
    sunday = date(2024, 10, 27)
    # Index file with Friday's date
    csv_text = (
        "Index Name,Index Date,Open Index Value,High Index Value,"
        "Low Index Value,Closing Index Value\n"
        "Nifty 50,25-10-2024,24418.05,24440.25,24073.9,24180.8\n"
        "Nifty 100,25-10-2024,25305.3,25326.6,24897.2,25022.2\n"
    )

    serve(monkeypatch, csv_text)

    test_non_trading = tmp_path / "benchmark_non_trading.json"
    with (
        patch("config.settings.BENCHMARK_CACHE_DIR", tmp_path),
        patch("config.settings.BENCHMARK_NON_TRADING_DAYS_FILE", test_non_trading),
    ):
        src = NSEIndexSource()
        prices = src.fetch_prices(sunday)

        # Mismatch must return None
        assert prices is None
        # Must not write cache file
        assert not (tmp_path / f"{sunday.isoformat()}.parquet").exists()
        # Must record Sunday as non-trading day
        cached_days = load_non_trading_days()
        assert sunday.isoformat() in cached_days


def test_recent_404_is_not_recorded_as_non_trading(tmp_path: Path, monkeypatch):
    """A 404 within the last 3 days may be a publishing delay, so it must stay retryable."""
    serve(monkeypatch, NoFile("404"))
    test_file = tmp_path / "non_trading_days.json"
    with (
        patch("config.settings.BENCHMARK_CACHE_DIR", tmp_path),
        patch("config.settings.BENCHMARK_NON_TRADING_DAYS_FILE", test_file),
    ):
        today = date.today()
        assert NSEIndexSource().fetch_prices(today) is None
        assert today.isoformat() not in load_non_trading_days()


def test_unparseable_file_raises_instead_of_no_data(tmp_path: Path, monkeypatch):
    """A broken file is an error to report, not 'no index data for this day'."""
    serve(monkeypatch, "")
    with (
        patch("config.settings.BENCHMARK_CACHE_DIR", tmp_path),
        patch("config.settings.BENCHMARK_NON_TRADING_DAYS_FILE", tmp_path / "n.json"),
    ):
        with pytest.raises(BadFile):
            NSEIndexSource().fetch_prices(date(2024, 10, 25))
        assert load_non_trading_days() == set()


def test_missing_benchmark_dates_and_refresh_fetch_only_gaps(tmp_path: Path):
    """Prices on D1..D5, benchmarks on D1, D2, D5: only D3 and D4 are fetched."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    days = [date(2024, 3, 4) + timedelta(days=i) for i in range(5)]
    non_trading_file = tmp_path / "n.json"
    with (
        Session(engine) as s,
        patch("config.settings.BENCHMARK_NON_TRADING_DAYS_FILE", non_trading_file),
    ):
        for d in days:
            s.add(DailyPrice(symbol="AAA", series="EQ", date=d, close=10.0))
        for d in (days[0], days[1], days[4]):
            s.add(BenchmarkPrice(index_code=NIFTY_500, date=d, close=1.0, source="NSE"))
        s.commit()

        assert missing_benchmark_dates(s, NIFTY_500) == [days[2], days[3]]
        record_non_trading_day(days[3])
        assert missing_benchmark_dates(s, NIFTY_500) == [days[2]]
        non_trading_file.unlink()

        source = MagicMock()
        source.fetch_prices.return_value = {NIFTY_500: 2.0}
        gaps = missing_benchmark_dates(s, NIFTY_500)
        load_benchmarks(gaps, source=source, delay=0, session=s)
        fetched = [c.args[0] for c in source.fetch_prices.call_args_list]
        assert fetched == [days[2], days[3]]


def _fetch_with_index_date(monkeypatch, tmp_path: Path, file_date_text: str, requested: date):
    """Run fetch_prices against a one-row index file that reports `file_date_text`."""
    csv_text = (
        "Index Name,Index Date,Open Index Value,High Index Value,"
        "Low Index Value,Closing Index Value\n"
        f"Nifty 50,{file_date_text},100,100,100,100\n"
    )
    serve(monkeypatch, csv_text)
    with (
        patch("config.settings.BENCHMARK_CACHE_DIR", tmp_path),
        patch("config.settings.BENCHMARK_NON_TRADING_DAYS_FILE", tmp_path / "n.json"),
    ):
        return NSEIndexSource().fetch_prices(requested)


def test_month_first_index_date_is_accepted(tmp_path: Path, monkeypatch):
    """NSE wrote '04-06-2023' (month first) for 6 April 2023; the file must load."""
    assert _fetch_with_index_date(monkeypatch, tmp_path, "04-06-2023", date(2023, 4, 6)) == {
        NIFTY_50: 100.0
    }


def test_date_matching_in_neither_order_is_still_rejected(tmp_path: Path, monkeypatch):
    """A file for another day must still be rejected: the safety check keeps working."""
    assert _fetch_with_index_date(monkeypatch, tmp_path, "25-10-2024", date(2024, 10, 27)) is None
    assert _fetch_with_index_date(monkeypatch, tmp_path, "04-06-2023", date(2023, 4, 7)) is None


def test_ambiguous_index_date_loads(tmp_path: Path, monkeypatch):
    """'05-05-2023' reads the same in both orders."""
    assert _fetch_with_index_date(monkeypatch, tmp_path, "05-05-2023", date(2023, 5, 5)) == {
        NIFTY_50: 100.0
    }


def test_network_failure_is_not_recorded_as_non_trading(tmp_path: Path, monkeypatch):
    """A failed download (FetchFailed) is not a holiday: nothing is cached, the next run retries."""
    serve(monkeypatch, FetchFailed("down"))
    test_file = tmp_path / "n.json"
    with (
        patch("config.settings.BENCHMARK_CACHE_DIR", tmp_path),
        patch("config.settings.BENCHMARK_NON_TRADING_DAYS_FILE", test_file),
    ):
        assert NSEIndexSource().fetch_prices(date(2024, 10, 25)) is None
        assert load_non_trading_days() == set()
