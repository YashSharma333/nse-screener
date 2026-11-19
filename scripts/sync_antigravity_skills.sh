#!/usr/bin/env bash
# Copies the project's Claude Code skills and slash commands into
# .agents/skills/, the folder Antigravity reads for workspace skills.
#
# Why copies, not symlinks? Antigravity's skill loader is documented to read
# <workspace>/.agents/skills/<name>/SKILL.md; real files are the safest. The
# single source of truth stays in .claude/ - edit there, then re-run this script
# (never edit .agents/skills by hand; the next run overwrites it).
#
# Slash commands become skills too (Antigravity workflows are deprecated). A
# command only has a `description`, so the skill name is taken from the file name.
#
# Usage:  bash scripts/sync_antigravity_skills.sh
set -euo pipefail

cd "$(dirname "$0")/.."
target=.agents/skills
mkdir -p "$target"

# 1. Skills: same SKILL.md format (name + description frontmatter) in both tools.
for dir in .claude/skills/*/; do
  name=$(basename "$dir")
  rm -rf "${target:?}/$name"
  cp -R "$dir" "$target/$name"
done

# 2. Commands: add the `name:` line Antigravity requires after the first `---`.
for file in .claude/commands/*.md; do
  name=$(basename "$file" .md)
  mkdir -p "$target/$name"
  awk -v n="$name" 'NR==1 {print; print "name: " n; next} {print}' "$file" > "$target/$name/SKILL.md"
done

echo "Synced to $target:"
ls "$target"
