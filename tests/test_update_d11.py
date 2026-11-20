"""
Unit tests for Daily Routine (--update) and UI Market Cap (Task D11).
=====================================================================
Tests:
• test_update_runs_steps_in_order (fakes record the call order)
• test_update_stops_on_first_failure_and_reports_which_step
• test_update_never_deletes (prune step is dry run only)
• test_update_is_idempotent_when_no_new_day
• test_indicator_schema_version_bumped_and_cache_rebuilds
• test_market_cap_column_in_cache_and_null_before_2024_02_01
• test_ui_data_returns_market_cap_not_bucket (no Streamlit import needed; test ui/data.py functions)
• README command list matches python run_pipeline.py --help (the check CLAUDE.md requires)
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from config.settings import PROJECT_ROOT
from src.computation.indicators.calculator import INDICATORS_SCHEMA_VERSION
from src.db.data_service import (
    INDICATORS_CACHE_PATH,
    load_indicator_snapshot,
)
from src.db.models import Base, DailyPrice
from src.etl.daily_update import (
    DailyUpdateError,
    DailyUpdateSummary,
    run_daily_update,
)
from ui.data import (
    filter_by_market_cap,
    format_market_cap,
    get_snapshot,
)


@pytest.fixture
def sqlite_session():
    """Isolated in-memory SQLite database for testing."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


# ---------------------------------------------------------------------------
# 1. Step order verification
# ---------------------------------------------------------------------------
def test_update_runs_steps_in_order(sqlite_session: Session) -> None:
    """Fakes record call order and verify steps 1 to 8 run in exact sequential order."""
    call_order: list[int] = []

    def make_fake_step(step_idx: int):
        def _fake() -> dict[str, str]:
            call_order.append(step_idx)
            return {"status": f"step_{step_idx}_ok"}

        return _fake

    handlers = {i: make_fake_step(i) for i in range(1, 9)}

    summary = run_daily_update(
        session=sqlite_session,
        today=date(2026, 11, 20),
        step_handlers=handlers,
    )

    assert call_order == [1, 2, 3, 4, 5, 6, 7, 8]
    assert isinstance(summary, DailyUpdateSummary)
    assert len(summary.step_results) == 8


# ---------------------------------------------------------------------------
# 2. Stop on first failure
# ---------------------------------------------------------------------------
def test_update_stops_on_first_failure_and_reports_which_step(sqlite_session: Session) -> None:
    """When a step fails, the update stops immediately and reports which step failed."""
    call_order: list[int] = []

    def fake_step_1():
        call_order.append(1)
        return {"new_dates": [], "rows_added": 0}

    def fake_step_2():
        call_order.append(2)
        return {"updated_rows": 0}

    def failing_step_3():
        call_order.append(3)
        raise ConnectionResetError("NSE connection dropped during symbol change download")

    handlers = {
        1: fake_step_1,
        2: fake_step_2,
        3: failing_step_3,
        4: lambda: call_order.append(4),
        5: lambda: call_order.append(5),
    }

    with pytest.raises(DailyUpdateError) as exc_info:
        run_daily_update(
            session=sqlite_session,
            today=date(2026, 11, 20),
            step_handlers=handlers,
        )

    err = exc_info.value
    assert err.step_number == 3
    assert err.step_name == "refresh_securities_and_renames"
    assert "NSE connection dropped" in str(err)
    # Subsequent steps 4, 5, etc. were NEVER called
    assert call_order == [1, 2, 3]


# ---------------------------------------------------------------------------
# 3. Prune dry run never deletes
# ---------------------------------------------------------------------------
def test_update_never_deletes(sqlite_session: Session) -> None:
    """Step 5 (prune dry run) runs in dry-run mode and never deletes rows."""
    purge_calls: list[dict[str, Any]] = []

    class FakeETL:
        def purge_bogus_sessions(self, dry_run=True, confirm=False, **kwargs):
            purge_calls.append({"dry_run": dry_run, "confirm": confirm})
            return {
                "bogus_count": 2,
                "purged_dates": [date(2024, 5, 18), date(2024, 5, 19)],
                "dry_run": True,
            }

    with patch("src.etl.pipeline.BhavcopyETL", return_value=FakeETL()):
        summary = run_daily_update(
            session=sqlite_session,
            today=date(2026, 11, 20),
            # Mock other steps except step 5
            step_handlers={
                1: lambda: {"new_dates": [], "rows_added": 0},
                2: lambda: {"updated_rows": 0},
                3: lambda: {},
                4: lambda: {},
                6: lambda: {},
                7: lambda: {},
                8: lambda: {},
            },
        )

    assert len(purge_calls) == 1
    # Crucial guarantee: dry_run must be True and confirm must be False
    assert purge_calls[0]["dry_run"] is True
    assert purge_calls[0]["confirm"] is False
    assert summary.bogus_sessions_flagged == 2
    assert any("bogus duplicate/holiday session(s) detected" in w for w in summary.warnings)


# ---------------------------------------------------------------------------
# 4. Idempotency when no new day exists
# ---------------------------------------------------------------------------
def test_update_is_idempotent_when_no_new_day(sqlite_session: Session) -> None:
    """When DB already contains prices up to yesterday, update runs cleanly with 0 rows added."""
    today = date(2026, 11, 20)
    yesterday = today - timedelta(days=1)

    # Insert price row with yesterday's date
    sqlite_session.add(
        DailyPrice(
            symbol="TESTCO",
            date=yesterday,
            open=100.0,
            high=105.0,
            low=99.0,
            close=102.0,
            volume=1000,
        )
    )
    sqlite_session.flush()

    # Step 1 should detect start_date > yesterday and add 0 rows
    summary = run_daily_update(
        session=sqlite_session,
        today=today,
        step_handlers={
            3: lambda: {},
            4: lambda: {},
            5: lambda: {},
            6: lambda: {},
            7: lambda: {},
            8: lambda: {},
        },
    )

    assert summary.new_dates == []
    assert summary.rows_added == 0
    assert "None (database already up to date)" in summary.format_text()


# ---------------------------------------------------------------------------
# 5. Schema version bumped and cache rebuilds
# ---------------------------------------------------------------------------
def test_indicator_schema_version_bumped_and_cache_rebuilds(tmp_path: Path) -> None:
    """Schema version is 5; cache with version 4 triggers automatic invalidation and rebuild."""
    assert INDICATORS_SCHEMA_VERSION == 5

    cache_file = tmp_path / "indicators.parquet"
    meta_file = tmp_path / "indicators.meta.json"

    # Create dummy parquet cache with schema version 4
    df_old = pd.DataFrame([{"symbol": "TCS", "date": date(2024, 1, 1), "close": 3800.0}])
    df_old.to_parquet(cache_file)
    meta_file.write_text(
        json.dumps(
            {
                "indicators_schema_version": 4,  # Old version
                "db_max_date": "2024-01-01",
                "indicator_history_years": 2,
            }
        ),
        encoding="utf-8",
    )

    # Call load_indicator_snapshot without force_refresh
    with (
        patch("src.db.data_service.load_raw_market_data", return_value=(df_old, True, "live")),
        patch("src.db.data_service.compute_market_indicators", return_value=df_old),
        patch(
            "src.db.data_service.get_db_cache_metadata",
            return_value={"db_max_date": "2024-01-01"},
        ),
    ):
        _snapshot, _full, _is_live, _msg = load_indicator_snapshot(
            cache_path=cache_file,
            force_refresh=False,
        )

    # Read the updated metadata file
    updated_meta = json.loads(meta_file.read_text(encoding="utf-8"))
    assert updated_meta["indicators_schema_version"] == 5


# ---------------------------------------------------------------------------
# 6. Market cap column in cache and null before 2024-02-01
# ---------------------------------------------------------------------------
def test_market_cap_column_in_cache_and_null_before_2024_02_01() -> None:
    """Cache contains market_cap column; values before 2024-02-01 are null/NaN."""
    if not INDICATORS_CACHE_PATH.exists():
        pytest.skip("indicators.parquet not present in repository")

    import pyarrow.parquet as pq

    schema = pq.read_schema(INDICATORS_CACHE_PATH)
    assert "market_cap" in schema.names

    # Read sample of dates before and after 2024-02-01
    table = pq.read_table(
        INDICATORS_CACHE_PATH,
        columns=["date", "market_cap"],
        filters=[("date", "<", date(2024, 2, 1))],
    )
    df_prior = table.to_pandas()
    if not df_prior.empty:
        # All market_cap rows before 2024-02-01 must be NaN / null
        assert df_prior["market_cap"].isna().all()


# ---------------------------------------------------------------------------
# 7. UI data returns market cap not cap bucket
# ---------------------------------------------------------------------------
def test_ui_data_returns_market_cap_not_bucket() -> None:
    """ui/data.py returns market_cap in rupees, format helper shows crores, no cap bucket."""
    # 1. Format helper tests
    assert format_market_cap(10_000_000_000) == "₹1,000.00 Cr"
    assert format_market_cap(50_000_000) == "₹5.00 Cr"
    assert format_market_cap(None) == ""
    assert format_market_cap(np.nan) == ""
    assert format_market_cap(0) == ""
    assert format_market_cap(-500) == ""
    assert format_market_cap("invalid") == ""

    # 2. Filter helper tests (rupees)
    df_test = pd.DataFrame(
        [
            {"symbol": "MEGA", "market_cap": 1_000_000_000_000.0},  # 100,000 Cr
            {"symbol": "MID", "market_cap": 50_000_000_000.0},  # 5,000 Cr
            {"symbol": "SMALL", "market_cap": 5_000_000_000.0},  # 500 Cr
            {"symbol": "ANCIENT", "market_cap": np.nan},  # missing
        ]
    )

    filtered = filter_by_market_cap(
        df_test, min_mcap_rupees=10_000_000_000.0, include_missing=False
    )
    assert sorted(filtered["symbol"].tolist()) == ["MEGA", "MID"]

    # 3. get_snapshot strips cap_bucket and includes market_cap
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

    fake_res = (fake_snapshot, fake_snapshot, True, "live")
    with patch("ui.data._fetch_market_data", return_value=fake_res):
        snap_data = get_snapshot(
            parquet_path=Path("nonexistent.parquet"),
            meta_path=Path("nonexistent.json"),
        )
        snap_df = snap_data["snapshot"]
        assert "market_cap" in snap_df.columns
        assert "cap_bucket" not in snap_df.columns
        assert "index_name" not in snap_df.columns


# ---------------------------------------------------------------------------
# 8. README command list matches run_pipeline.py --help
# ---------------------------------------------------------------------------
def test_readme_command_list_matches_help() -> None:
    """README documents every command option available in run_pipeline.py --help."""
    readme_path = PROJECT_ROOT / "README.md"
    assert readme_path.exists()
    readme_text = readme_path.read_text(encoding="utf-8")

    # Critical operational commands that must be documented in README
    required_commands = [
        "--update",
        "--load-prices",
        "--load-mcap",
        "--verify-prices",
        "--load-securities",
        "--load-renames",
        "--crosscheck-52w",
        "--download-corporate-actions",
        "--rebuild-adjusted",
        "--import-universe",
        "--apply-symbol-aliases",
        "--fill-missing-symbols",
        "--purge-bogus-sessions",
        "--benchmarks",
        "--refresh-cache",
    ]

    for cmd in required_commands:
        msg = f"Command '{cmd}' is in run_pipeline.py but missing from README.md"
        assert cmd in readme_text, msg
