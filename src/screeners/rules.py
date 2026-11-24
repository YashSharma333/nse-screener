"""
Safe AST Rule Parser for Strategy Expressions
==============================================
Evaluates boolean and arithmetic expressions safely using Python's ast module.
Restricted to a whitelist of operators and variable lookups against DataFrame columns.
Arbitrary function calls, attribute lookups, imports, and comprehensions are forbidden.
"""

from __future__ import annotations

import ast
import operator
from collections.abc import Sequence
from typing import Any

import pandas as pd

from src.screeners.base import MissingIndicatorError

# Whitelist allowed AST operators
_COMPARE_OPS: dict[type[ast.cmpop], Any] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}

_BOOL_OPS: dict[type[ast.boolop], Any] = {
    ast.And: operator.and_,
    ast.Or: operator.or_,
}

_BIN_OPS: dict[type[ast.operator], Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}

_UNARY_OPS: dict[type[ast.unaryop], Any] = {
    ast.Not: operator.not_,
    ast.USub: operator.neg,
}


def _eval_node(node: ast.AST, df: pd.DataFrame) -> Any:
    """Recursively evaluate a whitelisted AST node against a pandas DataFrame."""
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, df)

    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.Name):
        col = node.id
        if col not in df.columns:
            raise MissingIndicatorError([col])
        return df[col]

    if isinstance(node, ast.UnaryOp):
        op_fn = _UNARY_OPS.get(type(node.op))
        if op_fn is None:
            raise ValueError(f"Unsupported unary operator: {type(node.op).__name__}")
        val = _eval_node(node.operand, df)
        if isinstance(val, pd.Series) and op_fn is operator.not_:
            return ~val.astype(bool)
        return op_fn(val)

    if isinstance(node, ast.BinOp):
        op_fn = _BIN_OPS.get(type(node.op))
        if op_fn is None:
            raise ValueError(f"Unsupported binary operator: {type(node.op).__name__}")
        left = _eval_node(node.left, df)
        right = _eval_node(node.right, df)
        return op_fn(left, right)

    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, df)
        result = None
        for op, comparator in zip(node.ops, node.comparators, strict=False):
            op_fn = _COMPARE_OPS.get(type(op))
            if op_fn is None:
                raise ValueError(f"Unsupported comparison operator: {type(op).__name__}")
            right = _eval_node(comparator, df)
            comp_res = op_fn(left, right)
            result = comp_res if result is None else (result & comp_res)
            left = right
        return result

    if isinstance(node, ast.BoolOp):
        op_fn = _BOOL_OPS.get(type(node.op))
        if op_fn is None:
            raise ValueError(f"Unsupported boolean operator: {type(node.op).__name__}")
        values = [_eval_node(v, df) for v in node.values]
        res = values[0]
        for v in values[1:]:
            res = op_fn(res, v)
        return res

    raise ValueError(f"Unsupported or unsafe expression element: {type(node).__name__}")


def evaluate_rule(expr: str, df: pd.DataFrame) -> pd.Series:
    """Safely parse and evaluate a rule expression string against a DataFrame.

    Parameters
    ----------
    expr : str
        Expression to evaluate, e.g. "close > sma_50 and rsi_14 < 45"
    df : pd.DataFrame
        Market data containing required indicator columns.

    Returns
    -------
    pd.Series
        Boolean Series aligned with input DataFrame index.
    """
    if df.empty:
        return pd.Series(dtype=bool, index=df.index)

    cleaned = expr.strip()
    if not cleaned:
        return pd.Series(True, index=df.index, dtype=bool)

    try:
        parsed = ast.parse(cleaned, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Syntax error in rule expression '{expr}': {exc}") from exc

    raw_result = _eval_node(parsed, df)
    if isinstance(raw_result, pd.Series):
        return raw_result.fillna(False).astype(bool)

    # Scalar boolean fallback
    return pd.Series(bool(raw_result), index=df.index, dtype=bool)


def extract_required_columns(expr: str) -> list[str]:
    """Parse expression and extract all variable identifier names."""
    cleaned = expr.strip()
    if not cleaned:
        return []

    try:
        parsed = ast.parse(cleaned, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Syntax error in rule expression '{expr}': {exc}") from exc

    names = []
    for node in ast.walk(parsed):
        if isinstance(node, ast.Name):
            names.append(node.id)
    return sorted(set(names))


def validate_rule(expr: str, available_columns: Sequence[str] | None = None) -> list[str]:
    """Validate expression syntax and required columns.

    Returns
    -------
    list[str]
        List of required column identifiers referenced in the expression.
    """
    req_cols = extract_required_columns(expr)
    if available_columns is not None:
        missing = [c for c in req_cols if c not in available_columns]
        if missing:
            raise MissingIndicatorError(missing)
    return req_cols
