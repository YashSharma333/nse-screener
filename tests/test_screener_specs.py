"""Screeners declare the indicators they need and the engine provides them (CL-24)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.computation import cache_reader
from src.computation.indicators.resolver import ind
from src.screeners.catalog import RsiMomentumScreener


def _history(rows: int = 320) -> pd.DataFrame:
    """Two rising stocks, sorted by symbol then date, with a volume spike on the last day."""
    rng = np.random.default_rng(3)
    dates = list(pd.bdate_range("2025-01-01", periods=rows).date)
    frames = []
    for symbol, start in (("AAA", 100.0), ("BBB", 50.0)):
        close = start * np.cumprod(1 + rng.normal(0.002, 0.01, rows))
        frames.append(
            pd.DataFrame(
                {
                    "symbol": symbol,
                    "date": dates,
                    "series": "EQ",
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": rng.integers(400_000, 600_000, rows).astype(float),
                    "market_cap": 5_000.0,
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def test_rsi_period_can_be_edited_without_a_key_error() -> None:
    """Before CL-24 a changed rsi_period raised KeyError (the screener read cached rsi_9)."""
    history = _history()
    for period in (9, 7, 14):
        screener = RsiMomentumScreener(rsi_period=period)
        result = screener.screen(history)
        assert len(result) == 2
        assert {"passed", "near_miss"} <= set(result.columns)


def test_screen_evaluates_the_latest_day_one_row_per_stock() -> None:
    history = _history()
    result = RsiMomentumScreener().screen(history)
    assert set(result["date"]) == {history["date"].max()}
    assert sorted(result["symbol"]) == ["AAA", "BBB"]


def test_required_specs_follow_the_parameters() -> None:
    columns = {s.column for s in RsiMomentumScreener(rsi_period=7).required_specs()}
    assert "rsi_close_7" in columns
    assert "rsi_close_9" not in columns
    assert ind("wma", source=ind("rsi", length=7), length=21).column in columns


def test_screen_does_not_change_the_callers_history() -> None:
    history = _history()
    before = list(history.columns)
    RsiMomentumScreener().screen(history)
    assert list(history.columns) == before


def _write_cache(path, dates: list[date]) -> None:
    rows = [(s, d, float(i)) for s in ("AAA", "BBB") for i, d in enumerate(dates)]
    table = pa.table(
        {
            "symbol": [r[0] for r in rows],
            "date": pa.array([r[1] for r in rows], type=pa.date32()),
            "close": [r[2] for r in rows],
            "extra": [0.0] * len(rows),
        }
    )
    pq.write_table(table, path)


def test_read_history_up_to_cuts_after_the_evaluation_day(tmp_path, monkeypatch) -> None:
    dates = [date(2026, 4, d) for d in (1, 2, 6, 7, 8)]  # 3 April is a holiday
    path = tmp_path / "cache.parquet"
    _write_cache(path, dates)
    monkeypatch.setattr("config.settings.INDICATORS_CACHE_PATH", path)

    today = cache_reader.read_history_up_to(["close"], end_days_ago=0)
    assert today["date"].max() == date(2026, 4, 8)
    assert list(today.columns) == ["symbol", "date", "close"]  # only the columns asked for

    yesterday = cache_reader.read_history_up_to(["close"], end_days_ago=1)
    assert yesterday["date"].max() == date(2026, 4, 7)

    two_back = cache_reader.read_history_up_to(["close"], end_days_ago=3)
    assert two_back["date"].max() == date(2026, 4, 2)  # sessions, not calendar days
    assert (two_back.groupby("symbol")["date"].apply(lambda s: s.is_monotonic_increasing)).all()


def test_read_history_up_to_errors_are_readable(tmp_path, monkeypatch) -> None:
    path = tmp_path / "cache.parquet"
    _write_cache(path, [date(2026, 4, 1), date(2026, 4, 2)])
    monkeypatch.setattr("config.settings.INDICATORS_CACHE_PATH", path)
    with pytest.raises(ValueError, match="end_days_ago"):
        cache_reader.read_history_up_to(["close"], end_days_ago=-1)
    with pytest.raises(ValueError, match="sessions"):
        cache_reader.read_history_up_to(["close"], end_days_ago=5)


def test_read_history_up_to_without_a_cache_is_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("config.settings.INDICATORS_CACHE_PATH", tmp_path / "missing.parquet")
    assert cache_reader.read_history_up_to(["close"]).empty


@pytest.mark.parametrize(
    ("key", "needed"),
    [
        ("vcp_lite", {"atr_pct", "atr_pct_max_60d", "vol_dryup"}),
        ("rsi_pullback_uptrend", {"vol_dryup"}),
        ("rs_leaders", {"ret_rank_252d", "rs_63d"}),
        ("minervini_trend_template", {"ret_rank_252d", "rs_63d"}),
    ],
)
def test_history_columns_cover_the_cached_columns_the_rules_read(key: str, needed: set) -> None:
    """These four screeners read these cached columns, so the reader must load them."""
    from src.screeners.registry import get_screener

    assert needed <= set(get_screener(key).history_columns())
