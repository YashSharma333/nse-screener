"""RSI Pullback in Uptrend Screener
==================================
Bullish uptrend stocks (Close > 50 SMA > 200 SMA) undergoing a low-volume, orderly
pullback with RSI(14) in the bull support zone (40-55).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from src.screeners.base import Screener
from src.screeners.conditions.momentum import cond_rsi_in_range
from src.screeners.conditions.price import (
    cond_above_sma,
    cond_sma_above_sma,
)
from src.screeners.conditions.volume import cond_volume_dryup
from src.screeners.registry import register_screener


@dataclass(frozen=True)
class RsiPullbackParams:
    """Configurable parameters for RSI Pullback screener."""

    rsi_lower: float = 40.0
    rsi_upper: float = 55.0
    max_vol_multiple: float = 1.2


@register_screener()
class RsiPullbackUptrendScreener(Screener):
    """Orderly, low-volume pullbacks to support within an established uptrend."""

    key = "rsi_pullback_uptrend"
    name = "RSI Pullback in Uptrend"
    description = (
        "Bullish uptrend stocks (Close > 50 SMA > 200 SMA) undergoing a low-volume, orderly "
        "pullback with RSI(14) in the bull support zone (40-55)."
    )
    category = "pullback"
    version = "2.0"
    status: Literal["core", "experimental"] = "experimental"
    params_cls = RsiPullbackParams

    evidence_columns = (
        "close",
        "sma_50",
        "sma_200",
        "rsi_14",
        "volume",
        "volume_sma_20",
    )
    required_columns = (
        "close",
        "sma_50",
        "sma_200",
        "rsi_14",
        "volume",
        "volume_sma_20",
    )

    def price_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        c_gt_50 = cond_above_sma(df, "sma_50")
        sma_50_gt_200 = cond_sma_above_sma(df, fast_sma="sma_50", slow_sma="sma_200")
        return {"uptrend_alignment": (c_gt_50 & sma_50_gt_200).fillna(False)}

    def momentum_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        params: RsiPullbackParams = self.params
        return {
            "rsi_support_zone": cond_rsi_in_range(
                df, lower=params.rsi_lower, upper=params.rsi_upper, rsi_col="rsi_14"
            )
        }

    def volume_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        params: RsiPullbackParams = self.params
        return {
            "volume_orderly": cond_volume_dryup(
                df, max_ratio=params.max_vol_multiple, vol_sma_col="volume_sma_20"
            )
        }
