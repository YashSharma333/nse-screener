"""Classic Technical Filter Screeners
==================================
Standard technical screens categorized as "classic":
- Moving average positioning (Above 50 SMA, Above 200 SMA)
- Trend transitions (Golden Cross, Death Cross, Bullish MACD Crossover)
- Proximity extremes (Near 52-Week High, Near 52-Week Low)
- Momentum extremes (RSI Oversold, RSI Overbought)
- Volatility behavior (High Volatility, Bollinger Squeeze)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

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


@dataclass(frozen=True)
class EmptyParams:
    """Empty parameters for parameterless classic screeners."""


@dataclass(frozen=True)
class ThresholdPctParams:
    """Configurable percentage threshold parameter."""

    threshold_pct: float = 5.0


@dataclass(frozen=True)
class RsiThresholdParams:
    """Configurable RSI threshold parameter."""

    threshold: float = 30.0


@dataclass(frozen=True)
class RsiOverboughtParams:
    """Configurable RSI overbought threshold parameter."""

    threshold: float = 70.0


@dataclass(frozen=True)
class VolatilityThresholdParams:
    """Configurable volatility threshold parameter."""

    threshold: float = 0.03


@dataclass(frozen=True)
class SqueezeThresholdParams:
    """Configurable Bollinger Squeeze threshold parameter."""

    threshold: float = 0.1


# ---------------------------------------------------------------------------
# 1. Moving Average Screens
# ---------------------------------------------------------------------------
@register_screener()
class Above50SmaScreener(Screener):
    """Stocks with Close price above 50-day Simple Moving Average."""

    key = "above_50_sma"
    name = "Above 50 SMA"
    description = "Stocks trading strictly above their 50-day Simple Moving Average."
    category = "classic"
    version = "1.0"
    status: Literal["core", "experimental"] = "experimental"
    params_cls = EmptyParams
    evidence_columns = ("close", "sma_50")
    required_columns = ("close", "sma_50")

    def price_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        return {"close_above_sma50": cond_above_sma(df, "sma_50")}


@register_screener()
class Above200SmaScreener(Screener):
    """Stocks with Close price above 200-day Simple Moving Average."""

    key = "above_200_sma"
    name = "Above 200 SMA"
    description = "Stocks trading strictly above their 200-day Simple Moving Average."
    category = "classic"
    version = "1.0"
    status: Literal["core", "experimental"] = "experimental"
    params_cls = EmptyParams
    evidence_columns = ("close", "sma_200")
    required_columns = ("close", "sma_200")

    def price_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        return {"close_above_sma200": cond_above_sma(df, "sma_200")}


# ---------------------------------------------------------------------------
# 2. Crossover Screens
# ---------------------------------------------------------------------------
@register_screener()
class GoldenCrossScreener(Screener):
    """Stocks where 50-day SMA crossed above 200-day SMA."""

    key = "golden_cross"
    name = "Golden Cross"
    description = "Stocks undergoing a classical Golden Cross (50 SMA crossing above 200 SMA)."
    category = "classic"
    version = "1.0"
    status: Literal["core", "experimental"] = "experimental"
    params_cls = EmptyParams
    evidence_columns = ("golden_cross", "sma_50", "sma_200")
    required_columns = ("golden_cross",)

    def crossover_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        return {"golden_cross": cond_golden_cross(df)}


@register_screener()
class DeathCrossScreener(Screener):
    """Stocks where 50-day SMA crossed below 200-day SMA."""

    key = "death_cross"
    name = "Death Cross"
    description = "Stocks undergoing a classical Death Cross (50 SMA crossing below 200 SMA)."
    category = "classic"
    version = "1.0"
    status: Literal["core", "experimental"] = "experimental"
    params_cls = EmptyParams
    evidence_columns = ("death_cross", "sma_50", "sma_200")
    required_columns = ("death_cross",)

    def crossover_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        return {"death_cross": cond_death_cross(df)}


@register_screener()
class BullishMacdCrossoverScreener(Screener):
    """Stocks where MACD line crossed above signal line."""

    key = "bullish_macd_crossover"
    name = "Bullish MACD Crossover"
    description = "Stocks where the MACD line crossed above the 9-day signal line."
    category = "classic"
    version = "1.0"
    status: Literal["core", "experimental"] = "experimental"
    params_cls = EmptyParams
    evidence_columns = ("macd_cross_up", "macd_line", "macd_signal")
    required_columns = ("macd_cross_up",)

    def crossover_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        return {"macd_cross_up": cond_macd_bullish_cross(df)}


# ---------------------------------------------------------------------------
# 3. 52-Week Extreme Screens
# ---------------------------------------------------------------------------
@register_screener()
class Near52WeekHighScreener(Screener):
    """Stocks within a configurable percentage of their 52-week High."""

    key = "near_52w_high"
    name = "Near 52-Week High"
    description = "Stocks trading within N% of their 52-week High."
    category = "classic"
    version = "1.0"
    status: Literal["core", "experimental"] = "experimental"
    params_cls = ThresholdPctParams
    evidence_columns = ("close", "high_52w")
    required_columns = ("close", "high_52w")

    def price_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        params: ThresholdPctParams = self.params
        return {
            "within_52w_high": cond_within_pct_of_52w_high(
                df, max_pct_below=params.threshold_pct, high_col="high_52w"
            )
        }


@register_screener()
class Near52WeekLowScreener(Screener):
    """Stocks within a configurable percentage of their 52-week Low."""

    key = "near_52w_low"
    name = "Near 52-Week Low"
    description = "Stocks trading within N% of their 52-week Low."
    category = "classic"
    version = "1.0"
    status: Literal["core", "experimental"] = "experimental"
    params_cls = ThresholdPctParams
    evidence_columns = ("close", "low_52w")
    required_columns = ("close", "low_52w")

    def price_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        params: ThresholdPctParams = self.params
        return {"near_52w_low": cond_near_52w_low(df, threshold_pct=params.threshold_pct)}


# ---------------------------------------------------------------------------
# 4. Momentum Extremes
# ---------------------------------------------------------------------------
@register_screener()
class RsiOversoldScreener(Screener):
    """Stocks with RSI(14) below oversold threshold."""

    key = "rsi_oversold"
    name = "RSI Oversold"
    description = "Stocks with RSI(14) below oversold threshold (default < 30)."
    category = "classic"
    version = "1.0"
    status: Literal["core", "experimental"] = "experimental"
    params_cls = RsiThresholdParams
    evidence_columns = ("rsi_14",)
    required_columns = ("rsi_14",)

    def momentum_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        params: RsiThresholdParams = self.params
        return {"rsi_oversold": cond_rsi_oversold(df, threshold=params.threshold)}


@register_screener()
class RsiOverboughtScreener(Screener):
    """Stocks with RSI(14) above overbought threshold."""

    key = "rsi_overbought"
    name = "RSI Overbought"
    description = "Stocks with RSI(14) above overbought threshold (default > 70)."
    category = "classic"
    version = "1.0"
    status: Literal["core", "experimental"] = "experimental"
    params_cls = RsiOverboughtParams
    evidence_columns = ("rsi_14",)
    required_columns = ("rsi_14",)

    def momentum_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        params: RsiOverboughtParams = self.params
        return {"rsi_overbought": cond_rsi_overbought(df, threshold=params.threshold)}


# ---------------------------------------------------------------------------
# 5. Volatility Behaviors
# ---------------------------------------------------------------------------
@register_screener()
class HighVolatilityScreener(Screener):
    """Stocks with daily return volatility exceeding threshold."""

    key = "high_volatility"
    name = "High Volatility"
    description = "Stocks with 20-day daily return volatility exceeding threshold (default > 3%)."
    category = "classic"
    version = "1.0"
    status: Literal["core", "experimental"] = "experimental"
    params_cls = VolatilityThresholdParams
    evidence_columns = ("volatility_20",)
    required_columns = ("volatility_20",)

    def price_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        params: VolatilityThresholdParams = self.params
        return {"high_volatility": cond_high_volatility(df, threshold=params.threshold)}


@register_screener()
class BollingerSqueezeScreener(Screener):
    """Stocks where price is compressed near the lower Bollinger Band."""

    key = "bollinger_squeeze"
    name = "Bollinger Squeeze"
    description = "Stocks where price is near the lower Bollinger Band (< 10% band width)."
    category = "classic"
    version = "1.0"
    status: Literal["core", "experimental"] = "experimental"
    params_cls = SqueezeThresholdParams
    evidence_columns = ("close", "bb_upper", "bb_lower")
    required_columns = ("close", "bb_upper", "bb_lower")

    def price_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        params: SqueezeThresholdParams = self.params
        return {"bollinger_squeeze": cond_bollinger_squeeze(df, threshold=params.threshold)}
