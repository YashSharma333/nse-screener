---
name: nse-data-integrity
description: Use when a task touches market-data ingestion, Bhavcopy download or cache, trading-day or holiday logic, corporate-action adjustment, benchmark loading, the daily --update routine, the securities list or stock symbol names. Contains the NSE data rules learned the hard way and the checks to run before opening a PR.
---

# NSE data integrity

Read this before changing anything in `src/etl/`, `src/ingestion/` or
`run_pipeline.py`.

The data layer was rebuilt from official NSE files (tasks D1 to D12). Raw prices come
only from NSE's archives and are never relabelled or guessed. The old Bhavcopy ETL
(`BhavcopyETL`, universe import, alias merging, index constituents) was removed in D13:
do not bring it back.

## Facts about NSE data (do not undo these)

1. **Every calendar day is probed.** NSE holds weekend and holiday special sessions
   (Diwali Muhurat, Budget day Saturdays/Sundays, live-session drills). A Monday-to-Friday
   loop silently loses them.
2. **NSE may serve another day's file for a non-trading day.** The date inside every
   downloaded file (`DATE1`/`TIMESTAMP` in Bhavcopy, "Index Date" in the index file) must
   equal the requested date, otherwise reject the file, do not cache it, and record the
   date as non-trading.
3. **A 404 within the last 3 calendar days is never cached as a holiday.** The file may
   simply not be published yet (Bhavcopy usually appears in the evening, IST).
4. **Two Bhavcopy formats.** Old format (`cm<DD><MON><YYYY>bhav.csv.zip`) until
   2023-12-29, UDiFF (`BhavCopy_NSE_CM_0_0_0_<YYYYMMDD>_F_0000.csv.zip`) from 2024-01-01.
   Only series `EQ, BE, BZ, SM, ST` are stored, so `(symbol, date)` stays unique.
5. **Raw prices are immutable.** Never update or relabel `daily_prices.symbol`, `open`,
   `high`, `low`, `close`. Adjusted columns are rebuilt from official corporate actions
   only (`src/etl/adjust.py`); keep its cross-sectional guard (if 90 percent or more of
   symbols show an action on one date, it is a data problem, not a market event). After
   inserting historical rows, rebuild adjusted prices for the affected symbols.
6. **Never delete price history.** No code path may delete `daily_prices` rows. The
   bogus-session check (`src/etl/bogus_sessions.py`) only reports. If real corruption is
   ever found, ask the maintainer; the protocol is to reload from official files, not to
   patch rows.
7. **Market cap** comes from the official PR bundles and exists from 2024-02-01. Earlier
   dates stay NULL. Never estimate it.
8. **Renames are resolved at read time.** Official renames are stored in `symbol_changes`
   (from `symbolchange.csv`) and joined by `prices_for` in `src/ingestion/renames.py`
   (chains `A -> B -> C`, cycle detection, ISIN check). A rename is evidence, never a
   reason to rewrite raw rows.
9. **Benchmarks feed relative strength.** `--update` step 6 refreshes `benchmark_prices`;
   the indicator cache treats a changed benchmark date as stale.

## Checklist before opening the PR

- [ ] Tests are offline: replace `nse_files.fetch` (monkeypatch), `tmp_path`, SQLite. Nothing may touch
      the network (issue #82 was a test that did).
- [ ] Test for a file whose inner date differs from the requested date.
- [ ] Test that a recent 404 is not cached as non-trading.
- [ ] Test that rows inserted before a later split get correct adjusted prices.
- [ ] Nothing in the change can delete price rows.
- [ ] README and `python run_pipeline.py --help` agree for any new flag, table or setting.

## SQL sanity checks (put them in the PR "Commands" with a purpose line)

```sql
-- Only genuine special sessions should be on weekends
SELECT date, COUNT(*) FROM daily_prices WHERE DAYOFWEEK(date) IN (1,7) GROUP BY date ORDER BY date;
-- Price dates without benchmark rows (a few old special sessions are expected)
SELECT COUNT(DISTINCT date) FROM daily_prices WHERE date NOT IN (SELECT DISTINCT date FROM benchmark_prices);
-- Newest price date and market cap coverage
SELECT CAST(MAX(date) AS CHAR), SUM(market_cap IS NULL) FROM daily_prices WHERE date = (SELECT MAX(date) FROM daily_prices);
```

Genuine weekend sessions already confirmed: 2023-11-12, 2024-01-20, 2024-03-02,
2024-05-18, 2025-02-01, 2026-02-01, 2026-11-08.
