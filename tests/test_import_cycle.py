"""Regression test for issue 126: importing the strategy package must not hide a later import.

It runs in a fresh Python process because the bug depends on which module is imported FIRST;
inside the test run other modules are already loaded and would hide it.
"""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_conditions_and_strategies_import_in_either_order():
    code = (
        "import src.strategies.cli\nfrom src.conditions import evaluate\nprint(evaluate.__name__)\n"
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
    assert "evaluate" in result.stdout
