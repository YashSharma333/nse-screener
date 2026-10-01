import logging
import pandas as pd

logger = logging.getLogger(__name__)


class StockScreener:
    """Applies rule-based filters to identify stocks matching technical criteria."""

    def __init__(self, df: pd.DataFrame) -> None:
        self.df = df.copy()

    def golden_cross(self) -> pd.DataFrame:
        """Stocks where SMA 50 just crossed above SMA 200 (bullish signal)."""
        try:
            # sma_50 > sma_200 AND shifted sma_50 <= shifted sma_200
            mask = (self.df['sma_50'] > self.df['sma_200']) & (self.df['sma_50'].shift(1) <= self.df['sma_200'].shift(1))
            return self.df[mask]
        except Exception as e:
            logger.error(f"Error in golden_cross: {e}")
            return pd.DataFrame()

    def death_cross(self) -> pd.DataFrame:
        """Stocks where SMA 50 just crossed below SMA 200 (bearish signal)."""
        try:
            mask = (self.df['sma_50'] < self.df['sma_200']) & (self.df['sma_50'].shift(1) >= self.df['sma_200'].shift(1))
            return self.df[mask]
        except Exception as e:
            logger.error(f"Error in death_cross: {e}")
            return pd.DataFrame()

    def near_52_week_high(self, threshold_pct: float = 5.0) -> pd.DataFrame:
        """Stocks within threshold_pct% of their 52-week high."""
        try:
            pct_from_high = ((self.df['high_52w'] - self.df['close']) / self.df['high_52w']) * 100
            return self.df[pct_from_high <= threshold_pct]
        except Exception as e:
            logger.error(f"Error in near_52_week_high: {e}")
            return pd.DataFrame()

    def near_52_week_low(self, threshold_pct: float = 5.0) -> pd.DataFrame:
        """Stocks within threshold_pct% of their 52-week low."""
        try:
            pct_from_low = ((self.df['close'] - self.df['low_52w']) / self.df['low_52w']) * 100
            return self.df[pct_from_low <= threshold_pct]
        except Exception as e:
            logger.error(f"Error in near_52_week_low: {e}")
            return pd.DataFrame()

    def rsi_oversold(self, threshold: float = 30.0) -> pd.DataFrame:
        """Stocks with RSI below oversold threshold."""
        try:
            return self.df[self.df['rsi_14'] < threshold]
        except Exception as e:
            logger.error(f"Error in rsi_oversold: {e}")
            return pd.DataFrame()

    def rsi_overbought(self, threshold: float = 70.0) -> pd.DataFrame:
        """Stocks with RSI above overbought threshold."""
        try:
            return self.df[self.df['rsi_14'] > threshold]
        except Exception as e:
            logger.error(f"Error in rsi_overbought: {e}")
            return pd.DataFrame()

    def bullish_macd_crossover(self) -> pd.DataFrame:
        """MACD line crossed above signal line."""
        try:
            mask = (self.df['macd_line'] > self.df['macd_signal']) & (
                self.df['macd_line'].shift(1) <= self.df['macd_signal'].shift(1)
            )
            return self.df[mask]
        except Exception as e:
            logger.error(f"Error in bullish_macd_crossover: {e}")
            return pd.DataFrame()

    def high_volatility(self, threshold: float = 0.03) -> pd.DataFrame:
        """Stocks with daily volatility above threshold."""
        try:
            return self.df[self.df['volatility_20'] > threshold]
        except Exception as e:
            logger.error(f"Error in high_volatility: {e}")
            return pd.DataFrame()

    def bollinger_squeeze(self) -> pd.DataFrame:
        """Stocks where price is near the lower Bollinger Band (potential bounce)."""
        try:
            band_width = self.df['bb_upper'] - self.df['bb_lower']
            position = (self.df['close'] - self.df['bb_lower']) / band_width
            return self.df[position < 0.1]  # price in bottom 10% of band
        except Exception as e:
            logger.error(f"Error in bollinger_squeeze: {e}")
            return pd.DataFrame()
