"""
Unit tests for ui/data.py caching and invalidation mechanisms (F5).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pandas as pd

from ui.data import get_cache_invalidation_token, get_market_data, get_snapshot


def test_cache_invalidation_token_computation(tmp_path):
    parquet_file = tmp_path / "indicators.parquet"
    meta_file = tmp_path / "indicators.meta.json"

    # Missing files return (0.0, "missing")
    mtime, meta_hash = get_cache_invalidation_token(parquet_file, meta_file)
    assert mtime == 0.0
    assert meta_hash == "missing"

    # Create parquet and meta files
    parquet_file.write_text("fake parquet data")
    meta_file.write_text(json.dumps({"version": 2, "rows": 100}))

    mtime_1, hash_1 = get_cache_invalidation_token(parquet_file, meta_file)
    assert mtime_1 > 0.0
    assert len(hash_1) == 64  # SHA256 length

    # Changing meta file changes the hash
    meta_file.write_text(json.dumps({"version": 2, "rows": 200}))
    _mtime_2, hash_2 = get_cache_invalidation_token(parquet_file, meta_file)
    assert hash_2 != hash_1


def test_get_market_data_uses_cache_token(tmp_path):
    parquet_file = tmp_path / "indicators.parquet"
    meta_file = tmp_path / "indicators.meta.json"

    parquet_file.write_text("initial")
    meta_file.write_text(json.dumps({"v": 1}))

    dummy_result = {
        "snapshot": pd.DataFrame([{"symbol": "TCS", "close": 3500.0}]),
        "indicators": pd.DataFrame(),
        "is_live": True,
        "message": "ok",
    }

    with patch("ui.data._fetch_market_data", return_value=dummy_result) as mock_fetch:
        res1 = get_market_data(parquet_path=parquet_file, meta_path=meta_file)
        assert not res1["snapshot"].empty
        assert mock_fetch.call_count == 1

        # Second call with same files should use cached result
        res2 = get_market_data(parquet_path=parquet_file, meta_path=meta_file)
        assert res2["snapshot"].iloc[0]["symbol"] == "TCS"
        assert mock_fetch.call_count == 1  # Not called again!

        # Modify meta file -> cache token changes -> fetch called again
        meta_file.write_text(json.dumps({"v": 2}))
        res3 = get_market_data(parquet_path=parquet_file, meta_path=meta_file)
        assert res3["snapshot"].iloc[0]["symbol"] == "TCS"
        assert mock_fetch.call_count == 2  # Invalidation fired!


def test_empty_snapshot_is_not_kept_for_the_cache_ttl(tmp_path):
    """An empty result (DB hiccup, update still running) must be retried on the next run."""
    parquet_file = tmp_path / "indicators.parquet"
    meta_file = tmp_path / "indicators.meta.json"
    parquet_file.write_text("x")
    meta_file.write_text(json.dumps({"v": "empty-retry"}))

    empty = {"snapshot": pd.DataFrame(), "is_live": False, "message": "MySQL offline"}
    full = {
        "snapshot": pd.DataFrame([{"symbol": "TCS", "close": 3500.0}]),
        "is_live": True,
        "message": "ok",
    }

    with patch("ui.data._fetch_market_data", side_effect=[empty, full]) as mock_fetch:
        assert get_snapshot(parquet_file, meta_file)["snapshot"].empty
        # Same files, same token: a cached empty result would be returned again.
        assert not get_snapshot(parquet_file, meta_file)["snapshot"].empty
        assert mock_fetch.call_count == 2


def test_empty_database_ui_does_not_trigger_update():
    """Visiting an empty dashboard must not start a data update by itself."""
    empty_result = {
        "snapshot": pd.DataFrame(),
        "indicators": pd.DataFrame(),
        "is_live": False,
        "message": "offline",
    }

    mock_update = MagicMock()
    with (
        patch("ui.data._fetch_market_data", return_value=empty_result),
        patch("ui.data.run_full_update", mock_update),
    ):
        from streamlit.testing.v1 import AppTest

        from config.settings import PROJECT_ROOT

        at = AppTest.from_file(str(PROJECT_ROOT / "ui" / "Dashboard.py"), default_timeout=10)
        at.run()
        # Verify no unprompted update occurred
        mock_update.assert_not_called()
        assert not at.exception
