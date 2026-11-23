---
name: deep-cleanup
description: Use when deleting code, tests, files or branches, at the end of every task (quick repo checks) and for the weekly repository audit. Gives the deletion rules (D1-D5), how to find dead code, the DEAD / TEST-ONLY / KEEP table for the PR, and the repository tidiness checklist. Report first; delete only what is verified safe. Also says how to end a task (rule B9).
---

# Deep cleanup

Goal: no clutter, ever. Delete what is verifiably unused, keep what the project needs,
and clean a little in every task so there is never a big mess.

## Deletion rules (D)

| # | Rule |
|---|---|
| D1 | **Dead** means: no reference in `src`, `ui`, `scripts`, `run_pipeline.py` or the docs. **Test-only** (used only by tests) also counts as dead. |
| D2 | Before deleting, write a table in the PR: each item as `DEAD`, `TEST-ONLY` or `KEEP`, with the reason. Items marked "ask" need the owner's yes. |
| D3 | Never delete price history or any data file that is not on the approved list. No `rm -rf`. Regenerable caches only with approval. |
| D4 | Fix docs, skills, README and comments that mention deleted things in the SAME PR. |
| D5 | Keep code the backtest engine will clearly need (risk and cost functions) and mark it `# KEEP: used by the backtest engine`. |

Tests follow the code (see skill `simplify-for-beginner`, rules S1, S4, S7): delete the
tests that only exercised deleted code in the same PR, and never delete a test of a rule
we keep.

## How to find dead code

1. **ruff:** `ruff check --select F,ARG --no-fix src ui scripts run_pipeline.py` (unused
   imports, variables and arguments).
2. **Name scan:** list every top-level function and class, count where its name appears.
   Zero uses in code = `DEAD`; used only in `tests/` = `TEST-ONLY`. Run from the repo root:

   ```python
   import ast, pathlib, re

   defs = {}
   for folder in ("src", "ui", "scripts"):
       for path in pathlib.Path(folder).rglob("*.py"):
           for node in ast.parse(path.read_text()).body:
               if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                   defs[node.name] = str(path)


   def read_all(folders, files=()):
       text = "".join(p.read_text() for f in folders for p in pathlib.Path(f).rglob("*.py"))
       return text + "".join(pathlib.Path(f).read_text() for f in files)


   code = read_all(("src", "ui", "scripts"), ("run_pipeline.py",))
   tests = read_all(("tests",))
   for name, where in sorted(defs.items()):
       used_in_code = len(re.findall(rf"\b{name}\b", code)) - 1  # minus its own def line
       if used_in_code <= 0:
           print("TEST-ONLY" if re.search(rf"\b{name}\b", tests) else "DEAD", name, where)
   ```

3. **Grep before you delete:** `grep -rn "name" src ui scripts run_pipeline.py docs README.md`.
   The scan misses names used through `getattr`, string keys, JSON files or Streamlit pages,
   and it counts a name that appears only in a comment. Check the hits by hand.
4. **Coverage:** lines that `pytest --cov-report=term-missing` says never run are
   candidates, not proof.
5. **Pre-audit surprises happen:** a "dead" function can have one caller (for example a
   singular helper that calls the plural one). Read the callers before deleting.

## Quick checks (end of every task, takes a minute)

- [ ] Branches: apply the cleanup rules of skill `branch-guard` (delete only branches whose
      PR is verified MERGED).
- [ ] No temporary files left in the repo: `issue.md`, `*.orig`, `*.bak`, scratch scripts,
      `.DS_Store`, `__pycache__`, `.pytest_cache`, coverage files.
- [ ] `git status --short` shows nothing unexpected; nothing under `data/` or `reports/` is
      tracked by mistake (`git ls-files data reports`).
- [ ] No file larger than 1 MB newly added (`git diff main --stat`), no `.xlsx`, `.csv`
      exports, `.env` or secrets (the repo is public).
- [ ] README and docs match the CLI (`python run_pipeline.py --help`).
- [ ] Every issue you opened is linked in the PR; issues the PR fixes use `Fixes #N`.

## Full audit (weekly, and when the owner says a PR was merged)

1. Run `branch-guard` Step A (read-only). If the tree is dirty, delete nothing; report and stop.
2. Build a table of every local and remote branch: name, last commit date, PR number and
   state (`gh pr list --state all`), whether it has unpushed commits. Delete the branches
   that pass all `branch-guard` gates (local and remote); keep and explain the rest.
3. Fill in this table:

| Area | What to verify | How |
|---|---|---|
| Branches | stale local and remote branches, merged or abandoned | `git branch -vv`, `git branch -r`, `gh pr list --state all` |
| Pull requests | open PRs older than 7 days, PRs with failing CI | `gh pr list --state open` |
| Issues | fixed issues still open, duplicates, missing labels | `gh issue list --state open` |
| CI workflows | latest runs on `main` green, action versions current, Python matrix matches `CLAUDE.md`, branch triggers match the naming rules, no obsolete jobs | `gh run list --branch main --limit 10`, read `.github/workflows/*.yml` |
| Dependencies | unused or duplicate packages in `requirements*.txt`, unpinned versions | read the files, `pip check` |
| Code | unused imports and functions, dead files, leftover `TODO`/`FIXME` | the dead-code method above, `grep -rn "TODO\|FIXME" src ui` |
| Tests | duplicated or skipped tests, tests that need MySQL or the network | `MYSQL_HOST=127.0.0.1 MYSQL_PORT=1 pytest -q -rs` |
| Docs | README, `docs/`, `CLAUDE.md` and skills still accurate | compare with the real commands and files |
| Stray files | tracked data or generated files, large files | `git ls-files`, size check |
| Local git | old stashes, stale worktrees | `git stash list`, `git worktree prune` |

4. Delete only untracked temp files and caches you can explain. Changes to tracked files go
   in their own PR named `chore/<what>`; never mix cleanup into a feature or fix PR.
5. Anything you cannot fix safely becomes a GitHub issue labelled `tech-debt` (skill
   `github-issue`). Do not leave problems silent.
6. Open (or update) one issue `Hygiene report YYYY-MM-DD` labelled `tech-debt` with what was
   found, fixed and left (and why), and close the previous report issue. Print the same
   summary in your final report.

## Safety rules

1. **Report first, then delete.** Print what you found and why it is safe.
2. **Safe to delete:** branches that pass the `branch-guard` gates; untracked temp files you
   created; caches (`__pycache__`, `.pytest_cache`).
3. **Never delete without the owner:** anything in `data/`, `reports/`, backups, `.env`, the
   owner's database, uncommitted work, or any file you cannot explain. Exception: paths the
   owner approved by name in the changelog or the task prompt, exactly those paths.

## Ending a task (rule B9: chat hygiene)

End the final message with **`Next: CL-xx`** (the next task id from the task prompt). Then:

- Mention `/clear` or a new chat ONLY if the next task does not need this chat (for example
  the task changed rules or skills, wrote to the real database, or ended a milestone, and
  the task prompt says so).
- Mention `/compact` or the Resume prompt ONLY if the task is unfinished AND the context is
  long (above about 60%).
- Never suggest clearing while there is uncommitted work. Say nothing about clearing in any
  other case.
