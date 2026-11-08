"""Minervini Trend Template Screener
==================================
Identifies confirmed Stage 2 superperformance leaders meeting Mark Minervini's
8-point Trend Template (MA alignment, upward-trending 200 SMA, 52W positioning, RS).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from src.screeners.base import Screener
from src.screeners.conditions.momentum import (
    cond_percentile_rank_leader,
    cond_relative_strength_leader,
)
from src.screeners.conditions.price import (
    cond_above_52w_low_pct,
    cond_above_sma,
    cond_price_above_mas,
    cond_sma_above_sma,
    cond_sma_trending_up,
    cond_within_pct_of_52w_high,
)
from src.screeners.registry import register_screener


@dataclass(frozen=True)
class MinerviniParams:
    """Configurable parameters for Minervini Trend Template screener."""

    min_pct_above_52w_low: float = 30.0
    max_pct_below_52w_high: float = 25.0
    min_rs_rank: float = 70.0


@register_screener()
class MinerviniTrendTemplateScreener(Screener):
    """Mark Minervini's 8-point Trend Template for Stage 2 growth leaders."""

    key = "minervini_trend_template"
    name = "Minervini Trend Template"
    description = (
        "Identifies confirmed Stage 2 superperformance leaders meeting Mark Minervini's "
        "8-point Trend Template (MA alignment, upward-trending 200 SMA, 52W positioning, RS)."
    )
    category = "trend"
    version = "2.0"
    status: Literal["core", "experimental"] = "experimental"
    params_cls = MinerviniParams

    evidence_columns = (
        "close",
        "sma_50",
        "sma_150",
        "sma_200",
        "sma_200_slope_21d",
        "low_52w",
        "high_52w",
        "rs_63d",
        "ret_rank_252d",
    )
    required_columns = (
        "close",
        "sma_50",
        "sma_150",
        "sma_200",
        "sma_200_slope_21d",
        "low_52w",
        "high_52w",
    )

    def price_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        params: MinerviniParams = self.params
        price_above_150_200 = cond_price_above_mas(df, ("sma_150", "sma_200"))
        sma_150_gt_200 = cond_sma_above_sma(df, fast_sma="sma_150", slow_sma="sma_200")
        sma_200_up = cond_sma_trending_up(df, "sma_200", periods=21)
        sma_50_gt_150 = cond_sma_above_sma(df, fast_sma="sma_50", slow_sma="sma_150")
        sma_50_gt_200 = cond_sma_above_sma(df, fast_sma="sma_50", slow_sma="sma_200")
        price_above_50 = cond_above_sma(df, "sma_50")
        above_low_30pct = cond_above_52w_low_pct(
            df, min_pct_above=params.min_pct_above_52w_low, low_col="low_52w"
        )
        within_high_25pct = cond_within_pct_of_52w_high(
            df, max_pct_below=params.max_pct_below_52w_high, high_col="high_52w"
        )

        return {
            "price_above_150_200_sma": price_above_150_200,
            "sma_150_above_200": sma_150_gt_200,
            "sma_200_trending_up": sma_200_up,
            "sma_50_above_150_200": (sma_50_gt_150 & sma_50_gt_200).fillna(False),
            "price_above_50_sma": price_above_50,
            "price_above_52w_low_30pct": above_low_30pct,
            "price_within_25pct_52w_high": within_high_25pct,
        }

    def momentum_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        params: MinerviniParams = self.params
        cond_rs = (
            cond_relative_strength_leader(df, rs_col="rs_63d", min_rs=0.0)
            if "rs_63d" in df.columns
            else pd.Series(False, index=df.index)
        )
        cond_rank = (
            cond_percentile_rank_leader(df, rank_col="ret_rank_252d", min_rank=params.min_rs_rank)
            if "ret_rank_252d" in df.columns
            else pd.Series(False, index=df.index)
        )
        return {
            "relative_strength_leader": (cond_rs | cond_rank).fillna(False),
        }
