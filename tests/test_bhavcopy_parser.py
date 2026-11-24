"""
Unit tests for src/ingestion/bhavcopy_parser.py (task D3)
=========================================================
Tests run strictly offline with in-memory zip fixtures and tmp_path.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.ingestion.bhavcopy_parser import (
    FINAL_COLUMNS,
    parse_bhavcopy,
    write_cache,
)
from src.ingestion.nse_files import BadFile

# ---------------------------------------------------------------------------
# Test fixtures and zip builders
# ---------------------------------------------------------------------------


def make_zip(filename: str, csv_content: str) -> bytes:
    """Create in-memory zip bytes containing a single CSV file."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(filename, csv_content)
    return buf.getvalue()


def make_udiff_zip(
    records: list[dict[str, Any]],
    filename: str = "BhavCopy_NSE_CM_0_0_0_20240101_F_0000.csv",
) -> bytes:
    """Create UDiFF Bhavcopy zip bytes from record dictionaries."""
    df = pd.DataFrame(records)
    csv_text = df.to_csv(index=False)
    return make_zip(filename, csv_text)


def make_old_zip(
    records: list[dict[str, Any]],
    trailing_comma: bool = True,
    filename: str = "cm21NOV2016bhav.csv",
) -> bytes:
    """Create historical Old Bhavcopy zip bytes with trailing comma column."""
    df = pd.DataFrame(records)
    csv_text = df.to_csv(index=False)
    if trailing_comma:
        lines = csv_text.splitlines()
        csv_text = "\n".join(line + "," for line in lines) + "\n"
    return make_zip(filename, csv_text)


# ---------------------------------------------------------------------------
# Standard test records
# ---------------------------------------------------------------------------

UDIFF_RECORDS_2024_01_01: list[dict[str, Any]] = [
    {
        "TradDt": "2024-01-01",
        "BizDt": "2024-01-01",
        "Sgmt": "CM",
        "Src": "NSE",
        "FinInstrmTp": "STK",
        "FinInstrmId": "1",
        "ISIN": "INE009A01021",
        "TckrSymb": "INFY",
        "SctySrs": "EQ",
        "OpnPric": "1500.00",
        "HghPric": "1520.00",
        "LwPric": "1490.00",
        "ClsPric": "1510.50",
        "LastPric": "1510.00",
        "PrvsClsgPric": "1495.00",
        "TtlTradgVol": "1000000",
    },
    {
        "TradDt": "2024-01-01",
        "BizDt": "2024-01-01",
        "Sgmt": "CM",
        "Src": "NSE",
        "FinInstrmTp": "STK",
        "FinInstrmId": "2",
        "ISIN": "INE002A01018",
        "TckrSymb": "RELIANCE",
        "SctySrs": "BE",
        "OpnPric": "2500.00",
        "HghPric": "2550.00",
        "LwPric": "2480.00",
        "ClsPric": "2520.00",
        "LastPric": "2520.00",
        "PrvsClsgPric": "2500.00",
        "TtlTradgVol": "500000",
    },
    {
        "TradDt": "2024-01-01",
        "BizDt": "2024-01-01",
        "Sgmt": "CM",
        "Src": "NSE",
        "FinInstrmTp": "BND",
        "FinInstrmId": "3",
        "ISIN": "INE733E07KA7",
        "TckrSymb": "NTPCN3",
        "SctySrs": "N3",
        "OpnPric": "100.00",
        "HghPric": "105.00",
        "LwPric": "99.00",
        "ClsPric": "102.00",
        "LastPric": "102.00",
        "PrvsClsgPric": "100.00",
        "TtlTradgVol": "1000",
    },
]

OLD_RECORDS_2016_11_21: list[dict[str, Any]] = [
    {
        "SYMBOL": "INFY",
        "SERIES": "EQ",
        "OPEN": "1500.00",
        "HIGH": "1520.00",
        "LOW": "1490.00",
        "CLOSE": "1510.50",
        "LAST": "1510.00",
        "PREVCLOSE": "1495.00",
        "TOTTRDQTY": "1000000",
        "TOTTRDVAL": "1500000000",
        "TIMESTAMP": "21-NOV-2016",
        "TOTALTRADES": "50000",
        "ISIN": "INE009A01021",
    },
    {
        "SYMBOL": "RELIANCE",
        "SERIES": "BE",
        "OPEN": "2500.00",
        "HIGH": "2550.00",
        "LOW": "2480.00",
        "CLOSE": "2520.00",
        "LAST": "2520.00",
        "PREVCLOSE": "2500.00",
        "TOTTRDQTY": "500000",
        "TOTTRDVAL": "1250000000",
        "TIMESTAMP": "21-NOV-2016",
        "TOTALTRADES": "30000",
        "ISIN": "INE002A01018",
    },
    {
        "SYMBOL": "NTPCN3",
        "SERIES": "N3",
        "OPEN": "100.00",
        "HIGH": "105.00",
        "LOW": "99.00",
        "CLOSE": "102.00",
        "LAST": "102.00",
        "PREVCLOSE": "100.00",
        "TOTTRDQTY": "1000",
        "TOTTRDVAL": "100000",
        "TIMESTAMP": "21-NOV-2016",
        "TOTALTRADES": "100",
        "ISIN": "INE733E07KA7",
    },
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_udiff_basic():
    """UDiFF format: 3 rows (EQ, BE, N3 debenture) -> 2 rows, exact column order."""
    raw_zip = make_udiff_zip(UDIFF_RECORDS_2024_01_01)
    df = parse_bhavcopy(raw_zip, date(2024, 1, 1))

    assert list(df.columns) == FINAL_COLUMNS
    assert len(df) == 2
    assert list(df["symbol"]) == ["INFY", "RELIANCE"]
    assert list(df["series"]) == ["EQ", "BE"]
    assert df["open"].tolist() == [1500.00, 2500.00]
    assert df["high"].tolist() == [1520.00, 2550.00]
    assert df["low"].tolist() == [1490.00, 2480.00]
    assert df["close"].tolist() == [1510.50, 2520.00]
    assert df["prev_close"].tolist() == [1495.00, 2500.00]
    assert df["volume"].tolist() == [1000000, 500000]
    assert df["date"].iloc[0] == date(2024, 1, 1)


def test_old_format_basic():
    """Old format: old headers, trailing comma column, date 21-NOV-2016."""
    raw_zip = make_old_zip(OLD_RECORDS_2016_11_21)
    df = parse_bhavcopy(raw_zip, date(2016, 11, 21))

    assert list(df.columns) == FINAL_COLUMNS
    assert len(df) == 2
    assert list(df["symbol"]) == ["INFY", "RELIANCE"]
    assert list(df["series"]) == ["EQ", "BE"]
    assert df["close"].tolist() == [1510.50, 2520.00]
    assert df["volume"].tolist() == [1000000, 500000]
    assert df["date"].iloc[0] == date(2016, 11, 21)


def test_both_formats_give_identical_frame():
    """Identical market data in UDiFF and Old formats parses to equal DataFrames."""
    target_date = date(2024, 1, 2)

    udiff_records = [
        {
            "TradDt": "2024-01-02",
            "TckrSymb": "INFY",
            "SctySrs": "EQ",
            "ISIN": "INE009A01021",
            "OpnPric": "1500.00",
            "HghPric": "1520.00",
            "LwPric": "1490.00",
            "ClsPric": "1510.50",
            "PrvsClsgPric": "1495.00",
            "TtlTradgVol": "1000000",
        },
        {
            "TradDt": "2024-01-02",
            "TckrSymb": "RELIANCE",
            "SctySrs": "BE",
            "ISIN": "INE002A01018",
            "OpnPric": "2500.00",
            "HghPric": "2550.00",
            "LwPric": "2480.00",
            "ClsPric": "2520.00",
            "PrvsClsgPric": "2500.00",
            "TtlTradgVol": "500000",
        },
    ]

    old_records = [
        {
            "SYMBOL": "INFY",
            "SERIES": "EQ",
            "OPEN": "1500.00",
            "HIGH": "1520.00",
            "LOW": "1490.00",
            "CLOSE": "1510.50",
            "PREVCLOSE": "1495.00",
            "TOTTRDQTY": "1000000",
            "TIMESTAMP": "02-JAN-2024",
            "ISIN": "INE009A01021",
        },
        {
            "SYMBOL": "RELIANCE",
            "SERIES": "BE",
            "OPEN": "2500.00",
            "HIGH": "2550.00",
            "LOW": "2480.00",
            "CLOSE": "2520.00",
            "PREVCLOSE": "2500.00",
            "TOTTRDQTY": "500000",
            "TIMESTAMP": "02-JAN-2024",
            "ISIN": "INE002A01018",
        },
    ]

    df_udiff = parse_bhavcopy(make_udiff_zip(udiff_records), target_date)
    df_old = parse_bhavcopy(make_old_zip(old_records), target_date)

    pd.testing.assert_frame_equal(df_udiff, df_old)


def test_bl_series_dropped_so_key_unique():
    """ALKEM EQ and ALKEM BL block deal on the same day keeps only the EQ row."""
    records = [
        {
            "TradDt": "2024-01-01",
            "TckrSymb": "ALKEM",
            "SctySrs": "EQ",
            "ISIN": "INE540L01014",
            "OpnPric": "3000.00",
            "HghPric": "3050.00",
            "LwPric": "2980.00",
            "ClsPric": "3020.00",
            "PrvsClsgPric": "3000.00",
            "TtlTradgVol": "200000",
        },
        {
            "TradDt": "2024-01-01",
            "TckrSymb": "ALKEM",
            "SctySrs": "BL",
            "ISIN": "INE540L01014",
            "OpnPric": "3010.00",
            "HghPric": "3010.00",
            "LwPric": "3010.00",
            "ClsPric": "3010.00",
            "PrvsClsgPric": "3000.00",
            "TtlTradgVol": "50000",
        },
    ]
    raw_zip = make_udiff_zip(records)
    df = parse_bhavcopy(raw_zip, date(2024, 1, 1))

    assert len(df) == 1
    assert df.iloc[0]["symbol"] == "ALKEM"
    assert df.iloc[0]["series"] == "EQ"


def test_duplicate_regular_series_raises():
    """Two EQ rows for the same symbol raises BadFile."""
    records = [
        {
            "TradDt": "2024-01-01",
            "TckrSymb": "ALKEM",
            "SctySrs": "EQ",
            "ISIN": "INE540L01014",
            "OpnPric": "3000.00",
            "HghPric": "3050.00",
            "LwPric": "2980.00",
            "ClsPric": "3020.00",
            "PrvsClsgPric": "3000.00",
            "TtlTradgVol": "200000",
        },
        {
            "TradDt": "2024-01-01",
            "TckrSymb": "ALKEM",
            "SctySrs": "EQ",
            "ISIN": "INE540L01014",
            "OpnPric": "3000.00",
            "HghPric": "3050.00",
            "LwPric": "2980.00",
            "ClsPric": "3020.00",
            "PrvsClsgPric": "3000.00",
            "TtlTradgVol": "100000",
        },
    ]
    raw_zip = make_udiff_zip(records)
    with pytest.raises(BadFile) as exc_info:
        parse_bhavcopy(raw_zip, date(2024, 1, 1))
    assert "Duplicate (symbol, date) key" in str(exc_info.value)


def test_wrong_date_in_file_raises():
    """File date 2025-08-14 with expected 2025-08-15 raises BadFile."""
    records = [
        {
            "TradDt": "2025-08-14",
            "TckrSymb": "INFY",
            "SctySrs": "EQ",
            "ISIN": "INE009A01021",
            "OpnPric": "1500.00",
            "HghPric": "1520.00",
            "LwPric": "1490.00",
            "ClsPric": "1510.50",
            "PrvsClsgPric": "1495.00",
            "TtlTradgVol": "1000000",
        }
    ]
    raw_zip = make_udiff_zip(records, filename="BhavCopy_NSE_CM_0_0_0_20250814_F_0000.csv")
    with pytest.raises(BadFile) as exc_info:
        parse_bhavcopy(raw_zip, date(2025, 8, 15))
    assert "does not match expected date" in str(exc_info.value)


def test_mixed_dates_in_file_raises():
    """Rows with conflicting dates in one file raise BadFile."""
    records = [
        {
            "TradDt": "2024-01-01",
            "TckrSymb": "INFY",
            "SctySrs": "EQ",
            "ISIN": "INE009A01021",
            "OpnPric": "1500.00",
            "HghPric": "1520.00",
            "LwPric": "1490.00",
            "ClsPric": "1510.50",
            "PrvsClsgPric": "1495.00",
            "TtlTradgVol": "1000000",
        },
        {
            "TradDt": "2024-01-02",
            "TckrSymb": "RELIANCE",
            "SctySrs": "EQ",
            "ISIN": "INE002A01018",
            "OpnPric": "2500.00",
            "HghPric": "2550.00",
            "LwPric": "2480.00",
            "ClsPric": "2520.00",
            "PrvsClsgPric": "2500.00",
            "TtlTradgVol": "500000",
        },
    ]
    raw_zip = make_udiff_zip(records)
    with pytest.raises(BadFile) as exc_info:
        parse_bhavcopy(raw_zip, date(2024, 1, 1))
    assert "mixed dates" in str(exc_info.value)


def test_symbol_not_relabelled():
    """TATAMOTORS remains TATAMOTORS without any relabeling."""
    records = [
        {
            "TradDt": "2024-01-01",
            "TckrSymb": "TATAMOTORS",
            "SctySrs": "EQ",
            "ISIN": "INE155A01022",
            "OpnPric": "750.00",
            "HghPric": "760.00",
            "LwPric": "745.00",
            "ClsPric": "755.00",
            "PrvsClsgPric": "750.00",
            "TtlTradgVol": "2000000",
        }
    ]
    raw_zip = make_udiff_zip(records)
    df = parse_bhavcopy(raw_zip, date(2024, 1, 1))
    assert df.iloc[0]["symbol"] == "TATAMOTORS"


def test_prev_close_and_isin_present():
    """Both prev_close and isin are present and correctly populated."""
    records = [
        {
            "TradDt": "2024-01-01",
            "TckrSymb": "INFY",
            "SctySrs": "EQ",
            "ISIN": "INE009A01021",
            "OpnPric": "1500.00",
            "HghPric": "1520.00",
            "LwPric": "1490.00",
            "ClsPric": "1510.50",
            "PrvsClsgPric": "1495.25",
            "TtlTradgVol": "1000000",
        }
    ]
    raw_zip = make_udiff_zip(records)
    df = parse_bhavcopy(raw_zip, date(2024, 1, 1))

    assert "prev_close" in df.columns
    assert "isin" in df.columns
    assert df.iloc[0]["prev_close"] == 1495.25
    assert df.iloc[0]["isin"] == "INE009A01021"


def test_parquet_roundtrip_keeps_date32(tmp_path: Path):
    """Writing to parquet cache preserves date column as Arrow date32."""
    raw_zip = make_udiff_zip(UDIFF_RECORDS_2024_01_01)
    df = parse_bhavcopy(raw_zip, date(2024, 1, 1))

    target_path = write_cache(df, date(2024, 1, 1), cache_dir=tmp_path)
    assert target_path.exists()
    assert target_path.name == "2024-01-01.parquet"

    # Verify PyArrow schema has date32
    table = pq.read_table(target_path)
    assert table.schema.field("date").type == pa.date32()

    # Read back into DataFrame
    roundtrip_df = pd.read_parquet(target_path)
    assert isinstance(roundtrip_df["date"].iloc[0], date)
    pd.testing.assert_frame_equal(df, roundtrip_df)
