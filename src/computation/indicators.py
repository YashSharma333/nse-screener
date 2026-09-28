import logging
import pandas as pd
from typing import Dict, Any

logger = logging.getLogger(__name__)

class TechnicalCalculator:
    """Generates technical indicators from historical OHLCV data."""
    
    @staticmethod
    def calculate_sma(df: pd.DataFrame, window: int, column: str = 'close') -> pd.Series:
        """Calculates Simple Moving Average."""
        try:
            return df[column].rolling(window=window).mean()
        except Exception as e:
            logger.error(f"SMA calculation failed: {e}")
            raise

    @staticmethod
    def calculate_52_week_high(df: pd.DataFrame, column: str = 'high') -> pd.Series:
        """Determines the rolling 52-week high."""
        try:
            return df[column].rolling(window=252, min_periods=1).max()
        except Exception as e:
            logger.error(f"52-week high calculation failed: {e}")
            raise

    @classmethod
    def apply_all_indicators(cls, df: pd.DataFrame) -> pd.DataFrame:
        """Applies all standard screener indicators to the dataset."""
        df['sma_50'] = cls.calculate_sma(df, window=50)
        df['sma_200'] = cls.calculate_sma(df, window=200)
        df['high_52w'] = cls.calculate_52_week_high(df)
        return df