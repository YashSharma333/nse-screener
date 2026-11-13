"""Tests for TASK T64b: Rejecting fake weekend/holiday sessions and purging duplicates.

Verifies:
1. Downloader validates trade date in file (DATE1 / TIMESTAMP) vs requested date;
   mismatch returns None, does not write parquet cache, logs a warning, and
   cached parquet includes TradeDate with date32 type.
2. Benchmark loader validates Index Date in file vs requested date;
   mismatch skips, records as non-trading, and does not write cache.
3. Purge detection identifies dates where >=90% of symbols share identical close AND volume
   (minimum 100 common symbols) compared to the previous stored session.
4. --dry-run writes reports/bogus_sessions.csv and deletes nothing.
5. --yes backs up deleted rows to reports/purged_rows_<timestamp>.csv,
   deletes rows in one transaction, removes parquet cache files, records dates
   in non-trading caches, and rebuilds adjustments.
6. Acceptance test on a fixture with the 7 genuine weekend sessions and normal weekdays.
7. CLI flags dispatch correctly.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from run_pipeline import main as run_pipeline_main
from src.db.models import Base, BenchmarkPrice, DailyPrice
from src.etl.pipeline import BhavcopyETL
from src.ingestion.benchmark import (
    NSEIndexSource,
)
from src.ingestion.benchmark import (
    load_non_trading_days as load_benchmark_non_trading_days,
)
from src.ingestion.downloader import NSEBhavcopyDownloader


@pytest.fixture
def sqlite_db(tmp_path: Path):
    """File-backed SQLite database for real schema tests."""
    db_file = tmp_path / "test_purge.db"
    engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    return engine, session_factory


# ===========================================================================
# 1. Downloader Trade Date Validation
# ===========================================================================
def test_downloader_rejects_mismatched_trade_date_friday_for_sunday(tmp_path: Path):
    """Downloader must reject a file whose DATE1 does not match the requested date.

    When Sunday 2024-10-27 is requested and NSE returns Friday 2024-10-25's file:
    - download_bhavcopy returns None
    - no parquet cache file is created
    - a valid Sunday file returns the DataFrame and stores TradeDate as date32[day].
    """
    cache_dir = tmp_path / "bhavcopy_cache"
    dl = NSEBhavcopyDownloader()
    dl._session = MagicMock()

    sunday = date(2024, 10, 27)

    # 1. Fake response: Friday's DATE1 returned for Sunday's request
    friday_csv = (
        b"SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, "
        b"LOW_PRICE, CLOSE_PRICE, TTL_TRD_QNTY\n"
        b"RELIANCE, EQ, 25-Oct-2024, 2680.00, 2685.00, 2700.00, 2670.00, 2690.00, 100000\n"
    )
    mock_resp_mismatch = MagicMock()
    mock_resp_mismatch.status_code = 200
    mock_resp_mismatch.content = friday_csv

    dl.get_session = MagicMock(return_value=dl._session)
    dl._session.get.return_value = mock_resp_mismatch

    with patch("src.ingestion.downloader.CACHE_DIR", cache_dir):
        df_result = dl.download_bhavcopy(sunday)

        # Must return None (treated as unavailable)
        assert df_result is None
        # Parquet cache must NOT be written
        cache_file = cache_dir / f"{sunday.isoformat()}.parquet"
        assert not cache_file.exists()

    # 2. Genuine Sunday file: DATE1 matches Sunday
    sunday_csv = (
        b"SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, "
        b"LOW_PRICE, CLOSE_PRICE, TTL_TRD_QNTY\n"
        b"RELIANCE, EQ, 27-Oct-2024, 2690.00, 2700.00, 2720.00, 2695.00, 2715.00, 50000\n"
    )
    mock_resp_valid = MagicMock()
    mock_resp_valid.status_code = 200
    mock_resp_valid.content = sunday_csv
    dl._session.get.return_value = mock_resp_valid

    with patch("src.ingestion.downloader.CACHE_DIR", cache_dir):
        df_valid = dl.download_bhavcopy(sunday)

        assert df_valid is not None
        assert not df_valid.empty
        assert "TradeDate" in df_valid.columns
        assert df_valid["TradeDate"].iloc[0] == sunday
        assert cache_file.exists()

        # Verify real date32 Arrow type in parquet
        table = pq.read_table(cache_file)
        field = table.schema.field("TradeDate")
        assert field.type == pa.date32()


def test_downloader_rejects_mismatched_zip_format(tmp_path: Path):
    """Historical zip format: TIMESTAMP must match requested date."""
    cache_dir = tmp_path / "bhavcopy_cache"
    dl = NSEBhavcopyDownloader()
    dl._session = MagicMock()

    sunday = date(2018, 10, 28)

    # Friday's TIMESTAMP in ZIP
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zip_content = (
            "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TIMESTAMP\n"
            "TCS,EQ,3400.0,3450.0,3390.0,3420.0,3420.0,3380.0,50000,26-OCT-2018\n"
        )
        zf.writestr("cm26OCT2018bhav.csv", zip_content)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = zip_buf.getvalue()

    dl.get_session = MagicMock(return_value=dl._session)
    # First URL format (new) returns 404, fallback to zip
    mock_404 = MagicMock(status_code=404)
    dl._session.get.side_effect = [mock_404, mock_resp]

    with patch("src.ingestion.downloader.CACHE_DIR", cache_dir):
        df_result = dl.download_bhavcopy(sunday)

        assert df_result is None
        assert not (cache_dir / f"{sunday.isoformat()}.parquet").exists()


# ===========================================================================
# 2. Benchmark Loader Trade Date Validation
# ===========================================================================
def test_benchmark_loader_rejects_mismatched_index_date(tmp_path: Path):
    """Benchmark loader must reject index close file if Index Date does not match requested date."""
    sunday = date(2024, 10, 27)
    # Index file with Friday's date
    csv_text = (
        "Index Name,Index Date,Open Index Value,High Index Value,"
        "Low Index Value,Closing Index Value\n"
        "Nifty 50,25-10-2024,24418.05,24440.25,24073.9,24180.8\n"
        "Nifty 100,25-10-2024,25305.3,25326.6,24897.2,25022.2\n"
    )

    mock_session = MagicMock()
    mock_resp = MagicMock(status_code=200, text=csv_text)
    mock_session.get.return_value = mock_resp

    test_non_trading = tmp_path / "benchmark_non_trading.json"
    with (
        patch("src.ingestion.benchmark.BENCHMARK_CACHE_DIR", tmp_path),
        patch("src.ingestion.benchmark.NON_TRADING_DAYS_FILE", test_non_trading),
    ):
        src = NSEIndexSource(session=mock_session)
        prices = src.fetch_prices(sunday)

        # Mismatch must return None
        assert prices is None
        # Must not write cache file
        assert not (tmp_path / f"{sunday.isoformat()}.parquet").exists()
        # Must record Sunday as non-trading day
        cached_days = load_benchmark_non_trading_days()
        assert sunday.isoformat() in cached_days


# ===========================================================================
# Helper: Seed Database Fixture
# ===========================================================================
def _seed_daily_prices(
    session: Session,
    dates_and_prices: list[tuple[date, dict[str, tuple[float, int]]]],
) -> None:
    """Insert bulk daily price records into SQLite for given dates.

    dates_and_prices is a list of (date, {symbol: (close, volume)}).
    """
    records = []
    for d, sym_map in dates_and_prices:
        for sym, (close_val, vol_val) in sym_map.items():
            records.append(
                DailyPrice(
                    symbol=sym,
                    date=d,
                    open=close_val - 2.0,
                    high=close_val + 5.0,
                    low=close_val - 5.0,
                    close=close_val,
                    volume=vol_val,
                    prev_close=close_val - 1.0,
                    adj_close=close_val,
                    adj_volume=vol_val,
                )
            )
    session.add_all(records)
    session.commit()


# ===========================================================================
# 3. Purge Detection & Dry-Run
# ===========================================================================
def test_purge_dry_run_lists_duplicates_and_deletes_nothing(sqlite_db, tmp_path: Path):
    """Dry-run must detect duplicate Sunday, write reports, and delete nothing."""
    _engine, session_factory = sqlite_db
    reports_dir = tmp_path / "reports"

    d_fri = date(2024, 1, 5)
    d_sun = date(2024, 1, 7)  # Duplicate copy of Friday
    d_mon = date(2024, 1, 8)  # Genuine Monday with new prices

    symbols = [f"STOCK_{i:03d}" for i in range(110)]
    fri_map = {s: (100.0 + i, 1000 + i * 10) for i, s in enumerate(symbols)}
    # Sunday has 100% identical close AND volume to Friday
    sun_map = {s: (100.0 + i, 1000 + i * 10) for i, s in enumerate(symbols)}
    # Monday has distinct prices and volumes
    mon_map = {s: (105.0 + i, 2000 + i * 10) for i, s in enumerate(symbols)}

    with session_factory() as session:
        _seed_daily_prices(session, [(d_fri, fri_map), (d_sun, sun_map), (d_mon, mon_map)])

        etl = BhavcopyETL(cache_dir=tmp_path / "bhavcopy_cache")

        result = etl.purge_bogus_sessions(
            dry_run=True,
            confirm=False,
            reports_dir=reports_dir,
            session=session,
        )

        assert result["dry_run"] is True
        assert result["bogus_count"] == 1
        assert result["purged_dates"] == [d_sun]

        # Verify CSV report was written
        report_file = reports_dir / "bogus_sessions.csv"
        assert report_file.exists()
        df_rep = pd.read_csv(report_file)
        assert len(df_rep) == 1
        assert df_rep.iloc[0]["date"] == "2024-01-07"
        assert df_rep.iloc[0]["weekday"] == "Sunday"
        assert "100.00%" in str(df_rep.iloc[0]["%identical"])

        # Verify database rows were NOT deleted
        fri_count = (
            session.execute(select(DailyPrice).where(DailyPrice.date == d_fri)).scalars().all()
        )
        sun_count = (
            session.execute(select(DailyPrice).where(DailyPrice.date == d_sun)).scalars().all()
        )
        mon_count = (
            session.execute(select(DailyPrice).where(DailyPrice.date == d_mon)).scalars().all()
        )

        assert len(fri_count) == 110
        assert len(sun_count) == 110
        assert len(mon_count) == 110


# ===========================================================================
# 4. Purge Execution with --yes
# ===========================================================================
def test_purge_with_yes_deletes_rows_backs_up_cleans_caches_and_rebuilds(sqlite_db, tmp_path: Path):
    """--yes must back up deleted rows, delete daily & benchmark prices, and rebuild."""
    engine, session_factory = sqlite_db
    reports_dir = tmp_path / "reports"
    cache_dir = tmp_path / "bhavcopy_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    bench_cache_dir = tmp_path / "benchmark_cache"
    bench_cache_dir.mkdir(parents=True, exist_ok=True)

    d_fri = date(2024, 1, 5)
    d_sun = date(2024, 1, 7)  # Duplicate copy of Friday

    symbols = [f"STOCK_{i:03d}" for i in range(110)]
    fri_map = {s: (100.0 + i, 1000 + i * 10) for i, s in enumerate(symbols)}
    sun_map = {s: (100.0 + i, 1000 + i * 10) for i, s in enumerate(symbols)}

    # Create dummy parquet cache files for Sunday
    sun_parquet = cache_dir / f"{d_sun.isoformat()}.parquet"
    sun_parquet.write_text("dummy bhavcopy cache")
    sun_bench_parquet = bench_cache_dir / f"{d_sun.isoformat()}.parquet"
    sun_bench_parquet.write_text("dummy benchmark cache")

    non_trading_file = tmp_path / "non_trading_days.json"
    bench_non_trading_file = tmp_path / "bench_non_trading.json"

    with session_factory() as session:
        _seed_daily_prices(session, [(d_fri, fri_map), (d_sun, sun_map)])

        # Also add a benchmark price row for Sunday
        session.add(
            BenchmarkPrice(
                index_code="NIFTY 50",
                date=d_sun,
                close=21500.0,
                source="NSE",
            )
        )
        session.commit()

        etl = BhavcopyETL(cache_dir=cache_dir)
        etl.non_trading_path = non_trading_file

        with (
            patch("src.etl.pipeline.PROJECT_ROOT", tmp_path),
            patch("src.ingestion.benchmark.BENCHMARK_CACHE_DIR", bench_cache_dir),
            patch("src.ingestion.benchmark.NON_TRADING_DAYS_FILE", bench_non_trading_file),
            patch("src.etl.adjust.get_db") as mock_adj_db,
            patch("src.etl.adjust.default_engine", engine),
            patch("src.db.data_service.load_indicator_snapshot") as mock_snap,
        ):
            mock_adj_db.return_value.__enter__.return_value = session

            result = etl.purge_bogus_sessions(
                dry_run=False,
                confirm=True,
                reports_dir=reports_dir,
                session=session,
            )

        assert result["dry_run"] is False
        assert result["bogus_count"] == 1
        assert result["purged_dates"] == [d_sun]
        assert result["deleted_rows"] == 111  # 110 daily_prices + 1 benchmark_prices

        # 1. Database: Sunday rows deleted; Friday rows remain
        sun_daily = (
            session.execute(select(DailyPrice).where(DailyPrice.date == d_sun)).scalars().all()
        )
        assert len(sun_daily) == 0

        sun_bench = (
            session.execute(select(BenchmarkPrice).where(BenchmarkPrice.date == d_sun))
            .scalars()
            .all()
        )
        assert len(sun_bench) == 0

        fri_daily = (
            session.execute(select(DailyPrice).where(DailyPrice.date == d_fri)).scalars().all()
        )
        assert len(fri_daily) == 110

        # 2. Backup file: created, non-empty, contains deleted Sunday rows
        backup_file = Path(result["backup_file"])
        assert backup_file.exists()
        df_backup = pd.read_csv(backup_file)
        assert len(df_backup) == 111
        assert "daily_prices" in df_backup["table"].values
        assert "benchmark_prices" in df_backup["table"].values
        assert all(df_backup["date"] == "2024-01-07")

        # 3. Cache files deleted
        assert not sun_parquet.exists()
        assert not sun_bench_parquet.exists()

        # 4. Recorded in non-trading days caches
        assert d_sun.isoformat() in json.loads(non_trading_file.read_text())
        assert d_sun.isoformat() in json.loads(bench_non_trading_file.read_text())

        # 5. Indicator cache refresh was triggered
        mock_snap.assert_called_once_with(use_adjusted=True, force_refresh=True)


# ===========================================================================
# 5. Acceptance Test on Comprehensive Fixture (ADDENDUM TO T64b)
# ===========================================================================
def test_acceptance_fixture_with_seven_genuine_weekend_sessions(sqlite_db, tmp_path: Path):
    """Acceptance test based on real DB empirics:

    - Keeps the seven genuine weekend sessions:
      2023-11-12 (Diwali Muhurat Sunday)
      2024-01-20 (Saturday DR session)
      2024-03-02 (Saturday DR session)
      2024-05-18 (Saturday DR session)
      2025-02-01 (Saturday Budget)
      2026-02-01 (Sunday session)
      2026-11-08 (Diwali Muhurat Sunday)
    - Keeps normal weekdays:
      2026-11-11 and 2026-11-12
    - Flags ONLY the duplicated fake sessions:
      2024-01-06 (Saturday duplicate of Friday)
      2024-01-07 (Sunday duplicate of Saturday)
    """
    _engine, session_factory = sqlite_db
    reports_dir = tmp_path / "reports"

    symbols = [f"STOCK_{i:03d}" for i in range(120)]  # >= 100 common symbols

    # Define test epochs:
    # 1. Copies: Friday 2024-01-05, Saturday 2024-01-06 (copy), Sunday 2024-01-07 (copy)
    d_fri_copy = date(2024, 1, 5)
    d_sat_copy = date(2024, 1, 6)
    d_sun_copy = date(2024, 1, 7)

    # 2. Seven genuine weekend sessions (paired with prior session)
    genuine_pairs = [
        (date(2023, 11, 10), date(2023, 11, 12)),  # Fri -> Sun Diwali Muhurat
        (date(2024, 1, 19), date(2024, 1, 20)),  # Fri -> Sat DR session
        (date(2024, 3, 1), date(2024, 3, 2)),  # Fri -> Sat DR session
        (date(2024, 5, 17), date(2024, 5, 18)),  # Fri -> Sat DR session
        (date(2025, 1, 31), date(2025, 2, 1)),  # Fri -> Sat Budget Day
        (date(2026, 1, 30), date(2026, 2, 1)),  # Fri -> Sun special session
        (date(2026, 11, 6), date(2026, 11, 8)),  # Fri -> Sun Diwali Muhurat
    ]

    # 3. Normal weekdays
    d_tue_norm = date(2026, 11, 10)
    d_wed_norm = date(2026, 11, 11)
    d_thu_norm = date(2026, 11, 12)

    data_payload: list[tuple[date, dict[str, tuple[float, int]]]] = []

    # Populate copied sessions
    fri_copy_map = {s: (100.0 + i, 1000 + i * 10) for i, s in enumerate(symbols)}
    sat_copy_map = {s: (100.0 + i, 1000 + i * 10) for i, s in enumerate(symbols)}  # exact copy
    sun_copy_map = {s: (100.0 + i, 1000 + i * 10) for i, s in enumerate(symbols)}  # exact copy
    data_payload.extend(
        [
            (d_fri_copy, fri_copy_map),
            (d_sat_copy, sat_copy_map),
            (d_sun_copy, sun_copy_map),
        ]
    )

    # Populate genuine weekend pairs (real trading, distinct prices & volumes)
    base_price = 200.0
    for prior_d, gen_d in genuine_pairs:
        p_map = {s: (base_price + i, 5000 + i * 20) for i, s in enumerate(symbols)}
        # Genuine session has different close (+2%) and lower volume
        g_map = {s: ((base_price + i) * 1.02, 1500 + i * 5) for i, s in enumerate(symbols)}
        data_payload.append((prior_d, p_map))
        data_payload.append((gen_d, g_map))
        base_price += 100.0

    # Populate normal weekdays (real trading, distinct prices & volumes)
    tue_map = {s: (900.0 + i, 8000 + i * 10) for i, s in enumerate(symbols)}
    wed_map = {s: (905.0 + i, 8200 + i * 10) for i, s in enumerate(symbols)}
    thu_map = {s: (910.0 + i, 8400 + i * 10) for i, s in enumerate(symbols)}
    data_payload.extend(
        [
            (d_tue_norm, tue_map),
            (d_wed_norm, wed_map),
            (d_thu_norm, thu_map),
        ]
    )

    with session_factory() as session:
        _seed_daily_prices(session, data_payload)

        etl = BhavcopyETL(cache_dir=tmp_path / "bhavcopy_cache")
        bogus_list = etl.find_bogus_sessions(session=session)

        flagged_dates = {b["date"] for b in bogus_list}

        # 1. MUST flag only the copies
        assert d_sat_copy in flagged_dates
        assert d_sun_copy in flagged_dates
        assert len(flagged_dates) == 2

        # 2. MUST KEEP the 7 genuine weekend sessions
        genuine_dates = {pair[1] for pair in genuine_pairs}
        for gd in genuine_dates:
            assert gd not in flagged_dates, f"Genuine date {gd} was improperly flagged!"

        # 3. MUST NEVER flag normal weekdays
        assert d_wed_norm not in flagged_dates
        assert d_thu_norm not in flagged_dates

        # 4. Dry-run report test
        res = etl.purge_bogus_sessions(
            dry_run=True,
            confirm=False,
            reports_dir=reports_dir,
            session=session,
        )
        assert res["bogus_count"] == 2
        df_csv = pd.read_csv(reports_dir / "bogus_sessions.csv")
        assert len(df_csv) == 2
        assert set(df_csv["date"]) == {"2024-01-06", "2024-01-07"}


# ===========================================================================
# 6. CLI Option Parsing and Dispatch
# ===========================================================================
def test_cli_purge_bogus_sessions_flag(tmp_path: Path):
    """run_pipeline.py --purge-bogus-sessions dispatches to etl.purge_bogus_sessions."""
    test_args = [
        "run_pipeline.py",
        "--purge-bogus-sessions",
        "--start",
        "2024-01-01",
        " --end",
        "2024-01-31",
        "--dry-run",
    ]
    # Clean whitespace
    test_args = [arg.strip() for arg in test_args]

    with (
        patch("sys.argv", test_args),
        patch("run_pipeline.BhavcopyETL") as mock_etl_cls,
    ):
        mock_instance = MagicMock()
        mock_instance.purge_bogus_sessions.return_value = {
            "bogus_count": 0,
            "purged_dates": [],
            "dry_run": True,
        }
        mock_etl_cls.return_value = mock_instance

        run_pipeline_main()

        mock_instance.purge_bogus_sessions.assert_called_once_with(
            start=date(2024, 1, 1),
            end=date(2024, 1, 31),
            dry_run=True,
            confirm=False,
        )
