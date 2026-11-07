"""Crossover Screening Conditions
==============================
Reusable condition functions evaluating technical line crossovers:
Golden Cross, Death Cross, MACD crossovers, and recent crossing events.
"""

from __future__ import annotations

import pandas as pd

from src.screeners.base import MissingIndicatorError


def cond_golden_cross(df: pd.DataFrame) -> pd.Series:
    """Condition: 50-day SMA crossed above 200-day SMA on this bar."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    if "golden_cross" not in df.columns:
        raise MissingIndicatorError(["golden_cross"])

    return df["golden_cross"].fillna(False).astype(bool)


def cond_death_cross(df: pd.DataFrame) -> pd.Series:
    """Condition: 50-day SMA crossed below 200-day SMA on this bar."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    if "death_cross" not in df.columns:
        raise MissingIndicatorError(["death_cross"])

    return df["death_cross"].fillna(False).astype(bool)


def cond_recent_golden_cross(df: pd.DataFrame, window: int = 10) -> pd.Series:
    """Condition: Golden Cross occurred within the last N trading sessions."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    if window == 10:
        if "golden_cross_10d" not in df.columns:
            raise MissingIndicatorError(["golden_cross_10d"])
        return df["golden_cross_10d"].fillna(False).astype(bool)

    target_col = f"golden_cross_{window}d"
    if target_col not in df.columns:
        raise MissingIndicatorError([target_col])
    return df[target_col].fillna(False).astype(bool)


def cond_macd_bullish_cross(df: pd.DataFrame) -> pd.Series:
    """Condition: MACD line crossed above signal line on this bar."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    if "macd_cross_up" not in df.columns:
        raise MissingIndicatorError(["macd_cross_up"])

    return df["macd_cross_up"].fillna(False).astype(bool)


def cond_macd_above_signal(df: pd.DataFrame) -> pd.Series:
    """Condition: MACD line is strictly above signal line."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    missing = [c for c in ("macd_line", "macd_signal") if c not in df.columns]
    if missing:
        raise MissingIndicatorError(missing)

    return (df["macd_line"] > df["macd_signal"]).fillna(False)
