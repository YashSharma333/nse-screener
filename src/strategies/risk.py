"""
Risk and Position Sizing Primitives
====================================
Pure mathematical functions for trade risk containment, stop-loss calculation,
position sizing, trailing stops, R-multiples, and portfolio heat monitoring.
Strictly adheres to capital preservation principles for NSE cash equity swing trading.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal


def round_to_tick(price: float, tick_size: float = 0.05) -> float:
    """Round price to nearest exchange tick size using ROUND_HALF_UP.

    NSE equity default tick size is ₹0.05.
    """
    if price <= 0:
        return 0.0
    d_price = Decimal(str(round(price, 6)))
    d_tick = Decimal(str(tick_size))
    ticks = (d_price / d_tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return float(ticks * d_tick)


def risk_per_share(entry_price: float, stop_price: float) -> float:
    """Compute absolute monetary risk per share.

    Formula
    -------
    R_per_share = entry_price - stop_price
    """
    if entry_price <= stop_price:
        raise ValueError(
            f"entry_price ({entry_price}) must be strictly greater than stop_price ({stop_price})."
        )
    return round(entry_price - stop_price, 4)


def initial_stop(
    entry_price: float,
    stop_loss_type: str,
    stop_loss_value: float,
    atr: float | None = None,
    support_level: float | None = None,
    tick_size: float = 0.05,
) -> float:
    """Calculate the initial stop-loss price for a long position.

    Formulas
    --------
    - ATR: stop = entry_price - (stop_loss_value * atr)
    - Percentage: stop = entry_price * (1 - stop_loss_value / 100)
    - Support: stop = support_level * (1 - 0.005)
    """
    if entry_price <= 0:
        raise ValueError("entry_price must be > 0")
    if stop_loss_value <= 0:
        raise ValueError("stop_loss_value must be > 0")

    if stop_loss_type in ("atr", "trailing_atr"):
        if atr is None or atr <= 0:
            raise ValueError(f"ATR value must be > 0 for stop_loss_type='{stop_loss_type}'")
        raw_stop = entry_price - (stop_loss_value * atr)
    elif stop_loss_type in ("percentage", "trailing_pct"):
        raw_stop = entry_price * (1.0 - (stop_loss_value / 100.0))
    elif stop_loss_type == "support":
        if support_level is None or support_level <= 0:
            raise ValueError("support_level must be > 0 for stop_loss_type='support'")
        raw_stop = support_level * 0.995  # 0.5% below key support
    else:
        raise ValueError(f"Unknown stop_loss_type: '{stop_loss_type}'")

    if raw_stop <= 0 or raw_stop >= entry_price:
        raise ValueError(
            f"Calculated stop ({raw_stop:.2f}) must be strictly between 0 and {entry_price:.2f}"
        )

    return round_to_tick(raw_stop, tick_size)


def target_price(
    entry_price: float,
    stop_price: float,
    target_type: str,
    target_value: float | None = None,
    resistance_level: float | None = None,
    tick_size: float = 0.05,
) -> float | None:
    """Calculate profit target price for a long position.

    Formulas
    --------
    - R:R Multiple: target = entry_price + target_value * (entry_price - stop_price)
    - Percentage: target = entry_price * (1 + target_value / 100)
    - Resistance: target = resistance_level
    - None: returns None
    """
    if target_type == "none" or target_type is None:
        return None

    if target_value is not None and target_value <= 0:
        raise ValueError("target_value must be > 0")

    if target_type == "rr_multiple":
        if target_value is None:
            raise ValueError("target_value is required for target_type='rr_multiple'")
        r = risk_per_share(entry_price, stop_price)
        raw_target = entry_price + (target_value * r)
    elif target_type == "percentage":
        if target_value is None:
            raise ValueError("target_value is required for target_type='percentage'")
        raw_target = entry_price * (1.0 + (target_value / 100.0))
    elif target_type == "resistance":
        if resistance_level is None or resistance_level <= entry_price:
            raise ValueError("resistance_level must be > entry_price")
        raw_target = resistance_level
    else:
        raise ValueError(f"Unknown target_type: '{target_type}'")

    return round_to_tick(raw_target, tick_size)


def risk_reward(entry_price: float, stop_price: float, target: float) -> float:
    """Calculate Risk:Reward ratio.

    Formula
    -------
    R:R = (target - entry_price) / (entry_price - stop_price)
    """
    r_risk = risk_per_share(entry_price, stop_price)
    r_gain = target - entry_price
    if r_gain <= 0:
        return 0.0
    return round(r_gain / r_risk, 2)


def position_size(
    equity: float,
    risk_percent: float,
    entry_price: float,
    stop_price: float,
    max_allocation_percent: float = 25.0,
    fixed_amount: float | None = None,
) -> int:
    """Compute number of shares to buy using strict risk-budgeting.

    Formulas
    --------
    - risk_amount = equity * (risk_percent / 100)
    - shares_by_risk = floor(risk_amount / (entry - stop))
    - shares_by_max_cap = floor(equity * (max_allocation / 100) / entry)
    - shares_by_fixed = floor(fixed_amount / entry) [if specified]
    - size = max(0, min(shares_by_risk, shares_by_max_cap, shares_by_fixed))
    """
    if equity <= 0 or entry_price <= 0:
        return 0

    rps = risk_per_share(entry_price, stop_price)
    risk_budget = equity * (risk_percent / 100.0)
    shares_risk = math.floor(risk_budget / rps)

    max_capital = equity * (max_allocation_percent / 100.0)
    shares_cap = math.floor(max_capital / entry_price)

    shares = min(shares_risk, shares_cap)

    if fixed_amount is not None and fixed_amount > 0:
        shares_fixed = math.floor(fixed_amount / entry_price)
        shares = min(shares, shares_fixed)

    return max(0, shares)


def trail_next(
    current_trail: float,
    high_since_entry: float,
    trailing_type: str,
    trailing_value: float,
    atr: float | None = None,
    tick_size: float = 0.05,
) -> float:
    """Compute the next trailing stop level ensuring the stop ratchet never loosens.

    Formulas
    --------
    - ATR: new_stop = high_since_entry - (trailing_value * atr)
    - Percentage: new_stop = high_since_entry * (1 - trailing_value / 100)
    - ratchet: return max(current_trail, new_stop)
    """
    if trailing_type == "none":
        return current_trail

    if trailing_type == "atr":
        if atr is None or atr <= 0:
            return current_trail
        raw = high_since_entry - (trailing_value * atr)
    elif trailing_type == "percentage":
        raw = high_since_entry * (1.0 - (trailing_value / 100.0))
    else:
        return current_trail

    candidate = round_to_tick(raw, tick_size)
    return max(current_trail, candidate)


def r_multiple(exit_price: float, entry_price: float, initial_stop_price: float) -> float:
    """Calculate the realized R-multiple of a completed trade.

    Formula
    -------
    R = (exit_price - entry_price) / (entry_price - initial_stop_price)
    """
    rps = risk_per_share(entry_price, initial_stop_price)
    return round((exit_price - entry_price) / rps, 2)


def portfolio_heat(
    open_positions: Sequence[tuple[float, float, int]],
    equity: float,
) -> float:
    """Compute total open portfolio risk as a percentage of total equity.

    Parameters
    ----------
    open_positions : Sequence[tuple[entry_price, current_stop, shares]]
        List of currently open position tuples.
    equity : float
        Current total portfolio equity.

    Formula
    -------
    heat = sum((entry - stop) * shares) / equity * 100
    """
    if equity <= 0 or not open_positions:
        return 0.0

    total_risk = 0.0
    for entry, stop, shares in open_positions:
        if shares > 0 and entry > stop:
            total_risk += (entry - stop) * shares

    return round((total_risk / equity) * 100.0, 2)
