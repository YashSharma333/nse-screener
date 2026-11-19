# Data spike D1: what the official NSE files really contain (read-only, 19 Nov 2026)

Question: can the data layer use only official NSE files, with no guessing? Nothing was written
to the database or the repo cache. About 45 official files were downloaded once into a scratch
folder (33 bhavcopy days, 7 PR bundles, `EQUITY_L.csv`, `symbolchange.csv`, `eq_etfseclist.csv`,
`delisted.csv`) from `nsearchives.nseindia.com` with only a browser User-Agent (no cookies).
Scripts were throwaway. This file records the results so the next tasks (D2 to D12) rest on facts.

Sample: UDiFF bhavcopy for 16 dates (2024-01-01 to 2026-11-17, including the cut-over, a Sunday
Muhurat session and Saturday special sessions), old-format bhavcopy for 17 dates (2016-11-21 to
2024-07-05, including HDFC's last day and a 2019 Sunday session), PR bundles for 2024-01-02 to
2024-02-01 and 2026-11-17.

## Answers

| Question | Result |
|---|---|
| Does the date inside each file equal the requested date? | Yes, in all 33 files, including 10 weekend special sessions |
| What does NSE return on a real holiday or Sunday? | HTTP 404 (an HTML page, 3,814 bytes) for UDiFF on 2025-08-15 and Sunday 2025-01-26. The old format returns 404 after 2024-07-05. (A previous-day file was reported by others for `sec_bhavdata_full`, which we do not use.) The date check stays anyway |
| Do the two formats agree? | Yes. On 2024-01-02, 2024-03-15 and 2024-07-05 all 1,815, 1,798 and 1,906 EQ rows match in open, high, low, close, prev close and volume (0 differences). Switching format on 2024-01-01 causes no break |
| Is PrevClose present? | Yes, in both formats, every day |
| ISIN present? | Yes, in both formats (0 blanks). Not needed as a row key (see renames) |
| Which series exist? | Many: EQ, BE, BZ, BL, SM, ST, GB, GS, IV, E1, N1 to N9 and more. About 2,440 to 3,756 rows per day (EQ about 1,500 in 2016, about 2,760 in 2026) |
| One row per symbol per day? | **Not across all series.** Up to 40 tickers a day (none on some days) appear several times: `BL` (block-deal session, for example ALKEM BL 5,050 beside EQ 5,175), `N*` (debentures of the same company, for example BRITANNIA N3 at 29.96), `P1` (preference). **Among the regular trading series EQ, BE, BZ, SM, ST there were 0 duplicates in 33 files**, so (symbol, date) stays unique if only those five are kept |
| What is in `EQUITY_L.csv`? | 2,632 rows: EQ 2,405, BE 193, BZ 34. No ETFs (the 361 EQ tickers in the latest bhavcopy that are missing from it equal the 361 rows of `eq_etfseclist.csv`) and no SME (SM and ST). All ISINs start INE except 2 (IN9). 9 listed symbols did not trade in the latest file |
| First usable market-cap date | **2024-02-01** (`MCAP01022024.csv`). The bundles of 2024-01-02, 25, 29, 30 and 31 have none |
| Market-cap file content | Trade date, symbol, series, name, category, last trade date, face value, issue size, close, market cap in rupees. Covers 2,630 of the 2,632 `EQUITY_L` symbols. Market cap equals issue size times close within 0.1% for 2,902 of 3,113 matched rows. The close in it differs from the bhavcopy close on 580 rows (cause not found), so market cap is stored as published, never recomputed |
| `symbolchange.csv` | 1,081 rows, from 1999. No old symbol is used twice (0 reuse). **148 rows are chains** (the new symbol is itself renamed later), so chains must be resolved in date order. Some rows are bonds (blank company name) |
| Our holiday detection | Self-consistent: 2,478 cached trading dates, 1,171 recorded non-trading days, 0 dates in both, 0 weekdays in neither. Not compared with an external list (owner decision) |

## Events in real files (one example each, not a survey)

- **Rename keeps the ISIN:** TATAGLOBAL (2019-12-31) and TATACONSUM (2020-03-23) both INE192A01025. TATAMOTORS (2025-10-23) and TMPV (2025-10-24) both INE155A01022.
- **A split changes the ISIN:** COFORGE INE591G01017 (2023-12-29) became INE591G01025 (2026-11-17). So ISIN cannot join a stock's history across splits; renames come from `symbolchange.csv`, not from ISIN.
- **Merger:** HDFC (EQ) traded on 2023-07-12 and is absent on 2023-07-13. HDFCBANK continued with the same ISIN. The merged-away symbol simply disappears, so the 3-day rule fits.
- **Demerger:** the parent was renamed TMPV on 2025-10-24 with its ISIN; the demerged business listed on 2025-11-12 as **TMCV**, ISIN INE1TAE01010, and `EQUITY_L` names it "Tata Motors Limited". Company names are therefore unsafe as keys; the symbol (and listing date) is the key. TMPV's close fell from 403 (2025-10-24) to 259 (2026-11-17) around the demerger, so the parent's history needs an official ratio or an owner override.

## What this means for the next tasks

1. **D3 parsers:** keep only series EQ, BE, BZ, SM, ST, and only symbols in `EQUITY_L.csv` or mapped to them by `symbolchange.csv`. Everything else (N*, P*, BL, W*, GB, GS, IV, ETFs) is another instrument. Key stays (symbol, date). Use UDiFF from 2024-01-01 and the old format before.
2. **D6 market cap:** first date 2024-02-01. Member names change case (`MCAP...` and `mcap...`), so match case-insensitively. Store the published value.
3. **D8 renames:** resolve the 148 chains by effective date; ignore rows without a company name (bonds).
4. **D9 3-day rule:** 9 symbols of `EQUITY_L` were already absent from the latest file, which is the case the rule is meant for.
5. **D10:** the Tata Motors case shows why a demerger needs a ratio: no official ratio exists.

## Not verified

- Only 33 bhavcopy days and 2 holiday probes were sampled, not every day. D7 (an independent re-download of about 40 dates) is the real check of the loaded data.
- The meaning of series codes comes from the file contents and NSE practice (BL as block deals is inferred from the ALKEM example); NSE's own code table was not fetched.
- Why 580 MCAP closes differ from the bhavcopy close is unknown.
- PR bundles were checked on 7 dates; the claim "present every day from 2024-02-01" is untested.
- `delisted.csv` was downloaded but not analysed (not needed under the 3-day rule).
- `www.nseindia.com` (corporate-actions API) was not touched in this spike.
