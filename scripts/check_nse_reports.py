#!/usr/bin/env python3
"""Live health check for the three official NSE reports (corporate actions CA1).

What it does: downloads each report from NSE right now and checks that the format is still
the one our parser knows. It writes NOTHING (no database, no files).

Why: NSE changes report formats from time to time. Run this when something looks wrong,
or about once a month, and you will hear about a change before it can damage the data.

Run from the project folder:
    python scripts/check_nse_reports.py

Exit code 0 = everything as expected, 1 = at least one check failed (read the FAIL lines).
This script talks to the real NSE website, so it is never run by pytest or CI.
"""

from __future__ import annotations

import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

# Allow "python scripts/check_nse_reports.py" from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import CORP_ACTIONS_MAX_UNPARSED_SHARE
from src.ingestion.corporate_actions import (
    REQUIRED_API_FIELDS,
    NSEDownloadError,
    NSEFormatChangedError,
    NSEReportsDownloader,
    build_action_rows,
)
from src.ingestion.corporate_actions_parser import unparsed_price_share

# Real events with a known exact factor (verified on NSE's site on 18 Nov 2026).
SPOT_CHECKS: tuple[tuple[str, date, float], ...] = (
    ("HARDWYN", date(2023, 6, 5), 0.075),  # split 10 to 1 and bonus 1:3 on one day
    ("COCHINSHIP", date(2024, 1, 10), 0.5),  # split 10 to 5
    ("AHCL", date(2026, 4, 24), 0.1),  # split 10 to 2 and bonus 1:1 on one day
)
# Fields NSE sent in 2026 besides the required ones. Anything else is reported as "new".
KNOWN_OPTIONAL_FIELDS = {
    "bcEndDate",
    "bcStartDate",
    "caBroadcastDate",
    "ind",
    "ndEndDate",
    "ndStartDate",
}

failures: list[str] = []


def report(ok: bool, label: str, detail: str = "") -> None:
    """Print one PASS or FAIL line and remember failures for the exit code."""
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  ({detail})" if detail else ""))
    if not ok:
        failures.append(label)


def check_corporate_actions(downloader: NSEReportsDownloader) -> None:
    """Fetch 2023 to today and check shape, fields, wording and known factors."""
    print("\n== 1. Corporate actions (NSE website JSON API)")
    start = date(2023, 1, 1)
    end = date.today()
    try:
        rows = downloader.fetch_corporate_actions(start, end)
    except (NSEDownloadError, NSEFormatChangedError) as exc:
        report(False, "download and shape check", str(exc))
        return
    report(len(rows) > 5000, "row count looks normal", f"{len(rows)} rows since {start}")

    fields = set().union(*(row.keys() for row in rows))
    new_fields = sorted(fields - set(REQUIRED_API_FIELDS) - KNOWN_OPTIONAL_FIELDS)
    report(not new_fields, "no unknown new fields", f"new: {new_fields}" if new_fields else "")

    action_rows, parts = build_action_rows(rows, datetime.now())
    share = unparsed_price_share(parts)
    report(
        share <= CORP_ACTIONS_MAX_UNPARSED_SHARE,
        "split/bonus wording still readable",
        f"{share:.2%} unreadable, limit {CORP_ACTIONS_MAX_UNPARSED_SHARE:.0%}",
    )
    print("      actions by type:", dict(Counter(row["action_type"] for row in action_rows)))

    for symbol, day, expected in SPOT_CHECKS:
        factors = [
            row["price_factor"]
            for row in action_rows
            if row["symbol"] == symbol and row["ex_date"] == day and row["price_factor"]
        ]
        product = 1.0
        for factor in factors:
            product *= factor
        report(
            bool(factors) and abs(product - expected) < 1e-6,
            f"spot check {symbol} {day}",
            f"factor {product:.6f}, expected {expected}",
        )


def check_symbol_changes(downloader: NSEReportsDownloader) -> None:
    """Check symbolchange.csv columns, size and one known rename."""
    print("\n== 2. Symbol changes (symbolchange.csv)")
    try:
        _, frame = downloader.fetch_symbol_changes()
    except (NSEDownloadError, NSEFormatChangedError) as exc:
        report(False, "download and column check", str(exc))
        return
    report(len(frame) > 500, "row count looks normal", f"{len(frame)} rows")
    known = frame[(frame.old_symbol == "ICICIPRULI") & (frame.new_symbol == "ICICILIFE")]
    report(not known.empty, "known rename ICICIPRULI -> ICICILIFE present")


def check_listing_master(downloader: NSEReportsDownloader) -> None:
    """Check EQUITY_L.csv columns, size and one known ISIN."""
    print("\n== 3. Listing master (EQUITY_L.csv)")
    try:
        _, frame = downloader.fetch_listing_master()
    except (NSEDownloadError, NSEFormatChangedError) as exc:
        report(False, "download and column check", str(exc))
        return
    report(len(frame) > 1500, "row count looks normal", f"{len(frame)} rows")
    known = frame[frame.symbol == "360ONE"]
    report(
        not known.empty and known.iloc[0]["isin"] == "INE466L01038",
        "known ISIN for 360ONE",
    )


def main() -> int:
    """Run all checks and return the process exit code."""
    downloader = NSEReportsDownloader()
    check_corporate_actions(downloader)
    check_symbol_changes(downloader)
    check_listing_master(downloader)
    print()
    if failures:
        print(f"RESULT: {len(failures)} check(s) FAILED: {failures}")
        print("NSE may have changed a report. Do not trust new downloads until this is fixed.")
        return 1
    print("RESULT: all checks passed. NSE formats match what the parser expects.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
