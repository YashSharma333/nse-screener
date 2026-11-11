"""Screener Catalog
==================
All screener classes and their condition logic in one place.

Screeners are grouped as:
- Classic (standard technical filters)
- Swing (core swing-trading momentum screens)
- Breakout / Trend / Pullback / Quantitative (experimental)

Each screener uses the new simplified pattern:
  - ``default_params = {}``  — plain dict of threshold defaults
  - ``rules(self, df, p)``   — single method returning {name: bool Series}

No @dataclass params, no 4-method split, no conditions sub-package.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

import pandas as pd

from src.screeners.base import MissingIndicatorError, Screener
from src.screeners.registry import register_screener

# ---------------------------------------------------------------------------
# Private condition helpers (inlined from the old conditions/ sub-package)
# These are module-private; import from catalog if you need them in tests.
# ---------------------------------------------------------------------------


def _cond_above_sma(df: pd.DataFrame, sma_col: str = "sma_50") -> pd.Series:
    """Close price is strictly above the specified SMA."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)
    missing = [c for c in ("close", sma_col) if c not in df.columns]
    if missing:
        raise MissingIndicatorError(missing)
    return (df["close"] > df[sma_col]).fillna(False)


def _cond_sma_above_sma(
    df: pd.DataFrame, fast_sma: str = "sma_50", slow_sma: str = "sma_200"
) -> pd.Series:
    """Fast SMA is strictly above Slow SMA."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)
    missing = [c for c in (fast_sma, slow_sma) if c not in df.columns]
    if missing:
        raise MissingIndicatorError(missing)
    return (df[fast_sma] > df[slow_sma]).fillna(False)


def _cond_within_pct_of_52w_high(
    df: pd.DataFrame,
    max_pct_below: float = 15.0,
    high_col: str = "high_252d_prev",
) -> pd.Series:
    """Close price is within max_pct_below% of 52-week High."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)
    missing = [c for c in ("close", high_col) if c not in df.columns]
    if missing:
        raise MissingIndicatorError(missing)
    threshold = 1.0 - (max_pct_below / 100.0)
    return (df["close"] >= threshold * df[high_col]).fillna(False)


def _cond_above_52w_low_pct(
    df: pd.DataFrame,
    min_pct_above: float = 30.0,
    low_col: str = "low_52w",
) -> pd.Series:
    """Close price is at least min_pct_above% above 52-week Low."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)
    missing = [c for c in ("close", low_col) if c not in df.columns]
    if missing:
        raise MissingIndicatorError(missing)
    threshold = 1.0 + (min_pct_above / 100.0)
    return (df["close"] >= threshold * df[low_col]).fillna(False)


def _cond_sma_trending_up(
    df: pd.DataFrame, sma_col: str = "sma_200", periods: int = 21
) -> pd.Series:
    """Specified SMA slope over last N periods is non-negative."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)
    slope_col = f"{sma_col}_slope_{periods}d"
    if slope_col not in df.columns:
        raise MissingIndicatorError([slope_col])
    return (df[slope_col] >= 0.0).fillna(False)


def _cond_near_52w_low(df: pd.DataFrame, threshold_pct: float = 5.0) -> pd.Series:
    """Close price is within threshold_pct% of 52-week Low."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)
    missing = [c for c in ("close", "low_52w") if c not in df.columns]
    if missing:
        raise MissingIndicatorError(missing)
    low_series = df["low_52w"].replace(0, float("nan"))
    pct_from_low = ((df["close"] - df["low_52w"]) / low_series) * 100.0
    return (pct_from_low <= threshold_pct).fillna(False)


def _cond_bollinger_squeeze(df: pd.DataFrame, threshold: float = 0.1) -> pd.Series:
    """Price position within Bollinger Bands is near lower band."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)
    missing = [c for c in ("close", "bb_upper", "bb_lower") if c not in df.columns]
    if missing:
        raise MissingIndicatorError(missing)
    band_width = (df["bb_upper"] - df["bb_lower"]).replace(0, float("nan"))
    position = (df["close"] - df["bb_lower"]) / band_width
    return (position <= threshold).fillna(False)


def _cond_high_volatility(df: pd.DataFrame, threshold: float = 0.03) -> pd.Series:
    """20-day return volatility exceeds threshold."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)
    if "volatility_20" not in df.columns:
        raise MissingIndicatorError(["volatility_20"])
    return (df["volatility_20"] >= threshold).fillna(False)


def _cond_atr_contracting(df: pd.DataFrame, threshold_factor: float = 0.75) -> pd.Series:
    """Current ATR% has contracted compared to its 60-day maximum."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)
    missing = [c for c in ("atr_pct", "atr_pct_max_60d") if c not in df.columns]
    if missing:
        if "bb_width" in df.columns:
            return (df["bb_width"] <= 0.12).fillna(False)
        raise MissingIndicatorError(missing)
    return (df["atr_pct"] <= df["atr_pct_max_60d"] * threshold_factor).fillna(False)


def _cond_volume_surge(
    df: pd.DataFrame, multiplier: float = 1.5, vol_sma_col: str = "volume_sma_20"
) -> pd.Series:
    """Traded volume exceeds multiplier × volume moving average."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)
    missing = [c for c in ("volume", vol_sma_col) if c not in df.columns]
    if missing:
        raise MissingIndicatorError(missing)
    return (df["volume"] > multiplier * df[vol_sma_col]).fillna(False)


def _cond_min_volume(
    df: pd.DataFrame, min_volume: float = 250_000.0, vol_sma_col: str = "volume_sma_20"
) -> pd.Series:
    """20-day average volume satisfies minimum liquidity threshold."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)
    if vol_sma_col not in df.columns:
        raise MissingIndicatorError([vol_sma_col])
    return (df[vol_sma_col] > min_volume).fillna(False)


def _cond_volume_dryup(
    df: pd.DataFrame, max_ratio: float = 0.6, vol_sma_col: str = "volume_sma_20"
) -> pd.Series:
    """Traded volume is subdued relative to its moving average."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)
    if "vol_dryup" in df.columns:
        return (df["vol_dryup"] <= max_ratio).fillna(False)
    missing = [c for c in ("volume", vol_sma_col) if c not in df.columns]
    if missing:
        raise MissingIndicatorError(missing)
    return (df["volume"] <= max_ratio * df[vol_sma_col]).fillna(False)


def _cond_rsi_bounds(
    df: pd.DataFrame,
    min_rsi: float = 40.0,
    max_rsi: float = 70.0,
    rsi_col: str = "rsi_14",
) -> pd.Series:
    """RSI indicator falls within [min_rsi, max_rsi]."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)
    if rsi_col not in df.columns:
        raise MissingIndicatorError([rsi_col])
    rsi = df[rsi_col]
    return ((rsi >= min_rsi) & (rsi <= max_rsi)).fillna(False)


def _cond_relative_strength_leader(
    df: pd.DataFrame, rs_col: str = "rs_63d", min_rs: float = 0.0
) -> pd.Series:
    """Stock's relative strength vs benchmark is positive."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)
    if rs_col not in df.columns:
        raise MissingIndicatorError([rs_col])
    return (df[rs_col] > min_rs).fillna(False)


def _cond_percentile_rank_leader(
    df: pd.DataFrame, rank_col: str = "ret_rank_252d", min_rank: float = 70.0
) -> pd.Series:
    """Stock's return percentile rank is above min_rank."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)
    if rank_col not in df.columns:
        raise MissingIndicatorError([rank_col])
    return (df[rank_col] >= min_rank).fillna(False)


def _cond_return_capped(
    df: pd.DataFrame, max_return: float = 40.0, ret_col: str = "return_60d"
) -> pd.Series:
    """Lookback return does not exceed max_return (anti-climax guard)."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)
    if ret_col not in df.columns:
        raise MissingIndicatorError([ret_col])
    return (df[ret_col] <= max_return).fillna(False)


def _cond_return_floor(
    df: pd.DataFrame, min_return: float = 30.0, ret_col: str = "return_120d"
) -> pd.Series:
    """Lookback return meets or exceeds min_return."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)
    if ret_col not in df.columns:
        raise MissingIndicatorError([ret_col])
    return (df[ret_col] >= min_return).fillna(False)


def _cond_return_ratio(
    df: pd.DataFrame,
    numerator_shift: int = 252,
    denominator_shift: int = 180,
    min_val: float | None = None,
    max_val: float | None = None,
) -> pd.Series:
    """((Close - Close_N) / Close_D) × 100 bounded within [min_val, max_val]."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)
    c_num_col = f"close_{numerator_shift}d"
    c_den_col = f"close_{denominator_shift}d"
    missing = [c for c in ("close", c_num_col, c_den_col) if c not in df.columns]
    if missing:
        raise MissingIndicatorError(missing)
    c_den = df[c_den_col].replace(0, float("nan"))
    ratio = ((df["close"] - df[c_num_col]) / c_den) * 100.0
    mask = pd.Series(True, index=df.index)
    if min_val is not None:
        mask &= ratio >= min_val
    if max_val is not None:
        mask &= ratio <= max_val
    return mask.fillna(False)


# Public aliases kept so existing imports still resolve during migration
# (tests/test_seeds_phase3.py imports these directly)
def cond_golden_cross(df: pd.DataFrame) -> pd.Series:
    """50-day SMA crossed above 200-day SMA on this bar."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)
    if "golden_cross" not in df.columns:
        raise MissingIndicatorError(["golden_cross"])
    return df["golden_cross"].fillna(False).astype(bool)


def cond_death_cross(df: pd.DataFrame) -> pd.Series:
    """50-day SMA crossed below 200-day SMA on this bar."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)
    if "death_cross" not in df.columns:
        raise MissingIndicatorError(["death_cross"])
    return df["death_cross"].fillna(False).astype(bool)


def cond_macd_bullish_cross(df: pd.DataFrame) -> pd.Series:
    """MACD line crossed above signal line on this bar."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)
    if "macd_cross_up" not in df.columns:
        raise MissingIndicatorError(["macd_cross_up"])
    return df["macd_cross_up"].fillna(False).astype(bool)


def cond_sma_trending_up(
    df: pd.DataFrame, sma_col: str = "sma_200", periods: int = 21
) -> pd.Series:
    """Specified SMA slope over last N periods is non-negative."""
    return _cond_sma_trending_up(df, sma_col=sma_col, periods=periods)


# ---------------------------------------------------------------------------
# Index universe for swing screeners
# ---------------------------------------------------------------------------

TRACKED_INDICES: set[str] = {
    "NIFTY 100",
    "NIFTY Midcap 150",
    "NIFTY Smallcap 250",
}

# ---------------------------------------------------------------------------
# CLASSIC SCREENERS
# ---------------------------------------------------------------------------


@register_screener()
class Above50SmaScreener(Screener):
    """Stocks with Close price above 50-day Simple Moving Average."""

    key = "above_50_sma"
    name = "Above 50 SMA"
    description = "Stocks trading strictly above their 50-day Simple Moving Average."
    category = "classic"
    version = "1.0"
    status: Literal["core", "experimental"] = "experimental"
    evidence_columns = ("close", "sma_50")
    required_columns = ("close", "sma_50")
    default_params: ClassVar[dict[str, Any]] = {}

    def rules(self, df: pd.DataFrame, _p: dict) -> dict[str, pd.Series]:
        return {"close_above_sma50": _cond_above_sma(df, "sma_50")}


@register_screener()
class Above200SmaScreener(Screener):
    """Stocks with Close price above 200-day Simple Moving Average."""

    key = "above_200_sma"
    name = "Above 200 SMA"
    description = "Stocks trading strictly above their 200-day Simple Moving Average."
    category = "classic"
    version = "1.0"
    status: Literal["core", "experimental"] = "experimental"
    evidence_columns = ("close", "sma_200")
    required_columns = ("close", "sma_200")
    default_params: ClassVar[dict[str, Any]] = {}

    def rules(self, df: pd.DataFrame, _p: dict) -> dict[str, pd.Series]:
        return {"close_above_sma200": _cond_above_sma(df, "sma_200")}


@register_screener()
class GoldenCrossScreener(Screener):
    """Stocks where 50-day SMA crossed above 200-day SMA."""

    key = "golden_cross"
    name = "Golden Cross"
    description = "Stocks undergoing a classical Golden Cross (50 SMA crossing above 200 SMA)."
    category = "classic"
    version = "1.0"
    status: Literal["core", "experimental"] = "experimental"
    evidence_columns = ("golden_cross", "sma_50", "sma_200")
    required_columns = ("golden_cross",)
    default_params: ClassVar[dict[str, Any]] = {}

    def rules(self, df: pd.DataFrame, _p: dict) -> dict[str, pd.Series]:
        return {"golden_cross": cond_golden_cross(df)}


@register_screener()
class DeathCrossScreener(Screener):
    """Stocks where 50-day SMA crossed below 200-day SMA."""

    key = "death_cross"
    name = "Death Cross"
    description = "Stocks undergoing a classical Death Cross (50 SMA crossing below 200 SMA)."
    category = "classic"
    version = "1.0"
    status: Literal["core", "experimental"] = "experimental"
    evidence_columns = ("death_cross", "sma_50", "sma_200")
    required_columns = ("death_cross",)
    default_params: ClassVar[dict[str, Any]] = {}

    def rules(self, df: pd.DataFrame, _p: dict) -> dict[str, pd.Series]:
        return {"death_cross": cond_death_cross(df)}


@register_screener()
class BullishMacdCrossoverScreener(Screener):
    """Stocks where MACD line crossed above signal line."""

    key = "bullish_macd_cross"
    name = "Bullish MACD Crossover"
    description = "Stocks where the MACD line crossed above the signal line on this bar."
    category = "classic"
    version = "1.0"
    status: Literal["core", "experimental"] = "experimental"
    evidence_columns = ("macd_cross_up", "macd_line", "macd_signal")
    required_columns = ("macd_cross_up",)
    default_params: ClassVar[dict[str, Any]] = {}

    def rules(self, df: pd.DataFrame, _p: dict) -> dict[str, pd.Series]:
        return {"macd_cross_up": cond_macd_bullish_cross(df)}


# Alias for backward compatibility
BullishMacdCrossScreener = BullishMacdCrossoverScreener


@register_screener()
class Near52WeekHighScreener(Screener):
    """Stocks within a configurable percentage of their 52-week High."""

    key = "near_52w_high"
    name = "Near 52-Week High"
    description = "Stocks trading within N% of their 52-week High."
    category = "classic"
    version = "1.0"
    status: Literal["core", "experimental"] = "experimental"
    evidence_columns = ("close", "high_52w")
    required_columns = ("close", "high_52w")
    default_params: ClassVar[dict[str, Any]] = {"threshold_pct": 5.0}

    def rules(self, df: pd.DataFrame, p: dict) -> dict[str, pd.Series]:
        return {
            "within_52w_high": _cond_within_pct_of_52w_high(
                df, max_pct_below=p["threshold_pct"], high_col="high_52w"
            )
        }


@register_screener()
class Near52WeekLowScreener(Screener):
    """Stocks within a configurable percentage of their 52-week Low."""

    key = "near_52w_low"
    name = "Near 52-Week Low"
    description = "Stocks trading within N% of their 52-week Low."
    category = "classic"
    version = "1.0"
    status: Literal["core", "experimental"] = "experimental"
    evidence_columns = ("close", "low_52w")
    required_columns = ("close", "low_52w")
    default_params: ClassVar[dict[str, Any]] = {"threshold_pct": 5.0}

    def rules(self, df: pd.DataFrame, p: dict) -> dict[str, pd.Series]:
        return {"near_52w_low": _cond_near_52w_low(df, threshold_pct=p["threshold_pct"])}


@register_screener()
class RsiOversoldScreener(Screener):
    """Stocks with RSI(14) below oversold threshold."""

    key = "rsi_oversold"
    name = "RSI Oversold"
    description = "Stocks with RSI(14) below oversold threshold (default < 30)."
    category = "classic"
    version = "1.0"
    status: Literal["core", "experimental"] = "experimental"
    evidence_columns = ("rsi_14",)
    required_columns = ("rsi_14",)
    default_params: ClassVar[dict[str, Any]] = {"threshold": 30.0}

    def rules(self, df: pd.DataFrame, p: dict) -> dict[str, pd.Series]:
        if df.empty:
            return {"rsi_oversold": pd.Series(False, index=df.index, dtype=bool)}
        if "rsi_14" not in df.columns:
            raise MissingIndicatorError(["rsi_14"])
        return {"rsi_oversold": (df["rsi_14"] < p["threshold"]).fillna(False)}


@register_screener()
class RsiOverboughtScreener(Screener):
    """Stocks with RSI(14) above overbought threshold."""

    key = "rsi_overbought"
    name = "RSI Overbought"
    description = "Stocks with RSI(14) above overbought threshold (default > 70)."
    category = "classic"
    version = "1.0"
    status: Literal["core", "experimental"] = "experimental"
    evidence_columns = ("rsi_14",)
    required_columns = ("rsi_14",)
    default_params: ClassVar[dict[str, Any]] = {"threshold": 70.0}

    def rules(self, df: pd.DataFrame, p: dict) -> dict[str, pd.Series]:
        if df.empty:
            return {"rsi_overbought": pd.Series(False, index=df.index, dtype=bool)}
        if "rsi_14" not in df.columns:
            raise MissingIndicatorError(["rsi_14"])
        return {"rsi_overbought": (df["rsi_14"] > p["threshold"]).fillna(False)}


@register_screener()
class HighVolatilityScreener(Screener):
    """Stocks with daily return volatility exceeding threshold."""

    key = "high_volatility"
    name = "High Volatility"
    description = "Stocks with 20-day daily return volatility exceeding threshold (default > 3%)."
    category = "classic"
    version = "1.0"
    status: Literal["core", "experimental"] = "experimental"
    evidence_columns = ("volatility_20",)
    required_columns = ("volatility_20",)
    default_params: ClassVar[dict[str, Any]] = {"threshold": 0.03}

    def rules(self, df: pd.DataFrame, p: dict) -> dict[str, pd.Series]:
        return {"high_volatility": _cond_high_volatility(df, threshold=p["threshold"])}


@register_screener()
class BollingerSqueezeScreener(Screener):
    """Stocks where price is compressed near the lower Bollinger Band."""

    key = "bollinger_squeeze"
    name = "Bollinger Squeeze"
    description = "Stocks where price is near the lower Bollinger Band (< 10% band width)."
    category = "classic"
    version = "1.0"
    status: Literal["core", "experimental"] = "experimental"
    evidence_columns = ("close", "bb_upper", "bb_lower")
    required_columns = ("close", "bb_upper", "bb_lower")
    default_params: ClassVar[dict[str, Any]] = {"threshold": 0.1}

    def rules(self, df: pd.DataFrame, p: dict) -> dict[str, pd.Series]:
        return {"bollinger_squeeze": _cond_bollinger_squeeze(df, threshold=p["threshold"])}


# ---------------------------------------------------------------------------
# SWING SCREENERS (core)
# ---------------------------------------------------------------------------


@register_screener()
class SwingVolumeScreener(Screener):
    """Swing momentum stocks with significant volume expansion."""

    key = "swing_volume"
    name = "Swing + Volume"
    description = (
        "Identifies liquid large- and mid-cap stocks in strong uptrend alignment "
        "(EMA 20 > SMA 50 > SMA 200) within 15% of 52-week high with >1.5x volume expansion."
    )
    category = "swing"
    version = "2.0"
    status: Literal["core", "experimental"] = "core"
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
    default_params: ClassVar[dict[str, Any]] = {
        "vol_multiplier": 1.5,
        "min_volume": 250_000.0,
        "max_pct_from_high": 15.0,
        "enforce_universe": True,
    }

    def rules(self, df: pd.DataFrame, p: dict) -> dict[str, pd.Series]:
        conds: dict[str, pd.Series] = {
            "ma_alignment": ((df["ema_20"] > df["sma_50"]) & (df["sma_50"] > df["sma_200"])).fillna(
                False
            ),
            "price_above_sma50": (df["close"] > df["sma_50"]).fillna(False),
            "price_above_sma200": (df["close"] > df["sma_200"]).fillna(False),
            "within_15pct_52w_high": (
                df["close"] >= (1.0 - p["max_pct_from_high"] / 100.0) * df["high_252d_prev"]
            ).fillna(False),
            "volume_surge": (df["volume"] > p["vol_multiplier"] * df["volume_sma_20"]).fillna(
                False
            ),
            "min_liquidity": (df["volume_sma_20"] > p["min_volume"]).fillna(False),
        }
        if p["enforce_universe"] and "index_name" in df.columns and not df.empty:
            conds["tracked_index_universe"] = df["index_name"].isin(TRACKED_INDICES)
        return conds


@register_screener()
class SwingMomentumScreener(Screener):
    """Swing momentum stocks with strong 6-month trend and volume expansion."""

    key = "swing_momentum"
    name = "Swing With Momentum"
    description = (
        "Combines all Swing + Volume criteria with multi-quarter velocity metrics "
        "(120D return >= 30%) and an anti-climax exhaustion guard (60D return <= 40%)."
    )
    category = "swing"
    version = "2.0"
    status: Literal["core", "experimental"] = "core"
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
    default_params: ClassVar[dict[str, Any]] = {
        "vol_multiplier": 1.5,
        "min_volume": 250_000.0,
        "max_pct_from_high": 15.0,
        "enforce_universe": True,
        "max_return_60d": 40.0,
        "min_return_120d": 30.0,
        "ratio_180_252_min": 50.0,
        "ratio_180_252_max": 300.0,
        "ratio_252_180_max": 300.0,
    }

    def rules(self, df: pd.DataFrame, p: dict) -> dict[str, pd.Series]:
        conds: dict[str, pd.Series] = {
            "ma_alignment": ((df["ema_20"] > df["sma_50"]) & (df["sma_50"] > df["sma_200"])).fillna(
                False
            ),
            "price_above_sma50": (df["close"] > df["sma_50"]).fillna(False),
            "price_above_sma200": (df["close"] > df["sma_200"]).fillna(False),
            "within_15pct_52w_high": (
                df["close"] >= (1.0 - p["max_pct_from_high"] / 100.0) * df["high_252d_prev"]
            ).fillna(False),
            "volume_surge": (df["volume"] > p["vol_multiplier"] * df["volume_sma_20"]).fillna(
                False
            ),
            "min_liquidity": (df["volume_sma_20"] > p["min_volume"]).fillna(False),
            "return_60d_capped": (df["return_60d"] <= p["max_return_60d"]).fillna(False),
            "return_120d_floor": (df["return_120d"] >= p["min_return_120d"]).fillna(False),
        }

        c_180 = df["close_180d"]
        c_252 = df["close_252d"]
        denom_180 = c_180.replace(0, float("nan"))
        denom_252 = c_252.replace(0, float("nan"))

        ratio_252_180 = ((df["close"] - c_252) / denom_180) * 100.0
        ratio_180_252 = ((df["close"] - c_180) / denom_252) * 100.0

        conds["ratio_252_180_capped"] = (ratio_252_180 <= p["ratio_252_180_max"]).fillna(False)
        conds["ratio_180_252_capped"] = (ratio_180_252 <= p["ratio_180_252_max"]).fillna(False)
        conds["ratio_180_252_floor"] = (ratio_180_252 >= p["ratio_180_252_min"]).fillna(False)

        if p["enforce_universe"] and "index_name" in df.columns and not df.empty:
            conds["tracked_index_universe"] = df["index_name"].isin(TRACKED_INDICES)

        return conds


@register_screener()
class RsiMomentumScreener(Screener):
    """Core systematic breakout screen: RSI Momentum."""

    key = "rsi_momentum"
    name = "RSI Momentum"
    description = (
        "Identifies breakout momentum: RSI(9) momentum vs 21 WMA (+1.1 offset) in [45, 65], "
        "20 EMA > 50 SMA > 200 SMA, Close within 15% of 52W High, and volume expansion > 1.5x."
    )
    category = "momentum"
    version = "1.0"
    status: Literal["core", "experimental"] = "core"

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
    default_params: ClassVar[dict[str, Any]] = {
        "rsi_period": 9,
        "wma_period": 21,
        "wma_offset": 1.1,
        "rsi_min": 45.0,
        "rsi_max": 65.0,
        "high_52w_pct": 0.85,
        "volume_mult": 1.5,
        "volume_avg_min": 250_000.0,
    }

    def rules(self, df: pd.DataFrame, p: dict[str, Any]) -> dict[str, pd.Series]:
        rsi_col = f"rsi_{p['rsi_period']}"
        wma_col = f"rsi_{p['rsi_period']}_wma_{p['wma_period']}"
        max_pct_below = (1.0 - p["high_52w_pct"]) * 100.0

        return {
            # Trend alignment
            "ema20_gt_sma50": (df["ema_20"] > df["sma_50"]).fillna(False),
            "sma50_gt_sma200": (df["sma_50"] > df["sma_200"]).fillna(False),
            "close_gt_sma50": (df["close"] > df["sma_50"]).fillna(False),
            "close_gt_sma200": (df["close"] > df["sma_200"]).fillna(False),
            "within_15pct_52wH": (
                df["close"] >= (1.0 - max_pct_below / 100.0) * df["high_252d_prev"]
            ).fillna(False),
            # Volume
            "volume_gt_1_5x_avg": (df["volume"] > p["volume_mult"] * df["volume_sma_20"]).fillna(
                False
            ),
            "avg_volume_gt_250k": (df["volume_sma_20"] > p["volume_avg_min"]).fillna(False),
            # RSI momentum
            "rsi9_above_wma21_plus_1_1": (df[rsi_col] >= df[wma_col] + p["wma_offset"]).fillna(
                False
            ),
            "rsi9_between_45_and_65": (
                (df[rsi_col] >= p["rsi_min"]) & (df[rsi_col] <= p["rsi_max"])
            ).fillna(False),
        }


# ---------------------------------------------------------------------------
# EXPERIMENTAL SCREENERS
# ---------------------------------------------------------------------------


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
    evidence_columns = ("close", "high_52w", "sma_50", "volume", "volume_sma_20")
    required_columns = ("close", "high_52w", "sma_50", "volume", "volume_sma_20")
    default_params: ClassVar[dict[str, Any]] = {
        "max_pct_from_high": 2.0,
        "vol_multiplier": 2.0,
        "min_liquidity": 100_000.0,
    }

    def rules(self, df: pd.DataFrame, p: dict) -> dict[str, pd.Series]:
        return {
            "breakout_52w_high": _cond_within_pct_of_52w_high(
                df, max_pct_below=p["max_pct_from_high"], high_col="high_52w"
            ),
            "price_above_50_sma": _cond_above_sma(df, "sma_50"),
            "volume_surge_2x": _cond_volume_surge(df, multiplier=p["vol_multiplier"]),
            "min_volume_liquidity": _cond_min_volume(df, min_volume=p["min_liquidity"]),
        }


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
    status: Literal["core", "experimental"] = "experimental"
    evidence_columns = (
        "close",
        "ema_20",
        "sma_50",
        "sma_200",
        "golden_cross_10d",
        "macd_line",
        "macd_signal",
        "rsi_14",
        "volume",
        "volume_sma_20",
    )
    required_columns = (
        "close",
        "ema_20",
        "sma_50",
        "sma_200",
        "golden_cross_10d",
        "macd_line",
        "macd_signal",
        "rsi_14",
        "volume",
        "volume_sma_20",
    )
    default_params: ClassVar[dict[str, Any]] = {"rsi_min": 50.0, "golden_cross_window": 10}

    def rules(self, df: pd.DataFrame, p: dict) -> dict[str, pd.Series]:
        gc_col = f"golden_cross_{p['golden_cross_window']}d"
        sma50_gt_200 = _cond_sma_above_sma(df, fast_sma="sma_50", slow_sma="sma_200")
        gc_recent = (
            df[gc_col].fillna(False).astype(bool)
            if gc_col in df.columns
            else pd.Series(False, index=df.index)
        )
        return {
            "close_above_20_ema": (df["close"] > df["ema_20"]).fillna(False),
            "close_above_50_sma": _cond_above_sma(df, "sma_50"),
            "trend_crossover_support": (gc_recent | sma50_gt_200).fillna(False),
            "macd_bullish": (df["macd_line"] > df["macd_signal"]).fillna(False),
            "rsi_above_50": _cond_rsi_bounds(
                df, min_rsi=p["rsi_min"], max_rsi=100.0, rsi_col="rsi_14"
            ),
            "volume_healthy": (df["volume"] >= df["volume_sma_20"]).fillna(False),
        }


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
    default_params: ClassVar[dict[str, Any]] = {
        "min_pct_above_52w_low": 30.0,
        "max_pct_below_52w_high": 25.0,
        "min_rs_rank": 70.0,
    }

    def rules(self, df: pd.DataFrame, p: dict) -> dict[str, pd.Series]:
        sma50_gt_150 = _cond_sma_above_sma(df, fast_sma="sma_50", slow_sma="sma_150")
        sma50_gt_200 = _cond_sma_above_sma(df, fast_sma="sma_50", slow_sma="sma_200")
        cond_rs = (
            _cond_relative_strength_leader(df, rs_col="rs_63d", min_rs=0.0)
            if "rs_63d" in df.columns
            else pd.Series(False, index=df.index)
        )
        cond_rank = (
            _cond_percentile_rank_leader(df, rank_col="ret_rank_252d", min_rank=p["min_rs_rank"])
            if "ret_rank_252d" in df.columns
            else pd.Series(False, index=df.index)
        )
        return {
            "price_above_150_200_sma": (
                (df["close"] > df["sma_150"]) & (df["close"] > df["sma_200"])
            ).fillna(False),
            "sma_150_above_200": _cond_sma_above_sma(df, fast_sma="sma_150", slow_sma="sma_200"),
            "sma_200_trending_up": _cond_sma_trending_up(df, "sma_200", periods=21),
            "sma_50_above_150_200": (sma50_gt_150 & sma50_gt_200).fillna(False),
            "price_above_50_sma": _cond_above_sma(df, "sma_50"),
            "price_above_52w_low_30pct": _cond_above_52w_low_pct(
                df, min_pct_above=p["min_pct_above_52w_low"], low_col="low_52w"
            ),
            "price_within_25pct_52w_high": _cond_within_pct_of_52w_high(
                df, max_pct_below=p["max_pct_below_52w_high"], high_col="high_52w"
            ),
            "relative_strength_leader": (cond_rs | cond_rank).fillna(False),
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
    status: Literal["core", "experimental"] = "experimental"
    evidence_columns = (
        "close",
        "sma_50",
        "sma_200",
        "rs_63d",
        "ret_rank_252d",
        "volume_sma_20",
    )
    required_columns = ("close", "sma_50", "sma_200", "volume_sma_20")
    default_params: ClassVar[dict[str, Any]] = {
        "min_rs_pct": 0.10,
        "min_rank": 80.0,
        "min_liquidity": 100_000.0,
    }

    def rules(self, df: pd.DataFrame, p: dict) -> dict[str, pd.Series]:
        c_gt_50 = _cond_above_sma(df, "sma_50")
        sma50_gt_200 = _cond_sma_above_sma(df, fast_sma="sma_50", slow_sma="sma_200")
        rs_lead = (
            _cond_relative_strength_leader(df, rs_col="rs_63d", min_rs=p["min_rs_pct"])
            if "rs_63d" in df.columns
            else pd.Series(False, index=df.index)
        )
        rank_lead = (
            _cond_percentile_rank_leader(df, rank_col="ret_rank_252d", min_rank=p["min_rank"])
            if "ret_rank_252d" in df.columns
            else pd.Series(False, index=df.index)
        )
        return {
            "price_trend_alignment": (c_gt_50 & sma50_gt_200).fillna(False),
            "rs_leader": (rs_lead | rank_lead).fillna(False),
            "min_liquidity": _cond_min_volume(df, min_volume=p["min_liquidity"]),
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
    status: Literal["core", "experimental"] = "experimental"
    evidence_columns = ("close", "sma_50", "sma_200", "rsi_14", "volume", "volume_sma_20")
    required_columns = ("close", "sma_50", "sma_200", "rsi_14", "volume", "volume_sma_20")
    default_params: ClassVar[dict[str, Any]] = {
        "rsi_lower": 40.0,
        "rsi_upper": 55.0,
        "max_vol_multiple": 1.2,
    }

    def rules(self, df: pd.DataFrame, p: dict) -> dict[str, pd.Series]:
        c_gt_50 = _cond_above_sma(df, "sma_50")
        sma50_gt_200 = _cond_sma_above_sma(df, fast_sma="sma_50", slow_sma="sma_200")
        return {
            "uptrend_alignment": (c_gt_50 & sma50_gt_200).fillna(False),
            "rsi_support_zone": _cond_rsi_bounds(
                df, min_rsi=p["rsi_lower"], max_rsi=p["rsi_upper"], rsi_col="rsi_14"
            ),
            "volume_orderly": _cond_volume_dryup(
                df, max_ratio=p["max_vol_multiple"], vol_sma_col="volume_sma_20"
            ),
        }


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
    evidence_columns = ("close", "sma_50", "atr_pct", "atr_pct_max_60d", "volume", "volume_sma_20")
    required_columns = ("close", "sma_50", "volume", "volume_sma_20")
    default_params: ClassVar[dict[str, Any]] = {
        "vol_dryup_threshold": 0.7,
        "atr_factor": 0.75,
    }

    def rules(self, df: pd.DataFrame, p: dict) -> dict[str, pd.Series]:
        return {
            "uptrend_close_above_50": _cond_above_sma(df, "sma_50"),
            "volatility_contraction": _cond_atr_contracting(df, threshold_factor=p["atr_factor"]),
            "volume_dryup": _cond_volume_dryup(
                df, max_ratio=p["vol_dryup_threshold"], vol_sma_col="volume_sma_20"
            ),
        }


# ---------------------------------------------------------------------------
# Backward-compat param aliases (now plain dicts, kept so old code still works)
# ---------------------------------------------------------------------------
SwingVolumeParams = SwingVolumeScreener.default_params
SwingMomentumParams = SwingMomentumScreener.default_params
RsiMomentumParams = RsiMomentumScreener.default_params
Breakout52wParams = Breakout52wVolumeScreener.default_params
CompositeTrendParams = CompositeTrendScreener.default_params
MinerviniParams = MinerviniTrendTemplateScreener.default_params
RsLeadersParams = RsLeadersScreener.default_params
RsiPullbackParams = RsiPullbackUptrendScreener.default_params
VcpLiteParams = VcpLiteScreener.default_params
