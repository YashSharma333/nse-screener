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

``add_all_indicators`` at the bottom is TEMPORARY: it builds today's cache columns from these
functions and is deleted in CL-25.
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
from src.computation.indicators.pickers import value_ago
from src.computation.indicators.price_helpers import pct_diff, ratio, window_stat

logger = logging.getLogger("src.computation.indicators")

# Bumped schema version whenever columns or formulas change
INDICATORS_SCHEMA_VERSION = 5


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
    check_output(output, ("upper", "middle", "lower"))
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
    """Average true range; ``output`` is "value" or "percent" (of the close)."""
    check_length(length)
    check_output(output, ("value", "percent"))
    average = ewm(df, true_range(df), span=length, adjust=False, min_periods=length)
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
    check_output(output, ("line", "signal", "histogram"))
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


# ===========================================================================
# Against a benchmark index
# ===========================================================================


def _benchmark_close(df: pd.DataFrame, benchmark: pd.DataFrame | None) -> pd.Series | None:
    """The benchmark's close for each row's date, or None when there is no benchmark."""
    if benchmark is None or benchmark.empty or "date" not in df.columns:
        return None
    bench_dates = pd.to_datetime(benchmark["date"]).dt.date
    stock_dates = df["date"]
    if pd.api.types.is_datetime64_any_dtype(stock_dates):
        stock_dates = stock_dates.dt.date
    by_date = dict(zip(bench_dates, benchmark["close"], strict=False))
    return stock_dates.map(by_date)


def relative_strength(
    df: pd.DataFrame, benchmark: pd.DataFrame | None, length: int = 63
) -> pd.Series:
    """Stock return ratio over the benchmark return ratio, minus 1 (0.10 = 10% ahead).

    ``benchmark`` has the columns ``date`` and ``close``; without one the result is NaN.
    """
    check_length(length)
    require(df, "close")
    bench_close = _benchmark_close(df, benchmark)
    if bench_close is None:
        return pd.Series(np.nan, index=df.index, dtype=float)
    stock_ratio = df["close"] / shift(df, df["close"], length)
    bench_ratio = bench_close / shift(df, bench_close, length)
    return (stock_ratio / bench_ratio.replace(0, np.nan)) - 1.0


def rs_line(df: pd.DataFrame, benchmark: pd.DataFrame | None) -> pd.Series:
    """The ratio line: stock close divided by the benchmark close on the same date."""
    require(df, "close")
    bench_close = _benchmark_close(df, benchmark)
    if bench_close is None:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return df["close"] / bench_close.replace(0, np.nan)


def cross_sectional_rank(df: pd.DataFrame, source: str, by: str = "date") -> pd.Series:
    """Percentile rank (0 to 100) of ``source`` among all stocks on the same date."""
    require(df, source, by)
    return df.groupby(by)[source].rank(pct=True) * 100.0


# ===========================================================================
# Crossovers (one pair of functions for golden, death and MACD crosses)
# ===========================================================================


def crossed_above(df: pd.DataFrame, a: str, b: str) -> pd.Series:
    """True on the row where column ``a`` moves from at-or-below ``b`` to above it."""
    require(df, a, b)
    return ((df[a] > df[b]) & (shift(df, df[a], 1) <= shift(df, df[b], 1))).astype(bool)


def crossed_below(df: pd.DataFrame, a: str, b: str) -> pd.Series:
    """True on the row where column ``a`` moves from at-or-above ``b`` to below it."""
    require(df, a, b)
    return ((df[a] < df[b]) & (shift(df, df[a], 1) >= shift(df, df[b], 1))).astype(bool)


def occurred_within(df: pd.DataFrame, source: str, length: int = 10) -> pd.Series:
    """True when the true/false column ``source`` was true in any of the last ``length`` rows."""
    check_length(length)
    require(df, source)
    recent = roll(df, df[source].astype(int), length, "max", min_periods=1)
    return recent > 0


# ===========================================================================
# TEMPORARY: today's cache columns, built from the functions above (deleted in CL-25)
# ===========================================================================


def _sort_order(df: pd.DataFrame) -> np.ndarray:
    """Row positions that sort the table by symbol, then date (date only for one stock)."""
    plain = df.reset_index(drop=True)
    by: list[str] = []
    if "symbol" in plain.columns and plain["symbol"].nunique() > 1:
        by.append("symbol")
    if "date" in plain.columns:
        by.append("date")
    if not by:
        return np.arange(len(plain))
    return plain.sort_values(by=by, kind="stable").index.to_numpy()


def _rsi_momentum(res: pd.DataFrame) -> pd.Series:
    """The composite breakout rule "RSI Momentum", from columns already in the table."""
    rsi_9 = res["rsi_9"]
    cond_a = (rsi_9 >= res["rsi_9_wma_21"] + 1.1) & (rsi_9 >= 45.0) & (rsi_9 <= 65.0)
    close = res["close"]
    cond_b = (
        (res["ema_20"] > res["sma_50"])
        & (res["sma_50"] > res["sma_200"])
        & (close > res["sma_50"])
        & (close > res["sma_200"])
    )
    cond_c = close >= (0.85 * res["high_252d_prev"])
    cond_d = (res["volume"] > 1.5 * res["volume_sma_20"]) & (res["volume_sma_20"] > 250_000)
    return (cond_a & cond_b & cond_c & cond_d).fillna(False)


def add_all_indicators(df: pd.DataFrame, benchmark_df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Add every indicator column of the cache to the table (TEMPORARY, deleted in CL-25).

    Works on a copy sorted by symbol and date and returns the rows in the input order. A stock
    with fewer than 2 rows gets empty indicator columns (as before).
    """
    if df.empty:
        return df.copy()

    order = _sort_order(df)
    table = df.iloc[order].reset_index(drop=True)
    res = table.copy()
    close, volume = res["close"], res["volume"]

    for length in (20, 50, 150, 200):
        res[f"sma_{length}"] = sma(res, length=length)
    res["sma_200_slope_21d"] = (
        res["sma_200"] / value_ago(res, "sma_200", 21).replace(0, np.nan)
    ) - 1.0
    for length in (12, 20, 26):
        res[f"ema_{length}"] = ema(res, length=length)

    res["bb_middle"] = bollinger_bands(res, output="middle")
    res["bb_upper"] = bollinger_bands(res, output="upper")
    res["bb_lower"] = bollinger_bands(res, output="lower")
    res["bb_width"] = (res["bb_upper"] - res["bb_lower"]) / res["bb_middle"].replace(0, np.nan)

    res["atr_14"] = atr(res)
    res["atr_pct"] = atr(res, output="percent")
    res["atr_pct_max_60d"] = roll(res, res["atr_pct"], 60, "max", min_periods=1)

    res["high_52w"] = window_stat(res, "high", 252, "max")
    res["low_52w"] = window_stat(res, "low", 252, "min")
    res["high_252d_prev"] = window_stat(res, "high", 252, "max", days_ago=1)

    res["daily_returns"] = _fraction_change(res, "close", 1)
    res["volatility_20"] = volatility(res)
    res["pct_change_daily"] = percent_change(res, length=1)
    res["pct_change_1m"] = percent_change(res, length=21)
    res["pct_change_3m"] = percent_change(res, length=63)
    res["pct_change_6m"] = percent_change(res, length=126)
    res["pct_change_12m"] = percent_change(res, length=252)

    for days in (60, 120, 180, 252):
        res[f"close_{days}d"] = value_ago(res, "close", days)
    for days in (60, 120):
        res[f"return_{days}d"] = pct_diff(close, res[f"close_{days}d"])

    res["volume_sma_20"] = sma(res, length=20, source="volume")
    res["volume_ratio"] = ratio(volume, res["volume_sma_20"])
    short_average = roll(res, volume, 5, "mean")
    long_average = roll(res, volume, 50, "mean", min_periods=5)  # old rule: partial window
    res["vol_dryup"] = short_average / long_average.replace(0, np.nan)
    res["vol_multiple"] = ratio(volume, res["volume_sma_20"])
    res["pct_from_52w_high"] = pct_diff(close, res["high_252d_prev"])

    res["rsi_9"] = rsi(res, length=9)
    res["rsi_9_wma_21"] = wma(res, length=21, source="rsi_9")
    res["rsi_14"] = rsi(res, length=14)
    res["macd_line"] = macd(res, output="line")
    res["macd_signal"] = macd(res, output="signal")
    res["macd_histogram"] = macd(res, output="histogram")
    for length in (63, 126, 252):
        res[f"rs_{length}d"] = relative_strength(res, benchmark_df, length=length)
    res["rsi_momentum"] = _rsi_momentum(res)

    res["golden_cross"] = crossed_above(res, "sma_50", "sma_200")
    res["death_cross"] = crossed_below(res, "sma_50", "sma_200")
    res["golden_cross_10d"] = occurred_within(res, "golden_cross", length=10)
    res["macd_cross_up"] = crossed_above(res, "macd_line", "macd_signal")
    res["macd_cross_down"] = crossed_below(res, "macd_line", "macd_signal")

    _blank_one_row_stocks(res, table.columns)

    if "date" in res.columns:
        res["ret_rank_126d"] = cross_sectional_rank(res, "pct_change_6m")
        res["ret_rank_252d"] = cross_sectional_rank(res, "pct_change_12m")
    else:
        res["ret_rank_126d"] = np.nan
        res["ret_rank_252d"] = np.nan

    result = res.iloc[np.argsort(order)]
    result.index = df.index
    return result


def _blank_one_row_stocks(res: pd.DataFrame, original_columns: pd.Index) -> None:
    """Old behaviour: a stock with fewer than 2 rows got no indicator values at all."""
    rows_per_stock = pd.Series(np.arange(len(res)), index=res.index)
    rows_per_stock = rows_per_stock.groupby(stock_keys(res), sort=False).transform("size")
    too_short = (rows_per_stock < 2).to_numpy()
    if not too_short.any():
        return
    for name in res.columns.difference(original_columns):
        if res[name].dtype == bool:
            res[name] = res[name].astype(object)  # a bool column with NaN becomes object
        res.loc[too_short, name] = np.nan
