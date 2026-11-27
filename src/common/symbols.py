"""The one place that cleans a stock symbol."""

from __future__ import annotations

import pandas as pd

from config import settings


def normalise_symbol(value: object) -> str:
    """Trim spaces and upper-case a symbol, e.g. ``' tcs '`` -> ``'TCS'``.

    Why: NSE files and our own tables must agree on spelling, or a join silently misses rows.
    This only fixes case and spaces. It never renames a symbol (renames live in
    ``symbol_changes``).
    """
    return str(value).strip().upper()


def is_fund_isin(isin: object) -> bool:
    """True for a fund unit (ETF, mutual fund) by its ISIN prefix, e.g. ``INF204KB14I2``.

    Why: the project screens company shares only; ``settings.EXCLUDED_ISIN_PREFIXES`` is the rule.
    A missing ISIN is not a fund (we never guess).
    """
    return str(isin).strip().upper().startswith(settings.EXCLUDED_ISIN_PREFIXES)


def drop_funds(frame: pd.DataFrame, isin_column: str = "isin") -> pd.DataFrame:
    """The frame without fund rows (ISIN prefix rule); no ISIN column means returned unchanged."""
    if isin_column not in frame.columns:
        return frame
    is_fund = (
        frame[isin_column]
        .astype(str)
        .str.strip()
        .str.upper()
        .str.startswith(settings.EXCLUDED_ISIN_PREFIXES)
    )
    return frame[~is_fund].reset_index(drop=True)
