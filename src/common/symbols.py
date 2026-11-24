"""The one place that cleans a stock symbol."""

from __future__ import annotations


def normalise_symbol(value: object) -> str:
    """Trim spaces and upper-case a symbol, e.g. ``' tcs '`` -> ``'TCS'``.

    Why: NSE files and our own tables must agree on spelling, or a join silently misses rows.
    This only fixes case and spaces. It never renames a symbol (renames live in
    ``symbol_changes``).
    """
    return str(value).strip().upper()
