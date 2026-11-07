"""Systematic Stock Screeners Package
==================================
Provides extensible, categorized systematic stock screens with condition-level
explainability and near-miss diagnostics.
"""

from __future__ import annotations

import src.screeners.classic  # noqa: F401
from src.screeners.base import MissingIndicatorError, Screener, ScreenerMetadata
from src.screeners.breakout_52w import Breakout52wParams, Breakout52wVolumeScreener
from src.screeners.composite_trend import CompositeTrendParams, CompositeTrendScreener
from src.screeners.declarative import (
    create_declarative_screener,
    load_custom_screeners,
)
from src.screeners.minervini import MinerviniParams, MinerviniTrendTemplateScreener
from src.screeners.registry import (
    clear_registry,
    get_screener,
    get_screener_keys,
    list_screeners,
    register_screener,
)
from src.screeners.rs_leaders import RsLeadersParams, RsLeadersScreener
from src.screeners.rsi_momentum import RsiMomentumParams, RsiMomentumScreener
from src.screeners.rsi_pullback import RsiPullbackParams, RsiPullbackUptrendScreener
from src.screeners.swing_momentum import SwingMomentumParams, SwingMomentumScreener
from src.screeners.swing_volume import TRACKED_INDICES, SwingVolumeParams, SwingVolumeScreener
from src.screeners.vcp_lite import VcpLiteParams, VcpLiteScreener

__all__ = [
    "TRACKED_INDICES",
    "Breakout52wParams",
    "Breakout52wVolumeScreener",
    "CompositeTrendParams",
    "CompositeTrendScreener",
    "MinerviniParams",
    "MinerviniTrendTemplateScreener",
    "MissingIndicatorError",
    "RsLeadersParams",
    "RsLeadersScreener",
    "RsiMomentumParams",
    "RsiMomentumScreener",
    "RsiPullbackParams",
    "RsiPullbackUptrendScreener",
    "Screener",
    "ScreenerMetadata",
    "SwingMomentumParams",
    "SwingMomentumScreener",
    "SwingVolumeParams",
    "SwingVolumeScreener",
    "VcpLiteParams",
    "VcpLiteScreener",
    "clear_registry",
    "create_declarative_screener",
    "get_screener",
    "get_screener_keys",
    "list_screeners",
    "load_custom_screeners",
    "register_screener",
]

# Auto-discover declarative JSON screeners
load_custom_screeners()
