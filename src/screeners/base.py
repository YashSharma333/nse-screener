"""Screener Base Class Architecture
================================
Provides the abstract base class `Screener` ensuring all systematic screens:
1. Define standardized metadata (key, name, description, category, version, status).
2. Group their screening rules strictly by nature:
   - Price conditions
   - Volume conditions
   - Crossover conditions
   - Momentum conditions
3. Support execution (`run`) and deep diagnostic introspection (`explain`),
   including near-miss analysis (stocks that failed by only 1 condition).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, fields, is_dataclass
from typing import Any, Literal

import numpy as np
import pandas as pd


class MissingIndicatorError(Exception):
    """Raised when required precomputed indicator columns are missing from the input."""

    def __init__(self, missing_columns: Sequence[str] | set[str] | list[str]):
        self.missing_columns = sorted(missing_columns)
        super().__init__(f"Missing required indicator columns: {', '.join(self.missing_columns)}")

    def __str__(self) -> str:
        return f"Missing required indicator columns: {', '.join(self.missing_columns)}"


@dataclass(frozen=True)
class ScreenerMetadata:
    """Metadata describing a registered screener."""

    key: str
    name: str
    description: str
    category: str
    version: str = "1.0"
    status: Literal["core", "experimental"] = "experimental"
    evidence_columns: tuple[str, ...] = ()
    required_columns: tuple[str, ...] = ()


class Screener:
    """Base class for quantitative stock screens."""

    key: str = ""
    name: str = ""
    description: str = ""
    category: str = "general"
    version: str = "1.0"
    status: Literal["core", "experimental"] = "experimental"
    evidence_columns: tuple[str, ...] = ()
    required_columns: tuple[str, ...] = ()
    params_cls: type | None = None

    def __init__(self, **params: Any) -> None:
        if self.params_cls is not None and is_dataclass(self.params_cls):
            valid_fields = {f.name for f in fields(self.params_cls)}
            unknown = set(params.keys()) - valid_fields
            if unknown:
                unknown_str = ", ".join(sorted(unknown))
                valid_str = ", ".join(sorted(valid_fields))
                raise TypeError(
                    f"Unknown parameter(s) for {self.__class__.__name__}: {unknown_str}. "
                    f"Valid parameters: {valid_str}"
                )
            self.params: Any = self.params_cls(**params)
        else:
            self.params = params

    @classmethod
    def get_metadata(cls) -> ScreenerMetadata:
        """Return standardized metadata for this screener."""
        return ScreenerMetadata(
            key=cls.key,
            name=cls.name,
            description=cls.description,
            category=cls.category,
            version=str(cls.version),
            status=cls.status,
            evidence_columns=cls.evidence_columns,
            required_columns=cls.required_columns,
        )

    # ------------------------------------------------------------------
    # Screening condition groups organized by nature
    # ------------------------------------------------------------------
    def universe_conditions(self, _df: pd.DataFrame) -> dict[str, pd.Series]:
        """Universe and index membership conditions."""
        return {}

    def price_conditions(self, _df: pd.DataFrame) -> dict[str, pd.Series]:
        """Price-based conditions (moving averages, bounds, high/low extremes)."""
        return {}

    def volume_conditions(self, _df: pd.DataFrame) -> dict[str, pd.Series]:
        """Volume-based conditions (volume surges, liquidity floors, dryup)."""
        return {}

    def crossover_conditions(self, _df: pd.DataFrame) -> dict[str, pd.Series]:
        """Technical crossover conditions (Golden Cross, MACD crossovers)."""
        return {}

    def momentum_conditions(self, _df: pd.DataFrame) -> dict[str, pd.Series]:
        """Momentum and oscillator conditions (RSI, RS vs benchmark, rank)."""
        return {}

    # ------------------------------------------------------------------
    # Condition aggregation and reflection
    # ------------------------------------------------------------------
    def conditions_by_nature(self, df: pd.DataFrame) -> dict[str, dict[str, pd.Series]]:
        """Return all conditions organized strictly by their nature."""
        self._validate_required_columns(df)
        return {
            "universe": self.universe_conditions(df),
            "price": self.price_conditions(df),
            "volume": self.volume_conditions(df),
            "crossover": self.crossover_conditions(df),
            "momentum": self.momentum_conditions(df),
        }

    def _validate_required_columns(self, df: pd.DataFrame) -> None:
        """Verify that all required columns are present in input DataFrame."""
        if not self.required_columns or df.empty:
            return
        missing = [col for col in self.required_columns if col not in df.columns]
        if missing:
            raise MissingIndicatorError(missing)

    def all_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        """Return a flattened mapping of {condition_name: boolean_series}."""
        grouped = self.conditions_by_nature(df)
        flattened: dict[str, pd.Series] = {}
        for _nature, conds in grouped.items():
            for name, series in conds.items():
                if isinstance(series, pd.Series):
                    flattened[name] = series.fillna(False).astype(bool)
                else:
                    flattened[name] = pd.Series(bool(series), index=df.index, dtype=bool)
        return flattened

    # ------------------------------------------------------------------
    # Execution & Diagnosis
    # ------------------------------------------------------------------
    def run(self, df: pd.DataFrame) -> pd.DataFrame:
        """Run screener and return rows that pass all screening conditions."""
        if df.empty:
            return df.copy()

        conditions = self.all_conditions(df)
        if not conditions:
            return df.copy()

        mask = pd.Series(True, index=df.index)
        for _name, cond_series in conditions.items():
            mask &= cond_series

        return df[mask].copy()

    def explain(self, df: pd.DataFrame) -> pd.DataFrame:
        """Evaluate conditions and produce detailed diagnostic columns without iterrows.

        Added diagnostic columns:
        - `cond_{name}`: boolean column per condition
        - `pass_{nature}`: boolean column indicating if all conditions in that nature group passed
        - `conditions_passed`: count of passed conditions for each row
        - `conditions_total`: total number of evaluated conditions
        - `passed`: boolean indicating if all conditions passed
        - `failed_conditions`: list of failed condition names
        - `near_miss`: boolean, True if exactly 1 condition failed
        - `near_miss_on`: string, name of the single failed condition (or empty string)
        """
        if df.empty:
            res = df.copy()
            res["conditions_passed"] = pd.Series(dtype=int)
            res["conditions_total"] = pd.Series(dtype=int)
            res["passed"] = pd.Series(dtype=bool)
            res["failed_conditions"] = pd.Series(dtype=object)
            res["near_miss"] = pd.Series(dtype=bool)
            res["near_miss_on"] = pd.Series(dtype=str)
            return res

        res = df.copy()
        grouped = self.conditions_by_nature(df)

        cond_names: list[str] = []
        cond_series_list: list[pd.Series] = []

        for nature, conds in grouped.items():
            nature_mask = pd.Series(True, index=df.index)
            has_nature_conds = False
            for name, series in conds.items():
                if isinstance(series, pd.Series):
                    clean_series = series.fillna(False).astype(bool)
                else:
                    clean_series = pd.Series(bool(series), index=df.index, dtype=bool)
                res[f"cond_{name}"] = clean_series
                cond_names.append(name)
                cond_series_list.append(clean_series)
                nature_mask &= clean_series
                has_nature_conds = True
            if has_nature_conds:
                res[f"pass_{nature}"] = nature_mask

        if not cond_series_list:
            res["conditions_passed"] = 0
            res["conditions_total"] = 0
            res["passed"] = True
            res["failed_conditions"] = [[] for _ in range(len(res))]
            res["near_miss"] = False
            res["near_miss_on"] = ""
            return res

        cond_matrix = pd.concat(cond_series_list, axis=1)
        cond_matrix.columns = cond_names

        # Vectorized diagnostics using NumPy array operations (G5)
        cond_values = cond_matrix.to_numpy(dtype=bool)  # Shape (N, C)
        failed_matrix = ~cond_values
        passed_count = cond_values.sum(axis=1)
        total_count = len(cond_names)

        res["conditions_passed"] = passed_count
        res["conditions_total"] = total_count
        res["passed"] = passed_count == total_count
        near_miss_mask = passed_count == (total_count - 1)
        res["near_miss"] = near_miss_mask

        # Vectorized near_miss_on
        cond_names_arr = np.array(cond_names, dtype=object)
        single_fail_idx = np.argmin(cond_values, axis=1)
        res["near_miss_on"] = np.where(near_miss_mask, cond_names_arr[single_fail_idx], "")

        # Vectorized failed_conditions list-per-row
        n_rows = len(df)
        failed_lists: list[list[str]] = [[] for _ in range(n_rows)]
        row_indices, col_indices = np.where(failed_matrix)
        for r, c in zip(row_indices, col_indices, strict=False):
            failed_lists[r].append(cond_names[c])

        res["failed_conditions"] = failed_lists
        return res


BaseScreener = Screener
