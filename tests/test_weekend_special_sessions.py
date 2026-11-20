"""Weekend and holiday special sessions (Diwali Muhurat, Budget Saturdays).

NSE holds real trading sessions on weekends, so the code must treat them as ordinary bars:
no false splits, benchmark closes stored, and adjusted prices correct for sessions that are
inserted before a later split.
"""

from __future__ import annotations

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

from src.computation.indicators.calculator import calculate_sma
from src.db.models import Base, BenchmarkPrice, DailyPrice
from src.etl.adjust import cumulative_factors, rebuild_adjusted
from src.ingestion.benchmark import (
    NSEIndexSource,
    load_benchmark_range,
)
from src.ingestion.benchmark import (
    load_non_trading_days as load_benchmark_non_trading_days,
)
from tests.official_actions import add_official_action


@pytest.fixture
def sqlite_db(tmp_path: Path):
    """File-backed SQLite database for real schema tests without MySQL."""
    db_file = tmp_path / "test_weekend.db"
    engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    return engine, session_factory


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

    # 1. No official corporate action: Fri -> Sun -> Mon is an ordinary run of bars
    events = pd.DataFrame(columns=["symbol", "date", "factor", "ratio_kind"])

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

        # End-to-end load_benchmark_range test with db session
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
            # Saturday is already in non_trading_days, so only Sunday is requested
            total_saved = load_benchmark_range(sat_date, sun_date)
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


def test_historical_session_gets_adjusted_for_subsequent_split(sqlite_db, tmp_path: Path):
    """A historical session inserted before a later split must receive the split factor.

    Stock with a 2:1 split on 2025-02-04 (NSE PrevClose already adjusted to 50 on the ex-date).
    Insert a Saturday session on 2025-02-01 (raw close 100), then rebuild adjusted prices.
    The Saturday adj_close must equal the neighbouring days' adj_close (50), not raw 100.
    >= 30 flat filler symbols are seeded so the market-wide event guard does not trigger.
    """
    engine, session_factory = sqlite_db
    sat_date = date(2025, 2, 1)

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
        # The official list: SPLIT_STOCK had a 2:1 split with ex-date Tuesday
        add_official_action(session, "SPLIT_STOCK", tue_date, factor=0.5)
        session.commit()

    # The Saturday session is inserted AFTER the split ex-date was already stored: raw close
    # 100 (unadjusted), as the loader writes it.
    with session_factory() as session:
        session.add(
            DailyPrice(
                symbol="SPLIT_STOCK",
                date=sat_date,
                open=100.0,
                high=100.0,
                low=100.0,
                close=100.0,
                prev_close=100.0,
                volume=10000,
            )
        )
        session.commit()

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
        patch("src.etl.adjust.get_db", _get_test_db),
        patch("src.etl.adjust.default_engine", engine),
    ):
        rebuild_adjusted(symbols=["SPLIT_STOCK"])

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
