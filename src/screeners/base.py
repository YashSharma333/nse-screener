"""Screener Base Class
====================
Provides the base class `Screener` that all systematic screens inherit from.

Every screener must define:
  - Metadata: key, name, description, category, version, status
  - default_params: plain dict of threshold values with their defaults
  - rules(df, params): a single method returning {condition_name: boolean Series}

The base class handles execution (run), diagnostics (explain), and near-miss
detection automatically — no boilerplate needed in subclasses.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

import numpy as np
import pandas as pd


class MissingIndicatorError(Exception):
    """Raised when required precomputed indicator columns are missing from the input."""

    def __init__(self, missing_columns: list[str] | set[str]):
        self.missing_columns = sorted(missing_columns)
        super().__init__(f"Missing required indicator columns: {', '.join(self.missing_columns)}")

    def __str__(self) -> str:
        return f"Missing required indicator columns: {', '.join(self.missing_columns)}"


class Screener:
    """Base class for quantitative stock screens.

    To create a new screener, subclass this and define:
      - Class-level attributes: key, name, description, category, version, status,
        evidence_columns, required_columns, default_params
      - One method: rules(self, df, params) -> dict[str, pd.Series]

    Example:
        @register_screener()
        class MyScreener(Screener):
            key = "my_screener"
            name = "My Screener"
            description = "Stocks above 50 SMA with volume surge."
            category = "swing"
            version = "1.0"
            status = "experimental"
            evidence_columns = ("close", "sma_50", "volume", "volume_sma_20")
            required_columns = ("close", "sma_50", "volume", "volume_sma_20")
            default_params = {"vol_multiplier": 1.5}

            def rules(self, df: pd.DataFrame, p: dict) -> dict[str, pd.Series]:
                return {
                    "above_50sma":    df["close"] > df["sma_50"],
                    "volume_surge":   df["volume"] > p["vol_multiplier"] * df["volume_sma_20"],
                }
    """

    key: str = ""
    name: str = ""
    description: str = ""
    category: str = "general"
    version: str = "1.0"
    status: Literal["core", "experimental"] = "experimental"
    evidence_columns: tuple[str, ...] = ()
    required_columns: tuple[str, ...] = ()
    # Plain dict of parameter names → default values.
    # Override individual params by passing keyword arguments to __init__.
    default_params: ClassVar[dict[str, Any]] = {}

    def __init__(self, **overrides: Any) -> None:
        # Merge class-level defaults with any caller-supplied overrides.
        valid = set(self.__class__.default_params.keys())
        unknown = set(overrides.keys()) - valid
        if unknown:
            raise TypeError(
                f"Unknown parameter(s) for {self.__class__.__name__}: "
                f"{', '.join(sorted(unknown))}. "
                f"Valid parameters: {', '.join(sorted(valid)) or '(none)'}"
            )
        self.params: dict[str, Any] = {**self.__class__.default_params, **overrides}

    # ------------------------------------------------------------------
    # The one method subclasses must implement
    # ------------------------------------------------------------------
    def rules(self, df: pd.DataFrame, params: dict[str, Any]) -> dict[str, pd.Series]:
        """Return a mapping of {condition_name: boolean Series}.

        Each Series must be aligned with df.index and contain only True/False.
        A row passes the screener only if ALL conditions are True.

        Parameters
        ----------
        df : pd.DataFrame
            Indicator snapshot or history slice for the screener to evaluate.
        params : dict
            Merged parameter dict (defaults + any caller overrides).
        """
        del df, params
        return {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _validate_required_columns(self, df: pd.DataFrame) -> None:
        """Raise MissingIndicatorError if any required column is absent."""
        if not self.required_columns or df.empty:
            return
        missing = [col for col in self.required_columns if col not in df.columns]
        if missing:
            raise MissingIndicatorError(missing)

    def all_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        """Return a flat {condition_name: bool Series} mapping.

        Calls self.rules() after validating required columns.
        """
        self._validate_required_columns(df)
        raw = self.rules(df, self.params)
        result: dict[str, pd.Series] = {}
        for name, series in raw.items():
            if isinstance(series, pd.Series):
                result[name] = series.fillna(False).astype(bool)
            else:
                result[name] = pd.Series(bool(series), index=df.index, dtype=bool)
        return result

    # ------------------------------------------------------------------
    # Public API: run() and explain()
    # ------------------------------------------------------------------
    def run(self, df: pd.DataFrame) -> pd.DataFrame:
        """Return rows from df that pass every screening condition."""
        if df.empty:
            return df.copy()

        conditions = self.all_conditions(df)
        if not conditions:
            return df.copy()

        mask = pd.Series(True, index=df.index)
        for cond_series in conditions.values():
            mask &= cond_series

        return df[mask].copy()

    def explain(self, df: pd.DataFrame) -> pd.DataFrame:
        """Evaluate all conditions and add diagnostic columns.

        Added columns:
        - ``cond_{name}``          : bool column per condition
        - ``conditions_passed``    : how many conditions this row passed
        - ``conditions_total``     : total number of conditions
        - ``passed``               : True if all conditions passed
        - ``failed_conditions``    : list of condition names that failed
        - ``near_miss``            : True if exactly 1 condition failed
        - ``near_miss_on``         : name of that single failed condition (or "")
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
        conditions = self.all_conditions(df)

        if not conditions:
            res["conditions_passed"] = 0
            res["conditions_total"] = 0
            res["passed"] = True
            res["failed_conditions"] = [[] for _ in range(len(res))]
            res["near_miss"] = False
            res["near_miss_on"] = ""
            return res

        cond_names: list[str] = list(conditions.keys())
        cond_series_list: list[pd.Series] = []

        for name, series in conditions.items():
            res[f"cond_{name}"] = series
            cond_series_list.append(series)

        # Vectorised diagnostics — no iterrows
        cond_values = np.stack([s.to_numpy(dtype=bool) for s in cond_series_list], axis=1)
        failed_matrix = ~cond_values
        passed_count = cond_values.sum(axis=1)
        total_count = len(cond_names)

        res["conditions_passed"] = passed_count
        res["conditions_total"] = total_count
        res["passed"] = passed_count == total_count

        near_miss_mask = passed_count == (total_count - 1)
        res["near_miss"] = near_miss_mask

        # Which single condition failed for near-miss rows
        cond_names_arr = np.array(cond_names, dtype=object)
        single_fail_idx = np.argmin(cond_values, axis=1)
        res["near_miss_on"] = np.where(near_miss_mask, cond_names_arr[single_fail_idx], "")

        # Build failed_conditions list per row
        n_rows = len(df)
        failed_lists: list[list[str]] = [[] for _ in range(n_rows)]
        row_indices, col_indices = np.where(failed_matrix)
        for r, c in zip(row_indices, col_indices, strict=False):
            failed_lists[r].append(cond_names[c])
        res["failed_conditions"] = failed_lists

        return res

    @classmethod
    def get_metadata(cls) -> dict[str, Any]:
        """Return screener metadata as a plain dict."""
        return {
            "key": cls.key,
            "name": cls.name,
            "description": cls.description,
            "category": cls.category,
            "version": str(cls.version),
            "status": cls.status,
            "evidence_columns": cls.evidence_columns,
            "required_columns": cls.required_columns,
        }


# Alias kept for backward compatibility with any code that imports BaseScreener
BaseScreener = Screener
