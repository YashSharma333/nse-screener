"""
Price-Based Screening Conditions
================================
Reusable condition functions strictly evaluating price, moving averages,
Bollinger bands, true range, and 52-week price extremes.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd


def cond_above_sma(df: pd.DataFrame, sma_col: str = "sma_50") -> pd.Series:
    """Condition: Close price is strictly above the specified SMA."""
    if df.empty or "close" not in df.columns or sma_col not in df.columns:
        return pd.Series(False, index=df.index, dtype=bool)
    return (df["close"] > df[sma_col]).fillna(False)


def cond_ma_alignment(
    df: pd.DataFrame,
    short_col: str = "ema_20",
    mid_col: str = "sma_50",
    long_col: str = "sma_200",
) -> pd.Series:
    """Condition: Short MA > Mid MA > Long MA (e.g., EMA 20 > SMA 50 > SMA 200)."""
    if df.empty or not {short_col, mid_col, long_col}.issubset(df.columns):
        return pd.Series(False, index=df.index, dtype=bool)
    return ((df[short_col] > df[mid_col]) & (df[mid_col] > df[long_col])).fillna(False)


def cond_price_above_mas(df: pd.DataFrame, mas: Sequence[str] = ("sma_50", "sma_200")) -> pd.Series:
    """Condition: Close price is above all specified moving averages."""
    if df.empty or "close" not in df.columns:
        return pd.Series(False, index=df.index, dtype=bool)
    mask = pd.Series(True, index=df.index)
    for ma in mas:
        if ma in df.columns:
            mask &= df["close"] > df[ma]
        else:
            return pd.Series(False, index=df.index, dtype=bool)
    return mask.fillna(False)


def cond_within_pct_of_52w_high(
    df: pd.DataFrame,
    max_pct_below: float = 15.0,
    high_col: str = "high_252d_prev",
) -> pd.Series:
    """Condition: Close price is within max_pct_below% of 52-week High.

    Close >= (1 - max_pct_below / 100) * Ref_High
    """
    if df.empty or "close" not in df.columns:
        return pd.Series(False, index=df.index, dtype=bool)

    if high_col in df.columns:
        ref_high = df[high_col]
    elif "high_52w" in df.columns:
        ref_high = df["high_52w"].shift(1)
    elif "high" in df.columns:
        ref_high = df["high"].rolling(252, min_periods=20).max().shift(1)
    else:
        return pd.Series(False, index=df.index, dtype=bool)

    threshold_factor = 1.0 - (max_pct_below / 100.0)
    return (df["close"] >= threshold_factor * ref_high).fillna(False)


def cond_above_52w_low_pct(
    df: pd.DataFrame,
    min_pct_above: float = 30.0,
    low_col: str = "low_52w",
) -> pd.Series:
    """Condition: Close price is at least min_pct_above% above 52-week Low.

    Close >= (1 + min_pct_above / 100) * Low_52w
    """
    if df.empty or "close" not in df.columns or low_col not in df.columns:
        return pd.Series(False, index=df.index, dtype=bool)

    threshold_factor = 1.0 + (min_pct_above / 100.0)
    return (df["close"] >= threshold_factor * df[low_col]).fillna(False)


def cond_sma_trending_up(
    df: pd.DataFrame,
    sma_col: str = "sma_200",
    periods: int = 21,
) -> pd.Series:
    """Condition: Specified SMA is trending up over the last N periods."""
    if df.empty or sma_col not in df.columns:
        return pd.Series(False, index=df.index, dtype=bool)

    if f"{sma_col}_slope_{periods}d" in df.columns:
        return (df[f"{sma_col}_slope_{periods}d"] >= 0.0).fillna(False)

    sma_shift = df[sma_col].shift(periods)
    return (df[sma_col] >= sma_shift).fillna(False)


def cond_near_52w_low(df: pd.DataFrame, threshold_pct: float = 5.0) -> pd.Series:
    """Condition: Close price is within threshold_pct% of 52-week Low."""
    if df.empty or "close" not in df.columns or "low_52w" not in df.columns:
        return pd.Series(False, index=df.index, dtype=bool)

    pct_from_low = ((df["close"] - df["low_52w"]) / df["low_52w"].replace(0, np.nan)) * 100.0
    return (pct_from_low <= threshold_pct).fillna(False)


def cond_bollinger_squeeze(df: pd.DataFrame, threshold: float = 0.1) -> pd.Series:
    """Condition: Price position within Bollinger Bands is near lower band (< threshold)."""
    if df.empty or not {"close", "bb_upper", "bb_lower"}.issubset(df.columns):
        return pd.Series(False, index=df.index, dtype=bool)

    band_width = df["bb_upper"] - df["bb_lower"]
    position = (df["close"] - df["bb_lower"]) / band_width.replace(0, np.nan)
    return (position < threshold).fillna(False)


def cond_high_volatility(df: pd.DataFrame, threshold: float = 0.03) -> pd.Series:
    """Condition: 20-day daily return volatility is greater than threshold."""
    if df.empty or "volatility_20" not in df.columns:
        return pd.Series(False, index=df.index, dtype=bool)
    return (df["volatility_20"] > threshold).fillna(False)


def cond_atr_contracting(df: pd.DataFrame, threshold_factor: float = 0.75) -> pd.Series:
    """Condition: Current ATR% has contracted compared to its 60-day maximum."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)
    if "atr_pct" in df.columns and "atr_pct_max_60d" in df.columns:
        return (df["atr_pct"] <= df["atr_pct_max_60d"] * threshold_factor).fillna(False)
    if "bb_width" in df.columns:
        return (df["bb_width"] <= 0.12).fillna(False)
    return pd.Series(False, index=df.index, dtype=bool)
