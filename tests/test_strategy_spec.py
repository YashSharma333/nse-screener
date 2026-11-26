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

NUM = {"type": "number", "value": 1}
SIMPLE_ENTRY = {
    "type": "compare",
    "op": ">",
    "left": {"type": "field", "name": "close"},
    "right": {"type": "indicator", "name": "sma", "params": {"length": 50}},
}


def test_valid_strategy_spec_creation():
    """Verify instantiating a valid StrategySpec with full defaults and custom params."""
    spec = StrategySpec(
        name="Swing Volume Pro",
        version="1.0",
        description="Flagship swing volume expansion strategy with 2R profit target.",
        status="candidate",
        entry=EntrySpec(
            condition=SIMPLE_ENTRY,
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


def test_entry_rejects_the_removed_screener_key_and_rule_text():
    """screener_key and inline_rules were removed. The error says what to write instead."""
    with pytest.raises(ValidationError, match=r"entry\.condition"):
        EntrySpec.model_validate({"screener_key": "swing_volume", "condition": SIMPLE_ENTRY})
    with pytest.raises(ValidationError, match=r"entry\.condition"):
        EntrySpec.model_validate({"inline_rules": ["close > sma_50"]})


def test_entry_needs_a_true_false_condition():
    """A bare number part cannot decide a signal."""
    with pytest.raises(ValidationError, match="true/false"):
        EntrySpec.model_validate({"condition": {"type": "field", "name": "close"}})
    with pytest.raises(ValidationError):
        EntrySpec.model_validate({})  # an entry with no condition would signal nothing or all


def test_entry_condition_errors_say_where_the_mistake_is():
    bad = {"type": "compare", "op": ">", "left": {"type": "field", "name": "vwap"}, "right": NUM}
    with pytest.raises(ValidationError, match=r"condition\.compare\.left\.field\.name"):
        EntrySpec.model_validate({"condition": bad})


def test_exit_rules_are_condition_trees_not_text():
    with pytest.raises(ValidationError, match="condition trees"):
        ExitSpec(stop_loss_type="percentage", stop_loss_value=5.0, exit_rules=["close < sma_50"])
    with pytest.raises(ValidationError, match="true/false"):
        ExitSpec(stop_loss_type="percentage", stop_loss_value=5.0, exit_rules=[NUM])
    spec = ExitSpec(stop_loss_type="percentage", stop_loss_value=5.0, exit_rules=[SIMPLE_ENTRY])
    assert spec.exit_rules[0].type == "compare"


def test_library_strategy_with_screener_key_is_rejected():
    """An old strategy file that still has screener_key fails to load with a helpful message."""
    old = (
        '{"name": "Old", "description": "x", "entry": {"screener_key": "swing_volume"},'
        ' "exit": {"stop_loss_type": "percentage", "stop_loss_value": 4.0}}'
    )
    with pytest.raises(ValidationError, match="screener_key"):
        StrategySpec.from_json(old)


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
        entry=EntrySpec(condition=SIMPLE_ENTRY),
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


def test_every_library_strategy_loads_with_a_condition_tree():
    """The shipped strategies are valid and every entry is a group of conditions."""
    from config.settings import PROJECT_ROOT

    files = sorted((PROJECT_ROOT / "strategies" / "library").glob("*.json"))
    assert files
    for f in files:
        spec = StrategySpec.from_json(f)
        assert spec.entry.condition.type == "group", f.name
        assert spec.entry.condition.mode == "all", f.name
        # saving and loading the spec again changes nothing
        assert StrategySpec.from_json(spec.to_json()).spec_hash == spec.spec_hash, f.name
