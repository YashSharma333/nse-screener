"""
Tests for Risk and Position Sizing Primitives (T15).
"""

from __future__ import annotations

import pytest

from src.strategies.risk import (
    initial_stop,
    portfolio_heat,
    position_size,
    r_multiple,
    risk_per_share,
    risk_reward,
    round_to_tick,
    target_price,
    trail_next,
)


def test_round_to_tick():
    """Verify tick rounding to ₹0.05 increments."""
    assert round_to_tick(102.34) == 102.35
    assert round_to_tick(102.31) == 102.30
    assert round_to_tick(102.325) == 102.35
    assert round_to_tick(-5.0) == 0.0


def test_risk_per_share():
    """Verify monetary risk per share calculation."""
    assert risk_per_share(100.0, 95.0) == 5.0
    with pytest.raises(ValueError, match="strictly greater than stop_price"):
        risk_per_share(100.0, 105.0)  # Stop above entry invalid for long


def test_initial_stop_calculations():
    """Verify stop-loss calculation for ATR, percentage, and support methods."""
    # 1. ATR stop (entry=1000, atr=25, 2.0x ATR = 50 pt stop -> 950.0)
    stop_atr = initial_stop(1000.0, "atr", 2.0, atr=25.0)
    assert stop_atr == 950.0

    # 2. Percentage stop (entry=1000, 5% stop -> 950.0)
    stop_pct = initial_stop(1000.0, "percentage", 5.0)
    assert stop_pct == 950.0

    # 3. Support level stop (support=900, 0.5% below support = 895.5)
    stop_sup = initial_stop(1000.0, "support", 1.0, support_level=900.0)
    assert stop_sup == 895.5

    # Invalid stop cases
    with pytest.raises(ValueError, match="ATR value must be > 0"):
        initial_stop(1000.0, "atr", 2.0, atr=0.0)
    with pytest.raises(ValueError, match="entry_price must be > 0"):
        initial_stop(-10.0, "percentage", 5.0)
    with pytest.raises(ValueError, match="stop_loss_value must be > 0"):
        initial_stop(100.0, "percentage", -5.0)
    with pytest.raises(ValueError, match="support_level must be > 0"):
        initial_stop(100.0, "support", 1.0, support_level=None)
    with pytest.raises(ValueError, match="Unknown stop_loss_type"):
        initial_stop(100.0, "unknown_type", 1.0)
    with pytest.raises(ValueError, match="Calculated stop"):
        initial_stop(100.0, "percentage", 150.0)


def test_target_price_and_risk_reward():
    """Verify target calculation and R:R ratio."""
    # 1. 2R target on 50 pt risk (entry=1000, stop=950) -> target = 1100.0
    tgt_2r = target_price(1000.0, 950.0, "rr_multiple", 2.0)
    assert tgt_2r == 1100.0
    assert risk_reward(1000.0, 950.0, tgt_2r) == 2.0
    assert risk_reward(1000.0, 950.0, 900.0) == 0.0

    # 2. Percentage target (entry=1000, 10% -> 1100.0)
    tgt_pct = target_price(1000.0, 950.0, "percentage", 10.0)
    assert tgt_pct == 1100.0

    # 3. Target None and resistance
    assert target_price(1000.0, 950.0, "none") is None
    tgt_res = target_price(1000.0, 950.0, "resistance", resistance_level=1200.0)
    assert tgt_res == 1200.0

    # Error handling in target price
    with pytest.raises(ValueError, match="target_value must be > 0"):
        target_price(1000.0, 950.0, "percentage", target_value=-5.0)
    with pytest.raises(ValueError, match="target_value is required"):
        target_price(1000.0, 950.0, "rr_multiple", target_value=None)
    with pytest.raises(ValueError, match="target_value is required"):
        target_price(1000.0, 950.0, "percentage", target_value=None)
    with pytest.raises(ValueError, match="resistance_level must be > entry_price"):
        target_price(1000.0, 950.0, "resistance", resistance_level=900.0)
    with pytest.raises(ValueError, match="Unknown target_type"):
        target_price(1000.0, 950.0, "exotic")


def test_position_sizing_risk_budget():
    """Verify position sizing under risk budget and capital constraints."""
    equity = 500_000.0
    risk_pct = 1.0  # 1% risk = ₹5,000
    entry = 1000.0
    stop = 950.0  # ₹50 risk/share

    # Max shares by risk: 5000 / 50 = 100 shares (₹100,000 position = 20% equity <= 25% cap)
    shares = position_size(equity, risk_pct, entry, stop, max_allocation_percent=25.0)
    assert shares == 100

    # If max allocation is tightened to 10% (₹50,000 max = 50 shares)
    shares_capped = position_size(equity, risk_pct, entry, stop, max_allocation_percent=10.0)
    assert shares_capped == 50

    # Fixed amount constraint (₹75,000 max = 75 shares)
    shares_fixed = position_size(
        equity, risk_pct, entry, stop, max_allocation_percent=25.0, fixed_amount=75_000.0
    )
    assert shares_fixed == 75


def test_trailing_stop_ratchet():
    """Trailing stop must only ratchet up and never loosen."""
    current_trail = 950.0
    # Stock rallies to 1100, ATR is 25, 2.0x ATR trail -> 1100 - 50 = 1050
    t1 = trail_next(current_trail, 1100.0, "atr", 2.0, atr=25.0)
    assert t1 == 1050.0

    # Stock pulls back to 1020 (high since entry is still 1100) -> trail remains 1050
    t2 = trail_next(t1, 1100.0, "atr", 2.0, atr=30.0)
    # If wider ATR gave 1100 - 60 = 1040, ratchet holds previous 1050!
    assert t2 == 1050.0

    # Percentage trailing stop
    t_pct = trail_next(current_trail, 1100.0, "percentage", 5.0)
    assert t_pct == 1045.0

    # None and fallback trailing types
    assert trail_next(current_trail, 1100.0, "none", 0.0) == current_trail
    assert trail_next(current_trail, 1100.0, "atr", 2.0, atr=None) == current_trail
    assert trail_next(current_trail, 1100.0, "other", 2.0) == current_trail


def test_r_multiple_and_portfolio_heat():
    """Verify realized R-multiple and cumulative portfolio heat."""
    # Bought at 1000, stop 950 (1R = 50), exited at 1100 -> +2.0R
    assert r_multiple(1100.0, 1000.0, 950.0) == 2.0
    # Exited at 950 (stopped out) -> -1.0R
    assert r_multiple(950.0, 1000.0, 950.0) == -1.0

    # Portfolio heat: 3 positions risking ₹2,000, ₹3,000, ₹1,000 on ₹100,000 equity = 6.0%
    positions = [
        (100.0, 90.0, 200),  # ₹2,000
        (500.0, 470.0, 100),  # ₹3,000
        (250.0, 240.0, 100),  # ₹1,000
    ]
    heat = portfolio_heat(positions, equity=100_000.0)
    assert heat == 6.0
    assert portfolio_heat([], equity=100_000.0) == 0.0
    assert portfolio_heat(positions, equity=0.0) == 0.0

    # Position size edge cases
    assert position_size(0.0, 1.0, 100.0, 90.0) == 0
