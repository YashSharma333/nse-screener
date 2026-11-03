"""
Systematic Stock Screeners Package
==================================
Provides extensible, categorized systematic stock screens with condition-level
explainability and near-miss diagnostics.
"""

from __future__ import annotations

import src.screeners.classic
import src.screeners.strategies  # noqa: F401
from src.screeners.base import Screener, ScreenerMetadata
from src.screeners.registry import (
    clear_registry,
    get_screener,
    get_screener_keys,
    list_screeners,
    register_screener,
)
from src.screeners.strategies import (
    Breakout52wVolumeScreener,
    CompositeTrendScreener,
    MinerviniTrendTemplateScreener,
    RsiPullbackUptrendScreener,
    RsLeadersScreener,
    VcpLiteScreener,
)
from src.screeners.swing import TRACKED_INDICES, SwingMomentumScreener, SwingVolumeScreener

__all__ = [
    "TRACKED_INDICES",
    "Breakout52wVolumeScreener",
    "CompositeTrendScreener",
    "MinerviniTrendTemplateScreener",
    "RsLeadersScreener",
    "RsiPullbackUptrendScreener",
    "Screener",
    "ScreenerMetadata",
    "SwingMomentumScreener",
    "SwingVolumeScreener",
    "VcpLiteScreener",
    "clear_registry",
    "get_screener",
    "get_screener_keys",
    "list_screeners",
    "register_screener",
]
