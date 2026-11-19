"""Tests for TASK T65b: Universe overrides + symbol aliases (renames).

Verifies:
1. Overrides survive re-import, hysteresis and force_exclude.
2. Alias apply with OLD rows in DB (ICICIPRULI case) and zero OLD rows in DB (PEL case).
3. A first day that gaps +8% at the open but PREV_CLOSE equals OLD's close => still verified_exact.
4. Same-day 2:1 split => ratio_mismatch with implied_ratio 2.0 and nothing merged.
5. A missing cached day => date_gap.
6. Both symbols trading the same day => overlap.
7. Dry-run changes nothing; backup written.
8. Fill-missing keeps history for a renamed stock and ingestion does not recreate renamed rows.
9. --detect-renames finds a pair, ignores a non-matching pair, and marks an ambiguous one.
10. Alembic upgrade and downgrade for migration 007.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

import openpyxl
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker

from config.settings import PROJECT_ROOT
from src.db.models import Base, DailyPrice, StockMaster, SymbolAlias
from src.etl.pipeline import BhavcopyETL


@pytest.fixture
def sqlite_db(tmp_path: Path):
    """File-backed SQLite database for schema testing."""
    db_file = tmp_path / "test_overrides_aliases.db"
    engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    return engine, session_factory


def _write_parquet_cache(
    file_path: Path,
    rows: list[dict],
    trade_date: date | None = None,
) -> None:
    """Helper to write parquet file using real Arrow date32 types for TradeDate."""
    df = pd.DataFrame(rows)
    table = pa.Table.from_pandas(df)
    if trade_date is not None:
        td_arr = pa.array([trade_date] * len(df), type=pa.date32())
        table = table.append_column("TradeDate", td_arr)
    pq.write_table(table, file_path)


def _create_sample_excel(file_path: Path, stocks: list[dict]) -> Path:
    """Helper to generate an Excel file matching Chartink format."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Watchlist"
    ws.append(
        [
            "Sr.",
            "Stock Name",
            "Symbol",
            "close",
            "industry",
            "marketcapname",
            "sector",
            "market_cap",
            "Add Column",
        ]
    )
    for idx, s in enumerate(stocks, 1):
        ws.append(
            [
                idx,
                s.get("name", f"Company {s['symbol']}"),
                s["symbol"],
                s.get("close", 100.0),
                s.get("industry", "Technology"),
                s.get("cap_bucket", "largecap"),
                s.get("sector", "IT"),
                s["market_cap"],
                None,
            ]
        )
    wb.save(file_path)
    return file_path


# ===========================================================================
# 1. Overrides: Hysteresis, Force Include, Force Exclude, Re-import Survival
# ===========================================================================
def test_universe_overrides_survive_reimport_and_force_exclude(tmp_path: Path, sqlite_db):
    """Overrides survive re-import, hysteresis and force_exclude:

    - STOCK_A: mcap 1000 (normal include)
    - STOCK_B: mcap 300 (below threshold) overridden with force_include => active
    - STOCK_C: mcap 1200 (above threshold) overridden with force_exclude => excluded
    - STOCK_D: not in excel file, overridden with force_include => active, cap_bucket 'Unknown'
    - Re-importing with same file preserves all states.
    """
    _engine, session_factory = sqlite_db
    excel_file = tmp_path / "universe.xlsx"
    _create_sample_excel(
        excel_file,
        [
            {"symbol": "STOCK_A", "market_cap": 1000.0},
            {"symbol": "STOCK_B", "market_cap": 300.0},
            {"symbol": "STOCK_C", "market_cap": 1200.0},
        ],
    )

    overrides_csv = tmp_path / "overrides.csv"
    overrides_csv.write_text(
        "symbol,action,reason,added_on\n"
        'STOCK_B,force_include,"missing large cap",2026-11-16\n'
        'STOCK_C,force_exclude,"owner exclude",2026-11-16\n'
        'STOCK_D,force_include,"not in file override",2026-11-16\n',
        encoding="utf-8",
    )

    reports_dir = tmp_path / "reports"
    etl = BhavcopyETL(cache_dir=tmp_path / "cache")

    with session_factory() as session:
        # First import
        res1 = etl.import_universe(
            file_path=excel_file,
            overrides_path=overrides_csv,
            dry_run=False,
            session=session,
            reports_dir=reports_dir,
        )
        assert res1["active_count"] == 3  # STOCK_A, STOCK_B, STOCK_D
        assert res1["applied_overrides_count"] == 3

        # Check stock_master in DB
        sm_a = session.get(StockMaster, "STOCK_A")
        assert sm_a is not None and sm_a.in_universe is True and sm_a.status == "active"

        sm_b = session.get(StockMaster, "STOCK_B")
        assert sm_b is not None and sm_b.in_universe is True and sm_b.status == "active"
        assert sm_b.source == "override"

        sm_c = session.get(StockMaster, "STOCK_C")
        assert sm_c is not None and sm_c.in_universe is False and sm_c.status == "below_threshold"

        sm_d = session.get(StockMaster, "STOCK_D")
        assert sm_d is not None and sm_d.in_universe is True and sm_d.status == "active"
        assert sm_d.cap_bucket == "Unknown"
        assert sm_d.source == "override"

        # Re-import: verify idempotency and override survival
        res2 = etl.import_universe(
            file_path=excel_file,
            overrides_path=overrides_csv,
            dry_run=False,
            session=session,
            reports_dir=reports_dir,
        )
        assert res2["active_count"] == 3

        # Check report CSV contains applied_overrides section
        report_df = pd.read_csv(reports_dir / "universe_import_report.csv")
        ov_rows = report_df[report_df["section"] == "applied_overrides"]
        assert len(ov_rows) == 3
        assert set(ov_rows["name"]) == {"STOCK_B", "STOCK_C", "STOCK_D"}


# ===========================================================================
# 2. Symbol Alias: Import CSV Command
# ===========================================================================
def test_import_symbol_aliases(tmp_path: Path, sqlite_db):
    """Test --import-symbol-aliases imports candidate records into symbol_alias table."""
    _engine, session_factory = sqlite_db
    alias_csv = tmp_path / "aliases.csv"
    alias_csv.write_text(
        "old_symbol,new_symbol,effective_date,status,implied_ratio,note\n"
        "ICICIPRULI,ICICILIFE,,candidate,,ICICI Life Insurance rename\n"
        "PEL,PIRAMALFIN,,candidate,,Piramal rename candidate\n",
        encoding="utf-8",
    )

    etl = BhavcopyETL(cache_dir=tmp_path / "cache")
    with session_factory() as session:
        res = etl.import_symbol_aliases(file_path=alias_csv, session=session)
        assert res["status"] == "success"
        assert res["imported"] == 2

        aliases = session.scalars(select(SymbolAlias)).all()
        assert len(aliases) == 2
        assert {a.old_symbol for a in aliases} == {"ICICIPRULI", "PEL"}


# ===========================================================================
# 3. Continuity Check Classifications: Gap Open, 2:1 Split, Date Gap, Overlap
# ===========================================================================
def test_continuity_gap_open_still_verified_exact(tmp_path: Path):
    """A first day that gaps +8% at the open but PREV_CLOSE equals OLD's close => verified_exact."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    d1 = date(2026, 10, 22)
    d2 = date(2026, 10, 23)

    _write_parquet_cache(
        cache_dir / f"{d1.isoformat()}.parquet",
        [
            {
                "Symbol": "OLD_SYM",
                "Open": 99.0,
                "High": 102.0,
                "Low": 98.0,
                "Close": 100.0,
                "Volume": 1000,
            }
        ],
        trade_date=d1,
    )
    # Day 2: gaps up +8% at open (108.0) but PrevClose is 100.0 (equals OLD close)
    _write_parquet_cache(
        cache_dir / f"{d2.isoformat()}.parquet",
        [
            {
                "Symbol": "NEW_SYM",
                "Open": 108.0,
                "High": 112.0,
                "Low": 107.0,
                "Close": 110.0,
                "PrevClose": 100.0,
                "Volume": 1500,
            }
        ],
        trade_date=d2,
    )

    etl = BhavcopyETL(cache_dir=cache_dir)
    res = etl.check_alias_continuity("OLD_SYM", "NEW_SYM")
    assert res["classification"] == "verified_exact"
    assert res["implied_ratio"] == 1.0
    assert res["diff"] == 0.0


def test_continuity_same_day_split_ratio_mismatch(tmp_path: Path):
    """Same-day 2:1 split => ratio_mismatch with implied_ratio 2.0 and nothing merged."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    d1 = date(2026, 10, 22)
    d2 = date(2026, 10, 23)

    _write_parquet_cache(
        cache_dir / f"{d1.isoformat()}.parquet",
        [{"Symbol": "OLD_SPLIT", "Close": 100.0, "Volume": 1000}],
        trade_date=d1,
    )
    # New first PrevClose is 200.0 (2:1 ratio mismatch)
    _write_parquet_cache(
        cache_dir / f"{d2.isoformat()}.parquet",
        [{"Symbol": "NEW_SPLIT", "Close": 205.0, "PrevClose": 200.0, "Volume": 1500}],
        trade_date=d2,
    )

    etl = BhavcopyETL(cache_dir=cache_dir)
    res = etl.check_alias_continuity("OLD_SPLIT", "NEW_SPLIT")
    assert res["classification"] == "ratio_mismatch"
    assert res["implied_ratio"] == 2.0


def test_continuity_missing_cached_day_date_gap(tmp_path: Path):
    """Missing cached trading day for OLD prior to NEW first date => date_gap."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    d1 = date(2026, 10, 20)
    d2 = date(2026, 10, 21)
    d3 = date(2026, 10, 22)

    # Cache has trading days d1, d2, d3.
    # OLD traded on d1, but NOT on d2. NEW starts on d3.
    _write_parquet_cache(
        cache_dir / f"{d1.isoformat()}.parquet",
        [{"Symbol": "OLD_GAP", "Close": 100.0, "Volume": 1000}],
        trade_date=d1,
    )
    _write_parquet_cache(
        cache_dir / f"{d2.isoformat()}.parquet",
        [{"Symbol": "OTHER_SYM", "Close": 50.0, "Volume": 500}],
        trade_date=d2,
    )
    _write_parquet_cache(
        cache_dir / f"{d3.isoformat()}.parquet",
        [{"Symbol": "NEW_GAP", "Close": 102.0, "PrevClose": 100.0, "Volume": 800}],
        trade_date=d3,
    )

    etl = BhavcopyETL(cache_dir=cache_dir)
    res = etl.check_alias_continuity("OLD_GAP", "NEW_GAP")
    assert res["classification"] == "date_gap"
    assert res["implied_ratio"] is None


def test_continuity_both_trading_same_day_overlap(tmp_path: Path):
    """Both symbols trading on the same day with volume > 0 and different prices => overlap."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    d1 = date(2026, 10, 22)
    _write_parquet_cache(
        cache_dir / f"{d1.isoformat()}.parquet",
        [
            {"Symbol": "SYM_X", "Close": 100.0, "Volume": 1000},
            {"Symbol": "SYM_Y", "Close": 250.0, "Volume": 500},
        ],
        trade_date=d1,
    )

    etl = BhavcopyETL(cache_dir=cache_dir)
    res = etl.check_alias_continuity("SYM_X", "SYM_Y")
    assert res["classification"] == "overlap"


# ===========================================================================
# 4. Apply Symbol Aliases: With OLD rows in DB (ICICIPRULI case)
# ===========================================================================
def test_apply_aliases_with_old_rows_in_db(tmp_path: Path, sqlite_db):
    """Alias apply with OLD rows in DB (ICICIPRULI case):

    - Dry-run changes nothing.
    - Backup written to reports/alias_backup_<OLD>_to_<NEW>.csv.
    - Moved daily_prices rows from OLD to NEW.
    - stock_master OLD set to status 'renamed', renamed_to NEW, in_universe False.
    - Fills missing dates for NEW.
    """
    _engine, session_factory = sqlite_db
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    reports_dir = tmp_path / "reports"

    d1 = date(2026, 10, 21)
    d2 = date(2026, 10, 22)
    d3 = date(2026, 10, 23)
    d4 = date(2026, 10, 24)

    # Parquet cache:
    _write_parquet_cache(
        cache_dir / f"{d1.isoformat()}.parquet",
        [{"Symbol": "ICICIPRULI", "Close": 420.0, "PrevClose": 418.0, "Volume": 1000}],
        trade_date=d1,
    )
    _write_parquet_cache(
        cache_dir / f"{d2.isoformat()}.parquet",
        [{"Symbol": "ICICIPRULI", "Close": 431.0, "PrevClose": 420.0, "Volume": 1000}],
        trade_date=d2,
    )
    _write_parquet_cache(
        cache_dir / f"{d3.isoformat()}.parquet",
        [{"Symbol": "ICICILIFE", "Close": 435.0, "PrevClose": 431.0, "Volume": 1200}],
        trade_date=d3,
    )
    _write_parquet_cache(
        cache_dir / f"{d4.isoformat()}.parquet",
        [{"Symbol": "ICICILIFE", "Close": 440.0, "PrevClose": 435.0, "Volume": 1500}],
        trade_date=d4,
    )

    with session_factory() as session:
        # Seed stock_master
        session.add(
            StockMaster(
                symbol="ICICIPRULI",
                company_name="ICICI Pru Life",
                sector="Financial Services",
                industry="Insurance",
                cap_bucket="largecap",
                in_universe=False,
                status="not_in_file",
                added_on=d1,
            )
        )
        session.add(
            StockMaster(
                symbol="ICICILIFE",
                company_name="ICICI Life Insurance",
                sector=None,  # missing sector to verify copy
                industry=None,
                cap_bucket="Unknown",
                in_universe=True,
                status="active",
                added_on=d3,
            )
        )
        # Seed daily_prices: ICICIPRULI has d1, d2; ICICILIFE has d4 (missing d3)
        session.add(DailyPrice(symbol="ICICIPRULI", date=d1, close=420.0, volume=1000))
        session.add(DailyPrice(symbol="ICICIPRULI", date=d2, close=431.0, volume=1000))
        session.add(DailyPrice(symbol="ICICILIFE", date=d4, close=440.0, volume=1500))

        # Seed symbol_alias
        session.add(
            SymbolAlias(
                old_symbol="ICICIPRULI",
                new_symbol="ICICILIFE",
                status="candidate",
            )
        )
        session.commit()

        etl = BhavcopyETL(cache_dir=cache_dir)

        # 1. Test Dry-Run: changes nothing
        res_dry = etl.apply_symbol_aliases(dry_run=True, session=session, reports_dir=reports_dir)
        assert res_dry["dry_run"] is True
        assert res_dry["applied_count"] == 0
        # Check DB unchanged
        old_cnt = len(
            session.scalars(select(DailyPrice).where(DailyPrice.symbol == "ICICIPRULI")).all()
        )
        assert old_cnt == 2
        new_cnt = len(
            session.scalars(select(DailyPrice).where(DailyPrice.symbol == "ICICILIFE")).all()
        )
        assert new_cnt == 1
        assert not (reports_dir / "alias_backup_ICICIPRULI_to_ICICILIFE.csv").exists()

        # 2. Test Apply (Write mode) with rebuild_adjusted and cache refresh mocked
        with (
            patch.object(etl, "rebuild_adjusted_prices") as mock_rebuild,
            patch("src.db.data_service.load_indicator_snapshot") as mock_refresh,
        ):
            res_apply = etl.apply_symbol_aliases(
                dry_run=False, session=session, reports_dir=reports_dir
            )
            assert res_apply["applied_count"] == 1
            mock_rebuild.assert_called_once_with(symbols=["ICICILIFE"])
            mock_refresh.assert_called_once()

        # Verify backup written
        backup_file = reports_dir / "alias_backup_ICICIPRULI_to_ICICILIFE.csv"
        assert backup_file.exists()
        df_bak = pd.read_csv(backup_file)
        assert len(df_bak) == 2

        # Verify daily_prices moved and filled:
        # OLD has 0 rows, NEW has all 4 dates (d1, d2, d3, d4)
        old_rows = session.scalars(
            select(DailyPrice).where(DailyPrice.symbol == "ICICIPRULI")
        ).all()
        assert len(old_rows) == 0

        new_rows = session.scalars(select(DailyPrice).where(DailyPrice.symbol == "ICICILIFE")).all()
        assert len(new_rows) == 4
        assert {r.date for r in new_rows} == {d1, d2, d3, d4}

        # Verify stock_master metadata updated and copied
        sm_old = session.get(StockMaster, "ICICIPRULI")
        assert sm_old.status == "renamed"
        assert sm_old.renamed_to == "ICICILIFE"
        assert sm_old.in_universe is False

        sm_new = session.get(StockMaster, "ICICILIFE")
        assert sm_new.sector == "Financial Services"
        assert sm_new.industry == "Insurance"
        assert sm_new.cap_bucket == "largecap"


# ===========================================================================
# 5. Apply Symbol Aliases: Zero OLD rows in DB (PEL case)
# ===========================================================================
def test_apply_aliases_zero_old_rows_in_db(tmp_path: Path, sqlite_db):
    """Alias apply with zero OLD rows in DB (PEL case):

    - PEL has 0 rows in DB, but exists in cache.
    - Rows from cache are loaded under PIRAMALFIN.
    - stock_master PEL is updated to status 'renamed' with renamed_to PIRAMALFIN.
    """
    _engine, session_factory = sqlite_db
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    reports_dir = tmp_path / "reports"

    d1 = date(2026, 1, 2)
    d2 = date(2026, 1, 3)
    d3 = date(2026, 1, 4)

    # Parquet cache: PEL on d1, d2; PIRAMALFIN on d3
    _write_parquet_cache(
        cache_dir / f"{d1.isoformat()}.parquet",
        [{"Symbol": "PEL", "Close": 1000.0, "Volume": 500}],
        trade_date=d1,
    )
    _write_parquet_cache(
        cache_dir / f"{d2.isoformat()}.parquet",
        [{"Symbol": "PEL", "Close": 1020.0, "Volume": 550}],
        trade_date=d2,
    )
    _write_parquet_cache(
        cache_dir / f"{d3.isoformat()}.parquet",
        [{"Symbol": "PIRAMALFIN", "Close": 1025.0, "PrevClose": 1020.0, "Volume": 600}],
        trade_date=d3,
    )

    with session_factory() as session:
        session.add(
            StockMaster(
                symbol="PEL",
                company_name="Piramal Enterprises",
                cap_bucket="midcap",
                in_universe=False,
                status="inactive",
                added_on=d1,
            )
        )
        session.add(
            StockMaster(
                symbol="PIRAMALFIN",
                company_name="Piramal Finance",
                cap_bucket="Unknown",
                in_universe=True,
                status="active",
                added_on=d3,
            )
        )
        # Note: 0 rows for PEL in daily_prices! Only d3 for PIRAMALFIN
        session.add(DailyPrice(symbol="PIRAMALFIN", date=d3, close=1025.0, volume=600))
        session.add(
            SymbolAlias(
                old_symbol="PEL",
                new_symbol="PIRAMALFIN",
                status="candidate",
            )
        )
        session.commit()

        etl = BhavcopyETL(cache_dir=cache_dir)
        with (
            patch.object(etl, "rebuild_adjusted_prices"),
            patch("src.db.data_service.load_indicator_snapshot"),
        ):
            res = etl.apply_symbol_aliases(dry_run=False, session=session, reports_dir=reports_dir)
            assert res["applied_count"] == 1

        # Check that d1 and d2 rows from PEL cache were inserted under PIRAMALFIN
        piramal_rows = session.scalars(
            select(DailyPrice).where(DailyPrice.symbol == "PIRAMALFIN")
        ).all()
        assert len(piramal_rows) == 3
        assert {r.date for r in piramal_rows} == {d1, d2, d3}

        # Check stock_master PEL status
        sm_pel = session.get(StockMaster, "PEL")
        assert sm_pel.status == "renamed"
        assert sm_pel.renamed_to == "PIRAMALFIN"


# ===========================================================================
# 6. Fill Missing Symbols is Alias-Aware
# ===========================================================================
def test_fill_missing_symbols_alias_aware(tmp_path: Path, sqlite_db):
    """--fill-missing-symbols pulls historical rows for renamed stock from OLD symbol cache,

    and ingestion does not recreate rows under OLD symbol marked 'renamed'.
    """
    _engine, session_factory = sqlite_db
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    d1 = date(2026, 1, 2)
    d2 = date(2026, 1, 3)

    # Parquet cache: on d1, file only has OLD_SYM; on d2, file has NEW_SYM
    _write_parquet_cache(
        cache_dir / f"{d1.isoformat()}.parquet",
        [{"Symbol": "OLD_SYM", "Close": 100.0, "Volume": 1000}],
        trade_date=d1,
    )
    _write_parquet_cache(
        cache_dir / f"{d2.isoformat()}.parquet",
        [{"Symbol": "NEW_SYM", "Close": 102.0, "Volume": 1200}],
        trade_date=d2,
    )

    with session_factory() as session:
        session.add(
            StockMaster(
                symbol="OLD_SYM",
                company_name="Old Corp",
                in_universe=False,
                status="renamed",
                renamed_to="NEW_SYM",
                added_on=d1,
            )
        )
        session.add(
            StockMaster(
                symbol="NEW_SYM",
                company_name="New Corp",
                in_universe=True,
                status="active",
                added_on=d2,
            )
        )
        # Stored dates in DB: seed d1 with a benchmark or another stock
        session.add(DailyPrice(symbol="OTHER", date=d1, close=50.0, volume=100))
        session.add(DailyPrice(symbol="OTHER", date=d2, close=51.0, volume=100))
        session.commit()

        etl = BhavcopyETL(cache_dir=cache_dir)
        with (
            patch.object(etl, "rebuild_adjusted_prices"),
            patch("src.db.data_service.load_indicator_snapshot"),
        ):
            res = etl.fill_missing_symbols(session=session)
            assert res["status"] == "success"

        # Check NEW_SYM received row for d1 (sourced from OLD_SYM in cache) and d2
        new_prices = session.scalars(select(DailyPrice).where(DailyPrice.symbol == "NEW_SYM")).all()
        assert len(new_prices) == 2
        assert {r.date for r in new_prices} == {d1, d2}

        # Check OLD_SYM has 0 rows in daily_prices
        old_prices = session.scalars(select(DailyPrice).where(DailyPrice.symbol == "OLD_SYM")).all()
        assert len(old_prices) == 0


# ===========================================================================
# 7. Detect Renames: Candidate, Ignored Mismatch, and Ambiguous
# ===========================================================================
def test_detect_renames(tmp_path: Path):
    """--detect-renames finds matching pair, ignores mismatch, and marks ambiguous:

    - OLD_OK -> NEW_OK: stops d1, starts d2, exact prevclose => candidate
    - OLD_RATIO -> NEW_RATIO: stops d1, starts d2, 2:1 ratio => ignored
    - OLD_AMBIG -> NEW_A and NEW_B: both match equally => marked ambiguous
    """
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    reports_dir = tmp_path / "reports"

    d1 = date(2026, 1, 2)
    d2 = date(2026, 1, 3)
    d3 = date(2026, 1, 4)

    # d1: OLD_OK, OLD_RATIO, OLD_AMBIG
    _write_parquet_cache(
        cache_dir / f"{d1.isoformat()}.parquet",
        [
            {"Symbol": "OLD_OK", "Close": 100.0, "PrevClose": 98.0, "Volume": 1000},
            {"Symbol": "OLD_RATIO", "Close": 75.0, "PrevClose": 74.0, "Volume": 1000},
            {"Symbol": "OLD_AMBIG", "Close": 300.0, "PrevClose": 295.0, "Volume": 1000},
        ],
        trade_date=d1,
    )
    # d2: NEW_OK (prevclose 100.0), NEW_RATIO (prevclose 150.0 -> 2:1 ratio),
    # NEW_A (prevclose 300.0), NEW_B (prevclose 300.0)
    _write_parquet_cache(
        cache_dir / f"{d2.isoformat()}.parquet",
        [
            {"Symbol": "NEW_OK", "Close": 102.0, "PrevClose": 100.0, "Volume": 1200},
            {"Symbol": "NEW_RATIO", "Close": 152.0, "PrevClose": 150.0, "Volume": 1200},
            {"Symbol": "NEW_A", "Close": 305.0, "PrevClose": 300.0, "Volume": 1200},
            {"Symbol": "NEW_B", "Close": 305.0, "PrevClose": 300.0, "Volume": 1200},
        ],
        trade_date=d2,
    )
    # d3: final session
    _write_parquet_cache(
        cache_dir / f"{d3.isoformat()}.parquet",
        [
            {"Symbol": "NEW_OK", "Close": 103.0, "PrevClose": 102.0, "Volume": 1200},
            {"Symbol": "NEW_RATIO", "Close": 153.0, "PrevClose": 152.0, "Volume": 1200},
            {"Symbol": "NEW_A", "Close": 306.0, "PrevClose": 305.0, "Volume": 1200},
            {"Symbol": "NEW_B", "Close": 306.0, "PrevClose": 305.0, "Volume": 1200},
        ],
        trade_date=d3,
    )

    etl = BhavcopyETL(cache_dir=cache_dir)
    res = etl.detect_renames(reports_dir=reports_dir)

    assert res["status"] == "success"
    report_file = reports_dir / "rename_candidates.csv"
    assert report_file.exists()

    df_report = pd.read_csv(report_file)
    # 1. OLD_OK -> NEW_OK is a verified candidate
    cand_row = df_report[df_report["old_symbol"] == "OLD_OK"]
    assert len(cand_row) == 1
    assert cand_row.iloc[0]["new_symbol"] == "NEW_OK"
    assert cand_row.iloc[0]["classification"] == "verified_exact"
    assert cand_row.iloc[0]["status"] == "candidate"

    # 2. OLD_RATIO is not in candidates
    assert len(df_report[df_report["old_symbol"] == "OLD_RATIO"]) == 0

    # 3. OLD_AMBIG is ambiguous
    ambig_rows = df_report[df_report["old_symbol"] == "OLD_AMBIG"]
    assert len(ambig_rows) == 2
    assert set(ambig_rows["status"]) == {"ambiguous"}


# ===========================================================================
# 8. Alembic Migration 007 Upgrade & Downgrade
# ===========================================================================
def test_alembic_upgrade_downgrade_migration_007(tmp_path: Path):
    """Test Alembic migration 007 upgrade to head and downgrade."""
    db_file = tmp_path / "test_migration_007.db"
    sqlalchemy_url = f"sqlite:///{db_file}"

    alembic_cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", sqlalchemy_url)
    alembic_cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))

    # Upgrade to 007 (where symbol_alias is created)
    command.upgrade(alembic_cfg, "f7c18050340f")

    engine = create_engine(sqlalchemy_url)
    insp = inspect(engine)
    tables = insp.get_table_names()
    assert "symbol_alias" in tables
    assert "stock_master" in tables

    sm_cols = [c["name"] for c in insp.get_columns("stock_master")]
    assert "renamed_to" in sm_cols

    # Downgrade to previous revision (006)
    command.downgrade(alembic_cfg, "78422e33ba2d")

    insp_after = inspect(engine)
    tables_after = insp_after.get_table_names()
    assert "symbol_alias" not in tables_after
    sm_cols_after = [c["name"] for c in insp_after.get_columns("stock_master")]
    assert "renamed_to" not in sm_cols_after

    # Re-upgrade to 007
    command.upgrade(alembic_cfg, "f7c18050340f")
    insp_final = inspect(engine)
    assert "symbol_alias" in insp_final.get_table_names()
    assert "renamed_to" in [c["name"] for c in insp_final.get_columns("stock_master")]
