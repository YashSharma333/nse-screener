"""
Historical Signal Evaluation Engine
===================================
Evaluates a strategy's entry condition (a condition tree, see docs/condition_engine.md) across
a multi-symbol price history without look-ahead bias: the answer on day ``t`` uses data up to the
close of ``t`` only. Provides sparse signal projection for memory-efficient backtest ingestion.
"""

from __future__ import annotations

import pandas as pd

from src.conditions import evaluate
from src.strategies.spec import EntrySpec


def evaluate_entry(
    entry: EntrySpec, table: pd.DataFrame, benchmarks: pd.DataFrame | None = None
) -> pd.Series:
    """True on every stock-day of ``table`` where the entry condition is true at the close.

    ``table`` is a price history (symbol, date, open, high, low, close, volume, ...) with many
    days per stock, because indicators and "N days ago" parts need the earlier rows.
    ``benchmarks`` (index_code, date, close) is needed only when the condition has an index part.
    """
    return evaluate(entry.condition, table, benchmarks)


def to_sparse(signals_df: pd.DataFrame) -> pd.DataFrame:
    """Downsample signal panel to only rows where signal is True.

    Parameters
    ----------
    signals_df : pd.DataFrame
        Panel with boolean 'signal' column.

    Returns
    -------
    pd.DataFrame
        Filtered sparse DataFrame containing only actionable signal dates.
    """
    if signals_df.empty or "signal" not in signals_df.columns:
        return pd.DataFrame()

    sparse = signals_df[signals_df["signal"]].copy()
    return sparse.reset_index(drop=True)
