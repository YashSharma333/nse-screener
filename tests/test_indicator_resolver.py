"""The indicator resolver (CL-23D): custom numbers go through the one indicator function."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from src.computation.indicators.calculator import atr, bollinger_bands, macd, rsi, sma
from src.computation.indicators.price_helpers import window_stat
from src.computation.indicators.resolver import (
    REGISTRY,
    compute_indicator,
    describe_indicators,
    ensure_columns,
    ind,
)


def _table(rows: int = 120) -> pd.DataFrame:
    """Two stocks, sorted by symbol then date, with random but seeded prices."""
    rng = np.random.default_rng(7)
    dates = list(pd.bdate_range("2026-01-01", periods=rows).date)
    close = rng.uniform(50, 150, rows * 2)
    return pd.DataFrame(
        {
            "symbol": ["A"] * rows + ["B"] * rows,
            "date": dates * 2,
            "open": close * 0.99,
            "high": close * 1.02,
            "low": close * 0.97,
            "close": close,
            "volume": rng.integers(1_000, 9_000, rows * 2).astype(float),
        }
    )


def test_default_indicator_equals_the_function_default() -> None:
    df = _table()
    pd.testing.assert_series_equal(compute_indicator(df, ind("sma")), sma(df))


def test_custom_length_equals_a_hand_computed_mean() -> None:
    df = _table()
    expected = df.groupby("symbol")["close"].transform(lambda s: s.rolling(50).mean())
    got = compute_indicator(df, ind("sma", length=50))
    pd.testing.assert_series_equal(got, expected, check_names=False)


def test_days_ago_gives_yesterdays_volume_average_inside_each_stock() -> None:
    df = _table()
    got = compute_indicator(df, ind("sma", source="volume", length=20, days_ago=1))
    expected = df.groupby("symbol")["volume"].transform(lambda s: s.rolling(20).mean().shift(1))
    pd.testing.assert_series_equal(got, expected, check_names=False)
    assert pd.isna(got.iloc[0]) and pd.isna(got.iloc[120])  # first row of each stock


def test_days_ago_on_the_output_equals_the_window_ending_earlier() -> None:
    df = _table()
    got = compute_indicator(df, ind("window", source="high", length=20, stat="max", days_ago=3))
    pd.testing.assert_series_equal(got, window_stat(df, "high", 20, "max", days_ago=3))


def test_column_names_are_stable_readable_and_unique() -> None:
    assert ind("sma").column == "sma_close_20"
    assert ind("sma", length=20).column == "sma_close_20"  # same spec, same name
    assert ind("sma", length=50).column == "sma_close_50"
    assert ind("sma", source="volume", length=20, days_ago=1).column == "sma_volume_20_ago1"
    assert ind("window", source="high", length=20, stat="max").column == "window_high_20_max"
    specs = [
        ind("sma", length=10),
        ind("sma", length=10, source="volume"),
        ind("ema", length=10),
        ind("sma", length=10, days_ago=1),
        ind("macd", output="line"),
        ind("macd", output="signal"),
    ]
    assert len({s.column for s in specs}) == len(specs)


def test_specs_are_frozen_hashable_and_equal_when_the_numbers_are() -> None:
    assert ind("sma") == ind("sma", length=20)
    assert len({ind("sma"), ind("sma", length=20)}) == 1
    with pytest.raises(AttributeError):
        ind("sma").days_ago = 2  # type: ignore[misc]


def test_ensure_columns_adds_four_columns_once() -> None:
    df = _table()
    specs = [ind("sma", length=n) for n in (10, 21, 50, 100)]
    before = list(df.columns)
    ensure_columns(df, specs)
    assert list(df.columns) == before + [s.column for s in specs]
    ensure_columns(df, specs)
    assert len(df.columns) == len(before) + 4
    pd.testing.assert_series_equal(df["sma_close_50"], sma(df, length=50), check_names=False)


def test_ensure_columns_does_not_recompute_a_column_that_exists() -> None:
    df = _table()
    df["sma_close_20"] = -1.0
    ensure_columns(df, [ind("sma")])
    assert (df["sma_close_20"] == -1.0).all()


def test_every_registered_indicator_takes_custom_numbers() -> None:
    df = _table()
    cases = {
        "sma": {"length": 5},
        "ema": {"length": 5},
        "wma": {"length": 5},
        "rsi": {"length": 7},
        "volatility": {"length": 5},
        "percent_change": {"length": 3},
        "true_range": {},
        "atr": {"length": 7, "output": "percent"},
        "bollinger_bands": {"length": 10, "std": 1.5, "output": "lower"},
        "macd": {"fast": 5, "slow": 10, "signal": 3, "output": "histogram"},
        "window": {"source": "low", "length": 5, "stat": "min"},
        "value": {"source": "open"},
    }
    assert set(cases) == set(REGISTRY)
    for name, custom in cases.items():
        result = compute_indicator(df, ind(name, **custom))
        assert len(result) == len(df)
        assert result.notna().any(), name
    pd.testing.assert_series_equal(
        compute_indicator(df, ind("bollinger_bands", length=10, std=1.5, output="lower")),
        bollinger_bands(df, length=10, std=1.5, output="lower"),
    )
    pd.testing.assert_series_equal(
        compute_indicator(df, ind("atr", output="percent")), atr(df, output="percent")
    )
    pd.testing.assert_series_equal(
        compute_indicator(df, ind("macd", output="signal")), macd(df, output="signal")
    )


def test_indicator_of_an_indicator_computes_the_inner_column_first() -> None:
    df = _table()
    inner = ind("rsi", length=14)
    outer = ind("sma", source=inner, length=5)
    assert outer.column == "sma_rsi_close_14_5"
    df_with_rsi = df.assign(rsi_close_14=rsi(df, length=14))
    expected = sma(df_with_rsi, length=5, source="rsi_close_14")
    got = compute_indicator(df, outer)
    pd.testing.assert_series_equal(got, expected)
    assert "rsi_close_14" not in df.columns  # the caller's table is not changed


def test_unknown_indicator_name_lists_the_allowed_names() -> None:
    with pytest.raises(ValueError, match=r"Unknown indicator 'smaa'.*sma"):
        ind("smaa")


def test_unknown_parameter_lists_the_allowed_parameters() -> None:
    with pytest.raises(ValueError, match=r"'sma' has no parameter 'period'.*length.*source"):
        ind("sma", period=5)


def test_window_days_ago_is_the_resolvers_not_the_functions() -> None:
    assert ind("window", days_ago=2).days_ago == 2
    assert "days_ago" not in dict(ind("window").params)


@pytest.mark.parametrize("bad", [-1, 1.5, True])
def test_days_ago_must_be_a_whole_number_of_zero_or_more(bad: object) -> None:
    with pytest.raises(ValueError, match="days_ago"):
        ind("sma", days_ago=bad)  # type: ignore[arg-type]


def test_a_bad_number_is_rejected_by_the_indicator_function_itself() -> None:
    with pytest.raises(ValueError, match="length"):
        compute_indicator(_table(), ind("sma", length=0))


def test_describe_indicators_is_json_with_defaults_from_the_signatures() -> None:
    described = {item["name"]: item for item in json.loads(json.dumps(describe_indicators()))}
    assert set(described) == set(REGISTRY)
    sma_params = {p["name"]: p["default"] for p in described["sma"]["parameters"]}
    assert sma_params == {"length": 20, "source": "close"}
    assert described["macd"]["outputs"] == ["line", "signal", "histogram"]
    assert described["atr"]["outputs"] == ["value", "percent"]
    assert described["sma"]["outputs"] == []
    window_names = [p["name"] for p in described["window"]["parameters"]]
    assert "days_ago" not in window_names
    stat = next(p for p in described["window"]["parameters"] if p["name"] == "stat")
    assert "count" in stat["choices"]


def test_chartink_example_volume_at_least_1_5_times_yesterdays_20_day_average() -> None:
    """Today's volume >= 1.5 x the 20-day average volume as of yesterday."""
    df = _table(60)
    df.loc[df.index[40], "volume"] = 1_000_000.0  # a spike on one day of stock A
    average_yesterday = ind("sma", source="volume", length=20, days_ago=1)
    ensure_columns(df, [average_yesterday])
    passes = df["volume"] >= 1.5 * df[average_yesterday.column]

    # by hand: yesterday's 20-day mean of this stock's volume
    by_hand = df.groupby("symbol")["volume"].transform(lambda s: s.rolling(20).mean().shift(1))
    assert passes.equals(df["volume"] >= 1.5 * by_hand)
    assert passes.iloc[40]
    assert not passes.iloc[:20].any()  # no 20-day history yet, so NaN compares false


def test_value_with_days_ago_is_the_column_n_days_ago_inside_each_stock() -> None:
    df = _table()
    spec = ind("value", source="close", days_ago=5)
    assert spec.column == "value_close_ago5"
    got = compute_indicator(df, spec)
    expected = df.groupby("symbol")["close"].shift(5)
    pd.testing.assert_series_equal(got, expected, check_names=False)
