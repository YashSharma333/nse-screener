---
description: Finish a task. Verifies the branch, runs the CI-like checks, reviews the diff, opens the pull request and stops. Never merges.
---

# /pr-ready

1. Run `branch-guard` Step D: the current branch must be the task branch and the
   working tree must contain only files that belong to the task.
2. Run the checks exactly as CI does and keep the real output:
   - `MYSQL_HOST=127.0.0.1 MYSQL_PORT=1 pytest -q`
   - `ruff check .`
   - `ruff format --check .`
   If anything fails, fix it (or file an issue if it is out of scope). Stop after
   three failed attempts and report.
3. Review the whole diff (`git diff main --stat` and the content). Confirm there
   are no secrets, `.env`, `.xlsx` or `.csv` data files, and no unrelated edits.
4. Run the quick checks of the `deep-cleanup` skill.
5. Confirm README/docs are updated for every new command, table or setting.
6. Commit specific paths with a Conventional Commit message, re-check the branch
   (Step D), then `git push -u origin <branch>`.
7. Open the PR with `gh pr create`, title `<type>(<task>): <summary>`, body with
   these sections in order: Summary, Changes, Commands (one purpose line above
   each command, e.g. "Run the full test suite offline:"), Verification, Not
   verified; `Fixes #N` only for issues the PR closes. Only what was created or
   fixed: no mention of the owner, merge advice, rollback, tracker or changelog,
   or `.claude/` changes. Merge advice goes only in the final report in step 10.
8. Print the PR URL. If `gh pr create` fails, say so and print the title and body.
9. NEVER run `gh pr merge`, never push to main, never enable auto-merge. Do not
   delete the branch: its PR is still open.
10. Finish with the final report from the "Final report" section of CLAUDE.md, including the branch
    used, which old branches were deleted or kept, and the merge advice: "Merge now" or
    "Wait for ...", the method (Squash and merge, Create a merge commit) with a one-line
    reason from the PR's real commits, and the commit title to paste.
