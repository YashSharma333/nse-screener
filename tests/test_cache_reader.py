"""Tests for src/computation/cache_reader.py: the read-only side of the indicator cache (CL-06).

The reader reads the cache path from ``config.settings`` when it runs; the autouse fixture in
conftest points it at a temporary folder, and these tests replace it with their own file.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from config import settings
from src.computation import cache_reader

DAYS = [d.date() for d in pd.bdate_range("2025-01-01", periods=30)]
SYMBOLS = ["AAA", "BBB", "CCC"]


def _frame() -> pd.DataFrame:
    rows = [
        {"symbol": s, "date": d, "close": 100.0 + i + k, "volume": 1000 + i}
        for k, s in enumerate(SYMBOLS)
        for i, d in enumerate(DAYS)
    ]
    return pd.DataFrame(rows)


@pytest.fixture
def cache_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A real cache (date32 dates) plus its sidecar, set as the default cache path."""
    path = tmp_path / "indicators.parquet"
    _frame().to_parquet(path, index=False)
    path.with_suffix(".meta.json").write_text(f'{{"db_max_date": "{DAYS[-1].isoformat()}"}}')
    monkeypatch.setattr(settings, "INDICATORS_CACHE_PATH", path)
    return path


def test_snapshot_is_the_latest_date_one_row_per_symbol(cache_file: Path) -> None:
    snapshot, message = cache_reader.read_snapshot()

    assert sorted(snapshot["symbol"]) == SYMBOLS
    assert set(snapshot["date"]) == {DAYS[-1]}
    assert "3 symbols" in message


def test_data_date_comes_from_the_meta_file(cache_file: Path) -> None:
    assert cache_reader.read_data_date() == DAYS[-1].isoformat()


@pytest.mark.parametrize("kind", ["date", "timestamp", "text"])
def test_snapshot_filter_works_for_every_stored_date_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """KEEP: Arrow cannot compare a date32 column with a string; older caches use other types."""
    frame = _frame()
    if kind == "timestamp":
        frame["date"] = pd.to_datetime(frame["date"])
    elif kind == "text":
        frame["date"] = frame["date"].map(dt.date.isoformat)
    path = tmp_path / f"{kind}.parquet"
    frame.to_parquet(path, index=False)  # no meta file: the newest date is read from the data
    monkeypatch.setattr(settings, "INDICATORS_CACHE_PATH", path)

    snapshot, _message = cache_reader.read_snapshot()

    assert len(snapshot) == len(SYMBOLS)
    assert set(snapshot["date"]) == {DAYS[-1]}


def test_missing_cache_gives_empty_frame_and_the_command_to_run() -> None:
    assert not settings.INDICATORS_CACHE_PATH.exists()

    snapshot, message = cache_reader.read_snapshot()

    assert snapshot.empty
    assert "python run_pipeline.py --update" in message
    assert cache_reader.read_history(["AAA"]).empty
    assert cache_reader.read_data_date() is None
    assert not settings.INDICATORS_CACHE_PATH.exists()  # reading never creates the file


def test_unreadable_cache_is_reported_not_rebuilt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = tmp_path / "indicators.parquet"
    broken.write_text("this is not a parquet file")
    monkeypatch.setattr(settings, "INDICATORS_CACHE_PATH", broken)

    snapshot, message = cache_reader.read_snapshot()

    assert snapshot.empty
    assert "unreadable" in message
    assert broken.read_text() == "this is not a parquet file"


def test_history_reads_only_the_asked_symbols_and_columns(cache_file: Path) -> None:
    history = cache_reader.read_history(["AAA", "CCC"], columns=["close"])

    assert set(history["symbol"]) == {"AAA", "CCC"}
    assert len(history) == 2 * len(DAYS)
    # symbol and date are always added, so every row can be placed on a stock and a day
    assert list(history.columns) == ["symbol", "date", "close"]
    assert history["date"].min() == DAYS[0]


def test_history_of_none_means_every_stock(cache_file: Path) -> None:
    history = cache_reader.read_history(None, columns=["close"])

    assert set(history["symbol"]) == {"AAA", "BBB", "CCC"}
    assert len(history) == 3 * len(DAYS)


def test_history_without_columns_returns_every_column(cache_file: Path) -> None:
    history = cache_reader.read_history(["BBB"])

    assert set(history.columns) == {"symbol", "date", "close", "volume"}
    assert len(history) == len(DAYS)


def test_fill_missing_market_cap_uses_last_known_value() -> None:
    """Newest session has no market cap yet: use each symbol's last known one (regression)."""
    history = pd.DataFrame(
        {
            "symbol": ["AAA", "AAA", "AAA", "BBB"],
            "date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-05", "2026-01-05"]).date,
            "market_cap": [1.0, 2.0, None, None],
        }
    )
    snapshot = pd.DataFrame(
        {
            "symbol": ["AAA", "BBB"],
            "date": [history["date"].iloc[2]] * 2,
            "market_cap": [None, None],
        }
    )

    result = cache_reader.fill_missing_market_cap(snapshot, history)

    assert result.loc[result.symbol == "AAA", "market_cap"].iloc[0] == 2.0
    assert pd.isna(result.loc[result.symbol == "BBB", "market_cap"].iloc[0])


def test_fill_missing_market_cap_keeps_existing_values() -> None:
    day = pd.Timestamp("2026-01-05").date()
    snapshot = pd.DataFrame({"symbol": ["AAA"], "date": [day], "market_cap": [9.0]})
    history = pd.DataFrame({"symbol": ["AAA"], "date": [day], "market_cap": [5.0]})

    assert cache_reader.fill_missing_market_cap(snapshot, history)["market_cap"].iloc[0] == 9.0


def test_snapshot_fills_market_cap_from_earlier_days(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end: today's row has no market cap, yesterday's does; the snapshot shows it."""
    frame = pd.DataFrame(
        {
            "symbol": ["AAA", "AAA"],
            "date": [DAYS[0], DAYS[1]],
            "market_cap": [5.0, float("nan")],
        }
    )
    path = tmp_path / "indicators.parquet"
    frame.to_parquet(path, index=False)
    monkeypatch.setattr(settings, "INDICATORS_CACHE_PATH", path)

    snapshot, _message = cache_reader.read_snapshot()

    assert snapshot["market_cap"].iloc[0] == 5.0
