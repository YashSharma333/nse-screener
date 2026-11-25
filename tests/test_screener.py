"""
Unit Tests for Systematic Screeners (T11)
=========================================
Tests:
- SwingVolumeScreener (Screener 1: Swing + Volume)
- SwingMomentumScreener (Screener 2: Swing With Momentum)
- Classic filter screeners from src.screeners.classic
"""

from __future__ import annotations

import pandas as pd

from src.screeners.catalog import (
    Above50SmaScreener,
    Above200SmaScreener,
    BollingerSqueezeScreener,
    BullishMacdCrossoverScreener,
    DeathCrossScreener,
    GoldenCrossScreener,
    HighVolatilityScreener,
    Near52WeekHighScreener,
    Near52WeekLowScreener,
    RsiOverboughtScreener,
    RsiOversoldScreener,
    SwingMomentumScreener,
    SwingVolumeScreener,
)
from tests.screener_utils import passed_rows


def _base_s1_row(**overrides) -> dict:
    """Return a snapshot row that passes every Swing + Volume condition."""
    row = {
        "symbol": "PASS",
        "close": 110.0,
        "ema_20": 105.0,  # close > ema_20 AND ema_20 > sma_50
        "sma_50": 95.0,  # ema_20 > sma_50 AND sma_50 > sma_200 AND close > sma_50
        "sma_200": 85.0,  # close > sma_200
        "high_252d_prev": 120.0,  # 0.85 * 120 = 102 <= 110
        "volume": 400_000,  # > 1.5 * 260_000 = 390_000
        "volume_sma_20": 260_000,  # > 250_000
        "sma_volume_20_ago1": 260_000,  # yesterday's average
    }
    row.update(overrides)
    return row


def _base_s2_row(**overrides) -> dict:
    """Return a snapshot row that passes every S1 and S2 condition."""
    row = _base_s1_row()
    row.update(
        {
            "close": 200.0,
            "ema_20": 185.0,
            "sma_50": 170.0,
            "sma_200": 150.0,
            "high_252d_prev": 220.0,  # 0.85 * 220 = 187 <= 200
            "volume": 500_000,
            "volume_sma_20": 300_000,
            "sma_volume_20_ago1": 300_000,
            "close_60d": 200.0 / 1.20,  # return over 60 days = 20% (<= 40%)
            "close_120d": 200.0 / 1.35,  # return over 120 days = 35% (>= 30%)
            "close_180d": 120.0,
            "close_252d": 100.0,
            # ratio_252_180 = (200 - 100) / 120 * 100 = 83.33 <= 300
            # ratio_180_252 = (200 - 120) / 100 * 100 = 80.0  50 <= x <= 300
        }
    )
    row.update(overrides)
    return row


# ============================================================
# Screener 1: Swing + Volume
# ============================================================
class TestScreener1SwingVolume:
    def test_passes_all_conditions(self):
        df = pd.DataFrame([_base_s1_row(symbol="PASS")])
        result = passed_rows(SwingVolumeScreener(), df)
        assert len(result) == 1
        assert result.iloc[0]["symbol"] == "PASS"

    def test_fails_volume_not_1_5x_sma(self):
        row = _base_s1_row(symbol="FAIL_VOL", volume=260_000)
        df = pd.DataFrame([row])
        result = passed_rows(SwingVolumeScreener(), df)
        assert len(result) == 0

    def test_fails_volume_sma_below_liquidity_floor(self):
        row = _base_s1_row(
            symbol="FAIL_LIQ",
            volume_sma_20=200_000,
            volume=310_000,
        )
        df = pd.DataFrame([row])
        result = passed_rows(SwingVolumeScreener(), df)
        assert len(result) == 0

    def test_fails_close_below_sma50(self):
        row = _base_s1_row(symbol="FAIL_MA", close=90.0, ema_20=85.0)
        df = pd.DataFrame([row])
        result = passed_rows(SwingVolumeScreener(), df)
        assert len(result) == 0

    def test_fails_52w_proximity(self):
        row = _base_s1_row(
            symbol="FAIL_52W",
            close=80.0,
            ema_20=75.0,
            sma_50=70.0,
            sma_200=60.0,
            high_252d_prev=200.0,
        )
        df = pd.DataFrame([row])
        result = passed_rows(SwingVolumeScreener(), df)
        assert len(result) == 0

    def test_mixed_pass_fail(self):
        rows = [
            _base_s1_row(symbol="PASS"),
            _base_s1_row(symbol="FAIL_VOL", volume=260_000),
        ]
        df = pd.DataFrame(rows)
        result = passed_rows(SwingVolumeScreener(), df)
        assert len(result) == 1
        assert result.iloc[0]["symbol"] == "PASS"


# ============================================================
# Screener 2: Swing With Momentum
# ============================================================
class TestScreener2SwingMomentum:
    def test_passes_all_conditions(self):
        df = pd.DataFrame([_base_s2_row(symbol="PASS_S2")])
        result = passed_rows(SwingMomentumScreener(), df)
        assert len(result) == 1
        assert result.iloc[0]["symbol"] == "PASS_S2"

    def test_fails_60d_return_too_high(self):
        row = _base_s2_row(symbol="FAIL_60D", close_60d=200.0 / 1.45)  # a 45% return
        df = pd.DataFrame([row])
        result = passed_rows(SwingMomentumScreener(), df)
        assert len(result) == 0

    def test_fails_120d_return_too_low(self):
        row = _base_s2_row(symbol="FAIL_120D", close_120d=200.0 / 1.25)  # a 25% return
        df = pd.DataFrame([row])
        result = passed_rows(SwingMomentumScreener(), df)
        assert len(result) == 0

    def test_fails_ratio_180_252_below_floor(self):
        row = _base_s2_row(
            symbol="FAIL_RATIO_LO",
            close=200.0,
            close_180d=199.0,
            close_252d=100.0,
        )
        df = pd.DataFrame([row])
        result = passed_rows(SwingMomentumScreener(), df)
        assert len(result) == 0

    def test_fails_ratio_180_252_above_cap(self):
        row = _base_s2_row(
            symbol="FAIL_RATIO_HI",
            close=500.0,
            close_180d=100.0,
            close_252d=100.0,
            ema_20=480.0,
            sma_50=460.0,
            sma_200=400.0,
            high_252d_prev=520.0,
            volume=600_000,
            volume_sma_20=300_000,
            close_60d=500.0 / 1.30,  # 30% over 60 days
            close_120d=500.0 / 1.35,  # 35% over 120 days
        )
        df = pd.DataFrame([row])
        result = passed_rows(SwingMomentumScreener(), df)
        assert len(result) == 0

    def test_s2_inherits_s1_filters(self):
        row = _base_s2_row(symbol="FAIL_S1_IN_S2", volume=260_000)
        df = pd.DataFrame([row])
        result = passed_rows(SwingMomentumScreener(), df)
        assert len(result) == 0

    def test_empty_df_returns_empty(self):
        cols = list(_base_s1_row().keys())
        empty = pd.DataFrame(columns=cols)
        assert len(passed_rows(SwingVolumeScreener(), empty)) == 0
        assert len(passed_rows(SwingMomentumScreener(), empty)) == 0


# ============================================================
# Classic Filters Tests
# ============================================================
class TestClassicFilters:
    def test_above_50_and_200_sma(self):
        df = pd.DataFrame(
            {
                "symbol": ["A", "B"],
                "close": [105.0, 95.0],
                "sma_50": [100.0, 100.0],
                "sma_200": [90.0, 100.0],
            }
        )
        s50 = passed_rows(Above50SmaScreener(), df)
        assert list(s50["symbol"]) == ["A"]

        s200 = passed_rows(Above200SmaScreener(), df)
        assert list(s200["symbol"]) == ["A"]

    def test_golden_and_death_cross(self):
        df = pd.DataFrame(
            {
                "symbol": ["A", "B"],
                "golden_cross": [True, False],
                "death_cross": [False, True],
            }
        )
        assert list(passed_rows(GoldenCrossScreener(), df)["symbol"]) == ["A"]
        assert list(passed_rows(DeathCrossScreener(), df)["symbol"]) == ["B"]

    def test_near_52w_high_and_low(self):
        df = pd.DataFrame(
            {
                "symbol": ["NEAR_HI", "NEAR_LO"],
                "close": [98.0, 102.0],
                "high_52w": [100.0, 150.0],
                "low_52w": [50.0, 100.0],
            }
        )
        near_hi = passed_rows(Near52WeekHighScreener(threshold_pct=5.0), df)
        assert list(near_hi["symbol"]) == ["NEAR_HI"]

        near_lo = passed_rows(Near52WeekLowScreener(threshold_pct=5.0), df)
        assert list(near_lo["symbol"]) == ["NEAR_LO"]

    def test_rsi_oversold_and_overbought(self):
        df = pd.DataFrame(
            {
                "symbol": ["OVERSOLD", "OVERBOUGHT"],
                "rsi_14": [25.0, 75.0],
            }
        )
        assert list(passed_rows(RsiOversoldScreener(), df)["symbol"]) == ["OVERSOLD"]
        assert list(passed_rows(RsiOverboughtScreener(), df)["symbol"]) == ["OVERBOUGHT"]

    def test_bullish_macd_crossover(self):
        df = pd.DataFrame(
            {
                "symbol": ["CROSS", "NO_CROSS"],
                "macd_cross_up": [True, False],
            }
        )
        assert list(passed_rows(BullishMacdCrossoverScreener(), df)["symbol"]) == ["CROSS"]

    def test_high_volatility_and_bollinger_squeeze(self):
        df = pd.DataFrame(
            {
                "symbol": ["A", "B"],
                "close": [101.0, 110.0],
                "volatility_20": [0.04, 0.01],
                "bb_lower": [100.0, 90.0],
                "bb_upper": [120.0, 120.0],
            }
        )
        # band_width = 20, position for A = (101 - 100) / 20 = 0.05 < 0.1 -> squeeze!
        assert list(passed_rows(HighVolatilityScreener(), df)["symbol"]) == ["A"]
        assert list(passed_rows(BollingerSqueezeScreener(), df)["symbol"]) == ["A"]
