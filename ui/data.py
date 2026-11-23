"""Data access layer for the Streamlit UI (READ ONLY).

The UI only reads the Parquet cache through ``src/computation/cache_reader.py``. It never
opens MySQL, never downloads anything and never rebuilds the cache: ``python run_pipeline.py
--update`` (or ``--refresh-cache``) does that, and the dashboard shows the result.

Streamlit caching is invalidated by a token made of the Parquet file's modification time and a
hash of its metadata file, so new data appears as soon as the file changes.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from typing import Any

import pandas as pd
import streamlit as st

from config import settings
from src.computation import cache_reader

logger = logging.getLogger(__name__)


def get_cache_invalidation_token() -> tuple[float, str]:
    """Compute a lightweight cache invalidation token: (parquet_mtime, meta_sha256)."""
    cache_path = settings.INDICATORS_CACHE_PATH
    meta_path = cache_path.with_suffix(".meta.json")

    mtime = 0.0
    if cache_path.exists():
        try:
            mtime = cache_path.stat().st_mtime
        except OSError:
            mtime = 0.0

    meta_hash = "missing"
    if meta_path.exists():
        try:
            meta_hash = hashlib.sha256(meta_path.read_bytes()).hexdigest()
        except OSError:
            meta_hash = "error"

    return mtime, meta_hash


@st.cache_data(ttl=300)
def _get_snapshot_cached(cache_token: tuple[float, str]) -> dict[str, Any]:
    """Read the latest snapshot (1 row per symbol) from the cache, remembered by Streamlit."""
    logger.debug("Streamlit cache miss / reload for market snapshot (token=%s)", cache_token)
    snapshot, message = cache_reader.read_snapshot()

    if not snapshot.empty:
        # Task D11: UI shows market cap, no cap bucket
        drop_cols = [c for c in ("cap_bucket", "index_name") if c in snapshot.columns]
        snapshot = snapshot.drop(columns=drop_cols)

    return {
        "snapshot": snapshot,
        "as_of": cache_reader.read_data_date(),
        "message": message,
    }


def get_snapshot() -> dict[str, Any]:
    """Latest market snapshot as ``{"snapshot": DataFrame, "as_of": str | None, "message": str}``.

    ``as_of`` is the trading day written in ``indicators.meta.json`` (None without a cache).
    """
    result = _get_snapshot_cached(get_cache_invalidation_token())
    if result["snapshot"].empty:
        # Never keep an empty result for the 5 minute TTL: while ``--update`` is rewriting the
        # file the page would otherwise stay empty until the cache expires.
        _get_snapshot_cached.clear()
    return result


@st.cache_data(ttl=300)
def _get_symbol_history_cached(
    symbol: str,
    columns: tuple[str, ...] | None,
    cache_token: tuple[float, str],
) -> pd.DataFrame:
    """Read the history of one symbol from the Parquet cache, remembered by Streamlit."""
    # cache_token is only here so Streamlit forgets the result when the cache file changes.
    logger.debug("Streamlit cache miss / reload for symbol history (%s, %s)", symbol, cache_token)
    return cache_reader.read_history([symbol], columns=columns)


def get_symbol_history(symbol: str, columns: Sequence[str] | None = None) -> pd.DataFrame:
    """History for one symbol with PyArrow projection and a per-symbol Streamlit cache."""
    col_tuple = tuple(columns) if columns is not None else None
    return _get_symbol_history_cached(symbol, col_tuple, get_cache_invalidation_token())


def format_market_cap(val: Any) -> str:
    """Format market cap in rupees to crore string (e.g. ₹1,234.56 Cr).

    Missing values (None, NaN, or non-positive) return empty string (blank, never a guess).
    """
    if val is None or pd.isna(val):
        return ""
    try:
        num = float(val)
        if num <= 0:
            return ""
        crores = num / 1e7
        return f"₹{crores:,.2f} Cr"
    except (ValueError, TypeError):
        return ""


def filter_by_market_cap(
    df: pd.DataFrame,
    min_mcap_rupees: float | None = None,
    max_mcap_rupees: float | None = None,
    include_missing: bool = True,
) -> pd.DataFrame:
    """Filter DataFrame by market cap in rupees.

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame containing 'market_cap'.
    min_mcap_rupees : float, optional
        Minimum market cap in rupees.
    max_mcap_rupees : float, optional
        Maximum market cap in rupees.
    include_missing : bool, default True
        Whether to keep stocks whose market cap is missing (e.g. before 2024-02-01).

    Returns
    -------
    pd.DataFrame
        Filtered DataFrame.
    """
    if df.empty or "market_cap" not in df.columns:
        return df

    mask = pd.Series(True, index=df.index)
    is_missing = df["market_cap"].isna()

    if min_mcap_rupees is not None:
        mask &= (df["market_cap"] >= min_mcap_rupees) | (is_missing if include_missing else False)
    if max_mcap_rupees is not None:
        mask &= (df["market_cap"] <= max_mcap_rupees) | (is_missing if include_missing else False)

    return df[mask]


def render_uninitialized_prompt(message: str | None = None) -> None:
    """Tell the user there is no data and which terminal commands create it (no buttons)."""
    st.info(
        f"{message or cache_reader.NO_CACHE_MESSAGE}\n\n"
        "**To load the data, run these commands in your terminal:**\n"
        "```bash\n"
        "alembic upgrade head\n"
        "python run_pipeline.py --load-prices   # first time only: all history, takes long\n"
        "python run_pipeline.py --update\n"
        "```\n\n"
        "See the README section 'Building the database from scratch' for the full order. "
        "The dashboard only reads the saved data; it never loads or rebuilds anything itself."
    )
