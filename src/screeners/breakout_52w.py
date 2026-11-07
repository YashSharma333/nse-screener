"""52-Week High Breakout + Volume Screener
========================================
Stocks breaking out to or within 2% of 52-week highs with heavy volume expansion
(volume >= 2.0x 20D SMA) and minimum liquidity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from src.screeners.base import Screener
from src.screeners.conditions.price import (
    cond_above_sma,
    cond_within_pct_of_52w_high,
)
from src.screeners.conditions.volume import (
    cond_min_volume,
    cond_volume_surge,
)
from src.screeners.registry import register_screener


@dataclass(frozen=True)
class Breakout52wParams:
    """Configurable parameters for 52W High Breakout screener."""

    max_pct_from_high: float = 2.0
    vol_multiplier: float = 2.0
    min_liquidity: float = 100_000.0


@register_screener()
class Breakout52wVolumeScreener(Screener):
    """52-Week High Breakouts accompanied by heavy volume surge."""

    key = "breakout_52w_volume"
    name = "52W High Breakout + Volume"
    description = (
        "Stocks breaking out to or within 2% of 52-week highs with heavy volume expansion "
        "(volume >= 2.0x 20D SMA) and minimum liquidity."
    )
    category = "breakout"
    version = "2.0"
    status: Literal["core", "experimental"] = "experimental"
    params_cls = Breakout52wParams

    evidence_columns = (
        "close",
        "high_52w",
        "sma_50",
        "volume",
        "volume_sma_20",
    )
    required_columns = (
        "close",
        "high_52w",
        "sma_50",
        "volume",
        "volume_sma_20",
    )

    def price_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        params: Breakout52wParams = self.params
        return {
            "breakout_52w_high": cond_within_pct_of_52w_high(
                df, max_pct_below=params.max_pct_from_high, high_col="high_52w"
            ),
            "price_above_50_sma": cond_above_sma(df, "sma_50"),
        }

    def volume_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        params: Breakout52wParams = self.params
        return {
            "volume_surge_2x": cond_volume_surge(df, multiplier=params.vol_multiplier),
            "min_volume_liquidity": cond_min_volume(df, min_volume=params.min_liquidity),
        }
