"""
The indicator resolver (CL-23D)
===============================
One entry point that runs the single indicator function with the user's numbers::

    spec = ind("sma", source="volume", length=20, days_ago=1)   # what the user picked
    spec.column                                                   # "sma_volume_20_ago1"
    compute_indicator(df, spec)                                   # the Series
    ensure_columns(df, [ind("sma", length=n) for n in (10, 21, 50, 100)])  # four columns

Defaults live ONLY in each function's signature; ``ind`` fills them in from there. An indicator
of an indicator is ``ind("sma", source=ind("rsi"), length=5)``: the inner one is computed into a
column first and its NAME is passed as ``source``, so the functions never know about nesting,
``days_ago`` or timeframes. ``days_ago`` is applied inside each stock, after the calculation.

``describe_indicators()`` lists names, parameters, defaults and outputs as plain JSON for a UI.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

import pandas as pd

from src.computation.indicators import calculator, price_helpers
from src.computation.indicators.grouped import check_days_ago, shift

# Table-only indicators. Benchmark, rank and crossover functions join in CL-24.
REGISTRY: dict[str, Callable[..., pd.Series]] = {
    "sma": calculator.sma,
    "ema": calculator.ema,
    "wma": calculator.wma,
    "rsi": calculator.rsi,
    "macd": calculator.macd,
    "bollinger_bands": calculator.bollinger_bands,
    "atr": calculator.atr,
    "true_range": calculator.true_range,
    "volatility": calculator.volatility,
    "percent_change": calculator.percent_change,
    "window": price_helpers.window_stat,
}

# Parameters the resolver owns, so the user never passes them to the function.
# window_stat has its own days_ago; the resolver's days_ago means the same for every indicator.
_HIDDEN = {"df", "days_ago"}

# Allowed values for parameters that are a fixed list (the rest are free numbers or columns).
_CHOICES = {
    "stat": price_helpers.STATS,
}


@dataclass(frozen=True)
class IndicatorSpec:
    """What the user picked: the indicator, its full numbers and how many days back."""

    name: str
    params: tuple[tuple[str, Any], ...]  # (parameter, value) in signature order, defaults filled
    days_ago: int = 0

    @property
    def column(self) -> str:
        """A stable, readable column name such as ``sma_volume_20_ago1``."""
        numbers = dict(self.params)
        # the column being read comes first, then the other numbers in signature order
        ordered = [numbers.pop("source")] if "source" in numbers else []
        values = [_as_text(value) for value in [*ordered, *numbers.values()]]
        ago = f"_ago{self.days_ago}" if self.days_ago else ""
        return "_".join([self.name, *values]) + ago


def _as_text(value: Any) -> str:
    """A parameter value as it appears in a column name (a nested indicator is its column)."""
    if isinstance(value, IndicatorSpec):
        return value.column
    return f"{value:g}" if isinstance(value, float) else str(value)


def _parameters(name: str) -> dict[str, inspect.Parameter]:
    """The parameters a user may set on indicator ``name``, in signature order."""
    if name not in REGISTRY:
        raise ValueError(f"Unknown indicator {name!r}; choose one of {sorted(REGISTRY)}")
    # KEEP: the only place that reads signatures. It removes a second copy of every default.
    signature = inspect.signature(REGISTRY[name])
    return {n: p for n, p in signature.parameters.items() if n not in _HIDDEN}


def ind(name: str, days_ago: int = 0, **custom: Any) -> IndicatorSpec:
    """Describe an indicator with the user's numbers; anything not given keeps its default."""
    parameters = _parameters(name)
    for given in custom:
        if given not in parameters:
            raise ValueError(
                f"{name!r} has no parameter {given!r}; choose one of {list(parameters)}"
            )
    check_days_ago(days_ago)
    params = tuple(
        (n, custom.get(n, p.default))
        for n, p in parameters.items()
        if n in custom or p.default is not inspect.Parameter.empty
    )
    return IndicatorSpec(name, params, days_ago)


def compute_indicator(df: pd.DataFrame, spec: IndicatorSpec) -> pd.Series:
    """Run the indicator function with the spec's numbers, then look ``days_ago`` back."""
    numbers = dict(spec.params)
    for key, value in numbers.items():
        if isinstance(value, IndicatorSpec):  # indicator of an indicator
            inner = compute_indicator(df, value)
            df = df.assign(**{value.column: inner})  # a copy: the caller's table is unchanged
            numbers[key] = value.column
    result = REGISTRY[spec.name](df, **numbers)
    return shift(df, result, spec.days_ago) if spec.days_ago else result


def ensure_columns(df: pd.DataFrame, specs: Iterable[IndicatorSpec]) -> pd.DataFrame:
    """Add the column of every spec that is not in ``df`` yet (in place) and return ``df``."""
    for spec in specs:
        if spec.column not in df.columns:
            df[spec.column] = compute_indicator(df, spec)
    return df


def describe_indicators() -> list[dict[str, Any]]:
    """Every indicator as JSON-ready data: name, summary, parameters (with defaults), outputs."""
    return [_describe(name) for name in REGISTRY]


def _describe(name: str) -> dict[str, Any]:
    parameters = []
    for param_name, parameter in _parameters(name).items():
        item: dict[str, Any] = {"name": param_name}
        item["type"] = str(parameter.annotation)
        if parameter.default is not inspect.Parameter.empty:
            item["default"] = parameter.default
        if param_name in _CHOICES:
            item["choices"] = list(_CHOICES[param_name])
        parameters.append(item)
    summary = (inspect.getdoc(REGISTRY[name]) or "").split("\n")[0]
    outputs = list(calculator.OUTPUT_CHOICES.get(name, ()))
    return {"name": name, "summary": summary, "parameters": parameters, "outputs": outputs}
