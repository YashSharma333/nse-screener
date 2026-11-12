"""
Tests for weekend and holiday special trading sessions ingestion (TASK T64).

Validates:
- candidate-days helper includes Saturdays and Sundays.
- Fake downloader: data only for one Sunday, 404 for all other days -> Sunday rows stored;
  other weekend days recorded as non-trading; second run makes ZERO requests for cached dates.
- Network error on a weekend date is NOT cached as non-trading.
- --recheck-non-trading clears and re-probes the requested range only.
- Fri -> Sun -> Mon price sequence: no false split/bonus event, adjusted prices unchanged,
  Sunday treated as a normal bar by indicator calculation (using real Arrow date32 type).
- Benchmark: weekend index file stored when it exists, skipped and cached when it does not.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from run_pipeline import main as run_pipeline_main
from src.computation.indicators.calculator import calculate_sma
from src.db.models import Base, BenchmarkPrice, DailyPrice
from src.etl.adjust import cumulative_factors, detect_events
from src.etl.pipeline import BhavcopyETL
from src.ingestion.benchmark import (
    NSEIndexSource,
)
from src.ingestion.benchmark import (
    load_non_trading_days as load_benchmark_non_trading_days,
)


@pytest.fixture
def sqlite_db(tmp_path: Path):
    """File-backed SQLite database for real schema tests without MySQL."""
    db_file = tmp_path / "test_weekend.db"
    engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    return engine, session_factory


def _make_bhavcopy_df(
    d: date, symbol: str = "TCS", close: float = 3500.0, prev_close: float = 3480.0
) -> pd.DataFrame:
    """Construct a single-row DataFrame simulating a parsed Bhavcopy with real date32 type."""
    df = pd.DataFrame(
        [
            {
                "Symbol": symbol,
                "Open": close - 10.0,
                "High": close + 20.0,
                "Low": close - 20.0,
                "Close": close,
                "PrevClose": prev_close,
                "Volume": 100_000,
            }
        ]
    )
    return df


def test_candidate_days_helper_includes_saturdays_and_sundays():
    """Verify that _calendar_days yields all days in [start, end], including Sat and Sun."""
    start = date(2024, 11, 8)  # Friday
    end = date(2024, 11, 11)  # Monday

    candidate_days = list(BhavcopyETL._calendar_days(start, end))

    assert len(candidate_days) == 4
    assert date(2024, 11, 8) in candidate_days  # Friday
    assert date(2024, 11, 9) in candidate_days  # Saturday
    assert date(2024, 11, 10) in candidate_days  # Sunday
    assert date(2024, 11, 11) in candidate_days  # Monday

    # Verify backward-compatibility alias _weekdays also yields all calendar days
    alias_days = list(BhavcopyETL._weekdays(start, end))
    assert alias_days == candidate_days


def test_fake_downloader_sunday_stored_and_second_run_zero_requests(sqlite_db, tmp_path: Path):
    """Data only for one Sunday, 404 for all other days.

    Sunday rows must be stored, other weekend days cached as non-trading,
    and a second run must make ZERO requests for cached dates.
    """
    _engine, session_factory = sqlite_db
    cache_dir = tmp_path / "bhavcopy_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    non_trading_file = cache_dir / "non_trading_days.json"

    etl = BhavcopyETL(cache_dir=cache_dir)
    mock_downloader = MagicMock()

    # Special weekend session (Diwali Muhurat 2024 was Friday 1 Nov 2024)
    sunday_date = date(2024, 11, 3)
    end_date = date(2024, 11, 4)  # Monday

    def fake_download(d: date):
        if d == sunday_date:
            return _make_bhavcopy_df(d, symbol="INFY", close=1850.0, prev_close=1840.0)
        return None  # 404 for Fri, Sat, Mon

    mock_downloader.download_bhavcopy.side_effect = fake_download
    etl._downloader = mock_downloader

    @contextmanager
    def _get_test_db():
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    with (
        patch("src.etl.pipeline.get_db", _get_test_db),
        patch("src.etl.pipeline.rebuild_adjusted"),
        patch("src.etl.pipeline.load_benchmarks"),
        patch("src.db.data_service.load_indicator_snapshot"),
    ):
        # Run 1: backfill range [2024-11-01, 2024-11-04] (4 days back from 2024-11-04 is 2024-10-31)
        etl.run_backfill(
            days_back=3,
            chunk_days=10,
            as_of_date=end_date,
            skip_constituent_filter=True,
        )

        # Verify Sunday rows were persisted in the DB
        with session_factory() as session:
            rows = session.query(DailyPrice).all()
            assert len(rows) == 1
            assert rows[0].symbol == "INFY"
            assert rows[0].date == sunday_date
            assert rows[0].close == 1850.0

        # Verify non_trading_days.json recorded the 404 dates, but NOT Sunday
        assert non_trading_file.exists()
        cached_non_trading = set(json.loads(non_trading_file.read_text(encoding="utf-8")))
        assert sunday_date.isoformat() not in cached_non_trading
        assert date(2024, 11, 1).isoformat() in cached_non_trading  # Friday 404
        assert date(2024, 11, 2).isoformat() in cached_non_trading  # Saturday 404
        assert date(2024, 11, 4).isoformat() in cached_non_trading  # Monday 404

        # Run 2: second run over the exact same range
        mock_downloader.download_bhavcopy.reset_mock()

        success, failed = etl.run_backfill(
            days_back=3,
            chunk_days=10,
            as_of_date=end_date,
            skip_constituent_filter=True,
        )

        # ZERO requests should have been made for cached dates!
        assert mock_downloader.download_bhavcopy.call_count == 0
        assert success == []
        assert failed == []


def test_network_error_on_weekend_not_cached_as_non_trading(tmp_path: Path):
    """A network error on a weekend date must NOT be recorded in non_trading_days.json."""
    cache_dir = tmp_path / "bhavcopy_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    non_trading_file = cache_dir / "non_trading_days.json"

    etl = BhavcopyETL(cache_dir=cache_dir)
    mock_downloader = MagicMock()

    sunday_date = date(2024, 11, 3)

    def mock_download(d: date):
        if d == sunday_date:
            raise ConnectionError("NSE network timeout / connection dropped")
        return None  # 404 for other days

    mock_downloader.download_bhavcopy.side_effect = mock_download
    etl._downloader = mock_downloader

    # Collect rows for Saturday and Sunday
    etl.collect_new_rows(date(2024, 11, 2), sunday_date)

    # Saturday (404) should be recorded
    assert non_trading_file.exists()
    cached = set(json.loads(non_trading_file.read_text(encoding="utf-8")))
    assert date(2024, 11, 2).isoformat() in cached

    # Sunday (network error) must NOT be cached as non-trading!
    assert sunday_date.isoformat() not in cached
    assert sunday_date not in etl.get_non_trading_days()


def test_recheck_non_trading_clears_and_reprobes_requested_range_only(sqlite_db, tmp_path: Path):
    """--recheck-non-trading clears and re-probes only the requested date range."""
    _engine, session_factory = sqlite_db
    cache_dir = tmp_path / "bhavcopy_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    non_trading_file = cache_dir / "non_trading_days.json"

    # Pre-seed non_trading_days.json:
    # 2024-11-01 (Fri, before range)
    # 2024-11-03 (Sun, inside range - mistakenly cached as non-trading earlier)
    # 2024-11-09 (Sat, inside range - genuine non-trading holiday)
    # 2024-11-17 (Sun, after range)
    initial_dates = ["2024-11-01", "2024-11-03", "2024-11-09", "2024-11-17"]
    non_trading_file.write_text(json.dumps(initial_dates), encoding="utf-8")

    etl = BhavcopyETL(cache_dir=cache_dir)
    mock_downloader = MagicMock()

    def mock_download(d: date):
        # Only dates inside recheck range [2024-11-02, 2024-11-10] should be probed
        assert date(2024, 11, 2) <= d <= date(2024, 11, 10), (
            f"Probed date {d} outside requested range!"
        )
        if d == date(2024, 11, 3):
            # Sunday special session has real data!
            return _make_bhavcopy_df(d, symbol="RELIANCE", close=2600.0, prev_close=2580.0)
        return None  # 404 for other dates

    mock_downloader.download_bhavcopy.side_effect = mock_download
    etl._downloader = mock_downloader

    @contextmanager
    def _get_test_db():
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    with (
        patch("src.etl.pipeline.get_db", _get_test_db),
        patch("src.etl.pipeline.rebuild_adjusted") as mock_rebuild,
    ):
        success, failed = etl.recheck_non_trading(
            start=date(2024, 11, 2),
            end=date(2024, 11, 10),
            skip_constituent_filter=True,
        )

        assert "RELIANCE" in success
        assert failed == []
        mock_rebuild.assert_called_once_with(symbols=["RELIANCE"])

        # Check DB has the Sunday row
        with session_factory() as session:
            rows = session.query(DailyPrice).all()
            assert len(rows) == 1
            assert rows[0].symbol == "RELIANCE"
            assert rows[0].date == date(2024, 11, 3)
            assert rows[0].close == 2600.0

        # Check updated non_trading_days.json:
        # - Outside dates (2024-11-01, 2024-11-17) preserved untouched
        # - 2024-11-03 cleared and NOT in non-trading
        # - 2024-11-09 re-recorded as non-trading (404)
        updated_cache = set(json.loads(non_trading_file.read_text(encoding="utf-8")))
        assert "2024-11-01" in updated_cache
        assert "2024-11-17" in updated_cache
        assert "2024-11-03" not in updated_cache
        assert "2024-11-09" in updated_cache


def test_cli_recheck_non_trading_flag(sqlite_db, tmp_path: Path):
    """Test CLI runner with --recheck-non-trading, --start, and --end flags."""
    _engine, _session_factory = sqlite_db
    cache_dir = tmp_path / "bhavcopy_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    non_trading_file = cache_dir / "non_trading_days.json"
    non_trading_file.write_text(json.dumps(["2024-11-03"]), encoding="utf-8")

    test_args = [
        "run_pipeline.py",
        "--recheck-non-trading",
        "--start",
        "2024-11-02",
        "--end",
        "2024-11-04",
        "--skip-constituents",
    ]

    with (
        patch("sys.argv", test_args),
        patch("src.etl.pipeline.BhavcopyETL.recheck_non_trading") as mock_recheck,
    ):
        mock_recheck.return_value = (["INFY"], [])
        run_pipeline_main()

        mock_recheck.assert_called_once_with(
            start=date(2024, 11, 2),
            end=date(2024, 11, 4),
            skip_constituent_filter=True,
        )


def test_fri_sun_mon_price_sequence_no_false_split_and_normal_bar_indicators():
    """Fri -> Sun -> Mon sequence:

    - No false split/bonus event detected.
    - Adjusted prices unchanged (adj_factor = 1.0).
    - Sunday is treated as a normal bar by technical indicator calculation.
    - Uses real PyArrow date32 column type.
    """
    fri_date = date(2024, 11, 1)  # Diwali Muhurat 2024 was Friday 1 Nov 2024
    # Special weekend trading session (e.g. disaster recovery / live session)
    sun_date = date(2024, 11, 3)
    mon_date = date(2024, 11, 4)

    raw_data = [
        {
            "symbol": "TCS",
            "date": fri_date,
            "open": 3950.0,
            "high": 4010.0,
            "low": 3940.0,
            "close": 4000.0,
            "prev_close": 3960.0,
            "volume": 1_000_000,
        },
        {
            "symbol": "TCS",
            "date": sun_date,
            "open": 4005.0,
            "high": 4030.0,
            "low": 4000.0,
            "close": 4020.0,
            "prev_close": 4000.0,
            "volume": 200_000,
        },
        {
            "symbol": "TCS",
            "date": mon_date,
            "open": 4025.0,
            "high": 4060.0,
            "low": 4015.0,
            "close": 4050.0,
            "prev_close": 4020.0,
            "volume": 1_200_000,
        },
    ]

    # Convert to DataFrame and verify Arrow date32 type
    df = pd.DataFrame(raw_data)
    arrow_table = pa.Table.from_pandas(df)
    date_field = arrow_table.schema.field("date")
    assert pa.types.is_date32(date_field.type), f"Expected date32, got {date_field.type}"

    # 1. Corporate action event detection: Fri -> Sun -> Mon
    events = detect_events(df, global_trading_days=[fri_date, sun_date, mon_date])
    assert events.empty, f"Unexpected corporate action events detected: {events}"

    # Also test fallback path without global_trading_days (uses np.busday_count)
    events_fallback = detect_events(df, global_trading_days=None)
    assert events_fallback.empty, (
        f"Unexpected corporate action events in fallback: {events_fallback}"
    )

    # 2. Adjust series: prices must remain unchanged
    factors = cumulative_factors(df["date"], events)
    assert (factors == 1.0).all()
    df["adj_factor"] = factors
    df["adj_close"] = df["close"] * factors
    assert (df["adj_close"] == df["close"]).all()

    # 3. Indicator code: Sunday must be treated as a normal bar
    # 2-bar SMA on Sunday should average [Fri Close (4000), Sun Close (4020)] -> 4010.0
    sma2 = calculate_sma(df, window=2, column="adj_close")
    assert np.isnan(sma2.iloc[0])  # Friday has no prior bar
    assert sma2.iloc[1] == 4010.0  # Sunday bar included
    assert sma2.iloc[2] == 4035.0  # Monday bar: (4020 + 4050) / 2


def test_benchmark_weekend_index_stored_when_exists_skipped_when_not(sqlite_db, tmp_path: Path):
    """Benchmark: weekend index file stored when it exists, skipped/cached when it does not."""
    _engine, session_factory = sqlite_db
    benchmark_cache_dir = tmp_path / "benchmark_cache"
    benchmark_cache_dir.mkdir(parents=True, exist_ok=True)
    benchmark_non_trading_file = benchmark_cache_dir / "non_trading_days.json"

    sat_date = date(2024, 11, 2)
    sun_date = date(2024, 11, 3)

    sample_csv = (
        "Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,"
        "Closing Index Value,Points Change,Change(%),Volume,Turnover (Rs. Cr.),P/E,P/B,Div Yield\n"
        "Nifty 50,03-11-2024,24000.0,24100.0,23950.0,24050.0,50.0,0.21,1000,100,20,3,1.2\n"
        "Nifty 500,03-11-2024,22000.0,22150.0,21980.0,22100.0,100.0,0.45,5000,500,22,3.5,1.0\n"
    )

    mock_session = MagicMock()

    def mock_get(url: str, **_kwargs):
        resp = MagicMock()
        if sat_date.strftime("%d%m%Y") in url:
            resp.status_code = 404
            resp.text = "Not Found"
        elif sun_date.strftime("%d%m%Y") in url:
            resp.status_code = 200
            resp.text = sample_csv
        else:
            resp.status_code = 404
            resp.text = "Not Found"
        return resp

    mock_session.get.side_effect = mock_get

    with (
        patch("src.ingestion.benchmark.BENCHMARK_CACHE_DIR", benchmark_cache_dir),
        patch("src.ingestion.benchmark.NON_TRADING_DAYS_FILE", benchmark_non_trading_file),
    ):
        source = NSEIndexSource(session=mock_session)

        # Saturday fetch: returns None and marks non-trading
        sat_prices = source.fetch_prices(sat_date)
        assert sat_prices is None
        assert sat_date.isoformat() in load_benchmark_non_trading_days()

        # Sunday fetch: returns prices and creates parquet cache
        sun_prices = source.fetch_prices(sun_date)
        assert sun_prices is not None
        assert "NIFTY 50" in sun_prices
        assert sun_prices["NIFTY 50"] == 24050.0
        assert "NIFTY 500" in sun_prices
        assert sun_prices["NIFTY 500"] == 22100.0
        assert sun_date.isoformat() not in load_benchmark_non_trading_days()

        # End-to-end ingest_benchmarks test with db session
        etl = BhavcopyETL()

        @contextmanager
        def _get_test_db():
            session = session_factory()
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        with patch("src.ingestion.benchmark.get_db", _get_test_db):
            # Saturday is already in non_trading_days, so ingest_benchmarks only passes Sunday
            total_saved = etl.ingest_benchmarks(start_date=sat_date, end_date=sun_date)
            assert total_saved == 2  # NIFTY 50 and NIFTY 500

            # Verify rows stored in BenchmarkPrice
            with session_factory() as session:
                rows = session.query(BenchmarkPrice).all()
                assert len(rows) == 2
                indices = {r.index_code: r.close for r in rows}
                assert indices["NIFTY 50"] == 24050.0
                assert indices["NIFTY 500"] == 22100.0
                for r in rows:
                    assert r.date == sun_date


def test_same_day_rerun_not_blocked_when_bhavcopy_initially_missing(sqlite_db, tmp_path: Path):
    """Regression test: Same-day re-run must not be blocked.

    If an update runs before NSE publishes today's Bhavcopy (returns None/404),
    that 404 must NOT be recorded as non-trading, and a subsequent run on the same
    day must make a request and store the rows once available.
    """
    _engine, session_factory = sqlite_db
    cache_dir = tmp_path / "bhavcopy_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    non_trading_file = cache_dir / "non_trading_days.json"

    etl = BhavcopyETL(cache_dir=cache_dir)
    mock_downloader = MagicMock()
    today = date.today()

    @contextmanager
    def _get_test_db():
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # Run 1: NSE has not yet published today's Bhavcopy -> returns None
    mock_downloader.download_bhavcopy.return_value = None
    etl._downloader = mock_downloader

    with patch("src.etl.pipeline.get_db", _get_test_db):
        stock_rows_1 = etl.collect_new_rows(today, today)
        assert stock_rows_1 == {}

        # "Today" must NOT be recorded as non-trading in non_trading_days.json
        assert today not in etl.get_non_trading_days()
        if non_trading_file.exists():
            cached_days = set(json.loads(non_trading_file.read_text(encoding="utf-8")))
            assert today.isoformat() not in cached_days

        # Even if a previous buggy run had cached today, it must never be skipped!
        etl.record_non_trading_days([today])  # intentionally simulate stale cache entry

        # Run 2: Later in the day, NSE publishes today's Bhavcopy
        mock_downloader.download_bhavcopy.reset_mock()
        mock_downloader.download_bhavcopy.return_value = _make_bhavcopy_df(
            today, symbol="INFY", close=1850.0, prev_close=1840.0
        )

        stock_rows_2 = etl.collect_new_rows(today, today)
        assert "INFY" in stock_rows_2
        assert mock_downloader.download_bhavcopy.call_count == 1

        success, failed = etl.load_to_db(stock_rows_2)
        assert "INFY" in success
        assert failed == []

        with session_factory() as session:
            rows = session.query(DailyPrice).filter_by(symbol="INFY", date=today).all()
            assert len(rows) == 1
            assert rows[0].close == 1850.0


def test_recheck_historical_session_rebuilds_adjustments_for_subsequent_split(
    sqlite_db, tmp_path: Path
):
    """Test recheck_non_trading rebuilds corporate action adjustments for historical sessions.

    Stock with a 2:1 split on 2025-02-04 (NSE PrevClose already adjusted to 50 on the ex-date).
    Insert a Saturday session on 2025-02-01 (raw close 100), then run recheck.
    The Saturday adj_close must equal the neighbouring days' adj_close (50), not raw 100.
    >= 30 flat filler symbols are seeded so the market-wide event guard does not trigger.
    """
    engine, session_factory = sqlite_db
    cache_dir = tmp_path / "bhavcopy_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    non_trading_file = cache_dir / "non_trading_days.json"

    # Pre-seed non_trading_days.json with the Saturday session (previously cached as holiday)
    sat_date = date(2025, 2, 1)
    non_trading_file.write_text(json.dumps([sat_date.isoformat()]), encoding="utf-8")

    # Dates in sequence
    fri_date = date(2025, 1, 31)
    mon_date = date(2025, 2, 3)
    tue_date = date(2025, 2, 4)  # Ex-date of 2:1 split

    with session_factory() as session:
        # Pre-seed >= 30 flat filler symbols so cross-sectional guard does not suppress the split
        for i in range(35):
            sym = f"SYM{i:02d}"
            for d in [fri_date, mon_date, tue_date]:
                session.add(
                    DailyPrice(
                        symbol=sym,
                        date=d,
                        open=100.0,
                        high=100.0,
                        low=100.0,
                        close=100.0,
                        prev_close=100.0,
                        volume=1000,
                        adj_close=100.0,
                    )
                )

        # Pre-seed SPLIT_STOCK on Fri, Mon, Tue
        # On Tue 2025-02-04, 2:1 split ex-date: PrevClose is adjusted to 50.0 by NSE
        session.add(
            DailyPrice(
                symbol="SPLIT_STOCK",
                date=fri_date,
                open=100.0,
                high=100.0,
                low=100.0,
                close=100.0,
                prev_close=100.0,
                volume=10000,
                adj_close=50.0,
            )
        )
        session.add(
            DailyPrice(
                symbol="SPLIT_STOCK",
                date=mon_date,
                open=100.0,
                high=100.0,
                low=100.0,
                close=100.0,
                prev_close=100.0,
                volume=10000,
                adj_close=50.0,
            )
        )
        session.add(
            DailyPrice(
                symbol="SPLIT_STOCK",
                date=tue_date,
                open=50.0,
                high=50.0,
                low=50.0,
                close=50.0,
                prev_close=50.0,
                volume=20000,
                adj_close=50.0,
            )
        )
        session.commit()

    etl = BhavcopyETL(cache_dir=cache_dir)
    mock_downloader = MagicMock()

    # Saturday session has raw unadjusted close 100.0
    mock_downloader.download_bhavcopy.return_value = _make_bhavcopy_df(
        sat_date, symbol="SPLIT_STOCK", close=100.0, prev_close=100.0
    )
    etl._downloader = mock_downloader

    @contextmanager
    def _get_test_db():
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    with (
        patch("src.etl.pipeline.get_db", _get_test_db),
        patch("src.etl.adjust.get_db", _get_test_db),
        patch("src.etl.adjust.default_engine", engine),
    ):
        success, failed = etl.recheck_non_trading(
            start=sat_date,
            end=sat_date,
            skip_constituent_filter=True,
        )

        assert "SPLIT_STOCK" in success
        assert failed == []

        # Check all 4 days of SPLIT_STOCK in daily_prices
        with session_factory() as session:
            rows = (
                session.query(DailyPrice)
                .filter_by(symbol="SPLIT_STOCK")
                .order_by(DailyPrice.date)
                .all()
            )
            assert len(rows) == 4
            dates_found = [r.date for r in rows]
            assert dates_found == [fri_date, sat_date, mon_date, tue_date]

            sat_row = next(r for r in rows if r.date == sat_date)
            # Raw close is 100.0
            assert sat_row.close == 100.0
            # Due to rebuild_adjusted, Saturday row gets the 0.5 split factor
            # It must equal neighbouring days' adj_close of 50.0, NOT raw 100.0!
            assert sat_row.adj_close == 50.0
            for r in rows:
                assert r.adj_close == 50.0
