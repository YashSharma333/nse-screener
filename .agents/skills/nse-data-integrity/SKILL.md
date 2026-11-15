---
name: nse-data-integrity
description: Use when a task touches market-data ingestion, Bhavcopy download or cache, trading-day or holiday logic, corporate-action adjustment, benchmark or index loading, backfill, recheck or purge commands, the stock universe (stock_master) or stock symbol names. Contains the NSE data rules learned the hard way and the checks to run before opening a PR.
---

# NSE data integrity

Read this before changing anything in `src/etl/`, `src/ingestion/`,
`run_pipeline.py` or the stock universe code.

## Facts about NSE data (do not undo these)

1. **Every calendar day is probed.** NSE holds weekend and holiday special
   sessions (Diwali Muhurat, Budget day Saturdays/Sundays, live-session drills).
   A Monday-to-Friday loop silently loses them.
2. **NSE may serve another day's file for a non-trading day.** In this project
   about 197 fake sessions (Sundays and exchange holidays) were stored as exact
   copies of the previous day. The date inside every downloaded file
   (`DATE1` in the daily file, `TIMESTAMP` in the zip format, "Index Date" in the
   index file) must equal the requested date, otherwise reject the file, do not
   cache it, and record the date as non-trading.
3. **A 404 within the last 3 calendar days is never cached as a holiday.** The
   file may simply not be published yet (Bhavcopy usually appears in the
   evening, IST). Never skip such dates because of the cache either.
4. **Adjusted prices must be rebuilt after historical inserts.** The incremental
   adjuster only understands the newest rows. After inserting rows for older
   dates, call `rebuild_adjusted(symbols=...)` for the affected symbols.
5. **Keep the cross-sectional guard** in `src/etl/adjust.py`: if 90 percent or
   more of symbols show a corporate action on one date, it is a data problem,
   not a market event.
6. **Never overwrite a stored `index_name` with NULL.** Universe mode writes the
   cap bucket from `stock_master`.
7. **Never delete price history** when a stock leaves the universe; change its
   status. Universe rule: enter at 500 cr or more, stay while 490 cr or more.
8. **Symbols change.** Example: ICICI Prudential Life became `ICICILIFE`
   (old name `ICICIPRULI`). History under the old symbol must be linked to the
   new one by an explicit, tested rename, never by guessing. If you see a
   symbol that stops and a new one that starts on the same date, file an issue.
9. **Do not call IndianAPI** (small monthly request limit) from tests, loops or
   jobs unless the task says so.

## Checklist before opening the PR

- [ ] Tests use a fake downloader and `tmp_path`; no network, no MySQL.
- [ ] Test for a file whose inner date differs from the requested date.
- [ ] Test that a recent 404 is not cached as non-trading.
- [ ] Test that rows inserted before a later split get correct adjusted prices.
- [ ] Destructive actions default to dry-run, need `--yes`, and back up rows.
- [ ] README updated for any new flag, table or setting.

## SQL sanity checks to give the owner (put them in the PR "Commands")

```sql
-- Only genuine special sessions should be on weekends
SELECT date, COUNT(*) FROM daily_prices WHERE DAYOFWEEK(date) IN (1,7) GROUP BY date ORDER BY date;
-- Every price date should have benchmark rows
SELECT COUNT(DISTINCT date) FROM daily_prices WHERE date NOT IN (SELECT DISTINCT date FROM benchmark_prices);
-- No NULL index labels on recent rows
SELECT date, COUNT(*), SUM(index_name IS NULL) FROM daily_prices WHERE date >= '2026-11-12' GROUP BY date;
```

Genuine weekend sessions already confirmed: 2023-11-12, 2024-01-20, 2024-03-02,
2024-05-18, 2025-02-01, 2026-02-01, 2026-11-08.
