"""
Strategy Engine CLI Interface
=============================
Provides command-line utilities for validating strategy JSON specifications,
listing library strategies, and evaluating live signals with risk-based sizing.

Usage:
------
python -m src.strategies.cli list
python -m src.strategies.cli validate strategies/library/swing_volume_atr_trail.json
python -m src.strategies.cli signals strategies/library/swing_volume_atr_trail.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from config.settings import PROJECT_ROOT, setup_logging
from src.db.data_service import load_indicator_snapshot
from src.screeners.registry import get_screener
from src.strategies.risk import initial_stop, position_size, target_price
from src.strategies.signals import evaluate_rules_history, evaluate_screener_history, to_sparse
from src.strategies.spec import StrategySpec

STRATEGIES_DIR = PROJECT_ROOT / "strategies" / "library"


def cmd_validate(path: str) -> int:
    """Validate a strategy JSON specification file."""
    p = Path(path)
    if not p.is_file():
        print(f"Error: File not found: {path}")
        return 1

    try:
        spec = StrategySpec.from_json(p)
        print("✓ Strategy Specification Validated Successfully")
        print("=" * 60)
        print(f"Name:        {spec.name} (v{spec.version})")
        print(f"Status:      {spec.status.upper()}")
        print(f"Author:      {spec.author}")
        print(f"Description: {spec.description}")
        print(f"Universe:    {spec.universe}")
        print("-" * 60)
        print("ENTRY LOGIC:")
        print(f"  Screener:  {spec.entry.screener_key}")
        print(f"  Timing:    {spec.entry.timing}")
        print(f"  Rules:     {spec.entry.inline_rules or 'None'}")
        print("-" * 60)
        print("EXIT LOGIC (RISK CONTAINMENT):")
        print(f"  Stop-Loss: {spec.exit.stop_loss_type} (value: {spec.exit.stop_loss_value})")
        print(f"  Target:    {spec.exit.target_type} (value: {spec.exit.target_value})")
        print(
            f"  Trailing:  {spec.exit.trailing_stop_type} (value: {spec.exit.trailing_stop_value})"
        )
        print(
            f"  Breakeven: {spec.exit.breakeven} (at {spec.exit.move_to_breakeven_at_r}R)"
            if spec.exit.breakeven
            else "  Breakeven: False"
        )
        print(f"  Max Days:  {spec.exit.max_holding_days} sessions")
        print("-" * 60)
        print("SIZING & COSTS:")
        print(f"  Method:    {spec.sizing.method} (risk: {spec.sizing.risk_percent}%)")
        print(f"  Max Pos:   {spec.sizing.max_positions}")
        print(f"  Slippage:  {spec.costs.slippage_bps} bps")
        print("-" * 60)
        print(f"Spec Hash:   {spec.spec_hash}")
        print("=" * 60)
        return 0
    except Exception as exc:
        print(f"✗ Validation Failed: {exc}")
        return 1


def cmd_list() -> int:
    """List all available strategies in strategies/library/."""
    if not STRATEGIES_DIR.exists():
        print("No strategies directory found.")
        return 0

    json_files = sorted(STRATEGIES_DIR.glob("*.json"))
    if not json_files:
        print("No strategy specifications found in strategies/library/.")
        return 0

    print("=" * 80)
    print(f"{'Strategy Name':<30} {'Status':<10} {'Entry Screener':<18} {'Stop':<10} {'Hash':<8}")
    print("-" * 80)

    for f in json_files:
        try:
            spec = StrategySpec.from_json(f)
            stop_str = f"{spec.exit.stop_loss_type[:3]}:{spec.exit.stop_loss_value}"
            print(
                f"{spec.name[:28]:<30} {spec.status:<10} "
                f"{spec.entry.screener_key[:16]:<18} {stop_str:<10} {spec.spec_hash[:8]}"
            )
        except Exception as exc:
            print(f"{f.name:<30} ERROR      {str(exc)[:35]}")
    print("=" * 80)
    return 0


def cmd_signals(path: str, equity: float = 500000.0) -> int:
    """Evaluate strategy signals on latest market snapshot and display sizing."""
    p = Path(path)
    if not p.is_file():
        print(f"Error: Strategy file not found: {path}")
        return 1

    try:
        spec = StrategySpec.from_json(p)
    except Exception as exc:
        print(f"Invalid strategy specification: {exc}")
        return 1

    # Load latest snapshot
    snapshot, _, _is_live, msg = load_indicator_snapshot(snapshot_only=True)
    if snapshot.empty:
        print(f"No market data available in database or cache. ({msg})")
        return 1

    screener = get_screener(spec.entry.screener_key)
    signals_df = evaluate_screener_history(screener, snapshot)

    # Evaluate inline rules if defined
    if spec.entry.inline_rules:
        rule_mask = evaluate_rules_history(spec.entry.inline_rules, snapshot)
        signals_df["signal"] = signals_df["signal"] & rule_mask

    sparse = to_sparse(signals_df)

    print("=" * 80)
    print(f"STRATEGY SIGNAL RADAR: {spec.name} (v{spec.version})")
    print(f"Universe: {spec.universe} | Portfolio Equity: ₹{equity:,.2f}")
    print("-" * 80)

    if sparse.empty:
        print(f"No qualifying trade signals detected for {spec.name}.")
        print("=" * 80)
        return 0

    hdr = (
        f"{'Symbol':<12} {'Close':<10} {'Stop Price':<12} "
        f"{'Target':<10} {'Shares':<8} {'Value (₹)':<12}"
    )
    print(hdr)
    print("-" * 80)

    for _idx, row in sparse.iterrows():
        sym = str(row["symbol"])
        close = float(row["close"])
        atr_val = float(row.get("atr_14", close * 0.02))

        try:
            stop_p = initial_stop(
                close,
                spec.exit.stop_loss_type,
                spec.exit.stop_loss_value,
                atr=atr_val,
            )
            tgt_p = target_price(
                close,
                stop_p,
                spec.exit.target_type,
                target_value=spec.exit.target_value,
            )
            shares = position_size(
                equity=equity,
                risk_percent=spec.sizing.risk_percent,
                entry_price=close,
                stop_price=stop_p,
                fixed_amount=spec.sizing.fixed_amount,
            )
            val = shares * close
            tgt_str = f"₹{tgt_p:.2f}" if tgt_p is not None else "Trailing"
            print(
                f"{sym:<12} ₹{close:<9.2f} ₹{stop_p:<11.2f} {tgt_str:<10} {shares:<8} ₹{val:<11.2f}"
            )
        except Exception as exc:
            print(f"{sym:<12} ₹{close:<9.2f} Error: {exc}")

    print("=" * 80)
    return 0


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(
        prog="python -m src.strategies.cli",
        description="Systematic Strategy Engine Management CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # validate
    val_p = subparsers.add_parser("validate", help="Validate strategy JSON specification")
    val_p.add_argument("path", help="Path to strategy JSON file")

    # list
    subparsers.add_parser("list", help="List all available strategies in strategies/library/")

    # signals
    sig_p = subparsers.add_parser("signals", help="Evaluate current strategy signals")
    sig_p.add_argument("path", help="Path to strategy JSON file")
    sig_p.add_argument(
        "--equity", type=float, default=500000.0, help="Portfolio equity for position sizing"
    )

    args = parser.parse_args()

    if args.command == "validate":
        sys.exit(cmd_validate(args.path))
    elif args.command == "list":
        sys.exit(cmd_list())
    elif args.command == "signals":
        sys.exit(cmd_signals(args.path, equity=args.equity))


if __name__ == "__main__":
    main()
