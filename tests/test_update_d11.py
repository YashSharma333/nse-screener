"""
Tests for the daily routine (--update), the indicator cache and the README (Task D11, CL-28).
=============================================================================================
Each step function is tested through its real code path against a SQLite database, with
only the network calls replaced. ``stub_steps`` turns the other steps into no-ops.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from config import settings
from config.settings import PROJECT_ROOT
from src.common.trading_calendar import PRICES, load_non_trading_days
from src.computation.indicators.calculator import INDICATORS_SCHEMA_VERSION
from src.db.data_service import load_indicator_snapshot
from src.db.models import Base, BenchmarkPrice, CorporateAction, DailyPrice, Security, SymbolChange
from src.etl import daily_update
from src.etl.daily_update import (
    DailyUpdateError,
    DailyUpdateSummary,
    UpdateContext,
    run_daily_update,
)
from src.ingestion import nse_files
from src.ingestion.nse_files import NoFile
from tests.update_helpers import STEP_FUNCTIONS, stub_steps

TODAY = date(2026, 11, 20)


@pytest.fixture
def sqlite_session():
    """Isolated in-memory SQLite database for testing."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def _ctx(today: date = TODAY) -> UpdateContext:
    return UpdateContext(today=today)


def _file_database(tmp_path: Path):
    """A SQLite FILE database, so a second connection is truly separate (like the cache step)."""
    engine = create_engine(f"sqlite:///{tmp_path / 'update.db'}")
    Base.metadata.create_all(engine)
    return engine


def _benchmark_row(code: str, day: date = date(2026, 11, 19)) -> BenchmarkPrice:
    return BenchmarkPrice(index_code=code, date=day, close=100, source="test")


def _price_row(symbol: str, day: date, close: float = 10.0) -> DailyPrice:
    return DailyPrice(
        symbol=symbol, date=day, open=close, high=close, low=close, close=close, volume=1000
    )


# ---------------------------------------------------------------------------
# The loop: order, stop on failure, commit per step
# ---------------------------------------------------------------------------
def test_update_runs_steps_in_order(
    sqlite_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seven steps run in exactly this order and each result is kept in the summary."""
    call_order: list[str] = []
    for name in STEP_FUNCTIONS:
        monkeypatch.setattr(
            daily_update, name, lambda s, ctx, name=name: call_order.append(name) or {"ok": name}
        )

    summary = run_daily_update(session=sqlite_session, today=TODAY)

    assert call_order == list(STEP_FUNCTIONS)
    assert isinstance(summary, DailyUpdateSummary)
    assert len(summary.step_results) == 7


def test_update_stops_on_first_failure_and_reports_which_step(
    sqlite_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing step stops the run, names itself, and later steps never run."""
    stub_steps(monkeypatch)
    called: list[str] = []

    def failing_step(s, ctx):
        raise ConnectionResetError("NSE connection dropped during symbol change download")

    monkeypatch.setattr(daily_update, "step_refresh_reference_data", failing_step)
    monkeypatch.setattr(
        daily_update, "step_check_bogus_sessions", lambda s, ctx: called.append("bogus") or {}
    )

    with pytest.raises(DailyUpdateError) as exc_info:
        run_daily_update(session=sqlite_session, today=TODAY)

    err = exc_info.value
    assert err.step_number == 3
    assert err.step_name == "refresh_reference_data"
    assert "NSE connection dropped" in str(err)
    assert called == []


def test_cache_step_sees_earlier_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The cache step reads through its own connection, so the benchmark write must be committed."""
    engine = _file_database(tmp_path)
    stub_steps(monkeypatch)
    seen_by_cache_step: list[int] = []

    def write_benchmark(s, ctx):
        s.add(_benchmark_row("NIFTY 50"))
        s.flush()  # written but NOT committed: the loop must commit it

    def count_through_new_connection(s, ctx):
        with engine.connect() as other_connection:
            seen_by_cache_step.append(
                other_connection.scalar(select(func.count(BenchmarkPrice.id)))
            )

    monkeypatch.setattr(daily_update, "step_refresh_benchmarks", write_benchmark)
    monkeypatch.setattr(daily_update, "step_rebuild_indicator_cache", count_through_new_connection)
    with Session(engine) as session:
        run_daily_update(session=session, today=TODAY)

    assert seen_by_cache_step == [1]


def test_failing_step_rolls_back_only_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A step that fails half-way keeps the committed work of earlier steps, none of its own."""
    engine = _file_database(tmp_path)
    stub_steps(monkeypatch)

    def step_3_writes(s, ctx):
        s.add(_benchmark_row("NIFTY 50"))

    def step_4_writes_then_fails(s, ctx):
        s.add(_benchmark_row("NIFTY 500"))
        s.flush()
        raise ConnectionResetError("boom after a partial write")

    monkeypatch.setattr(daily_update, "step_refresh_reference_data", step_3_writes)
    monkeypatch.setattr(daily_update, "step_check_bogus_sessions", step_4_writes_then_fails)

    with Session(engine) as session, pytest.raises(DailyUpdateError) as exc_info:
        run_daily_update(session=session, today=TODAY)

    assert exc_info.value.step_number == 4
    with engine.connect() as connection:
        codes = connection.scalars(select(BenchmarkPrice.index_code)).all()
    assert codes == ["NIFTY 50"]  # step 3 kept, step 4's half-written row gone


# ---------------------------------------------------------------------------
# Step 1: load prices (today is tried; a 404 for today is not remembered)
# ---------------------------------------------------------------------------
def test_load_prices_tries_today_and_does_not_cache_early_404(
    sqlite_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Today's file is requested; NSE not having it yet is NOT stored as a holiday."""
    sqlite_session.add(_price_row("TESTCO", date(2026, 11, 19)))
    sqlite_session.flush()
    asked: list[str] = []

    def not_published_yet(url: str) -> bytes:
        asked.append(url)
        raise NoFile(url)

    monkeypatch.setattr(nse_files, "fetch", not_published_yet)
    result = daily_update.step_load_prices(sqlite_session, _ctx())

    assert any("20261120" in url for url in asked)
    assert result == {"new_dates": [], "rows_added": 0}
    assert TODAY not in load_non_trading_days(PRICES)


def test_load_prices_skips_download_when_today_already_stored(
    sqlite_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With prices already stored for today, nothing is downloaded."""
    sqlite_session.add(_price_row("TESTCO", TODAY))
    sqlite_session.flush()
    asked: list[str] = []
    monkeypatch.setattr(nse_files, "fetch", lambda url: asked.append(url) or b"")

    assert daily_update.step_load_prices(sqlite_session, _ctx()) == {
        "new_dates": [],
        "rows_added": 0,
    }
    assert asked == []


# ---------------------------------------------------------------------------
# Step 2: market cap
# ---------------------------------------------------------------------------
def test_market_cap_step_loads_new_dates_and_warns_on_unmatched(sqlite_session: Session) -> None:
    """The new dates are passed to the loader; unmatched symbols become a warning."""
    ctx = _ctx()
    ctx.summary.new_dates = [date(2026, 11, 19), TODAY]
    calls: list[tuple[date, date]] = []

    def fake_load(start, end, session):
        calls.append((start, end))
        return SimpleNamespace(updated_rows=7, unmatched_symbols=2)

    with patch("src.ingestion.mcap_loader.load_market_caps", side_effect=fake_load):
        result = daily_update.step_market_cap(sqlite_session, ctx)

    assert calls == [(date(2026, 11, 19), TODAY)]
    assert result == {"updated_rows": 7, "unmatched": 2}
    assert ctx.summary.mcap_rows_updated == 7
    assert any("2 PR bundle market cap symbols" in w for w in ctx.summary.warnings)


def test_market_cap_step_repairs_recent_dates_without_new_dates(sqlite_session: Session) -> None:
    """No new dates: a recent date that still has no market cap is retried."""
    sqlite_session.add(_price_row("TESTCO", TODAY))  # market_cap is NULL
    sqlite_session.flush()
    calls: list[tuple[date, date]] = []

    def fake_load(start, end, session):
        calls.append((start, end))
        return SimpleNamespace(updated_rows=1, unmatched_symbols=0)

    with patch("src.ingestion.mcap_loader.load_market_caps", side_effect=fake_load):
        daily_update.step_market_cap(sqlite_session, _ctx())

    assert calls == [(TODAY, TODAY)]


# ---------------------------------------------------------------------------
# Step 3: reference data (securities, renames, corporate actions)
# ---------------------------------------------------------------------------
def _fake_reference_downloads(session: Session, *, add_review_event: bool):
    """Patches for the three NSE loaders: each writes one realistic row."""

    def fake_equity_l(session):
        session.add(Security(symbol="NEWCO", status="active"))
        session.add(Security(symbol="OLDCO", status="delisted"))
        return 2

    def fake_renames(session):
        session.add(SymbolChange(old_symbol="AAA", new_symbol="BBB", effective_date=TODAY))
        return 500  # the parser count is large; only NEW table rows must be reported

    def fake_actions(session, today):
        if add_review_event:
            session.add(
                CorporateAction(
                    symbol="XYZ",
                    ex_date=TODAY,
                    series="EQ",
                    action_type="other",
                    subject="odd event",
                    subject_hash="a" * 32,
                    parse_status="needs_review",
                )
            )
        return {"rows_stored": 1}

    return (
        patch("src.ingestion.securities.import_equity_l", side_effect=fake_equity_l),
        patch("src.ingestion.renames.load_renames", side_effect=fake_renames),
        patch(
            "src.ingestion.corporate_actions_store.run_corporate_actions_download",
            side_effect=fake_actions,
        ),
    )


def test_reference_step_counts_new_renames_and_warns_on_new_review_events(
    sqlite_session: Session,
) -> None:
    """Renames are the rows added in this run; a NEW review event raises the warning."""
    ctx = _ctx()
    p1, p2, p3 = _fake_reference_downloads(sqlite_session, add_review_event=True)
    with p1, p2, p3:
        daily_update.step_refresh_reference_data(sqlite_session, ctx)

    assert ctx.summary.renamed_symbols == [("AAA", "BBB")]
    assert ctx.summary.new_symbols == ["NEWCO"]
    assert ctx.summary.dropped_symbols == ["OLDCO"]
    assert any("1 new corporate action event(s)" in w for w in ctx.summary.warnings)


def test_reference_step_stays_quiet_when_review_count_did_not_rise(
    sqlite_session: Session,
) -> None:
    """Old events waiting for review (ten-year total) must not warn again every day."""
    sqlite_session.add(
        CorporateAction(
            symbol="OLD",
            ex_date=date(2020, 1, 1),
            series="EQ",
            action_type="other",
            subject="odd old event",
            subject_hash="b" * 32,
            parse_status="needs_review",
        )
    )
    sqlite_session.flush()
    ctx = _ctx()
    p1, p2, p3 = _fake_reference_downloads(sqlite_session, add_review_event=False)
    with p1, p2, p3:
        daily_update.step_refresh_reference_data(sqlite_session, ctx)

    assert not any("corporate action" in w for w in ctx.summary.warnings)


# ---------------------------------------------------------------------------
# Step 4: bogus sessions look only at the dates loaded in this run
# ---------------------------------------------------------------------------
def _add_identical_days(session: Session, days: list[date]) -> None:
    for day in days:  # identical close and volume on every day
        for i in range(120):
            session.add(
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
    session.flush()


def test_update_never_deletes(
    sqlite_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bogus step flags a duplicated NEW session but leaves every price row in place."""
    monkeypatch.setattr("config.settings.REPORTS_DIR", tmp_path / "reports")
    day1, day2 = date(2026, 11, 16), date(2026, 11, 17)
    _add_identical_days(sqlite_session, [day1, day2])
    rows_before = sqlite_session.query(DailyPrice).count()
    stub_steps(monkeypatch, keep=("step_check_bogus_sessions",))
    monkeypatch.setattr(
        daily_update,
        "step_load_prices",
        lambda s, ctx: ctx.summary.new_dates.append(day2) or {},
    )

    summary = run_daily_update(session=sqlite_session, today=TODAY)

    assert summary.bogus_sessions_flagged == 1
    assert any("bogus duplicate/holiday session(s) detected" in w for w in summary.warnings)
    assert sqlite_session.query(DailyPrice).count() == rows_before
    assert (tmp_path / "reports" / "bogus_sessions.csv").exists()


def test_bogus_step_does_nothing_without_new_dates(
    sqlite_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An old duplicate in the database is not rescanned when no new date was loaded."""
    monkeypatch.setattr("config.settings.REPORTS_DIR", tmp_path / "reports")
    _add_identical_days(sqlite_session, [date(2026, 11, 16), date(2026, 11, 17)])

    result = daily_update.step_check_bogus_sessions(sqlite_session, _ctx())

    assert result["bogus_count"] == 0
    assert not (tmp_path / "reports" / "bogus_sessions.csv").exists()


# ---------------------------------------------------------------------------
# Step 5: benchmarks ask for every missing date, including gaps in the middle
# ---------------------------------------------------------------------------
def test_benchmark_step_loads_missing_dates_up_to_today(sqlite_session: Session) -> None:
    """Every price date without a benchmark row is requested, today included."""
    for day in (date(2026, 11, 17), date(2026, 11, 18), date(2026, 11, 19), TODAY):
        sqlite_session.add(_price_row("TESTCO", day, 1))
    for day in (date(2026, 11, 17), date(2026, 11, 19)):
        sqlite_session.add(_benchmark_row("NIFTY 500", day))
    sqlite_session.flush()
    asked: list[list[date]] = []

    def fake_load(dates, **kwargs):
        asked.append(list(dates))
        return len(dates) * 5

    ctx = _ctx()
    with patch("src.ingestion.benchmark.load_benchmarks", side_effect=fake_load):
        daily_update.step_refresh_benchmarks(sqlite_session, ctx)

    assert asked == [[date(2026, 11, 18), TODAY]]
    assert ctx.summary.benchmark_rows_saved == 10


def test_benchmark_step_warns_when_no_history_and_points_to_from_flag(
    sqlite_session: Session,
) -> None:
    """With no benchmark rows at all the step warns and names the --from command."""
    ctx = _ctx()
    assert daily_update.step_refresh_benchmarks(sqlite_session, ctx) == {"saved_rows": 0}
    assert any("--benchmarks --from 2016-11-21" in w for w in ctx.summary.warnings)


# ---------------------------------------------------------------------------
# Step 7: the cache step passes through to load_indicator_snapshot
# ---------------------------------------------------------------------------
def test_cache_step_checks_freshness_without_forcing(sqlite_session: Session) -> None:
    """The daily cache step never forces a rebuild; the cache decides if it is stale."""
    snapshot = pd.DataFrame({"symbol": ["A", "B"]})
    with patch(
        "src.db.data_service.load_indicator_snapshot",
        return_value=(snapshot, snapshot, True, "live"),
    ) as fake:
        result = daily_update.step_rebuild_indicator_cache(sqlite_session, _ctx())

    fake.assert_called_once_with(force_refresh=False)
    assert result == {"snapshot_len": 2, "is_live": True, "message": "live"}


# ---------------------------------------------------------------------------
# Idempotency when no new day exists
# ---------------------------------------------------------------------------
def test_update_is_idempotent_when_no_new_day(
    sqlite_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With prices stored up to today, the real load step adds 0 rows and the summary says so."""
    sqlite_session.add(_price_row("TESTCO", TODAY, 102.0))
    sqlite_session.flush()
    # Market cap would download a PR bundle: keep every step except the price load stubbed.
    stub_steps(monkeypatch, keep=("step_load_prices",))

    summary = run_daily_update(session=sqlite_session, today=TODAY)

    assert summary.new_dates == []
    assert summary.rows_added == 0
    assert "None (database already up to date)" in summary.format_text()


# ---------------------------------------------------------------------------
# 5. Schema version bumped and cache rebuilds
# ---------------------------------------------------------------------------
def test_indicator_schema_version_bumped_and_cache_rebuilds(tmp_path: Path) -> None:
    """Schema version is 6; cache with version 4 triggers automatic invalidation and rebuild."""
    assert INDICATORS_SCHEMA_VERSION == 6

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
    assert updated_meta["indicators_schema_version"] == 6


# ---------------------------------------------------------------------------
# 6. Market cap column in cache and null before 2024-02-01
# ---------------------------------------------------------------------------
def test_market_cap_column_in_cache_and_null_before_2024_02_01() -> None:
    """Cache contains market_cap column; values before 2024-02-01 are null/NaN."""
    cache_path = settings.INDICATORS_CACHE_PATH
    if not cache_path.exists():
        pytest.skip("indicators.parquet not present in repository")

    import pyarrow.parquet as pq

    schema = pq.read_schema(cache_path)
    assert "market_cap" in schema.names

    # Read sample of dates before and after 2024-02-01
    table = pq.read_table(
        cache_path,
        columns=["date", "market_cap"],
        filters=[("date", "<", date(2024, 2, 1))],
    )
    df_prior = table.to_pandas()
    if not df_prior.empty:
        # All market_cap rows before 2024-02-01 must be NaN / null
        assert df_prior["market_cap"].isna().all()


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
