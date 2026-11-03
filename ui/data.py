"""
Data Access Layer for Streamlit UI
==================================
Provides Streamlit-cached access to market indicators with multi-factor
cache invalidation based on Parquet file modification time and metadata hash.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

import streamlit as st

from config.settings import PROJECT_ROOT
from src.db.data_service import get_market_data as _fetch_market_data

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


@st.cache_data(ttl=300)
def _get_market_data_cached(cache_token: tuple[float, str]) -> dict[str, Any]:
    """Fetch market data from database/parquet snapshot with Streamlit caching."""
    _mtime, _hash = cache_token
    logger.debug("Streamlit cache miss / reload for market data (token=%s)", cache_token)
    return _fetch_market_data()


def get_market_data(
    parquet_path: Path = PARQUET_PATH,
    meta_path: Path = META_PATH,
) -> dict[str, Any]:
    """Retrieve market data snapshot using cached wrapper with automatic invalidation."""
    token = get_cache_invalidation_token(parquet_path=parquet_path, meta_path=meta_path)
    return _get_market_data_cached(token)


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
                st.rerun()
            except Exception as exc:
                st.error(f"Initialization failed: {exc}")
            finally:
                st.session_state.is_initializing_etl = False
