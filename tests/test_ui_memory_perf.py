"""
Memory benchmark for UI data loading (G9).
Measures peak memory of full Parquet load vs. snapshot load and single symbol projection.
"""

from __future__ import annotations

import tempfile
import tracemalloc
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from config import settings
from src.computation.cache_reader import read_history, read_snapshot
from src.computation.indicators.calculator import INDICATORS_SCHEMA_VERSION
from src.db.data_service import save_indicator_parquet_cache


@pytest.fixture
def synthetic_parquet_path(monkeypatch):
    """Create a temporary multi-year parquet dataset: 100 symbols x 250 days = 25,000 rows."""
    symbols = [f"SYM_{i:03d}" for i in range(100)]
    dates = pd.date_range("2024-01-01", periods=250, freq="B").strftime("%Y-%m-%d").tolist()

    records = []
    for sym in symbols:
        for d in dates:
            records.append(
                {
                    "symbol": sym,
                    "date": d,
                    "close": 100.0 + float(np.random.uniform(-5, 5)),
                    "volume": 100000.0,
                    "sma_50": 98.0,
                    "sma_200": 95.0,
                    "rsi_14": 55.0,
                    "is_active": True,
                }
            )
    df = pd.DataFrame(records)

    with tempfile.TemporaryDirectory() as tmpdir:
        pq_path = Path(tmpdir) / "indicators.parquet"
        save_indicator_parquet_cache(
            df,
            cache_path=pq_path,
            meta_dict={
                "db_max_date": dates[-1],
                "adj_factors_count": 0,
                "adj_factors_max_detected_at": None,
                "benchmark_max_date": dates[-1],
                "indicators_schema_version": INDICATORS_SCHEMA_VERSION,
            },
        )
        monkeypatch.setattr(settings, "INDICATORS_CACHE_PATH", pq_path)
        yield pq_path


def test_ui_peak_memory_snapshot_vs_full(synthetic_parquet_path):
    """Verify that snapshot load consumes significantly less peak memory than full load."""
    # 1. Full load memory
    tracemalloc.start()
    df_full = pd.read_parquet(synthetic_parquet_path)
    _ = df_full.copy()
    _current, peak_full = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    del df_full

    # 2. Snapshot load memory (PyArrow pushdown filter on latest date)
    tracemalloc.start()
    snapshot, _message = read_snapshot()
    _current, peak_snapshot = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert not snapshot.empty
    assert len(snapshot) == 100  # Only latest date per symbol

    # 3. Single symbol history projection memory
    tracemalloc.start()
    sym_hist = read_history(["SYM_001"], columns=["close", "rsi_14"])
    _current, peak_symbol = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert not sym_hist.empty
    assert len(sym_hist) == 250

    # Verify projections
    assert set(sym_hist.columns) == {"symbol", "date", "close", "rsi_14"}
    assert peak_symbol < peak_full
    assert peak_snapshot < peak_full
