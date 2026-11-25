"""
Window helper and small arithmetic helpers (CL-23B)
===================================================
``window_stat`` answers one question for every stock at once: "the highest / lowest / average /
sum / count of SOURCE over the last N rows, optionally ending D rows ago"::

    window_stat(df, "high", 252, "max")             # 52-week high
    window_stat(df, "low", 20, "min")               # 20-day low
    window_stat(df, "high", 252, "max", days_ago=1) # yesterday's 252-day high
    window_stat(df, "volume", 20, "mean")           # average volume
    window_stat(df, "up", 5, "count")               # days in the last 5 that "up" was true

``source`` can be any column of the table: open, high, low, close, volume, or an indicator
column such as ``rsi_14`` (indicators stay in their own functions and are passed in by name).

The window is strict: a stock with fewer than ``length`` rows gets NaN (the old "52-week" value
accepted 20 rows; that was finding B9 and is not copied). Everything runs inside each stock.

``pct_diff``, ``ratio`` and ``absolute`` are the small helpers for "percent from the high",
"volume multiple" and "distance either way".
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.computation.indicators.grouped import (
    check_days_ago,
    check_length,
    require,
    roll,
    shift,
)

STATS = ("max", "min", "mean", "sum", "count")


def value(df: pd.DataFrame, source: str = "close") -> pd.Series:
    """The column ``source`` itself (with the resolver's ``days_ago``: its value N days ago)."""
    require(df, source)
    return df[source]


def window_stat(
    df: pd.DataFrame,
    source: str = "high",
    length: int = 252,
    stat: str = "max",
    days_ago: int = 0,
) -> pd.Series:
    """The ``stat`` of ``source`` over the last ``length`` rows of each stock.

    ``stat`` is "max", "min", "mean", "sum" or "count" (count needs a true/false ``source`` and
    returns how many of the rows were true). The window ends ``days_ago`` rows ago (0 = today).
    """
    check_length(length)
    check_days_ago(days_ago)
    if stat not in STATS:
        raise ValueError(f"stat must be one of {list(STATS)}, got {stat!r}")
    require(df, source)

    values = df[source]
    if stat == "count":
        if not pd.api.types.is_bool_dtype(values):
            raise ValueError(
                f"stat='count' needs a true/false (bool) column; {source!r} is {values.dtype}"
            )
        result = roll(df, values.astype(int), length, "sum")
    else:
        result = roll(df, values, length, stat)
    return result if days_ago == 0 else shift(df, result, days_ago)


def pct_diff(a: pd.Series, b: pd.Series) -> pd.Series:
    """Percent difference of ``a`` from ``b``: (a - b) / b * 100 (NaN when b is 0)."""
    return ((a - b) / b.replace(0, np.nan)) * 100.0


def ratio(a: pd.Series, b: pd.Series) -> pd.Series:
    """``a`` divided by ``b`` (NaN when b is 0, never infinity)."""
    return a / b.replace(0, np.nan)


def absolute(a: pd.Series) -> pd.Series:
    """The absolute value of one series (works on a value, not a window)."""
    return a.abs()
