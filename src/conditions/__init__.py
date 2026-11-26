"""User-built conditions: a saved tree of parts that is true or false per stock per day.

Build or load a tree with ``parse_condition`` / ``load_condition`` and evaluate it with
``evaluate``. See docs/condition_engine.md. ``rules.py`` (the old text rule parser) is removed
once the strategies use trees.
"""

from src.conditions.evaluate import evaluate, evaluate_number
from src.conditions.nodes import ConditionError, Node, condition_to_dict, parse_condition
from src.conditions.storage import SavedCondition, load_condition, save_condition

__all__ = [
    "ConditionError",
    "Node",
    "SavedCondition",
    "condition_to_dict",
    "evaluate",
    "evaluate_number",
    "load_condition",
    "parse_condition",
    "save_condition",
]
