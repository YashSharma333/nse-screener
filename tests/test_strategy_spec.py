"""
Tests for Strategy Specification Validation and Serialization (T14).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.strategies.spec import (
    CostModelSpec,
    EntrySpec,
    ExitSpec,
    PositionSizingSpec,
    StrategySpec,
)


def test_valid_strategy_spec_creation():
    """Verify instantiating a valid StrategySpec with full defaults and custom params."""
    spec = StrategySpec(
        name="Swing Volume Pro",
        version="1.0",
        description="Flagship swing volume expansion strategy with 2R profit target.",
        status="candidate",
        entry=EntrySpec(
            screener_key="swing_volume",
            inline_rules=["rsi_14 < 65"],
        ),
        exit=ExitSpec(
            stop_loss_type="atr",
            stop_loss_value=2.0,
            target_type="rr_multiple",
            target_value=2.0,
            move_to_breakeven_at_r=1.0,
            max_holding_days=15,
        ),
        sizing=PositionSizingSpec(
            risk_percent=1.0,
            max_positions=5,
        ),
        costs=CostModelSpec(
            slippage_bps=5.0,
            dp_charge_per_scrip_day=15.0,
        ),
    )

    assert spec.name == "Swing Volume Pro"
    assert spec.status == "candidate"
    assert spec.exit.move_to_breakeven_at_r == 1.0
    assert len(spec.spec_hash) == 64


def test_strategy_spec_mandatory_stop_loss_validation():
    """Missing or invalid stop-loss parameters must raise ValidationError."""
    # 1. Missing stop_loss_type
    with pytest.raises(ValidationError):
        ExitSpec(stop_loss_value=2.0)  # type: ignore

    # 2. Negative stop_loss_value
    with pytest.raises(ValidationError):
        ExitSpec(stop_loss_type="atr", stop_loss_value=-1.0)

    # 3. Target value missing when target_type is not none
    with pytest.raises(ValidationError, match="target_value must be > 0"):
        ExitSpec(stop_loss_type="atr", stop_loss_value=2.0, target_type="rr_multiple")


def test_strategy_spec_screener_key_validation():
    """Screener key must exist in ScreenerRegistry."""
    with pytest.raises(ValidationError, match="not registered in ScreenerRegistry"):
        EntrySpec(screener_key="completely_fake_screener_key")

    # A key that looks like a custom screener but is not registered is rejected too
    with pytest.raises(ValidationError, match="not registered in ScreenerRegistry"):
        EntrySpec(screener_key="custom_my_breakout")


def test_strategy_spec_holding_days_bounds():
    """Holding period must be between 1 and 30 days for swing strategy."""
    with pytest.raises(ValidationError):
        ExitSpec(stop_loss_type="percentage", stop_loss_value=5.0, max_holding_days=0)

    with pytest.raises(ValidationError):
        ExitSpec(stop_loss_type="percentage", stop_loss_value=5.0, max_holding_days=31)


def test_strategy_spec_serialization_round_trip():
    """Verify JSON export, from_json loading from a string and a file, and spec_hash determinism."""
    spec = StrategySpec(
        name="RSI Momentum Swing",
        description="Fast momentum shift with trailing ATR stop.",
        status="approved",
        entry=EntrySpec(screener_key="rsi_momentum"),
        exit=ExitSpec(
            stop_loss_type="atr",
            stop_loss_value=1.5,
            trailing_stop_type="atr",
            trailing_stop_value=1.5,
            max_holding_days=10,
        ),
    )

    json_str = spec.to_json()
    loaded_from_str = StrategySpec.from_json(json_str)
    assert loaded_from_str.spec_hash == spec.spec_hash
    assert loaded_from_str.name == spec.name

    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = Path(tmpdir) / "strat.json"
        file_path.write_text(spec.to_json(), encoding="utf-8")
        loaded_from_file = StrategySpec.from_json(file_path)
        assert loaded_from_file.spec_hash == spec.spec_hash
