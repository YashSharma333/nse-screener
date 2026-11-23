"""
Benchmark Index Ingestion Layer
===============================
Fetches official historical index closing prices for standard benchmark series:
  - NIFTY 50
  - NIFTY 100
  - NIFTY MIDCAP 150
  - NIFTY SMALLCAP 250
  - NIFTY 500

Provides an extensible BenchmarkSource interface with a primary NSE archive source.
"""

from __future__ import annotations

import io
import json
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from config import settings
from src.db.models import BenchmarkPrice, DailyPrice
from src.db.session import get_db
from src.db.upsert import upsert_rows
from src.ingestion.nse_files import BadFile
from src.ingestion.price_loader import is_recent_date

logger = logging.getLogger("src.ingestion.benchmark")


def parse_trade_date(val: object) -> date | None:
    """Parse a trade date string or object into a ``datetime.date`` (None if not a date)."""
    if val is None or pd.isna(val):
        return None
    if isinstance(val, pd.Timestamp):
        return val.date()
    if isinstance(val, date):
        return val
    val_str = str(val).strip()
    if not val_str:
        return None
    for fmt_str in ("%d-%b-%Y", "%d-%B-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(val_str, fmt_str).date()
        except ValueError:
            continue
    try:
        parsed = pd.to_datetime(val_str, dayfirst=True)
    except (ValueError, TypeError, OverflowError):
        return None
    return None if pd.isna(parsed) else parsed.date()


# The cache folder and the non-trading-days file are defined once, in config/settings.py.

# Canonical index names as stored in the database
NIFTY_50 = "NIFTY 50"
NIFTY_100 = "NIFTY 100"
NIFTY_MIDCAP_150 = "NIFTY MIDCAP 150"
NIFTY_SMALLCAP_250 = "NIFTY SMALLCAP 250"
NIFTY_500 = "NIFTY 500"

SUPPORTED_INDICES: list[str] = [
    NIFTY_50,
    NIFTY_100,
    NIFTY_MIDCAP_150,
    NIFTY_SMALLCAP_250,
    NIFTY_500,
]

INDEX_NAME_NORMALIZATION: dict[str, str] = {
    "nifty 50": NIFTY_50,
    "nifty 100": NIFTY_100,
    "nifty midcap 150": NIFTY_MIDCAP_150,
    "nifty smallcap 250": NIFTY_SMALLCAP_250,
    "nifty 500": NIFTY_500,
}


def canonical_index_code(code: str) -> str:
    """Normalize input index codes to the canonical database stored format.

    Examples:
        'NIFTY_500' -> 'NIFTY 500'
        'nifty 500' -> 'NIFTY 500'
        'NIFTY 500' -> 'NIFTY 500'
    """
    cleaned = code.strip().replace("_", " ")
    return INDEX_NAME_NORMALIZATION.get(cleaned.lower(), cleaned.upper())


def _ensure_cache_dir() -> Path:
    """Ensure benchmark cache directory exists on demand."""
    settings.BENCHMARK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return settings.BENCHMARK_CACHE_DIR


def load_non_trading_days() -> set[str]:
    """Load cached non-trading days from JSON file."""
    if settings.BENCHMARK_NON_TRADING_DAYS_FILE.exists():
        try:
            with settings.BENCHMARK_NON_TRADING_DAYS_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return set(data)
        except Exception as exc:
            logger.warning("Failed loading benchmark non-trading days: %s", exc)
    return set()


def record_non_trading_days(dates: Sequence[date]) -> None:
    """Record multiple verified non-trading days in one batch."""
    if not dates:
        return
    _ensure_cache_dir()
    existing = load_non_trading_days()
    updated = False
    for d in dates:
        d_str = d.isoformat()
        if d_str not in existing:
            existing.add(d_str)
            updated = True
    if updated:
        try:
            with settings.BENCHMARK_NON_TRADING_DAYS_FILE.open("w", encoding="utf-8") as f:
                json.dump(sorted(existing), f, indent=2)
        except Exception as exc:
            logger.warning("Failed saving benchmark non-trading days: %s", exc)


def record_non_trading_day(d: date) -> None:
    """Record a verified non-trading day (404/empty) to prevent repeated fetches."""
    record_non_trading_days([d])


def clear_non_trading_days(start: date, end: date) -> list[date]:
    """Remove cached non-trading days in [start, end] from benchmark cache.

    Returns the list of removed dates.
    """
    existing_str = load_non_trading_days()
    to_remove: list[date] = []
    remaining: set[str] = set()
    for s in existing_str:
        try:
            d = date.fromisoformat(s)
            if start <= d <= end:
                to_remove.append(d)
            else:
                remaining.add(s)
        except ValueError:
            remaining.add(s)

    if to_remove:
        try:
            _ensure_cache_dir()
            with settings.BENCHMARK_NON_TRADING_DAYS_FILE.open("w", encoding="utf-8") as f:
                json.dump(sorted(remaining), f, indent=2)
        except Exception as exc:
            logger.warning("Failed saving benchmark non-trading days: %s", exc)

    return sorted(to_remove)


class BenchmarkSource(ABC):
    """Abstract interface for downloading index benchmark prices."""

    @abstractmethod
    def fetch_prices(self, d: date) -> dict[str, float] | None:
        """Fetch index closes for a given date. Returns {index_code: close}."""
        pass


class NSEIndexSource(BenchmarkSource):
    """Primary source: official NSE daily index archive (ind_close_all_DDMMYYYY.csv)."""

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "*/*",
            }
        )
        self._warmed = False

    def _warm_session(self) -> None:
        """Warm session by visiting NSE homepage for session cookies."""
        if not self._warmed:
            try:
                self.session.get("https://www.nseindia.com", timeout=10)
                self._warmed = True
            except requests.RequestException as exc:
                logger.debug("NSE session warm-up failed: %s", exc)

    def _url(self, d: date) -> str:
        return f"https://nsearchives.nseindia.com/content/indices/ind_close_all_{d.strftime('%d%m%Y')}.csv"

    def _remember_no_file(self, d: date, reason: str) -> None:
        """Record d as non-trading, unless it is recent (NSE may not have published yet)."""
        if is_recent_date(d):
            logger.info("Benchmark %s for recent date %s: not cached, will retry", reason, d)
            return
        logger.debug("Benchmark %s for %s: marking non-trading day", reason, d)
        record_non_trading_day(d)

    def fetch_prices(self, d: date) -> dict[str, float] | None:
        # Check non-trading days cache
        if d.isoformat() in load_non_trading_days():
            return None

        cache_dir = _ensure_cache_dir()
        cache_file = cache_dir / f"{d.isoformat()}.parquet"
        if cache_file.exists():
            try:
                cdf = pd.read_parquet(cache_file)
                if "date" in cdf.columns:
                    cached_dates = cdf["date"].dropna()
                    if not cached_dates.empty:
                        cached_d = parse_trade_date(cached_dates.iloc[0])
                        if cached_d is not None and cached_d != d:
                            logger.warning(
                                "Cached benchmark parquet for %s has mismatched date %s "
                                "— unlinking",
                                d,
                                cached_d,
                            )
                            cache_file.unlink(missing_ok=True)
                        else:
                            return dict(zip(cdf["index_code"], cdf["close"], strict=False))
                    else:
                        return dict(zip(cdf["index_code"], cdf["close"], strict=False))
                else:
                    return dict(zip(cdf["index_code"], cdf["close"], strict=False))
            except Exception as e:
                logger.warning("Corrupt benchmark cache %s: %s", cache_file, e)

        self._warm_session()
        url = self._url(d)
        try:
            resp = self.session.get(url, timeout=10)
            if resp.status_code == 404:
                self._remember_no_file(d, "404")
                return None
            resp.raise_for_status()

            try:
                df = pd.read_csv(io.StringIO(resp.text))
            except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
                # A broken file is an error to report, not proof of a holiday.
                raise BadFile(f"Benchmark file for {d} cannot be parsed: {exc}") from exc
            df.columns = df.columns.str.strip()

            # Date validation: compare file's own trade date with requested date d
            date_col = next(
                (c for c in df.columns if c.lower() in ("index date", "index_date", "date")),
                None,
            )
            file_date: date | None = None
            if date_col is not None:
                non_null_dates = df[date_col].dropna()
                if not non_null_dates.empty:
                    file_date = parse_trade_date(non_null_dates.iloc[0])

            if date_col is None or file_date is None or file_date != d:
                raw_val = df[date_col].iloc[0] if (date_col and not df[date_col].empty) else None
                logger.warning(
                    "Benchmark trade date mismatch for %s: file reports %r (parsed: %s)",
                    d,
                    raw_val,
                    file_date,
                )
                self._remember_no_file(d, "date mismatch")
                return None

            results: dict[str, float] = {}
            for row in df.to_dict("records"):
                idx_name = str(row.get("Index Name", "")).strip().lower()
                matched_code = INDEX_NAME_NORMALIZATION.get(idx_name)
                if matched_code:
                    try:
                        val = float(
                            str(row.get("Closing Index Value", "")).replace(",", "").strip()
                        )
                        results[matched_code] = val
                    except (ValueError, TypeError):
                        pass

            if results:
                cache_df = pd.DataFrame(
                    [{"index_code": k, "close": v, "date": d} for k, v in results.items()]
                )
                cache_df.to_parquet(cache_file, index=False)
                return results

            # File returned but no matching indices
            self._remember_no_file(d, "no matching indices")
            return None
        except requests.RequestException as exc:
            # Network problem: not a holiday. Nothing is recorded, so the next run retries.
            logger.warning("NSE index source fetch failed for %s: %s", d, exc)
            return None


def load_benchmarks(
    dates: Sequence[date],
    source: BenchmarkSource | None = None,
    delay: float = 0.5,
    session: Session | None = None,
) -> int:
    """Download and store benchmark index prices for the given dates."""
    src = source or NSEIndexSource()
    total_saved = 0

    for d in dates:
        try:
            prices = src.fetch_prices(d)
        except (requests.RequestException, BadFile) as exc:
            # One bad day must not stop the others; it stays missing and is retried next run.
            logger.warning("Benchmark fetch failed for %s: %s", d, exc)
            continue
        if not prices:
            continue

        records = [
            {
                "index_code": idx_code,
                "date": d,
                "close": close_val,
                "source": "NSE",
            }
            for idx_code, close_val in prices.items()
        ]

        if not records:
            continue

        if session is not None:
            upsert_rows(
                session=session,
                model=BenchmarkPrice,
                rows=records,
                update_columns=["close", "source"],
                index_elements=["index_code", "date"],
            )
        else:
            with get_db() as s:
                upsert_rows(
                    session=s,
                    model=BenchmarkPrice,
                    rows=records,
                    update_columns=["close", "source"],
                    index_elements=["index_code", "date"],
                )

        total_saved += len(records)
        if delay > 0:
            time.sleep(delay)

    return total_saved


def missing_benchmark_dates(session: Session, index_code: str = NIFTY_500) -> list[date]:
    """Price dates that have no benchmark row yet, minus dates known to be non-trading.

    Why: asking for "dates after the last stored one" never retries a day that was
    missed in the middle. Asking for every gap heals them.
    """
    price_dates = set(session.scalars(select(DailyPrice.date).distinct()).all())
    have = set(
        session.scalars(
            select(BenchmarkPrice.date).where(BenchmarkPrice.index_code == index_code)
        ).all()
    )
    known_non_trading = load_non_trading_days()
    return sorted(d for d in price_dates - have if d.isoformat() not in known_non_trading)


def load_benchmark_range(
    start: date,
    end: date,
    session: Session | None = None,
) -> int:
    """Download benchmark closes for every calendar day in [start, end].

    Every day is probed, not just weekdays, because NSE holds weekend and holiday special
    sessions (Diwali Muhurat, Budget Saturdays). Days NSE has no index file for are
    remembered in the non-trading-days cache and skipped next time.
    """
    all_days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    known_non_trading = load_non_trading_days()
    dates = [d for d in all_days if d.isoformat() not in known_non_trading]
    logger.info("Loading benchmark history for %d dates (%s -> %s)", len(dates), start, end)
    return load_benchmarks(dates, session=session)
