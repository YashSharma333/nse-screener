"""The dashboard can only READ the Parquet cache; it never rebuilds it or touches MySQL (CL-06).

Why: the UI used to call the cache builder. When the cache looked stale, a page load queried
MySQL, recomputed 1.5 million rows and overwrote ``indicators.parquet``. Only
``run_pipeline.py --update`` and ``--refresh-cache`` may build the cache now.
"""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
import streamlit as st

from config import settings
from src.db import data_service
from ui import data as ui_data

UI_DIR = Path(__file__).resolve().parent.parent / "ui"

# Modules the UI must never import: they open the database, download files or write the cache.
FORBIDDEN_MODULES = ("src.db", "src.ingestion", "src.etl")


def _imported_modules(path: Path) -> list[str]:
    """Every module name imported anywhere in one file, including imports inside functions."""
    names: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
            # "from src import db" imports the module src.db without naming it in node.module
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_ui_never_imports_db() -> None:
    ui_files = sorted(UI_DIR.rglob("*.py"))
    assert len(ui_files) > 5, "the UI files were not found, so this test would prove nothing"

    offenders = []
    for path in ui_files:
        for module in _imported_modules(path):
            if any(module == bad or module.startswith(bad + ".") for bad in FORBIDDEN_MODULES):
                offenders.append(f"{path.relative_to(UI_DIR.parent)} imports {module}")

    assert offenders == []


def _write_cache(days: list[date], db_max_date: str) -> Path:
    """Write a small real cache (date32 dates) at the sandboxed default path."""
    rows = [
        {"symbol": symbol, "date": day, "close": 100.0 + i, "volume": 1000}
        for symbol in ("AAA", "BBB")
        for i, day in enumerate(days)
    ]
    cache_path = settings.INDICATORS_CACHE_PATH
    data_service.save_indicator_parquet_cache(
        pd.DataFrame(rows),
        meta_dict={
            "db_max_date": db_max_date,
            "adj_factors_count": 0,
            "adj_factors_max_detected_at": None,
            "benchmark_max_date": None,
        },
    )
    return cache_path


@pytest.fixture(autouse=True)
def _fresh_streamlit_cache():
    """Streamlit remembers results between tests; start each test from an empty memory."""
    st.cache_data.clear()
    yield
    st.cache_data.clear()


def _explode(*_args, **_kwargs):
    raise AssertionError("the UI tried to rebuild the cache")


def test_stale_cache_is_not_rebuilt(monkeypatch: pytest.MonkeyPatch) -> None:
    """MySQL is reachable and says it has newer data than the cache: the UI must NOT rebuild."""
    days = [date(2026, 11, 18), date(2026, 11, 19), date(2026, 11, 20)]
    cache_path = _write_cache(days, db_max_date="2026-11-20")
    meta_path = cache_path.with_suffix(".meta.json")
    parquet_before = cache_path.read_bytes()
    meta_before = meta_path.read_bytes()

    # The database "has" a newer day than the cache, so the old code would recompute everything.
    monkeypatch.setattr(data_service, "check_connection", lambda: True)
    monkeypatch.setattr(
        data_service, "get_db_cache_metadata", lambda *a, **k: {"db_max_date": "2026-11-21"}
    )
    monkeypatch.setattr(data_service, "load_raw_market_data", _explode)
    monkeypatch.setattr(data_service, "save_indicator_parquet_cache", _explode)

    result = ui_data.get_snapshot()

    assert set(result["snapshot"]["symbol"]) == {"AAA", "BBB"}
    assert set(result["snapshot"]["date"]) == {date(2026, 11, 20)}
    assert cache_path.read_bytes() == parquet_before
    assert meta_path.read_bytes() == meta_before


def test_missing_cache_is_not_built_and_says_what_to_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """No cache file: an empty table and a clear message, and nothing is written."""
    cache_path = settings.INDICATORS_CACHE_PATH
    assert not cache_path.exists()
    monkeypatch.setattr(data_service, "check_connection", lambda: True)
    monkeypatch.setattr(data_service, "load_raw_market_data", _explode)
    monkeypatch.setattr(data_service, "save_indicator_parquet_cache", _explode)

    result = ui_data.get_snapshot()

    assert result["snapshot"].empty
    assert "python run_pipeline.py --update" in result["message"]
    assert not cache_path.exists()
