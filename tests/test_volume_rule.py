"""The volume rule is Chartink style (CL-24, owner decision LOG-025).

Today's volume is compared with the 20-day average volume AS OF YESTERDAY, so a huge volume today
does not lift the bar it has to clear. The liquidity rule keeps using the latest average.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.screeners.catalog import (
    RsiMomentumScreener,
    SwingMomentumScreener,
    SwingVolumeScreener,
)


def _history_with_today_volume(today_volume: float) -> pd.DataFrame:
    """One stock, 25 trading days: volume 100 on the first 24 days, ``today_volume`` on the last."""
    volumes = [100.0] * 24 + [today_volume]
    dates = list(pd.bdate_range("2026-01-01", periods=25).date)
    return pd.DataFrame(
        {
            "symbol": "AAA",
            "date": dates,
            "series": "EQ",
            "open": 50.0,
            "high": 50.0,
            "low": 50.0,
            "close": 50.0,
            "volume": volumes,
            "market_cap": 5_000.0,
        }
    )


@pytest.mark.parametrize(
    "screener",
    [SwingVolumeScreener(min_volume=50.0), SwingMomentumScreener(min_volume=50.0)],
    ids=["swing_volume", "swing_momentum"],
)
def test_volume_surge_uses_yesterdays_average_not_one_that_includes_today(screener) -> None:
    # Today 152: 1.52x yesterday's 20-day average (100) passes the 1.5x rule. The average that
    # includes today is 102.6, and 152 is only 1.48x of it, which the old rule rejected.
    explained = screener.explain(screener.prepare(_history_with_today_volume(152.0)))
    today = explained.iloc[-1]
    assert bool(today["cond_volume_surge"]) is True


def test_volume_surge_still_fails_below_the_multiplier() -> None:
    screener = SwingVolumeScreener(min_volume=50.0)
    explained = screener.explain(screener.prepare(_history_with_today_volume(149.0)))
    assert bool(explained.iloc[-1]["cond_volume_surge"]) is False


def test_rsi_momentum_volume_surge_uses_yesterdays_average() -> None:
    screener = RsiMomentumScreener(volume_avg_min=50.0)
    explained = screener.explain(screener.prepare(_history_with_today_volume(152.0)))
    assert bool(explained.iloc[-1]["cond_volume_surge"]) is True


def test_liquidity_rule_stays_on_the_latest_average() -> None:
    # The latest average includes today: (19 * 100 + 152) / 20 = 102.6. A floor of 102.0 passes
    # on that average but would fail on yesterday's 100, so this proves which one is used.
    screener = SwingVolumeScreener(min_volume=102.0)
    explained = screener.explain(screener.prepare(_history_with_today_volume(152.0)))
    assert bool(explained.iloc[-1]["cond_min_liquidity"]) is True
