#!/usr/bin/env bash
# Starts the MySQL MCP server with LIMITED WRITE access (INSERT, UPDATE, DELETE).
#
# Why a separate server? The read-only server (scripts/mcp_mysql.sh) stays safe
# for everyday checks. This one is only for fixing data, by the steps in
# CLAUDE.md section 7a (backup first, preview, small batches).
# It uses its own MySQL user (nse_agent) that has no ALTER or DROP rights, so
# even a wrong command cannot delete a table. It never uses the root user.
set -euo pipefail

# Go to the repo root (this script lives in scripts/).
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a          # export every variable defined while sourcing
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

: "${MYSQL_AGENT_USER:?Set MYSQL_AGENT_USER in .env (see README, MCP servers)}"
: "${MYSQL_AGENT_PASSWORD:?Set MYSQL_AGENT_PASSWORD in .env}"

# Names the MCP package expects.
export MYSQL_HOST="${MYSQL_HOST:-localhost}"
export MYSQL_PORT="${MYSQL_PORT:-3306}"
export MYSQL_USER="$MYSQL_AGENT_USER"
export MYSQL_PASS="$MYSQL_AGENT_PASSWORD"
export MYSQL_DB="${MYSQL_DB:-nse_screener}"

# Data changes are allowed; schema changes are not (the MySQL user also lacks the rights).
export ALLOW_INSERT_OPERATION=true
export ALLOW_UPDATE_OPERATION=true
export ALLOW_DELETE_OPERATION=true
export ALLOW_DDL_OPERATION=false

# Exact version pinned on purpose (CLAUDE.md: pin dependencies).
exec npx -y @benborla29/mcp-server-mysql@2.0.9
