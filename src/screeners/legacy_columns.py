"""
Cached column names that the resolver's names stand for (temporary, deleted in CL-25)
=====================================================================================
The indicator cache still uses its old column names (``sma_50``, ``rsi_14``, ``high_52w`` ...).
A screener now asks the resolver for ``sma_close_50`` and so on. When the cache already holds that
very number under its old name, ``Screener.screen`` copies the cached column instead of computing
it again. That is faster, and it is also exact: the cache was computed from the full database
history, while recomputing from the cached rows alone starts the moving averages (RSI, EMA)
later and differs in the last digits.

CL-25 rebuilds the cache with the resolver's own names, and this file goes away.
"""

from __future__ import annotations

CACHED_NAME: dict[str, str] = {
    "sma_close_20": "sma_20",
    "sma_close_50": "sma_50",
    "sma_close_150": "sma_150",
    "sma_close_200": "sma_200",
    "ema_close_12": "ema_12",
    "ema_close_20": "ema_20",
    "ema_close_26": "ema_26",
    "sma_volume_20": "volume_sma_20",
    "rsi_close_9": "rsi_9",
    "rsi_close_14": "rsi_14",
    "wma_rsi_close_9_21": "rsi_9_wma_21",
    "volatility_close_20": "volatility_20",
    "window_high_252_max": "high_52w",
    "window_low_252_min": "low_52w",
    "window_high_252_max_ago1": "high_252d_prev",
    "bollinger_bands_close_20_2_upper": "bb_upper",
    "bollinger_bands_close_20_2_lower": "bb_lower",
    "macd_close_12_26_9_line": "macd_line",
    "macd_close_12_26_9_signal": "macd_signal",
    "value_close_ago60": "close_60d",
    "value_close_ago120": "close_120d",
    "value_close_ago180": "close_180d",
    "value_close_ago252": "close_252d",
}
