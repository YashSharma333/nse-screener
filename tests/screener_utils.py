"""Small helpers for screener tests."""

from __future__ import annotations

import pandas as pd

from src.screeners.base import Screener


def passed_rows(screener: Screener, df: pd.DataFrame) -> pd.DataFrame:
    """The rows of ``df`` that pass every condition (the screener's own indicators are added)."""
    prepared = screener.prepare(df)
    return prepared[screener.explain(prepared)["passed"]]
