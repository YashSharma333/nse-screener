"""
Technical and Quantitative Indicators Package
==============================================
Plain indicator functions (``sma``, ``rsi``, ...) in ``calculator.py`` and the resolver
(``resolver.py``) that runs them with the user's numbers. The cache saves only the list in
``settings.PRECOMPUTED_INDICATORS``.
"""

from src.computation.indicators.calculator import INDICATORS_SCHEMA_VERSION

__all__ = ["INDICATORS_SCHEMA_VERSION"]
