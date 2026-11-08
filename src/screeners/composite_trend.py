"""Composite Trend Regime Screener
================================
Multi-indicator trend regime confluence (Moving Averages, MACD, RSI, Volume).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from src.screeners.base import Screener
from src.screeners.conditions.crossover import (
    cond_macd_above_signal,
    cond_recent_golden_cross,
)
from src.screeners.conditions.momentum import cond_rsi_bounds
from src.screeners.conditions.price import (
    cond_above_sma,
    cond_sma_above_sma,
)
from src.screeners.registry import register_screener


@dataclass(frozen=True)
class CompositeTrendParams:
    """Configurable parameters for Composite Trend Regime screener."""

    rsi_min: float = 50.0
    golden_cross_window: int = 10


@register_screener()
class CompositeTrendScreener(Screener):
    """Multi-indicator trend regime confluence (Moving Averages, MACD, RSI, Volume)."""

    key = "composite_trend"
    name = "Composite Trend Regime"
    description = (
        "Multi-regime confluence screener requiring moving average alignment, "
        "bullish MACD momentum, RSI > 50, and healthy trading volume."
    )
    category = "trend"
    version = "2.0"
    status: Literal["core", "experimental"] = "experimental"
    params_cls = CompositeTrendParams

    evidence_columns = (
        "close",
        "ema_20",
        "sma_50",
        "sma_200",
        "golden_cross_10d",
        "macd_line",
        "macd_signal",
        "rsi_14",
        "volume",
        "volume_sma_20",
    )
    required_columns = (
        "close",
        "ema_20",
        "sma_50",
        "sma_200",
        "golden_cross_10d",
        "macd_line",
        "macd_signal",
        "rsi_14",
        "volume",
        "volume_sma_20",
    )

    def price_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        c_above_20_ema = (df["close"] > df["ema_20"]).fillna(False)
        c_above_50_sma = cond_above_sma(df, "sma_50")
        return {
            "close_above_20_ema": c_above_20_ema,
            "close_above_50_sma": c_above_50_sma,
        }

    def crossover_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        params: CompositeTrendParams = self.params
        gc_recent = cond_recent_golden_cross(df, window=params.golden_cross_window)
        ma_pos = cond_sma_above_sma(df, fast_sma="sma_50", slow_sma="sma_200")
        macd_bull = cond_macd_above_signal(df)
        return {
            "trend_crossover_support": (gc_recent | ma_pos).fillna(False),
            "macd_bullish": macd_bull,
        }

    def momentum_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        params: CompositeTrendParams = self.params
        return {
            "rsi_above_50": cond_rsi_bounds(
                df, min_rsi=params.rsi_min, max_rsi=100.0, rsi_col="rsi_14"
            )
        }

    def volume_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        return {"volume_healthy": (df["volume"] >= df["volume_sma_20"]).fillna(False)}
