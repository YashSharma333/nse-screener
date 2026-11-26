# NSE Screener: Project Blueprint

**Purpose of this file:** the single, complete picture of the project for any AI agent
(Claude Code) or human who joins. Read it together with `CLAUDE.md` (the rules).
`CLAUDE.md` says HOW to work. This file says WHAT we are building, WHY, what exists today,
and in what order the rest gets built.

**Last updated:** 21 Nov 2026 (after D13, the removal of the old ETL). Sections 0a and 13 are history; sections 5, 6 and 13a describe today. If something here disagrees with the code,
the code wins. Say so in your PR and fix this file.

---

## 0. Read this first (one-page summary)

- **What it is:** a private NSE (India) end-of-day stock scanner (conditions the user builds), strategy
  engine and backtester. Long-only, delivery, no F&O. Python, MySQL, Parquet, Streamlit.
- **Who it is for:** one owner, a Python beginner who is learning through it and also using
  it as a Data Analyst portfolio project. Code and docs must be easy to read and learn from.
- **What we are building toward:** clean data for about 1,800 stocks (market cap 500 cr or
  more), user-built conditions with no hard-coded numbers, an honest backtester that answers "does
  this strategy make money and how deep is the worst drawdown". The scanner is a
  Chartink-style builder: the user composes any condition from prices, indicators with
  custom numbers, index prices, math and groups. There are no prebuilt screeners (dropped
  26 Nov, CL-24B).
- **Where we are:** the data foundation is rebuilt and finished (official raw prices from
  2016-11-21, market cap, securities, renames, official corporate actions, one daily
  `--update` command; see 13a). The cleanup programme (CL-xx) is under way; its current order is CL-24B (drop the
  prebuilt screeners, done in this PR), CL-24C (condition engine) and CL-25 (16-column
  cache). The history backend, backtest engine, experiment lab, analytics, UI rebuild,
  automation and the scanner builder UI are still to do.
- **How work is done:** the owner works with a Lead Architect (a separate chat) who writes
  prompts, reviews PRs and verifies claims, and with Claude Code, which implements. Claude
  Code never merges a PR. It works on the database after a backup (`CLAUDE.md`, "Working on the real database").
- **Rules live in `CLAUDE.md`.** This file explains the project. `CLAUDE.md` explains how
  to behave in it.

## 0a. The story so far

**What the project was at the start (Phases 0 to 2, built with Antigravity):**
- A working Streamlit app that downloaded NSE Bhavcopy, stored it in MySQL, adjusted prices
  for splits and bonuses, computed technical indicators into a Parquet cache, and ran
  screeners (the prebuilt screeners were removed on 26 Nov, CL-24B).
- About 498 symbols (the NIFTY 500 constituents), roughly 3 years of history, 5 benchmark
  indices, 20 screeners (three core: swing_volume, swing_momentum, rsi_momentum), a strategy
  specification, risk, cost and signal modules, and about 243 offline tests.
- No backtest engine, no stock master, no handling of renamed symbols, and only weekday
  data.

**What we found and fixed on the way (Nov 2026):**
- The database was missing special weekend sessions (Diwali Muhurat). Fixed (T64) by probing
  every calendar day.
- NSE serves the previous day's file on non-trading days, which had created 197 fake
  sessions. Fixed by date validation inside each file, and the fakes were purged (T64b).
  The database is now a clean 776 dates.
- The universe was only the NIFTY 500. It was widened to every stock with market cap of 500
  cr or more, with a 490 cr buffer, using the owner's Chartink export (T65a). A
  `stock_master` table now holds name, sector, industry, cap bucket and status.
- Renamed symbols split a stock's price history (ICICIPRULI to ICICILIFE, PEL to PIRAMALFIN).
  Overrides and symbol aliases with a price-continuity check were added (T65b).
- A bug that only appears on MySQL (not SQLite) broke the real imports. Fixed in PR #41, and
  CI now runs a real MySQL job.
- Agent rules, 5 skills and 3 commands were written (DOCS-1) and project MCP servers were
  added (PR #42). The owner then moved all development from Antigravity to Claude Code.
- On 18 Nov 2026 the real universe import ran: 1,811 active, 489 below threshold, 1 not in
  file.

**What is left (in order):** review the unexplained price jumps (section 13); the cleanup order CL-24B (drop the prebuilt screeners), CL-24C (condition engine), CL-25 (16-column cache); findings H1 to H11 (P2); benchmarks, RS Rating,
pre-computed indicators and the dynamic-rule base (P3); history backend, run recording and
reconciliation with Chartink (P4); backtest engine (P5); experiment lab (P6); analytics
(P7); UI rebuild (P8); Docker and nightly automation with a private server (P9);
fundamentals via IndianAPI (P10); scanner builder (P11). Details are in section 14.

**Accounts and tools in play:** GitHub repo `YashSharma333/nse-screener` (public), Claude
Pro with Claude Code (desktop app), Zerodha as broker (costs only), IndianAPI (about 500
calls per month, fundamentals only), Chartink (source for the universe list and the
reference scan), a free Oracle Cloud server (later, private access only).

---

## 1. The goal in one paragraph

A private, end-of-day research platform for Indian (NSE) equities, long-only delivery,
no F&O. It must (1) hold clean, split-adjusted daily data for about 1,800 stocks with market
cap of 500 cr or more, (2) scan them with conditions the user composes (every period,
source, threshold and ratio is a parameter, no prebuilt screeners), (3) turn a condition into a complete trading
strategy and backtest it honestly (costs, slippage, look-ahead safe), answering "does this
make money, and how bad does it get" (CAGR, max drawdown, expectancy), and eventually
(4) let the owner build scanners in a Chartink-style no-code screen. Streamlit UI now,
React possibly much later. Private use only for now.

## 2. The owner (write for this person)

- Beginner in Python, learning through this project, targeting Data Analyst roles. The
  repo is also a portfolio piece, so readable code and clear docs matter as much as
  features.
- Plain names, short functions, a "why" comment wherever logic is not obvious. Explain
  new concepts in the PR text in simple English. No clever one-liners.
- Works on a MacBook Air, 16 GB RAM, Python 3.14 locally. CI runs 3.11 and 3.12. Code must
  run on all three.
- Was using Antigravity before; **from now on all development is done with Claude Code**.
  The owner reviews and merges every PR. An agent never merges.
- Trading broker: Zerodha (costs use Zerodha rates). No paid market-data feed is needed.

## 3. Words with exact meanings

| Word | Meaning |
|---|---|
| Condition | A saved tree of parts (prices, indicators with custom numbers, index prices, math, comparisons, groups) that is true or false per stock per day. Replaces the old word Screener. |
| Strategy | A complete trading plan: entry, exit, stop, sizing, costs. |
| Signal | The entry condition is true at the close of day `t`. |
| Trade | A signal that was actually taken (filled at `t+1` open). |
| Universe | The stocks we screen: market cap >= 500 cr to enter, stays until < 490 cr. |
| Benchmark | An NSE index used for relative strength (NIFTY 500, etc.). |

## 4. Tech stack and environment

- Python, pandas 3, numpy, SQLAlchemy 2, Alembic, PyMySQL, pyarrow (Parquet), pydantic v2,
  plotly, Streamlit, openpyxl, requests. All pinned in `requirements.txt`. Add a library
  only when really needed, pinned, and justified in the PR.
- MySQL 8 is the production database. SQLite is used in offline tests. **SQLite is not
  MySQL**: any SQL written for MySQL needs a compile test or the MySQL CI job (this already
  caused a bug, PR #41).
- Parquet files under `data/` hold caches. Raw NSE files are cached under
  `data/raw/bhavcopy_cache/`.
- CI (GitHub Actions): ruff lint and format, pytest on 3.11 and 3.12 (coverage >= 70%), and
  a MySQL 8 job that runs Alembic upgrade, downgrade, upgrade plus `pytest -m mysql`.
- The repo is **public**: never commit `.env`, `.xlsx`, `.csv` exports, data, tokens.

### MCP servers (configured in `.mcp.json`, added in PR #42)

| Server | Use | Rule |
|---|---|---|
| `mysql` | Read-only SQL on `nse_screener` through a `SELECT`-only user (`nse_readonly`) | Use it to check data and verify claims. Never try to write. Never use the owner's root account or read `.env`. |
| `mysql_write` | Data fixes (INSERT, UPDATE, DELETE only, no schema changes) through user `nse_agent` | Backup first (`scripts/backup_db.sh`), preview with SELECT, owner OK only for price-history deletes, DROP/TRUNCATE and changes over 10,000 rows (`CLAUDE.md`, "Working on the real database"). |
| `github` | Issues and pull requests | Open PRs and issues. **Never merge** (the merge tool is blocked in `.claude/settings.json`). |

The agent may run migrations, pipeline commands and small data fixes on the real database
after a backup, following the "Working on the real database" section of `CLAUDE.md`.

## 5. Architecture at a glance

```
NSE archives: Bhavcopy (2 formats), PR bundles (market cap), EQUITY_L, symbolchange,
              index files (benchmarks), corporate-actions API
        |
        v
  src/ingestion/nse_files.py  (the one polite download helper: retries, date check)
  price_loader / mcap_loader / securities / renames / benchmark / corporate_actions*
        |
        v
   MySQL: daily_prices, securities, symbol_changes, corporate_actions, adj_factors,
          benchmark_prices
        |
        v
  src/etl/adjust.py  (official corporate actions -> adj_* columns; raw prices never change)
        |
        v
  src/computation/indicators/calculator.py  ->  Parquet indicator cache
        |                                        (data/processed/indicators.parquet + meta.json)
        v
  src/conditions/  (condition trees: nodes, evaluate, groups, save/load JSON)
        |
        v
  src/strategies/ (StrategySpec JSON, signals, risk, costs)  ->  [backtest engine: NOT BUILT YET]
        |
        v
  ui/ (Streamlit: Dashboard; stock inspector component; condition builder page later)

  src/etl/daily_update.py runs the whole chain in order: `python run_pipeline.py --update`
  (9 steps, stops at the first failure). `scripts/run_app.sh` = update, then the dashboard.
```

### Key files

| Path | Role |
|---|---|
| `run_pipeline.py` | The CLI. Every data operation is a flag here; no flag prints help. |
| `scripts/run_app.sh` | Runs `--update`, then opens the dashboard (on the last good data if the update fails). |
| `src/etl/daily_update.py` | The 9-step daily routine (load prices, market cap, securities and renames, corporate actions, bogus-session check, benchmarks, adjust, indicator cache, summary). |
| `src/etl/adjust.py` | Adjusted prices from the OFFICIAL corporate actions (`build_events`), owner overrides, and a report-only list of unexplained price jumps. No price-based guessing. |
| `src/etl/bogus_sessions.py` | Read-only check for dates that copy the previous session. Never deletes. |
| `src/ingestion/` | `nse_files.py` (download helper), `bhavcopy_parser.py`, `price_loader.py`, `mcap_loader.py`, `securities.py`, `renames.py` (`prices_for`), `benchmark.py` (index closes for RS), `corporate_actions*.py` (official reports, parser, store and the adjustment reader). |
| `src/db/` | `models.py`, `session.py`, `upsert.py` (MySQL-safe upsert), `data_service.py`. |
| `src/computation/indicators/calculator.py` | All indicators, 2-year window plus 300 warm-up bars. |
| `src/computation/cache_reader.py` | Read-only access to the indicator cache. The UI uses only this; it never builds the cache. |
| `src/conditions/` | `nodes.py` (typed parts, checked on load), `evaluate.py`, `groups.py`, `storage.py` (save/load JSON). See `docs/condition_engine.md`. |
| `src/strategies/` | `spec.py` (pydantic StrategySpec, entry = `condition` tree), `signals.py`, `risk.py`, `costs.py`, `cli.py`. |
| `config/` | `settings.py`, `costs.json`, `adjustment_overrides.csv`. |
| `migrations/versions/` | Alembic 001 to 010. |
| `ui/` | Streamlit pages and components. |
| `tests/` | Offline tests only (SQLite and `tmp_path`). |

## 6. The data layer (hard-won rules, never undo)

1. **Every calendar day is probed**, because NSE holds weekend and holiday special sessions
   (for example Diwali Muhurat).
2. **NSE serves another day's file on non-trading days.** The date inside each file
   (DATE1, TIMESTAMP, "Index Date") must equal the requested date, otherwise the file is
   rejected.
3. **A 404 within the last 3 calendar days is never cached as a holiday** (the file may not
   be published yet).
4. **After inserting historical rows, rebuild adjusted prices** for those symbols
   (`rebuild_adjusted`).
5. **Corporate-action detection keeps its cross-sectional guard**: if 90% or more of symbols
   have an event on one date, it is a data problem, not a market event.
6. **Raw prices are immutable and price history is never deleted.** No code path deletes
   `daily_prices` rows; the bogus-session check only reports.
7. **Two Bhavcopy formats:** old (`cm<DD><MON><YYYY>bhav.csv.zip`) until 2023-12-29, UDiFF
   from 2024-01-01. Stored series: EQ, BE, BZ, SM, ST.

### Tables

| Table | What it holds |
|---|---|
| `daily_prices` | Raw OHLCV per symbol per date (`series`, `isin`, `prev_close`, `market_cap` from 2024-02-01) plus the adjusted `adj_*` columns. About 5.2 million rows, 4,661 symbols including delisted, 2016-11-21 onward. |
| `securities` | The official listing master (`EQUITY_L.csv`): symbol, ISIN, name, series, listing date, status. |
| `symbol_changes` | Official NSE renames (`symbolchange.csv`). Evidence only: raw rows are never relabelled; `prices_for` joins old and new names at read time. |
| `corporate_actions` | Official NSE corporate actions, one row per action part, with exact `price_factor` for splits, bonuses and consolidations (`parse_status` exact / needs_review / info). See `docs/corporate_actions.md`. |
| `adj_factors` | The factors actually applied by `--rebuild-adjusted`. |
| `benchmark_prices` | NSE index closes (NIFTY 50, 100, 500, Midcap 150, Smallcap 250). |

Removed in D13 (migration 010): `stock_master`, `symbol_alias`, `index_membership`,
`technical_indicators`, `daily_prices.index_name`.

### Which stocks are screened

All regular-series stocks are loaded, delisted ones included, so history has no
survivorship gap in the prices themselves. The dashboard filters by series and by a minimum
market cap (default 500 cr, EQ only; NULL market cap before 2024-02-01 is never guessed).
There is no stored "universe". **Every backtest result must still carry a
`survivorship_biased` flag** until a per-date tradable universe exists.

### Symbol renames

Raw `daily_prices.symbol` is never changed. Renames come from the official
`symbolchange.csv` (`symbol_changes`) and are joined at read time by `prices_for` in
`src/ingestion/renames.py` (chains `A -> B -> C`, cycle detection, `verify_rename_by_isin`).
Mergers and demergers (TATAMOTORS to TMPV, SRTRANSFIN to SHRIRAMFIN) are a separate
question when building a continuous series; the owner decides per case.

### CLI (all in `run_pipeline.py`)

`--update`, `--load-prices`, `--load-mcap`, `--load-securities`, `--load-renames`,
`--download-corporate-actions`, `--rebuild-adjusted`, `--benchmarks`, `--refresh-cache`,
`--verify-prices`, `--crosscheck-52w`, with `--from/--to` and `--start/--end` for ranges.
Check `--help` for the truth.

## 7. Indicators and cache design

- Cache: `data/processed/indicators.parquet` plus `indicators.meta.json` (schema version,
  history years, DB max date, adj-factor count, benchmark max date). Whenever these change,
  the cache rebuilds itself. **Bump `INDICATORS_SCHEMA_VERSION` whenever columns are
  added**, or the old cache silently lacks them.
- Window: 2 years of snapshots plus 300 warm-up bars, so every cached row is valid.
- Memory rule (16 GB): about 1,800 stocks x 10 years is about 4.5 million rows. Design is two
  outputs: a small **latest-snapshot** Parquet (one row per stock) that all UI pages read
  instantly, and **full history in small per-year Parquet pieces** that backtests load
  only when needed. Never load 10 years of all stocks into one DataFrame.
- **Pre-compute list (approved, CL-25, `PRECOMPUTED_INDICATORS` in `config/settings.py`):** the
  price columns plus 15 indicators, named by the resolver: SMA of close 20, 50, 200
  (`sma_close_50`); EMA of close 9, 10, 20, 50, 200; Bollinger Bands (20, 2) upper and lower;
  SMA of volume 20 and 50; RSI 14; and the PRIOR-day 252-day high and low
  (`window_high_252_max_ago1`, `window_low_252_min_ago1`; today's close can never break a window
  that includes today). The RS line is not cached; it returns later as a named indicator.
- Everything else (any period, source, offset or ratio) is computed when it is asked for. This is
  the **two-tier lookup**: `cache_reader.load_with_indicators(specs, symbols, dates)` reads a
  column named by the resolver (e.g. `sma_close_50`) from the Parquet file if it is saved there,
  otherwise computes it per symbol from the cached price rows. It never writes the cache. A
  computed EMA/RSI starts at the first cached row, so it can differ in far decimals from the
  full-history value; the saved columns are exact.
- `data/processed/benchmarks.parquet` (index_code, date, close; every stored index) is written by
  the cache build, so the read-only UI and the condition engine's index part need no MySQL.
- Offsets such as "1 day ago" shift **per symbol**. The old shift-fallback bug mixed
  symbols; guard it with a test.

## 8. Conditions (no prebuilt screeners)

**Owner decision 26 Nov (CL-24B):** the 25 prebuilt screeners, the `Screener` class, the
registry, JSON screeners and the screener UI pages were deleted. Prebuilt screeners remove
flexibility. The product is a Chartink-style builder: the user composes any condition.

- A **condition** is a JSON tree of parts: number, field (open, high, low, close, volume,
  market cap, N days ago), index close, indicator with custom numbers, window (max, min, sum,
  count over N days), math, function, compare (including crossed above and below) and group
  (all, any, any 1, any 2). It is true or false per stock per day. Design: CL-24C and
  [condition_engine.md](condition_engine.md).
- A strategy entry is a condition tree (`entry.condition`, CL-24C), evaluated over the price
  history by `src/conditions/evaluate.py`. Unknown (not enough history) means false, never a
  default. Index parts read close only until the index OHLC download exists.
- **No hard-coded numbers inside logic.** Every period, threshold and ratio is a parameter.
- **No `eval` or `exec`:** a condition is data (JSON), checked when it is loaded.
- The shipped strategies were checked against the old screeners they replaced: identical entry
  signals on the five golden dates (the strategy files hold the rules the screeners used).

### Acceptance scan (Chartink, owner's own)

1. 12-month return of the stock beats NIFTY 500's 12-month return (`rs_252d_nifty_500 > 0`).
2. EMA20 > SMA50 > SMA200.
3. Close > SMA200 and close > SMA50.
4. Close >= 0.85 x highest High of the last 252 days **ending yesterday** (`high_252d_prev`).
5. Volume > 1.5 x its 20-day average.
6. 20-day average volume > 250,000.

Reconciliation (T54): compare our output with the owner's Chartink history CSV (20 Mar to
12 Nov 2026, 160 trading days, 4,567 rows, 640 symbols) day by day, on symbols present in
both, and explain every difference. "Not in our universe" differences are expected.
Chartink sectors (26 names) and cap buckets (Smallcap, Midcap, Largecap) are kept as-is in
`stock_master`. The CSV has 5 blank sectors, 3 rows with sector "Indices", and
`dd-mm-yyyy` text dates, so clean it on import.

## 9. RS and benchmarks

- Benchmarks: today NIFTY 50, 100, 500, Midcap 150, Smallcap 250. To add: Bank Nifty,
  Smallcap 50 and 150, Metal, PSU Bank, Capital Markets, Defence, Oil & Gas. First print the
  real index names from a live NSE index file and let the owner pick. Drop any the file lacks.
- `rs_252d = (stock return ratio / benchmark return ratio) - 1`. Name columns by benchmark.
- **RS Rating (1 to 99):** weighted 12-month performance score, percentile-ranked across the
  universe each day, scaled to 1 to 99. Default 63/126/189/252 days with weights 40/20/20/20
  (IBD style). Weights and periods are configurable, with named presets for the owner's own
  "M-Score" formulas 1, 2 and 4. Check M-Score 3 as pasted: the 0.6 and 0.4 look like they
  multiply only the last term because of bracket placement. The rating depends on the
  universe, so it will not match IBD's numbers. `ret_rank_252d` is the existing simple version.

## 10. Strategy and backtest rules (LOCKED)

- Signal on day `t` close, fill at day `t+1` open.
- If stop and target fall in the same bar, assume the stop was hit first.
- Gaps through a stop exit at the open.
- Costs and slippage are always on by default: Zerodha rates, slippage 10 bps unless
  configured (`config/costs.json`: exchange fee 0.00307% from NSE circular effective 1 Mar
  2026, STT 0.1% each side on delivery, stamp duty 0.015% buy only, SEBI fee Rs 10 per crore,
  GST 18% on brokerage plus exchange plus SEBI, DP charge once per scrip per sell day).
  Show the owner the Zerodha table with sources and dates before locking it (H5).
- Risk primitives (`risk.py`): initial stop (ATR, percent, support), position size from a 1%
  risk budget and capital cap, trailing stop that never loosens, breakeven move, portfolio
  heat limit.
- `StrategySpec` (pydantic v2 JSON) lifecycle: draft, candidate, approved, retired.
- Determinism: every random process is seeded. Results are identical on repeat runs.
- No look-ahead: a value on day `t` uses data up to the close of `t` only. Every shifting or
  rolling calculation gets a test.
- What does not exist yet: the **backtest engine itself**, the experiment lab, performance
  analytics, and the history-saving backend.

## 11. UI

Streamlit: the Dashboard is the only page (the screener pages were deleted in CL-24B). The
stock inspector component (`ui/components/stock_inspector.py`) stays for the condition builder page. `ui/data.py` is the cached data layer; it is read-only and reads the cache through `src/computation/cache_reader.py` (CL-06). The entry point is `ui/Dashboard.py`.
Later: Streamlit stays for now, React maybe much later. No separate public website.

## 12. Deployment plan (parked)

Free Oracle Cloud server, always on, reachable **only from the owner's own devices**
(Tailscale-style private network), no public exposure, MySQL never exposed. Needs: Docker
with MySQL (T35), nightly job for Bhavcopy download, update and strategy signal history (T57).
Test NSE downloads from the server first (NSE sometimes blocks data-centre IPs). The owner
has no server experience: the guide must be a step-by-step PDF. A static IP and Kite
Connect are NOT needed (order placement is out of scope; data is free from Bhavcopy).
IndianAPI plan: about 500 calls per month, one stock per call. Never use it for prices or
industry, never in tests or loops. Intended only for fundamentals of stocks that pass scans
(Prompt K), cached because fundamentals change quarterly.

## 13a. Status as of 21 Nov 2026 (the D-series data rebuild)

An audit on 19 Nov showed the earlier data was unsafe (relabelled prices, guessed
adjustments, missing delisted stocks, gaps). The database was rebuilt from official NSE files:

| Task | What | PR |
|---|---|---|
| D2 | `nse_files.py`: one polite download helper and file registry | #73 |
| D3 | Parsers for both Bhavcopy formats | #74 |
| D4 | Clean raw-first schema (migration 009) | #75 |
| D5 | All raw prices from 2016-11-21 (series EQ, BE, BZ, SM, ST) | #76 |
| D6, D7 | Market cap from PR bundles; independent price verification | #77 |
| D8 | Securities list and official renames (`prices_for`) | #78 |
| D9 | The "3-day drop rule" was dropped on purpose (it would delete history) | - |
| D10 | Corporate actions on the shared fetch; 52-week cross-check | #79 |
| D11 | The single daily `--update` command; UI shows market cap | #80 |
| D12 | Removed the screener scaffold, conditions package and old scripts | #81 |
| UI1 to UI4 | Dashboard series selector, KPIs, screener sidebars, Sync button runs the full update | #83 to #86 |
| D13 | Removed the old ETL (`BhavcopyETL`, universe, aliases, index constituents), added the benchmark step to `--update`, `scripts/run_app.sh`, migration 010 | #87 |
| D14 | One location per kind of file (folders defined once in `config/settings.py`, backups only in `~/nse-backups` with rotation), and a test guard that fails if any test changes a real file under `data/` or `reports/` | this PR |

Most of the old data issues (#43, #60 to #63, #65, #68, #69) described the removed design
and were closed in D13.

## 13. Status as of 18 Nov 2026 (history, before the D-series)

**Done and merged:** Phases 0 to 2 (ingestion, adjustments, benchmarks, indicator cache,
screener platform, hardening F1 to F12); T64 (special sessions); T64b (fake-session
purge); T65a (stock master and universe import); DOCS-1 (agent rules, skills, commands,
PR #37); T65b (overrides and aliases, PR #39) plus its MySQL upsert fix (PR #41); MCP
servers (PR #42).

**Done by the owner on 18 Nov:** real universe import, 2,301 `stock_master` rows (1,811
active, 489 below threshold, 1 not in file).

**Done on 18 Nov (after the owner's go-ahead, each with a backup first):** repo moved to
Claude Code (PR #45); `ICICIPRULI -> ICICILIFE` alias applied (`verified_exact`; `PEL ->
PIRAMALFIN` skipped, `date_gap`, issue #43); `--fill-missing-symbols` run (728,261 rows; it
ignored `--dry-run`, fixed in PR #47, issue #46); 10-year backfill finished: 2,683,688 rows,
1,805 symbols, 2,478 trading dates from 2016-11-21 to 2026-11-17, 10 genuine weekend
sessions, no duplicates; split and bonus detection rebuilt from price jumps (PR #50, issue
#48) with owner-approved overrides in `config/adjustment_overrides.csv` (PR #51).

**Known problem (blocks trustworthy adjusted prices):** a 30-event sample checked against
internet sources found 21 of 24 applied events exactly right, 2 with the wrong factor (for
example COCHINSHIP 2024-01-10 was 2:1 but got 0.6) and 1 false event (IDEA 2020-03-18 was a
real crash). Real splits were also missed (PREMEXPLN 2024, MARATHON 2018, HARDWYN 2023).
Price alone cannot tell some ratios apart, so exact ratios come from NSE's official
corporate-actions data. Open data issues: #43 (PEL), #49 (stale `SPLIT` row in `adj_factors`).

**Done on 18 Nov (task CA1, with a backup first):** `--download-corporate-actions` downloads
the official corporate actions (NSE website JSON API; the API returns the full 10 years in one
call, the "1 year" limit is only in the website dropdown), `symbolchange.csv` and
`EQUITY_L.csv`. Migration 008 created `corporate_actions` (22,537 rows, 724 ex-dates with an
exact split/bonus/consolidation factor, 413 rows needing review) and `symbol_changes`
(1,080 rows). Verified against the database: COCHINSHIP 0.5, AHCL 0.1, HARDWYN 0.075,
PREMEXPLN 0.2, MARATHON 0.5, no IDEA 2020 event, a second run adds no rows. Comparison with
the current `adj_factors` (363 applied events): 321 match the official factor, 19 have the
wrong factor, 23 have no official event, and 159 official events that have a price row on the
ex-date are missing. Reader for the adjustment: `get_price_adjustment_events` in
`src/ingestion/corporate_actions_store.py`. NSE can change formats, so every download checks
the shape and stops before writing; `python scripts/check_nse_reports.py` is the live health
check.

**Done on 19 Nov (task CA2, with a backup first):** prices are now adjusted ONLY from the
official corporate actions. The PREV_CLOSE method and the price-jump ratio guessing were
deleted from `src/etl/adjust.py`. `build_events` keeps official events inside each stock's
price history (195 future or pre-listing events skipped), the owner's
`config/adjustment_overrides.csv` wins, and `find_unexplained_jumps` only reports. Real run:
534 events applied (was 363), 187,832 `daily_prices` rows re-adjusted, 190 unexplained jumps
listed in `reports/adj_events_uncertain.csv` for review, a second run updates 0 rows. Checked:
COCHINSHIP 0.5, AHCL 0.1, MARATHON 0.5, PREMEXPLN 0.2, NYKAA 1/6, no IDEA 2020 event. Eight
owner overrides that disagreed with the official factor were removed, and four splits missing
from NSE's list (Nov 2016 to Jan 2017) were added as overrides. Still open: the stale `SPLIT`
row in `adj_factors` (issue #49).

**Done on 19 Nov (task P1, read-only audit, no data changed):** full report and re-runnable
SQL in `docs/data_audit_P1.md`. Clean: prices (no duplicates, no broken OHLC, no fake
sessions), the calendar, the official-action adjustment (all 529 in-history events applied,
504 continuity checks, one suspect: DVL, #55) and the raw cache versus the database. Found
(HIGH): 124 renamed stocks have no history before the rename (#60), ICICILIFE misses 1,699
days (#61), and EQ-only ingestion leaves gaps in 566 symbols with 55 active symbols stale
(#62). Found (MEDIUM): only today's active stocks have prices, so backtests are survivor-only
(#63); benchmarks miss 3 dates in April 2023 (#64); `index_name` mixes two meanings and
`index_membership` is a single 2026-11-02 snapshot, not "from 2026-03-20" (#65); running the
tests overwrites the real indicator cache and `reports/adj_events_uncertain.csv` (#66).

**Immediately next:** decide the owner questions in #60, #62 and #63, review the 190
unexplained jumps (#58; regenerate the list first, #66), then P2. Run `--rebuild-adjusted`
after every `--download-corporate-actions`. Symbol changes are evidence for `symbol_alias`
(the continuity check stays), not auto-applied.

## 14. Roadmap (in order)

| # | Work | Depends on | Notes |
|---|---|---|---|
| P0 | DOCS-2: move rules, skills, commands to Claude Code | none | docs PR |
| owner | Aliases, fill-missing, 10-year backfill | P0 not required | owner runs commands |
| P1 | DATA-CHECK: read-only audit via MySQL MCP | backfill | **Done 19 Nov**: `docs/data_audit_P1.md`, issues #60 to #66 |
| P2 | **B1 Fixes PR**: H1 to H11, (`conditions/` and `scaffold.py` already deleted in D12), merge registry into base, schema version bump, missing columns, remove UI fallback | P1 | **Next.** Refactor, no behaviour change. The data issues #60 to #63 are separate owner decisions and do not block it |
| CL | **Cleanup order (26 Nov):** CL-24B drop the prebuilt screeners (done), CL-24C condition engine (this PR: strategies are condition trees), CL-25 16-column cache (needs CL-24C) | CL-24 | the P-rows below keep their old wording; this row has the current order |
| P3 | **B2 Updates PR**: benchmarks, RS Rating, pre-compute list, T60 safe-parser base with two-tier lookup, neutral rule names | P2 | needs owner approval of lists |
| P4 | **B3 Updates PR**: T46 finish (industry and sector import; `stock_master` no longer exists, so it needs a new table via a migration), T47, T48 history backend, T50 run parameter recording, T54 Chartink reconciliation, T56 look-ahead and determinism tests | P3 | acceptance = the Chartink scan |
| P5 | Backtest engine (signals to trades to equity curve), locked rules above | P4 | the big one |
| P6 | Experiment lab (parameter sweeps, walk-forward, run registry) | P5 | every run stores its parameters |
| P7 | Quant analytics (CAGR, max drawdown, Sharpe and Sortino, expectancy, MAE and MFE, regime split, survivorship flag) | P5 | |
| P8 | UI rebuild (backtest pages, results, strategy lifecycle) | P5, P7 | |
| P9 | Docker plus MySQL (T35), nightly automation (T57), Oracle deployment guide | P4 | private access only |
| P10 | Fundamentals via IndianAPI (Prompt K) | P9 | quota-aware, cached |
| P11 | Chartink-style scanner builder (Phase 10, T60 to T63, Prompt L) | P3 base, P5 for backtests on saved scanners | T62 cannot come earlier than P5 |

Task IDs T01 to T65 and the findings H1 to H11, F1 to F12 live in the owner's Master Tracker
PDF. Known H items: H1 import cycle between `strategies.rules` and `screeners.base` (fixed in I126: the parser lived in `src/screeners/rules.py`, since CL-24B it is `src/conditions/rules.py`); H4
strategy liquidity cap (needs `turnover_sma_20`); H5 brokerage and DP charges; H6 scaffold
tests; H8 and H9 broad `except`, `contextlib.suppress`, `TypeVar` boilerplate. If a task
needs an H item you cannot see, ask the owner to attach the tracker. Do not guess.

## 15. Decisions already made (do not reopen without a reason)

- No prebuilt screeners (26 Nov, CL-24B). Conditions are user-built trees in `src/conditions/`;
  the condition engine is built there (CL-24C; the old text rule parser is gone). Order: CL-24B, CL-24C, CL-25.
- No stored universe: load every regular series, filter in the dashboard (series, minimum market cap). Price history is never deleted.
- Monthly Chartink market-cap refresh is dropped; twice-yearly list is enough.
- Pre-compute only 1 to 2 years of indicators. Full history on demand.
- Streamlit now; private deployment only; no static IP; no paid data.
- Every prompt files GitHub issues for problems and updates the README.
- Pull requests are merged by a person, never by an agent; see "Git and pull requests" in CLAUDE.md.

## 16. Open items and known risks

1. Benchmarks miss a few special-session dates (issue #64); the daily `--update` now keeps
   them current for new days.
2. Tests must never touch the network (issue #82 was one that did). They also no longer
   touch the real cache or reports (issue #66, fixed in D13; D14 added a session guard that fails the run if any real file under `data/` or `reports/` changes).
3. Unexplained price jumps and missing official actions still need owner review (#58, #57, #55).
4. Survivorship bias: prices include delisted stocks, but there is no per-date tradable
   universe yet. Keep the `survivorship_biased` flag on backtests.
5. NSE's corporate-actions API is undocumented and `robots.txt` disallows `/api/` (#54);
   possible blocking of server IPs at deployment.
6. The cause of the dashboard appearing offline right after a cold start was not proven;
   D13 stops caching empty results and the launcher updates first. Watch for a repeat.

## 17. How an agent should work here (short)

1. Read `CLAUDE.md`, this file, and the task prompt. Check the branch first (skill `branch-guard`).
2. Verify claims with the read-only `mysql` MCP and by running code. Say what you did NOT verify.
3. One PR per task, small, Conventional Commits, task ID in the branch name.
4. Offline, deterministic tests with real column types. A regression test for every bug fix.
5. File a GitHub issue for every problem outside scope. Never silently work around one.
6. End with `/pr-ready`: the PR link, a plain-English summary, commands to run,
   verification results, what was not verified, issues filed, and open questions.
7. Never merge. Work on the database only by the "Working on the real database" section of `CLAUDE.md` (backup first). Never print secrets.
