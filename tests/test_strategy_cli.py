"""
Tests for Strategy CLI Interface (T17).
========================================
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from config.settings import PROJECT_ROOT
from src.strategies.cli import cmd_list, cmd_signals, cmd_validate, main


def test_cli_validate_success(capsys: pytest.CaptureFixture[str]):
    """Verify validation of a valid strategy JSON."""
    strategy_path = PROJECT_ROOT / "strategies" / "library" / "swing_volume_atr_trail.json"
    exit_code = cmd_validate(str(strategy_path))
    assert exit_code == 0
    captured = capsys.readouterr().out
    assert "Strategy Specification Validated Successfully" in captured
    assert "Swing Volume ATR Trailing" in captured


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


def test_cli_signals_no_snapshot(capsys: pytest.CaptureFixture[str]):
    """Verify signal command behavior when snapshot is empty."""
    strategy_path = PROJECT_ROOT / "strategies" / "library" / "swing_volume_atr_trail.json"
    mock_ret = (pd.DataFrame(), None, False, "Empty")
    with patch("src.strategies.cli.load_indicator_snapshot", return_value=mock_ret):
        exit_code = cmd_signals(str(strategy_path))
        assert exit_code == 1
        captured = capsys.readouterr().out
        assert "No market data available" in captured


def test_cli_signals_with_snapshot(capsys: pytest.CaptureFixture[str]):
    """Verify signal evaluation with mock snapshot data."""
    strategy_path = PROJECT_ROOT / "strategies" / "library" / "swing_volume_atr_trail.json"
    mock_df = pd.DataFrame(
        {
            "symbol": ["INFY", "TCS"],
            "date": [pd.Timestamp("2026-03-01"), pd.Timestamp("2026-03-01")],
            "close": [1500.0, 3200.0],
            "adj_close": [1500.0, 3200.0],
            "adj_open": [1490.0, 3190.0],
            "adj_high": [1510.0, 3210.0],
            "adj_low": [1485.0, 3180.0],
            "volume": [2_000_000, 100_000],
            "volume_sma_20": [1_000_000, 500_000],
            "sma_50": [1400.0, 3500.0],
            "sma_200": [1300.0, 3400.0],
            "ema_20": [1450.0, 3300.0],
            "high_252d_prev": [1600.0, 3600.0],
            "atr_14": [30.0, 60.0],
            "rsi_14": [65.0, 45.0],
        }
    )
    mock_ret = (mock_df, None, True, "OK")
    with patch("src.strategies.cli.load_indicator_snapshot", return_value=mock_ret):
        exit_code = cmd_signals(str(strategy_path), equity=1_000_000.0)
        assert exit_code == 0
        captured = capsys.readouterr().out
        assert "STRATEGY SIGNAL RADAR" in captured


def test_cli_main_dispatch():
    """Test CLI main arg parsing and dispatch."""
    strategy_path = str(PROJECT_ROOT / "strategies" / "library" / "swing_volume_atr_trail.json")
    with patch("sys.argv", ["cli", "validate", strategy_path]):
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
