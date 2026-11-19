#!/usr/bin/env bash
# Adds the read-only MySQL MCP server to Antigravity's global MCP config.
#
# Why a script? Antigravity has no per-project MCP file like Claude Code's
# .mcp.json: it reads ONE file for all projects (~/.gemini/config/mcp_config.json),
# and that file needs the absolute path of this repo, which differs per machine.
# The script fills in the path and MERGES the entry, so your other servers stay.
# It only registers scripts/mcp_mysql.sh (SELECT-only user); passwords stay in
# .env and are never copied into the Antigravity config.
#
# Usage:  bash scripts/install_antigravity_mcp.sh
set -euo pipefail

REPO_PATH="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG="${ANTIGRAVITY_MCP_CONFIG:-$HOME/.gemini/config/mcp_config.json}"

mkdir -p "$(dirname "$CONFIG")"
[ -f "$CONFIG" ] && cp "$CONFIG" "$CONFIG.bak"   # keep a copy before editing

python3 - "$CONFIG" "$REPO_PATH" <<'PY'
import json
import sys
from pathlib import Path

config_path, repo_path = Path(sys.argv[1]), sys.argv[2]
text = config_path.read_text().strip() if config_path.exists() else ""
config = json.loads(text) if text else {}  # an empty file is treated as {}
config.setdefault("mcpServers", {})["nse-mysql"] = {
    "command": "bash",
    "args": [f"{repo_path}/scripts/mcp_mysql.sh"],
}
config_path.write_text(json.dumps(config, indent=2) + "\n")
print(f"nse-mysql registered in {config_path}")
PY
echo "Restart Antigravity, then open its MCP panel: nse-mysql should show as connected."
