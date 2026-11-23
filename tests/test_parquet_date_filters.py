"""Regression tests: the cache BUILDER's read path must work with the real ``date32`` type.

The cache stores ``datetime.date`` values, which Arrow saves as ``date32``. Comparing that column
with a string raises ``ArrowNotImplementedError``; the snapshot loader used to swallow the error,
recompute all indicators and rewrite the cache on every page load. (The dashboard's own reader is
tested in ``test_cache_reader.py``.)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import src.db.data_service as dsv

DAYS = [d.date() for d in pd.bdate_range("2025-01-01", periods=30)]
SYMBOLS = ["AAA", "BBB", "CCC"]


def _frame() -> pd.DataFrame:
    rows = [
        {"symbol": s, "date": d, "close": 100.0 + i + k, "volume": 1000 + i}
        for k, s in enumerate(SYMBOLS)
        for i, d in enumerate(DAYS)
    ]
    return pd.DataFrame(rows)


def _meta() -> dict[str, Any]:
    return {
        "db_max_date": DAYS[-1].isoformat(),
        "adj_factors_count": 0,
        "adj_factors_max_detected_at": None,
        "benchmark_max_date": None,
    }


@pytest.fixture
def real_cache(tmp_path: Path) -> Path:
    path = tmp_path / "indicators.parquet"
    dsv.save_indicator_parquet_cache(_frame(), cache_path=path, meta_dict=_meta())
    return path


def _no_recompute(*_args: Any, **_kwargs: Any) -> None:
    raise AssertionError("cache was ignored and the database was queried")


def test_snapshot_only_reads_latest_date_from_cache(
    real_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dsv, "check_connection", lambda: True)
    monkeypatch.setattr(dsv, "get_db_cache_metadata", lambda *a, **k: _meta())
    monkeypatch.setattr(dsv, "load_raw_market_data", _no_recompute)

    snapshot, history, is_live, msg = dsv.load_indicator_snapshot(
        cache_path=real_cache, snapshot_only=True
    )

    assert len(snapshot) == len(SYMBOLS)
    assert set(snapshot["date"]) == {DAYS[-1]}
    assert history.empty
    assert is_live is True
    assert "Parquet Cache" in msg


def test_full_cache_read_does_not_recompute(
    real_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dsv, "check_connection", lambda: True)
    monkeypatch.setattr(dsv, "get_db_cache_metadata", lambda *a, **k: _meta())
    monkeypatch.setattr(dsv, "load_raw_market_data", _no_recompute)

    snapshot, history, _live, _msg = dsv.load_indicator_snapshot(cache_path=real_cache)

    assert len(snapshot) == len(SYMBOLS)
    assert len(history) == len(SYMBOLS) * len(DAYS)
