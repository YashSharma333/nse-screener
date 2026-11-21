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
    """Fakes record call order and verify steps 1 to 9 run in exact sequential order."""
    call_order: list[int] = []

    def make_fake_step(step_idx: int):
        def _fake() -> dict[str, str]:
            call_order.append(step_idx)
            return {"status": f"step_{step_idx}_ok"}

        return _fake

    handlers = {i: make_fake_step(i) for i in range(1, 10)}

    summary = run_daily_update(
        session=sqlite_session,
        today=date(2026, 11, 20),
        step_handlers=handlers,
    )

    assert call_order == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    assert isinstance(summary, DailyUpdateSummary)
    assert len(summary.step_results) == 9


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
# 3. Bogus-session check never deletes
# ---------------------------------------------------------------------------
def test_update_never_deletes(
    sqlite_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Step 5 flags a duplicated session but leaves every price row in place."""
    # Keep the CSV report out of the real reports/ folder.
    monkeypatch.setattr("config.settings.REPORTS_DIR", tmp_path / "reports")

    day1, day2 = date(2026, 11, 16), date(2026, 11, 17)
    for day in (day1, day2):  # identical close and volume on both days
        for i in range(120):
            sqlite_session.add(
                DailyPrice(
                    symbol=f"SYM{i}",
                    date=day,
                    open=10.0,
                    high=11.0,
                    low=9.0,
                    close=10.0 + i,
                    volume=1000 + i,
                )
            )
    sqlite_session.flush()
    rows_before = sqlite_session.query(DailyPrice).count()

    summary = run_daily_update(
        session=sqlite_session,
        today=date(2026, 11, 20),
        # Mock every step except step 5
        step_handlers={
            1: lambda: {"new_dates": [], "rows_added": 0},
            2: lambda: {"updated_rows": 0},
            3: lambda: {},
            4: lambda: {},
            6: lambda: {},
            7: lambda: {},
            8: lambda: {},
            9: lambda: {},
        },
    )

    assert summary.bogus_sessions_flagged == 1
    assert any("bogus duplicate/holiday session(s) detected" in w for w in summary.warnings)
    assert sqlite_session.query(DailyPrice).count() == rows_before  # nothing deleted
    assert (tmp_path / "reports" / "bogus_sessions.csv").exists()


# ---------------------------------------------------------------------------
# 3b. Benchmark step loads only dates newer than the last stored benchmark
# ---------------------------------------------------------------------------
def test_benchmark_step_loads_only_new_dates(sqlite_session: Session) -> None:
    """Step 6 asks for price dates after the last benchmark date, up to yesterday."""
    from src.db.models import BenchmarkPrice

    for day in (date(2026, 11, 17), date(2026, 11, 18), date(2026, 11, 19), date(2026, 11, 20)):
        sqlite_session.add(
            DailyPrice(symbol="TESTCO", date=day, open=1, high=1, low=1, close=1, volume=1)
        )
    sqlite_session.add(
        BenchmarkPrice(index_code="NIFTY 50", date=date(2026, 11, 17), close=100.0, source="NSE")
    )
    sqlite_session.flush()

    asked: list[list[date]] = []

    def fake_load(dates, **kwargs):
        asked.append(list(dates))
        return len(dates) * 5

    stubs = {i: (lambda: {}) for i in (3, 4, 5, 7, 8, 9)}
    stubs[1] = lambda: {"new_dates": [], "rows_added": 0}
    stubs[2] = lambda: {"updated_rows": 0}
    with patch("src.ingestion.benchmark.load_benchmarks", side_effect=fake_load):
        summary = run_daily_update(
            session=sqlite_session, today=date(2026, 11, 20), step_handlers=stubs
        )

    # 2026-11-20 is "today", so only the 18th and 19th are requested.
    assert asked == [[date(2026, 11, 18), date(2026, 11, 19)]]
    assert summary.benchmark_rows_saved == 10


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
            # Step 2 must be stubbed too: its default downloads a PR bundle from NSE,
            # and tests must never touch the network.
            2: lambda: {},
            3: lambda: {},
            4: lambda: {},
            5: lambda: {},
            6: lambda: {},
            7: lambda: {},
            8: lambda: {},
            9: lambda: {},
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
    """Every flag in `run_pipeline.py --help` is in the README; removed flags are not."""
    import re
    import subprocess
    import sys

    readme_text = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    help_text = subprocess.run(
        [sys.executable, "run_pipeline.py", "--help"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    cli_flags = set(re.findall(r"^\s+(--[a-z0-9][a-z0-9-]*)", help_text, flags=re.MULTILINE))

    # The daily and loading commands must really exist (guards against an empty parse).
    assert {"--update", "--load-prices", "--benchmarks", "--rebuild-adjusted"} <= cli_flags

    for flag in sorted(cli_flags - {"--help"}):
        assert flag in readme_text, f"{flag} is in run_pipeline.py --help but not in README.md"

    # Flags of the removed old ETL must not come back in the docs as commands.
    removed = [
        "--import-universe",
        "--apply-symbol-aliases",
        "--fill-missing-symbols",
        "--purge-bogus-sessions",
        "--backfill",
        "--incremental",
        "--detect-renames",
    ]
    for flag in removed:
        assert f"python run_pipeline.py {flag}" not in readme_text, f"{flag} was removed in D13"
