"""Tests for the owner-approved adjustment overrides (config/adjustment_overrides.csv).

Official NSE corporate actions adjust prices. The owner can add an event NSE's list lacks, or
replace its factor (action "apply" with an exact factor), or cancel a wrong one (action
"ignore"), in a CSV file. The owner's row always wins.
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
    build_events,
    load_adjustment_overrides,
    rebuild_adjusted,
)
from tests.official_actions import add_placeholder_action, add_prices

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


def _official(symbol: str, day: date, factor: float) -> pd.DataFrame:
    """One official event row, in the shape read_official_events returns."""
    return pd.DataFrame(
        [{"symbol": symbol, "date": day, "factor": factor, "kinds": "BONUS", "n_parts": 1}]
    )


def test_apply_override_replaces_the_official_factor_and_names_its_source(tmp_path):
    """The owner's factor beats the official one for the same symbol and date."""
    df = _series("CRASHY", [100.0, 100.0, 55.0, 56.0])
    official = _official("CRASHY", date(2024, 1, 3), 0.6)
    assert build_events(official, df).iloc[0]["factor"] == 0.6

    overrides = load_adjustment_overrides(
        _write(tmp_path, "CRASHY,2024-01-03,apply,0.5,owner approved 2:1\n")
    )
    events = build_events(official, df, overrides=overrides)
    assert len(events) == 1
    row = events.iloc[0]
    assert row["factor"] == 0.5
    assert row["source"] == "override"
    assert row["ratio_kind"] == "material"


def test_apply_override_adds_an_event_the_official_list_lacks(tmp_path):
    df = _series("CRASHY", [100.0, 100.0, 55.0, 56.0])
    overrides = load_adjustment_overrides(
        _write(tmp_path, "CRASHY,2024-01-03,apply,0.5,owner approved\n")
    )
    events = build_events(_official("OTHER", date(2024, 1, 3), 0.5), df, overrides=overrides)
    assert list(events["symbol"]) == ["CRASHY"]


def test_ignore_override_cancels_an_official_event(tmp_path):
    df = _series("GLITCH", [100.0, 100.0, 50.0, 51.0])
    official = _official("GLITCH", date(2024, 1, 3), 0.5)
    assert len(build_events(official, df)) == 1

    overrides = load_adjustment_overrides(_write(tmp_path, "GLITCH,2024-01-03,ignore,,bad print\n"))
    assert build_events(official, df, overrides=overrides).empty


def test_override_for_a_date_without_prices_is_skipped(tmp_path):
    df = _series("ABC", [100.0, 101.0, 102.0])
    overrides = load_adjustment_overrides(_write(tmp_path, "ABC,2030-01-01,apply,0.5,wrong date\n"))
    assert build_events(_official("OTHER", date(2024, 1, 3), 0.5), df, overrides=overrides).empty


def test_rebuild_uses_the_overrides_file(tmp_path):
    """End to end: an approved override smooths the adjusted history."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    closes = [100.0, 100.0, 55.0, 56.0]
    start = date(2024, 1, 1)
    path = _write(tmp_path, "CRASHY,2024-01-03,apply,0.5,owner approved\n")
    with Session(engine) as session:
        add_placeholder_action(session)  # official table is filled, but nothing for CRASHY
        add_prices(session, "CRASHY", closes, start)
        session.commit()

        rebuild_adjusted(session=session, overrides_path=path)
        session.commit()

        adjusted = [
            float(r.adj_close)
            for r in session.scalars(select(DailyPrice).order_by(DailyPrice.date)).all()
        ]
        assert adjusted == [50.0, 50.0, 55.0, 56.0]
        assert float(session.scalars(select(AdjFactor)).one().factor) == 0.5
