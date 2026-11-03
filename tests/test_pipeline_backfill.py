"""
Tests for backwards backfill, non-trading day tracking, and chunk commits (F2).
"""

from __future__ import annotations

import contextlib
import json
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, DailyPrice
from src.etl.pipeline import BhavcopyETL


@pytest.fixture
def sqlite_db(tmp_path):
    """In-memory or file SQLite engine for pipeline testing."""
    db_file = tmp_path / "test_pipeline.db"
    engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    return engine, session_factory


def _make_sample_df(d: date, symbol: str = "TCS", close: float = 3500.0) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Symbol": symbol,
                "Open": close - 10,
                "High": close + 20,
                "Low": close - 20,
                "Close": close,
                "PrevClose": close - 15,
                "Volume": 100_000,
            }
        ]
    )


class TestPipelineBackfill:
    def test_backfill_skips_already_stored_and_non_trading_dates(self, sqlite_db, tmp_path):
        _engine, session_factory = sqlite_db
        cache_dir = tmp_path / "bhavcopy_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)

        # Pre-seed daily_prices with 2024-01-02 and 2024-01-03
        with session_factory() as session:
            session.add(
                DailyPrice(
                    symbol="TCS",
                    date=date(2024, 1, 2),
                    open=3500,
                    high=3550,
                    low=3480,
                    close=3520,
                    prev_close=3500,
                    volume=10000,
                )
            )
            session.add(
                DailyPrice(
                    symbol="TCS",
                    date=date(2024, 1, 3),
                    open=3520,
                    high=3560,
                    low=3510,
                    close=3540,
                    prev_close=3520,
                    volume=12000,
                )
            )
            session.commit()

        # Pre-seed non_trading_days.json with 2024-01-04
        non_trading_file = cache_dir / "non_trading_days.json"
        non_trading_file.write_text(json.dumps(["2024-01-04"]))

        etl = BhavcopyETL(cache_dir=cache_dir)
        mock_downloader = MagicMock()
        mock_downloader.download_bhavcopy.return_value = _make_sample_df(
            date(2024, 1, 5), "TCS", 3550.0
        )
        etl._downloader = mock_downloader

        from contextlib import contextmanager

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
            patch("src.etl.pipeline.rebuild_adjusted") as mock_adj,
            patch("src.etl.pipeline.load_benchmarks") as mock_bench,
            patch("src.db.data_service.load_indicator_snapshot") as mock_snap,
        ):
            # Target range: 2024-01-01 (Mon, holiday) to 2024-01-05 (Fri)
            # Weekdays: 01-01, 01-02 (stored), 01-03 (stored), 01-04 (non_trading), 01-05
            _success, _failed = etl.run_backfill(
                days_back=5,
                chunk_days=10,
                as_of_date=date(2024, 1, 5),
                skip_constituent_filter=True,
            )

            # Download should only be called for 01-01 and 01-05, NOT 01-02, 01-03, or 01-04
            called_dates = [c[0][0] for c in mock_downloader.download_bhavcopy.call_args_list]
            assert date(2024, 1, 2) not in called_dates
            assert date(2024, 1, 3) not in called_dates
            assert date(2024, 1, 4) not in called_dates
            assert date(2024, 1, 5) in called_dates

            # Post-backfill hooks should have been called once
            assert mock_adj.call_count == 1
            assert mock_bench.call_count == 1
            assert mock_snap.call_count == 1

    def test_backfill_records_clean_404_and_skips_network_errors(self, sqlite_db, tmp_path):
        _engine, session_factory = sqlite_db
        cache_dir = tmp_path / "bhavcopy_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        non_trading_file = cache_dir / "non_trading_days.json"

        etl = BhavcopyETL(cache_dir=cache_dir)
        mock_downloader = MagicMock()

        def mock_download(d: date):
            if d == date(2024, 1, 1):
                return None  # Clean 404 / holiday
            if d == date(2024, 1, 2):
                raise ConnectionError("Network timeout")  # Network error!
            return _make_sample_df(d, "TCS", 3500.0)

        mock_downloader.download_bhavcopy.side_effect = mock_download
        etl._downloader = mock_downloader

        from contextlib import contextmanager

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
            etl.run_backfill(
                days_back=3,
                chunk_days=10,
                as_of_date=date(2024, 1, 3),
                skip_constituent_filter=True,
            )

            # Check non_trading_days.json
            assert non_trading_file.exists()
            saved_days = json.loads(non_trading_file.read_text())
            assert "2024-01-01" in saved_days
            # Network error date must NOT be saved as non-trading!
            assert "2024-01-02" not in saved_days

    def test_backfill_chunk_commit_and_resume(self, sqlite_db, tmp_path):
        _engine, session_factory = sqlite_db
        cache_dir = tmp_path / "bhavcopy_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)

        etl = BhavcopyETL(cache_dir=cache_dir)
        mock_downloader = MagicMock()

        call_count = 0

        def mock_download(d: date):
            nonlocal call_count
            call_count += 1
            if d == date(2024, 1, 4):  # Crash during chunk 2
                raise RuntimeError("Simulated crash mid-chunk")
            return _make_sample_df(d, "TCS", 3500.0)

        mock_downloader.download_bhavcopy.side_effect = mock_download
        etl._downloader = mock_downloader

        from contextlib import contextmanager

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
            # Dates: 01-01, 01-02 (chunk 1), 01-03, 01-04 (chunk 2 fails), 01-05 (chunk 3)
            # Run with chunk_days = 2
            with contextlib.suppress(RuntimeError):
                etl.run_backfill(
                    days_back=5,
                    chunk_days=2,
                    as_of_date=date(2024, 1, 5),
                    skip_constituent_filter=True,
                )

            # Chunk 1 (01-01 and 01-02) should be committed in DB!
            with session_factory() as session:
                stored = session.query(DailyPrice).all()
                stored_dates = {row.date for row in stored}
                assert date(2024, 1, 1) in stored_dates
                assert date(2024, 1, 2) in stored_dates
                assert date(2024, 1, 4) not in stored_dates
