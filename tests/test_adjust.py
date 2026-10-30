"""Unit tests for corporate actions adjustment engine (src/etl/adjust.py)."""

from datetime import date, timedelta

import pandas as pd

from src.etl.adjust import cumulative_factors, detect_events


def _build_series(
    prices: list[float], prev_closes: list[float], symbol: str = "ABC"
) -> pd.DataFrame:
    base_date = date(2024, 1, 1)
    dates = [base_date + timedelta(days=i) for i in range(len(prices))]
    return pd.DataFrame(
        {
            "symbol": symbol,
            "date": dates,
            "close": prices,
            "prev_close": prev_closes,
        }
    )


def test_detect_split_1_to_5():
    """A 1:5 stock split: close=1000, next day prev_close=200, close=205."""
    df = _build_series([1000.0, 205.0], [1000.0, 200.0])
    events = detect_events(df)
    assert len(events) == 1
    ev = events.iloc[0]
    assert ev["ratio_kind"] == "material"
    assert abs(ev["ratio"] - 0.2) < 1e-4


def test_detect_bonus_1_to_1():
    """A 1:1 bonus issue: close=500, next day prev_close=250, close=255."""
    df = _build_series([500.0, 255.0], [500.0, 250.0])
    events = detect_events(df)
    assert len(events) == 1
    ev = events.iloc[0]
    assert ev["ratio_kind"] == "material"
    assert abs(ev["ratio"] - 0.5) < 1e-4


def test_no_events_on_normal_trading():
    """Normal trading: prev_close equals prior close."""
    df = _build_series([100.0, 102.0, 101.0], [100.0, 100.0, 102.0])
    events = detect_events(df)
    assert len(events) == 0


def test_data_gap_skipped():
    """Trading gap > 7 days must be skipped to avoid false corporate actions."""
    d1 = date(2024, 1, 1)
    d2 = date(2024, 1, 20)  # 19 day gap
    df = pd.DataFrame(
        {
            "symbol": ["ABC", "ABC"],
            "date": [d1, d2],
            "close": [100.0, 50.0],
            "prev_close": [100.0, 50.0],
        }
    )
    events = detect_events(df)
    assert len(events) == 0


def test_minor_event_not_material():
    """Small dividend adjustment of 0.5% (ratio 0.995) flagged as minor."""
    df = _build_series([100.0, 100.0], [100.0, 99.5])
    events = detect_events(df)
    assert len(events) == 1
    assert events.iloc[0]["ratio_kind"] == "minor"


def test_cumulative_factors_two_events():
    """Two material splits: 1:2 on day 10 (factor 0.5) and 1:2 on day 20 (factor 0.5)."""
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(30)]
    events_df = pd.DataFrame(
        [
            {"date": date(2024, 1, 11), "factor": 0.5, "ratio_kind": "material"},
            {"date": date(2024, 1, 21), "factor": 0.5, "ratio_kind": "material"},
        ]
    )
    cum_factors = cumulative_factors(dates, events_df)

    # Before both events (days 0..9): factor = 0.5 * 0.5 = 0.25
    assert abs(cum_factors[0] - 0.25) < 1e-4
    assert abs(cum_factors[9] - 0.25) < 1e-4

    # Between event 1 and event 2 (days 10..19): factor = 0.5
    assert abs(cum_factors[10] - 0.5) < 1e-4
    assert abs(cum_factors[19] - 0.5) < 1e-4

    # On or after event 2 (day 20 onward): factor = 1.0
    assert abs(cum_factors[20] - 1.0) < 1e-4
    assert abs(cum_factors[29] - 1.0) < 1e-4
