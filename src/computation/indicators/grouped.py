"""
The shared "inside each stock" helpers of the indicator functions (CL-23)
=========================================================================
Every rolling, ewm and shift in the indicator package runs through these, so one stock never
sees another stock's rows and the checks (a column must exist, ``length`` must be 1 or more)
are written once. ``calculator.py`` and ``price_helpers.py`` import from here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def check_length(length: int, name: str = "length") -> None:
    """Reject a window that is not a whole number of at least 1."""
    if isinstance(length, bool) or not isinstance(length, (int, np.integer)) or length < 1:
        raise ValueError(f"{name} must be a whole number of at least 1, got {length!r}")


def require(df: pd.DataFrame, *columns: str) -> None:
    """Raise a clear KeyError when a needed column is not in the table."""
    missing = [name for name in columns if name not in df.columns]
    if missing:
        raise KeyError(f"Missing column(s) {missing}; the table has {list(df.columns)}")


def check_days_ago(days_ago: int) -> None:
    """Reject a look-back that is not a whole number of 0 or more (0 means today)."""
    if isinstance(days_ago, bool) or not isinstance(days_ago, (int, np.integer)) or days_ago < 0:
        raise ValueError(f"days_ago must be a whole number of 0 or more, got {days_ago!r}")


def check_output(output: str, allowed: tuple[str, ...]) -> None:
    if output not in allowed:
        raise ValueError(f"output must be one of {list(allowed)}, got {output!r}")


def stock_keys(df: pd.DataFrame) -> np.ndarray:
    """The stock of every row (one constant key when the table has no ``symbol`` column)."""
    if "symbol" in df.columns:
        return df["symbol"].to_numpy()
    return np.zeros(len(df), dtype=np.int8)


def in_table_order(df: pd.DataFrame, grouped: pd.Series) -> pd.Series:
    """Put a groupby-rolling/ewm result (stock by stock) back in the table's row order."""
    codes = pd.factorize(stock_keys(df))[0]
    order = np.argsort(codes, kind="stable")  # same order groupby(sort=False) produced
    values = np.empty(len(df), dtype=float)
    values[order] = grouped.to_numpy(dtype=float)
    return pd.Series(values, index=df.index)


def roll(
    df: pd.DataFrame, series: pd.Series, length: int, stat: str, min_periods: int | None = None
) -> pd.Series:
    """Rolling ``stat`` ("mean", "max", "min", "sum", "std") of ``series`` inside each stock."""
    window = series.astype(float).groupby(stock_keys(df), sort=False)
    rolled = window.rolling(length, min_periods=length if min_periods is None else min_periods)
    return in_table_order(df, getattr(rolled, stat)())


def ewm(df: pd.DataFrame, series: pd.Series, **kwargs: float | bool | int) -> pd.Series:
    """Exponential moving average of ``series`` inside each stock (pandas ewm arguments)."""
    grouped = series.astype(float).groupby(stock_keys(df), sort=False).ewm(**kwargs)
    return in_table_order(df, grouped.mean())


def shift(df: pd.DataFrame, series: pd.Series, periods: int) -> pd.Series:
    """``series`` moved down ``periods`` rows inside each stock (the first rows are NaN)."""
    return series.groupby(stock_keys(df), sort=False).shift(periods)
