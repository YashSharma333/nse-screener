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
- Execution Slippage: applied to execution fills (default: 5.0 bps).
- Short-Term Capital Gains (STCG): 20% under Section 111A of Income-tax Act (user parameter).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from config.settings import PROJECT_ROOT

COSTS_CONFIG_PATH = PROJECT_ROOT / "config" / "costs.json"

# Verified Default Rates
DEFAULT_EXCHANGE_RATE = 0.0000307  # ₹307 per crore per NSE/FA/73061 (effective 1 Mar 2026)
DEFAULT_SEBI_RATE = 0.000001  # ₹10 per crore
DEFAULT_STT_BUY_RATE = 0.001  # 0.1% delivery buy
DEFAULT_STT_SELL_RATE = 0.001  # 0.1% delivery sell
DEFAULT_STAMP_DUTY_RATE = 0.00015  # 0.015% delivery buy
DEFAULT_GST_RATE = 0.18  # 18% on (brokerage + exchange + sebi)
DEFAULT_SLIPPAGE_BPS = 5.0  # 5 bps
DEFAULT_STCG_TAX_RATE = 0.20  # 20% Section 111A


def load_cost_rates() -> dict[str, Any]:
    """Load cost rates from config/costs.json with fallback to verified defaults."""
    if COSTS_CONFIG_PATH.exists():
        try:
            with COSTS_CONFIG_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("rates", {})
        except Exception:
            pass
    return {
        "exchange_turnover_rate": DEFAULT_EXCHANGE_RATE,
        "sebi_turnover_rate": DEFAULT_SEBI_RATE,
        "stt_buy_rate": DEFAULT_STT_BUY_RATE,
        "stt_sell_rate": DEFAULT_STT_SELL_RATE,
        "stamp_duty_rate": DEFAULT_STAMP_DUTY_RATE,
        "gst_rate": DEFAULT_GST_RATE,
        "stcg_tax_rate": DEFAULT_STCG_TAX_RATE,
        "dp_charge_per_scrip_day": 0.0,
        "default_brokerage_per_order": 0.0,
        "default_slippage_bps": DEFAULT_SLIPPAGE_BPS,
    }


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
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
    brokerage: float = 0.0,
    rates: dict[str, Any] | None = None,
) -> TradeCosts:
    """Calculate execution costs and statutory fees for a delivery buy order."""
    r = rates or load_cost_rates()
    slippage_pct = slippage_bps / 10000.0
    fill_price = price * (1.0 + slippage_pct)
    turnover = fill_price * shares
    slippage_cost = (fill_price - price) * shares

    exch_rate = float(r.get("exchange_turnover_rate", DEFAULT_EXCHANGE_RATE))
    sebi_rate = float(r.get("sebi_turnover_rate", DEFAULT_SEBI_RATE))
    stt_rate = float(r.get("stt_buy_rate", DEFAULT_STT_BUY_RATE))
    stamp_rate = float(r.get("stamp_duty_rate", DEFAULT_STAMP_DUTY_RATE))
    gst_rate = float(r.get("gst_rate", DEFAULT_GST_RATE))

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
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
    brokerage: float = 0.0,
    dp_charge: float = 0.0,
    rates: dict[str, Any] | None = None,
) -> TradeCosts:
    """Calculate execution costs and statutory fees for a delivery sell order."""
    r = rates or load_cost_rates()
    slippage_pct = slippage_bps / 10000.0
    fill_price = price * (1.0 - slippage_pct)
    turnover = fill_price * shares
    slippage_cost = (price - fill_price) * shares

    exch_rate = float(r.get("exchange_turnover_rate", DEFAULT_EXCHANGE_RATE))
    sebi_rate = float(r.get("sebi_turnover_rate", DEFAULT_SEBI_RATE))
    stt_rate = float(r.get("stt_sell_rate", DEFAULT_STT_SELL_RATE))
    gst_rate = float(r.get("gst_rate", DEFAULT_GST_RATE))

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


def calculate_round_trip_costs(
    buy_price: float,
    sell_price: float,
    shares: int,
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
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
        stcg_rate = float(r.get("stcg_tax_rate", DEFAULT_STCG_TAX_RATE))
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
