"""
Tests for Screener Base Class and Registry (T10)
===============================================
Verifies:
- Standardized screener registration, discovery, and instantiation.
- Single unified rules() method.
- Execution (`run()`) and detailed diagnosis (`explain()`).
- Near-miss identification when exactly 1 condition fails.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pandas as pd
import pytest

from src.screeners.base import Screener
from src.screeners.registry import (
    get_screener,
    get_screener_keys,
    list_screeners,
    register_screener,
)


class DummySampleScreener(Screener):
    """Test screener using the unified rules() pattern."""

    key = "dummy_test_screener"
    name = "Dummy Test Screener"
    description = "Screener used for testing registry and explainability"
    category = "test"
    default_params: ClassVar[dict[str, Any]] = {}

    def rules(self, df: pd.DataFrame, p: dict[str, Any]) -> dict[str, pd.Series]:
        return {
            "close_above_sma50": (df["close"] > df["sma_50"]).fillna(False),
            "within_10pct_52w_high": (df["close"] >= 0.90 * df["high_52w"]).fillna(False),
            "volume_above_sma": (df["volume"] > df["volume_sma_20"]).fillna(False),
            "golden_cross_recent": df["golden_cross_10d"].fillna(False).astype(bool),
            "rsi_bullish": (df["rsi_14"] > 50.0).fillna(False),
        }


def test_registry_registration_and_lookup() -> None:
    """Verify screener can be registered and retrieved by key."""
    registered_cls = register_screener()(DummySampleScreener)
    assert registered_cls is DummySampleScreener

    screener = get_screener("dummy_test_screener")
    assert isinstance(screener, DummySampleScreener)
    assert screener.name == "Dummy Test Screener"
    assert screener.category == "test"

    # Category listing
    keys = get_screener_keys(category="test")
    assert "dummy_test_screener" in keys

    all_s = list_screeners()
    assert DummySampleScreener in all_s
    cat_s = list_screeners(category="test")
    assert DummySampleScreener in cat_s

    # Non-existent screener raises KeyError
    with pytest.raises(KeyError, match="Unknown screener 'non_existent_key'"):
        get_screener("non_existent_key")


def test_screener_run_and_explain() -> None:
    """Verify run() and explain() across all conditions."""
    screener = DummySampleScreener()

    # 3 rows:
    # Row 0: passes all 5 conditions (close > 50, within high, vol > sma, gc True, rsi > 50)
    # Row 1: passes 4 of 5 (fails only rsi_bullish) -> NEAR MISS!
    # Row 2: passes 2 of 5
    data = {
        "symbol": ["STOCK1", "STOCK2", "STOCK3"],
        "close": [100.0, 100.0, 40.0],
        "sma_50": [90.0, 90.0, 50.0],
        "high_52w": [105.0, 105.0, 100.0],
        "volume": [2000, 2000, 500],
        "volume_sma_20": [1000, 1000, 1000],
        "golden_cross_10d": [True, True, False],
        "rsi_14": [55.0, 45.0, 40.0],  # Row 1 fails rsi_bullish (> 50.0)
    }
    df = pd.DataFrame(data)

    # 1. run(df) returns only Row 0
    passed_df = screener.run(df)
    assert len(passed_df) == 1
    assert passed_df.iloc[0]["symbol"] == "STOCK1"

    # 2. explain(df) diagnostics
    explained = screener.explain(df)
    assert len(explained) == 3

    # Check condition columns
    assert "cond_close_above_sma50" in explained.columns
    assert "cond_rsi_bullish" in explained.columns

    # Row 0
    row0 = explained.iloc[0]
    assert row0["passed"] is True or row0["passed"] == 1
    assert row0["conditions_passed"] == 5
    assert row0["conditions_total"] == 5
    assert row0["near_miss"] is False or row0["near_miss"] == 0
    assert row0["near_miss_on"] == ""
    assert row0["failed_conditions"] == []

    # Row 1: Near miss on rsi_bullish
    row1 = explained.iloc[1]
    assert row1["passed"] is False or row1["passed"] == 0
    assert row1["conditions_passed"] == 4
    assert row1["conditions_total"] == 5
    assert row1["near_miss"] is True or row1["near_miss"] == 1
    assert row1["near_miss_on"] == "rsi_bullish"
    assert row1["failed_conditions"] == ["rsi_bullish"]

    # Row 2: Failed multiple conditions -> Not a near miss
    row2 = explained.iloc[2]
    assert row2["passed"] is False or row2["passed"] == 0
    assert row2["near_miss"] is False or row2["near_miss"] == 0
    assert len(row2["failed_conditions"]) > 1


def test_screener_empty_dataframe() -> None:
    """Verify run() and explain() gracefully handle empty DataFrames."""
    screener = DummySampleScreener()
    empty = pd.DataFrame()

    res_run = screener.run(empty)
    assert res_run.empty

    res_explain = screener.explain(empty)
    assert res_explain.empty
    assert "passed" in res_explain.columns
    assert "near_miss" in res_explain.columns
