"""
NSE official file download helper (task D2)
===========================================
The single, authoritative module for downloading official data files from NSE India:
• Bhavcopy files (UDiFF from 2024-01-01, old format before 2024-01-01)
• PR market cap bundles (daily from 2024-02-01)
• Equity listing master (EQUITY_L.csv)
• Symbol change history (symbolchange.csv)

This is the ONLY module that talks HTTP to NSE (a test enforces it): one header set, one
shared session with its cookies, one homepage warm-up, one set of retry rules.
"""

from __future__ import annotations

import logging
import time
from datetime import date
from urllib.parse import urlparse

import requests

from config import settings
from config.settings import (
    NSE_ARCHIVES_BASE,
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

# Retry / throttle defaults (the corporate-actions downloader reads them too).
MAX_RETRIES = 3
RETRY_WAIT = 6  # seconds between retries
REQUEST_DELAY = 0.8  # polite delay between downloads


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


def decode_csv_bytes(content: bytes) -> str:
    """Decode a CSV: UTF-8 (BOM allowed) first, Latin-1 as the fallback.

    Why: NSE's archive files carry no encoding label, and ``requests`` would guess Latin-1.
    """
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("latin-1")


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
# The one shared client
# ---------------------------------------------------------------------------

# One session for the whole process: NSE's cookies live in the session, so the warm-up of a
# host is only valid for the session that did it. Both are kept together here.
_session: requests.Session | None = None
_warmed_hosts: set[str] = set()


def _shared_session() -> requests.Session:
    """Return the process-wide NSE session (created on first use, with the browser headers)."""
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(HEADERS)
    return _session


def reset_client() -> None:
    """Forget the shared session and its warm-ups (used by tests, or to start clean)."""
    global _session
    _session = None
    _warmed_hosts.clear()


def _warm_up(session: requests.Session, url: str, timeout: int | float) -> None:
    """Visit the NSE homepage once per host so the session carries NSE's cookies.

    Why: www.nseindia.com answers its routes only after a homepage visit; the archives host
    (nsearchives.nseindia.com) does not need it. A failed visit is harmless: the real request
    is still attempted.
    """
    site = host(url)
    if site != NSE_WWW_HOST or site in _warmed_hosts:
        return
    try:
        session.get("https://www.nseindia.com", timeout=timeout)
    except requests.RequestException as exc:
        logger.debug("Homepage warm-up request failed: %s", exc)
    _warmed_hosts.add(site)


# ---------------------------------------------------------------------------
# Download helper
# ---------------------------------------------------------------------------


def fetch(
    url: str,
    *,
    timeout: int | float | None = None,
    retries: int = 3,
    retry_wait: float | None = None,
) -> bytes:
    """Download raw bytes from URL with retry, backoff and NSE warm-up.

    Parameters
    ----------
    url : str
        The URL to fetch.
    timeout : int or float, optional
        Request timeout in seconds (default: settings.NSE_REQUEST_TIMEOUT_SECONDS).
    retries : int, default 3
        Number of fetch attempts before raising FetchFailed.
    retry_wait : float, optional
        Fixed backoff duration in seconds. If None, uses exponential backoff (2**attempt).
    """
    if timeout is None:
        timeout = settings.NSE_REQUEST_TIMEOUT_SECONDS
    session = _shared_session()
    _warm_up(session, url, timeout)

    for attempt in range(retries):
        wait_duration = retry_wait if retry_wait is not None else 2**attempt
        try:
            r = session.get(url, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout):
            if attempt < retries - 1:
                time.sleep(wait_duration)
                continue
            break

        # HTTP 404 indicates no file exists for this date (holiday, weekend).
        # We do not retry 404s.
        if r.status_code == 404:
            raise NoFile(f"NSE has no file at {url} (HTTP 404)")

        # Temporary rate limiting (429), access block (403), or server errors (5xx)
        if r.status_code in (403, 429) or r.status_code >= 500:
            if attempt < retries - 1:
                time.sleep(wait_duration)
                continue
            break

        if r.status_code == 200:
            # Reject 200 OK responses that are HTML block pages
            check_not_html(r.content)
            return r.content

        # Any other unexpected HTTP status code
        if attempt < retries - 1:
            time.sleep(wait_duration)
            continue
        break

    raise FetchFailed(f"Failed to fetch {url} after {retries} attempts")


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


def bhavcopy_url(d: date) -> str:
    """Return official Bhavcopy URL: UDiFF format if d >= NSE_UDIFF_START else old format."""
    if d >= NSE_UDIFF_START:
        return bhavcopy_udiff_url(d)
    return bhavcopy_old_url(d)


def cm_52_wk_url(d: date) -> str:
    """Return official adjusted 52-week High/Low CSV URL: CM_52_wk_High_low_DDMMYYYY.csv."""
    ddmmyyyy = f"{d.day:02d}{d.month:02d}{d.year:04d}"
    return f"{NSE_ARCHIVES_BASE}/content/CM_52_wk_High_low_{ddmmyyyy}.csv"
