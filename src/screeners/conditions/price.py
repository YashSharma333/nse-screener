"""Price-Based Screening Conditions
=================================
Reusable condition functions evaluating price levels, moving average relationships,
52-week extremes, volatility bounds, and Bollinger Band compressions.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from src.screeners.base import MissingIndicatorError


def cond_above_sma(df: pd.DataFrame, sma_col: str = "sma_50") -> pd.Series:
    """Condition: Close price is strictly above the specified SMA."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    missing = [c for c in ("close", sma_col) if c not in df.columns]
    if missing:
        raise MissingIndicatorError(missing)

    return (df["close"] > df[sma_col]).fillna(False)


def cond_sma_above_sma(
    df: pd.DataFrame, fast_sma: str = "sma_50", slow_sma: str = "sma_200"
) -> pd.Series:
    """Condition: Fast SMA is strictly above Slow SMA (e.g. 50 SMA > 200 SMA)."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    missing = [c for c in (fast_sma, slow_sma) if c not in df.columns]
    if missing:
        raise MissingIndicatorError(missing)

    return (df[fast_sma] > df[slow_sma]).fillna(False)


def cond_ema_above_sma(
    df: pd.DataFrame, ema_col: str = "ema_20", sma_col: str = "sma_50"
) -> pd.Series:
    """Condition: EMA is strictly above SMA (e.g. 20 EMA > 50 SMA)."""
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
    """Condition: Moving averages strictly stacked in bullish order (fast > mid > slow)."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    missing = [c for c in (fast_ma, mid_ma, slow_ma) if c not in df.columns]
    if missing:
        raise MissingIndicatorError(missing)

    return ((df[fast_ma] > df[mid_ma]) & (df[mid_ma] > df[slow_ma])).fillna(False)


def cond_price_above_mas(df: pd.DataFrame, mas: Sequence[str]) -> pd.Series:
    """Condition: Close price is strictly above all specified moving averages."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    missing = [c for c in ("close", *mas) if c not in df.columns]
    if missing:
        raise MissingIndicatorError(missing)

    mask = pd.Series(True, index=df.index)
    for ma in mas:
        mask &= df["close"] > df[ma]
    return mask.fillna(False)


def cond_within_pct_of_52w_high(
    df: pd.DataFrame,
    max_pct_below: float = 15.0,
    high_col: str = "high_252d_prev",
) -> pd.Series:
    """Condition: Close price is within max_pct_below% of 52-week High.

    Close >= (1 - max_pct_below / 100) * Ref_High
    """
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    missing = [c for c in ("close", high_col) if c not in df.columns]
    if missing:
        raise MissingIndicatorError(missing)

    ref_high = df[high_col]
    threshold_factor = 1.0 - (max_pct_below / 100.0)
    return (df["close"] >= threshold_factor * ref_high).fillna(False)


def cond_above_52w_low_pct(
    df: pd.DataFrame,
    min_pct_above: float = 30.0,
    low_col: str = "low_52w",
) -> pd.Series:
    """Condition: Close price is at least min_pct_above% above 52-week Low.

    Close >= (1 + min_pct_above / 100) * Low_52w
    """
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    missing = [c for c in ("close", low_col) if c not in df.columns]
    if missing:
        raise MissingIndicatorError(missing)

    threshold_factor = 1.0 + (min_pct_above / 100.0)
    return (df["close"] >= threshold_factor * df[low_col]).fillna(False)


def cond_sma_trending_up(
    df: pd.DataFrame,
    sma_col: str = "sma_200",
    periods: int = 21,
) -> pd.Series:
    """Condition: Specified SMA is trending up over the last N periods."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    slope_col = f"{sma_col}_slope_{periods}d"
    if slope_col not in df.columns:
        raise MissingIndicatorError([slope_col])

    return (df[slope_col] >= 0.0).fillna(False)


def cond_near_52w_low(df: pd.DataFrame, threshold_pct: float = 5.0) -> pd.Series:
    """Condition: Close price is within threshold_pct% of 52-week Low."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    missing = [c for c in ("close", "low_52w") if c not in df.columns]
    if missing:
        raise MissingIndicatorError(missing)

    low_series = df["low_52w"].replace(0, float("nan"))
    pct_from_low = ((df["close"] - df["low_52w"]) / low_series) * 100.0
    return (pct_from_low <= threshold_pct).fillna(False)


def cond_bollinger_squeeze(df: pd.DataFrame, threshold: float = 0.1) -> pd.Series:
    """Condition: Price position within Bollinger Bands is near lower band (< threshold)."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    missing = [c for c in ("close", "bb_upper", "bb_lower") if c not in df.columns]
    if missing:
        raise MissingIndicatorError(missing)

    band_width = (df["bb_upper"] - df["bb_lower"]).replace(0, float("nan"))
    position = (df["close"] - df["bb_lower"]) / band_width
    return (position <= threshold).fillna(False)


def cond_high_volatility(df: pd.DataFrame, threshold: float = 0.03) -> pd.Series:
    """Condition: 20-day return volatility exceeds threshold."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    if "volatility_20" not in df.columns:
        raise MissingIndicatorError(["volatility_20"])

    return (df["volatility_20"] >= threshold).fillna(False)


def cond_atr_contracting(df: pd.DataFrame, threshold_factor: float = 0.75) -> pd.Series:
    """Condition: Current ATR% has contracted compared to its 60-day maximum."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    missing = [c for c in ("atr_pct", "atr_pct_max_60d") if c not in df.columns]
    if missing:
        # Fallback to bb_width if available
        if "bb_width" in df.columns:
            return (df["bb_width"] <= 0.12).fillna(False)
        raise MissingIndicatorError(missing)

    return (df["atr_pct"] <= df["atr_pct_max_60d"] * threshold_factor).fillna(False)
