"""
NSE official reports downloader (corporate actions, symbol changes, listing master)
===================================================================================
Downloads three official NSE reports through the one NSE client (``nse_files.fetch``):

1. **Corporate actions** (splits, bonuses, rights, mergers, dividends ...): the JSON API
   behind NSE's "Corporate Filings > Corporate Actions" page. There is no static CSV for
   this report (the page's "Download (.csv)" link builds the file in the browser from this
   same API), so the API is the only source. Verified 18 Nov 2026: the API returns ten
   years in one call; the website's "1 year" limit is only in its dropdown.
2. **Symbol changes**: ``symbolchange.csv`` on the archive host (renames such as
   ICICIPRULI to ICICILIFE). The API cannot give renames.
3. **Listing master**: ``EQUITY_L.csv`` (symbol, ISIN, face value, listing date).

Session, headers, warm-up and retry rules live only in ``src/ingestion/nse_files.py``; this
module adds the polite pause between yearly calls and the format checks.

NSE changes formats from time to time, so this module checks what it receives and fails
loudly (:class:`NSEFormatChangedError`) instead of storing something wrong. Reading the text of
a corporate action lives in ``corporate_actions_parser.py``.
"""

from __future__ import annotations

import io
import json
import logging
import time
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from config import settings
from config.settings import (
    NSE_API_BASE,
    NSE_ARCHIVES_BASE,
    NSE_CORP_ACTIONS_API_PATH,
)
from src.common.dates import parse_nse_date
from src.common.symbols import normalise_symbol
from src.ingestion import nse_files
from src.ingestion.corporate_actions_parser import (
    ActionPart,
    parse_subject,
    subject_hash,
)
from src.ingestion.nse_files import (
    EQUITY_L_URL,
    MAX_RETRIES,
    REQUEST_DELAY,
    RETRY_WAIT,
    SYMBOLCHANGE_URL,
    BadFile,
    FetchFailed,
    NoFile,
    check_not_html,
    decode_csv_bytes,
)

logger = logging.getLogger(__name__)

# Fields every API row must carry. Extra fields are fine (only logged), missing ones are not.
REQUIRED_API_FIELDS: tuple[str, ...] = (
    "symbol",
    "comp",
    "isin",
    "series",
    "faceVal",
    "subject",
    "exDate",
    "recDate",
)
# Columns we need in the CSV reports, matched by NAME (never by position), after the header
# text is stripped and upper-cased (EQUITY_L.csv has leading spaces in some headers).
SYMBOL_CHANGE_COLUMNS: tuple[str, ...] = (
    "NAME OF THE COMPANY",
    "OLD SYMBOL",
    "NEW SYMBOL",
    "EFFECTIVE DATE",
)
LISTING_MASTER_COLUMNS: tuple[str, ...] = (
    "SYMBOL",
    "NAME OF COMPANY",
    "SERIES",
    "DATE OF LISTING",
    "ISIN NUMBER",
    "FACE VALUE",
)
NSE_DATE_FORMAT = "%d-%b-%Y"  # 18-Nov-2026 (also accepts 18-NOV-2026)
# A past window at least this long that comes back empty means something is wrong
# (real data has about 2,000 actions per year).
_EMPTY_WINDOW_MIN_DAYS = 60
_EMPTY_WINDOW_MIN_AGE_DAYS = 30


class NSEDownloadError(RuntimeError):
    """A report could not be downloaded (network failure, blocked, or file missing)."""


class NSEFormatChangedError(RuntimeError):
    """NSE answered, but the content does not look like the format we know.

    Nothing is written to the database when this is raised.
    """


# ---------------------------------------------------------------------------
# Pure helpers: date windows, contract checks, parsing (no network)
# ---------------------------------------------------------------------------
def year_chunks(start: date, end: date) -> list[tuple[date, date]]:
    """Split ``start..end`` into pieces that never cross a calendar-year boundary."""
    if end < start:
        raise ValueError(f"end {end} is before start {start}")
    chunks = []
    piece_start = start
    while piece_start <= end:
        piece_end = min(end, date(piece_start.year, 12, 31))
        chunks.append((piece_start, piece_end))
        piece_start = piece_end + timedelta(days=1)
    return chunks


def default_window(today: date, history_years: int, forward_days: int) -> tuple[date, date]:
    """Default download window: ``history_years`` back, ``forward_days`` into the future."""
    try:
        start = today.replace(year=today.year - history_years)
    except ValueError:  # 29 February in a year that has no 29 February
        start = today.replace(year=today.year - history_years, day=28)
    return start, today + timedelta(days=forward_days)


def _must_have_rows(start: date, end: date, today: date) -> bool:
    """True for a long window entirely in the past, where an empty answer is not believable."""
    long_enough = (end - start).days >= _EMPTY_WINDOW_MIN_DAYS
    in_the_past = end <= today - timedelta(days=_EMPTY_WINDOW_MIN_AGE_DAYS)
    return long_enough and in_the_past


def check_api_payload(
    payload: Any, start: date, end: date, today: date | None = None
) -> list[dict[str, Any]]:
    """Check that the API reply has the shape we know and return its rows.

    Real shapes seen on 18 Nov 2026:
    * success: a JSON list of row dicts;
    * no data / bad parameters: HTTP 200 with ``{"data": [], "msg": "no data found"}``
      (not an error status, so it must be checked by shape).
    Anything else raises :class:`NSEFormatChangedError`.
    """
    today = today or date.today()
    if isinstance(payload, dict) and payload.get("data") == []:
        rows: list[Any] = []
    elif isinstance(payload, list):
        rows = payload
    else:
        shape = f"keys {sorted(payload)[:8]}" if isinstance(payload, dict) else type(payload)
        raise NSEFormatChangedError(
            f"Corporate actions {start}..{end}: expected a JSON list, got {shape}."
        )

    for position, row in enumerate(rows):
        if not isinstance(row, dict):
            raise NSEFormatChangedError(
                f"Corporate actions row {position} is not an object: {row!r}"
            )
        missing = [name for name in REQUIRED_API_FIELDS if name not in row]
        if missing:
            raise NSEFormatChangedError(
                f"Corporate actions row {position} lacks fields {missing}; "
                f"it has {sorted(row)}. NSE changed the report format."
            )

    if not rows and _must_have_rows(start, end, today):
        raise NSEFormatChangedError(
            f"Corporate actions {start}..{end} came back empty; a past window of this length "
            "always has data, so NSE probably changed the endpoint or its parameters."
        )
    return rows


def _parse_nse_date(value: Any, label: str, allow_blank: bool = False) -> date | None:
    """Parse ``18-Nov-2026`` strictly. A bad date stops the run and names the field."""
    if allow_blank and str(value).strip() in ("", "-"):
        return None
    return parse_nse_date(
        value,
        (NSE_DATE_FORMAT,),
        strict=True,
        error=NSEFormatChangedError,
        label=label,
        hint="NSE changed the date format.",
    )


def _to_float(value: Any) -> float | None:
    """Convert NSE's number-or-text values (``faceVal`` is both) to float, else None."""
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def build_action_rows(
    raw_rows: list[dict[str, Any]], fetched_at: datetime
) -> tuple[list[dict[str, Any]], list[ActionPart]]:
    """Turn raw API rows into database rows, one per action part.

    * exact duplicate rows are dropped (NSE sends a few);
    * a subject with two actions becomes two rows (``part_no`` 0, 1);
    * rows with the same unique key keep the last one, so the upsert never sees a key twice.

    Returns ``(rows_for_database, all_parts)``; the parts feed the wording canary.
    """
    seen_raw: set[str] = set()
    rows_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    all_parts: list[ActionPart] = []

    for raw in raw_rows:
        fingerprint = json.dumps(raw, sort_keys=True, default=str)
        if fingerprint in seen_raw:
            continue
        seen_raw.add(fingerprint)

        symbol = normalise_symbol(raw["symbol"])
        label = f"corporate action {symbol}"
        ex_date = _parse_nse_date(raw["exDate"], f"{label} exDate")
        record_date = _parse_nse_date(raw["recDate"], f"{label} recDate", allow_blank=True)
        series = str(raw["series"]).strip()
        subject_text = " ".join(str(raw["subject"]).split())
        fingerprint_of_subject = subject_hash(subject_text)

        for part in parse_subject(subject_text):
            all_parts.append(part)
            key = (symbol, ex_date, series, fingerprint_of_subject, part.part_no)
            rows_by_key[key] = {
                "symbol": symbol,
                "ex_date": ex_date,
                "series": series,
                "isin": str(raw["isin"]).strip(),
                "company": str(raw["comp"]).strip(),
                "face_value": _to_float(raw["faceVal"]),
                "record_date": record_date,
                "action_type": part.action_type,
                "part_no": part.part_no,
                "subject": subject_text[:500],
                "subject_hash": fingerprint_of_subject,
                "old_face_value": part.old_face_value,
                "new_face_value": part.new_face_value,
                "bonus_new_shares": part.bonus_new_shares,
                "bonus_held_shares": part.bonus_held_shares,
                "rights_new": part.rights_new,
                "rights_held": part.rights_held,
                "price_factor": part.price_factor,
                "parse_status": part.parse_status,
                "fetched_at": fetched_at,
            }
    return list(rows_by_key.values()), all_parts


def _read_report_csv(content: bytes, required: tuple[str, ...], name: str) -> pd.DataFrame:
    """Read a CSV report as text, normalise header names and check the needed columns exist."""
    check_not_html(content)
    frame = pd.read_csv(io.StringIO(decode_csv_bytes(content)), dtype=str, keep_default_na=False)
    frame.columns = [str(col).strip().upper() for col in frame.columns]
    missing = [col for col in required if col not in frame.columns]
    if missing:
        raise NSEFormatChangedError(
            f"{name}: missing columns {missing}; found {list(frame.columns)}. "
            "NSE changed the report format."
        )
    return frame.apply(lambda column: column.str.strip())


def _date_column(frame: pd.DataFrame, column: str, name: str) -> list[date | None]:
    """Parse a whole date column strictly (blank cells become None)."""
    return [
        _parse_nse_date(value, f"{name} row {position + 2} {column}", allow_blank=True)
        for position, value in enumerate(frame[column])
    ]


def parse_symbol_changes(content: bytes) -> pd.DataFrame:
    """Parse ``symbolchange.csv`` into ``company, old_symbol, new_symbol, effective_date``.

    Rows that are not a real equity rename are dropped: a blank company name (bonds and
    debentures) and ``old == new`` (a re-listing line, not a rename). Repeated lines are merged.
    """
    frame = _read_report_csv(content, SYMBOL_CHANGE_COLUMNS, "symbolchange.csv")
    result = pd.DataFrame(
        {
            "company": frame["NAME OF THE COMPANY"],
            "old_symbol": frame["OLD SYMBOL"].str.upper(),
            "new_symbol": frame["NEW SYMBOL"].str.upper(),
            "effective_date": _date_column(frame, "EFFECTIVE DATE", "symbolchange.csv"),
        }
    )
    complete = (result.old_symbol != "") & (result.new_symbol != "")
    complete &= result.effective_date.notna()
    complete &= result.company != ""
    complete &= result.old_symbol != result.new_symbol
    if not complete.all():
        logger.warning(
            "symbolchange.csv: skipped %d rows (blank company, same symbol or missing field)",
            int((~complete).sum()),
        )
    result = result[complete]
    return result.drop_duplicates(["old_symbol", "new_symbol", "effective_date"], keep="last")


def parse_listing_master(content: bytes) -> pd.DataFrame:
    """Parse ``EQUITY_L.csv`` into a typed table (listing date as a real date)."""
    frame = _read_report_csv(content, LISTING_MASTER_COLUMNS, "EQUITY_L.csv")
    return pd.DataFrame(
        {
            "symbol": frame["SYMBOL"].str.upper(),
            "company": frame["NAME OF COMPANY"],
            "series": frame["SERIES"],
            "listing_date": _date_column(frame, "DATE OF LISTING", "EQUITY_L.csv"),
            "isin": frame["ISIN NUMBER"],
            "face_value": pd.to_numeric(frame["FACE VALUE"], errors="coerce"),
        }
    )


# ---------------------------------------------------------------------------
# Downloader
# ---------------------------------------------------------------------------
class NSEReportsDownloader:
    """Downloads the three reports through ``nse_files.fetch`` (one shared warm session).

    Usage::

        dl = NSEReportsDownloader()
        rows = dl.fetch_corporate_actions(date(2016, 11, 18), date(2026, 11, 18))
    """

    def __init__(
        self,
        max_retries: int = MAX_RETRIES,
        retry_wait: float = RETRY_WAIT,
        request_timeout: float | None = None,
        request_delay: float = REQUEST_DELAY,
        api_base: str = NSE_API_BASE,
        archives_base: str = NSE_ARCHIVES_BASE,
    ) -> None:
        self.max_retries = max_retries
        self.retry_wait = retry_wait
        self.request_timeout = (
            settings.NSE_REQUEST_TIMEOUT_SECONDS if request_timeout is None else request_timeout
        )
        self.request_delay = request_delay
        self.api_base = api_base.rstrip("/")
        self.archives_base = archives_base.rstrip("/")

    def _download(self, url: str) -> bytes:
        """Fetch one URL; every failure becomes :class:`NSEDownloadError` with a clear reason.

        404 is not retried (NSE moved or removed the report); a blocked or broken answer is
        reported after the last attempt.
        """
        try:
            content = nse_files.fetch(
                url,
                timeout=self.request_timeout,
                retries=self.max_retries,
                retry_wait=self.retry_wait,
            )
        except NoFile as exc:
            raise NSEDownloadError(f"404 for {url}: NSE moved or removed this report.") from exc
        except (FetchFailed, BadFile) as exc:
            raise NSEDownloadError(
                f"{url} failed after {self.max_retries} attempts: {exc}"
            ) from exc
        return content.encode("utf-8") if isinstance(content, str) else content

    def fetch_corporate_actions_window(
        self, start: date, end: date, today: date | None = None
    ) -> list[dict[str, Any]]:
        """Download corporate actions whose ex-date is in ``start..end`` (inclusive)."""
        from_str = start.strftime("%d-%m-%Y")
        to_str = end.strftime("%d-%m-%Y")
        url = (
            f"{self.api_base}{NSE_CORP_ACTIONS_API_PATH}"
            f"?index=equities&from_date={from_str}&to_date={to_str}"
        )
        content = self._download(url)
        try:
            payload = json.loads(content.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise NSEDownloadError(f"Reply was not valid JSON from {url}: {exc}") from exc

        return check_api_payload(payload, start, end, today)

    def fetch_corporate_actions(
        self, start: date, end: date, today: date | None = None
    ) -> list[dict[str, Any]]:
        """Download ``start..end`` one calendar year at a time and join the pieces."""
        all_rows: list[dict[str, Any]] = []
        chunks = year_chunks(start, end)
        for number, (chunk_start, chunk_end) in enumerate(chunks):
            if number:
                time.sleep(self.request_delay)  # polite pause between calls
            rows = self.fetch_corporate_actions_window(chunk_start, chunk_end, today)
            logger.info("Corporate actions %s..%s: %d rows", chunk_start, chunk_end, len(rows))
            all_rows.extend(rows)
        return all_rows

    def fetch_symbol_changes(self) -> tuple[bytes, pd.DataFrame]:
        """Download ``symbolchange.csv``. Returns ``(raw_bytes, parsed_table)``."""
        content = self._download(SYMBOLCHANGE_URL)
        return content, parse_symbol_changes(content)

    def fetch_listing_master(self) -> tuple[bytes, pd.DataFrame]:
        """Download ``EQUITY_L.csv``. Returns ``(raw_bytes, parsed_table)``."""
        content = self._download(EQUITY_L_URL)
        return content, parse_listing_master(content)


__all__ = [
    "NSEDownloadError",
    "NSEFormatChangedError",
    "NSEReportsDownloader",
    "build_action_rows",
    "check_api_payload",
    "default_window",
    "parse_listing_master",
    "parse_symbol_changes",
    "year_chunks",
]
