## Phase 0 & Phase 1 Foundation: Safety Net and Data Foundation (T01–T09)

### Summary of Changes
This pull request delivers Phase 0 (safety net & quick wins) and Phase 1 (data foundation) of the upgrade plan for the `nse-screener` application. It establishes robust CI/CD, fixes critical calculation bugs in indicators, replaces deprecated methods, introduces database migrations via Alembic, implements corporate action price adjustments (splits/bonuses), ingests benchmark indices, tracks point-in-time index constituent membership, removes synthetic demo data in favor of live data with pre-computed Parquet caching, and adds the custom "HMM" indicator.

---

### Task Checklist (T01 – T09)

- [x] **T01: Dependencies & Tooling**: Pinned exact dependency versions in `requirements.txt` and created `requirements-dev.txt` (`alembic`, `pytest`, `pytest-cov`, `ruff`, `pre-commit`). Configured `pyproject.toml` for Ruff (100-character line limit, Python 3.11 target, modern linting/formatting rules). Configured `.pre-commit-config.yaml`.
- [x] **T02: Modern CI Pipeline**: Upgraded GitHub Actions workflow `.github/workflows/python-app.yml` from Python 3.10 to Python 3.11, matching README and Dockerfile. Replaced flake8 with fast Ruff linting and formatting checks. Enabled pytest with coverage reporting.
- [x] **T03: Naming Alignment**: Renamed "strategy" terminology to "screener" across the codebase (`src/screener.py` functions `screen_swing_volume`, `screen_swing_momentum`; UI navigation and components). Added backwards-compatible deprecation aliases.
- [x] **T04: Bhavcopy PrevClose & Alembic Migrations**: Documented NSE Bhavcopy headers in `docs/bhavcopy_columns.md`. Updated `downloader._parse()` to extract `PREV_CLOSE`/`PREVCLOSE`. Bootstrapped Alembic migrations (`migrations/001_baseline_schema.py` and `002_add_prev_close_to_daily_prices.py`). Deprecated `scripts/migrate_add_index_name.py`.
- [x] **T05: Corporate Action Price Adjustment Engine**: Created `src/etl/adjust.py` detecting splits/bonuses using `abs(ratio - 1.0) > 0.005`, generating cumulative adjustment factors, and populating `adj_factors` table and `adj_*` columns on `daily_prices`. Added report export to `reports/adj_events_review.csv`. Added Alembic migration `003_add_adj_factors_and_adjusted_prices.py`.
- [x] **T06: Benchmark Index Tracking**: Created `src/ingestion/benchmark.py` with `BenchmarkSource` interface and `NSEIndexSource` implementation (fetching from NSE archives `ind_close_all_DDMMYYYY.csv`), with fallback stub `YahooFinanceSource`. Added `benchmark_prices` table and migration `004_create_benchmark_prices.py`. Added `--benchmarks` CLI flag.
- [x] **T07: Technical Indicator Corrections & HMM Indicator**:
  - Replaced simple rolling mean RSI with Wilder's exponential smoothing RSI(14).
  - Added Wilder's Average True Range (ATR-14).
  - Added Donchian Channel High/Low/Mid.
  - Added true cross-sectional time-series crossover detection (`golden_cross`, `death_cross`, `bullish_macd_crossover`) using per-symbol `shift(1)`.
  - Added volume ratio (`volume / volume_sma_20`) and relative strength against NIFTY 500 benchmark.
  - Added custom **HMM indicator** combining RSI(9), WMA(21), EMA(20) > SMA(50) > SMA(200), Close > SMA(200) & SMA(50), 252D high proximity, and volume expansion (> 1.5x 20D SMA with volume > 250k).
- [x] **T08: Point-in-Time Index Membership**: Created `index_membership` table with Alembic migration `005_create_index_membership.py`. Extended `src/ingestion/index_constituents.py` with `snapshot_index_constituents()` and `get_index_constituents()`. Automatically snapshots constituents during ETL runs.
- [x] **T09: Unified Data Service & Performance Parquet Cache**:
  - Completely removed synthetic demo dataset generator (`generate_demo_dataset`).
  - Implemented pre-computed Parquet indicator cache at `data/processed/indicators.parquet` with atomic writing.
  - Created unified data loader `load_indicator_snapshot` and UI helper `get_market_data()` used across all Streamlit pages (`Dashboard.py`, `2_swing_volume.py`, `3_swing_momentum.py`).
  - Added auto-initialization in `ensure_data_initialized()` to run backend ETL automatically if DB is uninitialized or build missing Parquet cache without user intervention.
  - Fixed pipeline date desync where MySQL date is authoritative over cache date.

---

### External Details Verified

1. **NSE Endpoints & Headers**:
   - Modern full bhavdata CSV: `https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv` (contains `PREV_CLOSE`).
   - Historical ZIP: `https://nsearchives.nseindia.com/content/historical/EQUITIES/YYYY/MON/cmDDMONYYYYbhav.csv.zip` (contains `PREVCLOSE`).
   - Daily Index closing file: `https://nsearchives.nseindia.com/content/indices/ind_close_all_DDMMYYYY.csv`.
2. **Wilder Smoothing**: Verified exact recursive formulation: $\text{ATR}_t = \frac{\text{ATR}_{t-1} \cdot 13 + \text{TR}_t}{14}$.
3. **Database Consistency**: Alembic migrations 001–005 applied cleanly on MySQL 8.0, with SQLite compatibility retained for testing.

---

### How to Review

1. **Verify Automated CI**: GitHub Actions workflow runs `ruff check`, `ruff format --check`, and `pytest -q --cov=src` across all modules.
2. **Review Migrations**: Inspect `migrations/versions/` to verify table schemas and indexes.
3. **Review Indicator Logic**: Check `src/computation/indicators.py` for Wilder RSI, ATR, Donchian, relative strength, and the HMM indicator.
4. **Review UI & Data Service**: Check `src/db/data_service.py` and `ui/Dashboard.py` to confirm demo data removal, Parquet caching, and auto-initialization.

---

### Follow-ups (Out of Scope for this PR)
- Add a strategy/screener registry with dynamic plugin registration.
- Add backtesting and performance analytics engine.
- Implement historical performance tracking tables for screeners.
- Build forecasting models (ARIMA / Prophet / ML) for price action.
- Add specialized multi-timeframe weekly/monthly indicator resampling.
