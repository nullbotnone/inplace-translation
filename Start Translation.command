#!/bin/bash
# Double-click this in Finder. macOS opens a Terminal window, starts everything, and
# brings the console up in your browser. Leave the window open; closing it stops the
# translation. No typing required.
#
# If macOS says it "cannot be opened because it is from an unidentified developer",
# right-click the file once and choose Open. That only has to be done the first time.

cd "$(dirname "$0")" || { echo "Cannot find the project folder."; read -r; exit 1; }

CONSOLE="http://127.0.0.1:8000/admin"

# Open the console as soon as the server answers. The models keep loading behind it,
# and the page shows that happening, which is the reassurance a volunteer needs.
(
  for _ in $(seq 1 180); do
    if curl -s -o /dev/null --max-time 1 "$CONSOLE"; then
      open "$CONSOLE"
      exit 0
    fi
    sleep 1
  done
) &
opener=$!

./start.sh "$@"
status=$?

# If startup failed, the poller above is still waiting for a server that will never
# answer. Without this it holds the window for another three minutes.
kill "$opener" 2>/dev/null

# Finder-launched windows can close on a clean exit, taking the reason with them.
if [ $status -ne 0 ]; then
  echo
  echo "Something went wrong (exit $status). The message above says what."
  echo "Press return to close this window."
  read -r
fi
exit $status
