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
from collections.abc import Callable, Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from config import settings
from src.common.db import use_session
from src.common.trading_calendar import get_trading_dates
from src.db.models import DailyPrice
from src.ingestion.bhavcopy_parser import parse_bhavcopy
from src.ingestion.nse_files import bhavcopy_url, fetch

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


def verify_prices(
    check_dates: Sequence[date] | None = None,
    *,
    session: Session | None = None,
    fetch_fn: Callable[..., bytes] = fetch,
    parse_fn: Callable[[bytes, date | str], pd.DataFrame] = parse_bhavcopy,
    report_path: Path | None = None,
    pause_seconds: float = 0.2,
    sleep_fn: Callable[[float], Any] = time.sleep,
) -> tuple[int, pd.DataFrame]:
    """Verify stored daily_prices by re-downloading fresh files directly from NSE.

    Compares every column: symbol, series, open, high, low, close, prev_close, volume, isin.
    In addition, spot-checks 3-5 individual stocks per date.

    Returns
    -------
    tuple[int, pd.DataFrame]
        Total number of differences found, and DataFrame summary per date.
    """
    rep_path = report_path or (settings.REPORTS_DIR / "verify_prices.csv")
    rep_path.parent.mkdir(parents=True, exist_ok=True)

    summary_rows: list[dict[str, Any]] = []
    total_diff_count: int = 0

    def _run_verification(s: Session) -> None:
        nonlocal total_diff_count
        dates_to_check: list[date]
        if check_dates is not None:
            dates_to_check = list(check_dates)
        else:
            all_dates = get_trading_dates(s)
            dates_to_check = select_check_dates(all_dates, sample_size=40, seed=7)

        logger.info("Starting price accuracy check across %d dates...", len(dates_to_check))

        for idx, d in enumerate(dates_to_check, 1):
            url = bhavcopy_url(d)
            try:
                # 1. Fresh download from NSE (bypasses all local caches)
                raw_bytes = fetch_fn(url)
                file_df = parse_fn(raw_bytes, d)
                if pause_seconds > 0:
                    sleep_fn(pause_seconds)
            except Exception as exc:
                logger.error("Failed fresh download for %s: %s", d, exc)
                summary_rows.append(
                    {
                        "date": d.isoformat(),
                        "file_rows": 0,
                        "db_rows": 0,
                        "missing_in_db": 0,
                        "extra_in_db": 0,
                        "changed_values": 1,
                        "status": f"FETCH_ERROR: {exc}",
                    }
                )
                total_diff_count += 1
                continue

            # 2. Query database for date d
            stmt = select(DailyPrice).where(DailyPrice.date == d)
            db_records = list(s.scalars(stmt).all())

            # Convert DB records to mapping keyed by symbol
            db_by_sym = {row.symbol: row for row in db_records}
            file_by_sym = {row["symbol"]: row for _, row in file_df.iterrows()}

            file_symbols = set(file_by_sym.keys())
            db_symbols = set(db_by_sym.keys())

            missing_in_db = len(file_symbols - db_symbols)
            extra_in_db = len(db_symbols - file_symbols)

            changed_values = 0
            mismatch_details: list[str] = []

            # Compare all matching symbols on all columns
            common_symbols = sorted(file_symbols & db_symbols)
            for sym in common_symbols:
                f_row = file_by_sym[sym]
                db_row = db_by_sym[sym]

                cols_to_check = (
                    ("series", f_row["series"], db_row.series),
                    ("open", f_row["open"], db_row.open),
                    ("high", f_row["high"], db_row.high),
                    ("low", f_row["low"], db_row.low),
                    ("close", f_row["close"], db_row.close),
                    ("prev_close", f_row["prev_close"], db_row.prev_close),
                    ("volume", f_row["volume"], db_row.volume),
                    ("isin", f_row.get("isin"), db_row.isin),
                )

                sym_changed = False
                for col_name, f_val, db_val in cols_to_check:
                    if not _values_equal(f_val, db_val):
                        sym_changed = True
                        mismatch_details.append(
                            f"{sym}.{col_name}: file={f_val!r} != db={db_val!r}"
                        )

                if sym_changed:
                    changed_values += 1

            # 3. Multi-Stock Sample Verification (3 to 5 stocks)
            # Pick 1 fixed anchor stock (RELIANCE if available, else first) + 2-4 random stocks
            sample_stocks: list[str] = []
            if "RELIANCE" in common_symbols:
                sample_stocks.append("RELIANCE")
            elif common_symbols:
                sample_stocks.append(common_symbols[0])

            other_candidates = [s_sym for s_sym in common_symbols if s_sym not in sample_stocks]
            if other_candidates:
                # Deterministic sample per date
                date_rng = random.Random(int(d.strftime("%Y%m%d")))
                sample_count = min(3, len(other_candidates))
                sample_stocks.extend(date_rng.sample(other_candidates, sample_count))

            sample_info = []
            for s_sym in sample_stocks:
                f_row = file_by_sym[s_sym]
                d_row = db_by_sym[s_sym]
                sample_info.append(
                    f"{s_sym}(series={d_row.series}, close={d_row.close}, vol={d_row.volume})"
                )

            date_diffs = missing_in_db + extra_in_db + changed_values
            total_diff_count += date_diffs
            status = "PASS" if date_diffs == 0 else "FAIL"

            logger.info(
                "[%d/%d] Date %s: %d file rows, %d DB rows -> %s. Spot-checked: %s",
                idx,
                len(dates_to_check),
                d,
                len(file_symbols),
                len(db_symbols),
                status,
                ", ".join(sample_info),
            )

            if date_diffs > 0:
                logger.error(
                    "Date %s MISMATCH: missing=%d, extra=%d, changed=%d. First diffs: %s",
                    d,
                    missing_in_db,
                    extra_in_db,
                    changed_values,
                    mismatch_details[:5],
                )

            summary_rows.append(
                {
                    "date": d.isoformat(),
                    "file_rows": len(file_symbols),
                    "db_rows": len(db_symbols),
                    "missing_in_db": missing_in_db,
                    "extra_in_db": extra_in_db,
                    "changed_values": changed_values,
                    "details": "; ".join(mismatch_details[:5]),
                    "status": status,
                }
            )

    with use_session(session) as s:
        _run_verification(s)

    report_df = pd.DataFrame(summary_rows)
    report_df.to_csv(rep_path, index=False)
    logger.info("Saved verification report to %s", rep_path)

    # Print summary table
    print("\n" + "=" * 80)
    print("INDEPENDENT PRICE VERIFICATION REPORT (D7)")
    print("=" * 80)
    print(
        f"{'Date':<12} {'File Rows':<11} {'DB Rows':<11} {'Missing':<9} "
        f"{'Extra':<8} {'Changed':<9} {'Status':<6}"
    )
    print("-" * 80)
    for _, r in report_df.iterrows():
        print(
            f"{r['date']:<12} {r['file_rows']:<11} {r['db_rows']:<11} "
            f"{r['missing_in_db']:<9} {r['extra_in_db']:<8} "
            f"{r['changed_values']:<9} {r['status']:<6}"
        )
    print("=" * 80)
    print(
        f"Total dates checked: {len(report_df)} | Total differences: {total_diff_count} | "
        f"Result: {'PASS (0 diffs)' if total_diff_count == 0 else 'FAIL (discrepancies found)'}\n"
    )

    return total_diff_count, report_df


def main() -> None:
    """CLI entrypoint for standalone scripts/verify_prices.py."""
    diff_count, _ = verify_prices()
    if diff_count > 0:
        logger.error("Verification failed with %d differences. Exiting with code 1.", diff_count)
        sys.exit(1)
    logger.info("Verification succeeded with 0 differences.")
    sys.exit(0)


if __name__ == "__main__":
    main()
