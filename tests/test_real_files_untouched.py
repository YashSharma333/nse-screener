"""Tests must never write real files: issue #66 and task D14 (one place per folder)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd

from config import settings
from src.db import data_service
from src.etl import adjust
from tests.real_files_guard import find_changes, snapshot

REPO = Path(__file__).resolve().parent.parent


def test_default_cache_path_is_sandboxed_during_tests() -> None:
    """Saving with the default path must land in the temporary sandbox, not data/processed."""
    real = REPO / "data" / "processed" / "indicators.parquet"
    assert data_service.INDICATORS_CACHE_PATH.resolve() != real.resolve()

    frame = pd.DataFrame({"symbol": ["TCS"], "date": [pd.Timestamp("2026-01-01").date()]})
    data_service.save_indicator_parquet_cache(frame)  # default path

    assert data_service.INDICATORS_CACHE_PATH.exists()
    assert data_service.INDICATORS_META_PATH.exists()


def test_every_output_folder_is_sandboxed_during_tests() -> None:
    """One value per folder in config.settings; the autouse fixture redirects all of them."""
    for name in (
        "REPORTS_DIR",
        "BENCHMARK_CACHE_DIR",
        "BENCHMARK_NON_TRADING_DAYS_FILE",
        "PRICE_NON_TRADING_DAYS_FILE",
        "BHAVCOPY_ALL_DIR",
        "MCAP_DIR",
        "CORP_ACTIONS_DIR",
    ):
        value = getattr(settings, name).resolve()
        in_real_folder = value.is_relative_to(REPO / "reports") or value.is_relative_to(
            REPO / "data"
        )
        assert not in_real_folder, f"{name} points at a real folder"


def test_default_event_report_lands_in_sandbox() -> None:
    """save_events_report() with no path writes under settings.REPORTS_DIR, not reports/."""
    events = pd.DataFrame(
        [
            {
                "symbol": "X",
                "date": "2026-01-01",
                "factor": 0.5,
                "ratio_kind": "m",
                "source": "s",
                "kinds": "SPLIT",
            }
        ]
    )
    adjust.save_events_report(events)

    assert (settings.REPORTS_DIR / "adj_events_review.csv").exists()


def test_real_defaults_are_absolute_and_inside_the_project() -> None:
    """In a fresh process (no fixture) every default must be absolute, so the current folder
    cannot change where files go (the old Path("reports") did exactly that)."""
    code = (
        "from config import settings as s; "
        "names = ['REPORTS_DIR','BENCHMARK_CACHE_DIR','BENCHMARK_NON_TRADING_DAYS_FILE',"
        "'PRICE_NON_TRADING_DAYS_FILE','BHAVCOPY_ALL_DIR','MCAP_DIR','CORP_ACTIONS_DIR']; "
        "bad = [n for n in names if not getattr(s, n).is_absolute() "
        "or not getattr(s, n).is_relative_to(s.PROJECT_ROOT)]; "
        "print(bad)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]"


def test_guard_detects_a_changed_new_and_removed_file(tmp_path: Path) -> None:
    """Negative control: the guard must flag exactly the kinds of change a leaking test makes."""
    (tmp_path / "reports").mkdir()
    (tmp_path / "data").mkdir()
    kept = tmp_path / "reports" / "kept.csv"
    gone = tmp_path / "data" / "gone.json"
    kept.write_text("a")
    gone.write_text("b")
    before = snapshot(tmp_path)
    assert find_changes(before, snapshot(tmp_path)) == []

    kept.write_text("a longer value")  # overwritten
    (tmp_path / "reports" / "new.csv").write_text("x")  # created
    gone.unlink()  # removed
    problems = find_changes(before, snapshot(tmp_path))

    assert len(problems) == 3
    assert any("kept.csv" in line for line in problems)
    assert any("new.csv" in line for line in problems)
    assert any("removed" in line and "gone.json" in line for line in problems)
