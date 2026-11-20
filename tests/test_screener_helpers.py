"""Tests for the screener page helpers (market cap rule, series grouping, parameter editor)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.screeners.catalog import SwingMomentumScreener, SwingVolumeScreener
from ui.components.screener_helpers import (
    apply_market_cap_filter,
    build_near_miss_table,
    build_results_table,
    changed_params,
    editable_params,
    group_by_series,
    inspector_symbols,
    param_step,
)

CR = 1e7


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["BIG", "SMALL", "NOCAP", "SME", "SMEBIG", "BZ1"],
            "series": ["EQ", "EQ", "EQ", "SM", "SM", "BZ"],
            "market_cap": [1000 * CR, 100 * CR, np.nan, 10 * CR, 900 * CR, np.nan],
        }
    )


def test_market_cap_filter_applies_to_eq_only():
    """Regression: SME / BZ rows must survive even with a tiny or missing market cap."""
    kept = apply_market_cap_filter(_frame(), 500)["symbol"].tolist()
    assert kept == ["BIG", "SME", "SMEBIG", "BZ1"]


def test_market_cap_filter_off_keeps_everything():
    assert len(apply_market_cap_filter(_frame(), 0)) == 6


def test_market_cap_filter_without_columns_is_noop():
    df = _frame().drop(columns=["market_cap"])
    assert len(apply_market_cap_filter(df, 500)) == 6
    assert apply_market_cap_filter(pd.DataFrame(), 500).empty


def test_group_by_series_puts_eq_first():
    assert [name for name, _ in group_by_series(_frame())] == ["EQ", "BZ", "SM"]
    assert group_by_series(pd.DataFrame()) == []
    no_series = _frame().drop(columns=["series"])
    assert [name for name, _ in group_by_series(no_series)] == ["All"]


def test_editable_params_follow_each_screener_defaults():
    volume = editable_params(SwingVolumeScreener.default_params)
    assert set(volume) == {"vol_multiplier", "min_volume", "max_pct_from_high"}
    momentum = editable_params(SwingMomentumScreener.default_params)
    assert "max_return_60d" in momentum


def test_changed_params_only_returns_differences():
    defaults = {"a": 1.5, "b": 10}
    assert changed_params({"a": 1.5, "b": 10}, defaults) == {}
    assert changed_params({"a": 2.0, "b": 10}, defaults) == {"a": 2.0}


def test_overrides_are_accepted_by_the_screener_and_defaults_stay_locked():
    overrides = changed_params({"vol_multiplier": 2.0}, {"vol_multiplier": 1.5})
    assert SwingVolumeScreener(**overrides).params["vol_multiplier"] == 2.0
    assert SwingVolumeScreener.default_params["vol_multiplier"] == 1.5


def test_param_step_scales_with_the_default():
    assert param_step(1.5) == 0.1
    assert param_step(250_000) == 10_000
    assert param_step(0) == 0.1


def test_results_table_has_numeric_market_cap_in_crores_and_series():
    passing = pd.DataFrame(
        {
            "symbol": ["AAA"],
            "series": ["EQ"],
            "market_cap": [2500 * CR],
            "close": [10.0],
            "volume": [300],
            "volume_sma_20": [100],
        }
    )
    table = build_results_table(passing)
    assert table["market_cap_cr"].iloc[0] == 2500.0
    assert table["vol_multiple"].iloc[0] == 3.0
    assert table.columns[:3].tolist() == ["symbol", "series", "market_cap_cr"]


def test_unknown_market_cap_is_blank_not_zero():
    table = build_near_miss_table(
        pd.DataFrame({"symbol": ["A"], "market_cap": [np.nan], "near_miss_on": ["volume_surge"]})
    )
    assert pd.isna(table["market_cap_cr"].iloc[0])
    assert "failed_condition" in table.columns


def test_inspector_symbols_are_only_results_passing_first():
    explained = pd.DataFrame(
        {
            "symbol": ["ZED", "ABC", "MID", "OUT"],
            "passed": [True, True, False, False],
            "near_miss": [False, False, True, False],
        }
    )
    assert inspector_symbols(explained) == ["ABC", "ZED", "MID"]
    assert inspector_symbols(pd.DataFrame()) == []
