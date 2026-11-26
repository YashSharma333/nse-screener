"""
Saving and loading conditions as JSON files (CL-24C)
====================================================
A saved scan is the tree plus a name::

    {"name": "My scan", "version": 1, "description": "...", "condition": {...tree...}}
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.conditions.nodes import ConditionError, Node, condition_to_dict, parse_condition

FILE_VERSION = 1


@dataclass(frozen=True)
class SavedCondition:
    """A named condition as stored in a file."""

    name: str
    condition: Node
    description: str = ""


def save_condition(path: Path, name: str, condition: Node, description: str = "") -> None:
    """Write the condition to ``path`` as readable JSON."""
    content: dict[str, Any] = {
        "name": name,
        "version": FILE_VERSION,
        "description": description,
        "condition": condition_to_dict(condition),
    }
    Path(path).write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")


def load_condition(path: Path) -> SavedCondition:
    """Read a saved condition; ConditionError says what is wrong with the file."""
    try:
        content = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ConditionError(f"cannot read {path}: {error}") from error
    if not isinstance(content, dict) or "condition" not in content:
        raise ConditionError(f"{path}: a saved condition needs a 'condition' tree")
    if content.get("version", FILE_VERSION) != FILE_VERSION:
        raise ConditionError(f"{path}: file version {content['version']!r} is not supported")
    try:
        condition = parse_condition(content["condition"])
    except ConditionError as error:
        raise ConditionError(f"{path}: {error}") from error
    return SavedCondition(
        content.get("name", Path(path).stem), condition, content.get("description", "")
    )
