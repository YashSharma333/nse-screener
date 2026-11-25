"""Tests for src/computation/indicators/price_helpers.py (CL-23B).

``window_stat`` answers "the highest / lowest / average / sum / count of SOURCE over the last N
rows, optionally ending D rows ago", for every stock at once. 52-week high, 20-day low and the
average volume all come from this one function.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.computation.indicators.calculator import sma
from src.computation.indicators.price_helpers import absolute, pct_diff, ratio, window_stat


def _table() -> pd.DataFrame:
    """Three stocks: A (10 rows, high 1..10), B (10 rows, high 100..109), C (3 rows)."""
    frames = []
    for symbol, highs in (
        ("A", [float(i) for i in range(1, 11)]),
        ("B", [float(i) for i in range(100, 110)]),
        ("C", [5.0, 6.0, 7.0]),
    ):
        n = len(highs)
        frames.append(
            pd.DataFrame(
                {
                    "symbol": symbol,
                    "date": pd.date_range(end="2024-06-28", periods=n, freq="B"),
                    "high": highs,
                    "low": [h - 1 for h in highs],
                    "close": [h - 0.5 for h in highs],
                    "volume": [h * 100 for h in highs],
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def _stock(result: pd.Series, df: pd.DataFrame, symbol: str) -> list[float]:
    return result[df["symbol"] == symbol].tolist()


# ---------------------------------------------------------------------------
# max / min / mean / sum
# ---------------------------------------------------------------------------
def test_max_min_mean_sum_over_three_rows() -> None:
    df = _table()
    a_high = [float(i) for i in range(1, 11)]
    top = _stock(window_stat(df, "high", 3, "max"), df, "A")
    assert np.isnan(top[0]) and np.isnan(top[1])
    assert top[2:] == a_high[2:]  # rising series: the newest value is the maximum
    assert _stock(window_stat(df, "high", 3, "min"), df, "A")[2:] == a_high[:8]
    assert _stock(window_stat(df, "high", 3, "mean"), df, "A")[2:] == pytest.approx(
        [(a_high[i] + a_high[i + 1] + a_high[i + 2]) / 3 for i in range(8)]
    )
    assert _stock(window_stat(df, "high", 3, "sum"), df, "A")[9] == 8 + 9 + 10


def test_defaults_are_the_52_week_high() -> None:
    """window_stat(df) is high / 252 rows / max: the 52-week high."""
    df = _table()
    pd.testing.assert_series_equal(window_stat(df), window_stat(df, "high", 252, "max"))
    assert window_stat(df).isna().all()  # no stock here has 252 rows


# ---------------------------------------------------------------------------
# days_ago: the window ends that many rows ago
# ---------------------------------------------------------------------------
def test_days_ago_one_is_the_window_ending_yesterday() -> None:
    df = _table()
    yesterday = window_stat(df, "high", 3, "max", days_ago=1)
    a = _stock(yesterday, df, "A")
    assert all(np.isnan(a[:3]))
    assert a[3] == 3.0 and a[9] == 9.0  # the window 7..9 ends the row before today


def test_days_ago_equals_the_result_shifted_inside_each_stock() -> None:
    df = _table()
    today = window_stat(df, "high", 3, "max")
    for days in (1, 2):
        expected = today.groupby(df["symbol"]).shift(days)
        pd.testing.assert_series_equal(window_stat(df, "high", 3, "max", days_ago=days), expected)


# ---------------------------------------------------------------------------
# Strict window, no leaks between stocks, any column
# ---------------------------------------------------------------------------
def test_window_longer_than_the_data_gives_nan() -> None:
    df = _table()
    assert window_stat(df, "high", 5, "max")[df["symbol"] == "C"].isna().all()
    assert window_stat(df, "high", 50, "max").isna().all()


def test_a_stock_never_sees_another_stocks_rows() -> None:
    df = _table()
    result = window_stat(df, "high", 4, "max")
    b = _stock(result, df, "B")
    assert all(np.isnan(b[:3]))  # not A's 10.0
    assert b[3] == 103.0
    assert (
        _stock(window_stat(df, "high", 2, "max", days_ago=1), df, "B")[:2]
        == [pytest.approx(np.nan, nan_ok=True)] * 2
    )


def test_volume_works_like_any_column() -> None:
    df = _table()
    average = _stock(window_stat(df, "volume", 3, "mean"), df, "A")
    assert average[2] == pytest.approx((100 + 200 + 300) / 3)


def test_an_indicator_column_can_be_the_source() -> None:
    """Indicators stay in their own functions and are passed in by column name."""
    df = _table()
    df["sma_3"] = sma(df, length=3, source="high")
    highest = _stock(window_stat(df, "sma_3", 2, "max"), df, "A")
    assert highest[3] == pytest.approx(3.0)  # max(sma at row 2 = 2.0, sma at row 3 = 3.0)


def test_20_40_and_252_day_highs_come_from_the_same_function() -> None:
    rng = np.random.default_rng(3)
    highs = rng.uniform(50, 150, 300)
    df = pd.DataFrame({"symbol": "X", "high": highs})
    for length in (20, 40, 252):
        result = window_stat(df, "high", length, "max")
        assert result.iloc[length - 1] == pytest.approx(highs[:length].max())
        assert result.iloc[299] == pytest.approx(highs[300 - length :].max())
        assert result.iloc[: length - 1].isna().all()


def test_a_table_without_a_symbol_column_is_one_stock() -> None:
    df = pd.DataFrame({"high": [1.0, 3.0, 2.0, 5.0]})
    assert window_stat(df, "high", 2, "max").tolist()[1:] == [3.0, 3.0, 5.0]


# ---------------------------------------------------------------------------
# stat="count": how many of the last N days a true/false column was true
# ---------------------------------------------------------------------------
def _flags() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["A"] * 10 + ["B"] * 6,
            "up": [True, False, True, True, False, True, True, True, False, True] + [True] * 6,
        }
    )


def test_count_over_five_days() -> None:
    df = _flags()
    result = window_stat(df, "up", 5, "count")
    a = _stock(result, df, "A")
    assert all(np.isnan(a[:4]))
    # windows rows 0-4, 1-5, 2-6, 3-7, 4-8, 5-9 hold 3, 3, 4, 4, 3, 4 trues
    assert a[4:] == [3, 3, 4, 4, 3, 4]


def test_count_does_not_leak_between_stocks() -> None:
    df = _flags()
    b = _stock(window_stat(df, "up", 5, "count"), df, "B")
    assert all(np.isnan(b[:4]))
    assert b[4:] == [5, 5]


def test_count_with_days_ago_and_strict_window() -> None:
    df = _flags()
    a = _stock(window_stat(df, "up", 5, "count", days_ago=1), df, "A")
    assert all(np.isnan(a[:5]))
    assert a[5] == 3  # the window rows 0-4 (3 trues) ends the row before row 5
    assert a[9] == 3  # rows 4-8 hold 3 trues
    assert window_stat(df, "up", 8, "count")[df["symbol"] == "B"].isna().all()


def test_count_needs_a_true_false_column() -> None:
    df = _table()
    with pytest.raises(ValueError, match="bool"):
        window_stat(df, "high", 3, "count")


# ---------------------------------------------------------------------------
# Bad arguments are clear errors
# ---------------------------------------------------------------------------
def test_bad_arguments_raise() -> None:
    df = _table()
    with pytest.raises(ValueError, match="stat"):
        window_stat(df, "high", 3, "median")
    with pytest.raises(ValueError, match="length"):
        window_stat(df, "high", 0, "max")
    with pytest.raises(ValueError, match="days_ago"):
        window_stat(df, "high", 3, "max", days_ago=-1)
    with pytest.raises(KeyError, match="nonexistent"):
        window_stat(df, "nonexistent", 3, "max")


# ---------------------------------------------------------------------------
# pct_diff, ratio, absolute
# ---------------------------------------------------------------------------
def test_pct_diff_is_percent_of_b_and_zero_b_is_nan() -> None:
    a = pd.Series([110.0, 90.0, 5.0])
    b = pd.Series([100.0, 100.0, 0.0])
    result = pct_diff(a, b)
    assert result.iloc[0] == pytest.approx(10.0)
    assert result.iloc[1] == pytest.approx(-10.0)
    assert np.isnan(result.iloc[2])  # never inf


def test_ratio_and_zero_denominator() -> None:
    result = ratio(pd.Series([300.0, 5.0]), pd.Series([200.0, 0.0]))
    assert result.iloc[0] == pytest.approx(1.5)
    assert np.isnan(result.iloc[1])


def test_absolute_of_negative_zero_and_nan() -> None:
    result = absolute(pd.Series([-2.5, 0.0, np.nan, 3.0]))
    assert result.iloc[0] == 2.5 and result.iloc[1] == 0.0 and result.iloc[3] == 3.0
    assert np.isnan(result.iloc[2])


def test_the_volume_multiple_is_a_ratio_of_two_columns() -> None:
    """vol_multiple = ratio(volume, average volume): built from the pieces, no special file."""
    df = _table()
    average = window_stat(df, "volume", 3, "mean")
    multiple = ratio(df["volume"], average)
    assert _stock(multiple, df, "A")[2] == pytest.approx(300 / 200)
