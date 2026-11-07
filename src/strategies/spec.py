"""
Strategy Specification Engine
=============================
Defines typed, validated Pydantic v2 schemas for systematic swing strategies.
Enforces strategy lifecycle, mandatory risk controls (stop-loss, holding period limits),
clean percent parameter normalization, and deterministic specification hashing.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.screeners.registry import get_screener_keys
from src.strategies.rules import validate_rule

# Valid strategy lifecycle statuses
StrategyStatus = Literal["draft", "candidate", "approved", "retired"]


class EntrySpec(BaseModel):
    """Specification for trade entry conditions and timing."""

    model_config = ConfigDict(extra="forbid")

    screener_key: str = Field(
        ...,
        description="Key of the registered screener generating candidates (e.g. 'swing_volume').",
    )
    inline_rules: list[str] = Field(
        default_factory=list,
        description="Optional additional rule expressions evaluated with safe AST parser.",
    )
    timing: Literal["next_open", "same_close"] = Field(
        default="next_open",
        description="Order execution timing. Swing strategies fill on next day open.",
    )

    @field_validator("screener_key")
    @classmethod
    def validate_screener_registered(cls, v: str) -> str:
        registered = get_screener_keys()
        # Allow custom prefix for declarative screeners
        if v not in registered and not v.startswith("custom_"):
            raise ValueError(
                f"Screener key '{v}' is not registered in ScreenerRegistry. "
                f"Available screeners: {sorted(registered)}"
            )
        return v

    @field_validator("inline_rules")
    @classmethod
    def validate_rules_syntax(cls, rules: list[str]) -> list[str]:
        for r in rules:
            validate_rule(r)
        return rules


class ExitSpec(BaseModel):
    """Specification for risk containment and trade exit logic."""

    model_config = ConfigDict(extra="forbid")

    stop_loss_type: Literal["atr", "percentage", "support", "trailing_atr", "trailing_pct"] = Field(
        ...,
        description="Mandatory stop-loss calculation model.",
    )
    stop_loss_value: float = Field(
        ...,
        gt=0,
        description="Stop-loss parameter: ATR mult (e.g. 2.0) or percentage (e.g. 5.0 for 5%).",
    )
    target_type: Literal["rr_multiple", "percentage", "resistance", "none"] = Field(
        default="none",
        description="Profit target model. 'rr_multiple' targets a multiple of risk (e.g. 2R).",
    )
    target_value: float | None = Field(
        default=None,
        description="Target parameter: R-multiple (e.g. 2.0) or percentage (e.g. 10.0 for 10%).",
    )
    trailing_stop_type: Literal["none", "atr", "percentage"] = Field(
        default="none",
        description="Trailing stop-loss model.",
    )
    trailing_stop_value: float | None = Field(
        default=None,
        description="Trailing stop parameter (ATR multiplier or percentage).",
    )
    breakeven: bool = Field(
        default=False,
        description="Whether to move stop-loss to entry price once a trigger threshold is reached.",
    )
    move_to_breakeven_at_r: float | None = Field(
        default=None,
        description="R-multiple gain (e.g. 1.0R) at which stop-loss is adjusted to breakeven.",
    )
    max_holding_days: int = Field(
        default=15,
        ge=1,
        le=30,
        description="Maximum holding period in trading sessions (1 to 30 days for swing strategy).",
    )
    exit_rules: list[str] = Field(
        default_factory=list,
        description="Optional conditional exit rules (e.g. 'close < sma_50').",
    )

    @field_validator("exit_rules")
    @classmethod
    def validate_exit_rules(cls, rules: list[str]) -> list[str]:
        for r in rules:
            validate_rule(r)
        return rules

    @model_validator(mode="after")
    def validate_target_and_breakeven(self) -> ExitSpec:
        if self.target_type != "none" and (self.target_value is None or self.target_value <= 0):
            raise ValueError(f"target_value must be > 0 when target_type is '{self.target_type}'.")
        if self.breakeven and self.move_to_breakeven_at_r is None:
            # Default to 1.0R if breakeven is enabled without explicit threshold
            self.move_to_breakeven_at_r = 1.0
        return self


class PositionSizingSpec(BaseModel):
    """Specification for capital allocation and position sizing."""

    model_config = ConfigDict(extra="forbid")

    method: Literal["risk_percent", "fixed_cash", "fixed_amount"] = Field(
        default="risk_percent",
        description="Position sizing methodology.",
    )
    risk_percent: float = Field(
        default=1.0,
        gt=0,
        le=5.0,
        description="Percentage of equity risked per trade (e.g. 1.0 = 1%). Normalized in math.",
    )
    fixed_amount: float = Field(
        default=50000.0,
        gt=0,
        description="Fixed rupee amount allocated per trade for fixed sizing methods.",
    )
    max_positions: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum concurrent open positions allowed in the portfolio.",
    )
    max_portfolio_heat_percent: float = Field(
        default=5.0,
        gt=0,
        le=20.0,
        description="Maximum cumulative open portfolio risk percentage (e.g. 5.0 = 5% total heat).",
    )


class CostModelSpec(BaseModel):
    """Specification for transaction charges, statutory fees, and slippage."""

    model_config = ConfigDict(extra="forbid")

    slippage_bps: float = Field(
        default=5.0,
        ge=0,
        description="Execution slippage in basis points (5.0 bps = 0.05%).",
    )
    dp_charge_per_scrip_day: float = Field(
        default=0.0,
        ge=0,
        description="Broker DP fee per scrip per sell day (broker-specific).",
    )
    exchange_turnover_rate: float = Field(
        default=0.0000307,
        ge=0,
        description="NSE Cash Transaction Charge (0.00307% / ₹307/cr per circular NSE/FA/73061).",
    )
    sebi_turnover_rate: float = Field(
        default=0.000001,
        ge=0,
        description="SEBI turnover fee (₹10 per crore / 0.0001%).",
    )
    stt_buy_rate: float = Field(
        default=0.001,
        ge=0,
        description="Securities Transaction Tax on delivery buy (0.1%).",
    )
    stt_sell_rate: float = Field(
        default=0.001,
        ge=0,
        description="Securities Transaction Tax on delivery sell (0.1%).",
    )
    stamp_duty_rate: float = Field(
        default=0.00015,
        ge=0,
        description="State stamp duty on delivery buy (0.015%).",
    )
    gst_rate: float = Field(
        default=0.18,
        ge=0,
        description="Goods and Services Tax on (brokerage + exchange fee + SEBI fee) (18%).",
    )
    brokerage_per_order: float = Field(
        default=0.0,
        ge=0,
        description="Brokerage charge per order (e.g. ₹0 or ₹20 flat).",
    )
    stcg_tax_rate: float = Field(
        default=0.20,
        ge=0,
        le=1.0,
        description="Short-Term Capital Gains (STCG) tax rate parameter under Section 111A.",
    )


class StrategySpec(BaseModel):
    """Complete, self-contained Strategy Specification."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=3, description="Unique strategy name.")
    version: str = Field(default="1.0", description="Semantic strategy version.")
    description: str = Field(..., description="Plain-language description of strategy intent.")
    author: str = Field(default="trader", description="Strategy author identifier.")
    status: StrategyStatus = Field(
        default="draft",
        description="Lifecycle status: 'draft', 'candidate', 'approved', or 'retired'.",
    )
    universe: str = Field(default="NIFTY 500", description="Constituent benchmark universe.")
    entry: EntrySpec
    exit: ExitSpec
    sizing: PositionSizingSpec = Field(default_factory=PositionSizingSpec)
    costs: CostModelSpec = Field(default_factory=CostModelSpec)

    @property
    def spec_hash(self) -> str:
        """Deterministic SHA256 hash of the canonical serialized strategy specification."""
        raw_dict = self.model_dump(mode="json")
        canonical_json = json.dumps(raw_dict, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    def to_json(self, indent: int = 2) -> str:
        """Serialize strategy to pretty-printed JSON string."""
        return json.dumps(self.model_dump(mode="json"), indent=indent)

    def save(self, filepath: Path | str) -> Path:
        """Persist strategy specification to JSON file."""
        p = Path(filepath)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.to_json(), encoding="utf-8")
        return p

    @classmethod
    def from_json(cls, data_or_path: str | Path | dict[str, Any]) -> StrategySpec:
        """Instantiate StrategySpec from JSON string, dictionary, or file path."""
        if isinstance(data_or_path, (str, Path)):
            p = Path(data_or_path)
            if p.is_file():
                content = p.read_text(encoding="utf-8")
                return cls.model_validate_json(content)
            # Try parsing as JSON string directly
            try:
                return cls.model_validate_json(str(data_or_path))
            except Exception:
                raise FileNotFoundError(
                    f"Strategy specification file not found: {data_or_path}"
                ) from None
        if isinstance(data_or_path, dict):
            return cls.model_validate(data_or_path)
        raise TypeError(f"Unsupported input type for StrategySpec.from_json: {type(data_or_path)}")

    @classmethod
    def export_schema(cls) -> dict[str, Any]:
        """Export standardized JSON Schema for StrategySpec validation."""
        return cls.model_json_schema()
