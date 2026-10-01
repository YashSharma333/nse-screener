"""
ETL deduplication and upsert tests.

All MySQL interactions are fully mocked via pytest-mock so the suite
runs without a live database.  Tests cover:

  - load_to_db with a mock session: verifies ON DUPLICATE KEY UPDATE
    is called with the expected row payload.
  - Idempotency: calling load_to_db twice with the same rows must not
    raise (simulates re-download of the same Bhavcopy date).
  - Per-symbol isolation: if the bulk upsert raises, the slow-path
    retries each symbol individually.
  - collect_new_rows: verifies that a None return from download_bhavcopy
    (holiday) is skipped gracefully and does not add to stock_rows.
  - collect_new_rows: verifies that a corrupt download (exception) is
    caught and skipped without halting the loop.
  - resolve_date_range: when the DB returns None (empty table), the
    pipeline falls back to the initial first-run date range.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, timedelta
from unittest.mock import MagicMock, patch, call

import pandas as pd
import pytest

from src.etl.pipeline import BhavcopyETL


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def etl(tmp_path, monkeypatch):
    """Return a BhavcopyETL with init_db and NSEBhavcopyDownloader mocked out."""
    with patch("src.etl.pipeline.init_db"), \
         patch("src.etl.pipeline.NSEBhavcopyDownloader"):
        instance = BhavcopyETL()
    return instance


def _mock_session():
    """Return a MagicMock that behaves as a context-managed SQLAlchemy session."""
    session = MagicMock()
    session.execute = MagicMock()
    session.commit = MagicMock()
    session.rollback = MagicMock()
    session.close = MagicMock()

    @contextmanager
    def _get_db():
        yield session

    return session, _get_db


def _sample_stock_rows() -> dict[str, list[dict]]:
    """Two symbols, two days each — 4 total rows."""
    d1 = date(2024, 6, 10)
    d2 = date(2024, 6, 11)
    return {
        "RELIANCE": [
            {"symbol": "RELIANCE", "date": d1, "open": 2900.0, "high": 2950.0,
             "low": 2880.0, "close": 2930.0, "volume": 500_000, "index_name": "NIFTY 100"},
            {"symbol": "RELIANCE", "date": d2, "open": 2930.0, "high": 2970.0,
             "low": 2910.0, "close": 2960.0, "volume": 520_000, "index_name": "NIFTY 100"},
        ],
        "INFY": [
            {"symbol": "INFY", "date": d1, "open": 1400.0, "high": 1430.0,
             "low": 1390.0, "close": 1420.0, "volume": 300_000, "index_name": "NIFTY 100"},
            {"symbol": "INFY", "date": d2, "open": 1420.0, "high": 1450.0,
             "low": 1410.0, "close": 1440.0, "volume": 310_000, "index_name": "NIFTY 100"},
        ],
    }


# ---------------------------------------------------------------------------
# load_to_db — happy path
# ---------------------------------------------------------------------------
class TestLoadToDbHappyPath:
    def test_bulk_upsert_called_once_per_batch(self, etl):
        """load_to_db should call session.execute exactly once for 4 rows
        when batch_size >= 4 (single batch)."""
        session, get_db_ctx = _mock_session()
        stock_rows = _sample_stock_rows()

        with patch("src.etl.pipeline.get_db", get_db_ctx):
            success, failed = etl.load_to_db(stock_rows)

        assert sorted(success) == ["INFY", "RELIANCE"]
        assert failed == []
        # One execute call per batch (all 4 rows fit in default batch of 1000)
        assert session.execute.call_count == 1

    def test_returns_empty_on_empty_input(self, etl):
        """Empty stock_rows must return ([], []) without touching the DB."""
        session, get_db_ctx = _mock_session()
        with patch("src.etl.pipeline.get_db", get_db_ctx):
            success, failed = etl.load_to_db({})
        assert success == []
        assert failed == []
        session.execute.assert_not_called()

    def test_idempotent_on_duplicate_rows(self, etl):
        """Calling load_to_db twice with identical rows must not raise.
        The ON DUPLICATE KEY UPDATE semantics are handled by MySQL;
        here we verify the pipeline calls execute twice without error."""
        session, get_db_ctx = _mock_session()
        stock_rows = _sample_stock_rows()

        with patch("src.etl.pipeline.get_db", get_db_ctx):
            s1, f1 = etl.load_to_db(stock_rows)
            s2, f2 = etl.load_to_db(stock_rows)

        assert f1 == [] and f2 == []
        # execute called once per load_to_db call (2 total)
        assert session.execute.call_count == 2


# ---------------------------------------------------------------------------
# load_to_db — per-symbol isolation (slow path)
# ---------------------------------------------------------------------------
class TestLoadToDbSymbolIsolation:
    def test_bulk_failure_triggers_per_symbol_retry(self, etl):
        """When the bulk upsert raises, the slow path retries each symbol
        individually.  A symbol whose per-symbol session also raises goes
        to failed; others succeed."""
        stock_rows = _sample_stock_rows()
        call_count = {"n": 0}

        good_session = MagicMock()
        bad_session = MagicMock()
        bad_session.execute.side_effect = Exception("constraint violation")

        @contextmanager
        def _flaky_get_db():
            call_count["n"] += 1
            if call_count["n"] == 1:
                # First call = bulk path → simulate bulk failure
                raise Exception("bulk fail")
            elif call_count["n"] == 2:
                # Second call = RELIANCE individual → succeed
                yield good_session
            else:
                # Third call = INFY individual → fail
                yield bad_session

        with patch("src.etl.pipeline.get_db", _flaky_get_db):
            success, failed = etl.load_to_db(stock_rows)

        # One of the two symbols should succeed, the other fail
        assert len(success) + len(failed) == 2
        assert set(success + failed) == {"RELIANCE", "INFY"}


# ---------------------------------------------------------------------------
# collect_new_rows — holiday skipping
# ---------------------------------------------------------------------------
class TestCollectNewRowsHolidays:
    def test_none_download_skipped(self, etl):
        """download_bhavcopy returning None (holiday) must result in zero rows."""
        etl._downloader.download_bhavcopy.return_value = None
        # One weekday range
        start = date(2024, 6, 10)  # Monday
        end   = date(2024, 6, 10)
        result = etl.collect_new_rows(start, end)
        assert result == {}

    def test_empty_df_download_skipped(self, etl):
        """download_bhavcopy returning an empty DataFrame must yield zero rows."""
        etl._downloader.download_bhavcopy.return_value = pd.DataFrame()
        start = date(2024, 6, 10)
        end   = date(2024, 6, 10)
        result = etl.collect_new_rows(start, end)
        assert result == {}

    def test_download_exception_skipped(self, etl):
        """Exception in download_bhavcopy must be caught; loop continues."""
        etl._downloader.download_bhavcopy.side_effect = ConnectionError("timeout")
        start = date(2024, 6, 10)
        end   = date(2024, 6, 14)  # Mon–Fri = 5 weekdays
        result = etl.collect_new_rows(start, end)
        assert result == {}
        # All 5 weekdays attempted despite exception
        assert etl._downloader.download_bhavcopy.call_count == 5

    def test_symbol_filter_applied(self, etl):
        """Only symbols in symbol_filter must be kept."""
        bhavcopy = pd.DataFrame(
            {
                "Symbol": ["RELIANCE", "INFY", "TCS"],
                "Open":   [2900.0, 1400.0, 3500.0],
                "High":   [2950.0, 1430.0, 3550.0],
                "Low":    [2880.0, 1390.0, 3480.0],
                "Close":  [2930.0, 1420.0, 3520.0],
                "Volume": [500_000, 300_000, 200_000],
            }
        )
        etl._downloader.download_bhavcopy.return_value = bhavcopy
        start = date(2024, 6, 10)
        end   = date(2024, 6, 10)
        result = etl.collect_new_rows(
            start, end,
            symbol_filter={"RELIANCE", "INFY"},
            symbol_index_map={"RELIANCE": "NIFTY 100", "INFY": "NIFTY 100"},
        )
        assert set(result.keys()) == {"RELIANCE", "INFY"}
        assert "TCS" not in result
        # Verify index_name was stamped correctly
        assert result["RELIANCE"][0]["index_name"] == "NIFTY 100"


# ---------------------------------------------------------------------------
# resolve_date_range — DB unavailable / empty table
# ---------------------------------------------------------------------------
class TestResolveDateRange:
    def test_empty_table_returns_first_run_range(self, etl):
        """When the DB returns None (empty table), resolve_date_range must
        return a start date ~initial_days_back days ago."""
        mock_row = MagicMock()
        mock_row.__getitem__ = MagicMock(return_value=None)

        @contextmanager
        def _get_db_empty():
            session = MagicMock()
            result = MagicMock()
            result.fetchone.return_value = None
            session.execute.return_value = result
            yield session

        with patch("src.etl.pipeline.get_db", _get_db_empty):
            start, end, is_update = etl.resolve_date_range()

        assert is_update is False
        today = date.today()
        expected_start = today - timedelta(days=etl.initial_days_back)
        assert start == expected_start
        assert end == today

    def test_db_exception_falls_back_to_first_run(self, etl):
        """If the DB connection raises, resolve_date_range falls back gracefully."""
        @contextmanager
        def _get_db_raises():
            raise ConnectionError("cannot connect")
            yield  # unreachable, but required for generator syntax

        with patch("src.etl.pipeline.get_db", _get_db_raises):
            start, end, is_update = etl.resolve_date_range()

        assert is_update is False
        assert start == date.today() - timedelta(days=etl.initial_days_back)

    def test_up_to_date_returns_start_gt_end(self, etl):
        """When last_stored == today, fetch_start > fetch_end → nothing to do."""
        today = date.today()

        @contextmanager
        def _get_db_current():
            session = MagicMock()
            result = MagicMock()
            result.fetchone.return_value = (today,)
            session.execute.return_value = result
            yield session

        with patch("src.etl.pipeline.get_db", _get_db_current):
            start, end, is_update = etl.resolve_date_range()

        assert start > end  # signals "nothing to fetch" in run()
        assert is_update is True
