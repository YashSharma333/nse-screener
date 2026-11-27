"""
NSE Bhavcopy parser (task D3)
=============================
Parses official NSE Bhavcopy zip archives in both formats:
1. **UDiFF format** (effective 2024-01-01 onward)
2. **Old CSV format** (historical through 2024-07-05)

Produces a standardized DataFrame with columns:
  [date, symbol, series, isin, open, high, low, close, prev_close, volume]

Filters to regular trading series (REGULAR_SERIES: EQ, BE, BZ, SM, ST)
to guarantee a strict (symbol, date) unique key without secondary instruments
(BL block deals, debt securities, warrants). Never relabels a symbol.
"""

from __future__ import annotations

import io
import logging
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd

from config import settings
from config.settings import REGULAR_SERIES
from src.common.dates import parse_nse_date
from src.common.symbols import drop_funds
from src.ingestion.nse_files import BadFile, check_date, check_zip

logger = logging.getLogger(__name__)

# Standard column mappings for both official formats:
# UDiFF header names -> project column names
UDIFF_COLUMN_MAP: dict[str, str] = {
    "TradDt": "date",
    "TckrSymb": "symbol",
    "SctySrs": "series",
    "ISIN": "isin",
    "OpnPric": "open",
    "HghPric": "high",
    "LwPric": "low",
    "ClsPric": "close",
    "PrvsClsgPric": "prev_close",
    "TtlTradgVol": "volume",
}

# Old format header names -> project column names
OLD_COLUMN_MAP: dict[str, str] = {
    "TIMESTAMP": "date",
    "SYMBOL": "symbol",
    "SERIES": "series",
    "ISIN": "isin",
    "OPEN": "open",
    "HIGH": "high",
    "LOW": "low",
    "CLOSE": "close",
    "PREVCLOSE": "prev_close",
    "TOTTRDQTY": "volume",
}

# Final standardized output columns
FINAL_COLUMNS: list[str] = [
    "date",
    "symbol",
    "series",
    "isin",
    "open",
    "high",
    "low",
    "close",
    "prev_close",
    "volume",
]


# ISO ('2026-11-18', the UDiFF format) or 'DD-Mon-YYYY' (old format, '21-NOV-2016').
FILE_DATE_FORMATS = ("%Y-%m-%d", "%d-%b-%Y")


def _read_file_date(text: str) -> date:
    """Read a date written in a Bhavcopy file; a date nobody can read means a bad file."""
    return parse_nse_date(
        text, FILE_DATE_FORMATS, strict=True, dayfirst=False, error=BadFile, label="Date in file"
    )


def parse_bhavcopy(raw_zip_bytes: bytes, expected_date: date | str) -> pd.DataFrame:
    """Parse raw Bhavcopy zip bytes into a standardized pandas DataFrame.

    Parameters
    ----------
    raw_zip_bytes : bytes
        The raw bytes of the downloaded Bhavcopy zip file.
    expected_date : date or str
        The requested trading date; used to verify that NSE did not serve a different date.

    Returns
    -------
    pd.DataFrame
        DataFrame with columns [date, symbol, series, isin, open, high, low,
        close, prev_close, volume].
    """
    check_zip(raw_zip_bytes)

    # Convert expected_date to date object if passed as string
    exp_date: date = (
        expected_date if isinstance(expected_date, date) else _read_file_date(str(expected_date))
    )

    # 1. Unzip (one CSV inside), read with dtype=str
    try:
        with zipfile.ZipFile(io.BytesIO(raw_zip_bytes)) as zf:
            csv_names = [
                n
                for n in zf.namelist()
                if n.lower().endswith(".csv") and not n.startswith("__MACOSX")
            ]
            if not csv_names:
                raise BadFile("Zip archive does not contain any CSV file")
            with zf.open(csv_names[0]) as f:
                df = pd.read_csv(f, dtype=str)
    except zipfile.BadZipFile as exc:
        raise BadFile("Corrupt zip archive") from exc

    # Clean whitespace from column names and all cell values
    df.columns = df.columns.str.strip()
    for col in df.columns:
        df[col] = df[col].astype(str).str.strip()

    # Drop unnamed/trailing empty columns often present in old format files
    valid_cols = [
        c for c in df.columns if c and not c.startswith("Unnamed") and not c.startswith(" ")
    ]
    df = df[valid_cols]

    # 2. Detect format by header: "TckrSymb" -> UDiFF, "SYMBOL" -> Old format
    if "TckrSymb" in df.columns:
        col_map = UDIFF_COLUMN_MAP
    elif "SYMBOL" in df.columns:
        col_map = OLD_COLUMN_MAP
    else:
        raise BadFile(
            "Unrecognized Bhavcopy format; headers contain neither 'TckrSymb' nor 'SYMBOL': "
            f"{list(df.columns)}"
        )

    # Verify that all required source columns are present
    missing_cols = [c for c in col_map if c not in df.columns]
    if missing_cols:
        raise BadFile(f"Missing required columns in Bhavcopy CSV: {missing_cols}")

    # Rename to project standard column names
    df = df.rename(columns=col_map)

    # Symbols must be uppercase; never relabel a symbol (keep exactly as published)
    df["symbol"] = df["symbol"].str.upper()

    # 3. Date check: every row must have the same date; that date must equal expected_date
    raw_dates = df["date"].unique()
    if len(raw_dates) == 0:
        raise BadFile("Bhavcopy file contains 0 data rows")
    if len(raw_dates) > 1:
        raise BadFile(f"File contains mixed dates: {raw_dates.tolist()}")

    file_date = _read_file_date(str(raw_dates[0]))
    check_date(file_date, exp_date)

    # 4. Filter: keep only rows with series in REGULAR_SERIES
    df = df[df["series"].isin(REGULAR_SERIES)].copy()
    df = drop_funds(df)  # ETFs / fund units are not screened (ISIN starts with INF)

    # 5. Type conversions: numeric columns to float/int, date to datetime.date
    for price_col in ("open", "high", "low", "close", "prev_close"):
        df[price_col] = pd.to_numeric(df[price_col], errors="raise").astype(float).round(2)
    df["volume"] = pd.to_numeric(df["volume"], errors="raise").astype("int64")
    df["date"] = file_date

    # 6. Primary key check: if (symbol, date) repeats in regular series, raise BadFile
    if df.duplicated(subset=["symbol", "date"]).any():
        dupes = df[df.duplicated(subset=["symbol", "date"], keep=False)]["symbol"].unique().tolist()
        raise BadFile(f"Duplicate (symbol, date) key for regular series in bhavcopy: {dupes}")

    # 7. Return standard ordered columns
    return df[FINAL_COLUMNS].reset_index(drop=True)


def write_cache(
    df: pd.DataFrame,
    d: date | str,
    *,
    cache_dir: Path | None = None,
) -> Path:
    """Save parsed Bhavcopy DataFrame to Parquet cache.

    Writes to data/raw/nse/bhavcopy_all/<YYYY-MM-DD>.parquet.
    The date column is preserved as PyArrow date32.
    """
    if cache_dir is None:
        cache_dir = settings.BHAVCOPY_ALL_DIR
    target_date = d if isinstance(d, date) else _read_file_date(str(d))
    target_path = cache_dir / f"{target_date.isoformat()}.parquet"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(target_path, index=False, engine="pyarrow")
    return target_path
