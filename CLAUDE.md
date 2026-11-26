# CLAUDE.md

Instructions for AI coding agents working in this repository. Humans can read it too.

NSE (India) end-of-day stock scanner (user-built conditions), strategy engine and backtester on daily Bhavcopy data. Long-only, delivery, no F&O. Read [docs/PROJECT_BLUEPRINT.md](docs/PROJECT_BLUEPRINT.md) for the goal, history, status and roadmap. If it disagrees with the code, the code wins: say so in the PR and fix the blueprint.

Terms: **Condition** = a saved tree of parts (prices, indicators with custom numbers, index prices, math, comparisons, groups) that is true or false per stock per day. **Strategy** = a complete trading plan. **Signal** = entry condition true at the close of day `t`. **Trade** = a signal that was taken (filled at the open of `t+1`).

## Commands

Use the project virtualenv (`.venv`). Local Python is 3.14; CI runs 3.11 and 3.12, so code must run on all three.

```bash
source .venv/bin/activate
MYSQL_HOST=127.0.0.1 MYSQL_PORT=1 pytest -q                  # full suite, offline (mirrors CI); coverage must stay >= 70%
pytest tests/test_adjust.py::test_name --no-cov              # one test (--no-cov avoids the coverage gate)
pytest -m mysql                                              # MySQL-only test; needs a live MySQL (CI runs it)
ruff check . && ruff format --check .                        # ruff is pinned to 0.17.3 (CI and pre-commit)
alembic upgrade head                                         # schema; heads must stay chained in migrations/versions/
scripts/run_app.sh                                           # daily update, then the dashboard
streamlit run ui/Dashboard.py                                # dashboard only, no update
python -m src.strategies.cli list|validate <json>|signals <json>
bash scripts/backup_db.sh                                    # compressed mysqldump to ~/nse-backups (keeps 5)
python run_pipeline.py --help                                # every data operation is a flag here
```

Gotchas:
- Tests run offline against SQLite and `tmp_path`. Fake data must use real column types (Arrow `date32` for dates). SQL written for MySQL needs a compile test or the MySQL CI job, because SQLite is not MySQL.
- Tests never touch real files. An autouse fixture in `tests/conftest.py` redirects the output folders of `config/settings.py`, and a session guard fails the run if anything under `data/` or `reports/` changed. Define every output folder once in `config/settings.py` and read it as `settings.X` when a function runs (never copy it at import or use it as a default argument). Do not run a pipeline command while tests run.
- Files live in one place each: reports in `reports/`, caches under `data/`, logs in `logs/`, DB backups only in `~/nse-backups`.
- MCP date columns come back one day early (UTC shift): use `CAST(date AS CHAR)` in queries.

## Architecture

Data flows one way:

`src/ingestion/` (Bhavcopy prices, PR-bundle market cap, securities, renames, benchmark, corporate actions; `nse_files.py` is the one polite download helper) -> MySQL (`src/db/models.py`) -> `src/etl/adjust.py` (adjusted prices) -> `src/db/data_service.py` + `src/computation/indicators/calculator.py` (indicators cached in `data/processed/indicators.parquet` plus `indicators.meta.json`) -> `src/conditions/` -> `src/strategies/` -> `ui/` (Streamlit, read-only: it reads the Parquet cache and may not import `src.db`, `src.ingestion` or `src.etl`; a test enforces it). `src/etl/daily_update.py` runs the whole chain (`python run_pipeline.py --update`, stops at the first failure). The backtest engine is not built yet.

- **Settings:** `config/settings.py` holds every tunable number (indicator window, `SPLIT_*` thresholds, file paths). Logic never hardcodes periods, thresholds or ratios.
- **Indicator cache:** only the last `INDICATOR_HISTORY_YEARS` are cached, plus warm-up bars. It holds the price columns plus the indicators listed in `PRECOMPUTED_INDICATORS` (`config/settings.py`), named as the resolver names them; any other indicator is computed on demand by `cache_reader.load_with_indicators`, which never writes the cache. It rebuilds when the DB max date, adjustment factors, benchmark date or schema change. **Bump `INDICATORS_SCHEMA_VERSION` (in `src/computation/indicators/calculator.py`) whenever cached columns or their formulas change.** Never load 10 years of all stocks into one DataFrame; backtests read per-year pieces.
- **Adjustment:** prices are adjusted only from NSE's official corporate actions (`corporate_actions` table). Manual corrections live in `config/adjustment_overrides.csv` (the override row always wins). Unexplained jumps are only reported, never changed. Re-run `--rebuild-adjusted` after every corporate-actions download. Raw `close` is never modified.
- **Stocks screened:** all regular series (EQ, BE, BZ, SM, ST) are loaded, delisted ones included; price history is never deleted. Market cap is NULL before 2024-02-01 and is never guessed.
- **Symbol renames:** raw `daily_prices.symbol` is never relabelled. Renames live in `symbol_changes` and are joined at read time (`prices_for` in `src/ingestion/renames.py`).
- **DB layer:** `src/db/upsert.py` is the dialect-agnostic upsert. A function that takes a session uses it as given and does not commit it; the caller commits. Use `use_session` from `src/common/db.py` for "use this session or open one".
- **Conditions and strategies:** conditions are JSON trees (`src/conditions/`: nodes, evaluation, save and load). Strategies are `StrategySpec` JSON files in `strategies/library/` whose entry is a condition tree. See `docs/strategy_engine.md` and `docs/condition_engine.md`.

## Rules that must not change

- **Backtests:** signal on day `t` close, fill at `t+1` open; stop and target in the same bar means the stop was hit first; gaps exit at the open; costs and slippage always on (Zerodha rates, 10 bps slippage unless configured). Every result carries a `survivorship_biased` flag.
- **No look-ahead:** a value on day `t` may only use data up to the close of `t`. Add a test for every shifting or rolling calculation, and shift within each symbol.
- **NSE data:** probe every calendar day (special sessions exist); the date inside a downloaded file must equal the requested date; a 404 within the last 3 days is never cached as a holiday; never delete price history; after inserting historical rows, rebuild adjusted prices for those symbols. Full list: skill `nse-data-integrity`.

## Code and tests

- Type hints on public functions, short single-purpose functions, docstrings that say what and why, a short comment wherever logic is not obvious. Plain names. Details: skill `code-standards`.
- Catch specific exceptions only; log with `logging`, never `print` in library code.
- Seed every random process. Prefer vectorised pandas; process large data in chunks.
- Add a dependency only when needed, pinned exactly in `requirements.txt`, and justify it in the PR.
- Every bug fix gets a regression test that fails before the fix. Every new CLI flag, table or setting gets a test. Never weaken or delete a test of a rule that stays.
- Update `README.md` and `docs/` whenever a command, table, column, setting or workflow changes.

## Git and pull requests

- Branch from an up-to-date `main`: `fix/<name>`, `feat/<name>`, `docs/<name>`, `chore/<name>`. One task = one branch = one PR. Never commit on `main`. Check the branch before every commit and push (skill `branch-guard`).
- Conventional Commits. The PR title is short and describes the change: `<type>: <what it does>`.
- PR description sections, in order: **Summary, Changes, Commands** (one purpose line above each command, e.g. "Run the full test suite offline:"), **Verification** (real results), **Not verified**. Add `Fixes #N` only for issues the PR closes. Describe only what was created or fixed.
- **Never merge a pull request.** Open it and stop: no `gh pr merge`, no auto-merge, no push to `main`, no force-push, no rewriting shared history.
- Before every push: tests, `ruff check .` and `ruff format --check .` must pass. Do not change `.github/workflows/` without calling it out in the PR.
- File a GitHub issue (skill `github-issue`) for any problem outside the task instead of silently fixing it. A tiny safe fix (typo, lint warning) in a file the PR already touches may go in as its own commit.

## Working on the real database and safety

- The repository is public. Never commit secrets, `.env`, data exports (`.xlsx`, `.csv`) or personal data. Passwords live only in `.env`; never print them.
- MCP servers (`.mcp.json`): `mysql` is read-only (use it to check and verify), `mysql_write` allows INSERT, UPDATE, DELETE only, `github` handles issues and PRs.
- Before the first write, migration or pipeline command against the real database, run `bash scripts/backup_db.sh`. Every UPDATE and DELETE has a `WHERE`; run the same condition as a `SELECT` first. After writing, run a `SELECT` that proves the result.
- Ask first before: deleting or rewriting `daily_prices` history, `DROP` or `TRUNCATE`, restoring a backup over live data, or any change touching more than 10,000 rows. State the rows affected and how to undo it.
- Never run `rm -rf`, `git reset --hard` or `git clean -fd` on anything you did not create, and never delete files in `data/`.
- Stop and report instead of pushing on when the same command fails after three attempts, a change needs a decision, or you would touch files outside the task.
- Say what you did not verify.

Skills (`.claude/skills/`): `branch-guard`, `code-standards`, `deep-cleanup`, `nse-data-integrity`, `db-migration`, `github-issue`. Command: `/pr-ready`.
