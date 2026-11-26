"""The cache holds only the approved indicator list; everything else is computed on demand (CL-25).

Covers: the list itself, the trimmed cache build, the benchmarks side file and
``cache_reader.load_with_indicators`` (reads the Parquet, never writes it).
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from config import settings
from src.computation import cache_reader
from src.computation.indicators.calculator import INDICATORS_SCHEMA_VERSION, rsi, sma
from src.computation.indicators.resolver import ind
from src.db.data_service import (
    compute_market_indicators,
    load_indicator_snapshot,
    save_benchmarks_parquet,
)
from src.db.models import Base, BenchmarkPrice, DailyPrice

APPROVED_COLUMNS = [
    "sma_close_20",
    "sma_close_50",
    "sma_close_200",
    "ema_close_9",
    "ema_close_10",
    "ema_close_20",
    "ema_close_50",
    "ema_close_200",
    "bollinger_bands_close_20_2_upper",
    "bollinger_bands_close_20_2_lower",
    "sma_volume_20",
    "sma_volume_50",
    "rsi_close_14",
    "window_high_252_max_ago1",
    "window_low_252_min_ago1",
]
BASE_COLUMNS = ["symbol", "date", "series", "open", "high", "low", "close", "volume", "market_cap"]


def _prices(symbols: tuple[str, ...] = ("AAA", "BBB"), days: int = 320) -> pd.DataFrame:
    """Seeded random-walk prices, sorted by symbol then date, with extra columns the cache drops."""
    rng = np.random.default_rng(7)
    frames = []
    for number, symbol in enumerate(symbols):
        close = 100.0 + number * 10 + np.cumsum(rng.normal(0, 1, days))
        frames.append(
            pd.DataFrame(
                {
                    "symbol": symbol,
                    "date": [d.date() for d in pd.bdate_range("2024-01-01", periods=days)],
                    "series": "EQ",
                    "isin": "INE000000000",
                    "open": close - 0.5,
                    "high": close + 1.0,
                    "low": close - 1.0,
                    "close": close,
                    "prev_close": close,
                    "volume": rng.integers(1_000, 5_000, days).astype(float),
                    "market_cap": 1e9,
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


@pytest.fixture
def cache_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A real cache built by the real builder function, set as the default cache path."""
    path = tmp_path / "indicators.parquet"
    compute_market_indicators(_prices()).to_parquet(path, index=False)
    path.with_suffix(".meta.json").write_text('{"db_max_date": "2025-03-10"}')
    monkeypatch.setattr(settings, "INDICATORS_CACHE_PATH", path)
    return path


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------
def test_the_precompute_list_is_the_approved_fifteen_resolver_names() -> None:
    assert [spec.column for spec in settings.PRECOMPUTED_INDICATORS] == APPROVED_COLUMNS


def test_schema_version_is_six() -> None:
    assert INDICATORS_SCHEMA_VERSION == 6


# ---------------------------------------------------------------------------
# The trimmed build
# ---------------------------------------------------------------------------
def test_the_cache_has_exactly_the_base_and_approved_columns() -> None:
    built = compute_market_indicators(_prices())

    assert list(built.columns) == BASE_COLUMNS + APPROVED_COLUMNS


def test_cached_values_equal_the_indicator_functions_run_directly() -> None:
    prices = _prices()
    built = compute_market_indicators(prices)

    pd.testing.assert_series_equal(built["sma_close_50"], sma(prices, length=50), check_names=False)
    pd.testing.assert_series_equal(built["rsi_close_14"], rsi(prices, length=14), check_names=False)


def test_prior_day_high_and_low_leave_out_today() -> None:
    """No look-ahead: a spike on the last day must not be in that day's own stored high."""
    prices = _prices(("AAA",), days=300)
    prices.loc[prices.index[-1], "high"] = 10_000.0
    prices.loc[prices.index[-1], "low"] = 0.5

    built = compute_market_indicators(prices)

    assert built["window_high_252_max_ago1"].iloc[-1] < 1_000.0
    assert built["window_low_252_min_ago1"].iloc[-1] > 1.0


def test_stored_windows_look_back_inside_each_stock() -> None:
    """The first row of the second stock must not see the first stock's last rows."""
    built = compute_market_indicators(_prices(days=300))

    second = built[built["symbol"] == "BBB"]
    assert second["window_high_252_max_ago1"].iloc[:252].isna().all()
    assert second["window_high_252_max_ago1"].iloc[252:].notna().all()


def test_empty_prices_give_an_empty_table() -> None:
    assert compute_market_indicators(pd.DataFrame()).empty


# ---------------------------------------------------------------------------
# Loading on demand (reads only, never writes)
# ---------------------------------------------------------------------------
def test_a_cached_indicator_is_read_from_the_file_not_recomputed(cache_file: Path) -> None:
    """A planted marker value in the file comes back, so the loader used the cached column."""
    table = pd.read_parquet(cache_file)
    table["sma_close_20"] = 123456.0
    table.to_parquet(cache_file, index=False)

    loaded = cache_reader.load_with_indicators([ind("sma", length=20)])

    assert (loaded["sma_close_20"] == 123456.0).all()


def test_an_uncached_indicator_is_computed_and_matches_the_function(cache_file: Path) -> None:
    assert "rsi_close_9" not in pq.read_schema(cache_file).names

    loaded = cache_reader.load_with_indicators([ind("rsi", length=9)])

    expected = rsi(pd.read_parquet(cache_file), length=9)
    assert "rsi_close_9" in loaded.columns
    np.testing.assert_allclose(loaded["rsi_close_9"], expected, equal_nan=True)


def test_asking_twice_leaves_the_file_unchanged(cache_file: Path) -> None:
    before = _digest(cache_file)
    names_before = pq.read_schema(cache_file).names

    cache_reader.load_with_indicators([ind("rsi", length=9)])
    cache_reader.load_with_indicators([ind("rsi", length=9)])

    assert _digest(cache_file) == before
    assert pq.read_schema(cache_file).names == names_before


def test_the_loader_filters_symbols_and_dates(cache_file: Path) -> None:
    last_day = pd.read_parquet(cache_file)["date"].max()

    loaded = cache_reader.load_with_indicators(
        [ind("sma", length=5)], symbols=["BBB"], dates=[last_day]
    )

    assert list(loaded["symbol"]) == ["BBB"]
    assert list(loaded["date"]) == [last_day]


def test_dates_filter_the_answer_but_not_the_history_used(cache_file: Path) -> None:
    """The 5-day average of the last day needs the four days before it."""
    full = pd.read_parquet(cache_file)
    last_day = full["date"].max()

    loaded = cache_reader.load_with_indicators([ind("sma", length=5)], dates=[last_day])

    expected = full[full["symbol"] == "AAA"]["close"].tail(5).mean()
    assert loaded.loc[loaded["symbol"] == "AAA", "sma_close_5"].iloc[0] == pytest.approx(expected)


def test_an_indicator_of_an_indicator_is_computed_on_demand(cache_file: Path) -> None:
    spec = ind("sma", source=ind("rsi", length=9), length=5)

    loaded = cache_reader.load_with_indicators([spec])

    assert spec.column in loaded.columns
    assert loaded[spec.column].notna().any()


def test_without_a_cache_the_loader_returns_an_empty_table() -> None:
    assert cache_reader.load_with_indicators([ind("sma", length=5)]).empty


# ---------------------------------------------------------------------------
# The benchmarks side file
# ---------------------------------------------------------------------------
@pytest.fixture
def benchmark_db(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'bench.db'}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        for code, level in (("NIFTY 500", 22000.0), ("NIFTY 100", 24000.0)):
            session.add(
                BenchmarkPrice(index_code=code, date=date(2024, 1, 1), close=level, source="NSE")
            )
            session.add(
                BenchmarkPrice(
                    index_code=code, date=date(2024, 1, 2), close=level + 10, source="NSE"
                )
            )
        session.commit()
    return engine


def test_benchmarks_file_holds_every_stored_index(
    benchmark_db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "benchmarks.parquet"
    monkeypatch.setattr(settings, "BENCHMARKS_CACHE_PATH", path)

    with (
        patch("src.db.data_service.engine", benchmark_db),
        patch("src.db.data_service.check_connection", return_value=True),
    ):
        save_benchmarks_parquet()

    table = cache_reader.read_benchmarks()
    assert sorted(table["index_code"].unique()) == ["NIFTY 100", "NIFTY 500"]
    assert list(table.columns) == ["index_code", "date", "close"]
    assert len(table) == 4


def test_missing_benchmarks_file_reads_as_an_empty_table() -> None:
    assert cache_reader.read_benchmarks().empty


def test_the_cache_build_writes_the_benchmarks_file(
    benchmark_db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench_path = tmp_path / "out" / "benchmarks.parquet"
    monkeypatch.setattr(settings, "BENCHMARKS_CACHE_PATH", bench_path)
    with sessionmaker(bind=benchmark_db)() as session:
        for day in pd.bdate_range("2024-01-01", periods=30):
            session.add(
                DailyPrice(
                    symbol="AAA", date=day.date(), open=10.0, high=11.0, low=9.0, close=10.0,
                    volume=100,
                )
            )  # fmt: skip
        session.commit()

    with (
        patch("src.db.data_service.engine", benchmark_db),
        patch("src.db.data_service.check_connection", return_value=True),
    ):
        load_indicator_snapshot(
            cache_path=tmp_path / "out" / "indicators.parquet", force_refresh=True
        )

    assert bench_path.exists()
    cached = pq.read_schema(tmp_path / "out" / "indicators.parquet").names
    assert cached == BASE_COLUMNS + APPROVED_COLUMNS
