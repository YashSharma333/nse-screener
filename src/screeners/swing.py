"""
Swing Trading Systematic Screeners
==================================
Implements Capstone Screeners:
1. SwingVolumeScreener ("swing_volume"):
   Liquid growth stocks in strong moving average alignment within 15% of 52-week high,
   accompanied by heavy volume expansion (> 1.5x 20D SMA).
2. SwingMomentumScreener ("swing_momentum"):
   Extends Swing + Volume with medium-term momentum acceleration criteria (120D return >= 30%,
   multi-period return ratios) and an anti-climax exhaustion guard (60D return <= 40%).
"""

from __future__ import annotations

from typing import Any

import numpy as np
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


@register_screener()
class SwingVolumeScreener(Screener):
    """Capstone Screener 1: Swing + Volume."""

    key = "swing_volume"
    name = "Swing + Volume"
    description = (
        "Identifies liquid large- and mid-cap stocks in strong uptrend alignment "
        "(EMA 20 > SMA 50 > SMA 200) within 15% of 52-week high with >1.5x volume expansion."
    )
    category = "swing"
    version = "2.0"

    def __init__(
        self,
        vol_multiplier: float = 1.5,
        min_volume: float = 250_000.0,
        max_pct_from_high: float = 15.0,
        enforce_universe: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            vol_multiplier=vol_multiplier,
            min_volume=min_volume,
            max_pct_from_high=max_pct_from_high,
            enforce_universe=enforce_universe,
            **kwargs,
        )
        self.vol_multiplier = vol_multiplier
        self.min_volume = min_volume
        self.max_pct_from_high = max_pct_from_high
        self.enforce_universe = enforce_universe

    def universe_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        if not self.enforce_universe or "index_name" not in df.columns or df.empty:
            return {}
        return {
            "tracked_index_universe": df["index_name"].isin(TRACKED_INDICES),
        }

    def price_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        if df.empty:
            return {}
        return {
            "ma_alignment": cond_ma_alignment(df, "ema_20", "sma_50", "sma_200"),
            "price_above_sma50": cond_price_above_mas(df, ("sma_50",)),
            "price_above_sma200": cond_price_above_mas(df, ("sma_200",)),
            "within_15pct_52w_high": cond_within_pct_of_52w_high(
                df, max_pct_below=self.max_pct_from_high, high_col="high_252d_prev"
            ),
        }

    def volume_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        if df.empty:
            return {}
        return {
            "volume_surge": cond_volume_surge(df, multiplier=self.vol_multiplier),
            "min_liquidity": cond_min_volume(df, min_volume=self.min_volume),
        }


@register_screener()
class SwingMomentumScreener(SwingVolumeScreener):
    """Capstone Screener 2: Swing With Momentum."""

    key = "swing_momentum"
    name = "Swing With Momentum"
    description = (
        "Combines all Swing + Volume criteria with multi-quarter velocity metrics "
        "(120D return >= 30%) and an anti-climax exhaustion guard (60D return <= 40%)."
    )
    category = "swing"
    version = "2.0"

    def __init__(
        self,
        max_return_60d: float = 40.0,
        min_return_120d: float = 30.0,
        ratio_180_252_min: float = 50.0,
        ratio_180_252_max: float = 300.0,
        ratio_252_180_max: float = 300.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.max_return_60d = max_return_60d
        self.min_return_120d = min_return_120d
        self.ratio_180_252_min = ratio_180_252_min
        self.ratio_180_252_max = ratio_180_252_max
        self.ratio_252_180_max = ratio_252_180_max

    def momentum_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        if df.empty:
            return {}

        # 1. 60-day return <= 40%
        if "return_60d" in df.columns:
            ret_60 = df["return_60d"]
        elif "close_60d" in df.columns:
            ret_60 = ((df["close"] - df["close_60d"]) / df["close_60d"].replace(0, np.nan)) * 100.0
        elif "close" in df.columns:
            shifted = df["close"].shift(60)
            ret_60 = ((df["close"] - shifted) / shifted.replace(0, np.nan)) * 100.0
        else:
            ret_60 = pd.Series(np.nan, index=df.index)

        cond_ret_60 = (ret_60 <= self.max_return_60d).fillna(False)

        # 2. Shift references: Close_180d and Close_252d
        c_180 = (
            df["close_180d"]
            if "close_180d" in df.columns
            else df["close"].shift(180)
            if "close" in df.columns
            else pd.Series(np.nan, index=df.index)
        )
        c_252 = (
            df["close_252d"]
            if "close_252d" in df.columns
            else df["close"].shift(252)
            if "close" in df.columns
            else pd.Series(np.nan, index=df.index)
        )

        denom_180 = c_180.replace(0, np.nan)
        denom_252 = c_252.replace(0, np.nan)

        ratio_252_180 = ((df["close"] - c_252) / denom_180) * 100.0
        cond_ratio_252_180 = (ratio_252_180 <= self.ratio_252_180_max).fillna(False)

        ratio_180_252 = ((df["close"] - c_180) / denom_252) * 100.0
        cond_ratio_180_252_max = (ratio_180_252 <= self.ratio_180_252_max).fillna(False)
        cond_ratio_180_252_min = (ratio_180_252 >= self.ratio_180_252_min).fillna(False)

        # 3. 120-day return >= 30%
        if "return_120d" in df.columns:
            ret_120 = df["return_120d"]
        elif "close_120d" in df.columns:
            ret_120 = (
                (df["close"] - df["close_120d"]) / df["close_120d"].replace(0, np.nan)
            ) * 100.0
        elif "close" in df.columns:
            shifted = df["close"].shift(120)
            ret_120 = ((df["close"] - shifted) / shifted.replace(0, np.nan)) * 100.0
        else:
            ret_120 = pd.Series(np.nan, index=df.index)

        cond_ret_120 = (ret_120 >= self.min_return_120d).fillna(False)

        return {
            "return_60d_cap": cond_ret_60,
            "ratio_252_180_cap": cond_ratio_252_180,
            "ratio_180_252_cap": cond_ratio_180_252_max,
            "ratio_180_252_floor": cond_ratio_180_252_min,
            "return_120d_floor": cond_ret_120,
        }
