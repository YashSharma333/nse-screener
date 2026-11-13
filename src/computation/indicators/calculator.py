"""
Technical Calculator Facade & Implementation
============================================
Contains mathematical indicator calculations strictly derived from Open, High, Low, Close,
Volume, and benchmark prices. Includes:
  - Moving Averages (SMA, EMA, WMA)
  - Bollinger Bands, ATR, Donchian Channels
  - 52-Week High / Low & Lookbacks
  - Traded Volume Moving Averages & Contraction / Dryup
  - Golden Cross, Death Cross, MACD Crossovers
  - Relative Strength Index (RSI), MACD, Relative Strength vs Benchmark
  - Cross-Sectional Percentile Return Ranks
  - Derived convenience metrics (vol_multiple, pct_from_52w_high)
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import numpy as np
import pandas as pd

logger = logging.getLogger("src.computation.indicators")

# Bumped schema version whenever columns or formulas change
INDICATORS_SCHEMA_VERSION = 4


# ===========================================================================
# 1. Price-Based Functions
# ===========================================================================


def calculate_sma(df: pd.DataFrame, window: int, column: str = "close") -> pd.Series:
    """Calculate Simple Moving Average (SMA)."""
    if df.empty or column not in df.columns or len(df) < window:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return df[column].rolling(window=window, min_periods=window).mean()


def calculate_ema(df: pd.DataFrame, span: int, column: str = "close") -> pd.Series:
    """Calculate Exponential Moving Average (EMA)."""
    if df.empty or column not in df.columns or len(df) < span:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return df[column].ewm(span=span, adjust=False, min_periods=span).mean()


def calculate_wma(df: pd.DataFrame, window: int, column: str = "close") -> pd.Series:
    """Calculate Weighted Moving Average (WMA) with linear weights."""
    if df.empty or column not in df.columns or len(df) < window:
        return pd.Series(np.nan, index=df.index, dtype=float)
    weights = np.arange(1, window + 1)
    denom = weights.sum()

    def _weighted_mean(x: np.ndarray) -> float:
        return float(np.dot(x, weights) / denom)

    return df[column].rolling(window=window, min_periods=window).apply(_weighted_mean, raw=True)


def calculate_bollinger_bands(
    df: pd.DataFrame, window: int = 20, num_std: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Calculate Bollinger Bands (Middle, Upper, Lower)."""
    if df.empty or "close" not in df.columns or len(df) < window:
        empty = pd.Series(np.nan, index=df.index, dtype=float)
        return empty, empty, empty

    middle = df["close"].rolling(window=window, min_periods=window).mean()
    std = df["close"].rolling(window=window, min_periods=window).std()
    upper = middle + (std * num_std)
    lower = middle - (std * num_std)
    return middle, upper, lower


def calculate_true_range(df: pd.DataFrame) -> pd.Series:
    """Calculate True Range (TR) from high, low, close."""
    if df.empty or not {"high", "low", "close"}.issubset(df.columns):
        return pd.Series(np.nan, index=df.index, dtype=float)

    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


def calculate_atr(df: pd.DataFrame, window: int = 14) -> tuple[pd.Series, pd.Series]:
    """Calculate Average True Range (ATR) and ATR percentage of close."""
    if df.empty or not {"high", "low", "close"}.issubset(df.columns) or len(df) < window:
        empty = pd.Series(np.nan, index=df.index, dtype=float)
        return empty, empty

    tr = calculate_true_range(df)
    atr = tr.ewm(span=window, adjust=False, min_periods=window).mean()
    close_nonzero = df["close"].replace(0, np.nan)
    atr_pct = (atr / close_nonzero) * 100.0
    return atr, atr_pct


def calculate_donchian_channels(df: pd.DataFrame, window: int = 20) -> tuple[pd.Series, pd.Series]:
    """Calculate Donchian Channel High and Low over rolling window."""
    if df.empty or "high" not in df.columns or "low" not in df.columns or len(df) < window:
        empty = pd.Series(np.nan, index=df.index, dtype=float)
        return empty, empty

    d_high = df["high"].rolling(window=window, min_periods=window).max()
    d_low = df["low"].rolling(window=window, min_periods=window).min()
    return d_high, d_low


def calculate_52w_high_low(
    df: pd.DataFrame, window: int = 252
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Calculate 52-week High, Low, and 1-day shifted 52-week High."""
    if df.empty or "high" not in df.columns or "low" not in df.columns:
        empty = pd.Series(np.nan, index=df.index, dtype=float)
        return empty, empty, empty

    w_size = min(window, len(df))
    min_p = min(20, len(df))
    high_52w = df["high"].rolling(window=w_size, min_periods=min_p).max()
    low_52w = df["low"].rolling(window=w_size, min_periods=min_p).min()
    high_252d_prev = high_52w.shift(1)
    return high_52w, low_52w, high_252d_prev


def calculate_shifted_rolling_high(
    df: pd.DataFrame, window: int = 252, shift_days: int = 1, column: str = "high"
) -> pd.Series:
    """Calculate rolling maximum shifted by shift_days."""
    if df.empty or column not in df.columns or len(df) < window:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return df[column].rolling(window=window, min_periods=window).max().shift(shift_days)


def calculate_shifted_price(df: pd.DataFrame, shift_days: int, column: str = "close") -> pd.Series:
    """Calculate historical price shifted by shift_days."""
    if df.empty or column not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return df[column].shift(shift_days)


def calculate_pct_change(df: pd.DataFrame, periods: int = 1, column: str = "close") -> pd.Series:
    """Calculate percentage price change over N lookback periods."""
    if df.empty or column not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return df[column].pct_change(periods=periods) * 100.0


def calculate_daily_returns(df: pd.DataFrame) -> pd.Series:
    """Calculate 1-day fractional returns."""
    if df.empty or "close" not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return df["close"].pct_change()


def calculate_volatility(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """Calculate rolling standard deviation of daily returns."""
    if df.empty or "close" not in df.columns or len(df) < window:
        return pd.Series(np.nan, index=df.index, dtype=float)
    daily_returns = df["close"].pct_change()
    return daily_returns.rolling(window=window, min_periods=window).std()


# ===========================================================================
# 2. Volume-Based Functions
# ===========================================================================


def calculate_volume_sma(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """Calculate rolling simple moving average of volume."""
    if df.empty or "volume" not in df.columns or len(df) < window:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return df["volume"].rolling(window=window, min_periods=window).mean()


def calculate_volume_ratio(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """Calculate ratio of today's volume relative to its 20-day rolling SMA."""
    if df.empty or "volume" not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype=float)

    vol_sma = calculate_volume_sma(df, window=window)
    vol_sma_nonzero = vol_sma.replace(0, np.nan)
    return df["volume"] / vol_sma_nonzero


def calculate_volume_dryup(
    df: pd.DataFrame, short_window: int = 5, long_window: int = 50
) -> pd.Series:
    """Calculate volume contraction / dryup ratio (5D SMA / 50D SMA)."""
    if df.empty or "volume" not in df.columns or len(df) < short_window:
        return pd.Series(np.nan, index=df.index, dtype=float)

    short_sma = df["volume"].rolling(window=short_window, min_periods=short_window).mean()
    long_w = min(long_window, len(df))
    long_sma = df["volume"].rolling(window=long_w, min_periods=short_window).mean()
    long_sma_nonzero = long_sma.replace(0, np.nan)
    return short_sma / long_sma_nonzero


# ===========================================================================
# 3. Crossover Functions
# ===========================================================================


def calculate_golden_death_cross(
    df: pd.DataFrame, sma_50_col: str = "sma_50", sma_200_col: str = "sma_200"
) -> tuple[pd.Series, pd.Series]:
    """Calculate Golden Cross (SMA50 crosses above SMA200) and Death Cross."""
    if sma_50_col not in df.columns or sma_200_col not in df.columns:
        raise KeyError(f"Missing required moving average columns: '{sma_50_col}', '{sma_200_col}'")

    sma_50 = df[sma_50_col]
    sma_200 = df[sma_200_col]

    sma_50_prev = sma_50.shift(1)
    sma_200_prev = sma_200.shift(1)

    golden_cross = (sma_50 > sma_200) & (sma_50_prev <= sma_200_prev)
    death_cross = (sma_50 < sma_200) & (sma_50_prev >= sma_200_prev)

    return golden_cross.fillna(False), death_cross.fillna(False)


def calculate_macd_crossovers(
    df: pd.DataFrame, macd_line_col: str = "macd_line", macd_signal_col: str = "macd_signal"
) -> tuple[pd.Series, pd.Series]:
    """Calculate MACD Bullish (cross up) and Bearish (cross down) crossovers."""
    if macd_line_col not in df.columns or macd_signal_col not in df.columns:
        raise KeyError(f"Missing required MACD columns: '{macd_line_col}', '{macd_signal_col}'")

    macd_line = df[macd_line_col]
    macd_signal = df[macd_signal_col]

    macd_line_prev = macd_line.shift(1)
    macd_signal_prev = macd_signal.shift(1)

    macd_cross_up = (macd_line > macd_signal) & (macd_line_prev <= macd_signal_prev)
    macd_cross_down = (macd_line < macd_signal) & (macd_line_prev >= macd_signal_prev)

    return macd_cross_up.fillna(False), macd_cross_down.fillna(False)


def calculate_recent_crossover(crossover_series: pd.Series, window: int = 10) -> pd.Series:
    """Calculate whether a crossover event occurred within the last N sessions."""
    if crossover_series.empty:
        return pd.Series(False, index=crossover_series.index, dtype=bool)

    rolling_recent = crossover_series.astype(int).rolling(window=window, min_periods=1).max()
    return (rolling_recent > 0).fillna(False)


# ===========================================================================
# 4. Momentum Functions
# ===========================================================================


def calculate_rsi(
    df: pd.DataFrame,
    window: int = 14,
    periods: int | None = None,
    column: str = "close",
) -> pd.Series:
    """Calculate Relative Strength Index (RSI) using Wilder's smoothing."""
    n_window = periods if periods is not None else window
    if df.empty or column not in df.columns or len(df) < n_window:
        return pd.Series(np.nan, index=df.index, dtype=float)

    delta = df[column].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1.0 / n_window, min_periods=n_window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / n_window, min_periods=n_window, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))

    rsi = rsi.where(avg_loss != 0, 100.0)
    rsi = rsi.where(avg_gain != 0, 0.0)
    return rsi


def calculate_macd(
    df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Calculate Moving Average Convergence Divergence (MACD)."""
    if df.empty or "close" not in df.columns or len(df) < slow:
        empty = pd.Series(np.nan, index=df.index, dtype=float)
        return empty, empty, empty

    ema_fast = df["close"].ewm(span=fast, adjust=False, min_periods=fast).mean()
    ema_slow = df["close"].ewm(span=slow, adjust=False, min_periods=slow).mean()
    macd_line = ema_fast - ema_slow
    macd_signal = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    macd_hist = macd_line - macd_signal
    return macd_line, macd_signal, macd_hist


def calculate_relative_strength(
    df: pd.DataFrame,
    benchmark_df: pd.DataFrame | None,
    periods_list: tuple[int, ...] = (63, 126, 252),
) -> dict[str, pd.Series]:
    """Calculate relative performance vs benchmark index."""
    results: dict[str, pd.Series] = {}

    if benchmark_df is None or benchmark_df.empty or "date" not in df.columns:
        for p in periods_list:
            results[f"rs_{p}d"] = pd.Series(np.nan, index=df.index, dtype=float)
        return results

    b_df = benchmark_df.copy()
    if not pd.api.types.is_datetime64_any_dtype(b_df["date"]):
        b_df["date"] = pd.to_datetime(b_df["date"]).dt.date

    stock_dates = df["date"]
    if pd.api.types.is_datetime64_any_dtype(stock_dates):
        stock_dates = stock_dates.dt.date

    bench_map = dict(zip(b_df["date"], b_df["close"], strict=False))
    b_close = stock_dates.map(bench_map)

    for p in periods_list:
        stock_ratio = df["close"] / df["close"].shift(p)
        bench_ratio = b_close / b_close.shift(p)
        bench_ratio_clean = bench_ratio.replace(0, np.nan)
        rs = (stock_ratio / bench_ratio_clean) - 1.0
        results[f"rs_{p}d"] = rs

    return results


def calculate_cross_sectional_ranks(
    df: pd.DataFrame, date_col: str = "date"
) -> tuple[pd.Series, pd.Series]:
    """Calculate cross-sectional percentile ranks (0 to 100) for 126d and 252d returns."""
    if df.empty or date_col not in df.columns:
        empty = pd.Series(np.nan, index=df.index, dtype=float)
        return empty, empty

    ret_126 = df.get("pct_change_6m", pd.Series(np.nan, index=df.index))
    ret_252 = df.get("pct_change_12m", pd.Series(np.nan, index=df.index))

    rank_126 = (
        df.groupby(date_col)[ret_126.name].rank(pct=True) * 100.0
        if ret_126.notna().any()
        else pd.Series(np.nan, index=df.index)
    )
    rank_252 = (
        df.groupby(date_col)[ret_252.name].rank(pct=True) * 100.0
        if ret_252.notna().any()
        else pd.Series(np.nan, index=df.index)
    )

    return rank_126, rank_252


def calculate_rsi_momentum(df: pd.DataFrame) -> pd.Series:
    """Calculate composite breakout momentum condition ('RSI Momentum')."""
    if df.empty or "close" not in df.columns:
        return pd.Series(False, index=df.index, dtype=bool)

    rsi_9 = calculate_rsi(df, window=9)
    rsi_9_df = pd.DataFrame({"rsi_9": rsi_9}, index=df.index)
    rsi_9_wma_21 = calculate_wma(rsi_9_df, window=21, column="rsi_9")

    cond_a = (rsi_9 >= rsi_9_wma_21 + 1.1) & (rsi_9 >= 45.0) & (rsi_9 <= 65.0)

    ema_20 = df.get("ema_20", pd.Series(np.nan, index=df.index))
    sma_50 = df.get("sma_50", pd.Series(np.nan, index=df.index))
    sma_200 = df.get("sma_200", pd.Series(np.nan, index=df.index))
    close = df["close"]

    cond_b = (ema_20 > sma_50) & (sma_50 > sma_200) & (close > sma_50) & (close > sma_200)

    high_ref = df.get("high_252d_prev", pd.Series(np.nan, index=df.index))
    cond_c = close >= (0.85 * high_ref)

    vol = df.get("volume", pd.Series(0, index=df.index))
    vol_sma_20 = df.get("volume_sma_20", pd.Series(0, index=df.index))
    cond_d = (vol > 1.5 * vol_sma_20) & (vol_sma_20 > 250_000)

    res = cond_a & cond_b & cond_c & cond_d
    return res.fillna(False)


# ===========================================================================
# 5. Technical Calculator Facade Class
# ===========================================================================


class TechnicalCalculator:
    """Computes technical, quantitative, and momentum indicators."""

    schema_version: int = INDICATORS_SCHEMA_VERSION

    # ------------------------------------------------------------------
    # Forwarding classmethods for price-based indicators
    # ------------------------------------------------------------------
    @classmethod
    def calculate_sma(cls, df: pd.DataFrame, window: int, column: str = "close") -> pd.Series:
        return calculate_sma(df, window=window, column=column)

    @classmethod
    def calculate_ema(cls, df: pd.DataFrame, span: int, column: str = "close") -> pd.Series:
        return calculate_ema(df, span=span, column=column)

    @classmethod
    def calculate_wma(cls, df: pd.DataFrame, window: int, column: str = "close") -> pd.Series:
        return calculate_wma(df, window=window, column=column)

    @classmethod
    def calculate_bollinger_bands(
        cls, df: pd.DataFrame, window: int = 20, num_std: float = 2.0
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        return calculate_bollinger_bands(df, window=window, num_std=num_std)

    @classmethod
    def calculate_true_range(cls, df: pd.DataFrame) -> pd.Series:
        return calculate_true_range(df)

    @classmethod
    def calculate_atr(cls, df: pd.DataFrame, window: int = 14) -> tuple[pd.Series, pd.Series]:
        return calculate_atr(df, window=window)

    @classmethod
    def calculate_donchian_channels(
        cls, df: pd.DataFrame, window: int = 20
    ) -> tuple[pd.Series, pd.Series]:
        return calculate_donchian_channels(df, window=window)

    @classmethod
    def calculate_52w_high_low(
        cls, df: pd.DataFrame, window: int = 252
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        return calculate_52w_high_low(df, window=window)

    @classmethod
    def calculate_shifted_rolling_high(
        cls, df: pd.DataFrame, window: int = 252, shift_days: int = 1, column: str = "high"
    ) -> pd.Series:
        return calculate_shifted_rolling_high(
            df, window=window, shift_days=shift_days, column=column
        )

    @classmethod
    def calculate_shifted_price(cls, df: pd.DataFrame, shift_days: int) -> pd.Series:
        return calculate_shifted_price(df, shift_days=shift_days)

    @classmethod
    def calculate_pct_change(cls, df: pd.DataFrame, periods: int) -> pd.Series:
        return calculate_pct_change(df, periods=periods)

    @classmethod
    def calculate_daily_returns(cls, df: pd.DataFrame) -> pd.Series:
        return calculate_daily_returns(df)

    @classmethod
    def calculate_volatility(cls, df: pd.DataFrame, window: int = 20) -> pd.Series:
        return calculate_volatility(df, window=window)

    # ------------------------------------------------------------------
    # Forwarding classmethods for volume-based indicators
    # ------------------------------------------------------------------
    @classmethod
    def calculate_volume_sma(cls, df: pd.DataFrame, window: int = 20) -> pd.Series:
        return calculate_volume_sma(df, window=window)

    @classmethod
    def calculate_volume_ratio(cls, df: pd.DataFrame, window: int = 20) -> pd.Series:
        return calculate_volume_ratio(df, window=window)

    @classmethod
    def calculate_volume_dryup(
        cls, df: pd.DataFrame, short_window: int = 5, long_window: int = 50
    ) -> pd.Series:
        return calculate_volume_dryup(df, short_window=short_window, long_window=long_window)

    # ------------------------------------------------------------------
    # Forwarding classmethods for crossover indicators
    # ------------------------------------------------------------------
    @classmethod
    def calculate_golden_death_cross(
        cls, df: pd.DataFrame, sma_50_col: str = "sma_50", sma_200_col: str = "sma_200"
    ) -> tuple[pd.Series, pd.Series]:
        return calculate_golden_death_cross(df, sma_50_col=sma_50_col, sma_200_col=sma_200_col)

    @classmethod
    def calculate_macd_crossovers(
        cls,
        df: pd.DataFrame,
        macd_line_col: str = "macd_line",
        macd_signal_col: str = "macd_signal",
    ) -> tuple[pd.Series, pd.Series]:
        return calculate_macd_crossovers(
            df, macd_line_col=macd_line_col, macd_signal_col=macd_signal_col
        )

    @classmethod
    def calculate_recent_cross(cls, crossover_series: pd.Series, window: int = 10) -> pd.Series:
        return calculate_recent_crossover(crossover_series, window=window)

    # ------------------------------------------------------------------
    # Forwarding classmethods for momentum indicators
    # ------------------------------------------------------------------
    @classmethod
    def calculate_rsi(
        cls,
        df: pd.DataFrame,
        window: int = 14,
        periods: int | None = None,
        column: str = "close",
    ) -> pd.Series:
        return calculate_rsi(df, window=window, periods=periods, column=column)

    @classmethod
    def calculate_macd(
        cls, df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        return calculate_macd(df, fast=fast, slow=slow, signal=signal)

    @classmethod
    def calculate_relative_strength(
        cls,
        df: pd.DataFrame,
        benchmark_df: pd.DataFrame | None,
        periods_list: tuple[int, ...] = (63, 126, 252),
    ) -> dict[str, pd.Series]:
        return calculate_relative_strength(df, benchmark_df=benchmark_df, periods_list=periods_list)

    @classmethod
    def calculate_cross_sectional_ranks(
        cls, df: pd.DataFrame, date_col: str = "date"
    ) -> tuple[pd.Series, pd.Series]:
        return calculate_cross_sectional_ranks(df, date_col=date_col)

    @classmethod
    def calculate_rsi_momentum(cls, df: pd.DataFrame) -> pd.Series:
        return calculate_rsi_momentum(df)

    # ------------------------------------------------------------------
    # Orchestration Engine
    # ------------------------------------------------------------------
    @classmethod
    def _apply_indicators_to_series(
        cls, df: pd.DataFrame, benchmark_df: pd.DataFrame | None = None
    ) -> pd.DataFrame:
        """Apply all indicator domains to a chronological single-symbol series."""
        if df.empty or len(df) < 2:
            return df.copy()

        res = df.copy()

        # 1. Price-based indicators
        res["sma_20"] = calculate_sma(res, window=20)
        res["sma_50"] = calculate_sma(res, window=50)
        res["sma_150"] = calculate_sma(res, window=150)
        res["sma_200"] = calculate_sma(res, window=200)

        # SMA 200 slope (21 sessions)
        sma_200_shift21 = res["sma_200"].shift(21)
        res["sma_200_slope_21d"] = (res["sma_200"] / sma_200_shift21.replace(0, np.nan)) - 1.0

        res["ema_12"] = calculate_ema(res, span=12)
        res["ema_20"] = calculate_ema(res, span=20)
        res["ema_26"] = calculate_ema(res, span=26)

        # Bollinger Bands & width
        bb_mid, bb_up, bb_low = calculate_bollinger_bands(res, window=20, num_std=2.0)
        res["bb_middle"] = bb_mid
        res["bb_upper"] = bb_up
        res["bb_lower"] = bb_low
        bb_mid_nonzero = bb_mid.replace(0, np.nan)
        res["bb_width"] = (bb_up - bb_low) / bb_mid_nonzero

        # ATR & ATR%
        atr, atr_pct = calculate_atr(res, window=14)
        res["atr_14"] = atr
        res["atr_pct"] = atr_pct
        res["atr_pct_max_60d"] = atr_pct.rolling(window=min(60, len(res)), min_periods=1).max()

        # Donchian Channels
        d_high_20, d_low_20 = calculate_donchian_channels(res, window=20)
        res["donchian_high_20"] = d_high_20
        res["donchian_low_20"] = d_low_20
        d_high_55, d_low_55 = calculate_donchian_channels(res, window=55)
        res["donchian_high_55"] = d_high_55
        res["donchian_low_55"] = d_low_55

        # 52w High / Low
        h_52, l_52, h_252_prev = calculate_52w_high_low(res, window=252)
        res["high_52w"] = h_52
        res["low_52w"] = l_52
        res["high_252d_prev"] = h_252_prev

        # Returns & Volatility
        res["daily_returns"] = calculate_daily_returns(res)
        res["volatility_20"] = calculate_volatility(res, window=20)

        # Lookbacks: 1D, 1M (21d), 3M (63d), 6M (126d), 12M (252d)
        res["pct_change_daily"] = calculate_pct_change(res, periods=1)
        res["pct_change_1m"] = calculate_pct_change(res, periods=21)
        res["pct_change_3m"] = calculate_pct_change(res, periods=63)
        res["pct_change_6m"] = calculate_pct_change(res, periods=126)
        res["pct_change_12m"] = calculate_pct_change(res, periods=252)

        # Shifted prices
        res["close_60d"] = calculate_shifted_price(res, shift_days=60)
        res["close_120d"] = calculate_shifted_price(res, shift_days=120)
        res["close_180d"] = calculate_shifted_price(res, shift_days=180)
        res["close_252d"] = calculate_shifted_price(res, shift_days=252)

        c60_nz = res["close_60d"].replace(0, np.nan)
        c120_nz = res["close_120d"].replace(0, np.nan)
        res["return_60d"] = ((res["close"] - res["close_60d"]) / c60_nz) * 100.0
        res["return_120d"] = ((res["close"] - res["close_120d"]) / c120_nz) * 100.0

        # 2. Volume-based indicators
        res["volume_sma_20"] = calculate_volume_sma(res, window=20)
        res["volume_ratio"] = calculate_volume_ratio(res, window=20)
        res["vol_dryup"] = calculate_volume_dryup(res, short_window=5, long_window=50)

        # Derived convenience metrics for UI & screener consumption
        v_nz = res["volume_sma_20"].replace(0, np.nan)
        res["vol_multiple"] = res["volume"] / v_nz
        h_nz = res["high_252d_prev"].replace(0, np.nan)
        res["pct_from_52w_high"] = ((res["close"] - res["high_252d_prev"]) / h_nz) * 100.0

        # 3. Momentum & Oscillator indicators
        res["rsi_9"] = calculate_rsi(res, window=9)
        rsi_9_df = pd.DataFrame({"rsi_9": res["rsi_9"]}, index=res.index)
        res["rsi_9_wma_21"] = calculate_wma(rsi_9_df, window=21, column="rsi_9")
        res["rsi_14"] = calculate_rsi(res, window=14)

        macd_line, macd_sig, macd_hist = calculate_macd(res, fast=12, slow=26, signal=9)
        res["macd_line"] = macd_line
        res["macd_signal"] = macd_sig
        res["macd_histogram"] = macd_hist

        # Relative strength vs benchmark
        rs_dict = calculate_relative_strength(
            res, benchmark_df=benchmark_df, periods_list=(63, 126, 252)
        )
        for k, v in rs_dict.items():
            res[k] = v

        # Composite breakout rule (RSI Momentum)
        res["rsi_momentum"] = calculate_rsi_momentum(res)

        # 4. Crossovers
        g_cross, d_cross = calculate_golden_death_cross(res)
        res["golden_cross"] = g_cross
        res["death_cross"] = d_cross
        res["golden_cross_10d"] = calculate_recent_crossover(g_cross, window=10)

        m_up, m_down = calculate_macd_crossovers(res)
        res["macd_cross_up"] = m_up
        res["macd_cross_down"] = m_down

        return res

    @classmethod
    def apply_all_indicators(
        cls, df: pd.DataFrame, benchmark_df: pd.DataFrame | None = None
    ) -> pd.DataFrame:
        """Apply all indicator domains to a dataframe across symbols and dates."""
        if df.empty:
            return df.copy()

        if "symbol" in df.columns and df["symbol"].nunique() > 1:
            sorted_df = df.sort_values(by=["symbol", "date"]) if "date" in df.columns else df
            symbol_frames = []
            for _, grp in sorted_df.groupby("symbol", as_index=False):
                computed = cls._apply_indicators_to_series(grp, benchmark_df=benchmark_df)
                symbol_frames.append(computed)
            res = pd.concat(symbol_frames).sort_index()
        else:
            sorted_df = df.sort_values(by="date") if "date" in df.columns else df
            res = cls._apply_indicators_to_series(sorted_df, benchmark_df=benchmark_df)

        # Cross-sectional ranks across symbols for each date
        if "date" in res.columns:
            r126, r252 = calculate_cross_sectional_ranks(res, date_col="date")
            res["ret_rank_126d"] = r126
            res["ret_rank_252d"] = r252
        else:
            res["ret_rank_126d"] = np.nan
            res["ret_rank_252d"] = np.nan

        return res

    @classmethod
    def compute_snapshot(
        cls,
        df: pd.DataFrame,
        symbols: Sequence[str] | None = None,
        benchmark_df: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """Compute the latest cross-sectional snapshot across all requested symbols."""
        if df.empty:
            return pd.DataFrame()

        computed_df = cls.apply_all_indicators(df, benchmark_df=benchmark_df)

        if symbols is not None:
            computed_df = computed_df[computed_df["symbol"].isin(symbols)]

        if computed_df.empty:
            return pd.DataFrame()

        # Extract latest bar per symbol
        if "date" in computed_df.columns:
            latest_idx = computed_df.groupby("symbol")["date"].idxmax()
            return computed_df.loc[latest_idx].reset_index(drop=True)

        return computed_df.groupby("symbol").last().reset_index()
