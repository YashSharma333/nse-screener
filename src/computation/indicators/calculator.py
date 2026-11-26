"""
Indicator functions (CL-23)
===========================
One plain function per indicator, for the whole price table at once::

    sma(df, length=20, source="close")     # 20-day average of the close, every stock
    sma(df, length=50)                     # the 50-day average
    sma(df, length=20, source="volume")    # the 20-day average of the volume

Rules every function follows:

* ``df`` has the columns ``date``, ``open``, ``high``, ``low``, ``close``, ``volume`` and
  (for many stocks) ``symbol``. A table without ``symbol`` is one stock. Rows must be sorted by
  symbol, then date: the functions never sort.
* Everything that looks back (rolling, ewm, shift) runs inside each stock (``groupby``), so one
  stock never sees another stock's rows.
* The result is one Series aligned to ``df.index``. Fewer rows than ``length`` gives NaN.
* The default numbers live ONLY in the function signatures. A window is always ``length``; the
  column to work on is always ``source`` (a column name; an indicator column works too).
* A missing column raises ``KeyError``; a ``length`` below 1 raises ``ValueError``.
* A function that has several results (Bollinger bands, MACD, ATR) takes ``output=`` and still
  returns one Series.

The cache saves only the indicators in ``settings.PRECOMPUTED_INDICATORS``; every other one is
computed through the resolver (``resolver.py``) when it is asked for.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from src.computation.indicators.grouped import (
    check_length,
    check_output,
    ewm,
    in_table_order,
    require,
    roll,
    shift,
    stock_keys,
)

logger = logging.getLogger("src.computation.indicators")

# The ``output=`` choices of the multi-output indicators, written once: the functions check
# against it and the resolver lists it for the UI.
OUTPUT_CHOICES = {
    "bollinger_bands": ("upper", "middle", "lower"),
    "atr": ("value", "percent"),
    "macd": ("line", "signal", "histogram"),
}

# Bumped schema version whenever columns or formulas change
INDICATORS_SCHEMA_VERSION = 6


# ===========================================================================
# Shared helpers (the one place for the checks and the "inside each stock" logic)
# ===========================================================================


def _fraction_change(df: pd.DataFrame, source: str, length: int) -> pd.Series:
    """(value / value ``length`` rows ago) - 1, inside each stock."""
    values = df[source].astype(float)
    return values / shift(df, values, length) - 1.0


# ===========================================================================
# Moving averages
# ===========================================================================


def sma(df: pd.DataFrame, length: int = 20, source: str = "close") -> pd.Series:
    """Simple moving average of ``source`` over ``length`` rows."""
    check_length(length)
    require(df, source)
    return roll(df, df[source], length, "mean")


def ema(df: pd.DataFrame, length: int = 20, source: str = "close") -> pd.Series:
    """Exponential moving average (span = ``length``)."""
    check_length(length)
    require(df, source)
    return ewm(df, df[source], span=length, adjust=False, min_periods=length)


def wma(df: pd.DataFrame, length: int = 20, source: str = "close") -> pd.Series:
    """Weighted moving average with linear weights (the newest row weighs most)."""
    check_length(length)
    require(df, source)
    weights = np.arange(1, length + 1)
    total = weights.sum()

    def weighted_mean(window: np.ndarray) -> float:
        return float(np.dot(window, weights) / total)

    window = df[source].astype(float).groupby(stock_keys(df), sort=False)
    rolled = window.rolling(length, min_periods=length).apply(weighted_mean, raw=True)
    return in_table_order(df, rolled)


def bollinger_bands(
    df: pd.DataFrame,
    length: int = 20,
    std: float = 2.0,
    source: str = "close",
    output: str = "upper",
) -> pd.Series:
    """One Bollinger band: ``output`` is "upper", "middle" or "lower"."""
    check_length(length)
    check_output(output, OUTPUT_CHOICES["bollinger_bands"])
    require(df, source)
    middle = roll(df, df[source], length, "mean")
    if output == "middle":
        return middle
    deviation = roll(df, df[source], length, "std")
    if output == "upper":
        return middle + (deviation * std)
    return middle - (deviation * std)


# ===========================================================================
# Range and volatility
# ===========================================================================


def true_range(df: pd.DataFrame) -> pd.Series:
    """True range: the largest of high-low, |high-previous close|, |low-previous close|."""
    require(df, "high", "low", "close")
    previous_close = shift(df, df["close"], 1)
    spans = [
        df["high"] - df["low"],
        (df["high"] - previous_close).abs(),
        (df["low"] - previous_close).abs(),
    ]
    return pd.concat(spans, axis=1).max(axis=1)


def atr(df: pd.DataFrame, length: int = 14, output: str = "value") -> pd.Series:
    """Average true range, Wilder smoothing (alpha = 1/length, like RSI) so it matches charting
    platforms; ``output`` is "value" or "percent" (of the close)."""
    check_length(length)
    check_output(output, OUTPUT_CHOICES["atr"])
    average = ewm(df, true_range(df), alpha=1.0 / length, adjust=False, min_periods=length)
    if output == "value":
        return average
    return (average / df["close"].replace(0, np.nan)) * 100.0


def volatility(df: pd.DataFrame, length: int = 20, source: str = "close") -> pd.Series:
    """Standard deviation of the 1-day fractional change of ``source`` over ``length`` rows."""
    check_length(length)
    require(df, source)
    daily = _fraction_change(df, source, 1)
    return roll(df, daily, length, "std")


def percent_change(df: pd.DataFrame, length: int = 1, source: str = "close") -> pd.Series:
    """Percent change of ``source`` over ``length`` rows (10.0 means +10%)."""
    check_length(length)
    require(df, source)
    return _fraction_change(df, source, length) * 100.0


# ===========================================================================
# Momentum
# ===========================================================================


def rsi(df: pd.DataFrame, length: int = 14, source: str = "close") -> pd.Series:
    """Relative strength index with Wilder's smoothing."""
    check_length(length)
    require(df, source)
    values = df[source].astype(float)
    delta = values - shift(df, values, 1)
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    average_gain = ewm(df, gain, alpha=1.0 / length, min_periods=length, adjust=False)
    average_loss = ewm(df, loss, alpha=1.0 / length, min_periods=length, adjust=False)

    strength = average_gain / average_loss.replace(0, np.nan)
    result = 100.0 - (100.0 / (1.0 + strength))
    result = result.where(average_loss != 0, 100.0)
    return result.where(average_gain != 0, 0.0)


def macd(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
    source: str = "close",
    output: str = "line",
) -> pd.Series:
    """MACD; ``output`` is "line", "signal" or "histogram"."""
    for name, value in (("fast", fast), ("slow", slow), ("signal", signal)):
        check_length(value, name)
    check_output(output, OUTPUT_CHOICES["macd"])
    require(df, source)
    fast_average = ewm(df, df[source], span=fast, adjust=False, min_periods=fast)
    slow_average = ewm(df, df[source], span=slow, adjust=False, min_periods=slow)
    line = fast_average - slow_average
    if output == "line":
        return line
    signal_line = ewm(df, line, span=signal, adjust=False, min_periods=signal)
    if output == "signal":
        return signal_line
    return line - signal_line
