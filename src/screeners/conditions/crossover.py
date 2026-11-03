"""
Crossover Screening Conditions
==============================
Reusable condition functions evaluating technical line crossovers:
Golden Cross, Death Cross, MACD crossovers, and recent crossing events.
"""

from __future__ import annotations

import pandas as pd


def cond_golden_cross(df: pd.DataFrame) -> pd.Series:
    """Condition: 50-day SMA crossed above 200-day SMA on this bar."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    if "golden_cross" in df.columns:
        return df["golden_cross"].fillna(False).astype(bool)

    if "sma_50" in df.columns and "sma_200" in df.columns:
        cross = (df["sma_50"] > df["sma_200"]) & (df["sma_50"].shift(1) <= df["sma_200"].shift(1))
        return cross.fillna(False)

    return pd.Series(False, index=df.index, dtype=bool)


def cond_death_cross(df: pd.DataFrame) -> pd.Series:
    """Condition: 50-day SMA crossed below 200-day SMA on this bar."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    if "death_cross" in df.columns:
        return df["death_cross"].fillna(False).astype(bool)

    if "sma_50" in df.columns and "sma_200" in df.columns:
        cross = (df["sma_50"] < df["sma_200"]) & (df["sma_50"].shift(1) >= df["sma_200"].shift(1))
        return cross.fillna(False)

    return pd.Series(False, index=df.index, dtype=bool)


def cond_recent_golden_cross(df: pd.DataFrame, window: int = 10) -> pd.Series:
    """Condition: Golden Cross occurred within the last N trading sessions."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    if "golden_cross_10d" in df.columns and window == 10:
        return df["golden_cross_10d"].fillna(False).astype(bool)

    gc = cond_golden_cross(df)
    recent = gc.astype(int).rolling(window=window, min_periods=1).max()
    return (recent > 0).fillna(False)


def cond_macd_bullish_cross(df: pd.DataFrame) -> pd.Series:
    """Condition: MACD line crossed above signal line on this bar."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    if "macd_cross_up" in df.columns:
        return df["macd_cross_up"].fillna(False).astype(bool)

    if "macd_line" in df.columns and "macd_signal" in df.columns:
        cross = (df["macd_line"] > df["macd_signal"]) & (
            df["macd_line"].shift(1) <= df["macd_signal"].shift(1)
        )
        return cross.fillna(False)

    return pd.Series(False, index=df.index, dtype=bool)


def cond_macd_above_signal(df: pd.DataFrame) -> pd.Series:
    """Condition: MACD line is strictly above signal line."""
    if df.empty or "macd_line" not in df.columns or "macd_signal" not in df.columns:
        return pd.Series(False, index=df.index, dtype=bool)
    return (df["macd_line"] > df["macd_signal"]).fillna(False)
