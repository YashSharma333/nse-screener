"""
Quantitative Stock Screener & Rule-Based Strategies
===================================================
Applies quantitative filters and multi-condition momentum strategies to NSE equity data.
"""

import logging
from typing import Optional, Set
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

TRACKED_INDICES: Set[str] = {
    "NIFTY 100",
    "NIFTY Midcap 150",
    "NIFTY Smallcap 250",
}


class StockScreener:
    """Applies rule-based technical and quantitative screening filters."""

    def __init__(self, df: pd.DataFrame) -> None:
        self.df = df.copy()

    # ------------------------------------------------------------------
    # Capstone Strategy 1: "Liquid 1.5x Vol"
    # ------------------------------------------------------------------
    def strategy_liquid_volume(self) -> pd.DataFrame:
        """Strategy 1: 'Liquid 1.5x Vol'

        Criteria:
        1. Universe: Must belong to tracked NIFTY indices (100, Midcap 150, Smallcap 250).
        2. EMA(Close, 20) > SMA(Close, 50) and SMA(Close, 50) > SMA(Close, 200).
        3. Close > SMA(Close, 50) and Close > SMA(Close, 200).
        4. Close >= 0.85 * (1-day shifted 252-day High).
        5. Volume > 1.5 * SMA(Volume, 20) and SMA(Volume, 20) > 250,000.
        """
        try:
            df = self.df
            mask = pd.Series(True, index=df.index)

            # 1. Universe filter (if index_name column is present)
            if 'index_name' in df.columns:
                valid_index = df['index_name'].isin(TRACKED_INDICES)
                mask &= valid_index

            # 2. Moving average alignment: EMA(20) > SMA(50) > SMA(200)
            if 'ema_20' in df.columns and 'sma_50' in df.columns and 'sma_200' in df.columns:
                mask &= (df['ema_20'] > df['sma_50']) & (df['sma_50'] > df['sma_200'])
            else:
                return pd.DataFrame()

            # 3. Price position: Close > SMA(50) and Close > SMA(200)
            mask &= (df['close'] > df['sma_50']) & (df['close'] > df['sma_200'])

            # 4. Proximity to shifted 252-day high (within 15% of 52w high)
            if 'high_252d_prev' in df.columns:
                high_ref = df['high_252d_prev']
            elif 'high_52w' in df.columns:
                high_ref = df['high_52w'].shift(1)
            elif 'high' in df.columns:
                high_ref = df['high'].rolling(252).max().shift(1)
            else:
                high_ref = df['close']

            mask &= (df['close'] >= 0.85 * high_ref)

            # 5. Volume conditions: Volume > 1.5x 20-day SMA, and 20-day SMA > 250,000
            if 'volume_sma_20' in df.columns:
                vol_sma = df['volume_sma_20']
            elif 'volume' in df.columns:
                vol_sma = df['volume'].rolling(20).mean()
            else:
                return pd.DataFrame()

            mask &= (df['volume'] > 1.5 * vol_sma) & (vol_sma > 250_000)

            return df[mask.fillna(False)]
        except Exception as e:
            logger.error("Error in strategy_liquid_volume: %s", e)
            return pd.DataFrame()

    def liquid_1_5x_volume(self) -> pd.DataFrame:
        """Alias for Strategy 1: Liquid 1.5x Vol."""
        return self.strategy_liquid_volume()

    def strategy_swing_volume(self) -> pd.DataFrame:
        """Alias for Strategy 1: Swing + Volume."""
        return self.strategy_liquid_volume()

    def swing_volume(self) -> pd.DataFrame:
        """Alias for Strategy 1: Swing + Volume."""
        return self.strategy_liquid_volume()

    # ------------------------------------------------------------------
    # Capstone Strategy 2: "Liquid 1.5x Vol Momentum"
    # ------------------------------------------------------------------
    def strategy_liquid_momentum(self) -> pd.DataFrame:
        """Strategy 2: 'Liquid 1.5x Vol Momentum'

        Criteria:
        1. Meets all conditions of Strategy 1 (Liquid 1.5x Vol).
        2. 60-day return <= 40%.
        3. ((Close - Close_252d) / Close_180d) * 100 <= 300.
        4. ((Close - Close_180d) / Close_252d) * 100 <= 300.
        5. ((Close - Close_180d) / Close_252d) * 100 >= 50.
        6. 120-day return >= 30%.
        """
        try:
            # Must satisfy Strategy 1 first
            s1_df = self.strategy_liquid_volume()
            if s1_df.empty:
                return pd.DataFrame()

            df = s1_df
            mask = pd.Series(True, index=df.index)

            # 1. 60-day return <= 40%
            if 'return_60d' in df.columns:
                ret_60 = df['return_60d']
            elif 'close_60d' in df.columns:
                ret_60 = ((df['close'] - df['close_60d']) / df['close_60d'].replace(0, np.nan)) * 100.0
            else:
                shifted_60 = df['close'].shift(60)
                ret_60 = ((df['close'] - shifted_60) / shifted_60.replace(0, np.nan)) * 100.0

            mask &= (ret_60 <= 40.0)

            # Shift references: Close_180d and Close_252d
            c_180 = df['close_180d'] if 'close_180d' in df.columns else df['close'].shift(180)
            c_252 = df['close_252d'] if 'close_252d' in df.columns else df['close'].shift(252)

            denom_180 = c_180.replace(0, np.nan)
            denom_252 = c_252.replace(0, np.nan)

            # 2. ((Close - Close_252d) / Close_180d) * 100 <= 300
            ratio_252_180 = ((df['close'] - c_252) / denom_180) * 100.0
            mask &= (ratio_252_180 <= 300.0)

            # 3. ((Close - Close_180d) / Close_252d) * 100 <= 300
            ratio_180_252 = ((df['close'] - c_180) / denom_252) * 100.0
            mask &= (ratio_180_252 <= 300.0)

            # 4. ((Close - Close_180d) / Close_252d) * 100 >= 50
            mask &= (ratio_180_252 >= 50.0)

            # 5. 120-day return >= 30%
            if 'return_120d' in df.columns:
                ret_120 = df['return_120d']
            elif 'close_120d' in df.columns:
                ret_120 = ((df['close'] - df['close_120d']) / df['close_120d'].replace(0, np.nan)) * 100.0
            else:
                shifted_120 = df['close'].shift(120)
                ret_120 = ((df['close'] - shifted_120) / shifted_120.replace(0, np.nan)) * 100.0

            mask &= (ret_120 >= 30.0)

            return df[mask.fillna(False)]
        except Exception as e:
            logger.error("Error in strategy_liquid_momentum: %s", e)
            return pd.DataFrame()

    def liquid_1_5x_volume_momentum(self) -> pd.DataFrame:
        """Alias for Strategy 2: Liquid 1.5x Vol Momentum."""
        return self.strategy_liquid_momentum()

    def strategy_swing_momentum(self) -> pd.DataFrame:
        """Alias for Strategy 2: Swing With Momentum."""
        return self.strategy_liquid_momentum()

    def swing_momentum(self) -> pd.DataFrame:
        """Alias for Strategy 2: Swing With Momentum."""
        return self.strategy_liquid_momentum()

    # ------------------------------------------------------------------
    # Standard Technical Filters
    # ------------------------------------------------------------------
    def golden_cross(self) -> pd.DataFrame:
        """Stocks where SMA 50 crossed above SMA 200."""
        try:
            mask = (self.df['sma_50'] > self.df['sma_200']) & (
                self.df['sma_50'].shift(1) <= self.df['sma_200'].shift(1)
            )
            return self.df[mask.fillna(False)]
        except Exception as e:
            logger.error("Error in golden_cross: %s", e)
            return pd.DataFrame()

    def death_cross(self) -> pd.DataFrame:
        """Stocks where SMA 50 crossed below SMA 200."""
        try:
            mask = (self.df['sma_50'] < self.df['sma_200']) & (
                self.df['sma_50'].shift(1) >= self.df['sma_200'].shift(1)
            )
            return self.df[mask.fillna(False)]
        except Exception as e:
            logger.error("Error in death_cross: %s", e)
            return pd.DataFrame()

    def near_52_week_high(self, threshold_pct: float = 5.0) -> pd.DataFrame:
        """Stocks within threshold_pct% of their 52-week high."""
        try:
            pct_from_high = ((self.df['high_52w'] - self.df['close']) / self.df['high_52w']) * 100
            return self.df[pct_from_high <= threshold_pct]
        except Exception as e:
            logger.error("Error in near_52_week_high: %s", e)
            return pd.DataFrame()

    def near_52_week_low(self, threshold_pct: float = 5.0) -> pd.DataFrame:
        """Stocks within threshold_pct% of their 52-week low."""
        try:
            pct_from_low = ((self.df['close'] - self.df['low_52w']) / self.df['low_52w']) * 100
            return self.df[pct_from_low <= threshold_pct]
        except Exception as e:
            logger.error("Error in near_52_week_low: %s", e)
            return pd.DataFrame()

    def rsi_oversold(self, threshold: float = 30.0) -> pd.DataFrame:
        """Stocks with RSI below oversold threshold."""
        try:
            return self.df[self.df['rsi_14'] < threshold]
        except Exception as e:
            logger.error("Error in rsi_oversold: %s", e)
            return pd.DataFrame()

    def rsi_overbought(self, threshold: float = 70.0) -> pd.DataFrame:
        """Stocks with RSI above overbought threshold."""
        try:
            return self.df[self.df['rsi_14'] > threshold]
        except Exception as e:
            logger.error("Error in rsi_overbought: %s", e)
            return pd.DataFrame()

    def bullish_macd_crossover(self) -> pd.DataFrame:
        """MACD line crossed above signal line."""
        try:
            mask = (self.df['macd_line'] > self.df['macd_signal']) & (
                self.df['macd_line'].shift(1) <= self.df['macd_signal'].shift(1)
            )
            return self.df[mask.fillna(False)]
        except Exception as e:
            logger.error("Error in bullish_macd_crossover: %s", e)
            return pd.DataFrame()

    def high_volatility(self, threshold: float = 0.03) -> pd.DataFrame:
        """Stocks with daily volatility above threshold."""
        try:
            return self.df[self.df['volatility_20'] > threshold]
        except Exception as e:
            logger.error("Error in high_volatility: %s", e)
            return pd.DataFrame()

    def bollinger_squeeze(self) -> pd.DataFrame:
        """Stocks where price is near the lower Bollinger Band."""
        try:
            band_width = self.df['bb_upper'] - self.df['bb_lower']
            position = (self.df['close'] - self.df['bb_lower']) / band_width
            return self.df[position < 0.1]
        except Exception as e:
            logger.error("Error in bollinger_squeeze: %s", e)
            return pd.DataFrame()
