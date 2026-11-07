"""Swing + Volume Screener
=======================
Capstone core swing screener:
Liquid large- and mid-cap stocks in strong moving average alignment
(EMA 20 > SMA 50 > SMA 200) within 15% of 52-week high, accompanied by
heavy volume expansion (> 1.5x 20D SMA).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from src.screeners.base import Screener
from src.screeners.conditions.price import (
    cond_ma_alignment,
    cond_price_above_mas,
    cond_within_pct_of_52w_high,
)
from src.screeners.conditions.volume import (
    cond_min_volume,
    cond_volume_surge,
)
from src.screeners.registry import register_screener

TRACKED_INDICES: set[str] = {
    "NIFTY 100",
    "NIFTY Midcap 150",
    "NIFTY Smallcap 250",
}


@dataclass(frozen=True)
class SwingVolumeParams:
    """Configurable thresholds for Swing + Volume screener."""

    vol_multiplier: float = 1.5
    min_volume: float = 250_000.0
    max_pct_from_high: float = 15.0
    enforce_universe: bool = True


@register_screener()
class SwingVolumeScreener(Screener):
    """Core Swing Screener: Swing + Volume."""

    key = "swing_volume"
    name = "Swing + Volume"
    description = (
        "Identifies liquid large- and mid-cap stocks in strong uptrend alignment "
        "(EMA 20 > SMA 50 > SMA 200) within 15% of 52-week high with >1.5x volume expansion."
    )
    category = "swing"
    version = "2.0"
    status: Literal["core", "experimental"] = "core"
    params_cls = SwingVolumeParams

    evidence_columns = (
        "ema_20",
        "sma_50",
        "sma_200",
        "close",
        "high_252d_prev",
        "volume",
        "volume_sma_20",
    )
    required_columns = (
        "ema_20",
        "sma_50",
        "sma_200",
        "close",
        "high_252d_prev",
        "volume",
        "volume_sma_20",
    )

    def universe_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        params: SwingVolumeParams = self.params
        if not params.enforce_universe or "index_name" not in df.columns or df.empty:
            return {}
        return {
            "tracked_index_universe": df["index_name"].isin(TRACKED_INDICES),
        }

    def price_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        params: SwingVolumeParams = self.params
        return {
            "ma_alignment": cond_ma_alignment(df, "ema_20", "sma_50", "sma_200"),
            "price_above_sma50": cond_price_above_mas(df, ("sma_50",)),
            "price_above_sma200": cond_price_above_mas(df, ("sma_200",)),
            "within_15pct_52w_high": cond_within_pct_of_52w_high(
                df, max_pct_below=params.max_pct_from_high, high_col="high_252d_prev"
            ),
        }

    def volume_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        params: SwingVolumeParams = self.params
        return {
            "volume_surge": cond_volume_surge(df, multiplier=params.vol_multiplier),
            "min_liquidity": cond_min_volume(df, min_volume=params.min_volume),
        }
