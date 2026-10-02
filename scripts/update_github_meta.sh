#!/usr/bin/env bash
# ==============================================================================
# Script: update_github_meta.sh
# Updates the GitHub repository description and topics using the GitHub CLI (gh).
# ==============================================================================

set -euo pipefail

# Set repository description and key technical topic tags
DESCRIPTION="NSE Screener: A Python and MySQL-backed quantitative NSE stock screener and ETL pipeline."
TOPICS="python,streamlit,data-analytics,sql,quantitative-analysis"

echo "Checking GitHub CLI authentication..."
if ! command -v gh &> /dev/null; then
    echo "Error: gh CLI is not installed. Please install it with 'brew install gh'."
    exit 1
fi

if [ -z "${GH_TOKEN:-}" ]; then
    TOKEN=$(printf "protocol=https\nhost=github.com\n" | git credential fill 2>/dev/null | grep '^password=' | cut -d= -f2- || true)
    if [ -n "$TOKEN" ]; then
        export GH_TOKEN="$TOKEN"
    fi
fi

echo "Updating GitHub repository metadata..."
gh repo edit \
    --description "$DESCRIPTION" \
    --add-topic "python" \
    --add-topic "streamlit" \
    --add-topic "data-analytics" \
    --add-topic "sql" \
    --add-topic "quantitative-analysis"

echo "✓ Successfully updated GitHub repository description and topics!"
