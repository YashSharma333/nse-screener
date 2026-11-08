"""VCP Lite (Volatility Contraction) Screener
============================================
Identifies stocks displaying volatility contraction (ATR% <= 75% of 60D max ATR%
or narrow Bollinger Band width) with drying volume in an established uptrend.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from src.screeners.base import Screener
from src.screeners.conditions.price import (
    cond_above_sma,
    cond_atr_contracting,
)
from src.screeners.conditions.volume import cond_volume_dryup
from src.screeners.registry import register_screener


@dataclass(frozen=True)
class VcpLiteParams:
    """Configurable parameters for VCP Lite screener."""

    vol_dryup_threshold: float = 0.7
    atr_factor: float = 0.75


@register_screener()
class VcpLiteScreener(Screener):
    """Volatility Contraction Pattern (VCP Lite) with volume dry-up."""

    key = "vcp_lite"
    name = "VCP Lite (Volatility Contraction)"
    description = (
        "Identifies stocks displaying volatility contraction (ATR% <= 75% of 60D max ATR% "
        "or narrow Bollinger Band width) with drying volume in an established uptrend."
    )
    category = "breakout"
    version = "2.0"
    status: Literal["core", "experimental"] = "experimental"
    params_cls = VcpLiteParams

    evidence_columns = (
        "close",
        "sma_50",
        "atr_pct",
        "atr_pct_max_60d",
        "volume",
        "volume_sma_20",
    )
    required_columns = (
        "close",
        "sma_50",
        "volume",
        "volume_sma_20",
    )

    def price_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        params: VcpLiteParams = self.params
        return {
            "uptrend_close_above_50": cond_above_sma(df, "sma_50"),
            "volatility_contraction": cond_atr_contracting(df, threshold_factor=params.atr_factor),
        }

    def volume_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        params: VcpLiteParams = self.params
        return {
            "volume_dryup": cond_volume_dryup(
                df, max_ratio=params.vol_dryup_threshold, vol_sma_col="volume_sma_20"
            ),
        }
