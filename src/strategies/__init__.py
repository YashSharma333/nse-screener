"""
Systematic Strategy Engine Package
==================================
Provides trading plan specifications, risk containment primitives, realistic cost models,
signal evaluation, and CLI tooling for NSE equity swing strategies.
"""

from __future__ import annotations

from src.strategies.costs import (
    TradeCosts,
    calculate_buy_costs,
    calculate_round_trip_costs,
    calculate_sell_costs,
    load_cost_rates,
)
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
from src.strategies.rules import evaluate_rule, validate_rule
from src.strategies.signals import (
    evaluate_rules_history,
    evaluate_screener_history,
    to_sparse,
)
from src.strategies.spec import (
    CostModelSpec,
    EntrySpec,
    ExitSpec,
    PositionSizingSpec,
    StrategySpec,
    StrategyStatus,
)

__all__ = [
    "CostModelSpec",
    "EntrySpec",
    "ExitSpec",
    "PositionSizingSpec",
    "StrategySpec",
    "StrategyStatus",
    "TradeCosts",
    "calculate_buy_costs",
    "calculate_round_trip_costs",
    "calculate_sell_costs",
    "evaluate_rule",
    "evaluate_rules_history",
    "evaluate_screener_history",
    "initial_stop",
    "load_cost_rates",
    "portfolio_heat",
    "position_size",
    "r_multiple",
    "risk_per_share",
    "risk_reward",
    "round_to_tick",
    "target_price",
    "to_sparse",
    "trail_next",
    "validate_rule",
]
