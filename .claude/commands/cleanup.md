---
description: Tidy the repository. Verifies branches, PRs, issues, CI workflows and stray files, deletes only what is verified safe, and writes a report.
---

# /cleanup

Run weekly, and whenever the owner says a PR was merged.

1. Run `branch-guard` Step A (read-only): current branch, working tree, fetch with
   prune. If the tree is dirty, do not delete anything; report and stop.
2. Build a table of every local and remote branch: name, last commit date, PR
   number and state (`gh pr list --state all`), and whether it has unpushed
   commits.
3. Delete the branches that pass all `branch-guard` gates (local and remote).
   Keep and explain the rest.
4. Run the full weekly audit table from the `repo-hygiene` skill: PRs, issues, CI
   workflow health, dependencies, dead code, tests, docs, stray files, stashes.
5. Delete only untracked temp files and caches you can explain. Anything in
   `data/`, `reports/`, backups or `.env` is never touched.
6. If tracked files need cleanup, create a separate `chore/<name>` branch and PR
   (use `/start-task` and `/pr-ready`). Do not merge.
7. Open (or update) the issue `Hygiene report YYYY-MM-DD` labelled `tech-debt`
   with what was found, fixed and left, and close last week's report issue.
8. Print the same summary in your final report.
