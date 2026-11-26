"""CL-23 / CL-25: the cached indicator values still equal the frozen output of the OLD code.

The fixtures were made once by running the old ``TechnicalCalculator.apply_all_indicators`` on a
small seeded table (3 stocks, shuffled rows):

* AAA: 400 trading days, with one zero-volume day;
* BBB: 60 days (shorter than the 200- and 252-day windows);
* CCC: 1 day (a day-one listing; the old code left every indicator column empty for it).

CL-25 trimmed the cache to the approved list and renamed the columns to the resolver's names
(``sma_50`` -> ``sma_close_50``). Every approved column that existed before must still be equal,
value for value. The 252-day high and low are now the PRIOR-day window, and (CL-23B, finding B9)
they need a full 252 rows of history, so they are compared only where the old column had that.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.db.data_service import compute_market_indicators

FIXTURES = Path(__file__).parent / "fixtures"

# old cache column -> the resolver's name for the same indicator
RENAMED = {
    "sma_20": "sma_close_20",
    "sma_50": "sma_close_50",
    "sma_200": "sma_close_200",
    "ema_20": "ema_close_20",
    "bb_upper": "bollinger_bands_close_20_2_upper",
    "bb_lower": "bollinger_bands_close_20_2_lower",
    "volume_sma_20": "sma_volume_20",
    "rsi_14": "rsi_close_14",
}

# The fixture was made on a Mac; Linux rounds the last digit of some ewm values differently
# (1e-16 relative). That is platform noise, not a logic change, so floats are compared to 10
# significant digits; NaN positions stay exact.
FLOAT_TOLERANCE = {"check_exact": False, "rtol": 1e-10, "atol": 1e-12}


def _by_stock_and_day(table: pd.DataFrame) -> pd.DataFrame:
    """The input is shuffled, so rows are matched by (symbol, date), never by position."""
    return table.set_index(["symbol", "date"]).sort_index()


def _built_and_frozen() -> tuple[pd.DataFrame, pd.DataFrame]:
    table = pd.read_parquet(FIXTURES / "indicators_before_input.parquet")
    frozen = pd.read_parquet(FIXTURES / "indicators_before_output.parquet")
    return (
        _by_stock_and_day(compute_market_indicators(table)),
        _by_stock_and_day(frozen),
    )


def test_every_approved_column_that_existed_before_has_the_same_values() -> None:
    built, frozen = _built_and_frozen()

    for old_name, new_name in RENAMED.items():
        pd.testing.assert_series_equal(
            built[new_name], frozen[old_name], check_names=False, **FLOAT_TOLERANCE
        )


def test_prior_day_252_day_high_and_low_equal_the_old_column_once_history_is_full() -> None:
    """The old ``high_252d_prev`` is the same prior-day window; it needs 253 rows of history."""
    built, frozen = _built_and_frozen()
    history = built.groupby(level="symbol").cumcount() + 1
    full = history >= 253

    pd.testing.assert_series_equal(
        built.loc[full, "window_high_252_max_ago1"],
        frozen.loc[full, "high_252d_prev"],
        check_names=False,
        **FLOAT_TOLERANCE,
    )
    assert built.loc[~full, "window_high_252_max_ago1"].isna().all()
    assert built.loc[~full, "window_low_252_min_ago1"].isna().all()


def test_one_row_stock_gets_empty_indicator_columns() -> None:
    """A day-one listing keeps its price columns and gets NaN in every indicator column."""
    built, _ = _built_and_frozen()
    row = built.loc["CCC"].iloc[0]

    indicator_columns = [name for name in built.columns if name in set(RENAMED.values())]
    assert row[indicator_columns].isna().all()
    assert pd.notna(row["close"])
