"""
Tests for Screener Scaffolding and Declarative JSON Extensibility (E1, E3).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from src.screeners.declarative import create_declarative_screener, load_custom_screeners
from src.screeners.registry import get_screener
from src.screeners.scaffold import main as scaffold_main
from src.screeners.scaffold import scaffold_screener


def test_scaffold_generator():
    """Verify that scaffold_screener generates valid Python code and tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root_path = Path(tmpdir)
        mod_p, test_p = scaffold_screener(
            key="custom_trend_burst",
            name="Trend Burst",
            category="breakout",
            description="Testing scaffold generation.",
            status="experimental",
            root_dir=root_path,
        )

        assert mod_p.exists()
        assert test_p.exists()
        content = mod_p.read_text(encoding="utf-8")
        assert "class CustomTrendBurstScreener" in content
        assert 'key = "custom_trend_burst"' in content
        assert "@register_screener" in content


def test_declarative_screener_creation_and_execution():
    """Verify dynamic construction of a Screener from JSON rule definition."""
    spec = {
        "key": "custom_test_filter",
        "name": "Test Declarative Filter",
        "category": "momentum",
        "description": "Custom declarative test filter.",
        "status": "experimental",
        "rules": [
            "close > sma_50",
            "rsi_14 < 60",
        ],
        "evidence_columns": ["close", "sma_50", "rsi_14"],
    }

    cls = create_declarative_screener(spec)
    assert cls.key == "custom_test_filter"
    assert cls.status == "experimental"

    screener = get_screener("custom_test_filter")
    assert screener.name == "Test Declarative Filter"

    df = pd.DataFrame(
        {
            "symbol": ["INFY", "TCS"],
            "close": [1500.0, 3200.0],
            "sma_50": [1400.0, 3500.0],
            "rsi_14": [45.0, 75.0],
        }
    )

    passed = screener.run(df)
    assert len(passed) == 1
    assert passed.iloc[0]["symbol"] == "INFY"

    explained = screener.explain(df)
    assert "passed" in explained.columns
    assert "cond_rule_1" in explained.columns
    assert "cond_rule_2" in explained.columns
    assert explained["passed"].tolist() == [True, False]


def test_declarative_screener_empty_rules_rejected():
    """Declarative screener must define at least one rule."""
    with pytest.raises(ValueError, match="must define at least one rule"):
        create_declarative_screener({"key": "no_rules", "rules": []})


def test_load_custom_screeners_from_directory():
    """Verify loading custom screeners from disk."""
    with tempfile.TemporaryDirectory() as tmpdir:
        p = Path(tmpdir)
        rule_file = p / "my_screener.json"
        rule_file.write_text(
            json.dumps(
                {
                    "key": "custom_disk_screener",
                    "name": "Disk Screener",
                    "rules": ["close > 100"],
                }
            ),
            encoding="utf-8",
        )

        loaded = load_custom_screeners(p)
        assert len(loaded) == 1
        assert loaded[0].key == "custom_disk_screener"


def test_scaffold_cli_main(capsys: pytest.CaptureFixture[str]):
    """Verify CLI entry point for scaffold generator."""
    with (
        tempfile.TemporaryDirectory() as tmpdir,
        patch(
            "sys.argv",
            [
                "scaffold",
                "--key",
                "cli_screener",
                "--name",
                "CLI Screener",
                "--root-dir",
                tmpdir,
            ],
        ),
    ):
        scaffold_main()
        captured = capsys.readouterr().out
        assert "Created Screener module" in captured
