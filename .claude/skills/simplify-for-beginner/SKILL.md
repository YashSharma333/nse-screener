---
name: simplify-for-beginner
description: Use for EVERY task that edits code or tests in this repo (refactors, bug fixes, new features, cleanup). Rules for how code must look so the owner (a Python beginner) can read and diagnose it (C1-C12), the test-as-you-go loop (T1-T6) and the test-suite rules (S1-S7). Also says how to end a task (rule B9).
---

# Simplify for the beginner

The owner is learning Python through this project. Quality means: **could the owner read
it, understand it and diagnose it?** There is no line limit. These rules sit on top of
`CLAUDE.md`; they never weaken it.

## Code rules (C)

| # | Rule |
|---|---|
| C1 | A function does ONE job and reads top to bottom like a short story. No line limit: 15 or 100 lines is fine if every part is easy to follow; if you have to explain it in pieces, split it into named steps. Plain names (`get_latest_date`). No functions nested inside functions, no `nonlocal`, no walrus tricks, no decorators, TypeVars or metaclasses unless truly unavoidable. |
| C2 | No function parameter that only tests use. A production function takes what production needs; tests use `monkeypatch`. |
| C3 | **One concept, one place.** Before writing a helper, search the whole repo (and `src/common/` once it exists, task CL-18). A second copy is a bug. |
| C4 | Vectorised pandas, never a Python loop over stocks: one `groupby('symbol')` over all stocks. Every `shift` and `rolling` happens inside `groupby('symbol')`, so one stock never leaks into another and no value uses the future. |
| C5 | Catch specific exceptions only. Log with `logging`, never `print` in library code. No silent `except`. |
| C6 | Complex but correct code stays, with a comment `# KEEP:` saying why and what it protects (the upsert key grouping, the atomic Parquet write, the safe rule parser). |
| C7 | No hard-coded numbers in logic; they live in `config/settings.py`. |
| C8 | A refactor changes NO behaviour. A behaviour change is its own task and its own PR. |
| C9 | A short "why" comment wherever the logic is not obvious. Docstrings say what and why. |
| C10 | **One function per indicator** (the indicator tasks CL-23 to CL-25 build this; follow it for any new indicator). Its default numbers live ONLY in the function signature (`sma(df, length=20, source='close')`). Custom numbers are arguments passed through the resolver; never write `sma_50()` and `sma_200()` as separate functions. |
| C11 | Price windows (52-week high, 20-day low, average volume) come from ONE window helper; values from the past from ONE picker (value N days ago, value on a date). Nothing recomputes them by hand. |
| C12 | Precompute only the owner-approved list (`settings.PRECOMPUTED_INDICATORS`). Every other indicator is computed on demand, never stored by default. |

Example, C2 (remove parameters that only tests use). Before: four fake functions so tests
can replace the network.

```python
def load_raw_prices(start, end, session=None, fetch_fn=None, parse_fn=None, sleep_fn=None): ...
```

After: production takes what it needs; the test replaces the function with `monkeypatch`.

```python
def load_raw_prices(start, end, session): ...
```

```python
def test_load_skips_holiday(monkeypatch):
    monkeypatch.setattr(nse_files, "fetch", lambda url: raise_no_file())
```

Example, C4 (one groupby, shift inside the group):

```python
g = df.groupby("symbol")["close"]
df["sma_50"] = g.transform(lambda s: s.rolling(50, min_periods=50).mean())
df["close_prev"] = g.shift(1)  # inside the group: no leak between stocks
```

## The test loop (T): test as you go, always

| # | Rule |
|---|---|
| T1 | **Before editing**, run the tests of the files you will touch. They must be green. If not, stop: a failure that already exists is its own issue. |
| T2 | **After EVERY edit**, run the narrowest test: `pytest tests/test_x.py::test_name --no-cov -q`. Red means fix it before the next edit. Never stack unverified edits. |
| T3 | **End of task:** `MYSQL_HOST=127.0.0.1 MYSQL_PORT=1 pytest -q`, then `ruff check .` and `ruff format --check .`, coverage at least 70% (aim for 85%+), then the golden check (T6). Never push red. |
| T4 | **Bug fixes:** write the regression test FIRST and watch it fail, then fix, then watch it pass. A test that never failed proves nothing. |
| T5 | **Break-fix protocol:** read the failure message. If the cause is unclear, restore ONLY the files you edited since the last green run (`git restore <those files>`) and redo the change in smaller steps. After 3 honest attempts stop, file an issue labelled `needs-user-input` with the exact failing output (skill `github-issue`), and report. |
| T6 | **Golden check** (refactors: zero behaviour change). The owner's tool `golden.py` (kept outside this repo; the task prompt gives its path and the `base.json` snapshot) compares the last 5 trading dates: row count and a hash of every indicator column, plus the pass-list of every registered screener. Run `check` after the task. Retained outputs must be identical. Deliberate changes happen only in tasks that say so, with a before/after table the owner approves. Re-snapshot first if `--update` ran since the snapshot. |

## Test-suite rules (S): refactor tests together with the code

| # | Rule |
|---|---|
| S1 | When you delete a function, delete every test that only exercised it, in the SAME PR, and say so in the PR (number of tests before and after). |
| S2 | Rewrite the remaining tests to the new names and structure. Tests call public functions and use `monkeypatch` and `tmp_path`. |
| S3 | Test files are named after what they test (`test_adjust.py`), never after a task (`_d10`, `phase3`). One test file per module or area. |
| S4 | **Never delete or weaken a test of a rule we keep:** no look-ahead, the date inside a file equals the requested date, price history is never deleted, signal at close and fill at next open, upsert NULL handling, date32 Parquet filters, the real-files guard. |
| S5 | Fake test data uses real column types (Arrow `date32` for dates). SQL written for MySQL needs a compile test or the MySQL CI job, because SQLite is not MySQL. |
| S6 | Tests never use the network or real files. A test slower than 5 seconds is shrunk or marked `slow` / `perf`. |
| S7 | Identify tests we no longer need and drop them: the code was removed, the test is a duplicate, or a new helper test covers the same behaviour. Every PR lists the tests it deleted and why. Covered twice means dropped once. Tests of kept rules stay (S4). |

## Ending a task (rule B9: chat hygiene)

End the final message with **`Next: CL-xx`** (the next task id from the task prompt). Then:

- Mention `/clear` or a new chat ONLY if the next task does not need this chat (for example
  the task changed rules or skills, wrote to the real database, or ended a milestone, and
  the task prompt says so).
- Mention `/compact` or the Resume prompt ONLY if the task is unfinished AND the context is
  long (above about 60%).
- Never suggest clearing while there is uncommitted work. Say nothing about clearing in any
  other case.
