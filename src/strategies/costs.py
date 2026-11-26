"""
Transaction Costs and Slippage Engine
======================================
Implements realistic cost modeling for NSE cash equity delivery trades:
- NSE Cash Transaction Charges: ₹307 per crore total (0.00307%, transaction 306.99 + IPFT 0.01)
  per NSE circular NSE/FA/73061 (issued 27 Feb 2026, effective 1 Mar 2026).
- Securities Transaction Tax (STT): 0.1% on delivery buy and sell (Finance Act).
- Stamp Duty: 0.015% on buy only (Indian Stamp Act).
- SEBI Turnover Fee: ₹10 per crore (0.0001%).
- GST: 18% on (brokerage + exchange charges + SEBI charges).
- Depository Participant (DP) Charge: broker-specific, charged once per scrip per sell day.
- Execution Slippage: applied to execution fills (default: 10 bps, ``default_slippage_bps``).
- Short-Term Capital Gains (STCG): 20% under Section 111A of Income-tax Act (user parameter).

Every rate lives in ``config/costs.json`` and nowhere else. The file is read once and cached.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from typing import Any

from config.settings import PROJECT_ROOT

COSTS_CONFIG_PATH = PROJECT_ROOT / "config" / "costs.json"

# Every rate the calculators read. A costs.json without one of them is rejected.
REQUIRED_RATE_KEYS = (
    "exchange_turnover_rate",
    "sebi_turnover_rate",
    "stt_buy_rate",
    "stt_sell_rate",
    "stamp_duty_rate",
    "gst_rate",
    "stcg_tax_rate",
    "dp_charge_per_scrip_day",
    "default_brokerage_per_order",
    "default_slippage_bps",
)


@cache
def load_cost_rates() -> dict[str, Any]:
    """Return the rates from config/costs.json (read once, then cached).

    There is no built-in copy of the rates on purpose: a second copy can go stale without anyone
    noticing. A missing, unreadable or incomplete file raises RuntimeError naming the problem.
    In tests, patch ``COSTS_CONFIG_PATH`` and call ``load_cost_rates.cache_clear()``.
    """
    try:
        with COSTS_CONFIG_PATH.open("r", encoding="utf-8") as f:
            rates = json.load(f)["rates"]
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        raise RuntimeError(f"Cannot read the cost rates from {COSTS_CONFIG_PATH}: {exc}") from exc
    missing = [key for key in REQUIRED_RATE_KEYS if key not in rates]
    if missing:
        raise RuntimeError(f"{COSTS_CONFIG_PATH} is missing rates: {', '.join(missing)}")
    return rates


@dataclass(frozen=True)
class TradeCosts:
    """Detailed itemized cost breakdown for a single buy or sell execution."""

    turnover: float
    fill_price: float
    slippage_cost: float
    brokerage: float
    exchange_fee: float
    sebi_fee: float
    stt: float
    stamp_duty: float
    gst: float
    dp_charge: float

    @property
    def total_statutory(self) -> float:
        """Sum of exchange, SEBI, STT, Stamp Duty, and GST."""
        return round(self.exchange_fee + self.sebi_fee + self.stt + self.stamp_duty + self.gst, 2)

    @property
    def total_cash_outflow(self) -> float:
        """Total direct fees paid (brokerage + statutory + DP charge)."""
        return round(self.brokerage + self.total_statutory + self.dp_charge, 2)

    @property
    def total_friction(self) -> float:
        """Total economic drag (slippage + all fees)."""
        return round(self.slippage_cost + self.total_cash_outflow, 2)


def calculate_buy_costs(
    price: float,
    shares: int,
    slippage_bps: float | None = None,
    brokerage: float = 0.0,
    rates: dict[str, Any] | None = None,
) -> TradeCosts:
    """Calculate execution costs and statutory fees for a delivery buy order."""
    r = rates or load_cost_rates()
    slippage_bps = r["default_slippage_bps"] if slippage_bps is None else slippage_bps
    slippage_pct = slippage_bps / 10000.0
    fill_price = price * (1.0 + slippage_pct)
    turnover = fill_price * shares
    slippage_cost = (fill_price - price) * shares

    exch_rate = float(r["exchange_turnover_rate"])
    sebi_rate = float(r["sebi_turnover_rate"])
    stt_rate = float(r["stt_buy_rate"])
    stamp_rate = float(r["stamp_duty_rate"])
    gst_rate = float(r["gst_rate"])

    exchange_fee = round(turnover * exch_rate, 2)
    sebi_fee = round(turnover * sebi_rate, 2)
    stt = round(turnover * stt_rate, 2)
    stamp_duty = round(turnover * stamp_rate, 2)
    gst = round((brokerage + exchange_fee + sebi_fee) * gst_rate, 2)

    return TradeCosts(
        turnover=round(turnover, 2),
        fill_price=round(fill_price, 4),
        slippage_cost=round(slippage_cost, 2),
        brokerage=round(brokerage, 2),
        exchange_fee=exchange_fee,
        sebi_fee=sebi_fee,
        stt=stt,
        stamp_duty=stamp_duty,
        gst=gst,
        dp_charge=0.0,  # DP charge only applies on sell
    )


def calculate_sell_costs(
    price: float,
    shares: int,
    slippage_bps: float | None = None,
    brokerage: float = 0.0,
    dp_charge: float = 0.0,
    rates: dict[str, Any] | None = None,
) -> TradeCosts:
    """Calculate execution costs and statutory fees for a delivery sell order."""
    r = rates or load_cost_rates()
    slippage_bps = r["default_slippage_bps"] if slippage_bps is None else slippage_bps
    slippage_pct = slippage_bps / 10000.0
    fill_price = price * (1.0 - slippage_pct)
    turnover = fill_price * shares
    slippage_cost = (price - fill_price) * shares

    exch_rate = float(r["exchange_turnover_rate"])
    sebi_rate = float(r["sebi_turnover_rate"])
    stt_rate = float(r["stt_sell_rate"])
    gst_rate = float(r["gst_rate"])

    exchange_fee = round(turnover * exch_rate, 2)
    sebi_fee = round(turnover * sebi_rate, 2)
    stt = round(turnover * stt_rate, 2)
    stamp_duty = 0.0  # Stamp duty is buy only
    gst = round((brokerage + exchange_fee + sebi_fee) * gst_rate, 2)

    return TradeCosts(
        turnover=round(turnover, 2),
        fill_price=round(fill_price, 4),
        slippage_cost=round(slippage_cost, 2),
        brokerage=round(brokerage, 2),
        exchange_fee=exchange_fee,
        sebi_fee=sebi_fee,
        stt=stt,
        stamp_duty=stamp_duty,
        gst=gst,
        dp_charge=round(dp_charge, 2),
    )


# KEEP: used by the backtest engine (P5); only tests call it today.
def calculate_round_trip_costs(
    buy_price: float,
    sell_price: float,
    shares: int,
    slippage_bps: float | None = None,
    brokerage_per_order: float = 0.0,
    dp_charge_per_scrip_day: float = 0.0,
    include_stcg: bool = False,
    rates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Calculate complete round-trip performance, fees, net profit, and tax."""
    r = rates or load_cost_rates()
    buy = calculate_buy_costs(
        buy_price, shares, slippage_bps=slippage_bps, brokerage=brokerage_per_order, rates=r
    )
    sell = calculate_sell_costs(
        sell_price,
        shares,
        slippage_bps=slippage_bps,
        brokerage=brokerage_per_order,
        dp_charge=dp_charge_per_scrip_day,
        rates=r,
    )

    gross_profit = round((sell_price - buy_price) * shares, 2)
    total_fees = round(buy.total_cash_outflow + sell.total_cash_outflow, 2)
    total_slippage = round(buy.slippage_cost + sell.slippage_cost, 2)
    total_friction = round(total_fees + total_slippage, 2)

    net_profit = round(
        (sell.turnover - buy.turnover) - (buy.total_cash_outflow + sell.total_cash_outflow), 2
    )
    net_return_pct = round((net_profit / buy.turnover) * 100.0, 2) if buy.turnover > 0 else 0.0

    stcg_tax = 0.0
    if include_stcg and net_profit > 0:
        stcg_rate = float(r["stcg_tax_rate"])
        stcg_tax = round(net_profit * stcg_rate, 2)

    profit_after_tax = round(net_profit - stcg_tax, 2)

    return {
        "shares": shares,
        "gross_profit": gross_profit,
        "net_profit": net_profit,
        "profit_after_tax": profit_after_tax,
        "net_return_pct": net_return_pct,
        "total_fees": total_fees,
        "total_slippage": total_slippage,
        "total_friction": total_friction,
        "stcg_tax": stcg_tax,
        "buy_costs": buy,
        "sell_costs": sell,
    }
