"""Tests for TASK T65a: Stock master + universe from Chartink Excel + fill missing symbols.

Verifies:
1. Alembic migration upgrade/downgrade for stock_master table.
2. First import boundary conditions (489.99, 490.00, 499.90, 500.00) with 490 keep-threshold.
3. Later import hysteresis: 498 not added, existing 495 kept, existing 480 flagged.
4. not_in_file handling: stored symbols missing in file flagged, price rows never deleted.
5. Idempotency: re-running with the same file changes nothing.
6. Pipeline filter uses stock_master universe symbols when populated; falls back to NIFTY
   constituents when stock_master is empty.
7. Fill missing symbols from cache date by date, supporting pre-T64b cache files,
   missing cache file downloads, partial IPO history, and idempotency.
8. Indicator window setting (INDICATOR_HISTORY_YEARS) respects >=300 bars warmup,
   keeps only the last N years in cache, ensures sma_200 is valid on the first kept row,
   and writes window metadata with bumped schema version.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import openpyxl
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import sessionmaker

from config.settings import PROJECT_ROOT
from src.db.data_service import (
    INDICATORS_SCHEMA_VERSION,
    get_universe_cap_buckets,
    get_universe_symbols,
    load_indicator_snapshot,
)
from src.db.models import Base, DailyPrice, StockMaster
from src.db.upsert import upsert_rows
from src.etl.pipeline import BhavcopyETL


@pytest.fixture
def sqlite_db(tmp_path: Path):
    """File-backed SQLite database for real schema tests."""
    db_file = tmp_path / "test_stock_master.db"
    engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    return engine, session_factory


def _create_sample_excel(
    file_path: Path,
    stocks: list[dict],
) -> Path:
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
                s.get("industry", "textiles"),
                s.get("cap_bucket", "smallcap"),
                s.get("sector", "manufacturing"),
                s["market_cap"],
                None,
            ]
        )
    wb.save(file_path)
    return file_path


# ===========================================================================
# 1. Alembic Upgrade & Downgrade
# ===========================================================================
def test_alembic_upgrade_and_downgrade(tmp_path: Path):
    """Test Alembic upgrade to head and downgrade for stock_master."""
    db_file = tmp_path / "test_alembic.db"
    sqlalchemy_url = f"sqlite:///{db_file}"

    alembic_cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", sqlalchemy_url)
    alembic_cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))

    # Upgrade to head
    command.upgrade(alembic_cfg, "head")
    engine = create_engine(sqlalchemy_url)
    insp = inspect(engine)
    tables = insp.get_table_names()
    assert "stock_master" in tables

    # Downgrade 1 revision
    command.downgrade(alembic_cfg, "-1")
    insp = inspect(engine)
    tables = insp.get_table_names()
    assert "stock_master" not in tables

    # Re-upgrade to head
    command.upgrade(alembic_cfg, "head")
    insp = inspect(engine)
    tables = insp.get_table_names()
    assert "stock_master" in tables


# ===========================================================================
# 2. First Import Boundaries (489.99, 490.00, 499.90, 500.00)
# ===========================================================================
def test_first_import_boundaries(tmp_path: Path, sqlite_db):
    """First import: table is empty; market_cap >= 490 becomes active/in_universe."""
    _engine, session_factory = sqlite_db
    xlsx_path = tmp_path / "test_universe_1.xlsx"
    reports_dir = tmp_path / "reports"

    stocks = [
        {"symbol": "STOCK_489", "market_cap": 489.99, "name": "Under 490 Corp"},
        {"symbol": "STOCK_490", "market_cap": 490.00, "name": "Exact 490 Corp"},
        {"symbol": "STOCK_499", "market_cap": 499.90, "name": "Under 500 Corp"},
        {"symbol": "STOCK_500", "market_cap": 500.00, "name": "Exact 500 Corp"},
    ]
    _create_sample_excel(xlsx_path, stocks)

    with session_factory() as session:
        etl = BhavcopyETL()

        # 1. Dry run: writes report, but does NOT write to database
        res_dry = etl.import_universe(
            xlsx_path,
            add_threshold=500.0,
            keep_threshold=490.0,
            dry_run=True,
            session=session,
            reports_dir=reports_dir,
        )
        assert res_dry["dry_run"] is True
        assert res_dry["active_count"] == 3
        assert res_dry["below_threshold_count"] == 1

        db_count = session.scalar(select(func.count()).select_from(StockMaster))
        assert db_count == 0  # Dry run touched nothing in DB

        # Verify report was written
        report_file = reports_dir / "universe_import_report.csv"
        assert report_file.exists()
        df_rep = pd.read_csv(report_file)
        assert "status_counts" in df_rep["section"].values

        # 2. Real import (dry_run=False)
        res_real = etl.import_universe(
            xlsx_path,
            add_threshold=500.0,
            keep_threshold=490.0,
            dry_run=False,
            session=session,
            reports_dir=reports_dir,
        )
        assert res_real["dry_run"] is False
        assert res_real["active_count"] == 3
        assert res_real["below_threshold_count"] == 1

        db_rows = {sm.symbol: sm for sm in session.scalars(select(StockMaster)).all()}
        assert len(db_rows) == 4

        # Boundary checks
        assert db_rows["STOCK_489"].in_universe is False
        assert db_rows["STOCK_489"].status == "below_threshold"
        assert db_rows["STOCK_489"].flagged_on is not None

        assert db_rows["STOCK_490"].in_universe is True
        assert db_rows["STOCK_490"].status == "active"
        assert db_rows["STOCK_490"].flagged_on is None

        assert db_rows["STOCK_499"].in_universe is True
        assert db_rows["STOCK_499"].status == "active"
        assert db_rows["STOCK_499"].flagged_on is None

        assert db_rows["STOCK_500"].in_universe is True
        assert db_rows["STOCK_500"].status == "active"
        assert db_rows["STOCK_500"].flagged_on is None


# ===========================================================================
# 3. Later Import Hysteresis
# ===========================================================================
def test_later_import_hysteresis(tmp_path: Path, sqlite_db):
    """Later import hysteresis:

    - 498 new/below is NOT added.
    - An existing 495 in universe is KEPT.
    - An existing 480 in universe is FLAGGED (dropped below threshold).
    - An existing 510 added is active.
    """
    _engine, session_factory = sqlite_db
    reports_dir = tmp_path / "reports"
    old_date = date(2026, 1, 1)

    with session_factory() as session:
        # Seed existing stock_master records
        sm1 = StockMaster(
            symbol="STOCK_495",
            company_name="Kept Co",
            market_cap_cr=495.0,
            in_universe=True,
            status="active",
            added_on=old_date,
        )
        sm2 = StockMaster(
            symbol="STOCK_480",
            company_name="Falling Co",
            market_cap_cr=510.0,
            in_universe=True,
            status="active",
            added_on=old_date,
        )
        sm3 = StockMaster(
            symbol="STOCK_BELOW",
            company_name="Old Small Co",
            market_cap_cr=450.0,
            in_universe=False,
            status="below_threshold",
            added_on=old_date,
            flagged_on=old_date,
        )
        session.add_all([sm1, sm2, sm3])
        session.commit()

        xlsx_path = tmp_path / "test_universe_later.xlsx"
        stocks = [
            {"symbol": "STOCK_495", "market_cap": 495.0},  # Already in universe, >= 490 -> stays
            {"symbol": "STOCK_480", "market_cap": 480.0},  # Was in universe, < 490 -> flagged
            {
                "symbol": "STOCK_BELOW",
                "market_cap": 498.0,
            },  # Was not in universe, < 500 -> stays out
            {"symbol": "NEW_498", "market_cap": 498.0},  # New, < 500 -> not added
            {"symbol": "NEW_500", "market_cap": 500.0},  # New, >= 500 -> added
        ]
        _create_sample_excel(xlsx_path, stocks)

        etl = BhavcopyETL()
        etl.import_universe(
            xlsx_path,
            add_threshold=500.0,
            keep_threshold=490.0,
            dry_run=False,
            session=session,
            reports_dir=reports_dir,
        )

        db_rows = {sm.symbol: sm for sm in session.scalars(select(StockMaster)).all()}

        # 1. Existing 495 is KEPT
        assert db_rows["STOCK_495"].in_universe is True
        assert db_rows["STOCK_495"].status == "active"
        assert db_rows["STOCK_495"].added_on == old_date

        # 2. Existing 480 is FLAGGED
        assert db_rows["STOCK_480"].in_universe is False
        assert db_rows["STOCK_480"].status == "below_threshold"
        assert db_rows["STOCK_480"].flagged_on is not None

        # 3. 498 not added (neither existing below nor brand new)
        assert db_rows["STOCK_BELOW"].in_universe is False
        assert db_rows["STOCK_BELOW"].status == "below_threshold"

        assert db_rows["NEW_498"].in_universe is False
        assert db_rows["NEW_498"].status == "below_threshold"

        # 4. Brand new >= 500 is added
        assert db_rows["NEW_500"].in_universe is True
        assert db_rows["NEW_500"].status == "active"


# ===========================================================================
# 4. Not in File Handling & No Price Rows Deleted
# ===========================================================================
def test_not_in_file_and_never_delete_price_rows(tmp_path: Path, sqlite_db):
    """Symbols present in price history but missing from file become status='not_in_file'.

    Never delete price rows when status changes.
    """
    _engine, session_factory = sqlite_db
    reports_dir = tmp_path / "reports"

    with session_factory() as session:
        # Seed price history with 2 symbols
        dp1 = DailyPrice(
            symbol="IN_FILE_SYM",
            date=date(2026, 1, 1),
            close=100.0,
            volume=1000,
        )
        dp2 = DailyPrice(
            symbol="MISSING_FROM_FILE",
            date=date(2026, 1, 1),
            close=200.0,
            volume=2000,
        )
        session.add_all([dp1, dp2])
        session.commit()

        # Excel only contains IN_FILE_SYM
        xlsx_path = tmp_path / "test_universe_missing.xlsx"
        stocks = [{"symbol": "IN_FILE_SYM", "market_cap": 600.0}]
        _create_sample_excel(xlsx_path, stocks)

        etl = BhavcopyETL()
        etl.import_universe(
            xlsx_path,
            dry_run=False,
            session=session,
            reports_dir=reports_dir,
        )

        db_rows = {sm.symbol: sm for sm in session.scalars(select(StockMaster)).all()}
        assert "MISSING_FROM_FILE" in db_rows
        assert db_rows["MISSING_FROM_FILE"].status == "not_in_file"
        assert db_rows["MISSING_FROM_FILE"].in_universe is False

        # Verify daily_prices rows were NEVER deleted
        prices = session.scalars(
            select(DailyPrice).where(DailyPrice.symbol == "MISSING_FROM_FILE")
        ).all()
        assert len(prices) == 1
        assert prices[0].close == 200.0


# ===========================================================================
# 5. Idempotency
# ===========================================================================
def test_import_universe_idempotency(tmp_path: Path, sqlite_db):
    """Re-running with the same file changes nothing."""
    _engine, session_factory = sqlite_db
    reports_dir = tmp_path / "reports"

    xlsx_path = tmp_path / "test_idempotent.xlsx"
    stocks = [
        {"symbol": "SYM_A", "market_cap": 600.0},
        {"symbol": "SYM_B", "market_cap": 495.0},
        {"symbol": "SYM_C", "market_cap": 300.0},
    ]
    _create_sample_excel(xlsx_path, stocks)

    with session_factory() as session:
        etl = BhavcopyETL()

        # Run 1
        etl.import_universe(xlsx_path, dry_run=False, session=session, reports_dir=reports_dir)
        snap1 = {
            sm.symbol: (sm.status, sm.in_universe, sm.market_cap_cr, sm.flagged_on)
            for sm in session.scalars(select(StockMaster)).all()
        }

        # Run 2 with exact same file
        etl.import_universe(xlsx_path, dry_run=False, session=session, reports_dir=reports_dir)
        snap2 = {
            sm.symbol: (sm.status, sm.in_universe, sm.market_cap_cr, sm.flagged_on)
            for sm in session.scalars(select(StockMaster)).all()
        }

        assert snap1 == snap2


# ===========================================================================
# 6. Pipeline Filter Uses Universe
# ===========================================================================
def test_pipeline_filter_uses_stock_master(tmp_path: Path, sqlite_db):
    """Pipeline uses stock_master.in_universe symbols when populated; falls back if empty."""
    _engine, session_factory = sqlite_db

    with session_factory() as session:
        # Case 1: Empty stock_master -> warning logged, falls back to empty set / old behavior
        syms_empty = get_universe_symbols(session=session)
        assert syms_empty == set()

        # Case 2: Populated stock_master
        sm1 = StockMaster(
            symbol="ACTIVE_1",
            company_name="Active 1",
            in_universe=True,
            status="active",
            added_on=date.today(),
        )
        sm2 = StockMaster(
            symbol="ACTIVE_2",
            company_name="Active 2",
            in_universe=True,
            status="active",
            added_on=date.today(),
        )
        sm3 = StockMaster(
            symbol="INACTIVE",
            company_name="Inactive",
            in_universe=False,
            status="below_threshold",
            added_on=date.today(),
        )
        session.add_all([sm1, sm2, sm3])
        session.commit()

        syms = get_universe_symbols(session=session)
        assert syms == {"ACTIVE_1", "ACTIVE_2"}


# ===========================================================================
# 7. Fill Missing Symbols from Cache
# ===========================================================================
def test_fill_missing_symbols(tmp_path: Path, sqlite_db):
    """Fill missing symbols from cache date by date:

    - Pre-T64b parquet files (without TradeDate) accepted for stored dates.
    - Missing cache file triggers downloader.fetch.
    - IPO symbol listed later simply has partial history.
    - Rebuild adjusted called once for added symbols.
    - Idempotent: re-running does not re-add rows.
    """
    _engine, session_factory = sqlite_db
    cache_dir = tmp_path / "bhavcopy_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    d1 = date(2026, 1, 2)
    d2 = date(2026, 1, 3)

    with session_factory() as session:
        # 1. Populate stock_master with 3 universe symbols
        session.add_all(
            [
                StockMaster(
                    symbol="SYM_A",
                    company_name="Company A",
                    in_universe=True,
                    status="active",
                    added_on=d1,
                ),
                StockMaster(
                    symbol="SYM_B",
                    company_name="Company B",
                    in_universe=True,
                    status="active",
                    added_on=d1,
                ),
                StockMaster(
                    symbol="SYM_IPO",
                    company_name="IPO Company",
                    in_universe=True,
                    status="active",
                    added_on=d1,
                ),
            ]
        )
        # 2. Seed daily_prices with only SYM_A on d1 and d2
        session.add_all(
            [
                DailyPrice(symbol="SYM_A", date=d1, close=100.0, volume=1000),
                DailyPrice(symbol="SYM_A", date=d2, close=102.0, volume=1200),
            ]
        )
        session.commit()

        # 3. Cache parquet for d1 (Pre-T64b format: NO TradeDate column, no IPO)
        df_d1 = pd.DataFrame(
            [
                {
                    "Symbol": "SYM_A",
                    "Open": 100.0,
                    "High": 101.0,
                    "Low": 99.0,
                    "Close": 100.0,
                    "Volume": 1000,
                },
                {
                    "Symbol": "SYM_B",
                    "Open": 200.0,
                    "High": 205.0,
                    "Low": 195.0,
                    "Close": 202.0,
                    "Volume": 5000,
                },
            ]
        )
        pq.write_table(pa.Table.from_pandas(df_d1), cache_dir / f"{d1.isoformat()}.parquet")

        # Notice: d2 cache file is intentionally MISSING to verify downloader.fetch fallback!
        mock_downloader = MagicMock()
        mock_downloader.cache_dir = cache_dir

        # When fetch(d2) is called, it creates the file with SYM_A, SYM_B, SYM_IPO
        def _mock_fetch(fetch_date: date):
            df_d2 = pd.DataFrame(
                [
                    {
                        "Symbol": "SYM_A",
                        "Open": 101.0,
                        "High": 103.0,
                        "Low": 100.0,
                        "Close": 102.0,
                        "Volume": 1200,
                    },
                    {
                        "Symbol": "SYM_B",
                        "Open": 202.0,
                        "High": 208.0,
                        "Low": 201.0,
                        "Close": 207.0,
                        "Volume": 6000,
                    },
                    {
                        "Symbol": "SYM_IPO",
                        "Open": 50.0,
                        "High": 55.0,
                        "Low": 48.0,
                        "Close": 52.0,
                        "Volume": 80000,
                    },
                ]
            )
            pq.write_table(
                pa.Table.from_pandas(df_d2), cache_dir / f"{fetch_date.isoformat()}.parquet"
            )
            return df_d2

        mock_downloader.fetch.side_effect = _mock_fetch

        etl = BhavcopyETL()
        etl._downloader = mock_downloader
        etl.cache_dir = cache_dir

        with (
            patch("src.etl.pipeline.rebuild_adjusted") as mock_rebuild,
            patch("src.db.data_service.load_indicator_snapshot") as mock_refresh,
        ):
            res = etl.fill_missing_symbols(session=session)

            assert res["status"] == "success"
            assert res["dates_processed"] == 2
            assert res["symbols_added"] == 2  # SYM_B and SYM_IPO added
            mock_downloader.fetch.assert_called_once_with(d2)
            mock_rebuild.assert_called_once()
            mock_refresh.assert_called_once()

        # Check rows in DB
        rows_d1 = {
            dp.symbol: dp
            for dp in session.scalars(select(DailyPrice).where(DailyPrice.date == d1)).all()
        }
        assert "SYM_A" in rows_d1
        assert "SYM_B" in rows_d1
        assert "SYM_IPO" not in rows_d1  # IPO had not listed yet on d1

        rows_d2 = {
            dp.symbol: dp
            for dp in session.scalars(select(DailyPrice).where(DailyPrice.date == d2)).all()
        }
        assert "SYM_A" in rows_d2
        assert "SYM_B" in rows_d2
        assert "SYM_IPO" in rows_d2  # IPO listed on d2!

        # Second run is idempotent (no missing symbols left)
        res2 = etl.fill_missing_symbols(session=session)
        assert res2["rows_added"] == 0
        assert res2["symbols_added"] == 0


# ===========================================================================
# 8. Indicator Window & Warmup (sma_200 valid on first kept row)
# ===========================================================================
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


# ===========================================================================
# 9. Regression Tests: index_name populated from stock_master.cap_bucket & Swing Screeners
# ===========================================================================
def test_universe_mode_writes_cap_bucket_and_never_overwrites_with_null(sqlite_db, monkeypatch):
    """(a) After an ingestion run in universe mode, stored rows have non-NULL index_name
    matching stock_master.cap_bucket.
    (b) Subsequent upsert with index_name=NULL never overwrites stored non-NULL value.
    """
    _engine, session_factory = sqlite_db
    d1 = date(2026, 1, 15)

    with session_factory() as session:
        # Populate stock_master
        session.add_all(
            [
                StockMaster(
                    symbol="RELIANCE",
                    company_name="Reliance Industries",
                    cap_bucket="largecap",
                    in_universe=True,
                    status="active",
                    added_on=d1,
                ),
                StockMaster(
                    symbol="MIDCAP_CO",
                    company_name="Midcap Co",
                    cap_bucket="midcap",
                    in_universe=True,
                    status="active",
                    added_on=d1,
                ),
                StockMaster(
                    symbol="UNKNOWN_CO",
                    company_name="Unknown Co",
                    cap_bucket="",  # empty cap bucket -> defaults to 'Unknown'
                    in_universe=True,
                    status="active",
                    added_on=d1,
                ),
            ]
        )
        session.commit()

        # Check get_universe_cap_buckets helper
        cap_map = get_universe_cap_buckets(session=session)
        assert cap_map["RELIANCE"] == "largecap"
        assert cap_map["MIDCAP_CO"] == "midcap"
        assert cap_map["UNKNOWN_CO"] == "Unknown"

        # Simulate collected rows in universe mode using the universe map
        stock_rows = {
            "RELIANCE": [
                {
                    "symbol": "RELIANCE",
                    "date": d1,
                    "open": 2500.0,
                    "high": 2550.0,
                    "low": 2490.0,
                    "close": 2540.0,
                    "volume": 1_000_000,
                    "prev_close": 2490.0,
                    "index_name": cap_map.get("RELIANCE", "Unknown"),
                }
            ],
            "MIDCAP_CO": [
                {
                    "symbol": "MIDCAP_CO",
                    "date": d1,
                    "open": 500.0,
                    "high": 520.0,
                    "low": 495.0,
                    "close": 515.0,
                    "volume": 300_000,
                    "prev_close": 495.0,
                    "index_name": cap_map.get("MIDCAP_CO", "Unknown"),
                }
            ],
        }

        all_rows = [r for rows in stock_rows.values() for r in rows]
        upsert_rows(session, DailyPrice, all_rows, index_elements=["symbol", "date"])
        session.commit()

        # Verify non-NULL index_name stored
        rel = session.scalars(select(DailyPrice).where(DailyPrice.symbol == "RELIANCE")).one()
        assert rel.index_name == "largecap"

        mid = session.scalars(select(DailyPrice).where(DailyPrice.symbol == "MIDCAP_CO")).one()
        assert mid.index_name == "midcap"

        # TEST UPSERT SAFETY: upsert incoming row with index_name=None ->
        # must NOT overwrite stored value
        update_row = [
            {
                "symbol": "RELIANCE",
                "date": d1,
                "close": 2560.0,
                "volume": 1_200_000,
                "index_name": None,  # incoming None
            }
        ]
        upsert_rows(session, DailyPrice, update_row, index_elements=["symbol", "date"])
        session.commit()

        rel_after = session.scalars(select(DailyPrice).where(DailyPrice.symbol == "RELIANCE")).one()
        assert float(rel_after.close) == 2560.0
        assert rel_after.volume == 1_200_000
        # CRITICAL: index_name must STILL be 'largecap', NOT None!
        assert rel_after.index_name == "largecap"


def test_swing_screeners_return_symbol_from_universe_mode():
    """(b) swing_volume and swing_momentum return a symbol whose rows came through
    the universe mode path (index_name='largecap') and pass every other rule.
    """
    from src.screeners.catalog import SwingMomentumScreener, SwingVolumeScreener

    # A stock row produced from universe mode where index_name is 'largecap'
    row = {
        "symbol": "SWING_STOCK",
        "date": date(2026, 1, 15),
        "close": 200.0,
        "ema_20": 185.0,
        "sma_50": 170.0,
        "sma_200": 150.0,  # ma_alignment: ema_20 > sma_50 > sma_200; close > 50, 200
        "high_252d_prev": 220.0,  # within 15% of 52W high (0.85 * 220 = 187 <= 200)
        "volume": 500_000,  # volume > 1.5 * volume_sma_20
        "volume_sma_20": 300_000,  # volume_sma_20 > 250k
        "index_name": "largecap",  # From stock_master!
        "return_60d": 20.0,  # <= 40
        "return_120d": 35.0,  # >= 30
        "close_180d": 120.0,
        "close_252d": 100.0,  # ratio_180_252 = (200 - 120) / 100 * 100 = 80.0 (50 <= x <= 300)
    }
    df = pd.DataFrame([row])

    # 1. Swing Volume Screener
    s1 = SwingVolumeScreener()
    assert s1.default_params["enforce_universe"] is False
    res_s1 = s1.run(df)
    assert len(res_s1) == 1
    assert res_s1.iloc[0]["symbol"] == "SWING_STOCK"

    # 2. Swing Momentum Screener
    s2 = SwingMomentumScreener()
    assert s2.default_params["enforce_universe"] is False
    res_s2 = s2.run(df)
    assert len(res_s2) == 1
    assert res_s2.iloc[0]["symbol"] == "SWING_STOCK"

    # 3. Backward compatibility: if enforce_universe=True, 'largecap' was not
    # in legacy TRACKED_INDICES
    res_s1_enforced = SwingVolumeScreener(enforce_universe=True).run(df)
    assert len(res_s1_enforced) == 0

    res_s2_enforced = SwingMomentumScreener(enforce_universe=True).run(df)
    assert len(res_s2_enforced) == 0
