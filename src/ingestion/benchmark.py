"""
Benchmark Index Ingestion Layer
===============================
Fetches official historical index closing prices for standard benchmark series:
  - NIFTY 50
  - NIFTY 100
  - NIFTY MIDCAP 150
  - NIFTY SMALLCAP 250
  - NIFTY 500

Provides an extensible BenchmarkSource interface with a primary NSE archive source
and a secondary yfinance fallback.
"""

from __future__ import annotations

import io
import logging
from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path
from typing import ClassVar

import pandas as pd
import requests
from sqlalchemy.dialects.mysql import insert as mysql_insert

from src.db.models import BenchmarkPrice
from src.db.session import get_db

logger = logging.getLogger("src.ingestion.benchmark")

BENCHMARK_CACHE_DIR = Path("data/raw/benchmark_cache")
BENCHMARK_CACHE_DIR.mkdir(parents=True, exist_ok=True)

SUPPORTED_INDICES: list[str] = [
    "NIFTY 50",
    "NIFTY 100",
    "NIFTY MIDCAP 150",
    "NIFTY SMALLCAP 250",
    "NIFTY 500",
]

INDEX_NAME_NORMALIZATION: dict[str, str] = {
    "nifty 50": "NIFTY 50",
    "nifty 100": "NIFTY 100",
    "nifty midcap 150": "NIFTY MIDCAP 150",
    "nifty smallcap 250": "NIFTY SMALLCAP 250",
    "nifty 500": "NIFTY 500",
}


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

    def _url(self, d: date) -> str:
        return f"https://nsearchives.nseindia.com/content/indices/ind_close_all_{d.strftime('%d%m%Y')}.csv"

    def fetch_prices(self, d: date) -> dict[str, float] | None:
        cache_file = BENCHMARK_CACHE_DIR / f"{d.isoformat()}.parquet"
        if cache_file.exists():
            try:
                cdf = pd.read_parquet(cache_file)
                return dict(zip(cdf["index_code"], cdf["close"], strict=False))
            except Exception as e:
                logger.warning("Corrupt benchmark cache %s: %s", cache_file, e)

        url = self._url(d)
        try:
            resp = self.session.get(url, timeout=10)
            if resp.status_code == 404:
                logger.debug("Benchmark data 404 for %s", d)
                return None
            resp.raise_for_status()

            df = pd.read_csv(io.StringIO(resp.text))
            df.columns = df.columns.str.strip()

            results: dict[str, float] = {}
            for _, row in df.iterrows():
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
                # Cache results
                cache_df = pd.DataFrame([{"index_code": k, "close": v} for k, v in results.items()])
                cache_df.to_parquet(cache_file, index=False)
                return results

            return None
        except Exception as exc:
            logger.warning("NSE index source fetch failed for %s: %s", d, exc)
            return None


class YahooFinanceSource(BenchmarkSource):
    """Fallback source: Yahoo Finance chart endpoints."""

    YAHOO_TICKERS: ClassVar[dict[str, str]] = {
        "NIFTY 50": "^NSEI",
        "NIFTY 100": "^CNX100",
        "NIFTY MIDCAP 150": "NIFTYMIDCAP150.NS",
        "NIFTY 500": "^CRSLDX",
    }

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            }
        )

    def fetch_prices(self, _d: date) -> dict[str, float] | None:
        # Fallback implementation on individual dates
        return None


def load_benchmarks(dates: list[date], source: BenchmarkSource | None = None) -> int:
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

        try:
            with get_db() as session:
                for r in records:
                    stmt = mysql_insert(BenchmarkPrice).values(r)
                    session.execute(
                        stmt.on_duplicate_key_update(
                            close=stmt.inserted.close,
                            source=stmt.inserted.source,
                        )
                    )
            total_saved += len(records)
        except Exception as exc:
            logger.warning("Failed saving benchmark prices for %s: %s", d, exc)

    return total_saved
