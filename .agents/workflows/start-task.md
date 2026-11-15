---
description: Start every task safely. Runs the branch guard, cleans merged branches, restates the scope and checks open work.
---

# /start-task

1. Read `AGENTS.md` fully.
2. Follow `branch-guard` Steps A to C: check the current branch and working tree,
   decide the task branch, then switch to it or create it. If the tree is dirty on
   the wrong branch, STOP and report.
3. Apply the `branch-guard` cleanup rules to every local branch other than `main`
   and the current one (delete only branches whose PR is verified MERGED).
4. Run `gh pr list` and `gh issue list`; note anything related to this task.
5. Restate in your own words: the goal, what is in scope, what is out of scope,
   the branch you are on (and whether you created or switched it), and which
   skills apply (`nse-data-integrity`, `db-migration`, `github-issue`).
6. If anything is unclear or needs a decision, file an issue labelled
   `needs-user-input` and ask before writing code.
