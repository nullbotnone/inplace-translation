#!/bin/bash
# Starts everything. bridge.py owns the pipeline, the broadcast and the console.
# Run it by hand, or let launchd run it (see com.church.sermon-translate.plist).
set -eo pipefail
cd "$(dirname "$0")"

# These two failures are otherwise a bash error or a Python traceback in sermon.log,
# which is no help to whoever is standing at the Mac ten minutes before the service.
if [ ! -f .venv/bin/activate ]; then
    echo "No Python environment here yet. Set one up first:" >&2
    echo "    brew install python@3.12" >&2
    echo "    python3.12 -m venv .venv && source .venv/bin/activate" >&2
    echo "    pip install speech-to-speech segno" >&2
    exit 1
fi
# misaki, which Kokoro's text processing needs, has no release for 3.13+. A venv built
# with a newer python fails at pip install with an unreadable ResolutionImpossible wall.
if ! .venv/bin/python -c 'import sys; sys.exit(sys.version_info[:2] >= (3, 13))'; then
    echo "This environment is Python $(.venv/bin/python -V | cut -d' ' -f2); it has to be 3.12 or older." >&2
    echo "Rebuild it:  rm -rf .venv && python3.12 -m venv .venv" >&2
    exit 1
fi
if ! command -v ffmpeg >/dev/null; then
    echo "ffmpeg is missing. Install it with:  brew install ffmpeg" >&2
    exit 1
fi

source .venv/bin/activate

# Kill the whole process group on exit, so the models never outlive the bridge.
trap 'kill 0' EXIT INT TERM

caffeinate -i python3 bridge.py "$@"
