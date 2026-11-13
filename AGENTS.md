# Agent Guidelines for NSE Screener

Standing rules and protocols for all autonomous and human-assisted agent workflows in this repository.

---

## 1. General Principles

- **Never Merge:** Do NOT merge pull requests. Pull requests are opened for review only.
- **One PR per Task:** Maintain strict task isolation. Bug fixes PRs and feature/update PRs stay separate.
- **Public Repository Cleanliness:** This repository is public. Never commit data files (`.xlsx`, `.csv` exports), secrets, credentials, or `.env` files. Never include personal or proprietary user data in git commits, issues, or PR descriptions.
- **Documentation Parity:** Always update `README.md` and related docs whenever adding or modifying any CLI command, database table/column, setting, or architecture pattern.
- **Explicit Verification Boundaries:** State clearly in every PR description what was verified and what was NOT verified.

---

## 2. CI-Like Pre-Push Verification

Before pushing any commit or opening/updating a PR, verify all checks pass locally without MySQL:

```bash
# 1. Run full test suite with MySQL unreachable (verifies offline/SQLite test independence)
MYSQL_HOST=127.0.0.1 MYSQL_PORT=1 pytest -q

# 2. Verify code quality and styling
ruff check .
ruff format --check .
```

All tests must be green and coverage must be $\ge 70\%$.

---

## 3. GitHub Issue Filing Protocol

Whenever you encounter a problem, unexpected behavior, or data discrepancy while working, **file a GitHub issue immediately** instead of silently working around it or silently fixing out-of-scope bugs.

### When to File an Issue
File an issue for:
1. **Out-of-Scope Bugs:** Any bug outside the current task scope.
2. **CI / Test Failures:** Any failure not caused by the current task or that cannot be cleanly fixed within scope.
3. **Data Anomalies:** Duplicated sessions, missing rows, symbol mismatches, or date discrepancies.
4. **Assumptions & Workarounds:** Any assumption made or deviation from the task prompt.
5. **Decisions Needed:** Any design question or requirement needing user clarification/decision.
6. **Command Failures:** Any CLI command that failed, timed out, or behaved unexpectedly.

### How to File an Issue
- Use `gh issue create`.
- If `gh` CLI is unauthenticated or fails, write the complete issue text to `reports/issues_draft.md` and output it clearly so the user can submit it.

### Standard Issue Format
- **Title:** Concise, descriptive summary of the problem.
- **Labels:** Choose among `bug`, `data-quality`, `needs-user-input`, `tech-debt`.
- **Body Structure:**
  - **Context:** Task ID, git branch, exact command run.
  - **What Happened:** Clear description of the actual behavior observed.
  - **Expected:** What should have happened.
  - **Evidence:** Short, sanitized log excerpts or file diffs (NO secrets or personal data).
  - **Impact:** Severity and affected components (screeners, ingestion, UI, etc.).
  - **Suggested Fix:** Recommended architectural or code fix.

### Tracking in Pull Requests
Every filed issue must be listed under an **"Issues filed"** section in the pull request description (e.g. `Fixes #33`, `References #34`, `References #35`).
