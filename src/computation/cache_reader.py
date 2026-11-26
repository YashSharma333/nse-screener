"""Read-only access to the indicator cache (``indicators.parquet`` + ``indicators.meta.json``).

Why this module exists (CL-06): the dashboard must never write data. It used to call the cache
builder in ``src/db/data_service.py``, so a page load could query MySQL, recompute 1.5 million
rows and overwrite the cache. This file only READS. It imports pandas, pyarrow and settings and
nothing else: no ``src.db``, no ``src.ingestion``, no ``src.etl``. The cache is built only by
``python run_pipeline.py --update`` or ``--refresh-cache``.

Every function reads the cache path from ``settings`` when it runs (never at import time), so
the tests can point it at a temporary folder.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from config import settings

logger = logging.getLogger(__name__)

NO_CACHE_MESSAGE = "No indicator cache found. Run: python run_pipeline.py --update"


def _meta_path(cache_path: Path) -> Path:
    """The sidecar file always sits next to the cache with the suffix ``.meta.json``."""
    return cache_path.with_suffix(".meta.json")


def read_meta() -> dict[str, Any]:
    """Return the cache's metadata (schema version, ``db_max_date``, ...); ``{}`` if unreadable."""
    meta_path = _meta_path(settings.INDICATORS_CACHE_PATH)
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read %s: %s", meta_path, exc)
        return {}


def read_data_date() -> str | None:
    """The trading day the cache was built for (``db_max_date`` in the metadata), or None."""
    return read_meta().get("db_max_date")


def date_literal(schema: Any, value: Any) -> Any:
    """Convert ``value`` to an Arrow scalar matching the type of the cache's ``date`` column.

    KEEP: Arrow does not compare ``date32`` columns with strings, so filters must use a value of
    the same type as the stored column (``date32`` normally; timestamp or string for older
    caches). Comparing with a plain string used to raise on every page load.
    """
    day = pd.Timestamp(value).date()
    field_type = schema.field("date").type
    if pa.types.is_date(field_type):
        return pa.scalar(day, type=field_type)
    if pa.types.is_timestamp(field_type):
        return pa.scalar(pd.Timestamp(day), type=field_type)
    return pa.scalar(day.isoformat(), type=pa.string())


def fill_missing_market_cap(snapshot: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    """Fill empty ``market_cap`` in the latest rows with each symbol's last known value.

    Why: NSE's market cap arrives a few days after the price, so the newest session has none.
    ``history`` needs ``symbol``, ``date`` and ``market_cap`` columns; the snapshot is returned
    unchanged if they are missing.
    """
    needed = {"symbol", "date", "market_cap"}
    if snapshot.empty or not needed.issubset(snapshot.columns) or not needed.issubset(history):
        return snapshot
    known = history.dropna(subset=["market_cap"]).sort_values("date")
    last_known = known.groupby("symbol")["market_cap"].last()
    filled = snapshot.copy()
    filled["market_cap"] = filled["market_cap"].fillna(filled["symbol"].map(last_known))
    return filled


def _latest_day(cache_path: Path) -> Any:
    """The day to show: the metadata's date, or the newest date inside the file."""
    from_meta = read_data_date()
    if from_meta:
        return from_meta
    return pq.read_table(cache_path, columns=["date"])["date"].to_pandas().max()


def read_snapshot() -> tuple[pd.DataFrame, str]:
    """Latest session, one row per symbol, plus a short message for the page.

    A missing or unreadable cache gives an empty table and a message that says what to run.
    Nothing is ever written or rebuilt here.
    """
    cache_path = settings.INDICATORS_CACHE_PATH
    if not cache_path.exists():
        return pd.DataFrame(), NO_CACHE_MESSAGE

    try:
        day = _latest_day(cache_path)
        dataset = ds.dataset(cache_path, format="parquet")
        table = dataset.to_table(filter=ds.field("date") == date_literal(dataset.schema, day))
        snapshot = table.to_pandas()
        if not snapshot.empty:
            snapshot["date"] = pd.to_datetime(snapshot["date"]).dt.date
        if "market_cap" in snapshot.columns and snapshot["market_cap"].isna().any():
            # Only 3 narrow columns are read, so this stays light on memory.
            mcap_history = pq.read_table(cache_path, columns=["symbol", "date", "market_cap"])
            snapshot = fill_missing_market_cap(snapshot, mcap_history.to_pandas())
    except (OSError, pa.ArrowException) as exc:
        logger.warning("Could not read the indicator cache %s: %s", cache_path, exc)
        return pd.DataFrame(), f"The indicator cache is unreadable. {NO_CACHE_MESSAGE}"

    return snapshot, f"Parquet cache: {len(snapshot)} symbols (as of {day})"


def read_history(symbols: Sequence[str], columns: Sequence[str] | None = None) -> pd.DataFrame:
    """Rows of the given symbols (all cached dates), reading only the columns asked for.

    PyArrow applies the symbol filter and the column projection while reading, so the whole
    cache never has to fit in memory (16 GB machine). Empty if the cache is missing.
    """
    cache_path = settings.INDICATORS_CACHE_PATH
    if not cache_path.exists():
        return pd.DataFrame()

    projection = None
    if columns is not None:
        projection = list(columns)
        # Callers always need to know which stock and which day a row belongs to.
        for position, required in enumerate(("symbol", "date")):
            if required not in projection:
                projection.insert(position, required)

    try:
        dataset = ds.dataset(cache_path, format="parquet")
        table = dataset.to_table(columns=projection, filter=ds.field("symbol").isin(list(symbols)))
    except (OSError, pa.ArrowException) as exc:
        logger.warning("Could not read the indicator cache %s: %s", cache_path, exc)
        return pd.DataFrame()

    history = table.to_pandas()
    if not history.empty:
        history["date"] = pd.to_datetime(history["date"]).dt.date
    return history


__all__ = [
    "NO_CACHE_MESSAGE",
    "date_literal",
    "fill_missing_market_cap",
    "read_data_date",
    "read_history",
    "read_meta",
    "read_snapshot",
]
