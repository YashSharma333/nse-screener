"""Tests for the NSE reports downloader (task CA1). Offline: HTTP is faked, nothing waits."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest
import requests

from src.ingestion.corporate_actions import (
    NSEDownloadError,
    NSEFormatChangedError,
    NSEReportsDownloader,
    build_action_rows,
    check_api_payload,
    decode_csv_bytes,
    default_window,
    parse_listing_master,
    parse_symbol_changes,
    year_chunks,
)

FIXTURE = Path(__file__).parent / "fixtures" / "corporate_actions_sample.json"
TODAY = date(2026, 11, 18)

SYMBOL_CHANGE_CSV = (
    "NAME OF THE COMPANY,OLD SYMBOL,NEW SYMBOL,EFFECTIVE DATE\n"
    " NIPPON INDIA MF - Dual Advantage,RDAXEDG,NDAXEDG,30-OCT-2019\n"
    "ICICI Life Insurance Limited,ICICIPRULI,ICICILIFE,23-OCT-2026\n"
    "ICICI Life Insurance Limited,ICICIPRULI,ICICILIFE,23-OCT-2026\n"
)
# EQUITY_L.csv really has a space in front of most header names.
LISTING_CSV = (
    "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, "
    "ISIN NUMBER, FACE VALUE\n"
    "360ONE,360 ONE WAM LIMITED,EQ,19-SEP-2019,1,1,INE466L01038,1\n"
    "20MICRONS,20 Microns Limited,EQ,06-OCT-2008,5,1,INE144J01027,5\n"
)


class FakeResponse:
    """Just enough of requests.Response for the downloader."""

    def __init__(self, status_code=200, payload=None, content=b"", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.content = content
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("not JSON")
        return self._payload


class FakeSession:
    """Plays back a list of responses (or exceptions) and remembers every call."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[tuple[str, dict | None]] = []
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def make_downloader(*sessions: FakeSession):
    """Downloader whose sessions are fakes. The list gives one session per refresh."""
    sleeps: list[float] = []
    downloader = NSEReportsDownloader(
        max_retries=3, retry_wait=7, request_delay=0.5, sleep=sleeps.append
    )
    queue = list(sessions)
    downloader._session = queue.pop(0)

    def fake_refresh():
        if queue:
            downloader._session = queue.pop(0)
        return downloader._session

    downloader.refresh_session = fake_refresh
    downloader.sleeps = sleeps
    return downloader


def ok_rows(n: int = 1):
    """n copies of one plain real row (a dividend: one action, no tricks)."""
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))
    base = next(row for row in rows if row["symbol"] == "PRECWIRE")
    return [dict(base) for _ in range(n)]


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------
def test_year_chunks_never_cross_a_year_boundary() -> None:
    chunks = year_chunks(date(2016, 11, 18), date(2018, 2, 3))
    assert chunks == [
        (date(2016, 11, 18), date(2016, 12, 31)),
        (date(2017, 1, 1), date(2017, 12, 31)),
        (date(2018, 1, 1), date(2018, 2, 3)),
    ]
    assert year_chunks(date(2026, 5, 1), date(2026, 5, 1)) == [(date(2026, 5, 1), date(2026, 5, 1))]
    with pytest.raises(ValueError, match="before start"):
        year_chunks(date(2026, 5, 2), date(2026, 5, 1))


def test_default_window_is_ten_years_back_and_ninety_days_ahead() -> None:
    assert default_window(TODAY, 10, 90) == (date(2016, 11, 18), date(2027, 2, 16))
    # 29 February has no ten-years-earlier twin in a non-leap year.
    assert default_window(date(2028, 2, 29), 10, 0)[0] == date(2018, 2, 28)


# ---------------------------------------------------------------------------
# Contract checks (what happens when NSE changes something)
# ---------------------------------------------------------------------------
def test_no_data_dict_is_an_empty_list_for_a_short_or_recent_window() -> None:
    reply = {"data": [], "msg": "no data found"}  # real NSE reply, HTTP 200
    assert check_api_payload(reply, date(2026, 11, 1), date(2026, 11, 3), TODAY) == []
    assert check_api_payload(reply, date(2026, 1, 1), date(2026, 12, 31), TODAY) == []


def test_empty_answer_for_a_long_past_window_is_an_error() -> None:
    with pytest.raises(NSEFormatChangedError, match="came back empty"):
        check_api_payload([], date(2024, 1, 1), date(2024, 12, 31), TODAY)


@pytest.mark.parametrize("payload", [{"unexpected": 1}, "<html>blocked</html>", 7, None])
def test_unknown_reply_shape_is_an_error(payload) -> None:
    with pytest.raises(NSEFormatChangedError, match="expected a JSON list"):
        check_api_payload(payload, date(2026, 11, 1), date(2026, 11, 3), TODAY)


def test_row_without_a_required_field_is_an_error_naming_the_field() -> None:
    row = ok_rows()[0]
    del row["exDate"]
    with pytest.raises(NSEFormatChangedError, match="exDate"):
        check_api_payload([row], date(2026, 11, 1), date(2026, 11, 3), TODAY)
    with pytest.raises(NSEFormatChangedError, match="not an object"):
        check_api_payload(["text"], date(2026, 11, 1), date(2026, 11, 3), TODAY)


def test_extra_new_fields_are_tolerated() -> None:
    row = ok_rows()[0] | {"brandNewField": "x"}
    assert check_api_payload([row], date(2026, 11, 1), date(2026, 11, 3), TODAY) == [row]


def test_bad_date_format_stops_the_run_and_names_the_row() -> None:
    row = ok_rows()[0] | {"exDate": "2026-11-18"}
    with pytest.raises(NSEFormatChangedError, match="not a %d-%b-%Y date"):
        build_action_rows([row], datetime(2026, 11, 18))


# ---------------------------------------------------------------------------
# Row building
# ---------------------------------------------------------------------------
def test_build_rows_drops_exact_duplicates_and_splits_combined_subjects() -> None:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))  # contains one duplicated row
    rows, parts = build_action_rows(raw, datetime(2026, 11, 18, 12, 0))
    dividends = [r for r in rows if r["symbol"] == "VADILALIND"]
    assert len(dividends) == 1  # the duplicate is gone
    sunil = sorted((r for r in rows if r["symbol"] == "SUNILHITEC"), key=lambda r: r["part_no"])
    assert [(r["action_type"], r["price_factor"]) for r in sunil] == [
        ("BONUS", 0.5),
        ("SPLIT", 0.1),
    ]
    assert len(parts) == len(rows)
    keys = {(r["symbol"], r["ex_date"], r["series"], r["subject_hash"], r["part_no"]) for r in rows}
    assert len(keys) == len(rows)  # the unique key never repeats


def test_build_rows_cleans_types_and_blanks() -> None:
    raw = ok_rows()[0] | {"faceVal": 5.0, "recDate": "-", "symbol": " cochinship ", "comp": " Co "}
    (row,), _ = build_action_rows([raw], datetime(2026, 11, 18))
    assert (row["symbol"], row["company"], row["face_value"]) == ("COCHINSHIP", "Co", 5.0)
    assert row["record_date"] is None
    assert isinstance(row["ex_date"], date)
    (no_value,), _ = build_action_rows([raw | {"faceVal": "-"}], datetime(2026, 11, 18))
    assert no_value["face_value"] is None  # "-" is not a number


# ---------------------------------------------------------------------------
# HTTP behaviour
# ---------------------------------------------------------------------------
def test_403_refreshes_the_session_and_retries() -> None:
    blocked = FakeSession(FakeResponse(403))
    fresh = FakeSession(FakeResponse(200, payload=ok_rows(2)))
    downloader = make_downloader(blocked, fresh)
    rows = downloader.fetch_corporate_actions_window(date(2026, 11, 1), date(2026, 11, 3), TODAY)
    assert len(rows) == 2
    assert len(blocked.calls) == 1
    assert len(fresh.calls) == 1
    assert downloader.sleeps == [7]  # waited retry_wait once


def test_reply_that_is_not_json_is_retried_like_a_block() -> None:
    first = FakeSession(FakeResponse(200, payload=None, content=b"<html>"))
    second = FakeSession(FakeResponse(200, payload=ok_rows()))
    downloader = make_downloader(first, second)
    assert downloader.fetch_corporate_actions_window(date(2026, 11, 1), date(2026, 11, 3), TODAY)


def test_429_waits_for_the_retry_after_header() -> None:
    session = FakeSession(
        FakeResponse(429, headers={"Retry-After": "12"}), FakeResponse(200, payload=ok_rows())
    )
    downloader = make_downloader(session)
    downloader.fetch_corporate_actions_window(date(2026, 11, 1), date(2026, 11, 3), TODAY)
    assert downloader.sleeps == [12.0]


def test_404_stops_immediately_without_retrying() -> None:
    session = FakeSession(FakeResponse(404))
    downloader = make_downloader(session)
    with pytest.raises(NSEDownloadError, match="404"):
        downloader.fetch_symbol_changes()
    assert len(session.calls) == 1


def test_repeated_timeouts_raise_after_the_last_attempt() -> None:
    session = FakeSession(*[requests.exceptions.Timeout("slow")] * 3)
    downloader = make_downloader(session)
    with pytest.raises(NSEDownloadError, match="after 3 attempts"):
        downloader.fetch_listing_master()
    assert len(session.calls) == 3
    assert downloader.sleeps == [7, 7]  # no pointless wait after the final failure


def test_fetch_corporate_actions_asks_one_year_at_a_time_with_nse_date_format() -> None:
    session = FakeSession(
        FakeResponse(200, payload=ok_rows(1)), FakeResponse(200, payload=ok_rows(2))
    )
    downloader = make_downloader(session)
    rows = downloader.fetch_corporate_actions(date(2025, 11, 18), date(2026, 11, 18), TODAY)
    assert len(rows) == 3
    params = [call[1] for call in session.calls]
    assert params[0] == {"index": "equities", "from_date": "18-11-2025", "to_date": "31-12-2025"}
    assert params[1] == {"index": "equities", "from_date": "01-01-2026", "to_date": "18-11-2026"}
    assert downloader.sleeps == [0.5]  # polite pause between the two calls


# ---------------------------------------------------------------------------
# CSV reports
# ---------------------------------------------------------------------------
def test_symbol_changes_parse_and_deduplicate() -> None:
    frame = parse_symbol_changes(SYMBOL_CHANGE_CSV.encode("latin-1"))
    assert list(frame.columns) == ["company", "old_symbol", "new_symbol", "effective_date"]
    assert len(frame) == 2  # the repeated ICICIPRULI line is dropped
    row = frame[frame.old_symbol == "ICICIPRULI"].iloc[0]
    assert (row.new_symbol, row.effective_date) == ("ICICILIFE", date(2026, 10, 23))


def test_listing_master_handles_leading_spaces_in_headers() -> None:
    frame = parse_listing_master(LISTING_CSV.encode("utf-8"))
    assert list(frame.columns) == [
        "symbol",
        "company",
        "series",
        "listing_date",
        "isin",
        "face_value",
    ]
    row = frame[frame.symbol == "360ONE"].iloc[0]
    assert (row["isin"], row["face_value"], row["listing_date"]) == (
        "INE466L01038",
        1.0,
        date(2019, 9, 19),
    )


def test_renamed_or_missing_csv_columns_are_an_error_not_a_guess() -> None:
    renamed = SYMBOL_CHANGE_CSV.replace("OLD SYMBOL", "PREVIOUS SYMBOL").encode()
    with pytest.raises(NSEFormatChangedError, match="OLD SYMBOL"):
        parse_symbol_changes(renamed)
    # Columns in a different ORDER are fine because we match by name.
    reordered = "EFFECTIVE DATE,NEW SYMBOL,OLD SYMBOL,NAME OF THE COMPANY\n23-OCT-2026,B,A,Co\n"
    assert parse_symbol_changes(reordered.encode()).iloc[0].old_symbol == "A"


def test_bad_csv_date_is_an_error() -> None:
    broken = SYMBOL_CHANGE_CSV.replace("23-OCT-2026", "2026/10/23")
    with pytest.raises(NSEFormatChangedError, match=r"symbolchange\.csv row"):
        parse_symbol_changes(broken.encode())


def test_decode_falls_back_to_latin1_and_strips_a_bom() -> None:
    assert decode_csv_bytes(b"\xef\xbb\xbfA,B") == "A,B"
    assert decode_csv_bytes("Zoë".encode("latin-1")) == "Zoë"
