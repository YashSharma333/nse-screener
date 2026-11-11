"""Momentum conditions — re-exports from catalog for backward compatibility."""

from __future__ import annotations

import pandas as pd

from src.screeners.base import MissingIndicatorError
from src.screeners.catalog import (
    _cond_percentile_rank_leader as cond_percentile_rank_leader,
)
from src.screeners.catalog import (
    _cond_relative_strength_leader as cond_relative_strength_leader,
)
from src.screeners.catalog import (
    _cond_return_capped as cond_return_capped,
)
from src.screeners.catalog import (
    _cond_return_floor as cond_return_floor,
)
from src.screeners.catalog import (
    _cond_return_ratio as cond_return_ratio,
)
from src.screeners.catalog import (
    _cond_rsi_bounds as cond_rsi_bounds,
)


def cond_rsi_in_range(
    df: pd.DataFrame,
    lower: float = 40.0,
    upper: float = 55.0,
    rsi_col: str = "rsi_14",
) -> pd.Series:
    return cond_rsi_bounds(df, min_rsi=lower, max_rsi=upper, rsi_col=rsi_col)


def cond_rsi_oversold(
    df: pd.DataFrame,
    threshold: float = 30.0,
    rsi_col: str = "rsi_14",
) -> pd.Series:
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)
    if rsi_col not in df.columns:
        raise MissingIndicatorError([rsi_col])
    return (df[rsi_col] < threshold).fillna(False)


def cond_rsi_overbought(
    df: pd.DataFrame,
    threshold: float = 70.0,
    rsi_col: str = "rsi_14",
) -> pd.Series:
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)
    if rsi_col not in df.columns:
        raise MissingIndicatorError([rsi_col])
    return (df[rsi_col] > threshold).fillna(False)


__all__ = [
    "cond_percentile_rank_leader",
    "cond_relative_strength_leader",
    "cond_return_capped",
    "cond_return_floor",
    "cond_return_ratio",
    "cond_rsi_bounds",
    "cond_rsi_in_range",
    "cond_rsi_overbought",
    "cond_rsi_oversold",
]
