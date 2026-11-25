"""
Day pickers (CL-23C)
====================
Small helpers to pick a value or a date from the past::

    value_ago(df, "close", 120)             # every stock's close 120 rows (trading days) ago
    value_ago(df, "rsi_14", 5)              # works on indicator columns too
    value_on(df, "close", date(2026, 4, 1)) # every stock's close on that exact day
    trading_date_ago(dates, today, 20)      # the trading date 20 sessions before today
    previous_trading_dates(dates, today, 5) # the 5 trading dates before today, oldest first
    trading_dates_between(dates, a, b)      # every trading date from a to b, both included

There is no calendar maths here. ``value_ago`` counts rows inside each stock, and the date helpers
take the market's real trading dates (from the price table), so weekends, holidays and special
sessions are handled by the data itself.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

import numpy as np
import pandas as pd

from src.computation.indicators.grouped import check_days_ago, require, shift, stock_keys


def _as_date(value: date | pd.Timestamp) -> date:
    """A plain ``date`` from a date or a Timestamp."""
    return pd.Timestamp(value).date()


def _sorted_dates(dates: Iterable[date | pd.Timestamp]) -> list[date]:
    """The distinct trading dates as plain dates, oldest first."""
    return sorted({_as_date(d) for d in dates})


def value_ago(df: pd.DataFrame, field: str, days_ago: int) -> pd.Series:
    """The value of column ``field`` ``days_ago`` rows earlier, inside each stock.

    ``field`` is any column: a price, the volume or an indicator. 0 is today's value. The first
    ``days_ago`` rows of every stock are NaN, and one stock never sees another stock's rows.
    """
    check_days_ago(days_ago)
    require(df, field)
    return shift(df, df[field], days_ago)


def value_on(df: pd.DataFrame, field: str, on_date: date | pd.Timestamp) -> pd.Series:
    """The value of column ``field`` on one exact day, repeated on every row of that stock.

    A stock with no row on ``on_date`` (it did not trade, or the market was shut) gets NaN.
    """
    require(df, field, "date")
    on_day = pd.to_datetime(df["date"]).dt.normalize() == pd.Timestamp(on_date).normalize()
    keys = stock_keys(df)
    # last() keeps one value per stock if a table ever has a repeated (stock, date) row
    on_that_day = df.loc[on_day, field].groupby(keys[on_day.to_numpy()]).last()
    return pd.Series(keys, index=df.index).map(on_that_day)


def _position_ago(trading_dates: list[date], today: date, n: int) -> int:
    """Index in the sorted trading dates of the session ``n`` before ``today``.

    Raises a ValueError when ``today`` is not a trading date or fewer than ``n`` sessions
    come before it.
    """
    check_days_ago(n)  # same rule as value_ago: a whole number of 0 or more
    position = int(np.searchsorted(trading_dates, today))
    if position == len(trading_dates) or trading_dates[position] != today:
        raise ValueError(f"{today} is not a trading date in the given dates")
    if n > position:
        raise ValueError(f"asked for {n} sessions before {today}, only {position} earlier exist")
    return position - n


def previous_trading_dates(
    dates: Iterable[date | pd.Timestamp], today: date | pd.Timestamp, n: int
) -> list[date]:
    """The ``n`` trading dates before ``today`` (not including it), oldest first."""
    trading = _sorted_dates(dates)
    first = _position_ago(trading, _as_date(today), n)
    return trading[first : first + n]


def trading_date_ago(
    dates: Iterable[date | pd.Timestamp], today: date | pd.Timestamp, n: int
) -> date:
    """The trading date ``n`` sessions before ``today`` (0 is ``today`` itself)."""
    trading = _sorted_dates(dates)
    return trading[_position_ago(trading, _as_date(today), n)]


def trading_dates_between(
    dates: Iterable[date | pd.Timestamp], start: date | pd.Timestamp, end: date | pd.Timestamp
) -> list[date]:
    """Every trading date from ``start`` to ``end``, both included, oldest first.

    The bounds need not be trading dates themselves. An ``end`` before ``start`` gives [].
    """
    first, last = _as_date(start), _as_date(end)
    return [d for d in _sorted_dates(dates) if first <= d <= last]
