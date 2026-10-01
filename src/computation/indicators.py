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

    @staticmethod
    def calculate_pct_change(df: pd.DataFrame, periods: int = 1, column: str = 'close') -> pd.Series:
        """Calculate percentage change over a given period lookback."""
        try:
            return df[column].pct_change(periods=periods) * 100.0
        except Exception as e:
            logger.error(f"Error calculating pct_change (periods={periods}): {e}")
            return pd.Series(index=df.index, dtype=float)

    @staticmethod
    def calculate_shifted_price(df: pd.DataFrame, shift_days: int, column: str = 'close') -> pd.Series:
        """Calculate historical shifted price reference."""
        try:
            return df[column].shift(shift_days)
        except Exception as e:
            logger.error(f"Error calculating shifted price ({shift_days} days): {e}")
            return pd.Series(index=df.index, dtype=float)

    @staticmethod
    def calculate_shifted_rolling_high(
        df: pd.DataFrame, window: int = 252, shift_days: int = 1, column: str = 'high'
    ) -> pd.Series:
        """Calculate rolling window high shifted by shift_days (default: 252-day high shifted 1 day)."""
        try:
            return df[column].rolling(window=window).max().shift(shift_days)
        except Exception as e:
            logger.error(f"Error calculating shifted rolling high: {e}")
            return pd.Series(index=df.index, dtype=float)

    @classmethod
    def _apply_indicators_to_series(cls, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate all indicators for a single stock time-series sorted by date."""
        df = df.copy()

        # Moving averages
        df['sma_50'] = cls.calculate_sma(df, window=50)
        df['sma_200'] = cls.calculate_sma(df, window=200)
        df['ema_12'] = cls.calculate_ema(df, window=12)
        df['ema_20'] = cls.calculate_ema(df, window=20)
        df['ema_26'] = cls.calculate_ema(df, window=26)

        # Volume indicators
        if 'volume' in df.columns:
            df['volume_sma_20'] = cls.calculate_sma(df, window=20, column='volume')

        # Momentum & Range
        df['rsi_14'] = cls.calculate_rsi(df, periods=14)
        df['high_52w'] = cls.calculate_52_week_high(df)
        df['low_52w'] = cls.calculate_52_week_low(df)
        df['high_252d_prev'] = cls.calculate_shifted_rolling_high(df, window=252, shift_days=1)

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

        # Lookback percentage changes: 1D, 1M (21d), 3M (63d), 6M (126d), 12M (252d)
        df['pct_change_daily'] = cls.calculate_pct_change(df, periods=1)
        df['pct_change_1m'] = cls.calculate_pct_change(df, periods=21)
        df['pct_change_3m'] = cls.calculate_pct_change(df, periods=63)
        df['pct_change_6m'] = cls.calculate_pct_change(df, periods=126)
        df['pct_change_12m'] = cls.calculate_pct_change(df, periods=252)

        # Historical shift references: 60d, 120d, 180d, 252d
        df['close_60d'] = cls.calculate_shifted_price(df, shift_days=60)
        df['close_120d'] = cls.calculate_shifted_price(df, shift_days=120)
        df['close_180d'] = cls.calculate_shifted_price(df, shift_days=180)
        df['close_252d'] = cls.calculate_shifted_price(df, shift_days=252)

        # Historical return metrics for momentum strategies
        df['return_60d'] = ((df['close'] - df['close_60d']) / df['close_60d']) * 100.0
        df['return_120d'] = ((df['close'] - df['close_120d']) / df['close_120d']) * 100.0

        return df

    @classmethod
    def apply_all_indicators(cls, df: pd.DataFrame) -> pd.DataFrame:
        """Apply all technical and quantitative indicators to a dataframe.

        If a 'symbol' column is present with multiple stocks, computes per stock.
        """
        if df.empty:
            return df.copy()

        if 'symbol' in df.columns and df['symbol'].nunique() > 1:
            sorted_df = df.sort_values(by=['symbol', 'date']) if 'date' in df.columns else df

            def _apply_with_symbol(grp):
                # grp has no 'symbol' column (include_groups=False drops groupby key).
                # Re-attach it from the group's position in the original frame.
                result = cls._apply_indicators_to_series(grp)
                # Restore the symbol value by looking it up from the original sorted_df
                sym_values = sorted_df.loc[grp.index, 'symbol']
                result['symbol'] = sym_values.values
                return result

            return (
                sorted_df.groupby('symbol', group_keys=False)
                .apply(_apply_with_symbol, include_groups=False)
            )

        if 'date' in df.columns:
            sorted_df = df.sort_values(by='date')
            return cls._apply_indicators_to_series(sorted_df)

        return cls._apply_indicators_to_series(df)