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
from datetime import date
from pathlib import Path

import pandas as pd
import requests
from sqlalchemy.orm import Session

from src.db.models import BenchmarkPrice
from src.db.session import get_db
from src.db.upsert import upsert_rows

logger = logging.getLogger("src.ingestion.benchmark")

BENCHMARK_CACHE_DIR = Path("data/raw/benchmark_cache")
NON_TRADING_DAYS_FILE = BENCHMARK_CACHE_DIR / "non_trading_days.json"

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
    BENCHMARK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return BENCHMARK_CACHE_DIR


def load_non_trading_days() -> set[str]:
    """Load cached non-trading days from JSON file."""
    if NON_TRADING_DAYS_FILE.exists():
        try:
            with NON_TRADING_DAYS_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return set(data)
        except Exception as exc:
            logger.warning("Failed loading benchmark non-trading days: %s", exc)
    return set()


def record_non_trading_day(d: date) -> None:
    """Record a verified non-trading day (404/empty) to prevent repeated fetches."""
    _ensure_cache_dir()
    existing = load_non_trading_days()
    d_str = d.isoformat()
    if d_str not in existing:
        existing.add(d_str)
        try:
            with NON_TRADING_DAYS_FILE.open("w", encoding="utf-8") as f:
                json.dump(sorted(existing), f, indent=2)
        except Exception as exc:
            logger.warning("Failed saving benchmark non-trading days: %s", exc)


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

    def fetch_prices(self, d: date) -> dict[str, float] | None:
        # Check non-trading days cache
        if d.isoformat() in load_non_trading_days():
            return None

        cache_dir = _ensure_cache_dir()
        cache_file = cache_dir / f"{d.isoformat()}.parquet"
        if cache_file.exists():
            try:
                cdf = pd.read_parquet(cache_file)
                return dict(zip(cdf["index_code"], cdf["close"], strict=False))
            except Exception as e:
                logger.warning("Corrupt benchmark cache %s: %s", cache_file, e)

        self._warm_session()
        url = self._url(d)
        try:
            resp = self.session.get(url, timeout=10)
            if resp.status_code == 404:
                logger.debug("Benchmark data 404 for %s — marking non-trading day", d)
                record_non_trading_day(d)
                return None
            resp.raise_for_status()

            df = pd.read_csv(io.StringIO(resp.text))
            df.columns = df.columns.str.strip()

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
                cache_df = pd.DataFrame([{"index_code": k, "close": v} for k, v in results.items()])
                cache_df.to_parquet(cache_file, index=False)
                return results

            # File returned but no matching indices
            record_non_trading_day(d)
            return None
        except Exception as exc:
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
        prices = src.fetch_prices(d)
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
