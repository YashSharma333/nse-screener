"""
Unit tests for src/ingestion/nse_files.py (task D2)
===================================================
Tests run strictly offline with FakeSession and FakeResponse:
no network calls, no database access.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
import requests

from src.ingestion.nse_files import (
    EQUITY_L_URL,
    SYMBOLCHANGE_URL,
    BadFile,
    FetchFailed,
    NoFile,
    bhavcopy_old_url,
    bhavcopy_udiff_url,
    bhavcopy_url,
    check_date,
    check_not_html,
    check_zip,
    fetch,
    pr_bundle_url,
)

# ---------------------------------------------------------------------------
# Test doubles (offline fakes)
# ---------------------------------------------------------------------------


class FakeResponse:
    """Offline simulated HTTP response."""

    def __init__(self, status_code: int = 200, content: bytes = b"test content"):
        self.status_code = status_code
        self.content = content
        self.text = content.decode("utf-8", errors="replace")


class FakeSession:
    """Offline simulated requests.Session recording get calls."""

    def __init__(self, responses: list[Any] | None = None):
        self.headers: dict[str, str] = {}
        self.responses: list[Any] = list(responses) if responses is not None else [FakeResponse()]
        self.calls: list[tuple[str, Any]] = []

    def get(self, url: str, timeout: Any = None) -> FakeResponse:
        self.calls.append((url, timeout))
        if not self.responses:
            return FakeResponse(200, b"default")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


def test_fetch_returns_bytes_on_200(fake_nse_session):
    """Successful HTTP 200 returns the raw bytes downloaded."""
    expected_data = b"SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE\nINFY,EQ,1500,1520,1490,1510\n"
    session = fake_nse_session(FakeSession([FakeResponse(200, expected_data)]))
    result = fetch("https://nsearchives.nseindia.com/content/cm/test.csv")
    assert result == expected_data
    assert len(session.calls) == 1
    assert session.calls[0][0] == "https://nsearchives.nseindia.com/content/cm/test.csv"


def test_404_raises_nofile_and_does_not_retry(fake_nse_session, nse_sleeps):
    """HTTP 404 indicates non-trading day/holiday; raises NoFile immediately (call count 1)."""
    session = fake_nse_session(FakeSession([FakeResponse(404, b"Not Found")]))

    with pytest.raises(NoFile) as exc_info:
        fetch("https://nsearchives.nseindia.com/content/cm/test.csv")

    assert "HTTP 404" in str(exc_info.value)
    # Exactly one request made, no retry on 404
    assert len(session.calls) == 1
    assert nse_sleeps == []


def test_retries_on_503_then_succeeds(fake_nse_session, nse_sleeps):
    """Server error (503) triggers exponential backoff and retries, succeeding on attempt 3."""
    expected_data = b"recovered content"
    session = fake_nse_session(
        FakeSession(
            [
                FakeResponse(503, b"Service Unavailable"),
                FakeResponse(503, b"Service Unavailable"),
                FakeResponse(200, expected_data),
            ]
        )
    )

    result = fetch("https://nsearchives.nseindia.com/content/cm/test.csv")

    assert result == expected_data
    assert len(session.calls) == 3
    # Exponential backoff sleeps: 2**0 = 1s, 2**1 = 2s
    assert nse_sleeps == [1, 2]


@pytest.mark.parametrize("status", [403, 429])
def test_blocked_or_rate_limited_is_retried(fake_nse_session, nse_sleeps, status):
    """403 (blocked) and 429 (too many requests) are temporary: retried, then succeed."""
    session = fake_nse_session(
        FakeSession([FakeResponse(status, b"slow down"), FakeResponse(200, b"data")])
    )
    assert fetch("https://nsearchives.nseindia.com/content/cm/test.csv") == b"data"
    assert len(session.calls) == 2
    assert nse_sleeps == [1]


def test_gives_up_after_three_failures_raises_fetchfailed(fake_nse_session, nse_sleeps):
    """Exhausting all 3 attempts on continuous 500 errors raises FetchFailed."""
    session = fake_nse_session(
        FakeSession(
            [
                FakeResponse(500, b"Internal Error"),
                FakeResponse(500, b"Internal Error"),
                FakeResponse(500, b"Internal Error"),
            ]
        )
    )

    with pytest.raises(FetchFailed) as exc_info:
        fetch("https://nsearchives.nseindia.com/content/cm/test.csv", retries=3)

    assert "Failed to fetch" in str(exc_info.value)
    assert len(session.calls) == 3
    assert nse_sleeps == [1, 2]


def test_fixed_retry_wait_replaces_exponential_backoff(fake_nse_session, nse_sleeps):
    """retry_wait gives the same pause between attempts (the corporate-actions downloader)."""
    fake_nse_session(FakeSession([FakeResponse(500), FakeResponse(500), FakeResponse(200)]))
    fetch("https://nsearchives.nseindia.com/content/cm/test.csv", retry_wait=7)
    assert nse_sleeps == [7, 7]


def test_connection_error_is_retried(fake_nse_session, nse_sleeps):
    """Network connection errors are caught, delayed, and retried."""
    expected_data = b"ok after reconnect"
    session = fake_nse_session(
        FakeSession(
            [
                requests.ConnectionError("Connection reset by peer"),
                FakeResponse(200, expected_data),
            ]
        )
    )

    result = fetch("https://nsearchives.nseindia.com/content/cm/test.csv")

    assert result == expected_data
    assert len(session.calls) == 2
    assert nse_sleeps == [1]


def test_html_page_with_200_raises_badfile(fake_nse_session):
    """An HTML page returned with HTTP 200 (such as an NSE block page) raises BadFile."""
    html_page = (
        b"<!DOCTYPE html>\n<html><head><title>Access Denied</title></head>"
        b"<body>Blocked by security policy</body></html>"
    )

    # Calling check_not_html directly raises BadFile
    with pytest.raises(BadFile) as exc_info:
        check_not_html(html_page)
    assert "HTML error/block page" in str(exc_info.value)

    # Calling fetch with a 200 response containing HTML also raises BadFile
    fake_nse_session(FakeSession([FakeResponse(200, html_page)]))
    with pytest.raises(BadFile):
        fetch("https://nsearchives.nseindia.com/content/cm/test.csv")


def test_bad_zip_magic_raises_badfile():
    """Files that do not start with b'PK' raise BadFile in check_zip."""
    # Valid ZIP header starts with b"PK"
    check_zip(b"PK\x03\x04\x14\x00...")

    # Corrupt or HTML content raises BadFile
    with pytest.raises(BadFile) as exc_info:
        check_zip(b"<html>This is not a zip file</html>")
    assert "missing PK signature" in str(exc_info.value)

    with pytest.raises(BadFile):
        check_zip(b"")


def test_check_date_wrong_date_raises_badfile():
    """Mismatched date inside file raises BadFile; matching date passes cleanly."""
    # Matching dates pass
    check_date(date(2024, 1, 1), date(2024, 1, 1))
    check_date("2024-01-01", "2024-01-01")
    check_date(date(2024, 1, 1), "2024-01-01")

    # Mismatched dates raise BadFile
    with pytest.raises(BadFile) as exc_info:
        check_date("2024-01-01", "2024-01-02")
    assert "2024-01-01" in str(exc_info.value)
    assert "2024-01-02" in str(exc_info.value)

    with pytest.raises(BadFile):
        check_date(date(2024, 1, 1), date(2024, 1, 2))


def test_warmup_only_for_www_host(fake_nse_session):
    """Only www.nseindia.com triggers cookie warm-up, and only once for the shared session."""
    # 1. Archives host makes exactly 1 request (no warmup)
    session = fake_nse_session(FakeSession([FakeResponse(200, b"data")]))
    fetch("https://nsearchives.nseindia.com/content/file.csv")
    assert len(session.calls) == 1
    assert session.calls[0][0] == "https://nsearchives.nseindia.com/content/file.csv"

    # 2. www.nseindia.com warms up first with GET https://www.nseindia.com (2 requests more)
    session.responses += [FakeResponse(200, b"cookie"), FakeResponse(200, b"api_data")]
    fetch("https://www.nseindia.com/api/test")
    assert len(session.calls) == 3
    assert session.calls[1][0] == "https://www.nseindia.com"
    assert session.calls[2][0] == "https://www.nseindia.com/api/test"

    # 3. The next www request does not repeat the warm-up
    session.responses.append(FakeResponse(200, b"api_data_2"))
    fetch("https://www.nseindia.com/api/test2")
    assert len(session.calls) == 4
    assert session.calls[3][0] == "https://www.nseindia.com/api/test2"


def test_reset_client_starts_a_fresh_session_and_warm_up(fake_nse_session):
    """A new session has no cookies, so the warm-up must be done again after a reset."""
    from src.ingestion import nse_files

    fake_nse_session(FakeSession([FakeResponse(), FakeResponse()]))
    fetch("https://www.nseindia.com/api/a")
    assert nse_files._warmed_hosts == {"www.nseindia.com"}

    nse_files.reset_client()
    assert nse_files._session is None
    assert nse_files._warmed_hosts == set()


def test_shared_session_is_reused_and_carries_browser_headers(monkeypatch):
    """One real requests.Session for all downloads, created once, with the browser headers."""
    from src.ingestion import nse_files

    nse_files.reset_client()
    first = nse_files._shared_session()
    assert nse_files._shared_session() is first
    assert first.headers["User-Agent"] == nse_files.HEADERS["User-Agent"]


def test_url_builders():
    """Verify exact URL strings produced by pure URL builder functions."""
    # UDiFF format: 2024-01-01 and 2025-11-17
    assert (
        bhavcopy_udiff_url(date(2024, 1, 1))
        == "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_20240101_F_0000.csv.zip"
    )
    assert (
        bhavcopy_udiff_url(date(2025, 11, 17))
        == "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_20251117_F_0000.csv.zip"
    )

    # Old format: 2016-11-21 gives .../2016/NOV/cm21NOV2016bhav.csv.zip
    assert (
        bhavcopy_old_url(date(2016, 11, 21))
        == "https://nsearchives.nseindia.com/content/historical/EQUITIES/2016/NOV/cm21NOV2016bhav.csv.zip"
    )

    # PR bundle: 2024-02-01 gives PR010224.zip
    assert (
        pr_bundle_url(date(2024, 2, 1))
        == "https://nsearchives.nseindia.com/archives/equities/bhavcopy/pr/PR010224.zip"
    )

    # Listing master and symbol change URLs
    assert EQUITY_L_URL == "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
    assert SYMBOLCHANGE_URL == "https://nsearchives.nseindia.com/content/equities/symbolchange.csv"


def test_bhavcopy_url_switches_on_2024_01_01():
    """Bhavcopy URL switcher uses old format before 2024-01-01, UDiFF on or after."""
    # Day before cut-over (2023-12-29) uses old format
    assert (
        bhavcopy_url(date(2023, 12, 29))
        == "https://nsearchives.nseindia.com/content/historical/EQUITIES/2023/DEC/cm29DEC2023bhav.csv.zip"
    )
    # Cut-over day (2024-01-01) uses UDiFF
    assert (
        bhavcopy_url(date(2024, 1, 1))
        == "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_20240101_F_0000.csv.zip"
    )
