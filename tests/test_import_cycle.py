"""Regression test for issue 126: importing the strategy package must not hide custom screeners.

It runs in a fresh Python process because the bug depends on which module is imported FIRST;
inside the test run other modules are already loaded and would hide it.
"""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_custom_screeners_load_when_strategies_are_imported_first():
    code = (
        "import src.strategies.cli\n"
        "from src.screeners.registry import get_screener\n"
        "print(get_screener('custom_tight_consolidation').key)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert "circular import" not in result.stderr + result.stdout
    assert result.returncode == 0, result.stderr[-500:]
    assert "custom_tight_consolidation" in result.stdout
