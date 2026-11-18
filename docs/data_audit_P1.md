# Data audit P1 (read-only, 19 Nov 2026)

Question: how far can the 10-year database be trusted before the screener refactor (P2)
and the backtester (P5) are built on it? Nothing was written: only `SELECT` queries through
the read-only `mysql` MCP, plus read-only looks at `data/raw/bhavcopy_cache/` and
`data/processed/`. Dates below are real dates (queries use `CAST(date AS CHAR)`, see #44).

Database at audit time: 2,683,688 `daily_prices` rows, 1,805 symbols, 2,478 dates
(2016-11-21 to 2026-11-17).

## Verdict in plain English

- **Trustworthy:** the price rows themselves (no duplicates, no zero or broken OHLC, no fake
  sessions), the calendar, the official-corporate-action adjustment (every event applied,
  continuity good in 503 of 504 checked events), the raw cache versus the database (identical
  for 1,804 of 1,805 symbols), the corporate-actions tables.
- **Not trustworthy yet (HIGH):** stock *histories*. 124 renamed stocks start at their rename
  date (#60), ICICILIFE misses 1,699 days (#61), and 566 symbols have gaps because only the
  EQ series is stored (#62). Indicators such as SMA200 and 252-day high are wrong or missing
  for those stocks.
- **Design limit for backtests (MEDIUM):** only today's active stocks have prices (#63).
- No BLOCKER: nothing was catastrophically wrong, no credentials were found.

Severity: BLOCKER = wrong numbers reach screens or backtests everywhere; HIGH = likely wrong
in some stocks; MEDIUM = cosmetic, rare or a design limit; INFO = verified OK.

## The 24 checks

| # | Check | Result | Severity |
|---|---|---|---|
| 1 | Trading dates, first, last, weekend sessions | 2,478 dates, 2016-11-21 to 2026-11-17, 10 weekend dates (all known special sessions). Matches expectation | INFO |
| 2 | Fake sessions (weekend or holiday not special) | The 10 weekend dates are Muhurat, Budget and special Saturdays (2019-10-27, 2020-02-01, 2020-11-14, 2023-11-12, 2024-01-20, 2024-03-02, 2024-05-18, 2025-02-01, 2026-02-01, 2026-11-08). No date repeats the previous day's close and volume for 20 percent or more of its symbols. Weekday holidays were not compared with an official holiday list | INFO |
| 3 | Benchmark dates versus price dates | Benchmarks lack 3 dates (2023-04-06, 04-10, 04-11); 0 benchmark dates without prices | MEDIUM (#64) |
| 4 | Row count per date versus neighbours | Largest day-to-day change 3.95 percent (listings); counts rise from 778 to 1,747 because only today's stocks are stored. No partial file | INFO (see #63) |
| 5 | Duplicate (symbol, date) | 0 | INFO |
| 6 | Price sanity | 0 rows with OHLC NULL or <= 0, high < low, high < max(open, close), low > min(open, close), close 0 with volume, volume NULL, negative or zero | INFO |
| 7 | Adjusted columns | 0 NULL or <= 0 adj_*; 0 rows with adj equal to close although a later event exists; 0 rows with adj different from close without a later event | INFO |
| 8 | `index_name` | 0 NULL (blueprint risk 1 closed). Values: smallcap, midcap, largecap, three index names, `Unknown` (1,705 rows, 3 symbols); two meanings mixed | MEDIUM (#65) |
| 9 | Gaps per symbol | 566 of 1,805 symbols have gaps; 1,716 gaps; 105,301 missing symbol-days; 648 gaps longer than 60 days. Cause: EQ-only filter. Worst: CCCL, MBECL, JYOTISTRUC, UNITECH, ARIHANT | HIGH (#62) |
| 10 | Stale active symbols | 55 active symbols last priced more than 5 trading days before 2026-11-17; 6 active symbols without any price (all in #35) | HIGH (#62, #35) |
| 11 | Symbols versus `stock_master` | 0 price symbols missing from `stock_master`; 6 active without prices; 489 below_threshold and 1 renamed without prices | MEDIUM (#63, #35) |
| 12 | Renamed symbols and aliases | ICICIPRULI to ICICILIFE applied but 1,699 old days missing; PEL to PIRAMALFIN not merged (#43); 124 official renames have no history before the rename | HIGH (#60, #61, #43) |
| 13 | Symbol shape | 0 symbols outside `[A-Z0-9&-]`; 0 case or space problems; 0 case duplicates. Cache has no series column (EQ only) | INFO |
| 14 | Status counts | 1,811 active, 489 below_threshold, 1 renamed (`not_in_file` is 0 since the ICICIPRULI rename; blueprint said 1 not_in_file). Matches the 2,301 rows | INFO |
| 15 | `adj_factors` versus official or override | 535 rows; 6 have no official exact event: CGCL, KNRCON, JSWSTEEL, STERTOOLS, KPRMILL (overrides, #57) and symbol SPLIT (#49). 0 factor mismatches | INFO (#57, #49) |
| 16 | Official exact events not applied | 724 symbol-date events: 529 inside history, all 529 applied; 173 belong to symbols without prices; 22 are on or before the first price date. 173 + 22 = 195, matching the CA2 count | INFO |
| 17 | Continuity at event dates | 504 events with a previous row checked; 1 beyond 25 percent (DVL 2021-08-05, +40.2 percent, #55); 24 beyond 15 percent. 30 events have no price row on the ex-date (long series gaps) so they cannot be judged; the largest 3-day moves are SUMEETINDS 2025-10-03 (+63 percent, factor 0.2) and SUMEETINDS 2018-08-02 (-53 percent over 2 months) | MEDIUM (#55) |
| 18 | Jumps with no event | 190 jumps, 119 symbols (152 down, 38 up): equals the CA2 figure. The local CSV was overwritten by a test run | INFO (#58, #66) |
| 19 | `adj_volume = volume / cumulative factor` | All 793,973 rows of the 411 symbols with events agree within 2 percent (0 off); adj_open and adj_high use the same factor. Spot values: COCHINSHIP 0.5, PREMEXPLN 0.2, NYKAA 1/6, MARATHON 0.5 before its event | INFO |
| 20 | Benchmarks | 5 indices, 2,475 dates each, 2016-11-21 to 2026-11-17; 0 duplicates, 0 NULL or zero; only moves beyond 10 percent are the 2020-03-23 crash | MEDIUM (#64) |
| 21 | `index_membership` | Single snapshot `valid_from` 2026-11-02, 3 indices, 501 rows (blueprint said from 2026-03-20); 4 symbols not in `stock_master` | MEDIUM (#65) |
| 22 | Indicator cache versus database | `indicators.parquet` is a test fixture (65 rows, 1 symbol, to 2024-03-05; meta `db_max_date` 2024-03-05, `adj_factors_count` 0). Schema version 4 equals the code. It rebuilds on next use | MEDIUM (#66) |
| 23 | Raw cache versus database | 2,478 files and 2,478 DB dates, identical sets; `non_trading_days.json` (1,171 dates) shares no date with the DB; for 1,804 of 1,805 symbols the row count, first and last date equal the cache (exception ICICILIFE); the cache has 3,718 symbols | INFO (#61, #63) |
| 24 | `corporate_actions`, `symbol_changes` | 22,537 and 1,080 rows; 0 duplicate keys; 0 NULL ex-dates; 761 exact (362 SPLIT 0.1 to 0.5, 398 BONUS 0.09 to 0.96, 1 CONSOLIDATION 10 for VERTOZ with no prices); 0 exact rows with NULL factor; 124 symbol changes touch active symbols with no alias | HIGH (#60) |

## Issues

New: #60 (HIGH, 124 renames), #61 (HIGH, ICICILIFE), #62 (HIGH, EQ-only gaps and stale
symbols), #63 (MEDIUM, survivorship), #64 (MEDIUM, benchmark dates), #65 (MEDIUM, index
labels and membership), #66 (MEDIUM, tests overwrite real files).
Commented on: #35, #43, #49, #53 (correction: older cache files, not newer, carry
`PrevClose` and `TradeDate`; `prev_close` is NULL for 1,041,541 rows), #55, #57 (a fifth
missing split, KPRMILL), #58.

## All clear

- Calendar and sessions (checks 1, 2, 4, 5): clear, apart from the benchmark dates.
- Price integrity (6, 7): clear.
- Symbol shape and status counts (13, 14): clear.
- Adjustment (15, 16, 18, 19): clear. Applied events match the official data; volume and
  OHLC factors are consistent.
- Corporate-action and symbol-change tables (24): clear as data; the rename use is #60.
- Raw cache versus database (23): clear apart from ICICILIFE.

## Not verified

- Weekday market holidays against an official holiday list (check 2 only tests for repeated
  prices).
- Whether each long gap is really a non-EQ series (the cause is proven for CCCL and UNITECH
  by the cache and by the filter in the downloader, not for all 566).
- Factor correctness beyond continuity: events with a plausible adjusted move can still carry a
  wrong ratio (the CA2 sample check covered 30 events).
- Anything on the Parquet indicator values (the cache on disk is a fixture).

## Re-running the audit

Paste each query into the read-only `mysql` MCP (or any read-only MySQL session). Queries
that return one row per symbol or date are large; add `LIMIT` or save the output to a file.

```sql
-- 1, 2: dates, weekend sessions
SELECT COUNT(DISTINCT date), CAST(MIN(date) AS CHAR), CAST(MAX(date) AS CHAR), COUNT(*), COUNT(DISTINCT symbol)
FROM daily_prices;
SELECT CAST(date AS CHAR), DAYNAME(date), COUNT(*) FROM daily_prices
WHERE DAYOFWEEK(date) IN (1,7) GROUP BY date ORDER BY date;

-- 2: repeated prices (fake session) per date
WITH cal AS (SELECT date, LAG(date) OVER (ORDER BY date) pd FROM (SELECT DISTINCT date FROM daily_prices) x)
SELECT CAST(a.date AS CHAR), COUNT(*), SUM(a.close=b.close AND a.volume=b.volume)
FROM daily_prices a JOIN cal ON cal.date=a.date JOIN daily_prices b ON b.symbol=a.symbol AND b.date=cal.pd
GROUP BY a.date HAVING SUM(a.close=b.close AND a.volume=b.volume)/COUNT(*) > 0.2;

-- 3: price dates without a benchmark close (and the reverse)
SELECT COUNT(DISTINCT date) FROM daily_prices WHERE date NOT IN (SELECT DISTINCT date FROM benchmark_prices);
SELECT COUNT(DISTINCT date) FROM benchmark_prices WHERE date NOT IN (SELECT DISTINCT date FROM daily_prices);

-- 4: day-to-day change of the row count
SELECT CAST(date AS CHAR), c, prev_c FROM (SELECT date, c, LAG(c) OVER (ORDER BY date) prev_c
FROM (SELECT date, COUNT(*) c FROM daily_prices GROUP BY date) a) b
WHERE prev_c IS NOT NULL AND ABS(c-prev_c)/prev_c > 0.02 ORDER BY ABS(c-prev_c)/prev_c DESC LIMIT 20;

-- 5: duplicates
SELECT COUNT(*) FROM (SELECT symbol, date FROM daily_prices GROUP BY symbol, date HAVING COUNT(*)>1) t;

-- 6: price sanity
SELECT SUM(open IS NULL OR open<=0), SUM(high IS NULL OR high<=0), SUM(low IS NULL OR low<=0),
 SUM(close IS NULL OR close<=0), SUM(high<low), SUM(high<GREATEST(open,close)),
 SUM(low>LEAST(open,close)), SUM(close=0 AND volume>0), SUM(volume IS NULL), SUM(volume<0),
 SUM(prev_close IS NULL), COUNT(*) FROM daily_prices;

-- 7: adjusted columns
SELECT SUM(adj_open IS NULL OR adj_open<=0), SUM(adj_high IS NULL OR adj_high<=0),
 SUM(adj_low IS NULL OR adj_low<=0), SUM(adj_close IS NULL OR adj_close<=0),
 SUM(adj_volume IS NULL) FROM daily_prices;
SELECT COUNT(*) FROM daily_prices p WHERE p.adj_close<>p.close
 AND NOT EXISTS (SELECT 1 FROM adj_factors f WHERE f.symbol=p.symbol AND f.date>p.date);
SELECT COUNT(*) FROM daily_prices p JOIN (SELECT symbol, MAX(date) md FROM adj_factors GROUP BY symbol) f
 ON f.symbol=p.symbol AND f.md>p.date WHERE ABS(p.adj_close-p.close)<0.0001;

-- 8: index_name, prev_close NULL by year
SELECT COALESCE(index_name,'<NULL>'), COUNT(*), COUNT(DISTINCT symbol) FROM daily_prices GROUP BY index_name;
SELECT YEAR(date), SUM(prev_close IS NULL), COUNT(*) FROM daily_prices GROUP BY YEAR(date);

-- 9: gaps (counts), then the worst symbols
WITH cal AS (SELECT date, ROW_NUMBER() OVER (ORDER BY date) rn FROM (SELECT DISTINCT date FROM daily_prices) x),
 r AS (SELECT p.symbol, c.rn, LAG(c.rn) OVER (PARTITION BY p.symbol ORDER BY c.rn) prn
       FROM daily_prices p JOIN cal c ON c.date=p.date),
 gaps AS (SELECT symbol, rn-prn-1 glen FROM r WHERE prn IS NOT NULL AND rn-prn>1)
SELECT COUNT(*), COUNT(DISTINCT symbol), SUM(glen=1), SUM(glen BETWEEN 2 AND 5), SUM(glen BETWEEN 6 AND 20),
 SUM(glen BETWEEN 21 AND 60), SUM(glen>60), SUM(glen) FROM gaps;
WITH cal AS (SELECT date, ROW_NUMBER() OVER (ORDER BY date) rn FROM (SELECT DISTINCT date FROM daily_prices) x),
 sp AS (SELECT p.symbol, MIN(c.rn) r1, MAX(c.rn) r2, COUNT(*) n FROM daily_prices p JOIN cal c ON c.date=p.date GROUP BY p.symbol)
SELECT symbol, (r2-r1+1)-n missing FROM sp ORDER BY missing DESC LIMIT 10;

-- 10, 11, 14: stale symbols, coverage, status
SELECT sm.status, COUNT(*), SUM(lp.symbol IS NULL) no_prices,
 SUM(lp.md < (SELECT MIN(date) FROM (SELECT DISTINCT date FROM daily_prices ORDER BY date DESC LIMIT 6) z)) stale
FROM stock_master sm LEFT JOIN (SELECT symbol, MAX(date) md FROM daily_prices GROUP BY symbol) lp ON lp.symbol=sm.symbol
GROUP BY sm.status;
SELECT COUNT(*) FROM (SELECT DISTINCT symbol FROM daily_prices) s WHERE s.symbol NOT IN (SELECT symbol FROM stock_master);

-- 12: aliases, renames, symbol changes with no history before the rename
SELECT * FROM symbol_alias;
SELECT symbol, status, renamed_to FROM stock_master WHERE status='renamed' OR renamed_to IS NOT NULL;
SELECT COUNT(*) FROM symbol_changes c JOIN (SELECT symbol, MIN(date) fd FROM daily_prices GROUP BY symbol) h
 ON h.symbol=c.new_symbol WHERE c.old_symbol<>c.new_symbol AND c.effective_date>'2016-12-01'
 AND h.fd BETWEEN DATE_SUB(c.effective_date, INTERVAL 5 DAY) AND DATE_ADD(c.effective_date, INTERVAL 10 DAY);

-- 13: symbol shape
SELECT COUNT(DISTINCT symbol) FROM daily_prices WHERE symbol NOT REGEXP '^[A-Z0-9&-]+$';
SELECT COUNT(*) FROM (SELECT UPPER(symbol) FROM stock_master GROUP BY UPPER(symbol) HAVING COUNT(*)>1) t;

-- 15: adj_factors without an official exact event (or with another factor)
SELECT f.symbol, CAST(f.date AS CHAR), f.factor, ca.pf FROM adj_factors f
LEFT JOIN (SELECT symbol, ex_date, EXP(SUM(LN(price_factor))) pf FROM corporate_actions
           WHERE parse_status='exact' GROUP BY symbol, ex_date) ca ON ca.symbol=f.symbol AND ca.ex_date=f.date
WHERE ca.pf IS NULL OR ABS(ca.pf-f.factor)/f.factor>0.001 ORDER BY f.date;

-- 16: official exact events by group (inside history must all be in adj_factors)
SELECT CASE WHEN h.fd IS NULL THEN 'no_prices' WHEN ca.ex_date<=h.fd THEN 'before_first_price'
            WHEN ca.ex_date>h.ld THEN 'after_last_price' ELSE 'inside_history' END grp,
 COUNT(*), SUM(f.symbol IS NOT NULL)
FROM (SELECT symbol, ex_date FROM corporate_actions WHERE parse_status='exact' GROUP BY symbol, ex_date) ca
LEFT JOIN (SELECT symbol, MIN(date) fd, MAX(date) ld FROM daily_prices GROUP BY symbol) h ON h.symbol=ca.symbol
LEFT JOIN adj_factors f ON f.symbol=ca.symbol AND f.date=ca.ex_date GROUP BY grp;

-- 17: adjusted move on event dates beyond 25 percent
WITH x AS (SELECT symbol, date, adj_close, LAG(adj_close) OVER (PARTITION BY symbol ORDER BY date) pc
           FROM daily_prices WHERE symbol IN (SELECT symbol FROM adj_factors))
SELECT x.symbol, CAST(x.date AS CHAR), f.factor, ROUND(100*(x.adj_close/x.pc-1),1)
FROM x JOIN adj_factors f ON f.symbol=x.symbol AND f.date=x.date
WHERE x.pc IS NOT NULL AND ABS(x.adj_close/x.pc-1)>0.25 ORDER BY ABS(x.adj_close/x.pc-1) DESC LIMIT 20;
SELECT COUNT(*) FROM adj_factors f LEFT JOIN daily_prices p ON p.symbol=f.symbol AND p.date=f.date WHERE p.symbol IS NULL;

-- 18: unexplained jumps (same rule as find_unexplained_jumps; expect 190)
WITH cal AS (SELECT date, ROW_NUMBER() OVER (ORDER BY date) rn FROM (SELECT DISTINCT date FROM daily_prices) x),
 r AS (SELECT p.symbol, p.date, p.close, c.rn, LAG(p.close) OVER (PARTITION BY p.symbol ORDER BY p.date) pc,
       LAG(c.rn) OVER (PARTITION BY p.symbol ORDER BY p.date) prn
       FROM daily_prices p JOIN cal c ON c.date=p.date)
SELECT COUNT(*), COUNT(DISTINCT r.symbol), SUM(r.pc/r.close>=1.25), SUM(r.close/r.pc>=1.25)
FROM r LEFT JOIN adj_factors f ON f.symbol=r.symbol AND f.date=r.date
WHERE r.prn=r.rn-1 AND f.symbol IS NULL AND (r.pc/r.close>=1.25 OR r.close/r.pc>=1.25);

-- 19: adjusted volume and prices use one factor
SELECT COUNT(*), SUM(ABS(adj_volume*(adj_close/close)/volume-1)>0.02),
 SUM(ABS(adj_open/open-adj_close/close)>0.002*adj_close/close)
FROM daily_prices WHERE symbol IN (SELECT symbol FROM adj_factors) AND volume>1000;

-- 20, 21: benchmarks and membership
SELECT index_code, COUNT(*), CAST(MIN(date) AS CHAR), CAST(MAX(date) AS CHAR), SUM(close IS NULL OR close<=0),
 COUNT(*)-COUNT(DISTINCT date) FROM benchmark_prices GROUP BY index_code;
WITH b AS (SELECT index_code, date, close, LAG(close) OVER (PARTITION BY index_code ORDER BY date) pc FROM benchmark_prices)
SELECT index_code, CAST(date AS CHAR), ROUND(100*(close/pc-1),1) FROM b WHERE pc>0 AND ABS(close/pc-1)>0.10;
SELECT index_code, COUNT(*), CAST(MIN(valid_from) AS CHAR), CAST(MAX(valid_from) AS CHAR),
 SUM(symbol NOT IN (SELECT symbol FROM stock_master)) FROM index_membership GROUP BY index_code;

-- 24: official tables
SELECT parse_status, COUNT(*), SUM(price_factor IS NULL) FROM corporate_actions GROUP BY parse_status;
SELECT COUNT(*) FROM (SELECT symbol, ex_date, action_type, part_no, subject_hash FROM corporate_actions
 GROUP BY symbol, ex_date, action_type, part_no, subject_hash HAVING COUNT(*)>1) t;
SELECT COUNT(*) FROM symbol_changes;
```

Checks 22 and 23 read files, not the database. With the project virtualenv:

```bash
# 22: what the indicator cache says (compare with the database max date, adj_factors count 535, schema version 4)
cat data/processed/indicators.meta.json
# 23: raw cache dates against database dates (one parquet per trading date, plus non_trading_days.json)
ls data/raw/bhavcopy_cache/*.parquet | wc -l
```

Check 23 in full compares, for every database symbol, the row count and the first and last
date with the same numbers computed from the cache files (the `Symbol` column of each file).
Expect exactly one difference per applied rename whose old history is still missing (today:
ICICILIFE).
