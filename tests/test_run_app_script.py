"""Tests for scripts/run_app.sh: update first, dashboard always opens."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

from config.settings import PROJECT_ROOT


def _make_fake_project(tmp_path: Path) -> tuple[Path, Path]:
    """A throwaway project folder holding the real script and fake python/streamlit."""
    (tmp_path / "scripts").mkdir()
    script = tmp_path / "scripts" / "run_app.sh"
    shutil.copy(PROJECT_ROOT / "scripts" / "run_app.sh", script)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("python", "streamlit"):
        fake = bin_dir / name
        # Record the call; the fake python can be told to fail through FAKE_PYTHON_EXIT.
        fake.write_text(
            f'#!/bin/sh\necho "{name} $@" >> "$CALL_LOG"\nexit ${{FAKE_{name.upper()}_EXIT:-0}}\n'
        )
        fake.chmod(0o755)
    return script, bin_dir


def _run(tmp_path: Path, python_exit: int) -> tuple[subprocess.CompletedProcess, list[str]]:
    script, bin_dir = _make_fake_project(tmp_path)
    log = tmp_path / "calls.log"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CALL_LOG": str(log),
        "FAKE_PYTHON_EXIT": str(python_exit),
    }
    proc = subprocess.run(
        ["bash", str(script), "--server.port", "8502"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return proc, log.read_text().splitlines()


def test_update_runs_before_the_dashboard(tmp_path: Path) -> None:
    proc, calls = _run(tmp_path, python_exit=0)
    assert calls == [
        "python run_pipeline.py --update",
        "streamlit run ui/Dashboard.py --server.port 8502",
    ]
    assert proc.returncode == 0
    assert "WARNING" not in proc.stderr


def test_dashboard_still_opens_when_the_update_fails(tmp_path: Path) -> None:
    proc, calls = _run(tmp_path, python_exit=1)
    assert calls[-1].startswith("streamlit run ui/Dashboard.py")
    assert "LAST GOOD data" in proc.stderr
