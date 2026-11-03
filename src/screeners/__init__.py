"""
Systematic Stock Screeners Package
==================================
Provides extensible, categorized systematic stock screens with condition-level
explainability and near-miss diagnostics.
"""

from __future__ import annotations

import src.screeners.classic  # noqa: F401
from src.screeners.base import Screener, ScreenerMetadata
from src.screeners.registry import (
    clear_registry,
    get_screener,
    get_screener_keys,
    list_screeners,
    register_screener,
)
from src.screeners.swing import TRACKED_INDICES, SwingMomentumScreener, SwingVolumeScreener

__all__ = [
    "TRACKED_INDICES",
    "Screener",
    "ScreenerMetadata",
    "SwingMomentumScreener",
    "SwingVolumeScreener",
    "clear_registry",
    "get_screener",
    "get_screener_keys",
    "list_screeners",
    "register_screener",
]
