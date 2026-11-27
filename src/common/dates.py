"""The one place that reads dates written in NSE's many text formats."""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd

# The formats NSE's reference files use ('19-SEP-2019', '19-September-2019', '19-09-2019', ISO).
NSE_DATE_FORMATS: tuple[str, ...] = ("%d-%b-%Y", "%d-%B-%Y", "%d-%m-%Y", "%Y-%m-%d")

# Text that NSE writes when a date is simply not there.
BLANK_DATE_TEXTS: frozenset[str] = frozenset({"", "-", "NA", "NULL"})


def is_blank_date(value: object) -> bool:
    """True for None, NaN/NaT and the blank markers NSE uses ('', '-', 'NA', 'NULL')."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return True
    return str(value).strip().upper() in BLANK_DATE_TEXTS


def parse_nse_date(
    value: object,
    formats: tuple[str, ...] = NSE_DATE_FORMATS,
    *,
    strict: bool = False,
    dayfirst: bool | None = None,
    error: type[Exception] = ValueError,
    label: str = "",
    hint: str = "",
) -> date | None:
    """Turn ``value`` into a ``date`` by trying each format in ``formats`` in order.

    Why one function: eight copies of this job had drifted apart. The differences are now
    explicit options:

    - ``strict=False`` (default): a blank or unreadable value gives ``None``.
    - ``strict=True``: an unreadable value (a blank too) raises ``error`` (default
      ``ValueError``) naming ``label``. A caller that allows blanks checks ``is_blank_date``
      first.
    - ``dayfirst``: when not None, pandas' flexible parser is the last resort
      (``dayfirst=True`` reads '04-06-2023' as 4 June).
    - ``hint``: extra text added to the strict error message.

    ``date``, ``datetime`` and ``pd.Timestamp`` values pass straight through as a plain date.
    """
    if isinstance(value, pd.Timestamp | datetime):
        return None if pd.isna(value) else value.date()
    if isinstance(value, date):
        return value

    parsed = None if is_blank_date(value) else _parse_text(str(value).strip(), formats, dayfirst)
    if parsed is None and strict:
        shown = " or ".join(formats)
        prefix = f"{label}: " if label else ""
        raise error(f"{prefix}'{str(value).strip()}' is not a {shown} date. {hint}".strip())
    return parsed


def _parse_text(text: str, formats: tuple[str, ...], dayfirst: bool | None) -> date | None:
    """Try each strptime format, then (only if asked) pandas. None when nothing fits."""
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    if dayfirst is None:
        return None
    try:
        parsed = pd.to_datetime(text, dayfirst=dayfirst)
    except (ValueError, TypeError, OverflowError):
        return None
    return None if pd.isna(parsed) else parsed.date()


def date_or_default(value: date | str | None, default: date) -> date:
    """A date, an ISO string ('2026-11-20') or None (use ``default``) as a date."""
    if value is None:
        return default
    return date.fromisoformat(value) if isinstance(value, str) else value
