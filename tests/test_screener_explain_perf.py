"""Benchmark and correctness tests for vectorized Screener.explain() (G5)."""

from __future__ import annotations

import time

import numpy as np
import pandas as pd

from src.screeners.base import Screener


class SyntheticTenConditionScreener(Screener):
    """Screener with 10 distinct conditions across categories for stress testing."""

    key = "synthetic_10_cond"
    name = "Synthetic 10 Condition Screener"
    category = "test"

    def price_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        return {
            "c1": (df["v1"] > 50).fillna(False),
            "c2": (df["v2"] > 50).fillna(False),
            "c3": (df["v3"] > 50).fillna(False),
        }

    def volume_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        return {
            "c4": (df["v4"] > 50).fillna(False),
            "c5": (df["v5"] > 50).fillna(False),
        }

    def crossover_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        return {
            "c6": (df["v6"] > 50).fillna(False),
            "c7": (df["v7"] > 50).fillna(False),
        }

    def momentum_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        return {
            "c8": (df["v8"] > 50).fillna(False),
            "c9": (df["v9"] > 50).fillna(False),
            "c10": (df["v10"] > 50).fillna(False),
        }


def _legacy_explain_reference(
    screener: Screener, df: pd.DataFrame
) -> tuple[list[list[str]], list[str]]:
    """Old row-wise implementation for equivalence testing."""
    grouped = screener.conditions_by_nature(df)
    cond_names: list[str] = []
    cond_series_list: list[pd.Series] = []
    for _nature, conds in grouped.items():
        for name, series in conds.items():
            cond_names.append(name)
            cond_series_list.append(series.fillna(False).astype(bool))

    cond_matrix = pd.concat(cond_series_list, axis=1)
    cond_matrix.columns = cond_names

    failed_lists: list[list[str]] = []
    near_miss_on_list: list[str] = []
    for _, row in cond_matrix.iterrows():
        failed = [name for name, val in row.items() if not val]
        failed_lists.append(failed)
        near_miss_on_list.append(failed[0] if len(failed) == 1 else "")

    return failed_lists, near_miss_on_list


def test_vectorized_explain_equivalence_on_sample():
    """Verify that vectorized explain matches legacy row-wise implementation results."""
    rng = np.random.default_rng(123)
    n = 1000
    data = {f"v{i}": rng.integers(0, 100, size=n) for i in range(1, 11)}
    data["symbol"] = [f"SYM_{i}" for i in range(n)]
    df = pd.DataFrame(data)

    screener = SyntheticTenConditionScreener()
    explained = screener.explain(df)

    expected_failed, expected_near_miss_on = _legacy_explain_reference(screener, df)

    assert explained["failed_conditions"].tolist() == expected_failed
    assert explained["near_miss_on"].tolist() == expected_near_miss_on


def test_vectorized_explain_one_million_rows_under_ten_seconds():
    """G5 requirement: 1,000,000 synthetic rows with 10 conditions in under 10 seconds."""
    rng = np.random.default_rng(42)
    n = 1_000_000
    # Generate 10 uniform random float columns
    data = {f"v{i}": rng.integers(0, 100, size=n) for i in range(1, 11)}
    df = pd.DataFrame(data)

    screener = SyntheticTenConditionScreener()

    start_time = time.perf_counter()
    explained = screener.explain(df)
    elapsed = time.perf_counter() - start_time

    assert len(explained) == n
    assert "conditions_passed" in explained.columns
    assert "failed_conditions" in explained.columns
    assert "near_miss_on" in explained.columns
    assert elapsed < 10.0, f"explain() took {elapsed:.2f}s for 1M rows, exceeding 10s threshold!"
