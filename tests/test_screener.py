"""
Scenario-based unit tests for StockScreener.

Named scenarios cover:
  - A stock that passes ALL Strategy 1 (Liquid 1.5x Vol) conditions
  - A stock that fails the volume condition (volume not 1.5x SMA)
  - A stock that fails the volume SMA liquidity floor (SMA < 250k)
  - A stock that fails the MA alignment (Close < SMA 50)
  - A stock that fails the 52-week high proximity (Close < 85% of high_252d_prev)
  - A stock not in a tracked NIFTY index → filtered by Strategy 1
  - A stock that passes ALL Strategy 2 (Liquid 1.5x Vol Momentum) conditions
  - A stock that fails the 60-day return cap (return > 40%)
  - A stock that fails the 120-day return floor (return < 30%)
  - A stock that fails the lower momentum ratio bound
"""

import pytest
import pandas as pd

from src.screener import StockScreener


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _base_s1_row(**overrides) -> dict:
    """Return a snapshot row that passes every Strategy 1 condition."""
    row = {
        "symbol":        "PASS",
        "close":         110.0,
        "ema_20":        105.0,   # close > ema_20 AND ema_20 > sma_50
        "sma_50":         95.0,   # ema_20 > sma_50 AND sma_50 > sma_200 AND close > sma_50
        "sma_200":        85.0,   # close > sma_200
        "high_252d_prev": 120.0,  # 0.85 * 120 = 102 ≤ 110 ✓
        "volume":       400_000,  # > 1.5 * 260_000 = 390_000 ✓
        "volume_sma_20": 260_000, # > 250_000 ✓
        "index_name":  "NIFTY 100",
    }
    row.update(overrides)
    return row


def _base_s2_row(**overrides) -> dict:
    """Return a snapshot row that passes every Strategy 1 AND Strategy 2 condition."""
    row = _base_s1_row()
    row.update(
        {
            "close":      200.0,
            "ema_20":     185.0,
            "sma_50":     170.0,
            "sma_200":    150.0,
            "high_252d_prev": 220.0,  # 0.85 * 220 = 187 ≤ 200 ✓
            "volume":     500_000,
            "volume_sma_20": 300_000,
            # Strategy 2 momentum columns
            "return_60d":  20.0,   # ≤ 40% ✓
            "return_120d": 35.0,   # ≥ 30% ✓
            "close_180d":  120.0,
            "close_252d":  100.0,
            # ratio_252_180 = (200 - 100) / 120 * 100 = 83.33  ≤ 300 ✓
            # ratio_180_252 = (200 - 120) / 100 * 100 = 80.0   50 ≤ x ≤ 300 ✓
        }
    )
    row.update(overrides)
    return row


def _screener(rows: list[dict]) -> StockScreener:
    return StockScreener(pd.DataFrame(rows))


# ============================================================
# Strategy 1: Liquid 1.5x Vol
# ============================================================
class TestStrategy1LiquidVolume:
    def test_passes_all_conditions(self):
        """Stock meeting every S1 filter must appear in results."""
        s = _screener([_base_s1_row(symbol="PASS")])
        result = s.strategy_liquid_volume()
        assert len(result) == 1
        assert result.iloc[0]["symbol"] == "PASS"

    def test_fails_volume_not_1_5x_sma(self):
        """Volume exactly at the SMA (not 1.5×) must be excluded."""
        row = _base_s1_row(symbol="FAIL_VOL", volume=260_000)  # == sma, not > 1.5x
        s = _screener([row])
        result = s.strategy_liquid_volume()
        assert len(result) == 0

    def test_fails_volume_sma_below_liquidity_floor(self):
        """volume_sma_20 < 250 000 must be excluded even if volume is 1.5x."""
        row = _base_s1_row(
            symbol="FAIL_LIQ",
            volume_sma_20=200_000,   # < 250k floor
            volume=310_000,           # > 1.5 * 200k = 300k, so volume check passes
        )
        s = _screener([row])
        result = s.strategy_liquid_volume()
        assert len(result) == 0

    def test_fails_close_below_sma50(self):
        """Close < SMA-50 violates the MA alignment condition."""
        row = _base_s1_row(symbol="FAIL_MA", close=90.0, ema_20=85.0)
        # close(90) < sma_50(95) → fail
        s = _screener([row])
        result = s.strategy_liquid_volume()
        assert len(result) == 0

    def test_fails_52w_proximity(self):
        """Close < 85% of high_252d_prev must be excluded."""
        row = _base_s1_row(
            symbol="FAIL_52W",
            close=80.0,
            ema_20=75.0,
            sma_50=70.0,
            sma_200=60.0,
            high_252d_prev=200.0,  # 0.85 * 200 = 170 > 80 → fail
        )
        s = _screener([row])
        result = s.strategy_liquid_volume()
        assert len(result) == 0

    def test_fails_not_in_tracked_index(self):
        """Stocks with index_name not in tracked set must be excluded."""
        row = _base_s1_row(symbol="FAIL_IDX", index_name="NIFTY IT")
        s = _screener([row])
        result = s.strategy_liquid_volume()
        assert len(result) == 0

    def test_nan_index_name_excluded(self):
        """NaN index_name (stock not in any tracked index) must be excluded."""
        row = _base_s1_row(symbol="FAIL_NAN", index_name=None)
        s = _screener([row])
        result = s.strategy_liquid_volume()
        assert len(result) == 0

    def test_mixed_pass_fail(self):
        """Only the passing stock is returned when mixed with a failing one."""
        rows = [
            _base_s1_row(symbol="PASS"),
            _base_s1_row(symbol="FAIL_VOL", volume=260_000),
        ]
        s = _screener(rows)
        result = s.strategy_liquid_volume()
        assert len(result) == 1
        assert result.iloc[0]["symbol"] == "PASS"


# ============================================================
# Strategy 2: Liquid 1.5x Vol Momentum
# ============================================================
class TestStrategy2LiquidMomentum:
    def test_passes_all_conditions(self):
        """Stock meeting every S2 filter must appear in results."""
        s = _screener([_base_s2_row(symbol="PASS_S2")])
        result = s.strategy_liquid_momentum()
        assert len(result) == 1
        assert result.iloc[0]["symbol"] == "PASS_S2"

    def test_fails_60d_return_too_high(self):
        """60-day return > 40% violates the cap condition."""
        row = _base_s2_row(symbol="FAIL_60D", return_60d=45.0)
        s = _screener([row])
        result = s.strategy_liquid_momentum()
        assert len(result) == 0

    def test_fails_120d_return_too_low(self):
        """120-day return < 30% violates the floor condition."""
        row = _base_s2_row(symbol="FAIL_120D", return_120d=25.0)
        s = _screener([row])
        result = s.strategy_liquid_momentum()
        assert len(result) == 0

    def test_fails_ratio_180_252_below_floor(self):
        """((Close - Close_180d) / Close_252d) * 100 < 50 is rejected."""
        # ratio_180_252 = (close - close_180d) / close_252d * 100
        # Set close_180d close to close so ratio ≈ 0 < 50
        row = _base_s2_row(
            symbol="FAIL_RATIO_LO",
            close=200.0,
            close_180d=199.0,   # ratio ≈ 1% < 50
            close_252d=100.0,
        )
        s = _screener([row])
        result = s.strategy_liquid_momentum()
        assert len(result) == 0

    def test_fails_ratio_180_252_above_cap(self):
        """((Close - Close_180d) / Close_252d) * 100 > 300 is rejected."""
        row = _base_s2_row(
            symbol="FAIL_RATIO_HI",
            close=500.0,
            close_180d=100.0,
            close_252d=100.0,   # ratio = 400% > 300
            ema_20=480.0,
            sma_50=460.0,
            sma_200=400.0,
            high_252d_prev=520.0,
            volume=600_000,
            volume_sma_20=300_000,
            return_60d=30.0,
            return_120d=35.0,
        )
        s = _screener([row])
        result = s.strategy_liquid_momentum()
        assert len(result) == 0

    def test_s2_inherits_s1_filters(self):
        """A row that passes S2 momentum but fails S1 volume must be excluded."""
        row = _base_s2_row(symbol="FAIL_S1_IN_S2", volume=260_000)
        s = _screener([row])
        result = s.strategy_liquid_momentum()
        assert len(result) == 0

    def test_empty_df_returns_empty(self):
        """Empty input to either strategy returns empty DataFrame without error."""
        cols = list(_base_s1_row().keys())
        empty = pd.DataFrame(columns=cols)
        s = StockScreener(empty)
        assert len(s.strategy_liquid_volume()) == 0
        # Add momentum cols for S2
        for col in ["return_60d", "return_120d", "close_180d", "close_252d"]:
            empty[col] = pd.Series(dtype=float)
        s2 = StockScreener(empty)
        assert len(s2.strategy_liquid_momentum()) == 0


# ============================================================
# Import compatibility
# ============================================================
class TestImportCompat:
    def test_screener_importable_from_both_paths(self):
        """src.screener and src.computation.screener must resolve to same class."""
        from src.screener import StockScreener as S1
        from src.computation.screener import StockScreener as S2
        assert S1 is S2
