"""
Crossover Technical Indicators
==============================
Contains state-transition and threshold-crossing indicators:
Golden Cross, Death Cross, MACD line/signal crossings, and recent crossover windows.
"""

from __future__ import annotations

import pandas as pd


def calculate_golden_death_cross(
    df: pd.DataFrame, sma_50_col: str = "sma_50", sma_200_col: str = "sma_200"
) -> tuple[pd.Series, pd.Series]:
    """Calculate Golden Cross (SMA50 crosses above SMA200) and Death Cross.

    Precondition:
        df must be sorted chronologically for a single symbol.
    """
    if sma_50_col not in df.columns or sma_200_col not in df.columns:
        raise KeyError(f"Missing required moving average columns: '{sma_50_col}', '{sma_200_col}'")

    sma_50 = df[sma_50_col]
    sma_200 = df[sma_200_col]

    sma_50_prev = sma_50.shift(1)
    sma_200_prev = sma_200.shift(1)

    golden_cross = (sma_50 > sma_200) & (sma_50_prev <= sma_200_prev)
    death_cross = (sma_50 < sma_200) & (sma_50_prev >= sma_200_prev)

    return golden_cross.fillna(False), death_cross.fillna(False)


def calculate_macd_crossovers(
    df: pd.DataFrame, macd_line_col: str = "macd_line", macd_signal_col: str = "macd_signal"
) -> tuple[pd.Series, pd.Series]:
    """Calculate MACD Bullish (cross up) and Bearish (cross down) crossovers.

    Precondition:
        df must be sorted chronologically for a single symbol.
    """
    if macd_line_col not in df.columns or macd_signal_col not in df.columns:
        raise KeyError(f"Missing required MACD columns: '{macd_line_col}', '{macd_signal_col}'")

    macd_line = df[macd_line_col]
    macd_signal = df[macd_signal_col]

    macd_line_prev = macd_line.shift(1)
    macd_signal_prev = macd_signal.shift(1)

    macd_cross_up = (macd_line > macd_signal) & (macd_line_prev <= macd_signal_prev)
    macd_cross_down = (macd_line < macd_signal) & (macd_line_prev >= macd_signal_prev)

    return macd_cross_up.fillna(False), macd_cross_down.fillna(False)


def calculate_recent_crossover(crossover_series: pd.Series, window: int = 10) -> pd.Series:
    """Calculate whether a crossover event occurred within the last N sessions."""
    if crossover_series.empty:
        return pd.Series(False, index=crossover_series.index, dtype=bool)

    # Rolling max of boolean series
    rolling_recent = crossover_series.astype(int).rolling(window=window, min_periods=1).max()
    return (rolling_recent > 0).fillna(False)
