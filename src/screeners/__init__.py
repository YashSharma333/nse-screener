"""
Systematic Stock Screeners Package
==================================
Provides extensible, categorized systematic stock screens with condition-level
explainability and near-miss diagnostics.
"""

from src.screeners.base import Screener, ScreenerMetadata
from src.screeners.registry import (
    clear_registry,
    get_screener,
    get_screener_keys,
    list_screeners,
    register_screener,
)

__all__ = [
    "Screener",
    "ScreenerMetadata",
    "clear_registry",
    "get_screener",
    "get_screener_keys",
    "list_screeners",
    "register_screener",
]
