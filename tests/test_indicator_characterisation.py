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


# Columns that depend on the 52-week window, and the history (rows) a stock needs before the
# value counts: the 252-day high/low need 252 rows, the "previous" ones need one more row.
STRICT_52_WEEK_COLUMNS = {
    "high_52w": 252,
    "low_52w": 252,
    "high_252d_prev": 253,
    "pct_from_52w_high": 253,
    "rsi_momentum": 253,
}


def _rows_of_history(table: pd.DataFrame) -> pd.Series:
    """For each row: how many rows of its own stock exist up to and including that date."""
    ordered = table.sort_values(["symbol", "date"], kind="stable")
    return (ordered.groupby("symbol").cumcount() + 1).reindex(table.index)


def test_add_all_indicators_matches_frozen_output_except_the_strict_52_week_window() -> None:
    """Every other column exactly equal; the 52-week columns equal once there is full history."""
    table, bench, frozen = _load()
    result = add_all_indicators(table, benchmark_df=bench)

    unchanged = [name for name in frozen.columns if name not in STRICT_52_WEEK_COLUMNS]
    pd.testing.assert_frame_equal(
        result[unchanged], frozen[unchanged], check_exact=True, check_like=True
    )

    history = _rows_of_history(table)
    for name, needed in STRICT_52_WEEK_COLUMNS.items():
        enough = history >= needed
        pd.testing.assert_series_equal(
            result.loc[enough, name], frozen.loc[enough, name], check_exact=True
        )
        too_short = result.loc[~enough, name]
        if name == "rsi_momentum":  # a boolean rule: False (or empty for a 1-row stock)
            assert too_short.fillna(False).eq(False).all()
        else:
            assert too_short.isna().all()


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
