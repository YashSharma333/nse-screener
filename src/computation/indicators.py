import logging
from typing import Dict
import pandas as pd

logger = logging.getLogger(__name__)


class TechnicalCalculator:
    """Class to calculate technical indicators for stock data."""

    @staticmethod
    def calculate_sma(df: pd.DataFrame, window: int, column: str = 'close') -> pd.Series:
        """Calculate Simple Moving Average."""
        try:
            return df[column].rolling(window=window).mean()
        except Exception as e:
            logger.error(f"Error calculating SMA: {e}")
            return pd.Series(index=df.index, dtype=float)

    @staticmethod
    def calculate_52_week_high(df: pd.DataFrame, column: str = 'high') -> pd.Series:
        """Calculate 52-week rolling high (assuming 252 trading days)."""
        try:
            return df[column].rolling(window=252).max()
        except Exception as e:
            logger.error(f"Error calculating 52-week high: {e}")
            return pd.Series(index=df.index, dtype=float)

    @staticmethod
    def calculate_ema(df: pd.DataFrame, window: int, column: str = 'close') -> pd.Series:
        """Calculate Exponential Moving Average."""
        try:
            return df[column].ewm(span=window, adjust=False).mean()
        except Exception as e:
            logger.error(f"Error calculating EMA: {e}")
            return pd.Series(index=df.index, dtype=float)

    @staticmethod
    def calculate_rsi(df: pd.DataFrame, periods: int = 14, column: str = 'close') -> pd.Series:
        """Calculate Relative Strength Index."""
        try:
            delta = df[column].diff()
            gain = delta.clip(lower=0).rolling(window=periods).mean()
            loss = (-delta.clip(upper=0)).rolling(window=periods).mean()
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))
            return rsi
        except Exception as e:
            logger.error(f"Error calculating RSI: {e}")
            return pd.Series(index=df.index, dtype=float)

    @staticmethod
    def calculate_52_week_low(df: pd.DataFrame, column: str = 'low') -> pd.Series:
        """Calculate 52-week rolling low (assuming 252 trading days)."""
        try:
            return df[column].rolling(window=252).min()
        except Exception as e:
            logger.error(f"Error calculating 52-week low: {e}")
            return pd.Series(index=df.index, dtype=float)

    @staticmethod
    def calculate_bollinger_bands(
        df: pd.DataFrame, window: int = 20, num_std: int = 2, column: str = 'close'
    ) -> Dict[str, pd.Series]:
        """Calculate Bollinger Bands."""
        try:
            middle = df[column].rolling(window=window).mean()
            std = df[column].rolling(window=window).std()
            upper = middle + (std * num_std)
            lower = middle - (std * num_std)
            return {'upper': upper, 'middle': middle, 'lower': lower}
        except Exception as e:
            logger.error(f"Error calculating Bollinger Bands: {e}")
            empty_series = pd.Series(index=df.index, dtype=float)
            return {'upper': empty_series, 'middle': empty_series, 'lower': empty_series}

    @staticmethod
    def calculate_macd(
        df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9, column: str = 'close'
    ) -> Dict[str, pd.Series]:
        """Calculate MACD."""
        try:
            fast_ema = df[column].ewm(span=fast, adjust=False).mean()
            slow_ema = df[column].ewm(span=slow, adjust=False).mean()
            macd_line = fast_ema - slow_ema
            signal_line = macd_line.ewm(span=signal, adjust=False).mean()
            histogram = macd_line - signal_line
            return {'macd_line': macd_line, 'signal_line': signal_line, 'histogram': histogram}
        except Exception as e:
            logger.error(f"Error calculating MACD: {e}")
            empty_series = pd.Series(index=df.index, dtype=float)
            return {'macd_line': empty_series, 'signal_line': empty_series, 'histogram': empty_series}

    @staticmethod
    def calculate_daily_returns(df: pd.DataFrame, column: str = 'close') -> pd.Series:
        """Calculate daily returns."""
        try:
            return df[column].pct_change()
        except Exception as e:
            logger.error(f"Error calculating daily returns: {e}")
            return pd.Series(index=df.index, dtype=float)

    @staticmethod
    def calculate_volatility(df: pd.DataFrame, window: int = 20, column: str = 'close') -> pd.Series:
        """Calculate rolling volatility (std of daily returns)."""
        try:
            returns = df[column].pct_change()
            return returns.rolling(window=window).std()
        except Exception as e:
            logger.error(f"Error calculating volatility: {e}")
            return pd.Series(index=df.index, dtype=float)

    @classmethod
    def apply_all_indicators(cls, df: pd.DataFrame) -> pd.DataFrame:
        """Apply all standard indicators to a dataframe."""
        df = df.copy()
        
        # SMAs
        df['sma_50'] = cls.calculate_sma(df, window=50)
        df['sma_200'] = cls.calculate_sma(df, window=200)
        
        # EMAs
        df['ema_12'] = cls.calculate_ema(df, window=12)
        df['ema_26'] = cls.calculate_ema(df, window=26)
        
        # RSI
        df['rsi_14'] = cls.calculate_rsi(df, periods=14)
        
        # 52-week High/Low
        df['high_52w'] = cls.calculate_52_week_high(df)
        df['low_52w'] = cls.calculate_52_week_low(df)
        
        # Bollinger Bands
        bb = cls.calculate_bollinger_bands(df, window=20)
        df['bb_upper'] = bb['upper']
        df['bb_middle'] = bb['middle']
        df['bb_lower'] = bb['lower']
        
        # MACD
        macd = cls.calculate_macd(df)
        df['macd_line'] = macd['macd_line']
        df['macd_signal'] = macd['signal_line']
        df['macd_histogram'] = macd['histogram']
        
        # Returns & Volatility
        df['daily_returns'] = cls.calculate_daily_returns(df)
        df['volatility_20'] = cls.calculate_volatility(df, window=20)
        
        return df