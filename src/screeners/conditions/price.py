"""Price conditions — re-exports from catalog for backward compatibility."""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from src.screeners.base import MissingIndicatorError
from src.screeners.catalog import (
    _cond_above_52w_low_pct as cond_above_52w_low_pct,
)
from src.screeners.catalog import (
    _cond_above_sma as cond_above_sma,
)
from src.screeners.catalog import (
    _cond_atr_contracting as cond_atr_contracting,
)
from src.screeners.catalog import (
    _cond_bollinger_squeeze as cond_bollinger_squeeze,
)
from src.screeners.catalog import (
    _cond_high_volatility as cond_high_volatility,
)
from src.screeners.catalog import (
    _cond_near_52w_low as cond_near_52w_low,
)
from src.screeners.catalog import (
    _cond_sma_above_sma as cond_sma_above_sma,
)
from src.screeners.catalog import (
    _cond_within_pct_of_52w_high as cond_within_pct_of_52w_high,
)
from src.screeners.catalog import (
    cond_sma_trending_up,
)


def cond_ema_above_sma(
    df: pd.DataFrame, ema_col: str = "ema_20", sma_col: str = "sma_50"
) -> pd.Series:
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)
    missing = [c for c in (ema_col, sma_col) if c not in df.columns]
    if missing:
        raise MissingIndicatorError(missing)
    return (df[ema_col] > df[sma_col]).fillna(False)


def cond_ma_alignment(
    df: pd.DataFrame,
    fast_ma: str = "ema_20",
    mid_ma: str = "sma_50",
    slow_ma: str = "sma_200",
) -> pd.Series:
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)
    missing = [c for c in (fast_ma, mid_ma, slow_ma) if c not in df.columns]
    if missing:
        raise MissingIndicatorError(missing)
    return ((df[fast_ma] > df[mid_ma]) & (df[mid_ma] > df[slow_ma])).fillna(False)


def cond_price_above_mas(df: pd.DataFrame, mas: Sequence[str]) -> pd.Series:
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)
    missing = [c for c in ("close", *mas) if c not in df.columns]
    if missing:
        raise MissingIndicatorError(missing)
    mask = pd.Series(True, index=df.index)
    for ma in mas:
        mask &= df["close"] > df[ma]
    return mask.fillna(False)


__all__ = [
    "cond_above_52w_low_pct",
    "cond_above_sma",
    "cond_atr_contracting",
    "cond_bollinger_squeeze",
    "cond_ema_above_sma",
    "cond_high_volatility",
    "cond_ma_alignment",
    "cond_near_52w_low",
    "cond_price_above_mas",
    "cond_sma_above_sma",
    "cond_sma_trending_up",
    "cond_within_pct_of_52w_high",
]
