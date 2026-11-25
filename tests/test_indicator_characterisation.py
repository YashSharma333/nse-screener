"""CL-23: today's indicator output, frozen, so the rewrite is proven to change no value.

The fixtures were made once by running the old ``TechnicalCalculator.apply_all_indicators`` on a
small seeded table (3 stocks, shuffled rows):

* AAA: 400 trading days, with one zero-volume day;
* BBB: 60 days (shorter than the 200- and 252-day windows);
* CCC: 1 day (a day-one listing; the old code left every indicator column empty for it).

``add_all_indicators`` is temporary (deleted in CL-25) and must reproduce this exactly, bit for
bit, including the partial-window quirks CL-26 fixes later, with ONE documented difference
(CL-23B, finding B9): the 52-week columns need a full 252 rows of history. The old code accepted
20 rows, so new listings passed "near the 52-week high" rules on a 20-day high.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from src.computation.indicators.calculator import add_all_indicators

FIXTURES = Path(__file__).parent / "fixtures"

# Deleted on purpose in CL-23: nothing reads the Donchian channels.
DELETED_COLUMNS = ["donchian_high_20", "donchian_low_20", "donchian_high_55", "donchian_low_55"]


# The fixture was made on a Mac; Linux rounds the last digit of some ewm values differently
# (1e-16 relative). That is platform noise, not a logic change (golden proved bit-exact equality
# on one machine), so floats are compared to 10 significant digits; booleans and NaN positions
# stay exact.
FLOAT_TOLERANCE = {"check_exact": False, "rtol": 1e-10, "atol": 1e-12}


def _assert_same(result: pd.DataFrame, frozen: pd.DataFrame) -> None:
    """Float columns to 10 digits, every other column (bool, text, dates) exactly."""
    floats = [c for c in frozen.columns if pd.api.types.is_float_dtype(frozen[c])]
    others = [c for c in frozen.columns if c not in floats]
    pd.testing.assert_frame_equal(
        result[floats], frozen[floats], check_like=True, **FLOAT_TOLERANCE
    )
    if others:
        pd.testing.assert_frame_equal(
            result[others], frozen[others], check_like=True, check_exact=True
        )


def _load() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    table = pd.read_parquet(FIXTURES / "indicators_before_input.parquet")
    bench = pd.read_parquet(FIXTURES / "indicators_before_benchmark.parquet")
    frozen = pd.read_parquet(FIXTURES / "indicators_before_output.parquet")
    return table, bench, frozen.drop(columns=DELETED_COLUMNS)


# Columns that depend on the 52-week window, and the history (rows) a stock needs before the
# value counts: the 252-day high/low need 252 rows, the "previous" ones need one more row.
STRICT_52_WEEK_COLUMNS = {
    "high_52w": 252,
    "low_52w": 252,
    "high_252d_prev": 253,
    "pct_from_52w_high": 253,
}

# CL-24 (LOG-025): the volume multiple compares today's volume with the 20-day average as of
# yesterday. These columns changed on purpose; the test below checks them against the rule.
VOLUME_RULE_COLUMNS = {"volume_ratio", "vol_multiple", "rsi_momentum"}


def _rows_of_history(table: pd.DataFrame) -> pd.Series:
    """For each row: how many rows of its own stock exist up to and including that date."""
    ordered = table.sort_values(["symbol", "date"], kind="stable")
    return (ordered.groupby("symbol").cumcount() + 1).reindex(table.index)


def test_add_all_indicators_matches_frozen_output_except_the_strict_52_week_window() -> None:
    """Other columns equal (floats to 10 digits); the 52-week ones once there is full history."""
    table, bench, frozen = _load()
    result = add_all_indicators(table, benchmark_df=bench)

    changed_on_purpose = STRICT_52_WEEK_COLUMNS.keys() | VOLUME_RULE_COLUMNS
    unchanged = [name for name in frozen.columns if name not in changed_on_purpose]
    _assert_same(result[unchanged], frozen[unchanged])

    history = _rows_of_history(table)
    for name, needed in STRICT_52_WEEK_COLUMNS.items():
        enough = history >= needed
        _assert_same(result.loc[enough, [name]], frozen.loc[enough, [name]])
        assert result.loc[~enough, name].isna().all()


def test_volume_multiple_and_rsi_momentum_use_yesterdays_20_day_average() -> None:
    """The volume rule of LOG-025, written out from the frozen columns."""
    table, bench, frozen = _load()
    result = add_all_indicators(table, benchmark_df=bench)

    average = frozen["volume_sma_20"]  # unchanged column: today's 20-day average
    ordered = table.sort_values(["symbol", "date"], kind="stable")  # the input is unsorted
    yesterday = average.loc[ordered.index].groupby(ordered["symbol"]).shift(1).reindex(table.index)
    expected_multiple = table["volume"] / yesterday.replace(0, np.nan)
    _assert_same(result[["vol_multiple"]], expected_multiple.to_frame("vol_multiple"))
    _assert_same(result[["volume_ratio"]], expected_multiple.to_frame("volume_ratio"))

    close = table["close"]
    rsi_9 = frozen["rsi_9"]
    expected_momentum = (
        (rsi_9 >= frozen["rsi_9_wma_21"] + 1.1)
        & (rsi_9 >= 45.0)
        & (rsi_9 <= 65.0)
        & (frozen["ema_20"] > frozen["sma_50"])
        & (frozen["sma_50"] > frozen["sma_200"])
        & (close > frozen["sma_50"])
        & (close > frozen["sma_200"])
        & (close >= 0.85 * result["high_252d_prev"])
        & (table["volume"] > 1.5 * yesterday)
        & (average > 250_000)
    ).fillna(False)
    assert result["rsi_momentum"].fillna(False).astype(bool).equals(expected_momentum)


def test_stocks_with_under_252_rows_lose_their_52_week_values() -> None:
    """The documented difference, on the two short stocks of the fixture."""
    table, bench, frozen = _load()
    result = add_all_indicators(table, benchmark_df=bench)
    short = result[result["symbol"] == "BBB"]  # 60 rows
    assert short[["high_52w", "low_52w", "high_252d_prev", "pct_from_52w_high"]].isna().all().all()
    old_short = frozen[frozen["symbol"] == "BBB"]
    assert old_short["high_52w"].notna().sum() == 41  # the old 20-row rule gave 41 values


def test_one_row_stock_gets_empty_indicator_columns() -> None:
    """A day-one listing keeps its price columns and gets NaN/None everywhere else."""
    table, bench, _ = _load()
    result = add_all_indicators(table, benchmark_df=bench)
    row = result[result["symbol"] == "CCC"].iloc[0]
    price_columns = {"symbol", "date", "open", "high", "low", "close", "volume"}
    filled = {name for name, value in row.items() if pd.notna(value)}
    assert filled == price_columns


def test_result_keeps_the_input_row_order() -> None:
    """The input is not sorted; the output index and order must be the input's."""
    table, bench, _ = _load()
    result = add_all_indicators(table, benchmark_df=bench)
    assert list(result.index) == list(table.index)
    assert list(result["symbol"]) == list(table["symbol"])
    assert list(result["date"]) == list(table["date"])
