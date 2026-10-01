#!/usr/bin/env bash
# ==============================================================================
# Script: update_github_meta.sh
# Updates the GitHub repository description and topics using the GitHub CLI (gh).
# ==============================================================================

set -euo pipefail

DESCRIPTION="ApexGrowth: A Python and MySQL-backed quantitative NSE stock screener and ETL pipeline."
TOPICS="python,streamlit,data-analytics,sql,quantitative-analysis"

echo "Checking GitHub CLI authentication..."
if ! command -v gh &> /dev/null; then
    echo "Error: gh CLI is not installed. Please install it with 'brew install gh'."
    exit 1
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
