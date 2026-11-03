"""Unit tests for the Data Service layer and Parquet indicator caching."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src.computation.indicators import INDICATORS_SCHEMA_VERSION
from src.db.data_service import (
    ensure_data_initialized,
    load_indicator_snapshot,
    load_raw_market_data,
    save_indicator_parquet_cache,
)


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "RELIANCE",
                "date": date(2024, 1, 10),
                "close": 2500.0,
                "rsi_14": 55.0,
                "golden_cross": False,
            },
            {
                "symbol": "RELIANCE",
                "date": date(2024, 1, 11),
                "close": 2520.0,
                "rsi_14": 57.0,
                "golden_cross": True,
            },
            {
                "symbol": "TCS",
                "date": date(2024, 1, 11),
                "close": 3800.0,
                "rsi_14": 62.0,
                "golden_cross": False,
            },
        ]
    )


def test_save_and_load_indicator_parquet_cache(tmp_path: Path):
    """Verify atomic write and fast reading of pre-computed indicators parquet cache."""
    cache_file = tmp_path / "processed" / "indicators.parquet"
    df = _sample_df()

    meta_dict = {
        "db_max_date": "2024-01-11",
        "adj_factors_count": 0,
        "adj_factors_max_detected_at": None,
        "benchmark_max_date": "2024-01-11",
        "indicators_schema_version": INDICATORS_SCHEMA_VERSION,
        "row_count": len(df),
    }

    save_indicator_parquet_cache(df, cache_path=cache_file, meta_dict=meta_dict)
    assert cache_file.exists()
    assert cache_file.with_suffix(".meta.json").exists()

    with patch("src.db.data_service.check_connection", return_value=False):
        snapshot, _, _, msg = load_indicator_snapshot(cache_path=cache_file, force_refresh=False)

    assert len(snapshot) == 2
    assert set(snapshot["symbol"]) == {"RELIANCE", "TCS"}
    assert "Parquet Cache" in msg
    rel_row = snapshot[snapshot["symbol"] == "RELIANCE"].iloc[0]
    assert rel_row["date"] == date(2024, 1, 11)
    assert bool(rel_row["golden_cross"]) is True


def test_cache_invalidation_reasons(tmp_path: Path):
    """F7: Test cache invalidation for each metadata mismatch."""
    cache_file = tmp_path / "indicators.parquet"
    meta_file = tmp_path / "indicators.meta.json"
    df = _sample_df()

    base_meta = {
        "db_max_date": "2024-01-11",
        "adj_factors_count": 2,
        "adj_factors_max_detected_at": "2024-01-10T12:00:00",
        "benchmark_max_date": "2024-01-11",
        "indicators_schema_version": INDICATORS_SCHEMA_VERSION,
        "row_count": len(df),
    }

    def _setup_cache(meta_override: dict):
        m = base_meta.copy()
        m.update(meta_override)
        df.to_parquet(cache_file, index=False)
        with meta_file.open("w", encoding="utf-8") as f:
            json.dump(m, f)

    # 1. Schema version changed
    _setup_cache({"indicators_schema_version": INDICATORS_SCHEMA_VERSION - 1})
    with (
        patch("src.db.data_service.check_connection", return_value=True),
        patch("src.db.data_service.get_db_cache_metadata", return_value=base_meta),
        patch(
            "src.db.data_service.load_raw_market_data", return_value=(pd.DataFrame(), True, "empty")
        ),
    ):
        _, _, _, msg = load_indicator_snapshot(cache_path=cache_file)
        # Should not hit cache because schema version is old
        assert "Parquet Cache" not in msg

    # 2. Corporate actions count changed
    _setup_cache({"adj_factors_count": 0})
    with (
        patch("src.db.data_service.check_connection", return_value=True),
        patch("src.db.data_service.get_db_cache_metadata", return_value=base_meta),
        patch(
            "src.db.data_service.load_raw_market_data", return_value=(pd.DataFrame(), True, "empty")
        ),
    ):
        _, _, _, msg = load_indicator_snapshot(cache_path=cache_file)
        assert "Parquet Cache" not in msg

    # 3. Benchmark max date changed
    _setup_cache({"benchmark_max_date": "2024-01-05"})
    with (
        patch("src.db.data_service.check_connection", return_value=True),
        patch("src.db.data_service.get_db_cache_metadata", return_value=base_meta),
        patch(
            "src.db.data_service.load_raw_market_data", return_value=(pd.DataFrame(), True, "empty")
        ),
    ):
        _, _, _, msg = load_indicator_snapshot(cache_path=cache_file)
        assert "Parquet Cache" not in msg

    # 4. DB max date changed
    _setup_cache({"db_max_date": "2024-01-08"})
    with (
        patch("src.db.data_service.check_connection", return_value=True),
        patch("src.db.data_service.get_db_cache_metadata", return_value=base_meta),
        patch(
            "src.db.data_service.load_raw_market_data", return_value=(pd.DataFrame(), True, "empty")
        ),
    ):
        _, _, _, msg = load_indicator_snapshot(cache_path=cache_file)
        assert "Parquet Cache" not in msg

    # 5. Perfect match -> Cache hit!
    _setup_cache({})
    with (
        patch("src.db.data_service.check_connection", return_value=True),
        patch("src.db.data_service.get_db_cache_metadata", return_value=base_meta),
    ):
        _, _, _, msg = load_indicator_snapshot(cache_path=cache_file)
        assert "Parquet Cache" in msg


def test_load_raw_market_data_when_offline_returns_empty_no_demo_data():
    """Verify demo dataset is completely removed; offline DB returns empty dataframe with notice."""
    with patch("src.db.data_service.check_connection", return_value=False):
        df, is_live, msg = load_raw_market_data()

    assert df.empty
    assert is_live is False
    assert "MySQL offline" in msg
    assert "Demo" not in msg


def test_ensure_data_initialized_non_blocking():
    """Verify ensure_data_initialized returns status without blocking downloads."""
    with patch("src.db.data_service.check_connection", return_value=False):
        status = ensure_data_initialized()
        assert status["online"] is False
        assert status["has_db_data"] is False
