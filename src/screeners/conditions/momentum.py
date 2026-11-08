"""Momentum and Oscillator Screening Conditions
============================================
Reusable condition functions evaluating RSI, MACD histograms, relative strength,
and rate-of-change return bounds.
"""

from __future__ import annotations

import pandas as pd

from src.screeners.base import MissingIndicatorError


def cond_rsi_bounds(
    df: pd.DataFrame,
    min_rsi: float = 40.0,
    max_rsi: float = 70.0,
    rsi_col: str = "rsi_14",
) -> pd.Series:
    """Condition: RSI indicator falls within [min_rsi, max_rsi]."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    if rsi_col not in df.columns:
        raise MissingIndicatorError([rsi_col])

    rsi = df[rsi_col]
    return ((rsi >= min_rsi) & (rsi <= max_rsi)).fillna(False)


def cond_rsi_in_range(
    df: pd.DataFrame,
    lower: float = 40.0,
    upper: float = 55.0,
    rsi_col: str = "rsi_14",
) -> pd.Series:
    """Condition: RSI indicator falls within support range [lower, upper]."""
    return cond_rsi_bounds(df, min_rsi=lower, max_rsi=upper, rsi_col=rsi_col)


def cond_rsi_oversold(
    df: pd.DataFrame,
    threshold: float = 30.0,
    rsi_col: str = "rsi_14",
) -> pd.Series:
    """Condition: RSI indicator is oversold (< threshold)."""
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
    """Condition: RSI indicator is overbought (> threshold)."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    if rsi_col not in df.columns:
        raise MissingIndicatorError([rsi_col])

    return (df[rsi_col] > threshold).fillna(False)


def cond_macd_histogram_positive(df: pd.DataFrame) -> pd.Series:
    """Condition: MACD histogram is strictly greater than 0."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    if "macd_histogram" not in df.columns:
        raise MissingIndicatorError(["macd_histogram"])

    return (df["macd_histogram"] > 0.0).fillna(False)


def cond_relative_strength_positive(
    df: pd.DataFrame,
    period: int = 63,
    benchmark_code: str = "NIFTY 500",
) -> pd.Series:
    """Condition: Relative return performance vs benchmark is positive."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    safe_bm = benchmark_code.replace(" ", "_")
    col_name = f"rs_{period}d_{safe_bm}"

    if col_name not in df.columns:
        fallback_col = f"rs_{period}d"
        if fallback_col in df.columns:
            return (df[fallback_col] > 0.0).fillna(False)
        raise MissingIndicatorError([col_name])

    return (df[col_name] > 0.0).fillna(False)


def cond_relative_strength_leader(
    df: pd.DataFrame,
    rs_col: str = "rs_63d",
    min_rs: float = 0.0,
) -> pd.Series:
    """Condition: Relative strength vs benchmark exceeds min_rs."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    if rs_col not in df.columns:
        raise MissingIndicatorError([rs_col])

    return (df[rs_col] > min_rs).fillna(False)


def cond_percentile_rank_leader(
    df: pd.DataFrame,
    rank_col: str = "ret_rank_252d",
    min_rank: float = 70.0,
) -> pd.Series:
    """Condition: Cross-sectional return percentile rank meets or exceeds min_rank."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    if rank_col not in df.columns:
        raise MissingIndicatorError([rank_col])

    return (df[rank_col] >= min_rank).fillna(False)


def cond_return_capped(
    df: pd.DataFrame,
    max_return: float = 40.0,
    ret_col: str = "return_60d",
) -> pd.Series:
    """Condition: Lookback return does not exceed max_return (anti-climax guard)."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    if ret_col not in df.columns:
        raise MissingIndicatorError([ret_col])

    return (df[ret_col] <= max_return).fillna(False)


def cond_return_floor(
    df: pd.DataFrame,
    min_return: float = 30.0,
    ret_col: str = "return_120d",
) -> pd.Series:
    """Condition: Lookback return meets or exceeds min_return."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    if ret_col not in df.columns:
        raise MissingIndicatorError([ret_col])

    return (df[ret_col] >= min_return).fillna(False)


def cond_return_ratio(
    df: pd.DataFrame,
    numerator_shift: int = 252,
    denominator_shift: int = 180,
    min_val: float | None = None,
    max_val: float | None = None,
) -> pd.Series:
    """Condition: ((Close - Close_N) / Close_D) * 100 bounded within [min_val, max_val]."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    c_num_col = f"close_{numerator_shift}d"
    c_den_col = f"close_{denominator_shift}d"
    missing = [c for c in ("close", c_num_col, c_den_col) if c not in df.columns]
    if missing:
        raise MissingIndicatorError(missing)

    c_num = df[c_num_col]
    c_den = df[c_den_col].replace(0, float("nan"))

    ratio = ((df["close"] - c_num) / c_den) * 100.0
    mask = pd.Series(True, index=df.index)

    if min_val is not None:
        mask &= ratio >= min_val
    if max_val is not None:
        mask &= ratio <= max_val

    return mask.fillna(False)
