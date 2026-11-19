---
name: branch-guard
description: MANDATORY at the start of every task and again before every commit and push. Checks which git branch you are on, which branch the task belongs to, switches to it or creates it safely, protects the owner's uncommitted work, and deletes branches that are no longer needed once their pull request is merged.
---

# Branch guard

The owner also works in this same folder and may switch branches by mistake or
leave uncommitted changes. You must never assume you are on the right branch.

## Step A: look before you touch anything (read-only)

```bash
git fetch --prune
git branch --show-current          # empty output = detached HEAD
git status --short                 # uncommitted or untracked files
git log --oneline -3
```
Write down: current branch, clean or dirty, and whether it is behind its remote.

## Step B: decide which branch the task belongs to

1. If the task prompt names a branch, that is the branch.
2. Otherwise use the naming rule from CLAUDE.md: `fix/<task>-name`,
   `feat/<task>-name`, `docs/<name>` or `chore/<name>`.
3. **One task = one branch.** Follow-up fixes go on the same branch while its PR is
   open.
4. **Never reuse a branch whose PR is already merged or closed.** Check with
   `gh pr list --head "<branch>" --state all --json number,state`. For follow-up
   work after a merge, create a new branch (for example `fix/<task>-followup`)
   from the updated `main`.

## Step C: get onto the right branch

| Situation | Action |
|---|---|
| Already on the right branch, clean | `git pull --ff-only`, continue |
| On another branch, clean, right branch exists locally | `git switch <branch>` then `git pull --ff-only` |
| Right branch exists only on the remote | `git switch -c <branch> --track origin/<branch>` |
| Branch does not exist yet | `git switch main`, `git pull --ff-only`, `git switch -c <branch>` |
| On `main` and the task needs changes | create the task branch; never commit on `main` |
| Detached HEAD | stop and report |
| **Dirty working tree on the wrong branch** | **STOP.** List the changed files and ask the owner. Do not discard, stash, commit or carry them over on your own |
| Dirty tree on the right branch | continue, but stage only the files that belong to this task |

Exception: when the task prompt says certain uncommitted files are the deliverable
(for example files the owner copied in), keep them and commit only those.

## Step D: re-check before every commit and every push

The owner may have switched branches while you were working.

```bash
git branch --show-current   # must equal the task branch
git status --short          # must contain only files that belong to the task
```
If the branch is wrong, stop and report. Never "fix" it by force.

## Commit safety

- Stage explicit paths (`git add path/to/file`). Do not use `git add -A` or
  `git add .` without reading `git status` first.
- Never commit to `main`. Never force-push. Never run `git reset --hard`,
  `git checkout -- .`, `git clean -fd` or `git stash drop` on work you did not create.
- Unexpected modified files that are not part of the task stay unstaged; mention
  them in the report.

## After a PR is merged: delete branches that are no longer needed

Run this at the start of every task and whenever the owner says a PR was merged.
A branch may be deleted only if ALL gates pass:

1. A pull request for the branch exists and its state is MERGED:
   `gh pr list --state merged --head "<branch>" --json number,headRefOid,mergedAt`
   (a squash merge leaves the branch looking "unmerged" to git, so use the PR
   state, not `git branch --merged`).
2. The branch has no commits that were not part of that PR: nothing unpushed
   (`git log origin/<branch>..<branch>` is empty) and the remote tip, if it
   still exists, equals the PR's `headRefOid`.
3. It is not `main`, not the branch you are currently on, and has no open PR.

If every gate passes:
```bash
git branch -D <branch>                    # local (-D is needed after squash merges)
git push origin --delete <branch>         # only if the remote branch still exists
git fetch --prune
```
If any gate fails, keep the branch and list it in your report with the reason.
Branches with no PR at all are never deleted automatically; report them.

## Always report

Every final report states: the branch worked on, whether it was switched or
created, and which branches were deleted or kept (with reasons).
