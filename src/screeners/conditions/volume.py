"""Volume-Based Screening Conditions
=================================
Reusable condition functions evaluating traded share volume, volume moving
averages, volume expansion/surges, volume dryup, and minimum liquidity.
"""

from __future__ import annotations

import pandas as pd

from src.screeners.base import MissingIndicatorError


def cond_volume_surge(
    df: pd.DataFrame,
    multiplier: float = 1.5,
    vol_sma_col: str = "volume_sma_20",
) -> pd.Series:
    """Condition: Traded volume exceeds multiplier * volume moving average."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    missing = [c for c in ("volume", vol_sma_col) if c not in df.columns]
    if missing:
        raise MissingIndicatorError(missing)

    return (df["volume"] > multiplier * df[vol_sma_col]).fillna(False)


def cond_min_volume(
    df: pd.DataFrame,
    min_volume: float = 250_000.0,
    vol_sma_col: str = "volume_sma_20",
) -> pd.Series:
    """Condition: 20-day average volume satisfies minimum liquidity threshold."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    if vol_sma_col not in df.columns:
        raise MissingIndicatorError([vol_sma_col])

    return (df[vol_sma_col] > min_volume).fillna(False)


def cond_volume_dryup(
    df: pd.DataFrame,
    max_ratio: float = 0.6,
    vol_sma_col: str = "volume_sma_20",
) -> pd.Series:
    """Condition: Traded volume is subdued / contracted relative to moving average."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    if "vol_dryup" in df.columns:
        return (df["vol_dryup"] <= max_ratio).fillna(False)

    missing = [c for c in ("volume", vol_sma_col) if c not in df.columns]
    if missing:
        raise MissingIndicatorError(missing)

    return (df["volume"] <= max_ratio * df[vol_sma_col]).fillna(False)
