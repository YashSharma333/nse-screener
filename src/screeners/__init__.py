"""Systematic Stock Screeners Package
==================================
Provides extensible, categorized systematic stock screens with condition-level
explainability and near-miss diagnostics.

Import names from their own module, for example
``from src.screeners.base import get_screener``.
"""

from __future__ import annotations

# Importing the catalog runs every @register_screener decorator, so the built-in
# screeners are registered as soon as anything from this package is imported.
from src.screeners import catalog  # noqa: F401
from src.screeners.declarative import load_custom_screeners

# Auto-discover declarative JSON screeners
load_custom_screeners()
