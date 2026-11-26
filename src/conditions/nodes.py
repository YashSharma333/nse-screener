"""
The parts a condition is built from (CL-24C)
============================================
A condition is a JSON tree. Every part is an object with a ``"type"``; the parts nest::

    {"type": "compare", "op": ">",
     "left":  {"type": "field", "name": "close"},
     "right": {"type": "indicator", "name": "sma", "params": {"length": 50}}}

Each part is either a **number** part (it gives a number per stock per day: a price, an
indicator, a sum ...) or a **true/false** part (compare, group). The rules about what may sit
inside what (``compare`` needs numbers, ``group`` needs true/false parts) are checked when the
tree is loaded, and a mistake is reported with its place in the tree, for example
``at group.children[1].compare.left: ...``. Evaluation is in ``evaluate.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from src.computation.indicators.resolver import REGISTRY, ind, indicator_parameters

FIELD_NAMES = ("open", "high", "low", "close", "volume", "market_cap")
INDEX_FIELD_NAMES = ("open", "high", "low", "close")
MATH_OPS = ("+", "-", "*", "/")
FUNCTION_NAMES = ("abs", "sqrt", "log", "log10", "floor", "ceil", "round", "square")
COMPARE_OPS = (">", ">=", "<", "<=", "=", "!=", "crossed_above", "crossed_below")
GROUP_MODES = ("all", "any", "any_1", "any_2")  # every / at least 1 / exactly 1 / at least 2
WINDOW_STATS = ("max", "min", "sum", "count", "mean")

# window and value are the window and field parts; the rest are indicators a user can pick
INDICATOR_NAMES = tuple(name for name in REGISTRY if name not in ("window", "value"))

NUMBER = "number"
TRUE_FALSE = "true/false"


class ConditionError(ValueError):
    """A condition is wrong (bad tree) or cannot be evaluated (missing column or table)."""


class _Part(BaseModel):
    """Base of every part: no unknown fields (a typo is an error), values never change."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: ClassVar[str]


class NumberNode(_Part):
    """A constant number."""

    kind: ClassVar[str] = NUMBER
    type: Literal["number"]
    value: float


class FieldNode(_Part):
    """A price or volume column of the stock, optionally ``days_ago`` trading days back."""

    kind: ClassVar[str] = NUMBER
    type: Literal["field"]
    name: Literal["open", "high", "low", "close", "volume", "market_cap"]
    days_ago: int = Field(default=0, ge=0)


class IndexNode(_Part):
    """A market index price (NIFTY 500 ...), optionally ``days_ago`` index days back."""

    kind: ClassVar[str] = NUMBER
    type: Literal["index"]
    index: str
    name: Literal["open", "high", "low", "close"] = "close"
    days_ago: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _index_name_is_not_empty(self) -> IndexNode:
        if not self.index.strip():
            raise ValueError("index must name an index, for example 'NIFTY 500'")
        return self


class IndicatorNode(_Part):
    """An indicator with the user's numbers, on the close or on any other number part."""

    kind: ClassVar[str] = NUMBER
    type: Literal["indicator"]
    name: str
    params: dict[str, int | float | str] = Field(default_factory=dict)
    source: Node | None = None
    days_ago: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _check_name_numbers_and_source(self) -> IndicatorNode:
        if self.name not in INDICATOR_NAMES:
            raise ValueError(f"unknown indicator {self.name!r}; choose one of {INDICATOR_NAMES}")
        allowed = indicator_parameters(self.name)
        if "source" in self.params:
            raise ValueError("put the source in the 'source' field, not in params")
        ind(self.name, **self.params)  # raises a readable ValueError for an unknown number
        if self.source is not None:
            if "source" not in allowed:
                raise ValueError(f"indicator {self.name!r} reads its own columns: it has no source")
            _need(self.source, NUMBER, "indicator source")
        return self


class WindowNode(_Part):
    """The max, min, sum, mean or count of a part over the last ``length`` days."""

    kind: ClassVar[str] = NUMBER
    type: Literal["window"]
    stat: Literal["max", "min", "sum", "count", "mean"]
    length: int = Field(ge=1)
    source: Node
    days_ago: int = Field(default=0, ge=0)  # applied AFTER the window is computed

    @model_validator(mode="after")
    def _source_matches_the_stat(self) -> WindowNode:
        wanted = TRUE_FALSE if self.stat == "count" else NUMBER
        _need(self.source, wanted, f"window stat {self.stat!r} needs a {wanted} part: the source")
        return self


class MathNode(_Part):
    """Two number parts joined by ``+ - * /`` (explicit nesting: no precedence rules)."""

    kind: ClassVar[str] = NUMBER
    type: Literal["math"]
    op: Literal["+", "-", "*", "/"]
    left: Node
    right: Node

    @model_validator(mode="after")
    def _both_sides_are_numbers(self) -> MathNode:
        _need(self.left, NUMBER, "math needs a number part on the left")
        _need(self.right, NUMBER, "math needs a number part on the right")
        return self


class FuncNode(_Part):
    """A function (abs, sqrt, log ...) of one number part."""

    kind: ClassVar[str] = NUMBER
    type: Literal["func"]
    name: Literal["abs", "sqrt", "log", "log10", "floor", "ceil", "round", "square"]
    arg: Node

    @model_validator(mode="after")
    def _arg_is_a_number(self) -> FuncNode:
        _need(self.arg, NUMBER, "func needs a number part: the arg")
        return self


class CompareNode(_Part):
    """Two number parts compared; true, false (or unknown, which counts as false)."""

    kind: ClassVar[str] = TRUE_FALSE
    type: Literal["compare"]
    op: Literal[">", ">=", "<", "<=", "=", "!=", "crossed_above", "crossed_below"]
    left: Node
    right: Node

    @model_validator(mode="after")
    def _both_sides_are_numbers(self) -> CompareNode:
        _need(self.left, NUMBER, "compare needs a number part on the left")
        _need(self.right, NUMBER, "compare needs a number part on the right")
        return self


class GroupNode(_Part):
    """True/false parts combined: all, any (at least 1), any_1 (exactly 1), any_2 (at least 2)."""

    kind: ClassVar[str] = TRUE_FALSE
    type: Literal["group"]
    mode: Literal["all", "any", "any_1", "any_2"]
    children: list[Node] = Field(min_length=1)

    @model_validator(mode="after")
    def _children_are_true_false(self) -> GroupNode:
        for child in self.children:
            _need(child, TRUE_FALSE, "group children must be true/false parts")
        if self.mode == "any_2" and len(self.children) < 2:
            raise ValueError("mode 'any_2' (at least two true) needs at least 2 children")
        return self


Node = Annotated[
    NumberNode
    | FieldNode
    | IndexNode
    | IndicatorNode
    | WindowNode
    | MathNode
    | FuncNode
    | CompareNode
    | GroupNode,
    Field(discriminator="type"),
]

for _part in (IndicatorNode, WindowNode, MathNode, FuncNode, CompareNode, GroupNode):
    _part.model_rebuild()

_NODE_ADAPTER: TypeAdapter[Any] = TypeAdapter(Node)


def _need(part: _Part, kind: str, message: str) -> None:
    """Raise ValueError (pydantic turns it into an error with the place in the tree)."""
    if part.kind != kind:
        raise ValueError(f"{message} (got a {part.kind} part)")


def parse_condition(data: Mapping[str, Any]) -> Node:
    """Build a checked condition tree from plain JSON data; ConditionError says what is wrong."""
    try:
        return _NODE_ADAPTER.validate_python(data)
    except ValidationError as error:
        raise ConditionError(describe_errors(error)) from error


def condition_to_dict(node: Node) -> dict[str, Any]:
    """The tree as plain JSON data (every default filled in, nothing left out)."""
    return node.model_dump(mode="json", exclude_none=True)


def describe_errors(error: ValidationError) -> str:
    """Readable text: one line per mistake, each with its place, like ``group.children[1].left``."""
    lines = []
    for item in error.errors():
        place = ""
        for step in item["loc"]:
            place += f"[{step}]" if isinstance(step, int) else f".{step}"
        message = item["msg"].removeprefix("Value error, ")
        lines.append(f"at {place.lstrip('.') or 'the root'}: {message}")
    return "Invalid condition: " + "; ".join(lines)
