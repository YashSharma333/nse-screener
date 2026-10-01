"""
NSE Bhavcopy Downloader
=======================
Downloads daily NSE Bhavcopy CSV files (one file per trading day containing
OHLCV for every listed stock) and saves the raw data to ``data/raw/``.

Two URL formats are attempted per date:
  • **NEW** (2023-present): ``sec_bhavdata_full_DDMMYYYY.csv``
  • **ZIP** (historical):   ``cm<DD><MON><YYYY>bhav.csv.zip``

Downloaded files are cached as Parquet under ``data/raw/bhavcopy_cache/`` so
re-runs for the same date are instant.
"""

from __future__ import annotations

import io
import logging
import time
import zipfile
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

from config.settings import setup_logging, PROJECT_ROOT

# ---------------------------------------------------------------------------
# Initialise centralized logging
# ---------------------------------------------------------------------------
setup_logging()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
RAW_DIR = PROJECT_ROOT / "data" / "raw"
CACHE_DIR = RAW_DIR / "bhavcopy_cache"

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
}

_MONTHS = [
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
]

# Retry / throttle defaults
MAX_RETRIES = 3
RETRY_WAIT = 6          # seconds between retries
REQUEST_TIMEOUT = 25     # seconds per HTTP request
REQUEST_DELAY = 0.8      # polite delay between daily downloads


class NSEBhavcopyDownloader:
    """Manages an NSE HTTP session and downloads daily Bhavcopy files.

    Usage::

        dl = NSEBhavcopyDownloader()
        df = dl.download_bhavcopy(date(2024, 6, 10))
    """

    # ------------------------------------------------------------------
    # Construction & session management
    # ------------------------------------------------------------------
    def __init__(
        self,
        max_retries: int = MAX_RETRIES,
        retry_wait: float = RETRY_WAIT,
        request_timeout: float = REQUEST_TIMEOUT,
        request_delay: float = REQUEST_DELAY,
    ) -> None:
        self.max_retries = max_retries
        self.retry_wait = retry_wait
        self.request_timeout = request_timeout
        self.request_delay = request_delay
        self._session: Optional[requests.Session] = None

        # Ensure output directories exist
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        logger.info("NSEBhavcopyDownloader initialised  (raw_dir=%s)", RAW_DIR)

    def get_session(self) -> requests.Session:
        """Return a warm NSE session (creates one on first call).

        NSE requires a prior visit to the homepage so that the session cookie
        is set before any API / archive requests succeed.
        """
        if self._session is not None:
            return self._session

        session = requests.Session()
        session.headers.update(NSE_HEADERS)
        try:
            logger.debug("Warming up NSE session (fetching homepage) …")
            session.get("https://www.nseindia.com", timeout=10)
            time.sleep(1)
        except requests.RequestException as exc:
            logger.warning("NSE homepage warm-up failed: %s (continuing anyway)", exc)

        self._session = session
        return self._session

    def refresh_session(self) -> requests.Session:
        """Force-create a fresh session (useful after repeated 403s)."""
        self._session = None
        return self.get_session()

    # ------------------------------------------------------------------
    # URL construction helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _url_new(d: date) -> str:
        """Build the *new-format* Bhavcopy URL (2023-present)."""
        return (
            "https://nsearchives.nseindia.com/products/content/"
            f"sec_bhavdata_full_{d.strftime('%d%m%Y')}.csv"
        )

    @staticmethod
    def _url_zip(d: date) -> str:
        """Build the *zip-format* Bhavcopy URL (historical archives)."""
        return (
            "https://nsearchives.nseindia.com/content/cm/"
            f"cm{d.strftime('%d')}{_MONTHS[d.month - 1]}{d.year}bhav.csv.zip"
        )

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------
    @staticmethod
    def _parse(content: bytes, fmt: str) -> Optional[pd.DataFrame]:
        """Parse raw Bhavcopy bytes into a normalised DataFrame.

        Parameters
        ----------
        content:
            Raw response body (CSV text or ZIP bytes).
        fmt:
            ``"new"`` for the plain-CSV format, ``"zip"`` for the zipped
            archive format.

        Returns
        -------
        DataFrame with columns ``[Symbol, Open, High, Low, Close, Volume]``
        filtered to Series == EQ, or ``None`` on parse failure.
        """
        try:
            if fmt == "zip":
                with zipfile.ZipFile(io.BytesIO(content)) as zf:
                    csv_name = next(
                        (n for n in zf.namelist() if n.endswith(".csv")),
                        None,
                    )
                    if csv_name is None:
                        logger.debug("ZIP archive contains no CSV file")
                        return None
                    df = pd.read_csv(io.BytesIO(zf.read(csv_name)))
            else:
                df = pd.read_csv(
                    io.StringIO(content.decode("utf-8", errors="replace"))
                )

            df.columns = df.columns.str.strip()

            # Column mapping differs between the two formats
            col_map = (
                {
                    "SYMBOL": "Symbol",
                    "SERIES": "Series",
                    "OPEN_PRICE": "Open",
                    "HIGH_PRICE": "High",
                    "LOW_PRICE": "Low",
                    "CLOSE_PRICE": "Close",
                    "TTL_TRD_QNTY": "Volume",
                }
                if fmt == "new"
                else {
                    "SYMBOL": "Symbol",
                    "SERIES": "Series",
                    "OPEN": "Open",
                    "HIGH": "High",
                    "LOW": "Low",
                    "CLOSE": "Close",
                    "TOTTRDQTY": "Volume",
                }
            )

            df = df.rename(
                columns={k: v for k, v in col_map.items() if k in df.columns}
            )

            if "Symbol" not in df.columns:
                logger.debug("Parsed CSV has no 'Symbol' column — skipping")
                return None

            # Keep only equity series
            if "Series" in df.columns:
                df = df[df["Series"].str.strip() == "EQ"]

            keep = [
                c
                for c in ["Symbol", "Open", "High", "Low", "Close", "Volume"]
                if c in df.columns
            ]
            return df[keep].reset_index(drop=True)

        except Exception as exc:
            logger.debug("Parse error (%s format): %s", fmt, exc)
            return None

    # ------------------------------------------------------------------
    # Public download entry-point
    # ------------------------------------------------------------------
    def download_bhavcopy(self, d: date) -> Optional[pd.DataFrame]:
        """Download and parse a single day's Bhavcopy.

        Returns ``None`` when the date is a weekend / holiday / unavailable.
        Parsed results are cached as Parquet under ``data/raw/bhavcopy_cache/``
        for instant re-runs.
        """
        cache_path = CACHE_DIR / f"{d.isoformat()}.parquet"

        # Fast path — already cached
        if cache_path.exists():
            logger.debug("Cache hit for %s", d)
            try:
                return pd.read_parquet(cache_path)
            except Exception as exc:
                logger.warning(
                    "Corrupt cache file %s (%s) — re-downloading", cache_path, exc
                )
                cache_path.unlink(missing_ok=True)

        session = self.get_session()

        for fmt, url in [("new", self._url_new(d)), ("zip", self._url_zip(d))]:
            for attempt in range(1, self.max_retries + 1):
                try:
                    resp = session.get(url, timeout=self.request_timeout)

                    if resp.status_code == 404:
                        logger.debug("%s %s → 404 (not available)", d, fmt)
                        break  # try next format

                    if resp.status_code == 403:
                        logger.warning(
                            "%s %s attempt %d → 403 — refreshing session",
                            d, fmt, attempt,
                        )
                        session = self.refresh_session()
                        time.sleep(self.retry_wait)
                        continue

                    if resp.status_code == 429:
                        # Respect Retry-After header; fall back to retry_wait
                        retry_after = float(
                            resp.headers.get("Retry-After", self.retry_wait)
                        )
                        logger.warning(
                            "%s %s attempt %d → 429 (rate-limited) — waiting %.0fs",
                            d, fmt, attempt, retry_after,
                        )
                        time.sleep(retry_after)
                        continue

                    resp.raise_for_status()

                    df = self._parse(resp.content, fmt)
                    if df is not None and not df.empty:
                        df.to_parquet(cache_path, index=False)
                        logger.debug(
                            "%s downloaded via %s format (%d rows)",
                            d, fmt, len(df),
                        )
                        return df

                    # Empty parse result — no point retrying the same format
                    break

                except requests.exceptions.Timeout:
                    logger.warning(
                        "%s %s attempt %d timed out", d, fmt, attempt
                    )
                    if attempt < self.max_retries:
                        time.sleep(self.retry_wait)

                except requests.RequestException as exc:
                    logger.warning(
                        "%s %s attempt %d failed: %s", d, fmt, attempt, exc
                    )
                    if attempt < self.max_retries:
                        time.sleep(self.retry_wait)

                except zipfile.BadZipFile as exc:
                    # Corrupt archive — log and skip this format; don't retry same URL
                    logger.warning(
                        "%s %s — corrupt ZIP archive: %s (skipping format)",
                        d, fmt, exc,
                    )
                    break

                except Exception as exc:
                    logger.error(
                        "Unexpected error downloading %s (%s): %s",
                        d, fmt, exc,
                        exc_info=True,
                    )
                    break

        # Neither format yielded data — likely a holiday
        logger.debug("%s — no Bhavcopy available (holiday / weekend)", d)
        return None
