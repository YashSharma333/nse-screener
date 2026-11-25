---
name: github-issue
description: Use whenever you must file a GitHub issue (a bug outside scope, test or CI failure, data anomaly, assumption, deviation, failed command). Gives the exact gh commands, labels, body template and fallback.
---

# Filing a GitHub issue

## Steps

1. Check you are logged in: `gh auth status`. If not, use the fallback below.
2. Search for duplicates: `gh issue list --state all --search "<keywords>"`.
3. Make sure labels exist (create once if missing):
   `gh label create data-quality --force`, and the same for `bug`,
   `needs-user-input`, `tech-debt`, `documentation`, `enhancement`.
4. Write the body to a temp file and create the issue:
   `gh issue create --title "<title>" --label "<label>" --body-file issue.md`
5. Add the issue number to the PR description under "Issues filed".

## Body template

```
## Context
Task ID, branch, exact command run.

## What happened
What you actually observed.

## Expected
What should have happened.

## Evidence
Short, sanitized log excerpt. No secrets, no personal data, no data exports.

## Impact
Which part is affected and how badly (screeners, ingestion, UI, backtests).

## Suggested fix
Your proposal, or "needs a maintainer decision" with the options.
```

## Fallback

If `gh` is not authenticated or fails, write the full text to
`reports/issues_draft.md` (do not commit it), print it in your final message, and
say plainly that the issue was NOT filed.

## Reminders

- File issues for problems you work around too, not only for blockers.
- Do not fix out-of-scope bugs in the current PR; file the issue instead. The only
  exception is a tiny, obviously safe fix (typo, lint warning, stale comment) in a
  file the PR already touches: make it a separate commit and mention it in the PR
  (see "Git and pull requests" in CLAUDE.md).
