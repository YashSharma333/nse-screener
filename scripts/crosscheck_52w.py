"""
Official 52-Week High/Low Cross-Check (Task D10, CL-32)
======================================================
Downloads official adjusted 52-week High/Low files (CM_52_wk_High_low_DDMMYYYY.csv)
for ~10 dates, compares them against our stored prices across stock rename lineages, and
writes reports/adj_crosscheck.csv.

52-week definition (NSE's own, on purpose): the highest adj_high and lowest adj_low over the
last CROSSCHECK_WINDOW_DAYS (365) CALENDAR days up to and including the checked date. It is
NOT the indicator cache's 252-bar column: the cache window is trading rows, ends the day
before, and a rarely-trading stock spans years in 252 rows. Using NSE's definition keeps this
an independent check of our data. A comparison FAILS above CROSSCHECK_TOLERANCE_PCT.

Differences are LISTED, never auto-fixed.
"""

from __future__ import annotations

import io
import logging
import random
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config import settings
from config.settings import REGULAR_SERIES
from src.common.db import use_session
from src.common.trading_calendar import get_trading_dates
from src.db.models import DailyPrice
from src.ingestion import nse_files
from src.ingestion.renames import RenameChains, family, load_chains_from_db

logger = logging.getLogger(__name__)

CROSSCHECK_COLUMNS = ["date", "symbol", "metric", "official", "ours", "difference_pct"]


def parse_52_wk_csv(content: bytes | str) -> pd.DataFrame:
    """Parse CM_52_wk_High_low_DDMMYYYY.csv content into a standardized DataFrame.

    Skips disclaimer and effective-date header lines, filters to regular series,
    and returns numeric official high and low columns.
    """
    text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
    lines = text.strip().splitlines()
    if len(lines) < 3:
        return pd.DataFrame(
            columns=["symbol", "series", "official_high", "official_low", "high_date", "low_date"]
        )

    # Line 0 is disclaimer, Line 1 is effective date, Line 2 is CSV header
    data_stream = io.StringIO("\n".join(lines[2:]))
    df = pd.read_csv(data_stream, dtype=str)
    df.columns = [c.strip().replace('"', "") for c in df.columns]

    req_cols = ["SYMBOL", "SERIES", "Adjusted 52_Week_High", "Adjusted 52_Week_Low"]
    missing = [c for c in req_cols if c not in df.columns]
    if missing:
        raise ValueError(f"52-week file missing required columns: {missing}")

    df = df[df["SERIES"].isin(REGULAR_SERIES)].copy()
    df["symbol"] = df["SYMBOL"].str.strip().str.upper()
    df["series"] = df["SERIES"].str.strip()
    df["official_high"] = pd.to_numeric(
        df["Adjusted 52_Week_High"].str.strip().str.replace(",", ""), errors="coerce"
    )
    df["official_low"] = pd.to_numeric(
        df["Adjusted 52_Week_Low"].str.strip().str.replace(",", ""), errors="coerce"
    )
    df["high_date"] = df.get("52_Week_High_Date", pd.Series(dtype=str)).astype(str).str.strip()
    df["low_date"] = df.get("52_Week_Low_DT", pd.Series(dtype=str)).astype(str).str.strip()

    valid = df["symbol"].notna() & (df["symbol"] != "") & (df["official_high"] > 0)
    return df[valid][
        ["symbol", "series", "official_high", "official_low", "high_date", "low_date"]
    ].reset_index(drop=True)


def select_crosscheck_dates(
    all_trading_dates: Sequence[date],
    n_dates: int = 10,
    min_history_days: int | None = None,
    seed: int = 42,
) -> list[date]:
    """Select ~10 diverse trading dates that have at least 52 weeks of prior price history."""
    sorted_dates = sorted(set(all_trading_dates))
    if not sorted_dates:
        return []

    if min_history_days is None:
        min_history_days = settings.CROSSCHECK_WINDOW_DAYS
    min_date = sorted_dates[0] + timedelta(days=min_history_days)
    eligible = [d for d in sorted_dates if d >= min_date]
    if not eligible:
        return sorted_dates[-min(n_dates, len(sorted_dates)) :]

    if len(eligible) <= n_dates:
        return eligible

    rng = random.Random(seed)
    return sorted(rng.sample(eligible, n_dates))


def official_levels(check_date: date) -> pd.DataFrame:
    """NSE's official 52-week high and low for every stock on ``check_date``."""
    return parse_52_wk_csv(nse_files.fetch(nse_files.cm_52_wk_url(check_date)))


def our_levels(session: Session, check_date: date) -> dict[str, tuple[float, float]]:
    """Our 52-week (high, low) per stored symbol: the 365-calendar-day window, adjusted prices."""
    window_start = check_date - timedelta(days=settings.CROSSCHECK_WINDOW_DAYS)
    stmt = (
        select(
            DailyPrice.symbol,
            func.max(func.coalesce(DailyPrice.adj_high, DailyPrice.high)).label("max_high"),
            func.min(func.coalesce(DailyPrice.adj_low, DailyPrice.low)).label("min_low"),
        )
        .where(DailyPrice.date >= window_start, DailyPrice.date <= check_date)
        .group_by(DailyPrice.symbol)
    )
    return {
        row.symbol: (float(row.max_high), float(row.min_low))
        for row in session.execute(stmt).all()
        if row.max_high is not None and row.min_low is not None
    }


def comparison(check_date: date, symbol: str, metric: str, official: float, ours: float) -> dict:
    """One report row: the official number, ours and the difference in percent."""
    return {
        "date": check_date.isoformat(),
        "symbol": symbol,
        "metric": metric,
        "official": official,
        "ours": ours,
        "difference_pct": round(abs(ours - official) / official * 100, 2),
    }


def compare_levels(
    check_date: date,
    official: pd.DataFrame,
    ours: dict[str, tuple[float, float]],
    chains: RenameChains,
) -> list[dict[str, Any]]:
    """Compare official and our levels. A renamed stock uses the best value across its names."""
    records: list[dict[str, Any]] = []
    for row in official.itertuples():
        ours_for_stock = [ours[name] for name in family(row.symbol, chains) if name in ours]
        if not ours_for_stock:
            continue
        our_high = max(high for high, _ in ours_for_stock)
        our_low = min(low for _, low in ours_for_stock)
        official_high, official_low = float(row.official_high), float(row.official_low)

        records.append(comparison(check_date, row.symbol, "52W_HIGH", official_high, our_high))
        if official_low > 0:
            records.append(comparison(check_date, row.symbol, "52W_LOW", official_low, our_low))
    return records


def run_crosscheck_for_date(
    check_date: date,
    session: Session,
    chains: RenameChains | None = None,
) -> list[dict[str, Any]]:
    """Download and compare official 52-week High/Low against our prices for one date.

    Returns comparison records: date, symbol, metric, official, ours, difference_pct.
    Does not modify the database.
    """
    official = official_levels(check_date)
    if official.empty:
        return []
    chains = chains if chains is not None else load_chains_from_db(session)
    return compare_levels(check_date, official, our_levels(session, check_date), chains)


def print_summary(report: pd.DataFrame) -> bool:
    """Print the definition, the counts and the PASS/FAIL line. Returns True for PASS."""
    tolerance = settings.CROSSCHECK_TOLERANCE_PCT
    failing = report[report["difference_pct"] > tolerance] if not report.empty else report
    passed = not report.empty and failing.empty  # nothing compared is not a pass
    print("=" * 80)
    print("OFFICIAL 52-WEEK HIGH/LOW CROSS-CHECK")
    print(
        f"Definition: highest adj_high / lowest adj_low over {settings.CROSSCHECK_WINDOW_DAYS} "
        "calendar days up to the checked date (NSE's definition, not the 252-bar cache column)."
    )
    print("=" * 80)
    print(f"Dates: {report['date'].nunique() if not report.empty else 0}")
    print(f"Comparisons: {len(report)} | above {tolerance}%: {len(failing)}")
    if not failing.empty:
        print("\nWorst differences:")
        print(failing.head(10).to_string(index=False))
    print(f"Result: {'PASS' if passed else 'FAIL'}")
    return passed


def crosscheck_52w(
    dates: Sequence[date] | None = None,
    session: Session | None = None,
) -> pd.DataFrame:
    """Run the 52-week cross-check over the target dates, write the report, print PASS/FAIL.

    Differences are never auto-fixed. A date whose official file cannot be read is logged
    and skipped (the summary then counts fewer dates).
    """
    with use_session(session) as s:
        target_dates = (
            list(dates)
            if dates is not None
            else select_crosscheck_dates(get_trading_dates(s), n_dates=10)
        )
        chains = load_chains_from_db(s)
        records: list[dict[str, Any]] = []
        logger.info("Running 52-week cross-check for %d dates...", len(target_dates))
        for d in target_dates:
            try:
                found = run_crosscheck_for_date(d, s, chains=chains)
            except Exception as exc:  # one unreadable official file must not stop the run
                logger.warning("Cross-check for %s failed: %s", d, exc)
                continue
            logger.info("Cross-check %s: compared %d metrics", d, len(found))
            records.extend(found)

    report = pd.DataFrame(records, columns=CROSSCHECK_COLUMNS)
    report = report.sort_values(
        ["difference_pct", "date", "symbol"], ascending=[False, True, True]
    ).reset_index(drop=True)
    report_path = settings.REPORTS_DIR / "adj_crosscheck.csv"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report.to_csv(report_path, index=False)
    logger.info("Saved 52-week cross-check report to %s (%d comparisons)", report_path, len(report))
    print_summary(report)
    return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    crosscheck_52w()
