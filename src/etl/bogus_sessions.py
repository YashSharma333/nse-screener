"""Detect bogus duplicate sessions in ``daily_prices`` (read-only).

NSE sometimes serves the previous day's file for a weekend or holiday. The loaders reject
such files, but this check is a second safety net: it only REPORTS suspicious dates and
never changes or deletes a row (price history is never deleted in this project).
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from config import settings
from src.common.dates import parse_nse_date
from src.common.db import use_session
from src.common.trading_calendar import get_trading_dates
from src.db.models import DailyPrice

logger = logging.getLogger(__name__)

# A date is suspicious when this share of the symbols it has in common with the previous
# stored date have identical close AND identical volume.
IDENTICAL_PCT_THRESHOLD = 90.0
# Below this many common symbols the comparison is too small to mean anything.
MIN_COMMON_SYMBOLS = 100
# Dates are read from MySQL in chunks so we never pull ten years at once.
DATE_CHUNK_SIZE = 50


# What a database may hand back for a date: a date, a Timestamp or ISO text.
_DB_DATE_FORMATS = ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S")


def _to_day(value: object) -> date:
    """Turn a value read from the database into a plain ``date``."""
    return parse_nse_date(value, _DB_DATE_FORMATS, strict=True)


def _load_close_volume(
    session: Session, dates: list[date]
) -> dict[date, dict[str, tuple[float | None, int | None]]]:
    """Read (close, volume) per symbol for the given dates, chunk by chunk."""
    day_data: dict[date, dict[str, tuple[float | None, int | None]]] = {d: {} for d in dates}
    for i in range(0, len(dates), DATE_CHUNK_SIZE):
        chunk = dates[i : i + DATE_CHUNK_SIZE]
        query = select(
            DailyPrice.symbol, DailyPrice.date, DailyPrice.close, DailyPrice.volume
        ).where(DailyPrice.date.in_(chunk))
        for sym, dt, close, volume in session.execute(query).all():
            day = _to_day(dt)
            if day in day_data:
                day_data[day][str(sym)] = (
                    float(close) if close is not None else None,
                    int(volume) if volume is not None else None,
                )
    return day_data


def _identical_count(
    curr: dict[str, tuple[float | None, int | None]],
    prev: dict[str, tuple[float | None, int | None]],
    common: set[str],
) -> int:
    """Count common symbols whose close and volume are the same on both days."""
    count = 0
    for sym in common:
        c1, v1 = curr[sym]
        c2, v2 = prev[sym]
        if None in (c1, c2, v1, v2):
            continue
        if abs(c1 - c2) < 1e-4 and v1 == v2:
            count += 1
    return count


def _find(
    session: Session,
    start: date | None,
    end: date | None,
    only_dates: list[date] | None = None,
) -> list[dict]:
    stored = sorted({_to_day(d) for d in get_trading_dates(session)})
    if len(stored) < 2:
        return []

    # Pair every stored date inside the range with the stored date just before it.
    pairs = [
        (d, stored[idx - 1])
        for idx, d in enumerate(stored)
        if idx > 0
        and not (start and d < start)
        and not (end and d > end)
        and (only_dates is None or d in only_dates)
    ]
    if not pairs:
        return []

    day_data = _load_close_volume(session, sorted({d for pair in pairs for d in pair}))
    bogus: list[dict] = []
    for d, prev_d in pairs:
        curr, prev = day_data.get(d, {}), day_data.get(prev_d, {})
        common = set(curr) & set(prev)
        if len(common) < MIN_COMMON_SYMBOLS:
            continue
        identical = _identical_count(curr, prev, common)
        pct = identical / len(common) * 100.0
        if pct >= IDENTICAL_PCT_THRESHOLD:
            bogus.append(
                {
                    "date": d,
                    "weekday": d.strftime("%A"),
                    "pct_identical": pct,
                    "common_count": len(common),
                    "identical_count": identical,
                    "prev_date": prev_d,
                }
            )
    return bogus


def find_bogus_sessions(
    start: date | None = None,
    end: date | None = None,
    session: Session | None = None,
    only_dates: list[date] | None = None,
) -> list[dict]:
    """Return stored dates that look like copies of the previous session (read-only).

    Genuine special sessions (Diwali Muhurat, Budget Saturdays) have different prices and
    volumes, so they are not flagged. ``only_dates`` limits the check to those dates (each
    is still compared with the stored date before it).
    """
    with use_session(session) as s:
        return _find(s, start, end, only_dates)


def report_bogus_sessions(
    start: date | None = None,
    end: date | None = None,
    reports_dir: Path | None = None,
    session: Session | None = None,
    only_dates: list[date] | None = None,
) -> dict:
    """Write ``reports/bogus_sessions.csv`` and return the flagged dates. Never deletes.

    Without ``only_dates`` the whole range is checked and the file is rewritten. With
    ``only_dates`` (the daily update passes the dates it just loaded) only those dates are
    checked, new findings are APPENDED so earlier findings stay, and the file is left
    alone when nothing is flagged.
    """
    out_dir = reports_dir or settings.REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    report_file = out_dir / "bogus_sessions.csv"

    flagged = find_bogus_sessions(start=start, end=end, session=session, only_dates=only_dates)
    rows = pd.DataFrame(
        [
            {
                "date": b["date"].isoformat(),
                "weekday": b["weekday"],
                "%identical": f"{b['pct_identical']:.2f}%",
            }
            for b in flagged
        ],
        columns=["date", "weekday", "%identical"],
    )
    if only_dates is None:
        rows.to_csv(report_file, index=False)
    elif flagged:
        rows.to_csv(report_file, mode="a", index=False, header=not report_file.exists())
    logger.info(
        "Found %d flagged sessions (report file %s, nothing deleted)", len(flagged), report_file
    )

    return {
        "bogus_count": len(flagged),
        "flagged_dates": [b["date"] for b in flagged],
        "report_file": str(report_file),
    }
