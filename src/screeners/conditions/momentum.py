"""
Momentum & Oscillator Screening Conditions
==========================================
Reusable condition functions evaluating RSI, comparative relative strength,
percentile ranks, return caps/floors, and multi-period return ratios.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def cond_rsi_in_range(
    df: pd.DataFrame,
    lower: float = 40.0,
    upper: float = 55.0,
    rsi_col: str = "rsi_14",
) -> pd.Series:
    """Condition: RSI oscillator is between lower and upper bounds."""
    if df.empty or rsi_col not in df.columns:
        return pd.Series(False, index=df.index, dtype=bool)
    return ((df[rsi_col] >= lower) & (df[rsi_col] <= upper)).fillna(False)


def cond_rsi_oversold(
    df: pd.DataFrame,
    threshold: float = 30.0,
    rsi_col: str = "rsi_14",
) -> pd.Series:
    """Condition: RSI is below oversold threshold."""
    if df.empty or rsi_col not in df.columns:
        return pd.Series(False, index=df.index, dtype=bool)
    return (df[rsi_col] < threshold).fillna(False)


def cond_rsi_overbought(
    df: pd.DataFrame,
    threshold: float = 70.0,
    rsi_col: str = "rsi_14",
) -> pd.Series:
    """Condition: RSI is above overbought threshold."""
    if df.empty or rsi_col not in df.columns:
        return pd.Series(False, index=df.index, dtype=bool)
    return (df[rsi_col] > threshold).fillna(False)


def cond_relative_strength_leader(
    df: pd.DataFrame,
    rs_col: str = "rs_63d",
    min_rs: float = 0.0,
) -> pd.Series:
    """Condition: Relative strength vs benchmark is strictly above min_rs."""
    if df.empty or rs_col not in df.columns:
        return pd.Series(False, index=df.index, dtype=bool)
    return (df[rs_col] > min_rs).fillna(False)


def cond_percentile_rank_leader(
    df: pd.DataFrame,
    rank_col: str = "ret_rank_252d",
    min_rank: float = 80.0,
) -> pd.Series:
    """Condition: Cross-sectional return percentile rank is at or above min_rank."""
    if df.empty or rank_col not in df.columns:
        return pd.Series(False, index=df.index, dtype=bool)
    return (df[rank_col] >= min_rank).fillna(False)


def cond_return_capped(
    df: pd.DataFrame,
    max_return: float = 40.0,
    ret_col: str = "return_60d",
) -> pd.Series:
    """Condition: Lookback return does not exceed max_return (anti-climax guard)."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    if ret_col in df.columns:
        ret = df[ret_col]
    elif "close_60d" in df.columns and ret_col == "return_60d":
        ret = ((df["close"] - df["close_60d"]) / df["close_60d"].replace(0, np.nan)) * 100.0
    elif "close" in df.columns:
        shifted = df["close"].shift(60)
        ret = ((df["close"] - shifted) / shifted.replace(0, np.nan)) * 100.0
    else:
        return pd.Series(False, index=df.index, dtype=bool)

    return (ret <= max_return).fillna(False)


def cond_return_floor(
    df: pd.DataFrame,
    min_return: float = 30.0,
    ret_col: str = "return_120d",
) -> pd.Series:
    """Condition: Lookback return meets or exceeds min_return."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    if ret_col in df.columns:
        ret = df[ret_col]
    elif "close_120d" in df.columns and ret_col == "return_120d":
        ret = ((df["close"] - df["close_120d"]) / df["close_120d"].replace(0, np.nan)) * 100.0
    elif "close" in df.columns:
        shifted = df["close"].shift(120)
        ret = ((df["close"] - shifted) / shifted.replace(0, np.nan)) * 100.0
    else:
        return pd.Series(False, index=df.index, dtype=bool)

    return (ret >= min_return).fillna(False)


def cond_return_ratio(
    df: pd.DataFrame,
    numerator_shift: int = 252,
    denominator_shift: int = 180,
    min_val: float | None = None,
    max_val: float | None = None,
) -> pd.Series:
    """Condition: ((Close - Close_N) / Close_D) * 100 bounded within [min_val, max_val]."""
    if df.empty or "close" not in df.columns:
        return pd.Series(False, index=df.index, dtype=bool)

    c_num = (
        df[f"close_{numerator_shift}d"]
        if f"close_{numerator_shift}d" in df.columns
        else df["close"].shift(numerator_shift)
    )
    c_den = (
        df[f"close_{denominator_shift}d"]
        if f"close_{denominator_shift}d" in df.columns
        else df["close"].shift(denominator_shift)
    )

    ratio = ((df["close"] - c_num) / c_den.replace(0, np.nan)) * 100.0
    mask = pd.Series(True, index=df.index)

    if min_val is not None:
        mask &= ratio >= min_val
    if max_val is not None:
        mask &= ratio <= max_val

    return mask.fillna(False)
