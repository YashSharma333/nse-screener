"""Swing With Momentum Screener
=============================
Capstone core swing screener:
Extends Swing + Volume with medium-term momentum acceleration criteria (120D return >= 30%,
multi-period return ratios) and an anti-climax exhaustion guard (60D return <= 40%).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from src.screeners.base import Screener
from src.screeners.conditions.momentum import (
    cond_return_capped,
    cond_return_floor,
)
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
class SwingMomentumParams:
    """Configurable thresholds for Swing With Momentum screener."""

    vol_multiplier: float = 1.5
    min_volume: float = 250_000.0
    max_pct_from_high: float = 15.0
    enforce_universe: bool = True
    max_return_60d: float = 40.0
    min_return_120d: float = 30.0
    ratio_180_252_min: float = 50.0
    ratio_180_252_max: float = 300.0
    ratio_252_180_max: float = 300.0


@register_screener()
class SwingMomentumScreener(Screener):
    """Core Swing Screener: Swing With Momentum."""

    key = "swing_momentum"
    name = "Swing With Momentum"
    description = (
        "Combines all Swing + Volume criteria with multi-quarter velocity metrics "
        "(120D return >= 30%) and an anti-climax exhaustion guard (60D return <= 40%)."
    )
    category = "swing"
    version = "2.0"
    status: Literal["core", "experimental"] = "core"
    params_cls = SwingMomentumParams

    evidence_columns = (
        "ema_20",
        "sma_50",
        "sma_200",
        "close",
        "high_252d_prev",
        "volume",
        "volume_sma_20",
        "return_60d",
        "return_120d",
        "close_180d",
        "close_252d",
    )
    required_columns = (
        "ema_20",
        "sma_50",
        "sma_200",
        "close",
        "high_252d_prev",
        "volume",
        "volume_sma_20",
        "return_60d",
        "return_120d",
        "close_180d",
        "close_252d",
    )

    def universe_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        params: SwingMomentumParams = self.params
        if not params.enforce_universe or "index_name" not in df.columns or df.empty:
            return {}
        return {
            "tracked_index_universe": df["index_name"].isin(TRACKED_INDICES),
        }

    def price_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        params: SwingMomentumParams = self.params
        return {
            "ma_alignment": cond_ma_alignment(df, "ema_20", "sma_50", "sma_200"),
            "price_above_sma50": cond_price_above_mas(df, ("sma_50",)),
            "price_above_sma200": cond_price_above_mas(df, ("sma_200",)),
            "within_15pct_52w_high": cond_within_pct_of_52w_high(
                df, max_pct_below=params.max_pct_from_high, high_col="high_252d_prev"
            ),
        }

    def volume_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        params: SwingMomentumParams = self.params
        return {
            "volume_surge": cond_volume_surge(df, multiplier=params.vol_multiplier),
            "min_liquidity": cond_min_volume(df, min_volume=params.min_volume),
        }

    def momentum_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        params: SwingMomentumParams = self.params

        # 1. 60-day return <= 40% (anti-climax exhaustion guard)
        cond_ret_60 = cond_return_capped(df, max_return=params.max_return_60d, ret_col="return_60d")

        # 2. Ratio checks using precomputed shifted prices
        c_180 = df["close_180d"]
        c_252 = df["close_252d"]
        denom_180 = c_180.replace(0, float("nan"))
        denom_252 = c_252.replace(0, float("nan"))

        ratio_252_180 = ((df["close"] - c_252) / denom_180) * 100.0
        cond_ratio_252_180 = (ratio_252_180 <= params.ratio_252_180_max).fillna(False)

        ratio_180_252 = ((df["close"] - c_180) / denom_252) * 100.0
        cond_ratio_180_252_max = (ratio_180_252 <= params.ratio_180_252_max).fillna(False)
        cond_ratio_180_252_min = (ratio_180_252 >= params.ratio_180_252_min).fillna(False)

        # 3. 120-day return >= 30%
        cond_ret_120 = cond_return_floor(
            df, min_return=params.min_return_120d, ret_col="return_120d"
        )

        return {
            "return_60d_capped": cond_ret_60,
            "ratio_252_180_capped": cond_ratio_252_180,
            "ratio_180_252_capped": cond_ratio_180_252_max,
            "ratio_180_252_floor": cond_ratio_180_252_min,
            "return_120d_floor": cond_ret_120,
        }
