"""
Tests for NSE Transaction Costs and Realistic Slippage Model (T16).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.strategies import costs
from src.strategies.costs import (
    calculate_buy_costs,
    calculate_round_trip_costs,
    calculate_sell_costs,
    load_cost_rates,
)


@pytest.fixture(autouse=True)
def _fresh_rates_cache():
    """The rates are cached; clear before and after so a patched path never leaks."""
    load_cost_rates.cache_clear()
    yield
    load_cost_rates.cache_clear()


def _write_costs_file(path: Path, **overrides: float) -> Path:
    """A minimal costs.json with every required rate; ``overrides`` change single rates."""
    rates = dict.fromkeys(costs.REQUIRED_RATE_KEYS, 0.0)
    rates.update(overrides)
    path.write_text(json.dumps({"rates": rates}), encoding="utf-8")
    return path


def test_rates_come_from_the_patched_file(tmp_path, monkeypatch):
    path = _write_costs_file(tmp_path / "costs.json", stt_buy_rate=0.5)
    monkeypatch.setattr(costs, "COSTS_CONFIG_PATH", path)
    assert load_cost_rates()["stt_buy_rate"] == 0.5
    # changing the file changes the result once the cache is cleared
    _write_costs_file(path, stt_buy_rate=0.25)
    load_cost_rates.cache_clear()
    assert load_cost_rates()["stt_buy_rate"] == 0.25


def test_costs_file_is_opened_only_once(tmp_path, monkeypatch):
    path = _write_costs_file(tmp_path / "costs.json")
    monkeypatch.setattr(costs, "COSTS_CONFIG_PATH", path)
    opens: list[Path] = []
    real_open = Path.open

    def counting_open(self, *args, **kwargs):
        if self == path:
            opens.append(self)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counting_open)
    load_cost_rates()
    load_cost_rates()
    calculate_buy_costs(100.0, 10)
    assert len(opens) == 1


def test_missing_costs_file_raises_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(costs, "COSTS_CONFIG_PATH", tmp_path / "nope.json")
    with pytest.raises(RuntimeError, match=r"nope\.json"):
        load_cost_rates()


def test_broken_costs_file_raises_a_clear_error(tmp_path, monkeypatch):
    path = tmp_path / "costs.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(costs, "COSTS_CONFIG_PATH", path)
    with pytest.raises(RuntimeError, match=r"costs\.json"):
        load_cost_rates()


def test_costs_file_missing_a_rate_raises(tmp_path, monkeypatch):
    path = tmp_path / "costs.json"
    path.write_text(json.dumps({"rates": {"gst_rate": 0.18}}), encoding="utf-8")
    monkeypatch.setattr(costs, "COSTS_CONFIG_PATH", path)
    with pytest.raises(RuntimeError, match="stt_buy_rate"):
        load_cost_rates()


def test_default_slippage_is_10_bps_and_comes_from_the_file():
    """CLAUDE.md rule: 10 bps unless configured. One buy with no argument uses the file value."""
    assert load_cost_rates()["default_slippage_bps"] == 10.0
    buy = calculate_buy_costs(1000.0, 100)  # no slippage argument
    assert buy.fill_price == pytest.approx(1001.0)  # 10 bps above 1000


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
