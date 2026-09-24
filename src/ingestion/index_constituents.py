"""
NSE Index Constituent Fetcher
=============================
Fetches the official constituent lists for NIFTY Large Cap, Mid Cap, and
Small Cap indices from the NSE archives, and merges them into a single
master symbol set.

This module is used by :class:`~src.etl.pipeline.BhavcopyETL` to restrict
the pipeline to stocks that belong to at least one of these three market-cap
tiers — automatically excluding micro-cap and penny stocks.

Data sources (CSV files hosted by NSE):

* ``ind_nifty100list.csv``          — NIFTY 100  (Large Cap proxy)
* ``ind_niftymidcap150list.csv``    — NIFTY Midcap 150
* ``ind_niftysmallcap250list.csv``  — NIFTY Smallcap 250

Together these cover the NIFTY 500 universe (100 + 150 + 250 = 500).
"""

from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
import requests

from config.settings import setup_logging

# ---------------------------------------------------------------------------
# Initialise centralized logging
# ---------------------------------------------------------------------------
setup_logging()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_BASE_URL = "https://nsearchives.nseindia.com/content/indices"

# (logical name, CSV filename on NSE, minimum expected count)
_INDEX_SPECS: list[tuple[str, str, int]] = [
    ("NIFTY 100 (Large Cap)",   "ind_nifty100list.csv",          90),
    ("NIFTY Midcap 150",        "ind_niftymidcap150list.csv",   140),
    ("NIFTY Smallcap 250",      "ind_niftysmallcap250list.csv", 230),
]

_NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
}

MAX_RETRIES = 3
RETRY_WAIT = 5        # seconds between retries
REQUEST_TIMEOUT = 20  # seconds per HTTP request


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------
@dataclass
class ConstituentResult:
    """Holds the output of a constituent-fetch operation."""

    symbols: set[str] = field(default_factory=set)
    """Master set of symbols across all successfully fetched indices."""

    per_index: dict[str, set[str]] = field(default_factory=dict)
    """Symbols broken out by index name, for diagnostics."""

    failed_indices: list[str] = field(default_factory=list)
    """Index names that could not be fetched after all retries."""


# ---------------------------------------------------------------------------
# Fetcher class
# ---------------------------------------------------------------------------
class NSEIndexConstituents:
    """Fetches and merges NSE index constituent lists.

    Usage::

        fetcher = NSEIndexConstituents()
        result  = fetcher.fetch_all()
        print(result.symbols)       # {'RELIANCE', 'TCS', …}
        print(len(result.symbols))  # ~500

    The class manages its own HTTP session (with NSE cookie warm-up) and
    is safe to instantiate independently of :class:`NSEBhavcopyDownloader`.
    """

    def __init__(
        self,
        max_retries: int = MAX_RETRIES,
        retry_wait: float = RETRY_WAIT,
        request_timeout: float = REQUEST_TIMEOUT,
    ) -> None:
        self.max_retries = max_retries
        self.retry_wait = retry_wait
        self.request_timeout = request_timeout
        self._session: Optional[requests.Session] = None

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------
    def _get_session(self) -> requests.Session:
        """Return a warm NSE session, creating one if needed."""
        if self._session is not None:
            return self._session

        session = requests.Session()
        session.headers.update(_NSE_HEADERS)
        try:
            logger.debug("Warming up NSE session for index constituent fetch …")
            session.get("https://www.nseindia.com", timeout=10)
            time.sleep(1)
        except requests.RequestException as exc:
            logger.warning("NSE homepage warm-up failed: %s (continuing)", exc)

        self._session = session
        return self._session

    def _refresh_session(self) -> requests.Session:
        """Force a fresh session (e.g. after 403)."""
        self._session = None
        return self._get_session()

    # ------------------------------------------------------------------
    # Single-index fetch
    # ------------------------------------------------------------------
    def _fetch_index(
        self,
        name: str,
        filename: str,
        min_expected: int,
    ) -> Optional[set[str]]:
        """Download one index CSV and extract the symbol column.

        Returns ``None`` if all retries are exhausted.
        """
        url = f"{_BASE_URL}/{filename}"
        session = self._get_session()

        for attempt in range(1, self.max_retries + 1):
            try:
                resp = session.get(url, timeout=self.request_timeout)

                if resp.status_code == 403:
                    logger.warning(
                        "  %s attempt %d → 403 — refreshing session",
                        name, attempt,
                    )
                    session = self._refresh_session()
                    time.sleep(self.retry_wait)
                    continue

                resp.raise_for_status()

                df = pd.read_csv(io.StringIO(resp.text))
                df.columns = df.columns.str.strip()

                # Find the symbol column — NSE sometimes uses varying casing
                sym_col = next(
                    (c for c in df.columns if c.strip().lower() == "symbol"),
                    None,
                )
                if sym_col is None:
                    logger.error(
                        "  %s: 'Symbol' column not found — columns are: %s",
                        name, df.columns.tolist(),
                    )
                    return None  # schema change — no amount of retrying helps

                symbols = set(df[sym_col].str.strip().dropna().tolist())

                if len(symbols) < min_expected:
                    logger.warning(
                        "  %s attempt %d: only %d symbols (expected ≥%d)",
                        name, attempt, len(symbols), min_expected,
                    )
                    if attempt < self.max_retries:
                        time.sleep(self.retry_wait)
                        session = self._refresh_session()
                        continue

                logger.info("  ✓ %-28s  %d symbols", name, len(symbols))
                return symbols

            except requests.exceptions.Timeout:
                logger.warning(
                    "  %s attempt %d timed out (timeout=%ds)",
                    name, attempt, self.request_timeout,
                )
                if attempt < self.max_retries:
                    time.sleep(self.retry_wait)

            except requests.RequestException as exc:
                logger.warning(
                    "  %s attempt %d network error: %s", name, attempt, exc
                )
                if attempt < self.max_retries:
                    time.sleep(self.retry_wait)
                    session = self._refresh_session()

            except Exception as exc:
                logger.error(
                    "  %s: unexpected error: %s", name, exc, exc_info=True,
                )
                return None

        logger.error("  ✗ %s: all %d attempts exhausted", name, self.max_retries)
        return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def fetch_all(self) -> ConstituentResult:
        """Fetch Large Cap, Mid Cap, and Small Cap lists and merge them.

        Returns a :class:`ConstituentResult` with the master symbol set.
        Raises :class:`RuntimeError` if *all* three indices fail (the
        pipeline should not proceed with zero symbols).
        """
        result = ConstituentResult()
        logger.info("Fetching NSE index constituent lists …")

        for name, filename, min_expected in _INDEX_SPECS:
            symbols = self._fetch_index(name, filename, min_expected)
            if symbols is not None:
                result.per_index[name] = symbols
                result.symbols |= symbols
            else:
                result.failed_indices.append(name)

        # Summary
        logger.info("-" * 50)
        logger.info(
            "  Master universe: %d unique symbols  (from %d/%d indices)",
            len(result.symbols),
            len(result.per_index),
            len(_INDEX_SPECS),
        )
        if result.failed_indices:
            logger.warning(
                "  Failed indices: %s", ", ".join(result.failed_indices)
            )
        logger.info("-" * 50)

        if not result.symbols:
            raise RuntimeError(
                "All index constituent fetches failed — cannot proceed. "
                "Check network connectivity and NSE availability."
            )

        return result
