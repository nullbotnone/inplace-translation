#!/bin/bash
# Starts everything. bridge.py owns the pipeline, the broadcast and the console.
# Run it by hand, or let launchd run it (see com.church.sermon-translate.plist).
set -eo pipefail
cd "$(dirname "$0")"
source .venv/bin/activate

# Kill the whole process group on exit, so the models never outlive the bridge.
trap 'kill 0' EXIT INT TERM

caffeinate -i python3 bridge.py "$@"
