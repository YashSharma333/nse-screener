"""
Contract test suite for systematic Screeners (G6).
Enforces structural, computational, AST, and point-in-time invariants across all screeners.
"""

from __future__ import annotations

import ast
import functools
import operator
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.computation.indicators.calculator import TechnicalCalculator
from src.screeners.base import BaseScreener
from src.screeners.registry import list_screeners

ALL_SCREENERS = list_screeners()


@pytest.fixture(scope="module")
def synthetic_full_and_truncated_data():
    """Generate 300 trading days for 5 stocks and compute indicators."""
    np.random.seed(42)
    symbols = ["INFY", "TCS", "HDFCBANK", "RELIANCE", "ITC"]
    dates = pd.date_range("2024-01-01", periods=300, freq="B").strftime("%Y-%m-%d").tolist()

    records = []
    for sym in symbols:
        price = 1000.0
        for d in dates:
            ret = float(np.random.normal(0.0005, 0.015))
            price *= 1.0 + ret
            high = price * (1.0 + abs(float(np.random.normal(0, 0.005))))
            low = price * (1.0 - abs(float(np.random.normal(0, 0.005))))
            op = (high + low) / 2.0
            vol = float(np.random.randint(100_000, 2_000_000))
            records.append(
                {
                    "symbol": sym,
                    "date": d,
                    "open": op,
                    "high": high,
                    "low": low,
                    "close": price,
                    "volume": vol,
                    "prev_close": price / (1.0 + ret),
                }
            )
    raw_df = pd.DataFrame(records)

    benchmark_records = []
    b_price = 20000.0
    for d in dates:
        b_ret = float(np.random.normal(0.0003, 0.008))
        b_price *= 1.0 + b_ret
        benchmark_records.append({"date": d, "close": b_price})
    bench_df = pd.DataFrame(benchmark_records)

    calc = TechnicalCalculator()
    full_indicators = calc.apply_all_indicators(raw_df, benchmark_df=bench_df)

    t_mid = dates[220]
    raw_truncated = raw_df[raw_df["date"] <= t_mid].copy()
    bench_truncated = bench_df[bench_df["date"] <= t_mid].copy()
    trunc_indicators = calc.apply_all_indicators(raw_truncated, benchmark_df=bench_truncated)

    return {
        "dates": dates,
        "t_mid": t_mid,
        "full_indicators": full_indicators,
        "trunc_indicators": trunc_indicators,
        "calc_columns": set(full_indicators.columns),
    }


def test_registry_uniqueness():
    """All registered screeners must have unique keys."""
    keys = [s.key for s in ALL_SCREENERS]
    assert len(keys) == len(set(keys)), f"Duplicate screener keys found: {keys}"


@pytest.mark.parametrize("screener_cls", ALL_SCREENERS, ids=lambda s: s.key)
def test_screener_metadata_and_conditions_contract(screener_cls: type[BaseScreener]):
    """1. Unique key, non-empty metadata, status in {'core', 'experimental'}.
    2. Screener must define valid params, evidence_columns, required_columns.
    """
    screener = screener_cls()
    assert screener.key and isinstance(screener.key, str)
    assert screener.name and isinstance(screener.name, str)
    assert screener.description and isinstance(screener.description, str)
    assert screener.category and isinstance(screener.category, str)
    assert screener.version and isinstance(screener.version, str)
    assert screener.status in {"core", "experimental"}
    assert isinstance(screener.evidence_columns, (list, tuple))
    assert isinstance(screener.required_columns, (list, tuple))


@pytest.mark.parametrize("screener_cls", ALL_SCREENERS, ids=lambda s: s.key)
def test_screener_column_alignment_with_calculator(
    screener_cls: type[BaseScreener], synthetic_full_and_truncated_data
):
    """6 & 7. evidence_columns and required_columns must exist in calculator output."""
    screener = screener_cls()
    calc_cols = synthetic_full_and_truncated_data["calc_columns"]

    missing_req = set(screener.required_columns) - calc_cols
    assert not missing_req, (
        f"Screener {screener.key} required_columns missing in calculator: {missing_req}"
    )

    missing_ev = set(screener.evidence_columns) - calc_cols
    assert not missing_ev, (
        f"Screener {screener.key} evidence_columns missing in calculator: {missing_ev}"
    )


@pytest.mark.parametrize("screener_cls", ALL_SCREENERS, ids=lambda s: s.key)
def test_screener_condition_evaluation_and_explain(
    screener_cls: type[BaseScreener], synthetic_full_and_truncated_data
):
    """3. Conditions return boolean Series aligned with input df index; NaN inputs return False.
    4. run(df) returns filtered DataFrame matching logical AND across all conditions.
    5. explain(df) contract: original cols + summary metrics + individual condition cols.
    """
    screener = screener_cls()
    df = synthetic_full_and_truncated_data["full_indicators"].copy()

    # 3. Test conditions return aligned bool Series
    cond_dict = screener.all_conditions(df)
    assert len(cond_dict) > 0, f"Screener {screener.key} defined no conditions"

    cond_results = []
    for name, series in cond_dict.items():
        assert isinstance(series, pd.Series)
        assert len(series) == len(df)
        assert series.index.equals(df.index)
        assert series.dtype == bool
        assert not series.isna().any(), f"Condition {name} produced NaN values"
        cond_results.append(series)

    # 4. run(df) matches logical AND of all conditions
    expected_mask = functools.reduce(operator.and_, cond_results)
    run_df = screener.run(df)
    assert isinstance(run_df, pd.DataFrame)
    assert run_df.index.equals(df[expected_mask].index)

    # 5. explain(df) contract
    explained = screener.explain(df)
    assert len(explained) == len(df)
    assert explained.index.equals(df.index)

    mandatory_meta_cols = [
        "conditions_passed",
        "conditions_total",
        "passed",
        "near_miss",
        "near_miss_on",
        "failed_conditions",
    ]
    for col in mandatory_meta_cols:
        assert col in explained.columns, f"Missing {col} in explain() output"

    for name in cond_dict:
        cond_col = f"cond_{name}"
        assert cond_col in explained.columns, f"Missing condition column {cond_col} in explain()"

    # Verify vectorised failed_conditions output consistency
    assert isinstance(explained["failed_conditions"].iloc[0], list)
    pd.testing.assert_series_equal(explained["passed"], expected_mask, check_names=False)


@pytest.mark.parametrize("screener_cls", ALL_SCREENERS, ids=lambda s: s.key)
def test_screener_point_in_time_look_ahead(
    screener_cls: type[BaseScreener], synthetic_full_and_truncated_data
):
    """9. Point-in-time invariant:
    Truncating data after t_mid must not change screener decisions or condition results at t_mid.
    """
    screener = screener_cls()
    t_mid = synthetic_full_and_truncated_data["t_mid"]

    full_df = synthetic_full_and_truncated_data["full_indicators"]
    trunc_df = synthetic_full_and_truncated_data["trunc_indicators"]

    full_t = full_df[full_df["date"] == t_mid].sort_values("symbol").reset_index(drop=True)
    trunc_t = trunc_df[trunc_df["date"] == t_mid].sort_values("symbol").reset_index(drop=True)

    # Evaluate screener explain on both
    full_explained = screener.explain(full_t)
    trunc_explained = screener.explain(trunc_t)

    # Compare boolean pass decisions at t_mid
    pd.testing.assert_series_equal(
        full_explained["passed"],
        trunc_explained["passed"],
        check_names=False,
    )
    pd.testing.assert_series_equal(
        full_explained["conditions_passed"],
        trunc_explained["conditions_passed"],
        check_names=False,
    )
    pd.testing.assert_series_equal(
        full_explained["failed_conditions"],
        trunc_explained["failed_conditions"],
        check_names=False,
    )


def test_screener_modules_ast_no_forbidden_look_ahead_methods():
    """8. AST static check: no screener or condition module uses shift/rolling/pct_change."""
    screener_dir = Path("src/screeners")
    py_files = list(screener_dir.glob("*.py")) + list((screener_dir / "conditions").glob("*.py"))
    forbidden = {"shift", "rolling", "pct_change"}

    violations = []
    for filepath in py_files:
        tree = ast.parse(filepath.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in forbidden:
                violations.append((filepath.name, node.attr, node.lineno))

    assert not violations, (
        f"Forbidden methods used in screener code (must use precomputed indicators): {violations}"
    )
