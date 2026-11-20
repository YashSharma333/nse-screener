"""
Official 52-Week High/Low Cross-Check (Task D10)
===============================================
Downloads official adjusted 52-week High/Low files (CM_52_wk_High_low_DDMMYYYY.csv)
for ~10 dates, compares them against the database rolling 52-week max adj_high and
min adj_low across stock rename lineages, and writes reports/adj_crosscheck.csv.

Differences are LISTED, never auto-fixed.
"""

from __future__ import annotations

import io
import logging
import random
from collections.abc import Callable, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config.settings import PROJECT_ROOT, REGULAR_SERIES
from src.db.models import DailyPrice
from src.db.session import get_db
from src.ingestion.nse_files import cm_52_wk_url, fetch
from src.ingestion.renames import RenameChains, load_chains_from_db

logger = logging.getLogger(__name__)

DEFAULT_REPORT_PATH = PROJECT_ROOT / "reports" / "adj_crosscheck.csv"
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
    min_history_days: int = 365,
    seed: int = 42,
) -> list[date]:
    """Select ~10 diverse trading dates that have at least 52 weeks of prior price history."""
    sorted_dates = sorted(set(all_trading_dates))
    if not sorted_dates:
        return []

    min_date = sorted_dates[0] + timedelta(days=min_history_days)
    eligible = [d for d in sorted_dates if d >= min_date]
    if not eligible:
        return sorted_dates[-min(n_dates, len(sorted_dates)) :]

    if len(eligible) <= n_dates:
        return eligible

    rng = random.Random(seed)
    return sorted(rng.sample(eligible, n_dates))


def run_crosscheck_for_date(
    check_date: date,
    session: Session,
    fetch_fn: Callable[[str], bytes] = fetch,
    chains: RenameChains | None = None,
) -> list[dict[str, Any]]:
    """Download and compare official 52-week High/Low against DB for one date.

    Returns comparison records: date, symbol, metric, official, ours, difference_pct.
    Does not modify the database.
    """
    if chains is None:
        chains = load_chains_from_db(session)

    url = cm_52_wk_url(check_date)
    raw_bytes = fetch_fn(url)
    official_df = parse_52_wk_csv(raw_bytes)
    if official_df.empty:
        return []

    window_start = check_date - timedelta(days=365)

    # 1. Query DB prices over [check_date - 365d, check_date]
    # Aggregate max(adj_high or high) and min(adj_low or low) per symbol
    stmt = (
        select(
            DailyPrice.symbol,
            func.max(func.coalesce(DailyPrice.adj_high, DailyPrice.high)).label("max_high"),
            func.min(func.coalesce(DailyPrice.adj_low, DailyPrice.low)).label("min_low"),
        )
        .where(
            DailyPrice.date >= window_start,
            DailyPrice.date <= check_date,
        )
        .group_by(DailyPrice.symbol)
    )
    db_rows = session.execute(stmt).all()
    db_metrics: dict[str, tuple[float | None, float | None]] = {
        row.symbol: (
            float(row.max_high) if row.max_high is not None else None,
            float(row.min_low) if row.min_low is not None else None,
        )
        for row in db_rows
    }

    records: list[dict[str, Any]] = []

    for row in official_df.itertuples():
        sym = row.symbol
        # Incorporate former names if renamed
        curr = chains.get_current(sym)
        formers = chains.get_former(curr)
        family = {curr, sym, *formers}

        # Find best high and low across all family symbols in DB
        highs = [
            db_metrics[s][0] for s in family if s in db_metrics and db_metrics[s][0] is not None
        ]
        lows = [
            db_metrics[s][1] for s in family if s in db_metrics and db_metrics[s][1] is not None
        ]

        if not highs or not lows:
            continue

        ours_high = max(highs)
        ours_low = min(lows)

        off_high = float(row.official_high)
        off_low = float(row.official_low)

        diff_high_pct = round(abs(ours_high - off_high) / off_high * 100, 2)
        records.append(
            {
                "date": check_date.isoformat(),
                "symbol": sym,
                "metric": "52W_HIGH",
                "official": off_high,
                "ours": ours_high,
                "difference_pct": diff_high_pct,
            }
        )

        if off_low > 0:
            diff_low_pct = round(abs(ours_low - off_low) / off_low * 100, 2)
            records.append(
                {
                    "date": check_date.isoformat(),
                    "symbol": sym,
                    "metric": "52W_LOW",
                    "official": off_low,
                    "ours": ours_low,
                    "difference_pct": diff_low_pct,
                }
            )

    return records


def crosscheck_52w(
    dates: Sequence[date] | None = None,
    session: Session | None = None,
    fetch_fn: Callable[[str], bytes] = fetch,
    report_path: Path | None = None,
) -> pd.DataFrame:
    """Run 52-week cross-check over target dates and write report.

    Differences are never auto-fixed.
    """
    target_path = report_path or DEFAULT_REPORT_PATH
    target_path.parent.mkdir(parents=True, exist_ok=True)

    def _execute(s: Session) -> pd.DataFrame:
        nonlocal dates
        if dates is None:
            # Query trading dates from daily_prices
            stmt = select(DailyPrice.date).distinct().order_by(DailyPrice.date)
            all_dates = s.execute(stmt).scalars().all()
            dates = select_crosscheck_dates(all_dates, n_dates=10)

        chains = load_chains_from_db(s)
        all_records: list[dict[str, Any]] = []

        logger.info("Running 52-week cross-check for %d dates...", len(dates))
        for d in dates:
            try:
                recs = run_crosscheck_for_date(d, s, fetch_fn=fetch_fn, chains=chains)
                logger.info("Cross-check %s: compared %d metrics", d, len(recs))
                all_records.extend(recs)
            except Exception as exc:
                logger.warning("Cross-check for %s failed: %s", d, exc)

        res_df = pd.DataFrame(all_records, columns=CROSSCHECK_COLUMNS)
        if not res_df.empty:
            res_df = res_df.sort_values(
                ["difference_pct", "date", "symbol"], ascending=[False, True, True]
            ).reset_index(drop=True)

        res_df.to_csv(target_path, index=False)
        logger.info(
            "Saved 52-week cross-check report to %s (%d comparisons)",
            target_path,
            len(res_df),
        )
        return res_df

    if session is not None:
        return _execute(session)
    with get_db() as managed_session:
        return _execute(managed_session)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    df_result = crosscheck_52w()
    print(f"Cross-check finished. Total comparisons: {len(df_result)}")
    if not df_result.empty:
        print("\nTop 20 Differences:")
        print(df_result.head(20).to_string(index=False))
