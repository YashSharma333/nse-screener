"""
Unit tests for the plain indicator functions (CL-23).

Each indicator is one function: ``sma(df, length=20, source="close")`` and so on. It takes the
whole price table (many stocks at once), works inside each stock, and returns one Series aligned
to the table. Tests use small hand-made tables so every expected number can be checked by hand.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.computation.indicators.calculator import (
    add_all_indicators,
    atr,
    bollinger_bands,
    cross_sectional_rank,
    crossed_above,
    crossed_below,
    ema,
    macd,
    occurred_within,
    percent_change,
    relative_strength,
    rs_line,
    rsi,
    sma,
    true_range,
    volatility,
    wma,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _flat_df(n: int, close: float = 100.0, volume: float = 300_000.0) -> pd.DataFrame:
    """One stock (no symbol column), n rows, constant OHLCV."""
    dates = pd.date_range(end="2024-06-30", periods=n, freq="B")
    return pd.DataFrame(
        {
            "date": dates,
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": float(close),
            "volume": float(volume),
        }
    )


def _two_stocks(n: int = 30) -> pd.DataFrame:
    """Stock AAA rises 1..n, stock BBB rises 1000..1000+n-1; rows sorted by symbol, date."""
    frames = []
    for symbol, start in (("AAA", 1.0), ("BBB", 1000.0)):
        closes = [start + i for i in range(n)]
        frames.append(
            pd.DataFrame(
                {
                    "symbol": symbol,
                    "date": pd.date_range(end="2024-06-30", periods=n, freq="B"),
                    "open": closes,
                    "high": [c + 1 for c in closes],
                    "low": [c - 1 for c in closes],
                    "close": closes,
                    "volume": [c * 10 for c in closes],
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# Moving averages: any length, any source column
# ---------------------------------------------------------------------------
def test_sma_default_length_is_20() -> None:
    df = _flat_df(30)
    pd.testing.assert_series_equal(sma(df), sma(df, length=20))
    assert pd.isna(sma(df).iloc[18]) and not pd.isna(sma(df).iloc[19])


def test_sma_custom_length_is_the_hand_computed_mean() -> None:
    df = _two_stocks(60)
    result = sma(df, length=50)
    stock = df[df["symbol"] == "AAA"]["close"].reset_index(drop=True)
    assert result.iloc[49] == pytest.approx(stock.iloc[:50].mean())
    assert result.iloc[59] == pytest.approx(stock.iloc[10:60].mean())
    assert result.iloc[:49].isna().all()


def test_sma_of_volume_uses_the_same_function() -> None:
    df = _flat_df(25)
    df["volume"] = [float(i + 1) for i in range(25)]
    result = sma(df, length=20, source="volume")
    assert result.iloc[19] == pytest.approx(10.5)  # mean of 1..20
    assert result.iloc[24] == pytest.approx(15.5)  # mean of 6..25


def test_sma_of_an_indicator_column_by_name() -> None:
    """An indicator is passed in by column name: SMA of an EMA column."""
    df = _flat_df(60)
    df["ema_10"] = ema(df, length=10)
    result = sma(df, length=5, source="ema_10")
    assert result.iloc[13] == pytest.approx(df["ema_10"].iloc[9:14].mean())


@pytest.mark.parametrize("function", [sma, ema, wma, rsi, volatility, percent_change])
def test_stocks_never_leak_into_each_other(function) -> None:
    """The second stock's first rows are NaN, not the first stock's last values."""
    df = _two_stocks(30)
    result = function(df, length=5)
    second = result[df["symbol"] == "BBB"]
    assert second.iloc[:4].isna().all()


def test_percent_change_default_is_one_day_in_percent() -> None:
    df = _two_stocks(5)
    result = percent_change(df)
    assert result.iloc[1] == pytest.approx((2.0 - 1.0) / 1.0 * 100.0)
    assert pd.isna(result.iloc[5])  # first row of BBB


def test_percent_change_21_days_on_a_ramp() -> None:
    n = 50
    closes = [100.0 * (1 + 0.005 * i) for i in range(n)]
    df = _flat_df(n)
    df["close"] = closes
    result = percent_change(df, length=21)
    assert pd.isna(result.iloc[20]) and not pd.isna(result.iloc[21])
    assert result.iloc[21] == pytest.approx((closes[21] - closes[0]) / closes[0] * 100)


def test_ema_wma_known_values() -> None:
    df = _flat_df(10)
    df["close"] = [float(i + 1) for i in range(10)]
    assert pd.isna(ema(df, length=3).iloc[1])
    assert wma(df, length=3).iloc[2] == pytest.approx((1 * 1 + 2 * 2 + 3 * 3) / 6)


def test_bad_length_and_missing_column_are_clear_errors() -> None:
    df = _flat_df(30)
    with pytest.raises(ValueError, match="length"):
        sma(df, length=0)
    with pytest.raises(KeyError, match="nonexistent"):
        sma(df, source="nonexistent")


def test_length_longer_than_the_data_gives_nan_not_a_crash() -> None:
    assert sma(_flat_df(10), length=50).isna().all()


# ---------------------------------------------------------------------------
# Multi-output indicators: one Series each, chosen with output=
# ---------------------------------------------------------------------------
def test_bollinger_outputs() -> None:
    df = _flat_df(30)
    df["close"] = [100.0 + (i % 5) for i in range(30)]
    middle = bollinger_bands(df, output="middle")
    upper = bollinger_bands(df, output="upper")
    lower = bollinger_bands(df, length=20, std=2.0, output="lower")
    window = df["close"].iloc[:20]
    assert middle.iloc[19] == pytest.approx(window.mean())
    assert upper.iloc[19] == pytest.approx(window.mean() + 2 * window.std())
    assert lower.iloc[19] == pytest.approx(window.mean() - 2 * window.std())
    with pytest.raises(ValueError, match="output"):
        bollinger_bands(df, output="side")


def test_macd_outputs_are_consistent() -> None:
    df = _flat_df(80)
    df["close"] = [100.0 + np.sin(i / 5.0) * 5 for i in range(80)]
    line = macd(df, output="line")
    signal = macd(df, output="signal")
    hist = macd(df, output="histogram")
    valid = hist.dropna().index
    assert len(valid) > 0
    pd.testing.assert_series_equal((line - signal).loc[valid], hist.loc[valid])
    with pytest.raises(ValueError, match="output"):
        macd(df, output="x")


def test_true_range_and_atr() -> None:
    df = _flat_df(30)
    tr = true_range(df)
    assert tr.iloc[1] == pytest.approx(2.0)  # high - low = 2, no gap
    assert atr(df, length=14).iloc[13] == pytest.approx(2.0)
    assert atr(df, output="percent").iloc[13] == pytest.approx(2.0)  # 2 / close 100 * 100
    assert pd.isna(atr(df, length=14).iloc[12])


# ---------------------------------------------------------------------------
# RSI
# ---------------------------------------------------------------------------
def test_rsi_all_gains_is_100_and_all_losses_is_0() -> None:
    up = _flat_df(50)
    up["close"] = [float(100 + i) for i in range(50)]
    down = _flat_df(50)
    down["close"] = [float(100 - i) for i in range(50)]
    assert (rsi(up, length=14).dropna() > 99.0).all()
    assert (rsi(down, length=14).dropna() < 1.0).all()


def test_rsi_nan_boundary() -> None:
    """One diff plus a 14-row window: the first valid value is at index 14."""
    df = _flat_df(20)
    df["close"] = [100.0 + (i % 3) for i in range(20)]
    result = rsi(df, length=14)
    assert pd.isna(result.iloc[13]) and not pd.isna(result.iloc[14])


# ---------------------------------------------------------------------------
# Crossovers: one pair of functions for golden, death and MACD crosses
# ---------------------------------------------------------------------------
def test_crossed_above_and_below_fire_on_the_crossing_row_only() -> None:
    df = pd.DataFrame(
        {
            "symbol": "AAA",
            "fast": [1.0, 2.0, 4.0, 5.0, 3.0, 2.0],
            "slow": [3.0, 3.0, 3.0, 3.0, 3.0, 3.0],
        }
    )
    assert list(crossed_above(df, "fast", "slow")) == [False, False, True, False, False, False]
    assert list(crossed_below(df, "fast", "slow")) == [False, False, False, False, False, True]


def test_crossover_does_not_use_the_previous_stock_row() -> None:
    """Stock 2's first row must not be compared with stock 1's last row."""
    df = pd.DataFrame(
        {
            "symbol": ["AAA", "AAA", "BBB", "BBB"],
            "fast": [1.0, 1.0, 5.0, 5.0],
            "slow": [3.0, 3.0, 3.0, 3.0],
        }
    )
    assert not crossed_above(df, "fast", "slow").iloc[2]


def test_occurred_within_looks_back_inside_each_stock() -> None:
    df = pd.DataFrame(
        {
            "symbol": ["AAA"] * 5 + ["BBB"] * 3,
            "flag": [False, True, False, False, False, False, False, False],
        }
    )
    result = occurred_within(df, "flag", length=3)
    assert list(result[:5]) == [False, True, True, True, False]
    assert not result[5:].any()  # the cross in AAA is not seen by BBB


# ---------------------------------------------------------------------------
# Benchmark based: relative strength, RS line, cross-sectional rank
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("as_date", [True, False])
def test_relative_strength_flat_and_falling_benchmark(as_date: bool) -> None:
    """(stock ratio) / (benchmark ratio) - 1, with python-date or datetime benchmark dates."""
    n = 70
    dates = pd.date_range(end="2024-06-30", periods=n, freq="B")
    bench_dates = dates.date if as_date else dates
    stock = _flat_df(n)
    stock["date"] = dates.date
    stock["close"] = [100.0 if i < 63 else 110.0 for i in range(n)]

    flat = pd.DataFrame({"date": bench_dates, "close": 20000.0})
    assert relative_strength(stock, flat, length=63).iloc[-1] == pytest.approx(0.10, abs=1e-4)

    falling = pd.DataFrame({"date": bench_dates, "close": [20000.0] * 63 + [19000.0] * 7})
    flat_stock = _flat_df(n)
    flat_stock["date"] = dates.date
    rs_value = relative_strength(flat_stock, falling, length=63).iloc[63]
    assert rs_value == pytest.approx(1.0 / 0.95 - 1.0, abs=1e-4)


def test_relative_strength_without_a_benchmark_is_nan() -> None:
    assert relative_strength(_flat_df(30), None).isna().all()
    assert rs_line(_flat_df(30), pd.DataFrame(columns=["date", "close"])).isna().all()


def test_rs_line_is_stock_close_over_benchmark_close() -> None:
    df = _two_stocks(5)
    df["date"] = df["date"].dt.date
    bench = pd.DataFrame({"date": df["date"].unique(), "close": [10.0, 20.0, 40.0, 80.0, 160.0]})
    result = rs_line(df, bench)
    assert result.iloc[2] == pytest.approx(3.0 / 40.0)
    assert result.iloc[7] == pytest.approx(1002.0 / 40.0)


def test_cross_sectional_rank_is_per_date_percentile() -> None:
    df = pd.DataFrame(
        {
            "symbol": ["A", "B", "C", "A", "B", "C"],
            "date": ["d1"] * 3 + ["d2"] * 3,
            "ret": [1.0, 2.0, 3.0, 3.0, 2.0, 1.0],
        }
    )
    result = cross_sectional_rank(df, "ret")
    assert result.iloc[2] == pytest.approx(100.0)
    assert result.iloc[0] == pytest.approx(100.0 / 3)
    assert result.iloc[3] == pytest.approx(100.0)  # A is top on d2


# ---------------------------------------------------------------------------
# The temporary add_all_indicators (same columns as before; deleted in CL-25)
# ---------------------------------------------------------------------------
EXPECTED_COLUMNS = [
    "sma_20",
    "sma_50",
    "sma_150",
    "sma_200",
    "sma_200_slope_21d",
    "ema_12",
    "ema_20",
    "ema_26",
    "rsi_9",
    "rsi_14",
    "rsi_9_wma_21",
    "high_52w",
    "low_52w",
    "high_252d_prev",
    "bb_upper",
    "bb_middle",
    "bb_lower",
    "bb_width",
    "macd_line",
    "macd_signal",
    "macd_histogram",
    "daily_returns",
    "volatility_20",
    "volume_sma_20",
    "volume_ratio",
    "vol_dryup",
    "vol_multiple",
    "pct_from_52w_high",
    "pct_change_daily",
    "pct_change_1m",
    "pct_change_3m",
    "pct_change_6m",
    "pct_change_12m",
    "close_60d",
    "close_120d",
    "close_180d",
    "close_252d",
    "return_60d",
    "return_120d",
    "atr_14",
    "atr_pct",
    "atr_pct_max_60d",
    "golden_cross",
    "golden_cross_10d",
    "death_cross",
    "macd_cross_up",
    "macd_cross_down",
    "rs_63d",
    "rs_126d",
    "rs_252d",
    "rsi_momentum",
    "ret_rank_126d",
    "ret_rank_252d",
]


def test_add_all_indicators_has_every_expected_column() -> None:
    result = add_all_indicators(_flat_df(300))
    assert [c for c in EXPECTED_COLUMNS if c not in result.columns] == []


def test_add_all_indicators_handles_empty_and_single_row() -> None:
    empty = pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    assert len(add_all_indicators(empty)) == 0
    assert len(add_all_indicators(_flat_df(1))) == 1


def test_add_all_indicators_multi_stock_columns_and_isolation() -> None:
    a = _flat_df(60, close=100.0)
    a["symbol"] = "SYM1"
    b = _flat_df(60, close=50.0)
    b["symbol"] = "SYM2"
    result = add_all_indicators(pd.concat([a, b], ignore_index=True))
    assert [c for c in EXPECTED_COLUMNS if c not in result.columns] == []
    first_b = result[result["symbol"] == "SYM2"].index[0]
    assert not result.loc[first_b, "golden_cross"]  # not compared with SYM1's last row


def test_shifted_closes_come_from_the_same_stock() -> None:
    df = _two_stocks(80)
    result = add_all_indicators(df)
    second = result[result["symbol"] == "BBB"].reset_index(drop=True)
    assert second["close_60d"].iloc[:60].isna().all()
    assert second["close_60d"].iloc[60] == pytest.approx(1000.0)  # BBB's own first close


def test_rsi_momentum_is_a_plain_bool_on_a_trending_table() -> None:
    n = 60
    df = _flat_df(n)
    df["close"] = [90.0 + i * 2.0 for i in range(n)]
    df["volume"] = [300_000.0 if i == n - 1 else 100_000.0 for i in range(n)]
    result = add_all_indicators(df)
    assert not result["atr_14"].dropna().empty
    assert isinstance(result["rsi_momentum"].iloc[-1], (bool, np.bool_))


def test_relative_strength_columns_for_every_stock_after_misaligned_concat() -> None:
    """Three stocks concatenated with a fresh index all get rs_63d after the lookback."""
    n = 100
    dates = pd.date_range(end="2024-06-30", periods=n, freq="B").date
    bench = pd.DataFrame({"date": dates, "close": [20000.0 + i * 10.0 for i in range(n)]})
    frames = [
        pd.DataFrame(
            {
                "date": dates,
                "symbol": symbol,
                "open": 100.0,
                "high": 105.0,
                "low": 95.0,
                "close": [100.0 + float(i) for i in range(n)],
                "volume": 300_000.0,
            }
        )
        for symbol in ("SYM1", "SYM2", "SYM3")
    ]
    result = add_all_indicators(pd.concat(frames, ignore_index=True), benchmark_df=bench)
    for symbol in ("SYM1", "SYM2", "SYM3"):
        assert result[result["symbol"] == symbol]["rs_63d"].notna().sum() == n - 63
