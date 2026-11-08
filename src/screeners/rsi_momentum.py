"""RSI Momentum Screener
====================
Systematic core momentum breakout screener combining:
1. RSI(9) momentum and acceleration vs its 21-period WMA (+1.1 offset) within [45, 65].
2. Trend alignment: 20 EMA > 50 SMA > 200 SMA and Close > 50 SMA & 200 SMA.
3. 52-week High proximity: Close >= 85% of prior 252-day High.
4. Volume expansion: Volume > 1.5x 20-day Volume SMA with liquidity floor > 250,000 shares.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from src.screeners.base import Screener
from src.screeners.conditions.price import (
    cond_above_sma,
    cond_ema_above_sma,
    cond_sma_above_sma,
    cond_within_pct_of_52w_high,
)
from src.screeners.conditions.volume import cond_min_volume, cond_volume_surge
from src.screeners.registry import register_screener


@dataclass(frozen=True)
class RsiMomentumParams:
    """Configurable thresholds for RSI Momentum Screener."""

    rsi_period: int = 9
    wma_period: int = 21
    wma_offset: float = 1.1
    rsi_min: float = 45.0
    rsi_max: float = 65.0
    high_52w_pct: float = 0.85
    volume_mult: float = 1.5
    volume_avg_min: float = 250_000.0


@register_screener()
class RsiMomentumScreener(Screener):
    """Core systematic breakout screen: RSI Momentum."""

    key = "rsi_momentum"
    name = "RSI Momentum"
    description = (
        "Composite breakout momentum condition combining RSI(9)/WMA(21), "
        "moving averages, 52w high proximity, and volume expansion."
    )
    category = "momentum"
    version = "1.0"
    status: Literal["core", "experimental"] = "core"
    params_cls = RsiMomentumParams

    evidence_columns = (
        "rsi_9",
        "rsi_9_wma_21",
        "ema_20",
        "sma_50",
        "sma_200",
        "close",
        "high_252d_prev",
        "volume",
        "volume_sma_20",
        "rsi_momentum",
    )
    required_columns = (
        "rsi_9",
        "rsi_9_wma_21",
        "ema_20",
        "sma_50",
        "sma_200",
        "close",
        "high_252d_prev",
        "volume",
        "volume_sma_20",
    )

    def price_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        params: RsiMomentumParams = self.params
        max_pct_below = (1.0 - params.high_52w_pct) * 100.0
        return {
            "ema20_gt_sma50": cond_ema_above_sma(df, ema_col="ema_20", sma_col="sma_50"),
            "sma50_gt_sma200": cond_sma_above_sma(df, fast_sma="sma_50", slow_sma="sma_200"),
            "close_gt_sma50": cond_above_sma(df, sma_col="sma_50"),
            "close_gt_sma200": cond_above_sma(df, sma_col="sma_200"),
            "within_15pct_52wH": cond_within_pct_of_52w_high(
                df, max_pct_below=max_pct_below, high_col="high_252d_prev"
            ),
        }

    def volume_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        params: RsiMomentumParams = self.params
        return {
            "volume_gt_1_5x_avg": cond_volume_surge(
                df, multiplier=params.volume_mult, vol_sma_col="volume_sma_20"
            ),
            "avg_volume_gt_250k": cond_min_volume(
                df, min_volume=params.volume_avg_min, vol_sma_col="volume_sma_20"
            ),
        }

    def momentum_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        params: RsiMomentumParams = self.params
        rsi_col = f"rsi_{params.rsi_period}"
        wma_col = f"rsi_{params.rsi_period}_wma_{params.wma_period}"

        cond_above_wma = (df[rsi_col] >= df[wma_col] + params.wma_offset).fillna(False)
        cond_bounds = ((df[rsi_col] >= params.rsi_min) & (df[rsi_col] <= params.rsi_max)).fillna(
            False
        )

        return {
            "rsi9_above_wma21_plus_1_1": cond_above_wma,
            "rsi9_between_45_and_65": cond_bounds,
        }
