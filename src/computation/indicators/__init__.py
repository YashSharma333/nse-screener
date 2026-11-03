"""
Technical and Quantitative Indicators Package
==============================================
Divided logically by calculation domain:
  - `price`: Moving averages, Bollinger Bands, ATR, Donchian Channels, 52W High/Low.
  - `volume`: Volume moving averages, Volume Ratio, Volume Dryup.
  - `crossovers`: Golden Cross, Death Cross, MACD crossovers, recent crossovers.
  - `momentum`: RSI, MACD, Relative Performance vs Benchmark, Cross-Sectional Ranks, HMM.
"""

from src.computation.indicators import crossovers, momentum, price, volume
from src.computation.indicators.calculator import (
    INDICATORS_SCHEMA_VERSION,
    TechnicalCalculator,
)

__all__ = [
    "INDICATORS_SCHEMA_VERSION",
    "TechnicalCalculator",
    "crossovers",
    "momentum",
    "price",
    "volume",
]
