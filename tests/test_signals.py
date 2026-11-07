"""
Tests for Historical Signal Evaluation and Rule Parsing (T13).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.computation.indicators.calculator import TechnicalCalculator
from src.screeners.base import MissingIndicatorError
from src.screeners.registry import get_screener
from src.strategies.rules import evaluate_rule, validate_rule
from src.strategies.signals import evaluate_rules_history, evaluate_screener_history, to_sparse


@pytest.fixture
def sample_market_df():
    """Create a sample dataframe with technical indicator columns."""
    return pd.DataFrame(
        {
            "symbol": ["INFY", "TCS", "RELIANCE"],
            "date": [pd.to_datetime("2024-01-01").date()] * 3,
            "close": [1500.0, 3500.0, 2400.0],
            "open": [1490.0, 3510.0, 2380.0],
            "sma_50": [1450.0, 3600.0, 2300.0],
            "sma_200": [1400.0, 3400.0, 2200.0],
            "rsi_14": [45.0, 65.0, 30.0],
        }
    )


def test_rule_parser_comparisons_and_bool_ops(sample_market_df):
    """Verify safe evaluation of comparisons, arithmetic, and boolean ops."""
    # 1. Simple comparison
    res1 = evaluate_rule("close > sma_50", sample_market_df)
    assert res1.tolist() == [True, False, True]

    # 2. Boolean AND
    res2 = evaluate_rule("close > sma_50 and rsi_14 < 50", sample_market_df)
    assert res2.tolist() == [True, False, True]

    # 3. Arithmetic in expression
    res3 = evaluate_rule("close > sma_200 * 1.05", sample_market_df)
    # INFY: 1500 > 1470 (True), TCS: 3500 > 3570 (False), RELIANCE: 2400 > 2310 (True)
    assert res3.tolist() == [True, False, True]

    # 4. Unary NOT
    res4 = evaluate_rule("not (rsi_14 > 60)", sample_market_df)
    assert res4.tolist() == [True, False, True]


def test_rule_parser_missing_column_and_syntax_error(sample_market_df):
    """Missing indicators raise MissingIndicatorError, bad syntax raises ValueError."""
    with pytest.raises(MissingIndicatorError) as exc_info:
        evaluate_rule("close > non_existent_indicator", sample_market_df)
    assert "non_existent_indicator" in str(exc_info.value)

    with pytest.raises(ValueError, match="Syntax error"):
        evaluate_rule("close > > sma_50", sample_market_df)


def test_rule_parser_security_sandbox(sample_market_df):
    """Arbitrary function calls, imports, and attribute accesses must be blocked."""
    unsafe_expressions = [
        "__import__('os').system('echo pwned')",
        "open('/etc/passwd')",
        "close.values.tolist()",
        "[x for x in close]",
        "lambda x: x > 5",
    ]
    for unsafe in unsafe_expressions:
        with pytest.raises(ValueError, match="Unsupported or unsafe"):
            evaluate_rule(unsafe, sample_market_df)


def test_evaluate_rules_history(sample_market_df):
    """Multiple rules are evaluated as logical AND across all rules."""
    rules = ["close > sma_50", "rsi_14 <= 45"]
    mask = evaluate_rules_history(rules, sample_market_df)
    assert mask.tolist() == [True, False, True]

    # Empty rules return all True
    assert evaluate_rules_history([], sample_market_df).all()


def test_validate_rule_helper():
    """validate_rule checks required columns against an available set."""
    cols = validate_rule("close > sma_50 and rsi_14 < 30")
    assert cols == ["close", "rsi_14", "sma_50"]

    with pytest.raises(MissingIndicatorError):
        validate_rule("close > missing_col", available_columns=["close", "open"])


def test_evaluate_screener_history_and_to_sparse():
    """Evaluate a registered screener across multi-day multi-symbol panel and sparse projection."""
    np.random.seed(42)
    symbols = ["INFY", "TCS"]
    dates = pd.date_range("2024-01-01", periods=100, freq="B").strftime("%Y-%m-%d").tolist()

    records = []
    for sym in symbols:
        price = 1000.0
        for d in dates:
            ret = float(np.random.normal(0.001, 0.01))
            price *= 1.0 + ret
            records.append(
                {
                    "symbol": sym,
                    "date": d,
                    "open": price * 0.99,
                    "high": price * 1.02,
                    "low": price * 0.98,
                    "close": price,
                    "volume": 200000.0,
                    "prev_close": price / (1.0 + ret),
                }
            )
    raw_df = pd.DataFrame(records)

    calc = TechnicalCalculator()
    indicators_df = calc.apply_all_indicators(raw_df)

    screener = get_screener("above_50_sma")
    signals_df = evaluate_screener_history(screener, indicators_df)

    assert "signal" in signals_df.columns
    assert "symbol" in signals_df.columns
    assert "date" in signals_df.columns
    assert "close" in signals_df.columns
    assert len(signals_df) == len(indicators_df)

    # Downsample to sparse signals
    sparse_df = to_sparse(signals_df)
    assert (sparse_df["signal"] == True).all()  # noqa: E712
    assert len(sparse_df) <= len(signals_df)


def test_signals_point_in_time_truncation():
    """Signals generated at date t must remain invariant when future data is truncated."""
    np.random.seed(123)
    symbols = ["RELIANCE", "HDFCBANK"]
    dates = pd.date_range("2024-01-01", periods=150, freq="B").strftime("%Y-%m-%d").tolist()

    records = []
    for sym in symbols:
        price = 2000.0
        for d in dates:
            ret = float(np.random.normal(0.0005, 0.015))
            price *= 1.0 + ret
            records.append(
                {
                    "symbol": sym,
                    "date": d,
                    "open": price * 0.995,
                    "high": price * 1.015,
                    "low": price * 0.985,
                    "close": price,
                    "volume": 150000.0,
                    "prev_close": price / (1.0 + ret),
                }
            )
    raw_df = pd.DataFrame(records)

    calc = TechnicalCalculator()
    full_df = calc.apply_all_indicators(raw_df)

    t_eval = dates[100]
    raw_trunc = raw_df[raw_df["date"] <= t_eval].copy()
    trunc_df = calc.apply_all_indicators(raw_trunc)

    screener = get_screener("breakout_52w_volume")
    full_signals = evaluate_screener_history(screener, full_df)
    trunc_signals = evaluate_screener_history(screener, trunc_df)

    full_at_t = (
        full_signals[full_signals["date"] == t_eval].sort_values("symbol").reset_index(drop=True)
    )
    trunc_at_t = (
        trunc_signals[trunc_signals["date"] == t_eval].sort_values("symbol").reset_index(drop=True)
    )

    pd.testing.assert_series_equal(full_at_t["signal"], trunc_at_t["signal"], check_names=False)
