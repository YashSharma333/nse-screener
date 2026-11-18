#!/usr/bin/env bash
# Prints the HTTP header the GitHub MCP server needs, as JSON.
#
# Claude Code runs this (see "headersHelper" in .mcp.json). The token is
# read from .env so it is never written into a committed file.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

: "${GITHUB_PERSONAL_ACCESS_TOKEN:?Set GITHUB_PERSONAL_ACCESS_TOKEN in .env}" >&2

printf '{"Authorization": "Bearer %s"}\n' "$GITHUB_PERSONAL_ACCESS_TOKEN"
