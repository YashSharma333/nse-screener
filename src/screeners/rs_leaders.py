"""Relative Strength Leaders Screener
====================================
Equities exhibiting market-leading relative strength outperformance vs NIFTY 500
(RS 63D > 10% or top 20th percentile 252D return rank) above moving averages.
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
    cond_above_sma,
    cond_sma_above_sma,
)
from src.screeners.conditions.volume import cond_min_volume
from src.screeners.registry import register_screener


@dataclass(frozen=True)
class RsLeadersParams:
    """Configurable parameters for Relative Strength Leaders screener."""

    min_rs_pct: float = 0.10
    min_rank: float = 80.0
    min_liquidity: float = 100_000.0


@register_screener()
class RsLeadersScreener(Screener):
    """Top-percentile Relative Strength leaders vs NIFTY 500."""

    key = "rs_leaders"
    name = "Relative Strength Leaders"
    description = (
        "Equities exhibiting market-leading relative strength outperformance vs NIFTY 500 "
        "(RS 63D > 10% or top 20th percentile 252D return rank) above moving averages."
    )
    category = "quantitative"
    version = "2.0"
    status: Literal["core", "experimental"] = "experimental"
    params_cls = RsLeadersParams

    evidence_columns = (
        "close",
        "sma_50",
        "sma_200",
        "rs_63d",
        "ret_rank_252d",
        "volume_sma_20",
    )
    required_columns = (
        "close",
        "sma_50",
        "sma_200",
        "volume_sma_20",
    )

    def price_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        c_gt_50 = cond_above_sma(df, "sma_50")
        sma_50_gt_200 = cond_sma_above_sma(df, fast_sma="sma_50", slow_sma="sma_200")
        return {"price_trend_alignment": (c_gt_50 & sma_50_gt_200).fillna(False)}

    def momentum_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        params: RsLeadersParams = self.params
        rs_lead = (
            cond_relative_strength_leader(df, rs_col="rs_63d", min_rs=params.min_rs_pct)
            if "rs_63d" in df.columns
            else pd.Series(False, index=df.index)
        )
        rank_lead = (
            cond_percentile_rank_leader(df, rank_col="ret_rank_252d", min_rank=params.min_rank)
            if "ret_rank_252d" in df.columns
            else pd.Series(False, index=df.index)
        )
        return {"rs_leader": (rs_lead | rank_lead).fillna(False)}

    def volume_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        params: RsLeadersParams = self.params
        return {"min_liquidity": cond_min_volume(df, min_volume=params.min_liquidity)}
