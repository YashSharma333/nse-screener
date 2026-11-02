"""Unit tests for the Data Service layer and Parquet indicator caching."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src.db.data_service import (
    compute_market_indicators,
    get_latest_market_snapshot,
    load_indicator_snapshot,
    load_raw_market_data,
    save_indicator_parquet_cache,
)


def test_save_and_load_indicator_parquet_cache(tmp_path: Path):
    """Verify atomic write and fast reading of pre-computed indicators parquet cache."""
    cache_file = tmp_path / "processed" / "indicators.parquet"

    df = pd.DataFrame(
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

    save_indicator_parquet_cache(df, cache_path=cache_file)
    assert cache_file.exists()

    with patch("src.db.data_service.check_connection", return_value=False):
        snapshot, _, _, msg = load_indicator_snapshot(cache_path=cache_file, force_refresh=False)

    assert len(snapshot) == 2  # RELIANCE and TCS latest rows
    assert set(snapshot["symbol"]) == {"RELIANCE", "TCS"}
    assert "Parquet Cache" in msg
    rel_row = snapshot[snapshot["symbol"] == "RELIANCE"].iloc[0]
    assert rel_row["date"] == date(2024, 1, 11)
    assert bool(rel_row["golden_cross"]) is True


def test_load_raw_market_data_when_offline_returns_empty_no_demo_data():
    """Verify demo dataset is completely removed; offline DB returns empty dataframe with notice."""
    with patch("src.db.data_service.check_connection", return_value=False):
        df, is_live, msg = load_raw_market_data()

    assert df.empty
    assert is_live is False
    assert "MySQL offline" in msg
    assert "Demo" not in msg  # No demo fallback


def test_compute_market_indicators_and_latest_snapshot():
    """Verify indicator calculation and snapshot reduction."""
    dates = pd.date_range("2024-01-01", periods=30, freq="D")
    records = []
    for d in dates:
        records.append(
            {
                "symbol": "INFY",
                "date": d.date(),
                "open": 1500.0,
                "high": 1520.0,
                "low": 1490.0,
                "close": 1510.0,
                "volume": 500000,
                "prev_close": 1505.0,
            }
        )
    raw_df = pd.DataFrame(records)

    with_indicators = compute_market_indicators(raw_df)
    assert "rsi_14" in with_indicators.columns
    assert "HMM" in with_indicators.columns
    assert "golden_cross" in with_indicators.columns

    snapshot = get_latest_market_snapshot(with_indicators)
    assert len(snapshot) == 1
    assert snapshot.iloc[0]["date"] == dates[-1].date()
