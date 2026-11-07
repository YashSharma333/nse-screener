# NSE Screener — Complete Project Guide

> A full-stack quantitative stock screener for NSE equities. Downloads daily price data from NSE, stores it in MySQL with split adjustments, computes technical indicators across price, volume, crossovers, and momentum, and presents it through a Streamlit dashboard with rule-based screening strategies.

---

## Roadmap & Phase Status

- **Phase 0: Foundation & Ingestion (T01–T03)** — **DONE**
  - Robust NSE Bhavcopy downloader with retries, session warmups, and dual URL patterns.
  - Parquet caching under `data/raw/bhavcopy_cache/`.
  - NIFTY index constituent scraper and membership tracking.
- **Phase 1: Persistence & Indicators (T04–T09)** — **DONE**
  - Database schema & Alembic migration lifecycle (`migrations/versions/`).
  - Corporate action detector & adjustment engine (`src/etl/adjust.py`).
  - Official NSE benchmark index ingestion (`src/ingestion/benchmark.py`).
  - Pre-computed Parquet indicator caching with multi-factor freshness sidecar (`indicators.meta.json`).
  - Removal of synthetic demo mode in favor of production real-world market feeds.
- **Phase 2: Screener Platform (T10–T12)** — **IN PROGRESS**
  - Hardening fixes (F1–F12).
  - Modular indicator domain packaging by nature (`price`, `volume`, `crossovers`, `momentum`).
  - Pluggable Screener Registry & Base Class with grouped screening conditions.
  - Systematic screeners (Minervini, Breakout 52W, RSI Pullback, VCP Lite, RS Leaders, RSI Momentum, etc.).
  - Screener Explorer UI page with near-miss diagnostics and export capabilities.

> [!IMPORTANT]
> **Data Availability:** Synthetic demo-mode data was removed in Phase 1; live MySQL or Parquet cache is required to run the application. Visiting the UI on an uninitialized database presents a setup callout with exact Alembic and CLI commands.

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
│   │   ├── adjust.py        # Corporate action split/bonus detector & adjuster
│   │   └── pipeline.py      # BhavcopyETL: chunked backfill, incremental, DB upserts
│   │
│   ├── computation/
│   │   └── indicators/      # Domain-specific indicator engines:
│   │       ├── price.py     # SMA, EMA, WMA, Bollinger Bands, ATR, 52W High/Low
│   │       ├── volume.py    # Volume SMA, volume ratio, volume dryup
│   │       ├── crossovers.py# Golden/Death Cross, MACD crossovers
│   │       ├── momentum.py  # RSI, MACD, Relative Strength vs benchmark, ranks
│   │       └── calculator.py# TechnicalCalculator facade
│   │
│   └── screeners/           # Extensible Screener Platform:
│       ├── base.py          # Screener base class, condition grouping & explainability
│       ├── registry.py      # @register_screener decorator & get_screener() lookup
│       ├── conditions/      # Reusable condition primitives grouped by nature:
│       │   ├── price.py     # Price conditions (MAs, 52W proximity, ATR)
│       │   ├── volume.py    # Volume conditions (surge, liquidity, dryup)
│       │   ├── crossover.py # Crossover conditions (Golden Cross, MACD)
│       │   └── momentum.py  # Momentum conditions (RSI, RS, ranks, return caps)
│       ├── minervini.py     # Minervini Stage 2 Trend Template screener
│       ├── breakout_52w.py  # 52W High Breakout + Volume screener
│       ├── rsi_pullback.py  # RSI Pullback in Uptrend screener
│       ├── vcp_lite.py      # Volatility Contraction Pattern screener
│       ├── rs_leaders.py    # Relative Strength Leaders screener
│       ├── composite_trend.py # Composite Trend Regime screener
│       ├── rsi_momentum.py  # RSI Momentum (RSI 9 > WMA 21) screener
│       ├── swing_volume.py  # Swing + Volume screener
│       ├── swing_momentum.py # Swing With Momentum screener
│       └── classic.py       # 11 classic technical filter screeners
│
├── ui/
│   ├── app.py               # Streamlit router (entry point)
│   ├── data.py              # Decoupled UI data service with multi-factor caching
│   ├── Dashboard.py         # Page 1: full market snapshot + ETL trigger button
│   ├── pages/
│   │   ├── 2_swing_volume.py    # Page 2: Swing + Volume strategy results
│   │   ├── 3_swing_momentum.py  # Page 3: Swing With Momentum strategy results
│   │   └── 4_screener_explorer.py # Page 4: Interactive Screener Explorer & Diagnostics
│   └── components/
│       ├── terminal_styles.py   # CSS theming, dark terminal look
│       ├── screener_view.py     # Reusable screener page renderer with near-miss tabs
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
LOGS_DIR = PROJECT_ROOT / "logs"
LOG_FILE = LOGS_DIR / "screener.log"
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
class Base(DeclarativeBase):
    pass


class DailyPrice(Base):
    __tablename__ = "daily_prices"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    open = Column(Numeric(10, 2))
    high = Column(Numeric(10, 2))
    low = Column(Numeric(10, 2))
    close = Column(Numeric(10, 2))
    volume = Column(BigInteger)
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
load_dotenv(PROJECT_ROOT / ".env")  # reads MYSQL_USER, PASSWORD, HOST, PORT, DB

DATABASE_URL = f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}"

try:
    engine = create_engine(
        DATABASE_URL,
        pool_size=10,
        max_overflow=20,
        pool_recycle=3600,  # recycle stale connections every hour
        pool_pre_ping=True,  # test conn before handing it out
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
except (ImportError, Exception):
    engine = None  # pymysql not installed or other startup error
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
result.symbols  # {'RELIANCE', 'TCS', 'DIXON', ...}  (~500 total)
result["RELIANCE"]  # → "NIFTY 100"
result["DIXON"]  # → "NIFTY Midcap 150"
```

The ETL uses this as a symbol filter — only rows matching these ~500 symbols are stored in MySQL. This keeps the DB focused and avoids noise from penny stocks and micro-caps.

---

## 7. ETL Pipeline

### `src/etl/pipeline.py` — `BhavcopyETL`

The **orchestrator** that ties ingestion → filter → load into one flow.

```python
etl = BhavcopyETL()
etl.run()  # full pipeline
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

## 8. Computation Layer — Indicators & Screener Framework

### `src/computation/indicators/` — Domain Modular Indicators

Indicators are decomposed into dedicated submodules by their mathematical and analytical nature:

1. **Price Indicators (`src/computation/indicators/price.py`)**:
   - `calculate_sma(df, window)` — Simple Moving Average (20, 50, 200).
   - `calculate_ema(df, window)` — Exponential Moving Average (12, 20, 26).
   - `calculate_wma(df, window)` — Linearly Weighted Moving Average.
   - `calculate_bollinger_bands(df, window, num_std)` — Upper, middle, lower Bollinger Bands.
   - `calculate_atr(df, window)` — Average True Range (volatility).
   - `calculate_donchian_channels(df, window)` — Upper and lower price channels.
   - `calculate_52_week_high / low` — 252-day rolling extreme levels.
   - `calculate_pct_change` — Multi-horizon returns (1D, 1M, 3M, 6M, 12M).

2. **Volume Indicators (`src/computation/indicators/volume.py`)**:
   - `calculate_volume_sma(df, window)` — Moving average of traded volume.
   - `calculate_volume_ratio(df, window)` — Relative volume expansion ratio (`volume / volume_sma`).
   - `calculate_volume_dryup(df, window, threshold)` — Institutional contraction flag.

3. **Crossover Indicators (`src/computation/indicators/crossovers.py`)**:
   - `calculate_golden_cross(df, fast, slow)` — Fast MA crossing above Slow MA.
   - `calculate_death_cross(df, fast, slow)` — Fast MA crossing below Slow MA.
   - `calculate_macd_crossover(df)` — MACD line crossing signal line.

4. **Momentum & Relative Strength (`src/computation/indicators/momentum.py`)**:
   - `calculate_rsi(df, window)` — Relative Strength Index (Wilder's RSI 14).
   - `calculate_macd(df)` — MACD line, signal line, and histogram.
   - `calculate_relative_strength(df, benchmark_df, lookback)` — Stock return relative to benchmark return: `(stock_perf / bench_perf) - 1`.
   - `calculate_cross_sectional_ranks(df, column)` — Percentile rank within the market universe.

5. **Calculator Facade (`src/computation/indicators/calculator.py`)**:
   - `TechnicalCalculator.apply_all_indicators(df, benchmark_df)`:
     Processes stock time-series cleanly grouped by symbol without inter-symbol bleed or future data lookahead. Emits schema version 2 with all columns pre-computed.

---

### Quantitative Screener Platform (`src/screeners/`)

The screener engine provides an extensible, pluggable screener architecture where screening conditions are categorized strictly by nature:
- **`price_conditions(df)`**: Evaluates moving average alignment, 52-week proximity, price bounds, and volatility contraction.
- **`volume_conditions(df)`**: Evaluates volume surges, liquidity floors, and dry-up ratios.
- **`crossover_conditions(df)`**: Evaluates Golden/Death crosses, MACD signal crosses, and recent crossover event windows.
- **`momentum_conditions(df)`**: Evaluates RSI support zones, benchmark Relative Strength, cross-sectional return percentile ranks, and return velocity bounds.

#### Explainability & Near-Miss Diagnostics
In addition to `.run(df)` returning qualifying equities, every screener implements `.explain(df)`, which augments the snapshot with:
- Individual boolean columns per condition (`cond_{name}`)
- Category summary booleans (`pass_price`, `pass_volume`, `pass_crossover`, `pass_momentum`)
- `conditions_passed` and `conditions_total` counts
- `near_miss`: Flagged `True` when **exactly 1 condition failed**, identifying bottleneck conditions
- `near_miss_on`: The specific condition that prevented qualification

#### Registered Systematic Screeners

| Key | Status | Category | Plain-Language Rules Summary |
|---|---|---|---|
| `minervini_trend_template` | `core` | `trend` | Mark Minervini 8-point Stage 2 trend: Close > 150/200 SMA, 150 SMA > 200 SMA, 200 SMA rising 20d, 50 SMA > 150/200 SMA, Close > 50 SMA, Close >= 1.3x 52W Low, within 25% of 52W High. |
| `breakout_52w_volume` | `core` | `breakout` | High-momentum 52-week breakout: Close within 2% or breaking 252d High with >=2.0x 20d SMA volume surge and liquidity >= 100k shares. |
| `rsi_pullback_uptrend` | `core` | `pullback` | Low-volume orderly pullback in confirmed uptrend: Close > 50 SMA > 200 SMA, RSI(14) resting in 40–55 bull support zone, volume dryup <= 0.8x 20d SMA. |
| `vcp_lite` | `core` | `pattern` | Volatility Contraction Pattern: Close > 50 SMA > 200 SMA, within 10% of 52W High, ATR(14)% < 3.5% (tightness), drying volume <= 0.7x 20d SMA. |
| `rs_leaders` | `core` | `momentum` | True market outperformance: Close > 50 SMA > 200 SMA, Relative Strength vs NIFTY 500 (63d) > 10% or 252d percentile rank >= 80, liquidity >= 100k shares. |
| `composite_trend` | `core` | `trend` | Multi-indicator trend regime confluence: Close > 20 EMA > 50 SMA > 200 SMA, RSI(14) > 55, MACD > Signal, volume >= 20d SMA. |
| `rsi_momentum` | `core` | `momentum` | Fast momentum shift: Close > 50 SMA, RSI(9) > WMA(21) of RSI(9) by >= 2.0 pts, liquidity >= 100k shares. *(Formerly referred to as HMM).* |
| `swing_volume` | `core` | `swing` | Flagship swing volume surge: 20 EMA > 50 SMA > 200 SMA, Close > 50 & 200 SMA, within 15% of 252d High, volume >= 1.5x 20d SMA, volume SMA >= 250k. |
| `swing_momentum` | `core` | `swing` | Flagship swing momentum: All Swing Volume criteria plus 120d return >= 30% and 60d return <= 40% (anti-chase filter). |
| `above_50_sma` | `experimental` | `classic` | Trend filter: Close strictly above 50-day Simple Moving Average. |
| `above_200_sma` | `experimental` | `classic` | Long-term regime: Close strictly above 200-day Simple Moving Average. |
| `golden_cross` | `experimental` | `classic` | Bullish regime shift: 50 SMA > 200 SMA with Golden Cross within last 10 sessions. |
| `death_cross` | `experimental` | `classic` | Bearish regime shift: 50 SMA < 200 SMA with Death Cross within last 10 sessions. |
| `near_52w_high` | `experimental` | `classic` | Proximity check: Close within 5% of 252-day rolling High. |
| `near_52w_low` | `experimental` | `classic` | Deep discount / rebound check: Close within 5% of 252-day rolling Low. |
| `rsi_oversold` | `experimental` | `classic` | Mean-reversion candidate: 14-day RSI strictly below 30. |
| `rsi_overbought` | `experimental` | `classic` | Momentum extreme: 14-day RSI strictly above 70. |
| `bullish_macd_crossover` | `experimental` | `classic` | MACD momentum trigger: MACD line crossed above Signal line within last 5 sessions. |
| `high_volatility` | `experimental` | `classic` | Volatility expansion: 20-day annualized close-to-close volatility >= 40%. |
| `bollinger_squeeze` | `experimental` | `classic` | Volatility squeeze: Bollinger Bandwidth (Upper - Lower)/SMA strictly < 5.0%. |

---

## 9. Data Service

### `src/db/data_service.py`

Bridge between MySQL / Parquet cache and the UI layer:
- **`load_raw_market_data()`:** Loads raw OHLCV prices from MySQL (or Parquet fallback).
- **`load_indicator_snapshot(use_adjusted=True, force_refresh=False)`:**
  Atomically maintains `data/processed/indicators.parquet` alongside `indicators.meta.json`. Validates cache freshness against DB maximum date, row counts, and corporate action counts.
- **`get_market_data()`:** Returns a dictionary containing the latest cross-sectional snapshot, full indicators frame, live status, and telemetry messages.

---

## 10. UI Layer & Caching

### `ui/data.py` — Decoupled UI Cache Manager
Streamlit pages import from `ui.data`:
- `@st.cache_data(ttl=300)` wrapper takes a cache-invalidation token: `(parquet_mtime, meta_sha256)`.
- If background ETL runs and updates `indicators.parquet` or `indicators.meta.json`, Streamlit automatically invalidates its cache on the next run.
- **`render_uninitialized_prompt()`:** If the database is empty or offline, displays an informative callout with exact Alembic and CLI commands (`alembic upgrade head`, `python run_pipeline.py --backfill --days-back 365`) and an interactive button guarded by session state.

### `ui/Dashboard.py` & Screener Pages
- **`Dashboard.py`**: Full market snapshot, interactive column selector, index multiselect, and search.
- **`pages/2_swing_volume.py`**: Swing + Volume qualified candidates.
- **`pages/3_swing_momentum.py`**: Swing With Momentum qualified candidates.
- **`components/stock_inspector.py`**: 3-panel interactive Plotly candlestick, volume, and RSI chart.
```
- Calls `check_connection()`
- If **online**: reads stored price history and indicator cache
- If **uninitialized or offline**: returns empty snapshot with clear status message prompting backfill

**`compute_market_indicators(raw_df)`:**
```python
with_indicators = compute_market_indicators(raw_df)
```
Groups by symbol, calls `TechnicalCalculator.apply_all_indicators()` on each group, and concatenates results. Returns full time-series with all 35+ indicator columns across price, volume, crossover, and momentum domains.

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
st.session_state.etl_status  # {"status": "success"|"offline"|..., "message": ...}
st.session_state.is_updating_etl  # bool — disables the button while ETL runs
```

**`get_market_data()`** — imported from `ui.data` with `@st.cache_data(ttl=300)` and cache token invalidation.

**Dashboard displays:**
- Header with LIVE MYSQL / DATABASE OFFLINE status indicator
- 4 KPI tiles: equity count, mean daily change, advance/decline, median RSI
- Filterable data table (by Index, by symbol search, by columns to display)
- CSV export button
- Stock Inspector chart at the bottom

### `ui/pages/2_swing_volume.py` and `3_swing_momentum.py`

Both share the same session cache as Dashboard (so switching pages doesn't reload data):
```python
data = get_market_data()  # reads session cache
snapshot = data["snapshot"]
screener = get_screener("swing_volume")
qualifying = screener.run(snapshot)  # apply filter rules
```
Display: KPI tiles, qualifying stocks table, CSV export, StockInspector.

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

3. Dashboard: get_market_data() called via ui/data.py
   → get_cache_invalidation_token() checks indicators.parquet and indicators.meta.json
   → load_indicator_snapshot()
     → check_connection() → SUCCESS (MySQL is up)
     → If daily_prices is empty:
       UI displays friendly setup banner with exact terminal commands:
       `alembic upgrade head`
       `python run_pipeline.py --backfill --days-back 365`

4. User runs backfill or clicks "🔄 Update Latest Market Data":
   → BhavcopyETL() created:
       • init_db() ensures database connectivity
       • NSEBhavcopyDownloader() warms up NSE session
   → etl.run_backfill(days_back=365, chunk_days=100):
       • Computes candidate weekdays in window
       • Subtracts stored dates from daily_prices
       • Subtracts confirmed non-trading days from non_trading_days.json
       • Downloads missing days in 100-day bounded chunks
       • Vectorizes records and upserts chunk immediately
       • Executes rebuild_adjusted(), load_benchmarks(), and refresh_indicator_cache() once
   → Cache token updates → Dashboard reloads with fresh indicators
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

ui/pages/2_swing_volume.py, 3_swing_momentum.py, 4_screener_explorer.py
│
├── ui.data                           → get_market_data(), render_uninitialized_prompt()
├── src.screeners                     → get_screener(), list_screeners()
└── ui.components.screener_view       → render_screener_page()
    ├── src.screeners.registry
    └── ui.components.stock_inspector → render_stock_inspector()
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

### Run Migrations & Ingestion from CLI
```bash
# Run database schema migrations
alembic upgrade head

# Backwards backfill historical data in chunks (e.g. 1 year)
.venv/bin/python run_pipeline.py --backfill --days-back 365

# Full historical backfill (default 1100 days, ~3 years)
.venv/bin/python run_pipeline.py --backfill

# Daily incremental update (fast, only new days)
.venv/bin/python run_pipeline.py --incremental

# Standalone corporate actions recomputation
.venv/bin/python run_pipeline.py --rebuild-adjusted

# Standalone benchmark index ingestion
.venv/bin/python run_pipeline.py --benchmarks

# Rebuild indicators snapshot cache
.venv/bin/python run_pipeline.py --refresh-cache
```

### Run Tests & Verification
```bash
# Full test suite with coverage
.venv/bin/pytest tests/ -v

# Linter and code style formatting checks
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

---

## 14. Offline & Missing Data Handling

The app never crashes when MySQL is offline or data is uninitialized:

| Layer | Online Behavior | Offline / Empty Behavior |
|---|---|---|
| `session.py` import | `engine` + `SessionLocal` created | `engine = None`, `SessionLocal = None` if credentials or driver missing |
| `init_db()` | Ensures DB tables exist | Logs warning, returns silently |
| `check_connection()` | Returns `True` | Returns `False` |
| `resolve_date_range()` | Queries MySQL for max date | Falls back to Parquet cache max date |
| `load_to_db()` | Upserts all rows | Returns `([], [])`, Parquet raw data is preserved |
| `run_incremental()` | Returns `"success"` or `"up_to_date"` | Returns `"offline"` with informational message |
| `load_indicator_snapshot()` | Reads from DB + updates Parquet cache | Falls back to cached Parquet snapshot; returns empty frame if no cache |
| Dashboard UI | Shows "LIVE MYSQL" | Displays setup banner with exact CLI commands (`alembic upgrade head`, `python run_pipeline.py --backfill`) |

**Why the Parquet cache matters:**
Even if MySQL is completely unavailable, every downloaded trading day is stored as a `.parquet` file. When MySQL comes back online, `resolve_date_range()` detects the latest cached date and the ETL resumes from exactly where it left off — no re-downloads, no data loss.
