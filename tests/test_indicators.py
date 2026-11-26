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
    atr,
    bollinger_bands,
    ema,
    macd,
    percent_change,
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


def _wilder_by_hand(df: pd.DataFrame, length: int) -> list[float]:
    """ATR by the textbook Wilder recursion: a = a * (n - 1) / n + true_range / n."""
    values: list[float] = []
    previous_close = None
    average = None
    for row in df.itertuples():
        if previous_close is None:
            tr = row.high - row.low
        else:
            tr = max(
                row.high - row.low,
                abs(row.high - previous_close),
                abs(row.low - previous_close),
            )
        average = tr if average is None else average * (length - 1) / length + tr / length
        values.append(average)
        previous_close = row.close
    return values


def _gappy_stock(n: int = 40, seed: int = 7) -> pd.DataFrame:
    """One stock with changing daily ranges and gaps, so true range is not constant."""
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 2, n))
    high = close + rng.uniform(0.5, 3.0, n)
    low = close - rng.uniform(0.5, 3.0, n)
    return pd.DataFrame(
        {
            "date": pd.date_range(end="2024-06-30", periods=n, freq="B"),
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1000.0,
        }
    )


def test_atr_is_wilder_smoothing_like_rsi() -> None:
    """ATR must follow Wilder (alpha = 1/length), the same smoothing RSI uses."""
    df = _gappy_stock()
    expected = pd.Series(_wilder_by_hand(df, 14))
    result = atr(df, length=14)
    assert result.iloc[:13].isna().all()  # nothing before the full window
    np.testing.assert_allclose(result.iloc[13:], expected.iloc[13:], rtol=1e-10)


def test_atr_never_mixes_stocks() -> None:
    """Each stock's ATR equals the ATR of that stock alone (shifts and smoothing are per stock)."""
    first, second = _gappy_stock(seed=1), _gappy_stock(seed=2)
    both = pd.concat([first.assign(symbol="AAA"), second.assign(symbol="BBB")], ignore_index=True)
    result = atr(both, length=14)
    np.testing.assert_allclose(result.iloc[:40], atr(first, length=14), rtol=1e-10)
    np.testing.assert_allclose(result.iloc[40:], atr(second, length=14), rtol=1e-10)


def test_atr_is_nan_when_history_is_shorter_than_the_window() -> None:
    assert atr(_gappy_stock(n=10), length=14).isna().all()


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
