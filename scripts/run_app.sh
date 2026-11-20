#!/usr/bin/env bash
# Start the dashboard with fresh data: run the daily update first, then open Streamlit.
#
# If the update fails (no internet, NSE down, MySQL stopped) the dashboard still opens on
# the last good data, and a warning tells you what happened. Extra arguments are passed to
# Streamlit, for example:  scripts/run_app.sh --server.port 8502
set -uo pipefail

# Always work from the project root, wherever the script is started from.
cd "$(dirname "$0")/.."

# Use the project virtualenv when it exists.
if [ -f .venv/bin/activate ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

echo "Step 1/2: updating the data (python run_pipeline.py --update) ..."
if ! python run_pipeline.py --update; then
    echo "WARNING: the update failed (see the message above)." >&2
    echo "         Opening the dashboard on the LAST GOOD data." >&2
fi

echo "Step 2/2: starting the dashboard ..."
exec streamlit run ui/Dashboard.py "$@"
