"""Day pickers (CL-23C): value N days ago, value on an exact date, previous trading dates."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from src.computation.indicators.pickers import (
    previous_trading_dates,
    trading_date_ago,
    trading_dates_between,
    value_ago,
    value_on,
)
from src.computation.indicators.price_helpers import window_stat


def _table() -> pd.DataFrame:
    """Two stocks, sorted by symbol then date. B has no row on 2026-04-03 (it did not trade)."""
    a_dates = [date(2026, 4, 1), date(2026, 4, 2), date(2026, 4, 3), date(2026, 4, 6)]
    b_dates = [date(2026, 4, 1), date(2026, 4, 2), date(2026, 4, 6)]
    return pd.DataFrame(
        {
            "symbol": ["A"] * 4 + ["B"] * 3,
            "date": a_dates + b_dates,
            "close": [10.0, 11.0, 12.0, 13.0, 100.0, 110.0, 130.0],
            "volume": [1, 2, 3, 4, 10, 20, 40],
        }
    )


# A trading calendar with a gap: 2026-04-03 (Fri) is a holiday, 04-04/05 a weekend.
TRADING = [
    date(2026, 4, 1),
    date(2026, 4, 2),
    date(2026, 4, 6),
    date(2026, 4, 7),
    date(2026, 4, 8),
]


# ---------------------------------------------------------------------------
# value_ago
# ---------------------------------------------------------------------------


def test_value_ago_is_the_value_n_rows_earlier_inside_each_stock() -> None:
    result = value_ago(_table(), "close", 3)
    assert result.iloc[3] == 10.0  # A: 3 rows before 04-06 is 04-01
    assert result.iloc[:3].isna().all()
    assert result.iloc[4:].isna().all()  # B has only 3 rows: nothing 3 rows ago


def test_value_ago_never_leaks_across_stocks() -> None:
    result = value_ago(_table(), "close", 1)
    assert np.isnan(result.iloc[0])  # A's first row
    assert np.isnan(result.iloc[4])  # B's first row must NOT get A's last close (13.0)
    assert result.iloc[5] == 100.0


def test_value_ago_zero_is_the_column_itself_and_any_column_works() -> None:
    df = _table()
    assert value_ago(df, "close", 0).tolist() == df["close"].tolist()
    assert value_ago(df.assign(rsi=[1.0] * 7), "rsi", 1).iloc[1] == 1.0  # an indicator column
    assert value_ago(df, "volume", 1).iloc[2] == 2.0


def test_value_ago_table_without_symbol_is_one_stock() -> None:
    df = _table().loc[:3].drop(columns="symbol")
    assert value_ago(df, "close", 2).tolist()[2:] == [10.0, 11.0]


def test_value_ago_bad_arguments_raise() -> None:
    with pytest.raises(ValueError, match="days_ago"):
        value_ago(_table(), "close", -1)
    with pytest.raises(ValueError, match="days_ago"):
        value_ago(_table(), "close", 1.5)  # type: ignore[arg-type]
    with pytest.raises(KeyError, match="nope"):
        value_ago(_table(), "nope", 1)


# ---------------------------------------------------------------------------
# value_on
# ---------------------------------------------------------------------------


def test_value_on_gives_each_stock_its_value_on_that_day() -> None:
    result = value_on(_table(), "close", date(2026, 4, 2))
    assert result.iloc[:4].tolist() == [11.0] * 4  # every row of A carries A's value
    assert result.iloc[4:].tolist() == [110.0] * 3


def test_value_on_is_nan_for_a_stock_with_no_row_that_day() -> None:
    result = value_on(_table(), "close", date(2026, 4, 3))
    assert result.iloc[:4].tolist() == [12.0] * 4
    assert result.iloc[4:].isna().all()  # B did not trade on 04-03


def test_value_on_a_date_nobody_traded_is_all_nan() -> None:
    assert value_on(_table(), "close", date(2026, 4, 4)).isna().all()


def test_value_on_a_weekend_special_session() -> None:
    special = pd.DataFrame(
        {
            "symbol": ["A", "A", "A"],
            "date": [date(2026, 4, 3), date(2026, 4, 4), date(2026, 4, 6)],  # 04-04 is a Saturday
            "close": [1.0, 2.0, 3.0],
        }
    )
    assert value_on(special, "close", date(2026, 4, 4)).tolist() == [2.0, 2.0, 2.0]


def test_value_on_accepts_timestamps_and_datetime_columns() -> None:
    df = _table().assign(date=lambda t: pd.to_datetime(t["date"]))
    result = value_on(df, "close", pd.Timestamp("2026-04-02"))
    assert result.iloc[0] == 11.0


def test_value_on_table_without_symbol_is_one_stock() -> None:
    df = _table().loc[:3].drop(columns="symbol")
    assert value_on(df, "close", date(2026, 4, 3)).tolist() == [12.0] * 4


# ---------------------------------------------------------------------------
# trading-date helpers
# ---------------------------------------------------------------------------


def test_trading_date_ago_skips_holidays_and_weekends() -> None:
    assert trading_date_ago(TRADING, date(2026, 4, 6), 1) == date(2026, 4, 2)  # not 04-05
    assert trading_date_ago(TRADING, date(2026, 4, 8), 4) == date(2026, 4, 1)
    assert trading_date_ago(TRADING, date(2026, 4, 8), 0) == date(2026, 4, 8)


def test_trading_date_ago_ignores_input_order_and_duplicates() -> None:
    shuffled = [TRADING[3], TRADING[0], TRADING[2], TRADING[1], TRADING[1], TRADING[4]]
    assert trading_date_ago(shuffled, date(2026, 4, 7), 2) == date(2026, 4, 2)


def test_trading_date_ago_errors() -> None:
    with pytest.raises(ValueError, match="not a trading date"):
        trading_date_ago(TRADING, date(2026, 4, 3), 1)
    with pytest.raises(ValueError, match="only 1 earlier"):
        trading_date_ago(TRADING, date(2026, 4, 2), 2)
    with pytest.raises(ValueError, match="days_ago must"):
        trading_date_ago(TRADING, date(2026, 4, 8), -1)


def test_previous_trading_dates_returns_exactly_n_in_order() -> None:
    assert previous_trading_dates(TRADING, date(2026, 4, 8), 3) == [
        date(2026, 4, 2),
        date(2026, 4, 6),
        date(2026, 4, 7),
    ]
    assert previous_trading_dates(TRADING, date(2026, 4, 8), 0) == []
    assert date(2026, 4, 8) not in previous_trading_dates(TRADING, date(2026, 4, 8), 4)


def test_previous_trading_dates_needs_enough_history() -> None:
    with pytest.raises(ValueError, match="only 1 earlier"):
        previous_trading_dates(TRADING, date(2026, 4, 2), 2)


def test_trading_dates_between_is_inclusive_and_uses_real_dates() -> None:
    assert trading_dates_between(TRADING, date(2026, 4, 2), date(2026, 4, 7)) == [
        date(2026, 4, 2),
        date(2026, 4, 6),
        date(2026, 4, 7),
    ]
    # the bounds need not be trading dates themselves
    assert trading_dates_between(TRADING, date(2026, 4, 3), date(2026, 4, 5)) == []
    assert trading_dates_between(TRADING, date(2026, 4, 8), date(2026, 4, 1)) == []


def test_trading_dates_accept_timestamps_and_a_pandas_series() -> None:
    series = pd.Series(pd.to_datetime(TRADING))
    assert trading_date_ago(series, pd.Timestamp("2026-04-06"), 1) == date(2026, 4, 2)


@pytest.mark.parametrize("stat", ["max", "min", "mean", "sum"])
@pytest.mark.parametrize("days_ago", [0, 1, 3])
def test_window_stat_days_ago_equals_value_ago_of_window_stat(stat: str, days_ago: int) -> None:
    """Looking back inside window_stat or after it must give the same numbers."""
    rng = np.random.default_rng(0)
    dates = pd.bdate_range("2026-01-01", periods=12).date
    df = pd.DataFrame(
        {
            "symbol": ["A"] * 12 + ["B"] * 12,
            "date": list(dates) * 2,
            "high": rng.uniform(10, 20, 24),
        }
    )
    inside = window_stat(df, "high", 4, stat, days_ago=days_ago)
    after = value_ago(df.assign(w=window_stat(df, "high", 4, stat)), "w", days_ago)
    pd.testing.assert_series_equal(inside, after, check_names=False)
