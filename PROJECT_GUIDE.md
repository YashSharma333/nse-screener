# NSE Screener — Complete Project Guide

> A full-stack quantitative stock screener for NSE equities. Downloads daily price data from NSE, stores it in MySQL, computes technical indicators, and presents it through a Streamlit dashboard with two rule-based trading strategies.

---

## Table of Contents

1. [Big Picture — What Does This App Do?](#1-big-picture)
2. [Project Structure](#2-project-structure)
3. [Dependencies — What's Installed and Why](#3-dependencies)
4. [Configuration Layer](#4-configuration-layer)
5. [Database — Schema, Creation, Models](#5-database)
6. [Ingestion Layer — How Data Enters the System](#6-ingestion-layer)
7. [ETL Pipeline — The Brain of the System](#7-etl-pipeline)
8. [Computation Layer — Indicators and Screener](#8-computation-layer)
9. [Data Service — Bridge Between DB and UI](#9-data-service)
10. [UI Layer — Streamlit App](#10-ui-layer)
11. [Full Execution Flow — End to End](#11-full-execution-flow)
12. [Module Import Map](#12-module-import-map)
13. [How to Run](#13-how-to-run)
14. [Offline Mode — What Happens Without MySQL](#14-offline-mode)

---

## 1. Big Picture

```
NSE Website
    │
    ▼ (HTTP download daily CSV)
Bhavcopy Downloader ──► Parquet Cache (data/raw/bhavcopy_cache/)
    │
    ▼ (ETL pipeline processes rows)
MySQL Database (daily_prices table)
    │
    ▼ (Data Service queries + indicators computed)
Streamlit UI
    ├── Dashboard (all 500 stocks, latest snapshot)
    ├── Swing + Volume (Strategy 1 filtered list)
    └── Swing With Momentum (Strategy 2 filtered list)
```

Every trading day NSE publishes a **Bhavcopy** — a CSV file with OHLCV data for every stock listed on NSE. This app:

1. **Downloads** those files day by day (going 3 years back on first run).
2. **Parses and caches** them as Parquet files locally.
3. **Loads** the data into a MySQL table (`daily_prices`).
4. **Computes** 20+ technical indicators per stock (SMA, EMA, RSI, MACD, Bollinger Bands, etc.).
5. **Screens** the universe against two quantitative strategies.
6. **Shows** everything in a dark-themed Streamlit terminal UI with charts.

---

## 2. Project Structure

```
nse-screener/
│
├── config/
│   ├── settings.py          # Paths, logging config, setup_logging()
│   └── logger.py            # (unused shim — settings.py handles logging)
│
├── src/
│   ├── ingestion/
│   │   ├── downloader.py         # NSEBhavcopyDownloader — HTTP + Parquet cache
│   │   └── index_constituents.py # NSEIndexConstituents — NIFTY 500 symbol list
│   │
│   ├── db/
│   │   ├── models.py        # SQLAlchemy ORM: DailyPrice, TechnicalIndicator
│   │   ├── session.py       # DB engine, SessionLocal, get_db(), init_db()
│   │   └── data_service.py  # load_raw_market_data(), compute_market_indicators()
│   │
│   ├── etl/
│   │   └── pipeline.py      # BhavcopyETL: resolve_date_range, collect_new_rows, load_to_db
│   │
│   ├── computation/
│   │   ├── indicators.py    # TechnicalCalculator — all indicator formulas
│   │   └── screener.py      # Re-export shim → src.screener.StockScreener
│   │
│   └── screener.py          # StockScreener — strategy_swing_volume, strategy_swing_momentum
│
├── ui/
│   ├── app.py               # Streamlit router (entry point)
│   ├── Dashboard.py         # Page 1: full market snapshot + ETL trigger button
│   ├── pages/
│   │   ├── 2_swing_volume.py    # Page 2: Swing + Volume strategy results
│   │   └── 3_swing_momentum.py  # Page 3: Swing With Momentum strategy results
│   └── components/
│       ├── terminal_styles.py   # CSS theming, dark terminal look
│       └── stock_inspector.py   # Candlestick + volume + RSI chart (Plotly)
│
├── tests/
│   ├── test_etl.py
│   ├── test_indicators.py
│   ├── test_pipeline.py
│   ├── test_screener.py
│   └── test_ui_pages.py
│
├── data/
│   └── raw/
│       └── bhavcopy_cache/   # *.parquet files — one per trading day
│
├── logs/
│   └── screener.log          # Rotating log (5 MB × 3 backups)
│
├── run_pipeline.py           # CLI entry point (python run_pipeline.py --incremental)
├── setup_project.py          # One-time project bootstrap (creates .env, directories)
├── .env                      # MySQL credentials (not committed)
├── requirements.txt
└── pytest.ini
```

---

## 3. Dependencies

| Package | Version | Why it's used |
|---|---|---|
| `streamlit` | ≥1.31 | The entire UI — pages, widgets, charts, session state |
| `pandas` | ≥2.1 | All data manipulation: reading CSVs, groupby, rolling, etc. |
| `numpy` | ≥1.26 | Numeric operations in indicator calculations and screener |
| `SQLAlchemy` | ≥2.0 | ORM for MySQL — models, sessions, `INSERT … ON DUPLICATE KEY UPDATE` |
| `pymysql` | ≥1.1 | MySQL driver — SQLAlchemy's `mysql+pymysql://` dialect uses this |
| `cryptography` | ≥42 | Required by pymysql for SSL support |
| `plotly` | ≥5.18 | Candlestick charts, volume bars, RSI oscillator in StockInspector |
| `requests` | ≥2.31 | HTTP downloads from NSE archives |
| `pyarrow` | ≥14 | Parquet read/write (`pd.read_parquet`, `df.to_parquet`) |
| `python-dotenv` | ≥1.0 | Reads `.env` file into environment variables |
| `pytest` + `pytest-mock` | ≥8 / ≥3.12 | Test runner and mocking framework |

---

## 4. Configuration Layer

### `config/settings.py`

This is the **first thing imported** by every other module. It does two things:

**1. Defines path constants:**
```python
PROJECT_ROOT = Path(__file__).resolve().parent.parent  # /nse-screener/
LOGS_DIR     = PROJECT_ROOT / "logs"
LOG_FILE     = LOGS_DIR / "screener.log"
```

**2. Defines `setup_logging()`:**
Called once per module (idempotent — won't add duplicate handlers). Sets up:
- A **rotating file handler** → `logs/screener.log` (5 MB max, 3 backups)
- A **stdout handler** → prints to terminal

**Why it's called at the top of every module:**
```python
setup_logging()
logger = logging.getLogger(__name__)
```
Each module gets its own named logger (e.g., `src.etl.pipeline`, `src.ingestion.downloader`) so log lines are easy to trace.

---

## 5. Database

### Schema

The app uses a single primary table:

```sql
CREATE TABLE daily_prices (
    id         BIGINT       AUTO_INCREMENT PRIMARY KEY,
    symbol     VARCHAR(20)  NOT NULL,         -- e.g. "RELIANCE"
    date       DATE         NOT NULL,         -- e.g. 2024-06-10
    open       DECIMAL(10,2),
    high       DECIMAL(10,2),
    low        DECIMAL(10,2),
    close      DECIMAL(10,2),
    volume     BIGINT,
    index_name VARCHAR(50),                   -- "NIFTY 100", "NIFTY Midcap 150", etc.

    UNIQUE KEY uq_symbol_date (symbol, date), -- prevents duplicate inserts
    INDEX ix_daily_prices_symbol (symbol),
    INDEX ix_daily_prices_date (date),
    INDEX ix_daily_prices_index_name (index_name)
);
```

A second table `technical_indicators` is defined in models.py but is a **placeholder** — indicators are computed in-memory by `TechnicalCalculator` at read time.

### How Tables Are Created

`src/db/models.py` defines ORM models using **SQLAlchemy 2.x declarative syntax**:

```python
class Base(DeclarativeBase): pass

class DailyPrice(Base):
    __tablename__ = "daily_prices"
    id         = Column(BigInteger, primary_key=True, autoincrement=True)
    symbol     = Column(String(20), nullable=False, index=True)
    date       = Column(Date, nullable=False, index=True)
    open       = Column(Numeric(10, 2))
    high       = Column(Numeric(10, 2))
    low        = Column(Numeric(10, 2))
    close      = Column(Numeric(10, 2))
    volume     = Column(BigInteger)
    index_name = Column(String(50), index=True)
    __table_args__ = (UniqueConstraint("symbol", "date", name="uq_symbol_date"),)
```

`init_db()` in `session.py` calls:
```python
Base.metadata.create_all(bind=engine)
```
This issues `CREATE TABLE IF NOT EXISTS` for every model — **idempotent, safe to call multiple times**.

`init_db()` also runs a migration check: if `index_name` column is missing from an older DB, it adds it via `ALTER TABLE` automatically.

### DB Engine and Session

```python
# session.py — runs at import time
load_dotenv(PROJECT_ROOT / ".env")   # reads MYSQL_USER, PASSWORD, HOST, PORT, DB

DATABASE_URL = f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}"

try:
    engine = create_engine(DATABASE_URL,
        pool_size=10, max_overflow=20,
        pool_recycle=3600,    # recycle stale connections every hour
        pool_pre_ping=True,   # test conn before handing it out
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
except (ImportError, Exception):
    engine = None        # pymysql not installed or other startup error
    SessionLocal = None  # DB features gracefully disabled
```

> **Important:** `create_engine()` is **lazy** — it does NOT connect to MySQL at this line. The connection happens only on the first query. If MySQL is offline at import time, the engine is still created successfully (as long as pymysql is installed).

**`get_db()` — safe session context manager:**
```python
@contextmanager
def get_db():
    if SessionLocal is None:
        raise RuntimeError("Database session factory is uninitialized...")
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```

Usage:
```python
with get_db() as session:
    session.execute(some_query)
# auto-commit on success, auto-rollback on exception, always closes
```

---

## 6. Ingestion Layer

### `src/ingestion/downloader.py` — `NSEBhavcopyDownloader`

Downloads daily Bhavcopy CSV files from NSE's public archive. Two URL formats exist:

| Format | URL Pattern | Used for |
|---|---|---|
| **New** (plain CSV) | `sec_bhavdata_full_DDMMYYYY.csv` | 2023–present |
| **ZIP** (historical) | `cmDDMONYYYYbhav.csv.zip` | Older archives |

**Flow of `download_bhavcopy(date)`:**

```
1. Check if CACHE_DIR/YYYY-MM-DD.parquet exists
   → YES: read and return immediately (instant cache hit)
   → NO: continue

2. Warm up HTTP session
   (visit nseindia.com homepage to get the session cookie NSE requires)

3. Try "new" URL format:
   → 200 OK: parse CSV → save as .parquet → return DataFrame
   → 404:     not available in this format → try ZIP
   → 403:     refresh session + retry (up to 3 times)
   → 429:     wait for Retry-After header + retry

4. Try "zip" URL format (same retry logic)

5. Both fail → return None  (holiday or weekend — no data published)
```

**Why Parquet cache?**
CSV takes ~400 KB per file per parse. Parquet is compressed columnar storage — reads 10× faster and is typed. With 777+ cached files, restarts and re-runs are instant without re-downloading.

**Parsing normalization:**
```python
# New format columns:  SYMBOL, SERIES, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, CLOSE_PRICE, TTL_TRD_QNTY
# ZIP format columns:  SYMBOL, SERIES, OPEN,       HIGH,       LOW,       CLOSE,       TOTTRDQTY
# After normalization: Symbol, Series, Open,        High,       Low,       Close,       Volume
```
Then filters `Series == "EQ"` — keeps only equity instruments, excludes F&O, ETFs, bonds.

---

### `src/ingestion/index_constituents.py` — `NSEIndexConstituents`

NSE Bhavcopy contains ~1900+ listed stocks. The app filters down to the **NIFTY 500 universe**:

| Index | Stocks | CSV File |
|---|---|---|
| NIFTY 100 (Large Cap) | ~100 | `ind_nifty100list.csv` |
| NIFTY Midcap 150 | ~150 | `ind_niftymidcap150list.csv` |
| NIFTY Smallcap 250 | ~250 | `ind_niftysmallcap250list.csv` |

`fetch_all()` downloads all three, merges them into a `ConstituentResult` (a dict mapping `symbol → index_name`):

```python
result = NSEIndexConstituents().fetch_all()
result.symbols          # {'RELIANCE', 'TCS', 'DIXON', ...}  (~500 total)
result['RELIANCE']      # → "NIFTY 100"
result['DIXON']         # → "NIFTY Midcap 150"
```

The ETL uses this as a symbol filter — only rows matching these ~500 symbols are stored in MySQL. This keeps the DB focused and avoids noise from penny stocks and micro-caps.

---

## 7. ETL Pipeline

### `src/etl/pipeline.py` — `BhavcopyETL`

The **orchestrator** that ties ingestion → filter → load into one flow.

```python
etl = BhavcopyETL()
etl.run()              # full pipeline
etl.run_incremental()  # UI-triggered lightweight update
```

---

### Step 1: `resolve_date_range()` — Which dates to fetch?

```python
fetch_start, fetch_end, is_update = self.resolve_date_range()
```

Decides the date window by checking two sources:

**Source A — MySQL:**
```sql
SELECT MAX(date) AS latest_date FROM daily_prices
```
Returns the most recent date stored. If DB is offline → `db_last_stored = None`.

**Source B — Parquet cache (offline fallback):**
Lists all `*.parquet` files in `data/raw/bhavcopy_cache/`, parses dates from filenames, finds `max()`. Even if MySQL is down, the system knows what's been downloaded.

**Decision:**
```
last_stored = max(db_last_stored, cache_last_stored)

if last_stored is None:
    → FIRST RUN: fetch_start = today - initial_days_back (1100 days = ~3 years)
    → is_update = False

elif last_stored + 1 day > today:
    → Already current: returns (today+1, today, True) → fetch_start > fetch_end
    → run() exits immediately

else:
    → UPDATE RUN: fetch_start = last_stored + 1 day
    → is_update = True
```

---

### Step 2: `collect_new_rows()` — Download and transform

```python
stock_rows = self.collect_new_rows(fetch_start, fetch_end, symbol_filter, symbol_index_map)
```

Iterates every **weekday (Mon–Fri)** in the date range:
```
for each weekday d in [fetch_start, fetch_end]:
    df = downloader.download_bhavcopy(d)
    if df is None:
        log "holiday / weekend"
        continue
    filter df to only NIFTY 500 symbols
    for each row in df:
        stock_rows[symbol].append({
            symbol, date, open, high, low, close, volume, index_name
        })
```

Returns a dict:
```python
{
  "RELIANCE": [{"date": date(2024,6,10), "open": 2900, "close": 2950, ...}, ...],
  "TCS":      [{"date": date(2024,6,10), ...}, ...]
}
```

---

### Step 3: `load_to_db()` — Upsert into MySQL

```python
success, failed = self.load_to_db(stock_rows)
```

Flattens all rows and upserts in batches of 1000:

```python
stmt = mysql_insert(DailyPrice).values(batch)
session.execute(
    stmt.on_duplicate_key_update(
        open=stmt.inserted.open,
        high=stmt.inserted.high,
        low=stmt.inserted.low,
        close=stmt.inserted.close,
        volume=stmt.inserted.volume,
        index_name=stmt.inserted.index_name,
    )
)
```

**Why `ON DUPLICATE KEY UPDATE`?**
The `(symbol, date)` unique constraint means re-running the ETL for the same dates never fails or creates duplicates — it just updates existing rows. The operation is fully **idempotent**.

**Error handling tiers:**

| Scenario | Behavior |
|---|---|
| Connection error (MySQL offline) | Returns `([], [])` immediately — Parquet data is safe |
| Data error (one bad row/symbol) | Fast path fails → slow path retries each symbol individually |
| Success | Returns `(sorted_success_list, [])` |

---

### Step 4: `run_incremental()` — UI-triggered update

```python
result = etl.run_incremental(lookback_days=5)
```

Called when user clicks "🔄 Update Latest Market Data". Key difference from `run()`:
- Temporarily sets `self.initial_days_back = 5`
- So even if no cache is found, it only scans the last 5 days (not 1100)
- After `run()` completes, calls `check_connection()` to determine the correct status

Returns one of:
```python
{"status": "success",    "symbols_updated": 497, ...}
{"status": "up_to_date", "message": "Already current with latest trading session"}
{"status": "offline",    "message": "Local Parquet cache is current. MySQL is offline."}
{"status": "error",      "message": "...details..."}
```

---

## 8. Computation Layer

### `src/computation/indicators.py` — `TechnicalCalculator`

A stateless class of `@staticmethod` methods. Each takes a DataFrame and returns a pandas Series.

| Method | Formula | Period |
|---|---|---|
| `calculate_sma(df, window)` | `close.rolling(window).mean()` | 50, 200 |
| `calculate_ema(df, window)` | `close.ewm(span=window).mean()` | 12, 20, 26 |
| `calculate_rsi(df)` | `100 - 100/(1+RS)` where RS = avg_gain/avg_loss | 14 |
| `calculate_bollinger_bands(df)` | `mean ± 2*std` | 20 |
| `calculate_macd(df)` | `EMA(12) - EMA(26)`, signal = `EMA(9)` | 12/26/9 |
| `calculate_52_week_high(df)` | `high.rolling(252).max()` | 252 |
| `calculate_52_week_low(df)` | `low.rolling(252).min()` | 252 |
| `calculate_volatility(df)` | `pct_change().rolling(20).std()` | 20 |
| `calculate_pct_change(df, periods)` | `close.pct_change(periods) * 100` | 1/21/63/126/252 |

**`apply_all_indicators(df)`** — Main entry point:

```python
result = TechnicalCalculator.apply_all_indicators(raw_df)
```

If `df` has multiple symbols, it **groups by symbol** and processes each independently — critical because rolling calculations must not bleed across different stocks:

```python
sorted_df.groupby('symbol', group_keys=False).apply(
    _apply_with_symbol, include_groups=False
)
```

**All columns added to the DataFrame:**
```
sma_50, sma_200
ema_12, ema_20, ema_26
volume_sma_20
rsi_14
high_52w, low_52w
high_252d_prev          ← 252-day rolling high, shifted 1 day back
bb_upper, bb_middle, bb_lower
macd_line, macd_signal, macd_histogram
daily_returns, volatility_20
pct_change_daily, pct_change_1m, pct_change_3m, pct_change_6m, pct_change_12m
close_60d, close_120d, close_180d, close_252d
return_60d, return_120d
```

---

### `src/screener.py` — `StockScreener`

Takes a **snapshot DataFrame** (one row per symbol = latest day) and applies boolean masks.

```python
screener = StockScreener(snapshot_df)
results  = screener.strategy_swing_volume()
```

#### Strategy 1: Swing + Volume

Finds stocks with institutional volume breakout + multi-timeframe uptrend.

```
Rule 1 — Universe:
  index_name must be in {"NIFTY 100", "NIFTY Midcap 150", "NIFTY Smallcap 250"}

Rule 2 — Trend alignment (short MA above long MA):
  EMA(20) > SMA(50) > SMA(200)

Rule 3 — Price above MAs:
  Close > SMA(50)  AND  Close > SMA(200)

Rule 4 — Near 52-week high (within 15%):
  Close >= 0.85 × (yesterday's 252-day rolling high)

Rule 5 — Volume breakout (institutional buying):
  Volume > 1.5 × 20-day average volume
  20-day average volume > 250,000  ← liquidity floor
```

#### Strategy 2: Swing With Momentum

Passes ALL of Strategy 1's rules PLUS momentum confirmation:

```
Rule 6 — Not overextended short-term:
  60-day return <= 40%

Rule 7 — Strong medium-term momentum:
  120-day return >= 30%

Rule 8 — Structural growth check:
  ((Close - Close_252d) / Close_180d) × 100  <= 300
  ((Close - Close_180d) / Close_252d) × 100  between 50 and 300
```

Rules 8's ratios detect stocks with **sustained 6-12 month growth** that isn't parabolic (which would filter out flash crashes and meme stock spikes).

**Method aliases (backward compatible):**
```python
strategy_swing_volume()     == liquid_1_5x_volume()   == swing_volume()
strategy_swing_momentum()   == liquid_1_5x_volume_momentum() == swing_momentum()
```

---

## 9. Data Service

### `src/db/data_service.py`

Bridge between MySQL (or demo data) and the UI. The UI never queries MySQL directly.

**`load_raw_market_data()`:**
```python
raw_df, is_live, status_msg = load_raw_market_data()
```
- Calls `check_connection()`
- If **online**: `SELECT * FROM daily_prices ORDER BY symbol, date` → returns full history
- If **offline or empty**: returns `generate_demo_dataset()` — 17 synthetic stocks (RELIANCE, TCS, TRENT, DIXON, etc.) with realistic random OHLCV data

**`compute_market_indicators(raw_df)`:**
```python
with_indicators = compute_market_indicators(raw_df)
```
Groups by symbol, calls `TechnicalCalculator.apply_all_indicators()` on each group, concatenates results. Returns full time-series with all 25+ indicator columns.

**`get_latest_market_snapshot(with_indicators)`:**
```python
snapshot = get_latest_market_snapshot(with_indicators)
```
Takes the full multi-year time-series and reduces it to **one row per symbol** (the most recent date). This is what the dashboard table and screener strategies operate on.

**Why this layered approach?**
- Full history is needed for indicators (e.g., 200-day SMA needs 200 rows)
- Screener only needs latest values (one row per stock)
- Keeping them separate avoids confusion and enables the snapshot to be cached cheaply

---

## 10. UI Layer

### `ui/app.py` — Entry point

Sets up `sys.path` and acts as the Streamlit multi-page router. Streamlit auto-discovers pages from `ui/pages/`.

### `ui/Dashboard.py` — Main Dashboard

**Session state keys:**
```python
st.session_state.etl_status         # {"status": "success"|"offline"|..., "message": ...}
st.session_state.is_updating_etl    # bool — disables the button while ETL runs
st.session_state.market_data_cache  # {"raw": df, "indicators": df, "snapshot": df, ...}
```

The cache is critical: without it, every widget interaction triggers a full MySQL query + indicator computation (~30 seconds). With it, the data is loaded once per session.

**`get_market_data()`** — loads and caches:
```python
if cache is None:
    raw_df  → compute_market_indicators() → get_latest_market_snapshot()
    cache = {raw, indicators, snapshot, is_live, status_msg}
return cache
```

**`run_incremental_update()`** — triggered by "🔄 Update Latest Market Data" button:
```python
etl = BhavcopyETL()
result = etl.run_incremental()          # max 5-day lookback
st.session_state.etl_status = result
st.session_state.market_data_cache = None  # bust cache → fresh reload on next run
```

**Dashboard displays:**
- Header with LIVE/DEMO mode indicator
- 4 KPI tiles: equity count, mean daily change, advance/decline, median RSI
- Filterable data table (by Index, by symbol search, by columns to display)
- CSV export button
- Stock Inspector chart at the bottom

### `ui/pages/2_swing_volume.py` and `3_swing_momentum.py`

Both share the same session cache as Dashboard (so switching pages doesn't reload data):
```python
data = get_market_data()          # reads session cache
snapshot = data["snapshot"]
screener = StockScreener(snapshot)
qualifying = screener.strategy_swing_volume()   # apply filter rules
```
Display: KPI tiles, qualifying stocks table, TradingView watchlist string, StockInspector.

### `ui/components/stock_inspector.py`

Interactive Plotly 3-panel chart:

| Panel | Content |
|---|---|
| Panel 1 (58%) | Candlestick (OHLC) + EMA 20 (cyan) + SMA 50 (amber) + SMA 200 (purple) |
| Panel 2 (22%) | Volume bars (green=up day, red=down day) + 20D volume SMA (orange) |
| Panel 3 (20%) | RSI(14) line + dashed thresholds at 70 (overbought) and 30 (oversold) |

Lookback options: 60, 120, 180, 252, 500 sessions.

---

## 11. Full Execution Flow — End to End

### First Run (Brand New Setup)

```
1. user: .venv/bin/streamlit run ui/Dashboard.py

2. Python imports trigger:
   settings.py        → setup_logging(), PROJECT_ROOT defined
   src/db/session.py  → load_dotenv(.env) → reads DB credentials
                      → create_engine("mysql+pymysql://...") ← lazy, no connection yet
                      → SessionLocal = sessionmaker(bind=engine)

3. Dashboard: get_market_data() called
   → load_raw_market_data()
     → check_connection() → SELECT 1 → SUCCESS (MySQL is up)
     → SELECT * FROM daily_prices → EMPTY TABLE
     → generate_demo_dataset() → 17 synthetic stocks, 300 days each
   → compute_market_indicators(demo_df) → 25+ indicator columns added
   → get_latest_market_snapshot() → 1 row per stock
   UI renders: "DEMO MODE"

4. User clicks "🔄 Update Latest Market Data":
   → BhavcopyETL() created:
       • init_db() → CREATE TABLE IF NOT EXISTS daily_prices ← table created here
       • NSEBhavcopyDownloader() → creates data/raw/bhavcopy_cache/ directory
   → etl.run_incremental(lookback_days=5):
       • self.initial_days_back = 5
       • resolve_date_range():
           MySQL: SELECT MAX(date) → None (empty)
           Parquet cache: no files → None
           last_stored = None → FIRST RUN mode
           But initial_days_back=5 → only last 5 days
       • collect_new_rows(today-5, today):
           ~3 weekdays: download_bhavcopy() → HTTP GET to NSE
           → parse CSV → save .parquet → filter to NIFTY 500
       • load_to_db():
           mysql_insert(DailyPrice).on_duplicate_key_update(...)
           → upserts ~497 symbols × 3 days ≈ 1,500 rows
       → returns {"status": "success", "symbols_updated": 497}

5. Cache busted → page reloads with real data
   UI renders: "LIVE MYSQL"
```

### Daily Incremental Update

```
User clicks "🔄 Update Latest Market Data" (data already loaded from yesterday):

resolve_date_range():
  MySQL: SELECT MAX(date) → 2026-10-01 (yesterday)
  fetch_start = 2026-10-02 (today)

collect_new_rows(2026-10-02, 2026-10-02):
  → download_bhavcopy(2026-10-02)
    → If holiday (Gandhi Jayanti): returns None → "holiday / weekend"
    → If trading day: downloads CSV, parses ~497 rows, saves .parquet
    → Upserts to MySQL

Result: {"status": "up_to_date"} or {"status": "success", "symbols_updated": 497}
```

---

## 12. Module Import Map

```
ui/Dashboard.py
│
├── config.settings                   → PROJECT_ROOT, setup_logging()
├── streamlit                         → all UI widgets
├── pandas                            → DataFrame
│
├── src.db.data_service
│   ├── config.settings               → setup_logging()
│   ├── src.db.session                → engine, check_connection()
│   └── src.computation.indicators    → TechnicalCalculator
│
├── src.etl.pipeline
│   ├── config.settings               → PROJECT_ROOT, setup_logging()
│   ├── src.db.models                 → DailyPrice (ORM model for upsert)
│   ├── src.db.session                → get_db(), init_db(), engine, check_connection()
│   ├── src.ingestion.downloader      → NSEBhavcopyDownloader
│   └── src.ingestion.index_constituents → NSEIndexConstituents
│
├── ui.components.terminal_styles     → apply_terminal_theme(), render_terminal_header()
│   └── streamlit
│
└── ui.components.stock_inspector     → render_stock_inspector()
    ├── pandas
    ├── plotly.graph_objects          → go.Candlestick, go.Bar, go.Scatter
    └── plotly.subplots               → make_subplots (3-row chart)

ui/pages/2_swing_volume.py
│
├── src.db.data_service               → same chain as above
├── src.screener                      → StockScreener
│   ├── numpy                         → np.nan for safe division
│   └── pandas
└── ui.components.*                   → same as Dashboard
```

---

## 13. How to Run

### One-time Setup
```bash
cd nse-screener

python3 -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Configure MySQL credentials
cp .env.example .env
# Edit .env: set MYSQL_USER, MYSQL_PASSWORD, MYSQL_HOST, MYSQL_DB

# Create the database
mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS nse_screener CHARACTER SET utf8mb4;"
```

### Run the Streamlit App
```bash
.venv/bin/streamlit run ui/Dashboard.py
```

### Run ETL from CLI
```bash
# Full 3-year backfill (first time, takes ~30 minutes)
.venv/bin/python run_pipeline.py

# Daily incremental update (fast, only new days)
.venv/bin/python run_pipeline.py --incremental

# Custom lookback
.venv/bin/python run_pipeline.py --incremental --lookback 10
```

### Run Tests
```bash
.venv/bin/pytest tests/ -v
# 52 tests — all should pass
```

> **Always use `.venv/bin/streamlit` and `.venv/bin/python`.**
> System Python does not have `pymysql`, causing `SessionLocal = None` and the
> "Database session factory is uninitialized" error.

> **After every `git pull`, stop Ctrl+C and restart Streamlit.**
> Python caches modules in memory. A running Streamlit process won't pick up
> code changes until it's fully restarted.

---

## 14. Offline Mode — What Happens Without MySQL

The app never crashes when MySQL is offline. Every layer has a fallback:

| Layer | Online Behavior | Offline Behavior |
|---|---|---|
| `session.py` import | `engine` + `SessionLocal` created | `engine = None`, `SessionLocal = None` if pymysql missing |
| `init_db()` | Creates tables | Logs warning, returns silently |
| `check_connection()` | Returns `True` | Returns `False` |
| `resolve_date_range()` | Queries MySQL for max date | Falls back to Parquet cache max date |
| `load_to_db()` | Upserts all rows | Returns `([], [])`, Parquet data is preserved |
| `run_incremental()` | Returns `"success"` or `"up_to_date"` | Returns `"offline"` with info message |
| `load_raw_market_data()` | Reads from MySQL | Returns synthetic demo dataset (17 stocks) |
| Dashboard UI | Shows "LIVE MYSQL" | Shows "DEMO MODE" — never shows error page |

**Why the Parquet cache matters:**
Even if MySQL is completely unavailable, every downloaded trading day is stored as a `.parquet` file. When MySQL comes back online, `resolve_date_range()` detects the latest cached date and the ETL resumes from exactly where it left off — no re-downloads, no data loss.
