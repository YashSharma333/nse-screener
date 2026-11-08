"""
Declarative JSON Screener Loader (E3)
=====================================
Enables traders and quantitative analysts to define systematic screeners purely
via JSON rule definitions without writing Python code. Parses rules safely with
the AST rule parser and registers dynamic Screener classes in ScreenerRegistry.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from config.settings import PROJECT_ROOT
from src.screeners.base import Screener
from src.screeners.registry import register_screener

logger = logging.getLogger(__name__)

CUSTOM_SCREENERS_DIR = PROJECT_ROOT / "screeners" / "custom"


def create_declarative_screener(spec: dict[str, Any]) -> type[Screener]:
    """Dynamically construct and register a Screener class from a declarative specification.

    Parameters
    ----------
    spec : dict[str, Any]
        Dictionary with keys: key, name, category, description, rules, evidence_columns.
    """
    key = str(spec["key"]).strip()
    name = str(spec.get("name", key)).strip()
    category = str(spec.get("category", "custom")).strip().lower()
    description = str(spec.get("description", f"Declarative screener {name}")).strip()
    version = str(spec.get("version", "1.0")).strip()
    status_val = spec.get("status", "experimental")
    screener_status: Literal["core", "experimental"] = (
        "core" if status_val == "core" else "experimental"
    )

    rules: list[str] = list(spec.get("rules", []))
    if not rules:
        raise ValueError(f"Declarative screener '{key}' must define at least one rule expression.")

    from src.strategies.rules import evaluate_rule, validate_rule

    # Validate rules and compute required columns
    all_req_cols: set[str] = set()
    for r in rules:
        all_req_cols.update(validate_rule(r))

    evidence_cols = tuple(spec.get("evidence_columns", sorted(all_req_cols)))
    required_cols = tuple(sorted(all_req_cols))

    # Dynamically build methods
    def conditions_by_nature(self, df: pd.DataFrame) -> dict[str, dict[str, pd.Series]]:
        self._validate_required_columns(df)
        conds: dict[str, pd.Series] = {}
        for idx, rule_expr in enumerate(rules, start=1):
            rule_name = f"rule_{idx}"
            conds[rule_name] = evaluate_rule(rule_expr, df)
        return {category: conds}

    attrs: dict[str, Any] = {
        "key": key,
        "name": name,
        "description": description,
        "category": category,
        "version": version,
        "status": screener_status,
        "evidence_columns": evidence_cols,
        "required_columns": required_cols,
        "rules": rules,
        "conditions_by_nature": conditions_by_nature,
    }

    class_name = "".join(w.capitalize() for w in key.replace("-", "_").split("_")) + "Screener"
    screener_cls = type(class_name, (Screener,), attrs)

    # Register in ScreenerRegistry
    register_screener(key=key)(screener_cls)

    logger.info("Registered declarative screener: '%s' with %d rules", key, len(rules))
    return screener_cls


def load_custom_screeners(
    directory: Path | None = None,
) -> list[type[Screener]]:
    """Scan directory for JSON screener definitions and register them.

    Parameters
    ----------
    directory : Path, optional
        Custom directory path. Defaults to screeners/custom/.
    """
    custom_dir = directory or CUSTOM_SCREENERS_DIR
    if not custom_dir.exists():
        return []

    registered: list[type[Screener]] = []
    for file_path in sorted(custom_dir.glob("*.json")):
        try:
            with file_path.open("r", encoding="utf-8") as f:
                spec = json.load(f)
            cls = create_declarative_screener(spec)
            registered.append(cls)
        except Exception as exc:
            logger.error("Failed to load custom declarative screener '%s': %s", file_path.name, exc)

    return registered
