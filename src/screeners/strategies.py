"""
Systematic Strategies and Quantitative Screeners
================================================
Implements advanced systematic screening strategies with conditions categorized
strictly by nature (Price, Volume, Crossover, Momentum):

1. MinerviniTrendTemplateScreener ("minervini_trend_template")
   Mark Minervini's 8-point Trend Template for Stage 2 growth leaders.
2. Breakout52wVolumeScreener ("breakout_52w_volume")
   52-Week High Breakouts accompanied by heavy institutional volume expansion.
3. RsiPullbackUptrendScreener ("rsi_pullback_uptrend")
   Orderly, low-volume pullbacks to key support zones within established uptrends.
4. VcpLiteScreener ("vcp_lite")
   Volatility Contraction Pattern with drying volume before breakout.
5. RsLeadersScreener ("rs_leaders")
   Top-percentile Relative Strength leaders outperforming the NIFTY 500 benchmark.
6. CompositeTrendScreener ("composite_trend")
   Multi-indicator trend regime confluence (Moving Averages, MACD, RSI, Volume).
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.screeners.base import Screener
from src.screeners.conditions.crossover import (
    cond_macd_above_signal,
    cond_recent_golden_cross,
)
from src.screeners.conditions.momentum import (
    cond_percentile_rank_leader,
    cond_relative_strength_leader,
    cond_rsi_in_range,
)
from src.screeners.conditions.price import (
    cond_above_52w_low_pct,
    cond_above_sma,
    cond_atr_contracting,
    cond_price_above_mas,
    cond_sma_trending_up,
    cond_within_pct_of_52w_high,
)
from src.screeners.conditions.volume import (
    cond_min_volume,
    cond_volume_dryup,
    cond_volume_surge,
)
from src.screeners.registry import register_screener


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

    def __init__(
        self,
        min_pct_above_52w_low: float = 30.0,
        max_pct_below_52w_high: float = 25.0,
        min_rs_rank: float = 70.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            min_pct_above_52w_low=min_pct_above_52w_low,
            max_pct_below_52w_high=max_pct_below_52w_high,
            min_rs_rank=min_rs_rank,
            **kwargs,
        )
        self.min_pct_above_52w_low = min_pct_above_52w_low
        self.max_pct_below_52w_high = max_pct_below_52w_high
        self.min_rs_rank = min_rs_rank

    def price_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        if df.empty:
            return {}

        # 1. Close > 150 SMA and Close > 200 SMA
        price_above_150_200 = cond_price_above_mas(df, ("sma_150", "sma_200"))
        # 2. 150 SMA > 200 SMA
        sma_150_gt_200 = (
            (df["sma_150"] > df["sma_200"]).fillna(False)
            if {"sma_150", "sma_200"}.issubset(df.columns)
            else pd.Series(False, index=df.index)
        )
        # 3. 200 SMA trending up for >= 1 month (21 sessions)
        sma_200_up = cond_sma_trending_up(df, "sma_200", periods=21)
        # 4. 50 SMA > 150 SMA and 50 SMA > 200 SMA
        sma_50_gt_150_200 = (
            ((df["sma_50"] > df["sma_150"]) & (df["sma_50"] > df["sma_200"])).fillna(False)
            if {"sma_50", "sma_150", "sma_200"}.issubset(df.columns)
            else pd.Series(False, index=df.index)
        )
        # 5. Close > 50 SMA
        price_above_50 = cond_above_sma(df, "sma_50")
        # 6. Close >= 1.30 * 52W Low (at least 30% above 52W Low)
        above_low_30pct = cond_above_52w_low_pct(
            df, min_pct_above=self.min_pct_above_52w_low, low_col="low_52w"
        )
        # 7. Close >= 0.75 * 52W High (within 25% of 52W High)
        within_high_25pct = cond_within_pct_of_52w_high(
            df, max_pct_below=self.max_pct_below_52w_high, high_col="high_52w"
        )

        return {
            "price_above_150_200_sma": price_above_150_200,
            "sma_150_above_200": sma_150_gt_200,
            "sma_200_trending_up": sma_200_up,
            "sma_50_above_150_200": sma_50_gt_150_200,
            "price_above_50_sma": price_above_50,
            "price_above_52w_low_30pct": above_low_30pct,
            "price_within_25pct_52w_high": within_high_25pct,
        }

    def momentum_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        if df.empty:
            return {}

        # 8. Relative Strength: rs_63d > 0 or ret_rank_252d >= min_rs_rank
        cond_rs = cond_relative_strength_leader(df, rs_col="rs_63d", min_rs=0.0)
        cond_rank = cond_percentile_rank_leader(
            df, rank_col="ret_rank_252d", min_rank=self.min_rs_rank
        )
        # Either RS positive vs benchmark or top 30% percentile
        combined_rs = (cond_rs | cond_rank).fillna(False)

        return {
            "relative_strength_leader": combined_rs,
        }


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

    def __init__(
        self,
        max_pct_from_high: float = 2.0,
        vol_multiplier: float = 2.0,
        min_liquidity: float = 100_000.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            max_pct_from_high=max_pct_from_high,
            vol_multiplier=vol_multiplier,
            min_liquidity=min_liquidity,
            **kwargs,
        )
        self.max_pct_from_high = max_pct_from_high
        self.vol_multiplier = vol_multiplier
        self.min_liquidity = min_liquidity

    def price_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        if df.empty:
            return {}
        return {
            "breakout_52w_high": cond_within_pct_of_52w_high(
                df, max_pct_below=self.max_pct_from_high, high_col="high_52w"
            ),
            "price_above_50_sma": cond_above_sma(df, "sma_50"),
        }

    def volume_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        if df.empty:
            return {}
        return {
            "volume_surge_2x": cond_volume_surge(df, multiplier=self.vol_multiplier),
            "min_volume_liquidity": cond_min_volume(df, min_volume=self.min_liquidity),
        }


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

    def __init__(
        self,
        rsi_lower: float = 40.0,
        rsi_upper: float = 55.0,
        max_vol_multiple: float = 1.2,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            rsi_lower=rsi_lower,
            rsi_upper=rsi_upper,
            max_vol_multiple=max_vol_multiple,
            **kwargs,
        )
        self.rsi_lower = rsi_lower
        self.rsi_upper = rsi_upper
        self.max_vol_multiple = max_vol_multiple

    def price_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        if df.empty:
            return {}
        uptrend = (
            ((df["close"] > df["sma_50"]) & (df["sma_50"] > df["sma_200"])).fillna(False)
            if {"close", "sma_50", "sma_200"}.issubset(df.columns)
            else pd.Series(False, index=df.index)
        )
        return {"uptrend_alignment": uptrend}

    def momentum_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        if df.empty:
            return {}
        return {
            "rsi_support_zone": cond_rsi_in_range(
                df, lower=self.rsi_lower, upper=self.rsi_upper, rsi_col="rsi_14"
            )
        }

    def volume_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        if df.empty or "volume" not in df.columns:
            return {}
        vol_sma = (
            df["volume_sma_20"]
            if "volume_sma_20" in df.columns
            else df["volume"].rolling(20).mean()
        )
        orderly_vol = (df["volume"] <= self.max_vol_multiple * vol_sma).fillna(False)
        return {"volume_orderly": orderly_vol}


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

    def __init__(
        self,
        vol_dryup_threshold: float = 0.7,
        atr_factor: float = 0.75,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            vol_dryup_threshold=vol_dryup_threshold,
            atr_factor=atr_factor,
            **kwargs,
        )
        self.vol_dryup_threshold = vol_dryup_threshold
        self.atr_factor = atr_factor

    def price_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        if df.empty:
            return {}
        return {
            "uptrend_close_above_50": cond_above_sma(df, "sma_50"),
            "volatility_contraction": cond_atr_contracting(df, threshold_factor=self.atr_factor),
        }

    def volume_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        if df.empty:
            return {}
        return {
            "volume_dryup": cond_volume_dryup(df, max_ratio=self.vol_dryup_threshold),
        }


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

    def __init__(
        self,
        min_rs_pct: float = 0.10,
        min_rank: float = 80.0,
        min_liquidity: float = 100_000.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            min_rs_pct=min_rs_pct,
            min_rank=min_rank,
            min_liquidity=min_liquidity,
            **kwargs,
        )
        self.min_rs_pct = min_rs_pct
        self.min_rank = min_rank
        self.min_liquidity = min_liquidity

    def price_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        if df.empty:
            return {}
        ma_aligned = (
            ((df["close"] > df["sma_50"]) & (df["sma_50"] > df["sma_200"])).fillna(False)
            if {"close", "sma_50", "sma_200"}.issubset(df.columns)
            else pd.Series(False, index=df.index)
        )
        return {"price_trend_alignment": ma_aligned}

    def momentum_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        if df.empty:
            return {}
        rs_lead = cond_relative_strength_leader(df, rs_col="rs_63d", min_rs=self.min_rs_pct)
        rank_lead = cond_percentile_rank_leader(
            df, rank_col="ret_rank_252d", min_rank=self.min_rank
        )
        return {"rs_leader": (rs_lead | rank_lead).fillna(False)}

    def volume_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        if df.empty:
            return {}
        return {"min_liquidity": cond_min_volume(df, min_volume=self.min_liquidity)}


@register_screener()
class CompositeTrendScreener(Screener):
    """Multi-indicator trend regime confluence (Moving Averages, MACD, RSI, Volume)."""

    key = "composite_trend"
    name = "Composite Trend Regime"
    description = (
        "Multi-regime confluence screener requiring moving average alignment, "
        "bullish MACD momentum, RSI > 50, and healthy trading volume."
    )
    category = "trend"
    version = "2.0"

    def price_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        if df.empty:
            return {}
        c_above_20_ema = (
            (df["close"] > df["ema_20"]).fillna(False)
            if "ema_20" in df.columns
            else pd.Series(False, index=df.index)
        )
        c_above_50_sma = cond_above_sma(df, "sma_50")
        return {
            "close_above_20_ema": c_above_20_ema,
            "close_above_50_sma": c_above_50_sma,
        }

    def crossover_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        if df.empty:
            return {}
        # Recent golden cross within 10 days OR SMA 50 > SMA 200
        gc_recent = cond_recent_golden_cross(df, window=10)
        ma_pos = (
            (df["sma_50"] > df["sma_200"]).fillna(False)
            if {"sma_50", "sma_200"}.issubset(df.columns)
            else pd.Series(False, index=df.index)
        )
        macd_bull = cond_macd_above_signal(df)
        return {
            "trend_crossover_support": (gc_recent | ma_pos).fillna(False),
            "macd_bullish": macd_bull,
        }

    def momentum_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        if df.empty or "rsi_14" not in df.columns:
            return {}
        return {"rsi_above_50": (df["rsi_14"] >= 50.0).fillna(False)}

    def volume_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        if df.empty or "volume" not in df.columns:
            return {}
        vol_sma = (
            df["volume_sma_20"]
            if "volume_sma_20" in df.columns
            else df["volume"].rolling(20).mean()
        )
        return {"volume_healthy": (df["volume"] >= vol_sma).fillna(False)}
