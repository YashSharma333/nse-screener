"""Regression for issue #66: tests must not write the real cache or reports."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.db import data_service
from src.etl import adjust


def test_default_cache_path_is_sandboxed_during_tests() -> None:
    """Saving with the default path must land in the temporary sandbox, not data/processed."""
    real = Path("data/processed/indicators.parquet").resolve()
    assert data_service.INDICATORS_CACHE_PATH.resolve() != real

    frame = pd.DataFrame({"symbol": ["TCS"], "date": [pd.Timestamp("2026-01-01").date()]})
    data_service.save_indicator_parquet_cache(frame)  # default path

    assert data_service.INDICATORS_CACHE_PATH.exists()
    assert data_service.INDICATORS_META_PATH.exists()


def test_default_reports_dir_is_sandboxed_during_tests() -> None:
    assert adjust.REPORTS_DIR.resolve() != Path("reports").resolve()
