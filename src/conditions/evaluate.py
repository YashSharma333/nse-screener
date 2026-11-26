"""
Evaluate a condition tree (CL-24C)
==================================
``evaluate(node, table, benchmarks)`` gives one true/false per row of ``table`` (one row per
stock per day); the answer on day ``t`` uses data up to the close of ``t`` only.

``table`` needs ``symbol``, ``date`` and the columns the tree reads (open, high, low, close,
volume, market_cap). ``benchmarks`` has ``index_code``, ``date`` and ``close`` (the
``benchmark_prices`` shape) and is needed only when the tree has an index part.

How unknowns work: a number is unknown (NaN) when there is not enough history (SMA 200 on day
50, a price 252 days ago on day 100) or when the maths has no answer (dividing by 0). A compare
with an unknown side is unknown, a count window over unknown rows is unknown, and the final
answer turns every unknown into False. Nothing is ever filled with a default.

Every look-back (days_ago, window, crossed_above) runs inside each stock through the shared
helpers of ``src/computation/indicators``, so one stock never sees another stock's rows.
"""

from __future__ import annotations

import operator
from collections.abc import Callable
from dataclasses import dataclass
from functools import cached_property
from typing import Any

import numpy as np
import pandas as pd

from src.computation.indicators.grouped import shift
from src.computation.indicators.price_helpers import ratio, window_stat
from src.computation.indicators.resolver import REGISTRY
from src.conditions.groups import combine
from src.conditions.nodes import (
    TRUE_FALSE,
    CompareNode,
    ConditionError,
    FieldNode,
    FuncNode,
    GroupNode,
    IndexNode,
    IndicatorNode,
    MathNode,
    Node,
    NumberNode,
    WindowNode,
)

_SOURCE = "__source__"  # the column that carries a nested part into an indicator function

_COMPARE: dict[str, Callable[[pd.Series, pd.Series], pd.Series]] = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "=": operator.eq,
    "!=": operator.ne,
}

_FUNCTIONS: dict[str, Callable[[np.ndarray], np.ndarray]] = {
    "abs": np.abs,
    "sqrt": np.sqrt,
    "log": np.log,
    "log10": np.log10,
    "floor": np.floor,
    "ceil": np.ceil,
    "round": np.round,
    "square": np.square,
}


@dataclass
class _Data:
    """The table and benchmarks one evaluation reads (the dates are converted only once)."""

    table: pd.DataFrame
    benchmarks: pd.DataFrame | None

    @cached_property
    def dates(self) -> pd.Series:
        if "date" not in self.table.columns:
            raise ConditionError("the table needs a 'date' column to match index rows")
        return pd.to_datetime(self.table["date"]).dt.date


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------
def evaluate(node: Node, table: pd.DataFrame, benchmarks: pd.DataFrame | None = None) -> pd.Series:
    """True/false for every row of ``table``; unknown counts as False."""
    if node.kind != TRUE_FALSE:
        raise ConditionError(f"evaluate needs a true/false condition, got a {node.kind} part")
    answer = _value(node, _Data(table, benchmarks))
    return answer.fillna(False).astype(bool)


def evaluate_number(
    node: Node, table: pd.DataFrame, benchmarks: pd.DataFrame | None = None
) -> pd.Series:
    """The number a number part gives for every row of ``table`` (NaN = unknown)."""
    if node.kind == TRUE_FALSE:
        raise ConditionError("evaluate_number needs a number part, got a true/false condition")
    return _value(node, _Data(table, benchmarks))


# ---------------------------------------------------------------------------
# One function per kind of part
# ---------------------------------------------------------------------------
def _value(node: Node, data: _Data) -> pd.Series:
    match node:
        case NumberNode():
            return pd.Series(float(node.value), index=data.table.index)
        case FieldNode():
            return _field(node, data)
        case IndexNode():
            return _index(node, data)
        case IndicatorNode():
            return _indicator(node, data)
        case WindowNode():
            return _window(node, data)
        case MathNode():
            return _math(node, data)
        case FuncNode():
            return _function(node, data)
        case CompareNode():
            return _compare(node, data)
        case GroupNode():
            children = [_value(child, data) for child in node.children]
            return combine(node.mode, children)
    raise ConditionError(f"unknown part {node!r}")  # unreachable: the tree was checked on load


def _field(node: FieldNode, data: _Data) -> pd.Series:
    table = data.table
    if node.name not in table.columns:
        raise ConditionError(f"the table has no column {node.name!r}; it has {list(table.columns)}")
    series = table[node.name].astype(float)
    return shift(table, series, node.days_ago) if node.days_ago else series


def _index(node: IndexNode, data: _Data) -> pd.Series:
    benchmarks = data.benchmarks
    code = node.index.strip().upper().replace("_", " ")
    if benchmarks is None:
        raise ConditionError(f"the condition uses index {code!r} but no benchmark table was given")
    if node.name not in benchmarks.columns:
        raise ConditionError(
            f"the benchmark table has no {node.name!r} column (it has {list(benchmarks.columns)}); "
            "index open, high and low arrive with the index OHLC download"
        )
    rows = benchmarks[benchmarks["index_code"].str.upper() == code]
    if rows.empty:
        known = sorted(benchmarks["index_code"].unique())
        raise ConditionError(f"index {code!r} is not in the benchmark table; it has {known}")
    dates = pd.to_datetime(rows["date"]).dt.date
    by_date = pd.Series(rows[node.name].to_numpy(dtype=float), index=dates.to_numpy())
    by_date = by_date[~by_date.index.duplicated(keep="last")].sort_index()
    # "N days ago" counts rows of the INDEX's own calendar, and looks only backwards
    by_date = by_date.shift(node.days_ago)
    return data.dates.map(by_date).astype(float)


def _source_frame(data: _Data, series: pd.Series) -> pd.DataFrame:
    """A small table holding only the stock names and the part to feed into a function."""
    frame = pd.DataFrame({_SOURCE: series}, index=data.table.index)
    if "symbol" in data.table.columns:
        frame["symbol"] = data.table["symbol"]
    return frame


def _indicator(node: IndicatorNode, data: _Data) -> pd.Series:
    numbers: dict[str, Any] = dict(node.params)
    frame = data.table
    if node.source is not None:
        frame = _source_frame(data, _value(node.source, data))
        numbers["source"] = _SOURCE
    try:
        result = REGISTRY[node.name](frame, **numbers)
    except (KeyError, ValueError, TypeError) as error:
        raise ConditionError(f"indicator {node.name!r}: {error}") from error
    result = result.astype(float)
    return shift(data.table, result, node.days_ago) if node.days_ago else result


def _window(node: WindowNode, data: _Data) -> pd.Series:
    inner = _value(node.source, data)
    stat = node.stat
    if stat == "count":
        # true = 1, false = 0, unknown = NaN; a window with an unknown row has no count yet
        inner = pd.Series(inner.to_numpy(dtype=float, na_value=np.nan), index=inner.index)
        stat = "sum"
    return window_stat(_source_frame(data, inner), _SOURCE, node.length, stat, node.days_ago)


def _math(node: MathNode, data: _Data) -> pd.Series:
    left, right = _value(node.left, data), _value(node.right, data)
    if node.op == "/":
        return ratio(left, right)  # dividing by 0 is unknown, never infinity
    result = {"+": operator.add, "-": operator.sub, "*": operator.mul}[node.op](left, right)
    return result.where(np.isfinite(result))


def _function(node: FuncNode, data: _Data) -> pd.Series:
    arg = _value(node.arg, data)
    values = arg.to_numpy(dtype=float)
    if node.name in ("log", "log10"):
        values = np.where(values > 0, values, np.nan)  # the log of 0 or less has no answer
    with np.errstate(invalid="ignore"):  # sqrt of a negative gives NaN: that is the answer
        result = _FUNCTIONS[node.name](values)
    return pd.Series(np.where(np.isfinite(result), result, np.nan), index=arg.index)


def _compare(node: CompareNode, data: _Data) -> pd.Series:
    left, right = _value(node.left, data), _value(node.right, data)
    known = left.notna() & right.notna()
    if node.op in _COMPARE:
        answer = _COMPARE[node.op](left, right)
    else:
        # a cross compares today with yesterday, inside each stock
        before_left, before_right = shift(data.table, left, 1), shift(data.table, right, 1)
        known &= before_left.notna() & before_right.notna()
        if node.op == "crossed_above":
            answer = (left > right) & (before_left <= before_right)
        else:
            answer = (left < right) & (before_left >= before_right)
    return answer.astype("boolean").mask(~known)
