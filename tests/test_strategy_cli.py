"""
Tests for Strategy CLI Interface (T17, CL-24C).
===============================================
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from config.settings import PROJECT_ROOT
from src.strategies.cli import cmd_list, cmd_signals, cmd_validate, main

LIBRARY = PROJECT_ROOT / "strategies" / "library"
READ_HISTORY = "src.strategies.cli.cache_reader.read_history"


def _strategy_file(tmp_path: Path, stop_type: str = "atr") -> Path:
    """A small strategy: enter when the close is above 100. Stop type is chosen by the test."""
    spec = {
        "name": "Close above 100",
        "description": "test strategy",
        "entry": {
            "condition": {
                "type": "compare",
                "op": ">",
                "left": {"type": "field", "name": "close"},
                "right": {"type": "number", "value": 100},
            }
        },
        "exit": {"stop_loss_type": stop_type, "stop_loss_value": 2.0},
    }
    path = tmp_path / "strategy.json"
    path.write_text(json.dumps(spec))
    return path


def _history(days: int = 30) -> pd.DataFrame:
    """Two stocks, many days: INFY trades above 100 on the last day, TCS below."""
    dates = list(pd.bdate_range("2026-02-01", periods=days).date)
    frames = []
    for symbol, last_close in (("INFY", 150.0), ("TCS", 90.0)):
        close = pd.Series([last_close - 1.0] * (days - 1) + [last_close])
        frames.append(
            pd.DataFrame(
                {
                    "symbol": symbol,
                    "date": dates,
                    "open": close,
                    "high": close + 2.0,
                    "low": close - 2.0,
                    "close": close,
                    "volume": 500_000.0,
                    "market_cap": 9_000.0,
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def test_cli_validate_success(capsys: pytest.CaptureFixture[str]):
    """Verify validation of a valid strategy JSON."""
    exit_code = cmd_validate(str(LIBRARY / "swing_volume_atr_trail.json"))
    assert exit_code == 0
    captured = capsys.readouterr().out
    assert "Strategy Specification Validated Successfully" in captured
    assert "Swing Volume ATR Trailing" in captured
    assert "8 parts" in captured


def test_cli_validate_nonexistent(capsys: pytest.CaptureFixture[str]):
    """Validation fails gracefully on missing file."""
    exit_code = cmd_validate("nonexistent_strategy.json")
    assert exit_code == 1
    captured = capsys.readouterr().out
    assert "File not found" in captured


def test_cli_list(capsys: pytest.CaptureFixture[str]):
    """Verify listing library strategies."""
    exit_code = cmd_list()
    assert exit_code == 0
    captured = capsys.readouterr().out
    assert "Strategy Name" in captured
    assert "Swing Volume ATR Trailing" in captured


def test_cli_signals_no_history(capsys: pytest.CaptureFixture[str]):
    """Verify signal command behavior when the cache has no history."""
    with patch(READ_HISTORY, return_value=pd.DataFrame()):
        exit_code = cmd_signals(str(LIBRARY / "swing_volume_atr_trail.json"))
    assert exit_code == 1
    assert "No market data available" in capsys.readouterr().out


def test_cli_signals_shows_the_stocks_the_condition_selects(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    """Only INFY passes on the newest day, and its stop comes from the computed ATR."""
    with patch(READ_HISTORY, return_value=_history()):
        exit_code = cmd_signals(str(_strategy_file(tmp_path)), equity=1_000_000.0)
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "STRATEGY SIGNAL RADAR" in out
    assert "INFY" in out and "TCS" not in out
    # ATR of INFY: every bar spans 4 (high - low), so the stop is 150 - 2.0 * ATR(about 4) = 142
    assert "₹142.00" in out


def test_cli_signals_missing_columns_for_atr_raise(tmp_path: Path):
    """An ATR stop needs high, low and close. Without them stop instead of guessing a number."""
    history = _history().drop(columns=["high", "low"])
    with patch(READ_HISTORY, return_value=history), pytest.raises(ValueError, match="high"):
        cmd_signals(str(_strategy_file(tmp_path)))


def test_cli_signals_unknown_atr_is_reported_not_guessed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    """A stock with fewer than 14 days of history has no ATR: say so, never use close * 0.02."""
    with patch(READ_HISTORY, return_value=_history(days=5)):
        cmd_signals(str(_strategy_file(tmp_path)))
    out = capsys.readouterr().out
    assert "ATR unknown" in out
    assert "₹147.00" not in out  # 150 * (1 - 2 * 0.02) would be 144; no guessed stop appears
    assert "₹144.00" not in out


def test_cli_signals_percentage_stop_needs_no_atr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    with patch(READ_HISTORY, return_value=_history(days=5)):
        exit_code = cmd_signals(str(_strategy_file(tmp_path, stop_type="percentage")))
    assert exit_code == 0
    assert "₹147.00" in capsys.readouterr().out  # 150 * (1 - 2 / 100)


def test_cli_main_dispatch():
    """Test CLI main arg parsing and dispatch."""
    strategy_path = str(LIBRARY / "swing_volume_atr_trail.json")
    with patch("sys.argv", ["cli", "validate", strategy_path]):
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
