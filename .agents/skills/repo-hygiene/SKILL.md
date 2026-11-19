---
name: repo-hygiene
description: Use at the end of every task (quick checks) and in the weekly cleanup (full audit) to keep the repository tidy. Covers stale branches, pull requests and issues, leftover temporary files, outdated docs, CI workflow health, unused code and dependencies, and accidentally tracked data files. Report first; delete only what is verified safe.
---

# Repo hygiene

Goal: no clutter, ever. Clean a little every task so there is never a big mess.

## Quick checks (end of every task, takes a minute)

- [ ] Branches: run the cleanup rules of `branch-guard` (merged branches only).
- [ ] No temporary files left in the repo: `issue.md`, `*.orig`, `*.bak`, scratch
      scripts, `.DS_Store`, `__pycache__`, `.pytest_cache`, coverage files.
- [ ] `git status --short` shows nothing unexpected; nothing under `data/` or
      `reports/` is tracked by mistake (`git ls-files data reports`).
- [ ] No file larger than 1 MB newly added (`git diff main --stat`), no `.xlsx`,
      `.csv` exports, `.env` or secrets.
- [ ] README and docs match the CLI (`python run_pipeline.py --help`).
- [ ] Every issue you opened is linked in the PR; issues the PR fixes use
      `Fixes #N`.

## Weekly audit (run via the `/cleanup` command)

| Area | What to verify | How |
|---|---|---|
| Branches | stale local and remote branches, merged or abandoned | `git branch -vv`, `git branch -r`, `gh pr list --state all` |
| Pull requests | open PRs older than 7 days, PRs with failing CI | `gh pr list --state open` |
| Issues | fixed issues still open, duplicates, missing labels | `gh issue list --state open` |
| CI workflows | latest runs on `main` green, action versions current, Python matrix matches `CLAUDE.md`, branch triggers match the naming rules (`fix/**`, `feat/**`, `docs/**`, `chore/**`), no obsolete jobs | `gh run list --branch main --limit 10`, read `.github/workflows/*.yml` |
| Dependencies | unused or duplicate packages in `requirements*.txt`, unpinned versions | read the files, `pip check` |
| Code | unused imports and functions, dead files, leftover `TODO`/`FIXME` | `ruff check .`, `grep -rn "TODO\|FIXME" src ui` |
| Tests | duplicated or skipped tests, tests that need MySQL or the network | run with `MYSQL_HOST=127.0.0.1 MYSQL_PORT=1 pytest -q -rs` |
| Docs | README, `docs/`, `CLAUDE.md` and skills still accurate | compare with the real commands and files |
| Stray files | tracked data or generated files, large files | `git ls-files`, size check |
| Local git | old stashes, stale worktrees | `git stash list`, `git worktree prune` |

## Rules

1. **Report first, then delete.** Print what you found and why it is safe.
2. **Safe to delete:** branches that pass the `branch-guard` gates; untracked
   temp files you created; caches (`__pycache__`, `.pytest_cache`).
3. **Never delete without the owner:** anything in `data/`, `reports/`, backups,
   `.env`, the owner's database, uncommitted work, or any file you cannot
   explain.
4. **Changes to tracked files go in their own PR** named `chore/<what>`; never
   mix cleanup into a feature or fix PR.
5. **Do not leave problems silent.** Anything you cannot fix safely becomes a
   GitHub issue (`github-issue` skill), labelled `tech-debt`.
6. **Weekly report:** open one issue titled `Hygiene report YYYY-MM-DD` with the
   table above filled in (found, fixed, left, why). Close the previous week's
   report issue.
