"""Tests for ui/data.py: the read-only, Streamlit-cached data layer (F5, CL-06)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
import streamlit as st

from config import settings
from src.computation import cache_reader
from src.computation.indicators.resolver import ind
from src.db.data_service import compute_market_indicators
from ui import data as ui_data


@pytest.fixture(autouse=True)
def _fresh_streamlit_cache():
    """Streamlit remembers results between tests; start each test from an empty memory."""
    st.cache_data.clear()
    yield
    st.cache_data.clear()


@pytest.fixture
def cache_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Point the default cache path at a temporary folder; return (parquet, meta) paths."""
    parquet_file = tmp_path / "indicators.parquet"
    monkeypatch.setattr(settings, "INDICATORS_CACHE_PATH", parquet_file)
    return parquet_file, parquet_file.with_suffix(".meta.json")


def test_cache_invalidation_token_computation(cache_files: tuple[Path, Path]) -> None:
    parquet_file, meta_file = cache_files

    # Missing files return (0.0, "missing")
    mtime, meta_hash = ui_data.get_cache_invalidation_token()
    assert mtime == 0.0
    assert meta_hash == "missing"

    # Create parquet and meta files
    parquet_file.write_text("fake parquet data")
    meta_file.write_text(json.dumps({"version": 2, "rows": 100}))

    mtime_1, hash_1 = ui_data.get_cache_invalidation_token()
    assert mtime_1 > 0.0
    assert len(hash_1) == 64  # SHA256 length

    # Changing meta file changes the hash
    meta_file.write_text(json.dumps({"version": 2, "rows": 200}))
    _mtime_2, hash_2 = ui_data.get_cache_invalidation_token()
    assert hash_2 != hash_1


def test_snapshot_is_remembered_until_the_cache_file_changes(
    cache_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    parquet_file, meta_file = cache_files
    parquet_file.write_text("initial")
    meta_file.write_text(json.dumps({"v": 1}))

    reads = []

    def fake_read_snapshot() -> tuple[pd.DataFrame, str]:
        reads.append(1)
        return pd.DataFrame([{"symbol": "TCS", "close": 3500.0}]), "ok"

    monkeypatch.setattr(cache_reader, "read_snapshot", fake_read_snapshot)

    assert not ui_data.get_snapshot()["snapshot"].empty
    assert len(reads) == 1

    # Second call with the same files uses the remembered result
    assert ui_data.get_snapshot()["snapshot"].iloc[0]["symbol"] == "TCS"
    assert len(reads) == 1  # not read again

    # Modify the meta file -> cache token changes -> read again
    meta_file.write_text(json.dumps({"v": 2}))
    assert ui_data.get_snapshot()["snapshot"].iloc[0]["symbol"] == "TCS"
    assert len(reads) == 2  # invalidation fired


def test_empty_snapshot_is_not_kept_for_the_cache_ttl(
    cache_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty result (update still writing the file) must be retried on the next run."""
    parquet_file, meta_file = cache_files
    parquet_file.write_text("x")
    meta_file.write_text(json.dumps({"v": "empty-retry"}))

    answers = iter(
        [
            (pd.DataFrame(), "no cache"),
            (pd.DataFrame([{"symbol": "TCS", "close": 3500.0}]), "ok"),
        ]
    )
    monkeypatch.setattr(cache_reader, "read_snapshot", lambda: next(answers))

    assert ui_data.get_snapshot()["snapshot"].empty
    # Same files, same token: a remembered empty result would be returned again.
    assert not ui_data.get_snapshot()["snapshot"].empty


def test_snapshot_reports_the_data_date_and_drops_cap_bucket(
    cache_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The UI shows market cap (no cap bucket) and the date from indicators.meta.json (D11)."""
    parquet_file, meta_file = cache_files
    parquet_file.write_text("x")
    meta_file.write_text(json.dumps({"db_max_date": "2026-11-20"}))
    fake_snapshot = pd.DataFrame(
        [
            {
                "symbol": "INFY",
                "date": date(2026, 11, 20),
                "close": 1850.0,
                "market_cap": 750_000_000_000.0,
                "cap_bucket": "largecap",
                "index_name": "NIFTY 100",
            }
        ]
    )
    monkeypatch.setattr(cache_reader, "read_snapshot", lambda: (fake_snapshot, "ok"))

    result = ui_data.get_snapshot()

    assert "market_cap" in result["snapshot"].columns
    assert "cap_bucket" not in result["snapshot"].columns
    assert "index_name" not in result["snapshot"].columns
    assert result["as_of"] == "2026-11-20"


def test_empty_cache_dashboard_shows_the_command_and_has_no_button() -> None:
    """An empty dashboard only tells the user what to run; it starts nothing (CL-06/CL-07)."""
    from streamlit.testing.v1 import AppTest

    from config.settings import PROJECT_ROOT

    at = AppTest.from_file(str(PROJECT_ROOT / "ui" / "Dashboard.py"), default_timeout=10)
    at.run()

    assert not at.exception
    assert any(
        "scripts/run_app.sh" in info.value and "python run_pipeline.py --update" in info.value
        for info in at.info
    )
    assert len(at.button) == 0


def _built_cache(parquet_file: Path, days: int = 300) -> pd.DataFrame:
    """A real cache for two stocks written by the real builder function."""
    rows = [
        {
            "symbol": symbol,
            "date": day.date(),
            "series": "EQ",
            "open": 100.0 + i,
            "high": 101.0 + i,
            "low": 99.0 + i,
            "close": 100.0 + i,
            "volume": 1000.0 + i,
            "market_cap": 1e9,
        }
        for symbol in ("AAA", "BBB")
        for i, day in enumerate(pd.bdate_range("2025-01-01", periods=days))
    ]
    built = compute_market_indicators(pd.DataFrame(rows))
    built.to_parquet(parquet_file, index=False)
    parquet_file.with_suffix(".meta.json").write_text(
        json.dumps({"db_max_date": built["date"].max().isoformat()})
    )
    return built


def test_snapshot_gets_the_dashboard_columns_that_are_not_saved(
    cache_files: tuple[Path, Path],
) -> None:
    parquet_file, _meta = cache_files
    built = _built_cache(parquet_file)
    names_before = list(built.columns)

    snapshot = ui_data.get_snapshot()["snapshot"]

    assert len(snapshot) == 2  # one row per stock, the newest day
    row = snapshot[snapshot["symbol"] == "AAA"].iloc[0]
    assert row["percent_change_close_1"] == pytest.approx((399.0 / 398.0 - 1) * 100)  # computed
    assert row["sma_close_50"] > 0  # read from the cache
    assert row["pct_from_52w_high"] == pytest.approx(0.0)  # yesterday's high was 399
    assert row["vol_multiple"] == pytest.approx(1299.0 / (sum(range(1279, 1299)) / 20))
    assert list(pd.read_parquet(parquet_file).columns) == names_before  # nothing was written


def test_symbol_indicators_come_from_the_cache_and_cover_only_that_symbol(
    cache_files: tuple[Path, Path],
) -> None:
    parquet_file, _meta = cache_files
    _built_cache(parquet_file)

    result = ui_data.get_symbol_indicators("BBB", [ind("rsi", length=9), ind("sma", length=50)])

    assert set(result["symbol"]) == {"BBB"}
    assert {"rsi_close_9", "sma_close_50"} <= set(result.columns)
