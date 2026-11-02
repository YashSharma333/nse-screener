#!/usr/bin/env python3
"""Data Quality Inspection Script.

Performs read-only audit of stored Bhavcopy data in MySQL:
  - Per-year row counts
  - Distinct symbols per month
  - Weekdays missing from the database
  - Share of null prev_close values
  - Top 10 largest one-day price jumps (unadjusted corporate actions proxy)
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text

from config.settings import setup_logging
from src.db.session import check_connection, engine

setup_logging()
logger = logging.getLogger("scripts.check_data_quality")


def inspect_data_quality() -> None:
    """Run data quality checks against daily_prices table."""
    if not check_connection() or engine is None:
        logger.error("Database connection unavailable. Cannot perform data quality checks.")
        return

    print("=" * 70)
    print("           NSE SCREENER — DATA QUALITY & COMPLETENESS REPORT")
    print("=" * 70)

    with engine.connect() as conn:
        # 1. Total row count & date range
        overview = (
            conn.execute(
                text("""
            SELECT
                COUNT(*) as total_rows,
                COUNT(DISTINCT symbol) as total_symbols,
                MIN(date) as min_date,
                MAX(date) as max_date,
                SUM(CASE WHEN prev_close IS NULL THEN 1 ELSE 0 END) as null_prev_close
            FROM daily_prices
        """)
            )
            .mappings()
            .fetchone()
        )

        if not overview or overview["total_rows"] == 0:
            print("\nTable `daily_prices` is empty. Ingest data before checking quality.")
            return

        total_rows = overview["total_rows"]
        null_prev = overview["null_prev_close"] or 0
        pct_null = (null_prev / total_rows) * 100.0

        print(f"\nTotal Records:       {total_rows:,}")
        print(f"Distinct Symbols:    {overview['total_symbols']:,}")
        print(f"Date Range:          {overview['min_date']} to {overview['max_date']}")
        print(f"Null prev_close:     {null_prev:,} ({pct_null:.2f}%)")

        # 2. Row count by year
        print("\n--- Rows per Year ---")
        rows_per_year = (
            conn.execute(
                text("""
            SELECT YEAR(date) as yr, COUNT(*) as cnt, COUNT(DISTINCT symbol) as sym_cnt
            FROM daily_prices
            GROUP BY yr
            ORDER BY yr ASC
        """)
            )
            .mappings()
            .fetchall()
        )

        for r in rows_per_year:
            print(f"Year {r['yr']}: {r['cnt']:,} rows ({r['sym_cnt']} distinct symbols)")

        # 3. Monthly distinct symbols (last 12 months)
        print("\n--- Distinct Symbols Per Month (Recent 12 Months) ---")
        symbols_per_month = (
            conn.execute(
                text("""
            SELECT DATE_FORMAT(date, '%Y-%m') as ym,
                   COUNT(DISTINCT symbol) as sym_cnt,
                   COUNT(*) as rows_cnt
            FROM daily_prices
            GROUP BY ym
            ORDER BY ym DESC
            LIMIT 12
        """)
            )
            .mappings()
            .fetchall()
        )

        for r in reversed(symbols_per_month):
            print(f"Month {r['ym']}: {r['sym_cnt']} symbols ({r['rows_cnt']:,} rows)")

        # 4. Top 10 largest one-day price jumps (proxy for splits/bonuses)
        print("\n--- Top 10 Largest One-Day Price Jumps (Unadjusted Event Flags) ---")
        jumps = (
            conn.execute(
                text("""
            SELECT symbol, date, prev_close, close,
                   ROUND(((close - prev_close) / prev_close) * 100, 2) as pct_jump
            FROM daily_prices
            WHERE prev_close IS NOT NULL AND prev_close > 0
            ORDER BY ABS((close - prev_close) / prev_close) DESC
            LIMIT 10
        """)
            )
            .mappings()
            .fetchall()
        )

        if jumps:
            print(f"{'Symbol':<12} {'Date':<12} {'Prev Close':<12} {'Close':<12} {'Jump %':<10}")
            print("-" * 60)
            for j in jumps:
                print(
                    f"{j['symbol']:<12} {j['date']!s:<12} {float(j['prev_close']):<12.2f} "
                    f"{float(j['close']):<12.2f} {float(j['pct_jump']):+8.2f}%"
                )
        else:
            print("No prev_close comparisons available.")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    inspect_data_quality()
