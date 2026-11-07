"""
Tests for NSE Transaction Costs and Realistic Slippage Model (T16).
"""

from __future__ import annotations

from src.strategies.costs import (
    calculate_buy_costs,
    calculate_round_trip_costs,
    calculate_sell_costs,
    load_cost_rates,
)


def test_rates_schedule_verified_constants():
    """Verify that loaded cost rates match official NSE circulars and statutes."""
    rates = load_cost_rates()
    # 0.00307% total per NSE circular NSE/FA/73061 (₹307 per crore)
    assert rates["exchange_turnover_rate"] == 0.0000307
    # ₹10 per crore SEBI turnover fee
    assert rates["sebi_turnover_rate"] == 0.000001
    # 0.1% STT on delivery buy and sell
    assert rates["stt_buy_rate"] == 0.001
    assert rates["stt_sell_rate"] == 0.001
    # 0.015% Stamp duty buy only
    assert rates["stamp_duty_rate"] == 0.00015
    # 18% GST
    assert rates["gst_rate"] == 0.18
    # 20% STCG Section 111A
    assert rates["stcg_tax_rate"] == 0.20


def test_buy_costs_calculation():
    """Verify itemized buy costs on ₹1,00,000 delivery turnover."""
    # Buy 100 shares at ₹1,000 with 0 slippage and 0 brokerage
    buy = calculate_buy_costs(1000.0, 100, slippage_bps=0.0, brokerage=0.0)

    assert buy.turnover == 100_000.0
    # Exchange fee: 100,000 * 0.0000307 = ₹3.07
    assert buy.exchange_fee == 3.07
    # SEBI fee: 100,000 * 0.000001 = ₹0.10
    assert buy.sebi_fee == 0.10
    # STT: 100,000 * 0.001 = ₹100.00
    assert buy.stt == 100.00
    # Stamp duty: 100,000 * 0.00015 = ₹15.00
    assert buy.stamp_duty == 15.00
    # GST: 18% of (0 + 3.07 + 0.10) = 18% of 3.17 = ₹0.57
    assert buy.gst == 0.57
    # DP charge is 0 on buy
    assert buy.dp_charge == 0.0
    # Total statutory: 3.07 + 0.10 + 100.00 + 15.00 + 0.57 = ₹118.74
    assert buy.total_statutory == 118.74


def test_sell_costs_calculation():
    """Verify itemized sell costs including DP charge and no stamp duty."""
    # Sell 100 shares at ₹1,100 with 0 slippage, 0 brokerage, and ₹15 DP charge
    sell = calculate_sell_costs(1100.0, 100, slippage_bps=0.0, brokerage=0.0, dp_charge=15.0)

    assert sell.turnover == 110_000.0
    # Exchange fee: 110,000 * 0.0000307 = ₹3.38
    assert sell.exchange_fee == 3.38
    # SEBI fee: 110,000 * 0.000001 = ₹0.11
    assert sell.sebi_fee == 0.11
    # STT: 110,000 * 0.001 = ₹110.00
    assert sell.stt == 110.00
    # Stamp duty is ₹0 on sell
    assert sell.stamp_duty == 0.0
    # GST: 18% of (3.38 + 0.11) = ₹0.63
    assert sell.gst == 0.63
    # DP charge
    assert sell.dp_charge == 15.0
    # Total direct cash outflow
    assert sell.total_cash_outflow == round(sell.total_statutory + 15.0, 2)


def test_round_trip_costs_and_stcg():
    """Verify round trip profit after slippage, transaction costs, and STCG tax."""
    res = calculate_round_trip_costs(
        buy_price=1000.0,
        sell_price=1100.0,
        shares=100,
        slippage_bps=5.0,  # 0.05% slippage each side
        brokerage_per_order=20.0,
        dp_charge_per_scrip_day=15.0,
        include_stcg=True,
    )

    assert res["gross_profit"] == 10_000.0  # (1100 - 1000) * 100
    assert res["total_slippage"] > 0
    assert res["total_fees"] > 0
    assert res["net_profit"] < res["gross_profit"]
    # STCG is 20% of net profit
    assert res["stcg_tax"] == round(res["net_profit"] * 0.20, 2)
    assert res["profit_after_tax"] == round(res["net_profit"] - res["stcg_tax"], 2)
