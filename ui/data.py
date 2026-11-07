"""Data Access Layer for Streamlit UI
==================================
Provides Streamlit-cached access to market indicators with multi-factor
cache invalidation based on Parquet file modification time and metadata hash.
Supports lightweight snapshots and lazy per-symbol historical projections.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from config.settings import PROJECT_ROOT
from src.db.data_service import (
    load_indicator_history as _load_indicator_history,
)
from src.db.data_service import (
    load_indicator_snapshot as _load_indicator_snapshot,
)

logger = logging.getLogger(__name__)

DATA_PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
PARQUET_PATH = DATA_PROCESSED_DIR / "indicators.parquet"
META_PATH = DATA_PROCESSED_DIR / "indicators.meta.json"


def get_cache_invalidation_token(
    parquet_path: Path = PARQUET_PATH,
    meta_path: Path = META_PATH,
) -> tuple[float, str]:
    """Compute a lightweight cache invalidation token.

    Returns
    -------
    tuple[float, str]
        (parquet_mtime, meta_sha256)
    """
    mtime = 0.0
    if parquet_path.exists():
        try:
            mtime = parquet_path.stat().st_mtime
        except OSError:
            mtime = 0.0

    meta_hash = "missing"
    if meta_path.exists():
        try:
            content = meta_path.read_bytes()
            meta_hash = hashlib.sha256(content).hexdigest()
        except OSError:
            meta_hash = "error"

    return mtime, meta_hash


def _fetch_market_data() -> tuple[pd.DataFrame, pd.DataFrame, bool, str] | dict[str, Any]:
    """Internal fetcher delegating to data_service.load_indicator_snapshot."""
    return _load_indicator_snapshot(snapshot_only=True)


@st.cache_data(ttl=300)
def _get_snapshot_cached(cache_token: tuple[float, str]) -> dict[str, Any]:
    """Fetch only latest snapshot (1 row per symbol) with Streamlit caching."""
    _mtime, _hash = cache_token
    logger.debug("Streamlit cache miss / reload for market snapshot (token=%s)", cache_token)
    res = _fetch_market_data()
    if isinstance(res, dict):
        snapshot = res.get("snapshot", pd.DataFrame())
        is_live = bool(res.get("is_live", False))
        msg = str(res.get("status_msg") or res.get("message") or "")
    elif isinstance(res, tuple):
        snapshot, _full, is_live, msg = res
    else:
        snapshot, is_live, msg = pd.DataFrame(), False, ""

    return {
        "snapshot": snapshot,
        "is_live": is_live,
        "status_msg": msg,
        "message": msg,
    }


def get_snapshot(
    parquet_path: Path = PARQUET_PATH,
    meta_path: Path = META_PATH,
) -> dict[str, Any]:
    """Retrieve market snapshot using cached wrapper with automatic invalidation."""
    token = get_cache_invalidation_token(parquet_path=parquet_path, meta_path=meta_path)
    return _get_snapshot_cached(token)


@st.cache_data(ttl=300)
def _get_symbol_history_cached(
    symbol: str,
    columns: tuple[str, ...] | None,
    cache_token: tuple[float, str],
) -> pd.DataFrame:
    """Fetch history for a single symbol from Parquet using PyArrow filters."""
    _mtime, _hash = cache_token
    logger.debug(
        "Streamlit cache miss / reload for symbol history (%s, token=%s)",
        symbol,
        cache_token,
    )
    return _load_indicator_history(columns=columns, symbols=[symbol], cache_path=PARQUET_PATH)


def get_symbol_history(
    symbol: str,
    columns: Sequence[str] | None = None,
    parquet_path: Path = PARQUET_PATH,
    meta_path: Path = META_PATH,
) -> pd.DataFrame:
    """Retrieve history for one symbol with PyArrow projection and per-symbol Streamlit cache."""
    token = get_cache_invalidation_token(parquet_path=parquet_path, meta_path=meta_path)
    col_tuple = tuple(columns) if columns is not None else None
    return _get_symbol_history_cached(symbol, col_tuple, token)


def get_market_data(
    parquet_path: Path = PARQUET_PATH,
    meta_path: Path = META_PATH,
) -> dict[str, Any]:
    """Backward-compatible wrapper returning snapshot data."""
    snap_data = get_snapshot(parquet_path=parquet_path, meta_path=meta_path)
    return {
        "raw": snap_data["snapshot"],
        "indicators": snap_data["snapshot"],
        "snapshot": snap_data["snapshot"],
        "is_live": snap_data["is_live"],
        "status_msg": snap_data["status_msg"],
        "message": snap_data["status_msg"],
    }


def render_uninitialized_prompt(custom_message: str | None = None) -> None:
    """Render a friendly callout when the market database has not been initialized."""
    msg = custom_message or (
        "No market data is available in the database or cache. "
        "The application does not use synthetic demo data."
    )
    st.info(
        f"{msg}\n\n"
        "**To initialize the database, run these commands in your terminal:**\n"
        "```bash\n"
        "alembic upgrade head\n"
        "python run_pipeline.py --backfill --days-back 365\n"
        "```\n\n"
        "Alternatively, click below to trigger a background update:"
    )

    if "is_initializing_etl" not in st.session_state:
        st.session_state.is_initializing_etl = False

    if st.button("Initialise Data", disabled=st.session_state.is_initializing_etl):
        st.session_state.is_initializing_etl = True
        with st.spinner("Fetching initial market data..."):
            try:
                from src.etl.pipeline import BhavcopyETL

                etl = BhavcopyETL()
                res = etl.run_incremental()
                st.session_state.init_status = res
                st.cache_data.clear()
            except Exception as exc:
                st.session_state.init_status = {
                    "status": "error",
                    "message": f"Initialization failed: {exc}",
                }
            finally:
                st.session_state.is_initializing_etl = False
                st.rerun()
