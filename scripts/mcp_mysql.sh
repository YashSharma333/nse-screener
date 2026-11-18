#!/usr/bin/env bash
# Starts the MySQL MCP server in READ-ONLY mode.
#
# Why a script? .mcp.json cannot read .env by itself, and we never want
# passwords inside a committed file. This script loads .env first.
# It uses a separate read-only MySQL user (see README, "MCP servers"),
# never the root user the pipeline uses.
set -euo pipefail

# Go to the repo root (this script lives in scripts/).
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a          # export every variable defined while sourcing
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

: "${MYSQL_READONLY_USER:?Set MYSQL_READONLY_USER in .env (see README, MCP servers)}"
: "${MYSQL_READONLY_PASSWORD:?Set MYSQL_READONLY_PASSWORD in .env}"

# Names the MCP package expects.
export MYSQL_HOST="${MYSQL_HOST:-localhost}"
export MYSQL_PORT="${MYSQL_PORT:-3306}"
export MYSQL_USER="$MYSQL_READONLY_USER"
export MYSQL_PASS="$MYSQL_READONLY_PASSWORD"
export MYSQL_DB="${MYSQL_DB:-nse_screener}"

# Belt and braces: the package blocks writes unless these are "true".
export ALLOW_INSERT_OPERATION=false
export ALLOW_UPDATE_OPERATION=false
export ALLOW_DELETE_OPERATION=false
export ALLOW_DDL_OPERATION=false

# Exact version pinned on purpose (AGENTS.md: pin dependencies).
exec npx -y @benborla29/mcp-server-mysql@2.0.9
