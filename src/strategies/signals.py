"""
Historical Signal Evaluation Engine
===================================
Evaluates systematic Screener conditions and inline rule expressions across
historical multi-symbol panels without look-ahead bias. Provides sparse signal
projection for memory-efficient backtest ingestion.
"""

from __future__ import annotations

import functools
import operator
from collections.abc import Sequence

import pandas as pd

from src.screeners.base import BaseScreener
from src.strategies.rules import evaluate_rule


def evaluate_screener_history(
    screener: BaseScreener,
    df: pd.DataFrame,
) -> pd.DataFrame:
    """Evaluate a systematic Screener across a multi-symbol historical panel.

    Parameters
    ----------
    screener : BaseScreener
        Configured Screener instance with registered conditions and evidence columns.
    df : pd.DataFrame
        Historical panel containing 'symbol', 'date', OHLCV and precomputed indicators.

    Returns
    -------
    pd.DataFrame
        DataFrame with ['symbol', 'date', 'signal', ...evidence_columns].
    """
    if df.empty:
        return pd.DataFrame(columns=["symbol", "date", "signal", *screener.evidence_columns])

    # Validate required columns
    screener._validate_required_columns(df)

    cond_dict = screener.all_conditions(df)
    if not cond_dict:
        signal_mask = pd.Series(True, index=df.index, dtype=bool)
    else:
        signal_mask = functools.reduce(operator.and_, cond_dict.values())

    # Build output panel with symbol, date, signal, close, and evidence columns
    base_cols = [c for c in ("symbol", "date", "close") if c in df.columns]
    ev_cols = [c for c in screener.evidence_columns if c in df.columns and c not in base_cols]

    out_df = df[base_cols + ev_cols].copy()
    out_df["signal"] = signal_mask
    return out_df


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
