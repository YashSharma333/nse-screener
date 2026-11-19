"""
NSE official file download helper (task D2)
===========================================
The single, authoritative module for downloading official data files from NSE India:
• Bhavcopy files (UDiFF from 2024-01-01, old format before 2024-01-01)
• PR market cap bundles (daily from 2024-02-01)
• Equity listing master (EQUITY_L.csv)
• Symbol change history (symbolchange.csv)

Nothing else in the project may call NSE directly after the later tasks.
"""

from __future__ import annotations

import concurrent.futures
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlparse

import requests

from config.settings import (
    NSE_ARCHIVES_BASE,
    NSE_ARCHIVES_HOST,
    NSE_UDIFF_START,
    NSE_WWW_HOST,
)

logger = logging.getLogger(__name__)

# Standard English month abbreviations used in historical NSE file paths.
# We avoid strftime("%b") because system locale may produce non-English names.
_MONTHS: list[str] = [
    "JAN",
    "FEB",
    "MAR",
    "APR",
    "MAY",
    "JUN",
    "JUL",
    "AUG",
    "SEP",
    "OCT",
    "NOV",
    "DEC",
]

# Browser headers: NSE blocks or drops automated requests without browser-like headers.
HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class NoFile(Exception):  # noqa: N818
    """HTTP 404: NSE has no file for this date (exchange holiday, weekend, etc.)."""


class BadFile(Exception):  # noqa: N818
    """HTTP 200 but content is unusable: HTML block page, bad zip, wrong date."""


class FetchFailed(Exception):  # noqa: N818
    """Download failed after exhausting all retry attempts."""


# ---------------------------------------------------------------------------
# Host extraction and validation checks
# ---------------------------------------------------------------------------


def host(url: str) -> str:
    """Return lowercase hostname from a URL (e.g. 'www.nseindia.com')."""
    parsed = urlparse(url)
    return (parsed.hostname or "").lower()


def check_zip(data: bytes) -> None:
    """Validate that data starts with the standard ZIP magic header (b'PK').

    NSE sometimes returns an HTML block page with HTTP 200 instead of the zip file.
    """
    if not isinstance(data, (bytes, bytearray)) or not data.startswith(b"PK"):
        raise BadFile("File content is not a valid zip archive (missing PK signature)")


def check_not_html(data: bytes | str) -> None:
    """Validate that content does not start with '<' after stripping whitespace.

    NSE sometimes serves HTML error or access-denied pages with HTTP 200 status codes.
    """
    stripped: bytes | str = data.strip() if hasattr(data, "strip") else data
    if isinstance(stripped, bytes) and stripped.startswith(b"<"):
        raise BadFile("Content appears to be HTML error/block page instead of data")
    if isinstance(stripped, str) and stripped.startswith("<"):
        raise BadFile("Content appears to be HTML error/block page instead of data")


def check_date(found: str | date, expected: str | date) -> None:
    """Validate that the date found inside the file matches the requested date.

    NSE is known to return another trading day's file on non-trading days or holidays.
    """
    # Normalize date objects to ISO string format (YYYY-MM-DD)
    found_str = found.isoformat() if isinstance(found, date) else str(found)
    expected_str = expected.isoformat() if isinstance(expected, date) else str(expected)
    if found_str != expected_str:
        raise BadFile(
            f"Date inside file ({found_str}) does not match expected date ({expected_str})"
        )


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------


def fetch(
    url: str,
    *,
    session: requests.Session | None = None,
    timeout: int | float = 30,
    retries: int = 3,
    sleep: Callable[[float], Any] = time.sleep,
) -> bytes:
    """Download raw bytes from URL with retry, exponential backoff, and NSE warm-up.

    Parameters
    ----------
    url : str
        The URL to fetch.
    session : requests.Session, optional
        Requests session; if None, a new session is created.
    timeout : int or float, default 30
        Request timeout in seconds.
    retries : int, default 3
        Number of fetch attempts before raising FetchFailed.
    sleep : callable, default time.sleep
        Sleep function for backoff (can be replaced in unit tests).
    """
    if session is None:
        session = requests.Session()
    session.headers.update(HEADERS)

    # Warm up session with homepage if hitting www.nseindia.com.
    # NSE requires cookies set on the homepage before answering www routes;
    # the archives host (nsearchives.nseindia.com) does not require warm-up.
    if host(url) == NSE_WWW_HOST and not getattr(session, "_nse_warmed", False):
        try:
            session.get("https://www.nseindia.com", timeout=timeout)
        except requests.RequestException as exc:
            # If warmup times out or fails, log debug info and still attempt the main URL
            logger.debug("Homepage warm-up request failed: %s", exc)
        session._nse_warmed = True

    for attempt in range(retries):
        try:
            r = session.get(url, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout):
            if attempt < retries - 1:
                sleep(2**attempt)
                continue
            break

        # HTTP 404 indicates no file exists for this date (holiday, weekend).
        # We do not retry 404s.
        if r.status_code == 404:
            raise NoFile(f"NSE has no file at {url} (HTTP 404)")

        # Temporary rate limiting (429), access block (403), or server errors (5xx)
        if r.status_code in (403, 429) or r.status_code >= 500:
            if attempt < retries - 1:
                sleep(2**attempt)
                continue
            break

        if r.status_code == 200:
            # Reject 200 OK responses that are HTML block pages
            check_not_html(r.content)
            return r.content

        # Any other unexpected HTTP status code
        if attempt < retries - 1:
            sleep(2**attempt)
            continue
        break

    raise FetchFailed(f"Failed to fetch {url} after {retries} attempts")


def fetch_many(
    urls: Sequence[str],
    *,
    workers: int = 4,
    pause: float = 0.3,
    timeout: int | float = 30,
    retries: int = 3,
    session: requests.Session | None = None,
) -> dict[str, bytes]:
    """Fetch multiple URLs concurrently with ThreadPoolExecutor and polite pause.

    Returns a dict mapping url -> content bytes.
    """
    results: dict[str, bytes] = {}
    if not urls:
        return results

    s = session or requests.Session()
    s.headers.update(HEADERS)

    def _worker(target_url: str) -> tuple[str, bytes]:
        if pause > 0:
            time.sleep(pause)
        data = fetch(target_url, session=s, timeout=timeout, retries=retries)
        return target_url, data

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_url = {executor.submit(_worker, u): u for u in urls}
        for future in concurrent.futures.as_completed(future_to_url):
            u, data = future.result()
            results[u] = data

    return results


# ---------------------------------------------------------------------------
# URL builders (pure functions of a date)
# ---------------------------------------------------------------------------


def bhavcopy_udiff_url(d: date) -> str:
    """Return the UDiFF Bhavcopy zip URL for date d (valid from 2024-01-01)."""
    yyyymmdd = f"{d.year:04d}{d.month:02d}{d.day:02d}"
    return f"{NSE_ARCHIVES_BASE}/content/cm/BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip"


def bhavcopy_old_url(d: date) -> str:
    """Return the historical Bhavcopy zip URL for date d (used up to 2024-07-05)."""
    yyyy = f"{d.year:04d}"
    mon = _MONTHS[d.month - 1]
    dd = f"{d.day:02d}"
    return (
        f"{NSE_ARCHIVES_BASE}/content/historical/EQUITIES/{yyyy}/{mon}/"
        f"cm{dd}{mon}{yyyy}bhav.csv.zip"
    )


def pr_bundle_url(d: date) -> str:
    """Return the PR bundle zip URL for date d (contains market cap from 2024-02-01)."""
    ddmmyy = f"{d.day:02d}{d.month:02d}{str(d.year)[-2:]}"
    return f"{NSE_ARCHIVES_BASE}/archives/equities/bhavcopy/pr/PR{ddmmyy}.zip"


EQUITY_L_URL: str = f"{NSE_ARCHIVES_BASE}/content/equities/EQUITY_L.csv"
SYMBOLCHANGE_URL: str = f"{NSE_ARCHIVES_BASE}/content/equities/symbolchange.csv"


def equity_l_url(_d: date | None = None) -> str:
    """Return the official listing master URL (EQUITY_L.csv)."""
    return EQUITY_L_URL


def symbolchange_url(_d: date | None = None) -> str:
    """Return the official symbol change history URL (symbolchange.csv)."""
    return SYMBOLCHANGE_URL


def bhavcopy_url(d: date) -> str:
    """Return official Bhavcopy URL: UDiFF format if d >= NSE_UDIFF_START else old format."""
    if d >= NSE_UDIFF_START:
        return bhavcopy_udiff_url(d)
    return bhavcopy_old_url(d)


# ---------------------------------------------------------------------------
# Registry of official NSE data sources
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileSource:
    """Metadata describing an official NSE file source."""

    name: str
    url_builder: Callable[..., str]
    host: str
    frequency_note: str

    @property
    def frequency(self) -> str:
        return self.frequency_note


FILE_REGISTRY: dict[str, FileSource] = {
    "bhavcopy": FileSource(
        name="bhavcopy",
        url_builder=bhavcopy_url,
        host=NSE_ARCHIVES_HOST,
        frequency_note="daily (trading days; UDiFF from 2024-01-01, old format before)",
    ),
    "bhavcopy_udiff": FileSource(
        name="bhavcopy_udiff",
        url_builder=bhavcopy_udiff_url,
        host=NSE_ARCHIVES_HOST,
        frequency_note="daily from 2024-01-01",
    ),
    "bhavcopy_old": FileSource(
        name="bhavcopy_old",
        url_builder=bhavcopy_old_url,
        host=NSE_ARCHIVES_HOST,
        frequency_note="daily historical up to 2024-07-05",
    ),
    "pr_bundle": FileSource(
        name="pr_bundle",
        url_builder=pr_bundle_url,
        host=NSE_ARCHIVES_HOST,
        frequency_note="daily (contains mcap<date>.csv from 2024-02-01)",
    ),
    "equity_l": FileSource(
        name="equity_l",
        url_builder=equity_l_url,
        host=NSE_ARCHIVES_HOST,
        frequency_note="listing master (updated when new securities list)",
    ),
    "symbolchange": FileSource(
        name="symbolchange",
        url_builder=symbolchange_url,
        host=NSE_ARCHIVES_HOST,
        frequency_note="symbol change history (cumulative file)",
    ),
}

REGISTRY = FILE_REGISTRY
