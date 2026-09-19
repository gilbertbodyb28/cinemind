#!/bin/bash
# Opens CineMind, starting the background service first if it is not up.
# Used by the CineMind app on the Desktop.
set -uo pipefail

UID_NUM="$(id -u)"
LABEL="com.cinemind.app"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
URL="http://localhost:8001"

answers() { /usr/bin/curl -fsS -o /dev/null --max-time 2 "$URL/api/"; }

if ! answers; then
  # Load the agent if it was never bootstrapped in this login session.
  if ! /bin/launchctl print "gui/$UID_NUM/$LABEL" >/dev/null 2>&1; then
    [ -f "$PLIST" ] && /bin/launchctl bootstrap "gui/$UID_NUM" "$PLIST" >/dev/null 2>&1
  fi
  /bin/launchctl kickstart "gui/$UID_NUM/$LABEL" >/dev/null 2>&1

  for _ in $(seq 1 30); do
    answers && break
    sleep 1
  done
fi

if answers; then
  /usr/bin/open "$URL"
  exit 0
fi

/usr/bin/osascript -e 'display alert "CineMind did not start" message "The background service is not answering on port 8001. Check ~/Library/Logs/cinemind.error.log" as critical'
exit 1
