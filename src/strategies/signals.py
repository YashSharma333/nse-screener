"""
Historical Signal Evaluation Engine
===================================
Evaluates a strategy's entry rules across historical multi-symbol panels without
look-ahead bias. Provides sparse signal projection for memory-efficient backtest ingestion.
"""

from __future__ import annotations

import functools
import operator
from collections.abc import Sequence

import pandas as pd

from src.conditions.rules import evaluate_rule


def evaluate_rules_history(
    rules: Sequence[str],
    df: pd.DataFrame,
) -> pd.Series:
    """Evaluate a sequence of rule expressions and return logical AND boolean Series.

    Parameters
    ----------
    rules : Sequence[str]
        List of rule expression strings (e.g. ["close > sma_50", "rsi_14 < 45"]).
    df : pd.DataFrame
        Historical market data panel.

    Returns
    -------
    pd.Series
        Boolean Series aligned with df.index indicating which rows pass all rules.
    """
    if df.empty:
        return pd.Series(dtype=bool, index=df.index)

    if not rules:
        return pd.Series(True, index=df.index, dtype=bool)

    masks = [evaluate_rule(rule_expr, df) for rule_expr in rules if rule_expr.strip()]
    if not masks:
        return pd.Series(True, index=df.index, dtype=bool)

    return functools.reduce(operator.and_, masks)


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
