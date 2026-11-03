"""
Volume-Based Screening Conditions
=================================
Reusable condition functions evaluating traded share volume, volume moving
averages, volume expansion/surges, volume dryup, and minimum liquidity.
"""

from __future__ import annotations

import pandas as pd


def cond_volume_surge(
    df: pd.DataFrame,
    multiplier: float = 1.5,
    vol_sma_col: str = "volume_sma_20",
) -> pd.Series:
    """Condition: Traded volume exceeds multiplier * volume moving average."""
    if df.empty or "volume" not in df.columns:
        return pd.Series(False, index=df.index, dtype=bool)

    if vol_sma_col in df.columns:
        vol_sma = df[vol_sma_col]
    else:
        vol_sma = df["volume"].rolling(20, min_periods=5).mean()

    return (df["volume"] > multiplier * vol_sma).fillna(False)


def cond_min_volume(
    df: pd.DataFrame,
    min_volume: float = 250_000.0,
    vol_sma_col: str = "volume_sma_20",
) -> pd.Series:
    """Condition: 20-day average volume satisfies minimum liquidity threshold."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    if vol_sma_col in df.columns:
        vol_sma = df[vol_sma_col]
    elif "volume" in df.columns:
        vol_sma = df["volume"].rolling(20, min_periods=5).mean()
    else:
        return pd.Series(False, index=df.index, dtype=bool)

    return (vol_sma > min_volume).fillna(False)


def cond_volume_dryup(
    df: pd.DataFrame,
    max_ratio: float = 0.6,
    vol_sma_col: str = "volume_sma_20",
) -> pd.Series:
    """Condition: Traded volume is subdued / contracted relative to moving average."""
    if df.empty or "volume" not in df.columns:
        return pd.Series(False, index=df.index, dtype=bool)

    if "vol_dryup" in df.columns:
        return (df["vol_dryup"] <= max_ratio).fillna(False)

    if vol_sma_col in df.columns:
        vol_sma = df[vol_sma_col]
    else:
        vol_sma = df["volume"].rolling(20, min_periods=5).mean()

    return (df["volume"] <= max_ratio * vol_sma).fillna(False)
