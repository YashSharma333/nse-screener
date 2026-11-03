"""
Momentum & Oscillator Technical Indicators
==========================================
Contains velocity, strength, oscillator, and comparative performance indicators:
Relative Strength Index (RSI), MACD, Relative Performance vs Benchmark (RS),
Cross-Sectional Return Percentile Ranks, and the custom HMM composite momentum rule.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.computation.indicators.price import calculate_wma


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

    # Wilder's exponential smoothing (alpha = 1 / window)
    avg_gain = gain.ewm(alpha=1.0 / n_window, min_periods=n_window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / n_window, min_periods=n_window, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))

    # Boundary conditions when loss is 0 (pure gains)
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
    """Calculate relative performance vs benchmark index.

    Formula for lookback N:
        stock_perf = close / close.shift(N)
        bench_perf = bench_close / bench_close.shift(N)
        rs_Nd = (stock_perf / bench_perf) - 1.0

    A value of +0.10 indicates the stock outperformed the benchmark by 10% over N days.
    Benchmark closes are aligned strictly by date (never by position or unaligned index).
    """
    results: dict[str, pd.Series] = {}

    if benchmark_df is None or benchmark_df.empty or "date" not in df.columns:
        for p in periods_list:
            results[f"rs_{p}d"] = pd.Series(np.nan, index=df.index, dtype=float)
        return results

    # Normalize dates and map benchmark close by date
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
    """Calculate cross-sectional percentile ranks (0 to 100) for 126d and 252d returns.

    Stocks are ranked against all active universe stocks on the same calendar date.
    """
    if df.empty or date_col not in df.columns:
        empty = pd.Series(np.nan, index=df.index, dtype=float)
        return empty, empty

    ret_126 = df.get("pct_change_6m", pd.Series(np.nan, index=df.index))
    ret_252 = df.get("pct_change_12m", pd.Series(np.nan, index=df.index))

    # Rank within each date cross-section (pct=True scales from ~0.0 to 1.0)
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


def calculate_hmm_indicator(df: pd.DataFrame) -> pd.Series:
    """Calculate custom composite breakout momentum condition ('HMM').

    NOTE: Despite the legacy naming, this indicator is NOT a probabilistic
    Hidden Markov Model. It is a deterministic rule-based composite filter
    combining:
      1. Condition A: RSI(9) >= WMA(RSI(9), 21) + 1.1 and 45 <= RSI(9) <= 65
      2. Condition B: EMA(20) > SMA(50) > SMA(200) and Close > SMA(50) & SMA(200)
      3. Condition C: Close >= 0.85 * (1-day shifted 252d High)
      4. Condition D: Volume > 1.5 * Volume_SMA(20) and Volume_SMA(20) > 250,000
    """
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

    hmm = cond_a & cond_b & cond_c & cond_d
    return hmm.fillna(False)
