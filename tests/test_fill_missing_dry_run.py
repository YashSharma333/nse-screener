"""Regression tests for issue #46: --fill-missing-symbols ignored --dry-run.

A dry run must only REPORT what would be added. It must not write rows, download
files, rebuild adjusted prices or refresh the indicator cache. The CLI must also
refuse --dry-run for commands that cannot honour it.
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, DailyPrice, StockMaster
from src.etl.dry_run import DRY_RUN_COMMANDS, dry_run_is_supported
from src.etl.pipeline import BhavcopyETL


@pytest.fixture
def session_factory(tmp_path: Path):
    """File-backed SQLite database with the real schema."""
    engine = create_engine(f"sqlite:///{tmp_path / 'dry_run.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _count_rows(session) -> int:
    return session.scalar(select(func.count()).select_from(DailyPrice))


def test_fill_missing_symbols_dry_run_writes_nothing(tmp_path: Path, session_factory):
    """--dry-run reports the rows it would add and leaves the database untouched."""
    cache_dir = tmp_path / "bhavcopy_cache"
    cache_dir.mkdir()
    day_with_cache = date(2026, 1, 2)
    day_without_cache = date(2026, 1, 3)  # a real run would have to download this file

    with session_factory() as session:
        session.add_all(
            [
                StockMaster(
                    symbol="SYM_A",
                    company_name="A",
                    in_universe=True,
                    status="active",
                    added_on=day_with_cache,
                ),
                StockMaster(
                    symbol="SYM_B",
                    company_name="B",
                    in_universe=True,
                    status="active",
                    added_on=day_with_cache,
                ),
            ]
        )
        # SYM_A already has a row on both days; SYM_B is missing on both.
        session.add_all(
            [
                DailyPrice(symbol="SYM_A", date=day_with_cache, close=100.0, volume=1000),
                DailyPrice(symbol="SYM_A", date=day_without_cache, close=101.0, volume=1000),
            ]
        )
        session.commit()

        cached_day = pd.DataFrame(
            [
                {"Symbol": "SYM_A", "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0},
                {"Symbol": "SYM_B", "Open": 200.0, "High": 205.0, "Low": 195.0, "Close": 202.0},
            ]
        ).assign(Volume=1000)
        pq.write_table(
            pa.Table.from_pandas(cached_day), cache_dir / f"{day_with_cache.isoformat()}.parquet"
        )

        downloader = MagicMock()
        etl = BhavcopyETL()
        etl._downloader = downloader
        etl.cache_dir = cache_dir
        rows_before = _count_rows(session)

        with (
            patch("src.etl.pipeline.rebuild_adjusted") as mock_rebuild,
            patch("src.db.data_service.load_indicator_snapshot") as mock_refresh,
        ):
            result = etl.fill_missing_symbols(session=session, dry_run=True)

        assert result["status"] == "dry_run"
        assert result["dry_run"] is True
        assert result["rows_added"] == 1  # SYM_B on the day that has a cache file
        assert result["dates_needing_download"] == 1  # the other day has no cache file
        assert _count_rows(session) == rows_before  # nothing written
        downloader.fetch.assert_not_called()  # nothing downloaded
        mock_rebuild.assert_not_called()
        mock_refresh.assert_not_called()


def test_dry_run_flag_is_supported_only_by_commands_that_honour_it():
    """The CLI must refuse --dry-run for commands that would silently ignore it."""

    def make_args(**flags) -> argparse.Namespace:
        base = dict.fromkeys(DRY_RUN_COMMANDS, False)
        base.update(dry_run=True, backfill=False, incremental=False)
        base.update(flags)
        return argparse.Namespace(**base)

    assert dry_run_is_supported(make_args(fill_missing_symbols=True))
    assert dry_run_is_supported(make_args(apply_symbol_aliases=True))
    assert not dry_run_is_supported(make_args(backfill=True))
    assert not dry_run_is_supported(make_args(incremental=True))
    # Without --dry-run there is nothing to refuse.
    assert dry_run_is_supported(make_args(backfill=True, dry_run=False))
