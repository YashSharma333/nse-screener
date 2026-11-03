"""
Price-Based Technical Indicators
================================
Contains mathematical calculations strictly derived from Open, High, Low, Close (OHLC)
prices: Moving Averages (SMA, EMA, WMA), Bollinger Bands, ATR, Donchian Channels,
price lookbacks, and percentage returns.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def calculate_sma(df: pd.DataFrame, window: int, column: str = "close") -> pd.Series:
    """Calculate Simple Moving Average (SMA)."""
    if df.empty or column not in df.columns or len(df) < window:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return df[column].rolling(window=window, min_periods=window).mean()


def calculate_ema(df: pd.DataFrame, span: int, column: str = "close") -> pd.Series:
    """Calculate Exponential Moving Average (EMA)."""
    if df.empty or column not in df.columns or len(df) < span:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return df[column].ewm(span=span, adjust=False, min_periods=span).mean()


def calculate_wma(df: pd.DataFrame, window: int, column: str = "close") -> pd.Series:
    """Calculate Weighted Moving Average (WMA) with linear weights."""
    if df.empty or column not in df.columns or len(df) < window:
        return pd.Series(np.nan, index=df.index, dtype=float)
    weights = np.arange(1, window + 1)
    denom = weights.sum()

    def _weighted_mean(x: np.ndarray) -> float:
        return float(np.dot(x, weights) / denom)

    return df[column].rolling(window=window, min_periods=window).apply(_weighted_mean, raw=True)


def calculate_bollinger_bands(
    df: pd.DataFrame, window: int = 20, num_std: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Calculate Bollinger Bands (Middle, Upper, Lower)."""
    if df.empty or "close" not in df.columns or len(df) < window:
        empty = pd.Series(np.nan, index=df.index, dtype=float)
        return empty, empty, empty

    middle = df["close"].rolling(window=window, min_periods=window).mean()
    std = df["close"].rolling(window=window, min_periods=window).std()
    upper = middle + (std * num_std)
    lower = middle - (std * num_std)
    return middle, upper, lower


def calculate_true_range(df: pd.DataFrame) -> pd.Series:
    """Calculate True Range (TR) from high, low, close."""
    if df.empty or not {"high", "low", "close"}.issubset(df.columns):
        return pd.Series(np.nan, index=df.index, dtype=float)

    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


def calculate_atr(df: pd.DataFrame, window: int = 14) -> tuple[pd.Series, pd.Series]:
    """Calculate Average True Range (ATR) and ATR percentage of close."""
    if df.empty or not {"high", "low", "close"}.issubset(df.columns) or len(df) < window:
        empty = pd.Series(np.nan, index=df.index, dtype=float)
        return empty, empty

    tr = calculate_true_range(df)
    atr = tr.ewm(span=window, adjust=False, min_periods=window).mean()
    close_nonzero = df["close"].replace(0, np.nan)
    atr_pct = (atr / close_nonzero) * 100.0
    return atr, atr_pct


def calculate_donchian_channels(df: pd.DataFrame, window: int = 20) -> tuple[pd.Series, pd.Series]:
    """Calculate Donchian Channel High and Low over rolling window."""
    if df.empty or "high" not in df.columns or "low" not in df.columns or len(df) < window:
        empty = pd.Series(np.nan, index=df.index, dtype=float)
        return empty, empty

    d_high = df["high"].rolling(window=window, min_periods=window).max()
    d_low = df["low"].rolling(window=window, min_periods=window).min()
    return d_high, d_low


def calculate_52w_high_low(
    df: pd.DataFrame, window: int = 252
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Calculate 52-week High, Low, and 1-day shifted 52-week High."""
    if df.empty or "high" not in df.columns or "low" not in df.columns:
        empty = pd.Series(np.nan, index=df.index, dtype=float)
        return empty, empty, empty

    w_size = min(window, len(df))
    min_p = min(20, len(df))
    high_52w = df["high"].rolling(window=w_size, min_periods=min_p).max()
    low_52w = df["low"].rolling(window=w_size, min_periods=min_p).min()
    high_252d_prev = high_52w.shift(1)
    return high_52w, low_52w, high_252d_prev


def calculate_shifted_rolling_high(
    df: pd.DataFrame, window: int = 252, shift_days: int = 1, column: str = "high"
) -> pd.Series:
    """Calculate rolling maximum shifted by shift_days."""
    if df.empty or column not in df.columns or len(df) < window:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return df[column].rolling(window=window, min_periods=window).max().shift(shift_days)


def calculate_shifted_price(df: pd.DataFrame, shift_days: int) -> pd.Series:
    """Calculate N-day backward shifted close price."""
    if df.empty or "close" not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return df["close"].shift(shift_days)


def calculate_pct_change(df: pd.DataFrame, periods: int) -> pd.Series:
    """Calculate percentage change over lookback periods."""
    if df.empty or "close" not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return df["close"].pct_change(periods=periods) * 100.0


def calculate_daily_returns(df: pd.DataFrame) -> pd.Series:
    """Calculate 1-day fractional returns."""
    if df.empty or "close" not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return df["close"].pct_change()


def calculate_volatility(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """Calculate rolling standard deviation of daily returns."""
    if df.empty or "close" not in df.columns or len(df) < window:
        return pd.Series(np.nan, index=df.index, dtype=float)
    daily_returns = df["close"].pct_change()
    return daily_returns.rolling(window=window, min_periods=window).std()
