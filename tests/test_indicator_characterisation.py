"""CL-23: today's indicator output, frozen, so the rewrite is proven to change no value.

The fixtures were made once by running the old ``TechnicalCalculator.apply_all_indicators`` on a
small seeded table (3 stocks, shuffled rows):

* AAA: 400 trading days, with one zero-volume day;
* BBB: 60 days (shorter than the 200- and 252-day windows);
* CCC: 1 day (a day-one listing; the old code left every indicator column empty for it).

``add_all_indicators`` is temporary (deleted in CL-25) and must reproduce this exactly, bit for
bit, including the partial-window quirks CL-26 and CL-23B fix later.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.computation.indicators.calculator import add_all_indicators

FIXTURES = Path(__file__).parent / "fixtures"

# Deleted on purpose in CL-23: nothing reads the Donchian channels.
DELETED_COLUMNS = ["donchian_high_20", "donchian_low_20", "donchian_high_55", "donchian_low_55"]


def _load() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    table = pd.read_parquet(FIXTURES / "indicators_before_input.parquet")
    bench = pd.read_parquet(FIXTURES / "indicators_before_benchmark.parquet")
    frozen = pd.read_parquet(FIXTURES / "indicators_before_output.parquet")
    return table, bench, frozen.drop(columns=DELETED_COLUMNS)


def test_add_all_indicators_matches_frozen_output() -> None:
    """Every column, every row, exactly equal (the input rows are shuffled on purpose)."""
    table, bench, frozen = _load()
    result = add_all_indicators(table, benchmark_df=bench)
    pd.testing.assert_frame_equal(result, frozen, check_exact=True, check_like=True)


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
