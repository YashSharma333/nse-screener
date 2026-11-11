"""Crossover conditions — re-exports from catalog for backward compatibility."""

from __future__ import annotations

import pandas as pd

from src.screeners.base import MissingIndicatorError
from src.screeners.catalog import cond_death_cross, cond_golden_cross, cond_macd_bullish_cross


def cond_recent_golden_cross(df: pd.DataFrame, window: int = 10) -> pd.Series:
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)
    target_col = f"golden_cross_{window}d"
    if target_col not in df.columns:
        raise MissingIndicatorError([target_col])
    return df[target_col].fillna(False).astype(bool)


def cond_macd_above_signal(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)
    missing = [c for c in ("macd_line", "macd_signal") if c not in df.columns]
    if missing:
        raise MissingIndicatorError(missing)
    return (df["macd_line"] > df["macd_signal"]).fillna(False)


__all__ = [
    "cond_death_cross",
    "cond_golden_cross",
    "cond_macd_above_signal",
    "cond_macd_bullish_cross",
    "cond_recent_golden_cross",
]
