# NSE Bhavcopy Raw Columns Specification

This document details the exact structure and column names of raw NSE Bhavcopy files discovered from live NSE archive feeds.

## 1. New Format (2023 – Present)
- **URL Pattern**: `https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv`
- **Format**: Plain CSV, comma-separated
- **Observed Date**: 25-Oct-2024

### Columns
| Column Name | Description | Stored in DB | Renamed To |
|-------------|-------------|--------------|------------|
| `SYMBOL` | NSE stock ticker symbol | Yes | `Symbol` / `symbol` |
| `SERIES` | Market series (`EQ` for equity) | Filter (`EQ`) | Dropped |
| `DATE1` | Trading date | Filter/Timestamp | Dropped |
| `PREV_CLOSE` | Previous trading session close | Yes | `PrevClose` / `prev_close` |
| `OPEN_PRICE` | Open price | Yes | `Open` / `open` |
| `HIGH_PRICE` | Day high price | Yes | `High` / `high` |
| `LOW_PRICE` | Day low price | Yes | `Low` / `low` |
| `LAST_PRICE` | Last traded price | No | Dropped |
| `CLOSE_PRICE` | Official closing price | Yes | `Close` / `close` |
| `AVG_PRICE` | Volume-weighted average price | No | Dropped |
| `TTL_TRD_QNTY` | Total traded quantity (shares) | Yes | `Volume` / `volume` |
| `TURNOVER_LACS` | Turnover in Lakhs (INR) | No | Dropped |
| `NO_OF_TRADES` | Number of trades executed | No | Dropped |
| `DELIV_QTY` | Delivery quantity | No | Dropped |
| `DELIV_PER` | Delivery percentage | No | Dropped |

### Example Row
```csv
1018GS2026, GS, 25-Oct-2024, 109.00, 109.00, 109.00, 107.00, 108.94, 108.94, 107.90, 3030, 3.27, 28, 3020, 99.67
RELIANCE, EQ, 25-Oct-2024, 2680.00, 2682.00, 2715.00, 2670.00, 2695.00, 2697.50, 2692.10, 5214000, 140365.12, 192000, 2854000, 54.74
```

---

## 2. Historical Zip Format (Archives prior to 2023)
- **URL Pattern**: `https://nsearchives.nseindia.com/content/historical/EQUITIES/YYYY/MON/cmDDMONYYYYbhav.csv.zip`
- **Format**: ZIP archive containing `cmDDMONYYYYbhav.csv`
- **Observed Date**: 25-Oct-2018

### Columns
| Column Name | Description | Stored in DB | Renamed To |
|-------------|-------------|--------------|------------|
| `SYMBOL` | NSE stock ticker symbol | Yes | `Symbol` / `symbol` |
| `SERIES` | Market series (`EQ` for equity) | Filter (`EQ`) | Dropped |
| `OPEN` | Day open price | Yes | `Open` / `open` |
| `HIGH` | Day high price | Yes | `High` / `high` |
| `LOW` | Day low price | Yes | `Low` / `low` |
| `CLOSE` | Official closing price | Yes | `Close` / `close` |
| `LAST` | Last traded price | No | Dropped |
| `PREVCLOSE` | Previous trading session close | Yes | `PrevClose` / `prev_close` |
| `TOTTRDQTY` | Total traded quantity | Yes | `Volume` / `volume` |
| `TOTTRDVAL` | Total traded value | No | Dropped |
| `TIMESTAMP` | Trading date string | No | Dropped |
| `TOTALTRADES` | Number of trades | No | Dropped |
| `ISIN` | International Securities ID | No | Dropped |

### Example Row
```csv
20MICRONS,EQ,39,39.95,38.65,39.1,39.15,39.8,23830,930979.3,25-OCT-2018,271,INE144J01027,
```
