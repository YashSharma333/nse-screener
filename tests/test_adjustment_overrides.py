"""Tests for the owner-approved adjustment overrides (config/adjustment_overrides.csv).

The price-jump detector only applies big, clean drops. The owner can approve more events
(action "apply" with an exact factor) or cancel a wrong one (action "ignore") in a CSV file.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.db.models import AdjFactor, Base, DailyPrice
from src.etl.adjust import (
    detect_all_events,
    load_adjustment_overrides,
    rebuild_adjusted,
)

HEADER = "symbol,date,action,factor,reason\n"


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "overrides.csv"
    path.write_text(HEADER + body)
    return path


def _series(symbol: str, closes: list[float]) -> pd.DataFrame:
    start = date(2024, 1, 1)
    return pd.DataFrame(
        {
            "symbol": symbol,
            "date": [start + timedelta(days=i) for i in range(len(closes))],
            "close": closes,
            "prev_close": [closes[0], *closes[:-1]],
            "volume": 1000,
        }
    )


def test_load_reads_apply_and_ignore_rows(tmp_path):
    path = _write(
        tmp_path,
        "NYKAA,2022-11-10,apply,0.16666667,bonus 5:1\nBCG,2022-03-15,ignore,,looks like bad data\n",
    )
    overrides = load_adjustment_overrides(path)
    assert list(overrides["action"]) == ["apply", "ignore"]
    assert overrides.iloc[0]["date"] == date(2022, 11, 10)
    assert overrides.iloc[0]["factor"] == pytest.approx(1 / 6, abs=1e-7)


def test_load_missing_file_means_no_overrides(tmp_path):
    assert load_adjustment_overrides(tmp_path / "nope.csv").empty


@pytest.mark.parametrize(
    "bad_row",
    [
        "ABC,2024-01-03,delete,0.5,unknown action",
        "ABC,2024-01-03,apply,,apply needs a factor",
        "ABC,2024-01-03,apply,-1,factor must be positive",
        "ABC,not-a-date,apply,0.5,bad date",
    ],
)
def test_load_rejects_bad_rows_with_a_clear_message(tmp_path, bad_row):
    with pytest.raises(ValueError, match="adjustment_overrides"):
        load_adjustment_overrides(_write(tmp_path, bad_row + "\n"))


def test_apply_override_adds_the_exact_factor_and_clears_the_review_row(tmp_path):
    """A 45% crash is only 'review'. Once the owner approves it, it is applied exactly."""
    df = _series("CRASHY", [100.0, 100.0, 55.0, 56.0])
    applied, review = detect_all_events(df)
    assert applied.empty
    assert len(review) == 1

    overrides = load_adjustment_overrides(
        _write(tmp_path, "CRASHY,2024-01-03,apply,0.5,owner approved 2:1\n")
    )
    applied, review = detect_all_events(df, overrides=overrides)
    assert len(applied) == 1
    row = applied.iloc[0]
    assert row["factor"] == 0.5
    assert row["source"] == "override"
    assert row["ratio_kind"] == "material"
    assert review.empty  # no longer an open question


def test_ignore_override_cancels_an_automatic_event(tmp_path):
    df = _series("GLITCH", [100.0, 100.0, 50.0, 51.0])
    assert len(detect_all_events(df)[0]) == 1

    overrides = load_adjustment_overrides(_write(tmp_path, "GLITCH,2024-01-03,ignore,,bad print\n"))
    applied, _review = detect_all_events(df, overrides=overrides)
    assert applied.empty


def test_override_for_a_date_without_prices_is_skipped(tmp_path):
    df = _series("ABC", [100.0, 101.0, 102.0])
    overrides = load_adjustment_overrides(_write(tmp_path, "ABC,2030-01-01,apply,0.5,wrong date\n"))
    applied, _review = detect_all_events(df, overrides=overrides)
    assert applied.empty


def test_rebuild_uses_the_overrides_file(tmp_path):
    """End to end: an approved 'review' event smooths the adjusted history."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    closes = [100.0, 100.0, 55.0, 56.0]
    start = date(2024, 1, 1)
    path = _write(tmp_path, "CRASHY,2024-01-03,apply,0.5,owner approved\n")
    with Session(engine) as session:
        for i, close in enumerate(closes):
            session.add(
                DailyPrice(
                    symbol="CRASHY",
                    date=start + timedelta(days=i),
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    prev_close=closes[max(i - 1, 0)],
                    volume=1000,
                )
            )
        session.commit()

        rebuild_adjusted(session=session, overrides_path=path)
        session.commit()

        adjusted = [
            float(r.adj_close)
            for r in session.scalars(select(DailyPrice).order_by(DailyPrice.date)).all()
        ]
        assert adjusted == [50.0, 50.0, 55.0, 56.0]
        assert float(session.scalars(select(AdjFactor)).one().factor) == 0.5
