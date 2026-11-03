# Official NSE Index Archive File Format

## Overview
NSE publishes daily closing values of all benchmark and sectoral indices in the archive file:
`https://nsearchives.nseindia.com/content/indices/ind_close_all_DDMMYYYY.csv`

## Verified Index Spellings
The following canonical index names are present in the `Index Name` column of official files:

| Raw Index Name (in CSV) | Canonical Stored Code | Description |
| :--- | :--- | :--- |
| `Nifty 50` | `NIFTY 50` | Flagship 50 large-cap bluechip equities |
| `Nifty 100` | `NIFTY 100` | Top 100 large-cap equities |
| `Nifty Midcap 150` | `NIFTY MIDCAP 150` | Midcap market segment |
| `Nifty Smallcap 250` | `NIFTY SMALLCAP 250` | Smallcap market segment |
| `Nifty 500` | `NIFTY 500` | Broad market index tracking top 500 listed equities |

## CSV Columns
The daily index file provides the following schema:
- `Index Name`
- `Index Date`
- `Open Index Value`
- `High Index Value`
- `Low Index Value`
- `Closing Index Value`
- `Points Change`
- `Change(%)`
- `Volume`
- `Turnover (Rs. Cr.)`
- `P/E`
- `P/B`
- `Div Yield`
