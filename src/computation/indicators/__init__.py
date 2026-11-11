"""
Technical and Quantitative Indicators Package
==============================================
Provides TechnicalCalculator for computing all price, volume, crossover,
and momentum technical indicators across market datasets.
"""

from src.computation.indicators.calculator import (
    INDICATORS_SCHEMA_VERSION,
    TechnicalCalculator,
)

__all__ = [
    "INDICATORS_SCHEMA_VERSION",
    "TechnicalCalculator",
]
