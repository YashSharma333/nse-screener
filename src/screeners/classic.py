"""
Classic Technical Filter Screeners
==================================
Standard technical screens categorized as "classic":
- Moving average positioning (Above 50 SMA, Above 200 SMA)
- Trend transitions (Golden Cross, Death Cross, Bullish MACD Crossover)
- Proximity extremes (Near 52-Week High, Near 52-Week Low)
- Momentum extremes (RSI Oversold, RSI Overbought)
- Volatility behavior (High Volatility, Bollinger Squeeze)
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.screeners.base import Screener
from src.screeners.conditions.crossover import (
    cond_death_cross,
    cond_golden_cross,
    cond_macd_bullish_cross,
)
from src.screeners.conditions.momentum import (
    cond_rsi_overbought,
    cond_rsi_oversold,
)
from src.screeners.conditions.price import (
    cond_above_sma,
    cond_bollinger_squeeze,
    cond_high_volatility,
    cond_near_52w_low,
    cond_within_pct_of_52w_high,
)
from src.screeners.registry import register_screener


@register_screener()
class Above50SmaScreener(Screener):
    """Stocks with Close price above 50-day Simple Moving Average."""

    key = "above_50_sma"
    name = "Above 50 SMA"
    description = "Stocks trading strictly above their 50-day Simple Moving Average."
    category = "classic"

    def price_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        return {"close_above_sma50": cond_above_sma(df, "sma_50")}


@register_screener()
class Above200SmaScreener(Screener):
    """Stocks with Close price above 200-day Simple Moving Average."""

    key = "above_200_sma"
    name = "Above 200 SMA"
    description = "Stocks trading strictly above their 200-day Simple Moving Average."
    category = "classic"

    def price_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        return {"close_above_sma200": cond_above_sma(df, "sma_200")}


@register_screener()
class GoldenCrossScreener(Screener):
    """Stocks where 50-day SMA crossed above 200-day SMA."""

    key = "golden_cross"
    name = "Golden Cross"
    description = "Stocks undergoing a classical Golden Cross (50 SMA crossing above 200 SMA)."
    category = "classic"

    def crossover_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        return {"golden_cross": cond_golden_cross(df)}


@register_screener()
class DeathCrossScreener(Screener):
    """Stocks where 50-day SMA crossed below 200-day SMA."""

    key = "death_cross"
    name = "Death Cross"
    description = "Stocks undergoing a classical Death Cross (50 SMA crossing below 200 SMA)."
    category = "classic"

    def crossover_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        return {"death_cross": cond_death_cross(df)}


@register_screener()
class Near52WeekHighScreener(Screener):
    """Stocks within a configurable percentage of their 52-week High."""

    key = "near_52w_high"
    name = "Near 52-Week High"
    description = "Stocks trading within N% of their 52-week High."
    category = "classic"

    def __init__(self, threshold_pct: float = 5.0, **kwargs: Any) -> None:
        super().__init__(threshold_pct=threshold_pct, **kwargs)
        self.threshold_pct = threshold_pct

    def price_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        return {
            "within_52w_high": cond_within_pct_of_52w_high(
                df, max_pct_below=self.threshold_pct, high_col="high_52w"
            )
        }


@register_screener()
class Near52WeekLowScreener(Screener):
    """Stocks within a configurable percentage of their 52-week Low."""

    key = "near_52w_low"
    name = "Near 52-Week Low"
    description = "Stocks trading within N% of their 52-week Low."
    category = "classic"

    def __init__(self, threshold_pct: float = 5.0, **kwargs: Any) -> None:
        super().__init__(threshold_pct=threshold_pct, **kwargs)
        self.threshold_pct = threshold_pct

    def price_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        return {"near_52w_low": cond_near_52w_low(df, threshold_pct=self.threshold_pct)}


@register_screener()
class RsiOversoldScreener(Screener):
    """Stocks with RSI(14) below oversold threshold."""

    key = "rsi_oversold"
    name = "RSI Oversold"
    description = "Stocks with RSI(14) below oversold threshold (default < 30)."
    category = "classic"

    def __init__(self, threshold: float = 30.0, **kwargs: Any) -> None:
        super().__init__(threshold=threshold, **kwargs)
        self.threshold = threshold

    def momentum_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        return {"rsi_oversold": cond_rsi_oversold(df, threshold=self.threshold)}


@register_screener()
class RsiOverboughtScreener(Screener):
    """Stocks with RSI(14) above overbought threshold."""

    key = "rsi_overbought"
    name = "RSI Overbought"
    description = "Stocks with RSI(14) above overbought threshold (default > 70)."
    category = "classic"

    def __init__(self, threshold: float = 70.0, **kwargs: Any) -> None:
        super().__init__(threshold=threshold, **kwargs)
        self.threshold = threshold

    def momentum_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        return {"rsi_overbought": cond_rsi_overbought(df, threshold=self.threshold)}


@register_screener()
class BullishMacdCrossoverScreener(Screener):
    """Stocks where MACD line crossed above signal line."""

    key = "bullish_macd_crossover"
    name = "Bullish MACD Crossover"
    description = "Stocks where the MACD line crossed above the 9-day signal line."
    category = "classic"

    def crossover_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        return {"macd_cross_up": cond_macd_bullish_cross(df)}


@register_screener()
class HighVolatilityScreener(Screener):
    """Stocks with daily return volatility exceeding threshold."""

    key = "high_volatility"
    name = "High Volatility"
    description = "Stocks with 20-day daily return volatility exceeding threshold (default > 3%)."
    category = "classic"

    def __init__(self, threshold: float = 0.03, **kwargs: Any) -> None:
        super().__init__(threshold=threshold, **kwargs)
        self.threshold = threshold

    def price_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        return {"high_volatility": cond_high_volatility(df, threshold=self.threshold)}


@register_screener()
class BollingerSqueezeScreener(Screener):
    """Stocks where price is compressed near the lower Bollinger Band."""

    key = "bollinger_squeeze"
    name = "Bollinger Squeeze"
    description = "Stocks where price is near the lower Bollinger Band (< 10% band width)."
    category = "classic"

    def __init__(self, threshold: float = 0.1, **kwargs: Any) -> None:
        super().__init__(threshold=threshold, **kwargs)
        self.threshold = threshold

    def price_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        return {"bollinger_squeeze": cond_bollinger_squeeze(df, threshold=self.threshold)}
