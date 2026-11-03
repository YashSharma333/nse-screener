"""
Technical Calculator Facade
===========================
Orchestrates calculation of all technical indicator domains (Price, Volume, Crossovers,
Momentum) across single-symbol and multi-symbol DataFrames.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import numpy as np
import pandas as pd

from src.computation.indicators import crossovers, momentum, price, volume

logger = logging.getLogger("src.computation.indicators")

# Bumped schema version whenever columns or formulas change
INDICATORS_SCHEMA_VERSION = 2


class TechnicalCalculator:
    """Computes technical, quantitative, and momentum indicators."""

    schema_version: int = INDICATORS_SCHEMA_VERSION

    # ------------------------------------------------------------------
    # Forwarding methods for price-based indicators
    # ------------------------------------------------------------------
    @classmethod
    def calculate_sma(cls, df: pd.DataFrame, window: int, column: str = "close") -> pd.Series:
        return price.calculate_sma(df, window=window, column=column)

    @classmethod
    def calculate_ema(cls, df: pd.DataFrame, span: int, column: str = "close") -> pd.Series:
        return price.calculate_ema(df, span=span, column=column)

    @classmethod
    def calculate_wma(cls, df: pd.DataFrame, window: int, column: str = "close") -> pd.Series:
        return price.calculate_wma(df, window=window, column=column)

    @classmethod
    def calculate_bollinger_bands(
        cls, df: pd.DataFrame, window: int = 20, num_std: float = 2.0
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        return price.calculate_bollinger_bands(df, window=window, num_std=num_std)

    @classmethod
    def calculate_true_range(cls, df: pd.DataFrame) -> pd.Series:
        return price.calculate_true_range(df)

    @classmethod
    def calculate_atr(cls, df: pd.DataFrame, window: int = 14) -> tuple[pd.Series, pd.Series]:
        return price.calculate_atr(df, window=window)

    @classmethod
    def calculate_donchian_channels(
        cls, df: pd.DataFrame, window: int = 20
    ) -> tuple[pd.Series, pd.Series]:
        return price.calculate_donchian_channels(df, window=window)

    @classmethod
    def calculate_52w_high_low(
        cls, df: pd.DataFrame, window: int = 252
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        return price.calculate_52w_high_low(df, window=window)

    @classmethod
    def calculate_shifted_rolling_high(
        cls, df: pd.DataFrame, window: int = 252, shift_days: int = 1, column: str = "high"
    ) -> pd.Series:
        return price.calculate_shifted_rolling_high(
            df, window=window, shift_days=shift_days, column=column
        )

    @classmethod
    def calculate_shifted_price(cls, df: pd.DataFrame, shift_days: int) -> pd.Series:
        return price.calculate_shifted_price(df, shift_days=shift_days)

    @classmethod
    def calculate_pct_change(cls, df: pd.DataFrame, periods: int) -> pd.Series:
        return price.calculate_pct_change(df, periods=periods)

    @classmethod
    def calculate_daily_returns(cls, df: pd.DataFrame) -> pd.Series:
        return price.calculate_daily_returns(df)

    @classmethod
    def calculate_volatility(cls, df: pd.DataFrame, window: int = 20) -> pd.Series:
        return price.calculate_volatility(df, window=window)

    # ------------------------------------------------------------------
    # Forwarding methods for volume-based indicators
    # ------------------------------------------------------------------
    @classmethod
    def calculate_volume_sma(cls, df: pd.DataFrame, window: int = 20) -> pd.Series:
        return volume.calculate_volume_sma(df, window=window)

    @classmethod
    def calculate_volume_ratio(cls, df: pd.DataFrame, window: int = 20) -> pd.Series:
        return volume.calculate_volume_ratio(df, window=window)

    @classmethod
    def calculate_volume_dryup(
        cls, df: pd.DataFrame, short_window: int = 5, long_window: int = 50
    ) -> pd.Series:
        return volume.calculate_volume_dryup(df, short_window=short_window, long_window=long_window)

    # ------------------------------------------------------------------
    # Forwarding methods for crossover indicators
    # ------------------------------------------------------------------
    @classmethod
    def calculate_golden_death_cross(
        cls, df: pd.DataFrame, sma_50_col: str = "sma_50", sma_200_col: str = "sma_200"
    ) -> tuple[pd.Series, pd.Series]:
        return crossovers.calculate_golden_death_cross(
            df, sma_50_col=sma_50_col, sma_200_col=sma_200_col
        )

    @classmethod
    def calculate_macd_crossovers(
        cls,
        df: pd.DataFrame,
        macd_line_col: str = "macd_line",
        macd_signal_col: str = "macd_signal",
    ) -> tuple[pd.Series, pd.Series]:
        return crossovers.calculate_macd_crossovers(
            df, macd_line_col=macd_line_col, macd_signal_col=macd_signal_col
        )

    @classmethod
    def calculate_recent_crossover(cls, crossover_series: pd.Series, window: int = 10) -> pd.Series:
        return crossovers.calculate_recent_crossover(crossover_series, window=window)

    # ------------------------------------------------------------------
    # Forwarding methods for momentum & comparative indicators
    # ------------------------------------------------------------------
    @classmethod
    def calculate_rsi(
        cls,
        df: pd.DataFrame,
        window: int = 14,
        periods: int | None = None,
        column: str = "close",
    ) -> pd.Series:
        return momentum.calculate_rsi(df, window=window, periods=periods, column=column)

    @classmethod
    def calculate_macd(
        cls, df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        return momentum.calculate_macd(df, fast=fast, slow=slow, signal=signal)

    @classmethod
    def calculate_relative_strength(
        cls,
        df: pd.DataFrame,
        benchmark_df: pd.DataFrame | None,
        periods_list: Sequence[int] = (63, 126, 252),
    ) -> dict[str, pd.Series]:
        return momentum.calculate_relative_strength(
            df, benchmark_df=benchmark_df, periods_list=tuple(periods_list)
        )

    @classmethod
    def calculate_cross_sectional_ranks(
        cls, df: pd.DataFrame, date_col: str = "date"
    ) -> tuple[pd.Series, pd.Series]:
        return momentum.calculate_cross_sectional_ranks(df, date_col=date_col)

    @classmethod
    def calculate_hmm_indicator(cls, df: pd.DataFrame) -> pd.Series:
        return momentum.calculate_hmm_indicator(df)

    # ------------------------------------------------------------------
    # Core orchestration method
    # ------------------------------------------------------------------
    @classmethod
    def _apply_indicators_to_series(
        cls, df: pd.DataFrame, benchmark_df: pd.DataFrame | None = None
    ) -> pd.DataFrame:
        """Apply all indicator domains to a chronological single-symbol series."""
        if df.empty or len(df) < 2:
            return df.copy()

        res = df.copy()

        # 1. Price-based indicators
        res["sma_20"] = price.calculate_sma(res, window=20)
        res["sma_50"] = price.calculate_sma(res, window=50)
        res["sma_150"] = price.calculate_sma(res, window=150)
        res["sma_200"] = price.calculate_sma(res, window=200)

        # SMA 200 slope (21 sessions)
        sma_200_shift21 = res["sma_200"].shift(21)
        res["sma_200_slope_21d"] = (res["sma_200"] / sma_200_shift21.replace(0, np.nan)) - 1.0

        res["ema_12"] = price.calculate_ema(res, span=12)
        res["ema_20"] = price.calculate_ema(res, span=20)
        res["ema_26"] = price.calculate_ema(res, span=26)

        # Bollinger Bands & width
        bb_mid, bb_up, bb_low = price.calculate_bollinger_bands(res, window=20, num_std=2.0)
        res["bb_middle"] = bb_mid
        res["bb_upper"] = bb_up
        res["bb_lower"] = bb_low
        bb_mid_nonzero = bb_mid.replace(0, np.nan)
        res["bb_width"] = (bb_up - bb_low) / bb_mid_nonzero

        # ATR & ATR%
        atr, atr_pct = price.calculate_atr(res, window=14)
        res["atr_14"] = atr
        res["atr_pct"] = atr_pct
        res["atr_pct_max_60d"] = atr_pct.rolling(window=min(60, len(res)), min_periods=1).max()

        # Donchian Channels
        d_high_20, d_low_20 = price.calculate_donchian_channels(res, window=20)
        res["donchian_high_20"] = d_high_20
        res["donchian_low_20"] = d_low_20
        d_high_55, d_low_55 = price.calculate_donchian_channels(res, window=55)
        res["donchian_high_55"] = d_high_55
        res["donchian_low_55"] = d_low_55

        # 52w High / Low
        h_52, l_52, h_252_prev = price.calculate_52w_high_low(res, window=252)
        res["high_52w"] = h_52
        res["low_52w"] = l_52
        res["high_252d_prev"] = h_252_prev

        # Returns & Volatility
        res["daily_returns"] = price.calculate_daily_returns(res)
        res["volatility_20"] = price.calculate_volatility(res, window=20)

        # Lookbacks: 1D, 1M (21d), 3M (63d), 6M (126d), 12M (252d)
        res["pct_change_daily"] = price.calculate_pct_change(res, periods=1)
        res["pct_change_1m"] = price.calculate_pct_change(res, periods=21)
        res["pct_change_3m"] = price.calculate_pct_change(res, periods=63)
        res["pct_change_6m"] = price.calculate_pct_change(res, periods=126)
        res["pct_change_12m"] = price.calculate_pct_change(res, periods=252)

        # Shifted prices
        res["close_60d"] = price.calculate_shifted_price(res, shift_days=60)
        res["close_120d"] = price.calculate_shifted_price(res, shift_days=120)
        res["close_180d"] = price.calculate_shifted_price(res, shift_days=180)
        res["close_252d"] = price.calculate_shifted_price(res, shift_days=252)

        c60_nz = res["close_60d"].replace(0, np.nan)
        c120_nz = res["close_120d"].replace(0, np.nan)
        res["return_60d"] = ((res["close"] - res["close_60d"]) / c60_nz) * 100.0
        res["return_120d"] = ((res["close"] - res["close_120d"]) / c120_nz) * 100.0

        # 2. Volume-based indicators
        res["volume_sma_20"] = volume.calculate_volume_sma(res, window=20)
        res["volume_ratio"] = volume.calculate_volume_ratio(res, window=20)
        res["vol_dryup"] = volume.calculate_volume_dryup(res, short_window=5, long_window=50)

        # 3. Momentum & Oscillator indicators
        res["rsi_9"] = momentum.calculate_rsi(res, window=9)
        res["rsi_14"] = momentum.calculate_rsi(res, window=14)

        macd_line, macd_sig, macd_hist = momentum.calculate_macd(res, fast=12, slow=26, signal=9)
        res["macd_line"] = macd_line
        res["macd_signal"] = macd_sig
        res["macd_histogram"] = macd_hist

        # Relative strength vs benchmark
        rs_dict = momentum.calculate_relative_strength(
            res, benchmark_df=benchmark_df, periods_list=(63, 126, 252)
        )
        for k, v in rs_dict.items():
            res[k] = v

        # Composite breakout rule (HMM)
        res["HMM"] = momentum.calculate_hmm_indicator(res)

        # 4. Crossovers
        g_cross, d_cross = crossovers.calculate_golden_death_cross(res)
        res["golden_cross"] = g_cross
        res["death_cross"] = d_cross
        res["golden_cross_10d"] = crossovers.calculate_recent_crossover(g_cross, window=10)

        m_up, m_down = crossovers.calculate_macd_crossovers(res)
        res["macd_cross_up"] = m_up
        res["macd_cross_down"] = m_down

        return res

    @classmethod
    def apply_all_indicators(
        cls, df: pd.DataFrame, benchmark_df: pd.DataFrame | None = None
    ) -> pd.DataFrame:
        """Apply all indicator domains to a dataframe across symbols and dates."""
        if df.empty:
            return df.copy()

        if "symbol" in df.columns and df["symbol"].nunique() > 1:
            sorted_df = df.sort_values(by=["symbol", "date"]) if "date" in df.columns else df
            # Group by symbol, preserving symbol column and all input rows
            symbol_frames = []
            for _, grp in sorted_df.groupby("symbol", as_index=False):
                computed = cls._apply_indicators_to_series(grp, benchmark_df=benchmark_df)
                symbol_frames.append(computed)
            res = pd.concat(symbol_frames).sort_index()
        else:
            sorted_df = df.sort_values(by="date") if "date" in df.columns else df
            res = cls._apply_indicators_to_series(sorted_df, benchmark_df=benchmark_df)

        # Cross-sectional ranks across symbols for each date
        if "date" in res.columns:
            r126, r252 = momentum.calculate_cross_sectional_ranks(res, date_col="date")
            res["ret_rank_126d"] = r126
            res["ret_rank_252d"] = r252
        else:
            res["ret_rank_126d"] = np.nan
            res["ret_rank_252d"] = np.nan

        return res
