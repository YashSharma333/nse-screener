"""
Scenario-based unit tests for TechnicalCalculator.

Each test constructs a minimal synthetic DataFrame with deterministic
values so the expected output can be hand-verified.  Tests cover:
  - 20-day Volume SMA accuracy
  - 252-day shifted rolling high
  - RSI mathematical correctness
  - Percentage-change lookbacks (1M / 3M / 6M / 12M)
  - Historical price shifts
  - apply_all_indicators column completeness
  - Edge cases (empty / single row)
"""

import pytest
import numpy as np
import pandas as pd

from src.computation.indicators import TechnicalCalculator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _flat_df(n: int, close: float = 100.0, volume: int = 300_000) -> pd.DataFrame:
    """Return a DataFrame with *n* rows, constant OHLCV values."""
    dates = pd.date_range(end="2024-06-30", periods=n, freq="B")
    return pd.DataFrame(
        {
            "date":   dates,
            "open":   close,
            "high":   close + 1,
            "low":    close - 1,
            "close":  float(close),
            "volume": float(volume),
        }
    )


def _make_calc() -> TechnicalCalculator:
    return TechnicalCalculator()


# ---------------------------------------------------------------------------
# Scenario: Volume SMA-20 accuracy
# ---------------------------------------------------------------------------
class TestVolumeSMA20:
    def test_constant_volume_sma_equals_volume(self):
        """When volume is constant, SMA-20 == volume after 20 rows."""
        calc = _make_calc()
        df = _flat_df(30, volume=500_000)
        result = calc.calculate_sma(df, window=20, column="volume")
        # First 19 rows are NaN (need 20 to form a window)
        assert pd.isna(result.iloc[18])
        # 20th row onward: SMA == 500 000
        assert not pd.isna(result.iloc[19])
        assert abs(result.iloc[29] - 500_000) < 1e-6

    def test_sma_of_increasing_volume(self):
        """SMA-20 of [1, 2, …, 20] == 10.5 (mean of 1..20)."""
        calc = _make_calc()
        n = 25
        dates = pd.date_range(end="2024-06-30", periods=n, freq="B")
        df = pd.DataFrame(
            {
                "date":   dates,
                "open":   100.0,
                "high":   101.0,
                "low":    99.0,
                "close":  100.0,
                "volume": [float(i + 1) for i in range(n)],
            }
        )
        result = calc.calculate_sma(df, window=20, column="volume")
        # At index 19 the window covers values 1..20 → mean = 10.5
        assert abs(result.iloc[19] - 10.5) < 1e-6
        # At index 24 the window covers values 6..25 → mean = 15.5
        assert abs(result.iloc[24] - 15.5) < 1e-6


# ---------------------------------------------------------------------------
# Scenario: 252-day shifted rolling high
# ---------------------------------------------------------------------------
class TestHigh252dPrev:
    def test_flat_high_equals_value(self):
        """Constant high+1 series → rolling max == high+1, shifted 1 day.

        rolling(252).max() first produces a valid value at index 251 (0-based).
        After .shift(1) that value moves to index 252, so:
          - index 251 is NaN (shift consumed the first valid rolling value)
          - index 252 onward is valid = 101.0
        """
        calc = _make_calc()
        n = 300
        df = _flat_df(n, close=100.0)
        result = calc.calculate_shifted_rolling_high(df, window=252, shift_days=1)
        # Index 251 is NaN: rolling needs 252 rows (0–251), shift moves it to 252.
        assert pd.isna(result.iloc[251])
        # Index 252 is the first valid value
        assert not pd.isna(result.iloc[252])
        # high column is close + 1 = 101
        assert abs(result.iloc[260] - 101.0) < 1e-6

    def test_high_tracks_maximum(self):
        """Inject a spike at index 10; the shifted rolling high captures it
        for all valid rows where the spike is still within the 252-day window.

        The spike at index 10 enters the rolling window at row 10 and exits
        when the window moves past it (after row 10 + 252 - 1 = 261 in the
        un-shifted series, which maps to shifted index 262). We only assert
        over rows where the spike is definitely in-window.
        """
        calc = _make_calc()
        n = 270
        df = _flat_df(n, close=100.0)
        df.loc[10, "high"] = 999.0
        result = calc.calculate_shifted_rolling_high(df, window=252, shift_days=1)
        # First valid shifted value is at index 252 (rolling needs 252 rows then +1 shift)
        # The spike at index 10 is within the window for shifted indices 252..262
        in_window = result.iloc[252:263].dropna()
        assert len(in_window) > 0
        assert (in_window == 999.0).all(), f"Expected all 999.0, got: {in_window.tolist()}"


# ---------------------------------------------------------------------------
# Scenario: RSI mathematical correctness
# ---------------------------------------------------------------------------
class TestRSI:
    def test_rsi_all_gains_returns_100(self):
        """Strictly increasing prices → all gains, no losses → RSI ≈ 100."""
        calc = _make_calc()
        n = 50
        dates = pd.date_range(end="2024-06-30", periods=n, freq="B")
        df = pd.DataFrame(
            {
                "date":   dates,
                "open":   [float(i + 100) for i in range(n)],
                "high":   [float(i + 101) for i in range(n)],
                "low":    [float(i + 99) for i in range(n)],
                "close":  [float(i + 100) for i in range(n)],
                "volume": 100_000.0,
            }
        )
        rsi = calc.calculate_rsi(df, periods=14)
        # After the warm-up period, RSI should be very close to 100
        valid = rsi.dropna()
        assert (valid > 99.0).all(), f"Expected RSI ~100, got min={valid.min():.2f}"

    def test_rsi_all_losses_returns_0(self):
        """Strictly decreasing prices → all losses, no gains → RSI ≈ 0."""
        calc = _make_calc()
        n = 50
        dates = pd.date_range(end="2024-06-30", periods=n, freq="B")
        df = pd.DataFrame(
            {
                "date":   dates,
                "open":   [float(100 - i) for i in range(n)],
                "high":   [float(101 - i) for i in range(n)],
                "low":    [float(99 - i) for i in range(n)],
                "close":  [float(100 - i) for i in range(n)],
                "volume": 100_000.0,
            }
        )
        rsi = calc.calculate_rsi(df, periods=14)
        valid = rsi.dropna()
        assert (valid < 1.0).all(), f"Expected RSI ~0, got max={valid.max():.2f}"

    def test_rsi_nan_boundary(self):
        """RSI requires 15 rows (1 diff + 14-period window) — check exact NaN boundary."""
        calc = _make_calc()
        df = _flat_df(20, close=100.0)
        # Inject variation so we don't get a degenerate 0/0
        df["close"] = [100.0 + (i % 3) for i in range(20)]
        rsi = calc.calculate_rsi(df, periods=14)
        # diff() makes index 0 NaN; rolling(14) needs 14 more → first valid at index 14
        assert pd.isna(rsi.iloc[13])
        assert not pd.isna(rsi.iloc[14])


# ---------------------------------------------------------------------------
# Scenario: Percentage-change lookbacks
# ---------------------------------------------------------------------------
class TestPctChange:
    def test_1m_pct_change_accuracy(self):
        """21-period pct change on a +10% linear ramp: first valid = exactly 21-period return."""
        calc = _make_calc()
        n = 50
        # close[i] = 100 * (1 + 0.005 * i)  → each row +0.5%
        dates = pd.date_range(end="2024-06-30", periods=n, freq="B")
        closes = [100.0 * (1 + 0.005 * i) for i in range(n)]
        df = pd.DataFrame(
            {
                "date":   dates,
                "open":   closes,
                "high":   [c + 1 for c in closes],
                "low":    [c - 1 for c in closes],
                "close":  closes,
                "volume": 100_000.0,
            }
        )
        result = calc.calculate_pct_change(df, periods=21)
        # Index 20 is NaN (shift of 21 requires at least 22 rows, but pct_change
        # on a shift-based approach: first non-NaN is at index 21)
        assert pd.isna(result.iloc[20])
        assert not pd.isna(result.iloc[21])
        # Expected: (close[21] - close[0]) / close[0] * 100
        expected = (closes[21] - closes[0]) / closes[0] * 100
        assert abs(result.iloc[21] - expected) < 1e-6

    def test_lookback_shift_accuracy(self):
        """Shifted price at index i should equal close[i - shift_days]."""
        calc = _make_calc()
        n = 80
        dates = pd.date_range(end="2024-06-30", periods=n, freq="B")
        closes = [float(i + 1) * 10 for i in range(n)]  # 10, 20, ..., 800
        df = pd.DataFrame(
            {
                "date":   dates,
                "open":   closes,
                "high":   [c + 1 for c in closes],
                "low":    [c - 1 for c in closes],
                "close":  closes,
                "volume": 100_000.0,
            }
        )
        shift_60 = calc.calculate_shifted_price(df, shift_days=60)
        # close_60[60] == close[0] == 10
        assert pd.isna(shift_60.iloc[59])
        assert not pd.isna(shift_60.iloc[60])
        assert abs(shift_60.iloc[60] - closes[0]) < 1e-6
        assert abs(shift_60.iloc[61] - closes[1]) < 1e-6


# ---------------------------------------------------------------------------
# Scenario: apply_all_indicators column completeness
# ---------------------------------------------------------------------------
EXPECTED_COLUMNS = [
    "sma_50", "sma_200", "ema_12", "ema_20", "ema_26", "rsi_14",
    "high_52w", "low_52w", "bb_upper", "bb_middle", "bb_lower",
    "macd_line", "macd_signal", "macd_histogram", "daily_returns", "volatility_20",
    "volume_sma_20", "high_252d_prev",
    "pct_change_daily", "pct_change_1m", "pct_change_3m", "pct_change_6m", "pct_change_12m",
    "close_60d", "close_120d", "close_180d", "close_252d",
    "return_60d", "return_120d",
]


class TestApplyAllIndicators:
    def test_all_expected_columns_present(self):
        """apply_all_indicators must add every expected column to the output."""
        calc = _make_calc()
        df = _flat_df(300)
        result = calc.apply_all_indicators(df.copy())
        missing = [c for c in EXPECTED_COLUMNS if c not in result.columns]
        assert not missing, f"Missing columns: {missing}"

    def test_no_crash_on_empty_df(self):
        """Empty input must return empty output without raising."""
        calc = _make_calc()
        empty = pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        result = calc.apply_all_indicators(empty)
        assert len(result) == 0

    def test_no_crash_on_single_row(self):
        """Single-row input must return single-row output (all indicators NaN is OK)."""
        calc = _make_calc()
        df = _flat_df(1)
        result = calc.apply_all_indicators(df.copy())
        assert len(result) == 1

    def test_multi_stock_dispatch(self):
        """apply_all_indicators must handle a multi-symbol DataFrame correctly."""
        calc = _make_calc()
        df_a = _flat_df(300, close=100.0)
        df_a["symbol"] = "AAA"
        df_b = _flat_df(300, close=200.0)
        df_b["symbol"] = "BBB"
        combined = pd.concat([df_a, df_b], ignore_index=True)
        result = calc.apply_all_indicators(combined)
        assert set(result["symbol"].unique()) == {"AAA", "BBB"}
        missing = [c for c in EXPECTED_COLUMNS if c not in result.columns]
        assert not missing, f"Missing columns in multi-stock mode: {missing}"
