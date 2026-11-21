"""The indicator cache keeps N years but computes on N years plus warm-up bars."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.data_service import INDICATORS_SCHEMA_VERSION, load_indicator_snapshot
from src.db.models import Base, DailyPrice


@pytest.fixture
def sqlite_db(tmp_path: Path):
    """File-backed SQLite database for real schema tests."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test_window.db'}")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)


def test_indicator_window_respects_warmup(tmp_path: Path, sqlite_db):
    """Cache refresh computes on last N years + >=300 warmup bars, keeps only last N years.

    Verifies sma_200 is valid (not NaN) on the first kept row, and metadata records
    indicator_history_years and bumped schema version.
    """
    engine, session_factory = sqlite_db
    cache_path = tmp_path / "indicators.parquet"

    # Generate 700 dates (~2 years) of prices so there are > 300 bars before 1-yr cutoff
    base_d = date(2022, 1, 1)
    dates = [base_d + timedelta(days=i) for i in range(700)]

    rows = []
    for idx, d in enumerate(dates):
        rows.append(
            DailyPrice(
                symbol="TEST_SYM",
                date=d,
                open=100.0 + idx * 0.1,
                high=102.0 + idx * 0.1,
                low=99.0 + idx * 0.1,
                close=101.0 + idx * 0.1,
                volume=1000 + idx,
            )
        )

    with session_factory() as session:
        session.add_all(rows)
        session.commit()

    with (
        patch("src.db.data_service.engine", engine),
        patch("src.db.data_service.check_connection", return_value=True),
        patch("src.db.data_service.INDICATOR_HISTORY_YEARS", 1),
        patch("src.db.data_service.INDICATOR_WARMUP_BARS", 300),
    ):
        _snapshot, full, _is_live, _msg = load_indicator_snapshot(
            cache_path=cache_path,
            force_refresh=True,
            history_years=1,
        )

        assert not full.empty
        max_date = dates[-1]
        cutoff_date = max_date.replace(year=max_date.year - 1)

        # Full cache should contain only rows >= cutoff_date
        assert full["date"].min() >= cutoff_date
        assert full["date"].max() == max_date

        # Critical test requirement: sma_200 valid on the very first kept row!
        first_row = full.sort_values(by="date").iloc[0]
        assert pd.notna(first_row["sma_200"])
        assert float(first_row["sma_200"]) > 0

        # Check metadata sidecar
        meta_file = cache_path.with_suffix(".meta.json")
        assert meta_file.exists()
        with meta_file.open("r", encoding="utf-8") as f:
            meta = json.load(f)

        assert meta["indicators_schema_version"] == INDICATORS_SCHEMA_VERSION
        assert meta["indicator_history_years"] == 1
        assert meta["history_window_start"] == str(cutoff_date)
        assert meta["history_window_end"] == str(max_date)
        assert meta["warmup_bars"] >= 300
