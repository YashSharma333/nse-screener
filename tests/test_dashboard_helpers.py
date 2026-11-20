"""Tests for the pure Dashboard helpers (series filter, KPIs, column presets)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ui.components.dashboard_helpers import (
    COLUMN_NAMES,
    COLUMN_PRESETS,
    color_change,
    compute_dashboard_kpis,
    default_series,
    filter_by_series,
    filter_by_symbol,
    rename_for_display,
)


def _snapshot() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["AAA", "BBB", "CCC", "DDD"],
            "series": ["EQ", "EQ", "SM", "BZ"],
            "close": [110.0, 90.0, 50.0, 10.0],
            "sma_200": [100.0, 100.0, 40.0, np.nan],
            "pct_change_daily": [2.0, -1.0, 0.0, np.nan],
            "rsi_14": [60.0, 40.0, 50.0, np.nan],
            "pct_from_52w_high": [-2.0, -30.0, -5.0, np.nan],
            "vol_multiple": [2.0, 1.0, 1.5, np.nan],
        }
    )


def _kpi(kpis, label_start):
    return next(value for label, value, _ in kpis if label.startswith(label_start))


def test_default_series_prefers_eq():
    assert default_series(["BE", "EQ", "SM"]) == ["EQ"]
    assert default_series(["BE", "SM"]) == ["BE", "SM"]


def test_filter_by_series_keeps_only_selected():
    result = filter_by_series(_snapshot(), ["EQ", "SM"])
    assert result["symbol"].tolist() == ["AAA", "BBB", "CCC"]


def test_filter_by_series_without_column_is_noop():
    df = _snapshot().drop(columns=["series"])
    assert len(filter_by_series(df, ["EQ"])) == len(df)


def test_symbol_search_is_case_insensitive_and_not_regex():
    df = _snapshot()
    assert filter_by_symbol(df, " bb ")["symbol"].tolist() == ["BBB"]
    assert len(filter_by_symbol(df, "")) == 4
    assert filter_by_symbol(df, ".*").empty  # "." and "*" are plain text, not a regex


def test_kpis_count_the_rows_you_pass_in_not_the_universe():
    """Regression: 'Stocks shown' must follow the filter and 'of N' the full snapshot."""
    full = _snapshot()
    kpis = compute_dashboard_kpis(filter_by_series(full, ["EQ"]), total_rows=len(full))
    assert _kpi(kpis, "Stocks shown") == "2"
    assert "4" in next(h for label, _, h in kpis if label == "Stocks shown")


def test_kpi_values():
    kpis = compute_dashboard_kpis(_snapshot(), total_rows=4)
    assert _kpi(kpis, "Advancing") == "1"
    assert _kpi(kpis, "Declining") == "1"
    assert _kpi(kpis, "Mean change") == "+0.33%"
    assert _kpi(kpis, "Median RSI") == "50.0"
    assert _kpi(kpis, "Above SMA 200") == "67%"  # 2 of the 3 stocks with a SMA 200
    assert _kpi(kpis, "Near 52W high") == "2"  # -2% and -5% are within 5%
    assert _kpi(kpis, "Volume") == "2"  # 2.0x and 1.5x


def test_kpis_on_empty_frame_show_dashes_not_crash():
    kpis = compute_dashboard_kpis(pd.DataFrame(), total_rows=0)
    assert _kpi(kpis, "Stocks shown") == "0"
    assert _kpi(kpis, "Mean change") == "—"
    assert _kpi(kpis, "Above SMA 200") == "—"


def test_presets_only_use_known_column_names():
    known = set(COLUMN_NAMES.values())
    for name, cols in COLUMN_PRESETS.items():
        assert set(cols) <= known, name


def test_rename_for_display_has_no_market_cap():
    df = _snapshot().assign(market_cap=1.0)
    assert "market_cap" not in rename_for_display(df).columns
    assert "Symbol" in rename_for_display(df).columns


def test_color_change():
    assert "10b981" in color_change(1.5)
    assert "f43f5e" in color_change(-0.1)
    assert color_change(0.0) == ""
    assert color_change(np.nan) == ""
    assert color_change("text") == ""


def test_kpis_with_missing_columns_show_dashes_not_crash():
    """Regression: a snapshot lacking sma_200 / 52W / volume columns must not raise."""
    df = _snapshot()[["symbol", "close", "pct_change_daily"]]
    kpis = compute_dashboard_kpis(df, total_rows=len(df))
    assert _kpi(kpis, "Stocks shown") == "4"
    assert _kpi(kpis, "Above SMA 200") == "—"
    assert _kpi(kpis, "Near 52W high") == "—"
    assert _kpi(kpis, "Volume") == "—"
    assert _kpi(kpis, "Median RSI") == "—"
