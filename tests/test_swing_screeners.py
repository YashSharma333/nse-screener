"""Swing screeners pick a stock that passes every rule."""

from __future__ import annotations

from datetime import date

import pandas as pd

from src.screeners.catalog import SwingMomentumScreener, SwingVolumeScreener


def test_swing_screeners_return_symbol_that_passes_every_rule() -> None:
    row = {
        "symbol": "SWING_STOCK",
        "date": date(2026, 1, 15),
        "close": 200.0,
        "ema_20": 185.0,
        "sma_50": 170.0,
        "sma_200": 150.0,  # ma_alignment: ema_20 > sma_50 > sma_200; close > 50, 200
        "high_252d_prev": 220.0,  # within 15% of 52W high (0.85 * 220 = 187 <= 200)
        "volume": 500_000,  # volume > 1.5 * volume_sma_20
        "volume_sma_20": 300_000,  # volume_sma_20 > 250k
        "return_60d": 20.0,  # <= 40
        "return_120d": 35.0,  # >= 30
        "close_180d": 120.0,
        "close_252d": 100.0,  # (200 - 120) / 100 * 100 = 80.0 (50 <= x <= 300)
    }
    df = pd.DataFrame([row])

    for screener in (SwingVolumeScreener(), SwingMomentumScreener()):
        result = screener.run(df)
        assert len(result) == 1
        assert result.iloc[0]["symbol"] == "SWING_STOCK"
