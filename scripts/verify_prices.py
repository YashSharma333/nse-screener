"""
Independent Price Verification Script (Task D7)
================================================
Re-downloads ~48 trading dates fresh from NSE (bypassing all local caches),
compares them row-by-row and column-by-column against daily_prices in MySQL,
and writes reports/verify_prices.csv.

If any difference is found (missing rows, extra rows, or altered values):
Exits with code 1. By the project delete-all rule: NO manual patching of rows.
"""

from __future__ import annotations

import logging
import random
import sys
import time
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from config import settings
from src.common.db import use_session
from src.common.trading_calendar import get_trading_dates
from src.db.models import DailyPrice
from src.ingestion import nse_files
from src.ingestion.bhavcopy_parser import parse_bhavcopy

logger = logging.getLogger(__name__)


def select_check_dates(
    all_dates: Sequence[date],
    sample_size: int = 40,
    seed: int = 7,
) -> list[date]:
    """Select ~48 check dates: 40 seeded random dates + mandatory landmark dates.

    Mandatory landmark dates:
    - 2016-11-21 (earliest historical start)
    - 2023-07-12 (historic corporate action / HDFC last day)
    - 2024-01-01 (UDiFF format cut-over date)
    - 2024-07-05 (last date old Bhavcopy format was published)
    - 2024-07-08 (first date purely in UDiFF format)
    - One Budget Saturday (e.g. 2020-02-01)
    - One Diwali Muhurat Sunday (e.g. 2023-11-12)
    - Latest loaded date in database
    """
    date_set = set(all_dates)
    if not date_set:
        return []

    mandatory: list[date] = []

    # Fixed historical landmarks if present in data
    for d_fixed in (
        date(2016, 11, 21),
        date(2023, 7, 12),
        date(2024, 1, 1),
        date(2024, 7, 5),
        date(2024, 7, 8),
    ):
        if d_fixed in date_set:
            mandatory.append(d_fixed)

    # Budget Saturday (Saturday on Feb 1 or any Saturday)
    saturdays = [d for d in all_dates if d.weekday() == 5]
    budget_saturday = next(
        (d for d in saturdays if d.month == 2 and d.day == 1),
        saturdays[0] if saturdays else None,
    )
    if budget_saturday and budget_saturday in date_set and budget_saturday not in mandatory:
        mandatory.append(budget_saturday)

    # Diwali Sunday (Sunday in Oct/Nov or any Sunday)
    sundays = [d for d in all_dates if d.weekday() == 6]
    diwali_sunday = next(
        (d for d in sundays if d.month in (10, 11)),
        sundays[0] if sundays else None,
    )
    if diwali_sunday and diwali_sunday in date_set and diwali_sunday not in mandatory:
        mandatory.append(diwali_sunday)

    # Latest date
    max_d = max(all_dates)
    if max_d not in mandatory:
        mandatory.append(max_d)

    # Sample remaining dates deterministically
    remaining = sorted(date_set - set(mandatory))
    rng = random.Random(seed)
    sampled = rng.sample(remaining, min(sample_size, len(remaining)))

    return sorted(set(mandatory + sampled))


def _values_equal(val_a: Any, val_b: Any) -> bool:
    """Compare two database / file values with numeric tolerance."""
    if val_a is None and val_b is None:
        return True
    if val_a is None or val_b is None:
        return False

    # Compare numbers as float/Decimal rounded to 2 decimal places
    try:
        dec_a = round(Decimal(str(val_a)), 2)
        dec_b = round(Decimal(str(val_b)), 2)
        return dec_a == dec_b
    except Exception:
        pass

    return str(val_a).strip() == str(val_b).strip()


COMPARED_COLUMNS = ("series", "open", "high", "low", "close", "prev_close", "volume", "isin")


def download_fresh_file(d: date) -> pd.DataFrame:
    """Download and parse NSE's Bhavcopy for ``d``, bypassing every local cache."""
    file_df = parse_bhavcopy(nse_files.fetch(nse_files.bhavcopy_url(d)), d)
    if settings.PRICE_DOWNLOAD_PAUSE_SECONDS > 0:
        time.sleep(settings.PRICE_DOWNLOAD_PAUSE_SECONDS)  # be polite to NSE
    return file_df


def changed_columns(file_row: pd.Series, db_row: DailyPrice) -> list[str]:
    """Describe every compared column where the file and the database row disagree."""
    return [
        f"{db_row.symbol}.{column}: file={file_row.get(column)!r} != db={getattr(db_row, column)!r}"
        for column in COMPARED_COLUMNS
        if not _values_equal(file_row.get(column), getattr(db_row, column))
    ]


def compare_day(file_df: pd.DataFrame, db_rows: list[DailyPrice]) -> dict[str, Any]:
    """Compare one day's file with the stored rows: missing, extra and changed symbols."""
    file_by_symbol = {row["symbol"]: row for _, row in file_df.iterrows()}
    db_by_symbol = {row.symbol: row for row in db_rows}

    details: list[str] = []
    changed = 0
    for symbol in sorted(file_by_symbol.keys() & db_by_symbol.keys()):
        differences = changed_columns(file_by_symbol[symbol], db_by_symbol[symbol])
        details.extend(differences)
        changed += bool(differences)

    missing = len(file_by_symbol.keys() - db_by_symbol.keys())
    extra = len(db_by_symbol.keys() - file_by_symbol.keys())
    return {
        "file_rows": len(file_by_symbol),
        "db_rows": len(db_by_symbol),
        "missing_in_db": missing,
        "extra_in_db": extra,
        "changed_values": changed,
        "details": "; ".join(details[:5]),
        "status": "PASS" if missing + extra + changed == 0 else "FAIL",
    }


def verify_one_date(session: Session, d: date) -> dict[str, Any]:
    """One report row for date ``d``: fresh NSE file against the stored prices."""
    try:
        file_df = download_fresh_file(d)
    except Exception as exc:  # any failure (network, bad file) counts as a difference
        logger.error("Failed fresh download for %s: %s", d, exc)
        return {
            "date": d.isoformat(),
            "file_rows": 0,
            "db_rows": 0,
            "missing_in_db": 0,
            "extra_in_db": 0,
            "changed_values": 1,
            "details": "",
            "status": f"FETCH_ERROR: {exc}",
        }

    db_rows = list(session.scalars(select(DailyPrice).where(DailyPrice.date == d)).all())
    row = {"date": d.isoformat(), **compare_day(file_df, db_rows)}
    if row["status"] == "FAIL":
        logger.error(
            "Date %s MISMATCH: missing=%d, extra=%d, changed=%d. First diffs: %s",
            d,
            row["missing_in_db"],
            row["extra_in_db"],
            row["changed_values"],
            row["details"],
        )
    return row


def count_differences(report: pd.DataFrame) -> int:
    """Total differences in the report (missing + extra + changed symbols, fetch errors count 1)."""
    if report.empty:
        return 0
    return int(report[["missing_in_db", "extra_in_db", "changed_values"]].sum().sum())


def print_summary(report: pd.DataFrame, differences: int) -> None:
    """Print the per-date table and the PASS/FAIL line."""
    print("\n" + "=" * 80)
    print("INDEPENDENT PRICE VERIFICATION REPORT (D7)")
    print("=" * 80)
    print(
        f"{'Date':<12} {'File Rows':<11} {'DB Rows':<11} {'Missing':<9} "
        f"{'Extra':<8} {'Changed':<9} {'Status':<6}"
    )
    print("-" * 80)
    for r in report.itertuples():
        print(
            f"{r.date:<12} {r.file_rows:<11} {r.db_rows:<11} "
            f"{r.missing_in_db:<9} {r.extra_in_db:<8} {r.changed_values:<9} {r.status:<6}"
        )
    print("=" * 80)
    print(
        f"Total dates checked: {len(report)} | Total differences: {differences} | "
        f"Result: {'PASS (0 diffs)' if differences == 0 else 'FAIL (discrepancies found)'}\n"
    )


def verify_prices(
    check_dates: Sequence[date] | None = None,
    *,
    session: Session | None = None,
) -> tuple[int, pd.DataFrame]:
    """Verify stored daily_prices by re-downloading fresh files directly from NSE.

    Compares every column of ``COMPARED_COLUMNS`` for every symbol of every checked date
    (default: ``select_check_dates`` over all stored dates). Writes
    reports/verify_prices.csv and prints a PASS/FAIL summary.

    Returns the total number of differences and the report (one row per date).
    """
    with use_session(session) as s:
        dates = (
            list(check_dates)
            if check_dates is not None
            else select_check_dates(get_trading_dates(s))
        )
        logger.info("Starting price accuracy check across %d dates...", len(dates))
        rows = []
        for number, d in enumerate(dates, start=1):
            row = verify_one_date(s, d)
            logger.info("[%d/%d] Date %s -> %s", number, len(dates), d, row["status"])
            rows.append(row)

    report = pd.DataFrame(rows)
    report_path = settings.REPORTS_DIR / "verify_prices.csv"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report.to_csv(report_path, index=False)
    logger.info("Saved verification report to %s", report_path)

    differences = count_differences(report)
    print_summary(report, differences)
    return differences, report


def main() -> None:
    """CLI entrypoint for standalone scripts/verify_prices.py."""
    settings.setup_logging()
    diff_count, _ = verify_prices()
    if diff_count > 0:
        logger.error("Verification failed with %d differences. Exiting with code 1.", diff_count)
        sys.exit(1)
    logger.info("Verification succeeded with 0 differences.")
    sys.exit(0)


if __name__ == "__main__":
    main()
