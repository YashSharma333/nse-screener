"""Unit Tests for Modular Systematic Screeners
=============================================
Verifies condition execution, explainability, and near-miss identification across:
1. MinerviniTrendTemplateScreener
2. Breakout52wVolumeScreener
3. RsiPullbackUptrendScreener
4. VcpLiteScreener
5. RsLeadersScreener
6. CompositeTrendScreener
"""

from __future__ import annotations

import pandas as pd

from src.screeners.catalog import (
    Breakout52wVolumeScreener,
    CompositeTrendScreener,
    MinerviniTrendTemplateScreener,
    RsiPullbackUptrendScreener,
    RsLeadersScreener,
    VcpLiteScreener,
)


# ---------------------------------------------------------------------------
# 1. Minervini Trend Template
# ---------------------------------------------------------------------------
def test_minervini_trend_template_pass_and_near_miss() -> None:
    data = {
        "symbol": ["PASS", "NEAR_MISS_52W_LOW", "FAIL_MULTIPLE"],
        "close": [100.0, 100.0, 40.0],
        "sma_50": [90.0, 90.0, 50.0],
        "sma_150": [80.0, 80.0, 60.0],
        "sma_200": [70.0, 70.0, 70.0],
        "sma_200_slope_21d": [0.05, 0.05, -0.02],
        "low_52w": [
            60.0,
            85.0,
            35.0,
        ],  # PASS: 100 >= 1.3 * 60 = 78; NEAR_MISS: 100 < 1.3 * 85 = 110.5
        "high_52w": [110.0, 110.0, 100.0],  # 100 >= 0.75 * 110 = 82.5
        "rs_63d": [0.15, 0.15, -0.10],
        "ret_rank_252d": [85.0, 85.0, 20.0],
    }
    df = pd.DataFrame(data)
    screener = MinerviniTrendTemplateScreener()

    # run()
    passed = screener.run(df)
    assert len(passed) == 1
    assert passed.iloc[0]["symbol"] == "PASS"

    # explain()
    explained = screener.explain(df)
    row_pass = explained.iloc[0]
    row_near = explained.iloc[1]
    row_fail = explained.iloc[2]

    assert bool(row_pass["passed"]) is True
    assert bool(row_near["passed"]) is False
    assert bool(row_near["near_miss"]) is True
    assert row_near["near_miss_on"] == "price_above_52w_low_30pct"
    assert bool(row_fail["near_miss"]) is False


# ---------------------------------------------------------------------------
# 2. Breakout 52W High + Volume
# ---------------------------------------------------------------------------
def test_breakout_52w_volume() -> None:
    data = {
        "symbol": ["BREAKOUT", "FAIL_VOLUME", "FAIL_PRICE"],
        "close": [198.0, 198.0, 150.0],
        "high_52w": [200.0, 200.0, 200.0],  # 198 >= 0.98 * 200 = 196
        "sma_50": [180.0, 180.0, 180.0],
        "volume": [500_000, 150_000, 500_000],  # 2.0x of 200k = 400k
        "volume_sma_20": [200_000, 200_000, 200_000],
    }
    df = pd.DataFrame(data)
    screener = Breakout52wVolumeScreener()

    passed = screener.run(df)
    assert len(passed) == 1
    assert passed.iloc[0]["symbol"] == "BREAKOUT"

    explained = screener.explain(df)
    assert bool(explained.iloc[1]["near_miss"]) is True
    assert explained.iloc[1]["near_miss_on"] == "volume_surge_2x"


# ---------------------------------------------------------------------------
# 3. RSI Pullback in Uptrend
# ---------------------------------------------------------------------------
def test_rsi_pullback_uptrend() -> None:
    data = {
        "symbol": ["PULLBACK_PASS", "RSI_TOO_HIGH", "DOWNTREND"],
        "close": [105.0, 105.0, 80.0],
        "sma_50": [100.0, 100.0, 90.0],
        "sma_200": [90.0, 90.0, 100.0],
        "rsi_14": [48.0, 65.0, 45.0],  # 40-55 is pass
        "volume": [80_000, 80_000, 80_000],
        "volume_sma_20": [100_000, 100_000, 100_000],  # <= 1.2 * 100k
    }
    df = pd.DataFrame(data)
    screener = RsiPullbackUptrendScreener()

    passed = screener.run(df)
    assert len(passed) == 1
    assert passed.iloc[0]["symbol"] == "PULLBACK_PASS"

    explained = screener.explain(df)
    assert bool(explained.iloc[1]["near_miss"]) is True
    assert explained.iloc[1]["near_miss_on"] == "rsi_support_zone"


# ---------------------------------------------------------------------------
# 4. VCP Lite (Volatility Contraction)
# ---------------------------------------------------------------------------
def test_vcp_lite() -> None:
    data = {
        "symbol": ["VCP_PASS", "FAIL_DRYUP"],
        "close": [105.0, 105.0],
        "sma_50": [100.0, 100.0],
        "atr_pct": [1.5, 1.5],
        "atr_pct_max_60d": [3.0, 3.0],  # 1.5 <= 0.75 * 3.0 = 2.25
        "volume": [40_000, 90_000],  # SMA 100k -> 40k is 0.4 <= 0.7 dryup
        "volume_sma_20": [100_000, 100_000],
    }
    df = pd.DataFrame(data)
    screener = VcpLiteScreener()

    passed = screener.run(df)
    assert len(passed) == 1
    assert passed.iloc[0]["symbol"] == "VCP_PASS"

    explained = screener.explain(df)
    assert bool(explained.iloc[1]["near_miss"]) is True
    assert explained.iloc[1]["near_miss_on"] == "volume_dryup"


# ---------------------------------------------------------------------------
# 5. Relative Strength Leaders
# ---------------------------------------------------------------------------
def test_rs_leaders() -> None:
    data = {
        "symbol": ["LEADER_RS", "LEADER_RANK", "LAGGARD"],
        "close": [120.0, 120.0, 120.0],
        "sma_50": [110.0, 110.0, 110.0],
        "sma_200": [100.0, 100.0, 100.0],
        "rs_63d": [0.15, -0.05, -0.05],  # RS > 0.10
        "ret_rank_252d": [60.0, 92.0, 40.0],  # Rank >= 80
        "volume_sma_20": [150_000, 150_000, 150_000],
    }
    df = pd.DataFrame(data)
    screener = RsLeadersScreener()

    passed = screener.run(df)
    assert len(passed) == 2
    symbols = list(passed["symbol"])
    assert "LEADER_RS" in symbols
    assert "LEADER_RANK" in symbols
    assert "LAGGARD" not in symbols


# ---------------------------------------------------------------------------
# 6. Composite Trend Screener
# ---------------------------------------------------------------------------
def test_composite_trend() -> None:
    data = {
        "symbol": ["TRENDING", "NOT_TRENDING"],
        "close": [105.0, 95.0],
        "ema_20": [102.0, 98.0],
        "sma_50": [100.0, 100.0],
        "sma_200": [90.0, 105.0],
        "golden_cross_10d": [True, False],
        "macd_line": [2.5, 0.5],
        "macd_signal": [1.5, 1.0],  # line > signal
        "rsi_14": [58.0, 42.0],  # >= 50
        "volume": [120_000, 80_000],
        "volume_sma_20": [100_000, 100_000],
    }
    df = pd.DataFrame(data)
    screener = CompositeTrendScreener()

    passed = screener.run(df)
    assert len(passed) == 1
    assert passed.iloc[0]["symbol"] == "TRENDING"
