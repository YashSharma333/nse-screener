"""Global test configuration and fixtures for nse-screener."""

from __future__ import annotations

import datetime
import sqlite3

# Explicitly register sqlite3 date adapter and converter to address Python 3.12+ deprecation
sqlite3.register_adapter(datetime.date, lambda d: d.isoformat())
sqlite3.register_converter("date", lambda b: datetime.date.fromisoformat(b.decode()))


import pytest  # noqa: E402  (kept next to the fixture that needs it)


@pytest.fixture(autouse=True)
def _keep_real_files_untouched(tmp_path_factory, monkeypatch):
    """No test may write the real indicator cache or the real report CSVs (issue #66).

    Why: a plain ``pytest`` run used to overwrite ``data/processed/indicators.parquet`` with a
    tiny fixture and empty ``reports/*.csv``. The dashboard then found a stale cache and spent
    minutes rebuilding 1.5 million rows on its first page load.

    The paths below are read when a function runs (not when it is defined), so pointing them
    at a temporary folder is enough. Tests that need their own paths still pass them
    explicitly.
    """
    sandbox = tmp_path_factory.mktemp("isolated_real_files")
    monkeypatch.setattr("src.db.data_service.INDICATORS_CACHE_PATH", sandbox / "indicators.parquet")
    monkeypatch.setattr(
        "src.db.data_service.INDICATORS_META_PATH", sandbox / "indicators.meta.json"
    )
    monkeypatch.setattr("src.etl.adjust.REPORTS_DIR", sandbox / "reports")
    monkeypatch.setattr("src.etl.bogus_sessions.PROJECT_ROOT", sandbox)
