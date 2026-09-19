#!/bin/bash
# Run this once after granting Full Disk Access to /bin/bash.
# Installs and starts the CineMind LaunchAgent, then verifies it answers.
set -euo pipefail

PLIST_SRC="/Users/gilbert/CineMind/scripts/com.cinemind.app.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.cinemind.app.plist"
UID_NUM="$(id -u)"

cp "$PLIST_SRC" "$PLIST_DST"
launchctl bootout "gui/$UID_NUM/com.cinemind.app" 2>/dev/null || true
pkill -f "uvicorn server:app" 2>/dev/null || true
sleep 1
launchctl bootstrap "gui/$UID_NUM" "$PLIST_DST"
launchctl enable "gui/$UID_NUM/com.cinemind.app"

echo "Waiting for CineMind to answer on http://localhost:8001 ..."
for _ in $(seq 1 20); do
  if curl -fsS -o /dev/null http://localhost:8001/api/; then
    echo "OK — CineMind is running and will start again on every reboot."
    exit 0
  fi
  sleep 2
done

echo "Not answering yet. Check the log:"
echo "  tail -20 ~/Library/Logs/cinemind.error.log"
exit 1
