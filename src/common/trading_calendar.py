"""The one place that knows which days NSE did not trade (CL-21).

Two things live here, because they answer the same question ("which days count?"):

* **Non-trading-days cache.** A small JSON list of ISO dates per *source* (``"prices"`` for the
  Bhavcopy loader, ``"benchmark"`` for the index loader). A day is recorded when NSE has no file
  for it (404, wrong date inside the file). Each source keeps its own list because a day can
  have prices but no index file. Writes are atomic (temp file, then replace).
* **Date queries.** ``get_trading_dates`` and ``latest_date`` replace the ``SELECT DISTINCT
  date`` / ``MAX(date)`` queries that used to be written out in about ten places.

The file paths are defined once in ``config/settings.py`` and read when a function runs, so the
tests can redirect them.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config import settings
from src.db.models import DailyPrice

logger = logging.getLogger(__name__)

PRICES = "prices"
BENCHMARK = "benchmark"

# A 404 within this many days may only mean NSE has not published yet, so it is never cached.
RECENT_NON_TRADING_BUFFER_DAYS: int = 3


def non_trading_days_path(source: str) -> Path:
    """Return the JSON file that holds the non-trading days of ``source``."""
    if source == PRICES:
        return settings.PRICE_NON_TRADING_DAYS_FILE
    if source == BENCHMARK:
        return settings.BENCHMARK_NON_TRADING_DAYS_FILE
    raise ValueError(f"Unknown non-trading-days source {source!r}; use PRICES or BENCHMARK")


def load_non_trading_days(source: str, path: Path | None = None) -> set[date]:
    """Read the confirmed non-trading dates of ``source`` (empty when the file is missing).

    Why: avoids thousands of requests to NSE for days it is known to have no file.
    ``path`` overrides the settings file (used by tests and one-off tools).
    """
    target = path or non_trading_days_path(source)
    if not target.exists():
        return set()
    try:
        raw: Any = json.loads(target.read_text(encoding="utf-8"))
        return {date.fromisoformat(item) for item in raw}
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("Could not read non-trading days from %s: %s", target, exc)
        return set()


def record_non_trading_day(source: str, day: date, path: Path | None = None) -> None:
    """Add ``day`` to the cache of ``source``, atomically (never a half-written JSON file)."""
    target = path or non_trading_days_path(source)
    existing = load_non_trading_days(source, target)
    if day in existing:
        return
    existing.add(day)
    tmp_path = target.with_suffix(".json.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(sorted(d.isoformat() for d in existing), indent=2)
        tmp_path.write_text(text, encoding="utf-8")
        tmp_path.replace(target)
    except OSError as exc:
        logger.warning("Could not write non-trading day %s to %s: %s", day, target, exc)
        tmp_path.unlink(missing_ok=True)


def is_recent_date(day: date, ref_date: date | None = None) -> bool:
    """True when ``day`` is within the last 3 calendar days.

    Why: NSE often uploads files in the late evening or the next day. A 404 that recent could
    be a publishing delay, so it must NOT be cached as a holiday.
    """
    today = ref_date or date.today()
    return day >= today - timedelta(days=RECENT_NON_TRADING_BUFFER_DAYS)


def record_missing_file(
    source: str, day: date, ref_date: date | None = None, path: Path | None = None
) -> bool:
    """Record ``day`` as non-trading unless it is recent. Returns True when it was recorded."""
    if is_recent_date(day, ref_date):
        return False
    record_non_trading_day(source, day, path)
    return True


def get_trading_dates(
    session: Session, start: date | None = None, end: date | None = None
) -> list[date]:
    """Every date that has prices in ``daily_prices``, oldest first (``start``/``end`` inclusive).

    Why: "which days did the market trade" is answered by the days that have stored prices.
    """
    stmt = select(DailyPrice.date).distinct().order_by(DailyPrice.date)
    if start is not None:
        stmt = stmt.where(DailyPrice.date >= start)
    if end is not None:
        stmt = stmt.where(DailyPrice.date <= end)
    return list(session.scalars(stmt).all())


def latest_date(session: Session, model: Any = DailyPrice) -> date | None:
    """The newest ``date`` stored in ``model``'s table (``DailyPrice`` by default), or None."""
    return session.scalar(select(func.max(model.date)))
