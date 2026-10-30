"""
Technical and Quantitative Indicators Computation Layer
=======================================================
Implements vectorised indicators calculated per stock time-series:
  - Moving Averages: SMA, EMA
  - Wilder RSI (14) & RSI (9)
  - Wilder ATR (14) and atr_pct
  - Donchian channels (20-day and 55-day, shifted 1 day)
  - MACD, Bollinger Bands, Volatility
  - Time-series crossovers: golden_cross, death_cross, macd_cross_up, macd_cross_down
  - Relative strength vs benchmark (rs_63d, rs_126d, rs_252d)
  - Custom quantitative indicator: HMM
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class TechnicalCalculator:
    """Class to calculate technical indicators for stock data."""

    @staticmethod
    def calculate_sma(df: pd.DataFrame, window: int, column: str = "close") -> pd.Series:
        """Calculate Simple Moving Average."""
        try:
            return df[column].rolling(window=window).mean()
        except Exception as e:
            logger.error("Error calculating SMA: %s", e)
            return pd.Series(index=df.index, dtype=float)

    @staticmethod
    def calculate_wma(df: pd.DataFrame, window: int, column: str = "close") -> pd.Series:
        """Calculate Weighted Moving Average (linearly weighted)."""
        try:
            weights = np.arange(1, window + 1)

            def _wma(x):
                return np.dot(x, weights) / weights.sum()

            return df[column].rolling(window=window).apply(_wma, raw=True)
        except Exception as e:
            logger.error("Error calculating WMA: %s", e)
            return pd.Series(index=df.index, dtype=float)

    @staticmethod
    def calculate_52_week_high(df: pd.DataFrame, column: str = "high") -> pd.Series:
        """Calculate 52-week rolling high (assuming 252 trading days)."""
        try:
            return df[column].rolling(window=252).max()
        except Exception as e:
            logger.error("Error calculating 52-week high: %s", e)
            return pd.Series(index=df.index, dtype=float)

    @staticmethod
    def calculate_52_week_low(df: pd.DataFrame, column: str = "low") -> pd.Series:
        """Calculate 52-week rolling low (assuming 252 trading days)."""
        try:
            return df[column].rolling(window=252).min()
        except Exception as e:
            logger.error("Error calculating 52-week low: %s", e)
            return pd.Series(index=df.index, dtype=float)

    @staticmethod
    def calculate_ema(df: pd.DataFrame, window: int, column: str = "close") -> pd.Series:
        """Calculate Exponential Moving Average."""
        try:
            return df[column].ewm(span=window, adjust=False).mean()
        except Exception as e:
            logger.error("Error calculating EMA: %s", e)
            return pd.Series(index=df.index, dtype=float)

    @staticmethod
    def calculate_rsi(df: pd.DataFrame, periods: int = 14, column: str = "close") -> pd.Series:
        """Calculate Relative Strength Index with Wilder smoothing.

        Uses ewm(alpha=1/periods, adjust=False, min_periods=periods).
        Preserves standard boundary conditions: 100 on all gains, 0 on all losses.
        """
        try:
            delta = df[column].diff()
            gain = delta.clip(lower=0)
            loss = -delta.clip(upper=0)

            # Wilder smoothing
            avg_gain = gain.ewm(alpha=1.0 / periods, adjust=False, min_periods=periods).mean()
            avg_loss = loss.ewm(alpha=1.0 / periods, adjust=False, min_periods=periods).mean()

            # Handle edge cases (all gains -> avg_loss == 0; all losses -> avg_gain == 0)
            rs = avg_gain / avg_loss
            rsi = 100.0 - (100.0 / (1.0 + rs))

            # When loss is 0 and gain > 0, RSI is 100
            rsi = rsi.where(~((avg_loss == 0) & (avg_gain > 0)), 100.0)
            # When gain is 0 and loss > 0, RSI is 0
            rsi = rsi.where(~((avg_gain == 0) & (avg_loss > 0)), 0.0)
            # When both are 0, RSI is 50
            rsi = rsi.where(~((avg_gain == 0) & (avg_loss == 0)), 50.0)

            return rsi
        except Exception as e:
            logger.error("Error calculating RSI: %s", e)
            return pd.Series(index=df.index, dtype=float)

    @staticmethod
    def calculate_atr(df: pd.DataFrame, periods: int = 14) -> tuple[pd.Series, pd.Series]:
        """Calculate Average True Range (ATR 14) using Wilder smoothing.

        True Range = max(high - low, abs(high - prev_close), abs(low - prev_close))
        Returns: (atr_14, atr_pct where atr_pct = atr_14 / close)
        """
        try:
            high = df["high"]
            low = df["low"]
            prev_close = df["close"].shift(1)

            tr1 = high - low
            tr2 = (high - prev_close).abs()
            tr3 = (low - prev_close).abs()

            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr_14 = tr.ewm(alpha=1.0 / periods, adjust=False, min_periods=periods).mean()
            atr_pct = (atr_14 / df["close"]) * 100.0
            return atr_14, atr_pct
        except Exception as e:
            logger.error("Error calculating ATR: %s", e)
            return pd.Series(index=df.index, dtype=float), pd.Series(index=df.index, dtype=float)

    @staticmethod
    def calculate_donchian_channels(df: pd.DataFrame) -> dict[str, pd.Series]:
        """Calculate Donchian channels (20-day and 55-day), shifted by 1 day."""
        try:
            return {
                "donchian_high_20": df["high"].rolling(window=20).max().shift(1),
                "donchian_low_20": df["low"].rolling(window=20).min().shift(1),
                "donchian_high_55": df["high"].rolling(window=55).max().shift(1),
                "donchian_low_55": df["low"].rolling(window=55).min().shift(1),
            }
        except Exception as e:
            logger.error("Error calculating Donchian channels: %s", e)
            empty = pd.Series(index=df.index, dtype=float)
            return {
                "donchian_high_20": empty,
                "donchian_low_20": empty,
                "donchian_high_55": empty,
                "donchian_low_55": empty,
            }

    @staticmethod
    def calculate_bollinger_bands(
        df: pd.DataFrame, window: int = 20, num_std: int = 2, column: str = "close"
    ) -> dict[str, pd.Series]:
        """Calculate Bollinger Bands."""
        try:
            middle = df[column].rolling(window=window).mean()
            std = df[column].rolling(window=window).std()
            upper = middle + (std * num_std)
            lower = middle - (std * num_std)
            return {"upper": upper, "middle": middle, "lower": lower}
        except Exception as e:
            logger.error("Error calculating Bollinger Bands: %s", e)
            empty_series = pd.Series(index=df.index, dtype=float)
            return {"upper": empty_series, "middle": empty_series, "lower": empty_series}

    @staticmethod
    def calculate_macd(
        df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9, column: str = "close"
    ) -> dict[str, pd.Series]:
        """Calculate MACD."""
        try:
            fast_ema = df[column].ewm(span=fast, adjust=False).mean()
            slow_ema = df[column].ewm(span=slow, adjust=False).mean()
            macd_line = fast_ema - slow_ema
            signal_line = macd_line.ewm(span=signal, adjust=False).mean()
            histogram = macd_line - signal_line
            return {"macd_line": macd_line, "signal_line": signal_line, "histogram": histogram}
        except Exception as e:
            logger.error("Error calculating MACD: %s", e)
            empty_series = pd.Series(index=df.index, dtype=float)
            return {
                "macd_line": empty_series,
                "signal_line": empty_series,
                "histogram": empty_series,
            }

    @staticmethod
    def calculate_daily_returns(df: pd.DataFrame, column: str = "close") -> pd.Series:
        """Calculate daily returns."""
        try:
            return df[column].pct_change()
        except Exception as e:
            logger.error("Error calculating daily returns: %s", e)
            return pd.Series(index=df.index, dtype=float)

    @staticmethod
    def calculate_volatility(
        df: pd.DataFrame, window: int = 20, column: str = "close"
    ) -> pd.Series:
        """Calculate rolling volatility (std of daily returns)."""
        try:
            returns = df[column].pct_change()
            return returns.rolling(window=window).std()
        except Exception as e:
            logger.error("Error calculating volatility: %s", e)
            return pd.Series(index=df.index, dtype=float)

    @staticmethod
    def calculate_pct_change(
        df: pd.DataFrame, periods: int = 1, column: str = "close"
    ) -> pd.Series:
        """Calculate percentage change over a given period lookback."""
        try:
            return df[column].pct_change(periods=periods) * 100.0
        except Exception as e:
            logger.error("Error calculating pct_change (periods=%d): %s", periods, e)
            return pd.Series(index=df.index, dtype=float)

    @staticmethod
    def calculate_shifted_price(
        df: pd.DataFrame, shift_days: int, column: str = "close"
    ) -> pd.Series:
        """Calculate historical shifted price reference."""
        try:
            return df[column].shift(shift_days)
        except Exception as e:
            logger.error("Error calculating shifted price (%d days): %s", shift_days, e)
            return pd.Series(index=df.index, dtype=float)

    @staticmethod
    def calculate_shifted_rolling_high(
        df: pd.DataFrame, window: int = 252, shift_days: int = 1, column: str = "high"
    ) -> pd.Series:
        """Calculate rolling window high shifted by shift_days (e.g. 252d high shifted 1d)."""
        try:
            return df[column].rolling(window=window).max().shift(shift_days)
        except Exception as e:
            logger.error("Error calculating shifted rolling high: %s", e)
            return pd.Series(index=df.index, dtype=float)

    @classmethod
    def _apply_indicators_to_series(
        cls, df: pd.DataFrame, benchmark_df: pd.DataFrame | None = None
    ) -> pd.DataFrame:
        """Calculate all indicators for a single stock time-series sorted by date."""
        df = df.copy()

        # Moving averages
        df["sma_50"] = cls.calculate_sma(df, window=50)
        df["sma_200"] = cls.calculate_sma(df, window=200)
        df["ema_12"] = cls.calculate_ema(df, window=12)
        df["ema_20"] = cls.calculate_ema(df, window=20)
        df["ema_26"] = cls.calculate_ema(df, window=26)

        # Volume indicators
        if "volume" in df.columns:
            df["volume_sma_20"] = cls.calculate_sma(df, window=20, column="volume")
            vol_sma_nonzero = df["volume_sma_20"].replace(0, np.nan)
            df["volume_ratio"] = df["volume"] / vol_sma_nonzero

        # Momentum & Range
        df["rsi_14"] = cls.calculate_rsi(df, periods=14)
        df["rsi_9"] = cls.calculate_rsi(df, periods=9)

        df["high_52w"] = cls.calculate_52_week_high(df)
        df["low_52w"] = cls.calculate_52_week_low(df)
        df["high_252d_prev"] = cls.calculate_shifted_rolling_high(df, window=252, shift_days=1)

        # ATR 14
        atr_14, atr_pct = cls.calculate_atr(df, periods=14)
        df["atr_14"] = atr_14
        df["atr_pct"] = atr_pct

        # Donchian Channels
        donchian = cls.calculate_donchian_channels(df)
        for k, v in donchian.items():
            df[k] = v

        # Bollinger Bands
        bb = cls.calculate_bollinger_bands(df, window=20)
        df["bb_upper"] = bb["upper"]
        df["bb_middle"] = bb["middle"]
        df["bb_lower"] = bb["lower"]

        # MACD
        macd = cls.calculate_macd(df)
        df["macd_line"] = macd["macd_line"]
        df["macd_signal"] = macd["signal_line"]
        df["macd_histogram"] = macd["histogram"]

        # Time-series crossovers (computed across history for this stock)
        sma_50_prev = df["sma_50"].shift(1)
        sma_200_prev = df["sma_200"].shift(1)
        df["golden_cross"] = (df["sma_50"] > df["sma_200"]) & (sma_50_prev <= sma_200_prev)
        df["death_cross"] = (df["sma_50"] < df["sma_200"]) & (sma_50_prev >= sma_200_prev)

        macd_line_prev = df["macd_line"].shift(1)
        macd_signal_prev = df["macd_signal"].shift(1)
        df["macd_cross_up"] = (df["macd_line"] > df["macd_signal"]) & (
            macd_line_prev <= macd_signal_prev
        )
        df["macd_cross_down"] = (df["macd_line"] < df["macd_signal"]) & (
            macd_line_prev >= macd_signal_prev
        )

        # Returns & Volatility
        df["daily_returns"] = cls.calculate_daily_returns(df)
        df["volatility_20"] = cls.calculate_volatility(df, window=20)

        # Lookback percentage changes: 1D, 1M (21d), 3M (63d), 6M (126d), 12M (252d)
        df["pct_change_daily"] = cls.calculate_pct_change(df, periods=1)
        df["pct_change_1m"] = cls.calculate_pct_change(df, periods=21)
        df["pct_change_3m"] = cls.calculate_pct_change(df, periods=63)
        df["pct_change_6m"] = cls.calculate_pct_change(df, periods=126)
        df["pct_change_12m"] = cls.calculate_pct_change(df, periods=252)

        # Historical shift references: 60d, 120d, 180d, 252d
        df["close_60d"] = cls.calculate_shifted_price(df, shift_days=60)
        df["close_120d"] = cls.calculate_shifted_price(df, shift_days=120)
        df["close_180d"] = cls.calculate_shifted_price(df, shift_days=180)
        df["close_252d"] = cls.calculate_shifted_price(df, shift_days=252)

        # Historical return metrics for momentum screeners
        df["return_60d"] = ((df["close"] - df["close_60d"]) / df["close_60d"]) * 100.0
        df["return_120d"] = ((df["close"] - df["close_120d"]) / df["close_120d"]) * 100.0

        # Relative strength vs NIFTY 500 benchmark (rs_63d, rs_126d, rs_252d)
        if benchmark_df is not None and not benchmark_df.empty and "date" in df.columns:
            merged = pd.merge(df[["date"]], benchmark_df[["date", "close"]], on="date", how="left")
            b_close = merged["close"]
            b_ret_63 = b_close.pct_change(periods=63)
            b_ret_126 = b_close.pct_change(periods=126)
            b_ret_252 = b_close.pct_change(periods=252)

            stock_ret_63 = df["close"].pct_change(periods=63)
            stock_ret_126 = df["close"].pct_change(periods=126)
            stock_ret_252 = df["close"].pct_change(periods=252)

            df["rs_63d"] = stock_ret_63 / b_ret_63.replace(0, np.nan)
            df["rs_126d"] = stock_ret_126 / b_ret_126.replace(0, np.nan)
            df["rs_252d"] = stock_ret_252 / b_ret_252.replace(0, np.nan)
        else:
            df["rs_63d"] = np.nan
            df["rs_126d"] = np.nan
            df["rs_252d"] = np.nan

        # Custom "HMM" indicator
        # Condition A: RSI(9) >= WMA(RSI(9), 21) + 1.1 and 45 <= RSI(9) <= 65
        # Condition B: EMA(Close, 20) > SMA(Close, 50) > SMA(Close, 200)
        #              and Close > SMA(50) and Close > SMA(200)
        # Condition C: Close >= 0.85 * (1-day shifted 252d High)
        # Condition D: Volume > 1.5 * SMA(Volume, 20) and SMA(Volume, 20) > 250,000
        rsi_9_wma_21 = cls.calculate_wma(df, window=21, column="rsi_9")
        cond_a = (df["rsi_9"] >= rsi_9_wma_21 + 1.1) & (df["rsi_9"] >= 45.0) & (df["rsi_9"] <= 65.0)
        cond_b = (
            (df["ema_20"] > df["sma_50"])
            & (df["sma_50"] > df["sma_200"])
            & (df["close"] > df["sma_50"])
            & (df["close"] > df["sma_200"])
        )
        cond_c = df["close"] >= 0.85 * df["high_252d_prev"]
        vol_sma_val = df.get("volume_sma_20", pd.Series(0, index=df.index))
        vol_val = df.get("volume", pd.Series(0, index=df.index))
        cond_d = (vol_val > 1.5 * vol_sma_val) & (vol_sma_val > 250_000)

        df["HMM"] = cond_a & cond_b & cond_c & cond_d

        return df

    @classmethod
    def apply_all_indicators(
        cls, df: pd.DataFrame, benchmark_df: pd.DataFrame | None = None
    ) -> pd.DataFrame:
        """Apply all technical and quantitative indicators to a dataframe."""
        if df.empty:
            return df.copy()

        if "symbol" in df.columns and df["symbol"].nunique() > 1:
            sorted_df = df.sort_values(by=["symbol", "date"]) if "date" in df.columns else df

            def _apply_with_symbol(grp):
                result = cls._apply_indicators_to_series(grp, benchmark_df=benchmark_df)
                sym_values = sorted_df.loc[grp.index, "symbol"]
                result["symbol"] = sym_values.values
                return result

            return sorted_df.groupby("symbol", group_keys=False).apply(
                _apply_with_symbol, include_groups=False
            )

        if "date" in df.columns:
            sorted_df = df.sort_values(by="date")
            return cls._apply_indicators_to_series(sorted_df, benchmark_df=benchmark_df)

        return cls._apply_indicators_to_series(df, benchmark_df=benchmark_df)
