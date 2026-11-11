"""Systematic Stock Screeners Package
==================================
Provides extensible, categorized systematic stock screens with condition-level
explainability and near-miss diagnostics.
"""

from __future__ import annotations

from src.screeners.base import BaseScreener, MissingIndicatorError, Screener
from src.screeners.catalog import (
    TRACKED_INDICES,
    Above50SmaScreener,
    Above200SmaScreener,
    BollingerSqueezeScreener,
    Breakout52wParams,
    Breakout52wVolumeScreener,
    BullishMacdCrossoverScreener,
    BullishMacdCrossScreener,
    CompositeTrendParams,
    CompositeTrendScreener,
    DeathCrossScreener,
    GoldenCrossScreener,
    HighVolatilityScreener,
    MinerviniParams,
    MinerviniTrendTemplateScreener,
    Near52WeekHighScreener,
    Near52WeekLowScreener,
    RsiMomentumParams,
    RsiMomentumScreener,
    RsiOverboughtScreener,
    RsiOversoldScreener,
    RsiPullbackParams,
    RsiPullbackUptrendScreener,
    RsLeadersParams,
    RsLeadersScreener,
    SwingMomentumParams,
    SwingMomentumScreener,
    SwingVolumeParams,
    SwingVolumeScreener,
    VcpLiteParams,
    VcpLiteScreener,
)
from src.screeners.declarative import (
    create_declarative_screener,
    load_custom_screeners,
)
from src.screeners.registry import (
    clear_registry,
    get_screener,
    get_screener_keys,
    list_screeners,
    register_screener,
)

__all__ = [
    "TRACKED_INDICES",
    "Above50SmaScreener",
    "Above200SmaScreener",
    "BaseScreener",
    "BollingerSqueezeScreener",
    "Breakout52wParams",
    "Breakout52wVolumeScreener",
    "BullishMacdCrossScreener",
    "BullishMacdCrossoverScreener",
    "CompositeTrendParams",
    "CompositeTrendScreener",
    "DeathCrossScreener",
    "GoldenCrossScreener",
    "HighVolatilityScreener",
    "MinerviniParams",
    "MinerviniTrendTemplateScreener",
    "MissingIndicatorError",
    "Near52WeekHighScreener",
    "Near52WeekLowScreener",
    "RsLeadersParams",
    "RsLeadersScreener",
    "RsiMomentumParams",
    "RsiMomentumScreener",
    "RsiOverboughtScreener",
    "RsiOversoldScreener",
    "RsiPullbackParams",
    "RsiPullbackUptrendScreener",
    "Screener",
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
