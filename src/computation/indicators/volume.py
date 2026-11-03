"""
Volume-Based Technical Indicators
=================================
Contains calculations strictly derived from traded share volumes:
Volume Moving Averages (Volume SMA), Volume Ratios, and Volume Dryup metrics.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def calculate_volume_sma(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """Calculate rolling simple moving average of volume."""
    if df.empty or "volume" not in df.columns or len(df) < window:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return df["volume"].rolling(window=window, min_periods=window).mean()


def calculate_volume_ratio(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """Calculate ratio of today's volume relative to its 20-day rolling SMA."""
    if df.empty or "volume" not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype=float)

    vol_sma = calculate_volume_sma(df, window=window)
    vol_sma_nonzero = vol_sma.replace(0, np.nan)
    return df["volume"] / vol_sma_nonzero


def calculate_volume_dryup(
    df: pd.DataFrame, short_window: int = 5, long_window: int = 50
) -> pd.Series:
    """Calculate volume contraction / dryup ratio (5D SMA / 50D SMA)."""
    if df.empty or "volume" not in df.columns or len(df) < short_window:
        return pd.Series(np.nan, index=df.index, dtype=float)

    short_sma = df["volume"].rolling(window=short_window, min_periods=short_window).mean()
    long_w = min(long_window, len(df))
    long_sma = df["volume"].rolling(window=long_w, min_periods=short_window).mean()
    long_sma_nonzero = long_sma.replace(0, np.nan)
    return short_sma / long_sma_nonzero
