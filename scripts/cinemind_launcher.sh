#!/bin/bash
# The executable inside /Applications/CineMind.app.
# Opens CineMind and starts the background service first if it is not up.
set -uo pipefail

LABEL="com.cinemind.local"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
RUNTIME="${CINEMIND_RUNTIME:-$HOME/CineMind}"
URL="http://localhost:8001"
UID_NUM="$(id -u)"

answers() { /usr/bin/curl -fs -o /dev/null --max-time 2 "$URL/api/"; }

alert() {
  /usr/bin/osascript -e "display alert \"CineMind startade inte\" message \"$1\" as critical" >/dev/null 2>&1
}

if ! answers; then
  if [ ! -f "$PLIST" ]; then
    alert "Bakgrundstjänsten är inte installerad. Kör scripts/install_cinemind_app.sh i Terminal."
    exit 1
  fi
  /bin/launchctl print "gui/$UID_NUM/$LABEL" >/dev/null 2>&1 \
    || /bin/launchctl bootstrap "gui/$UID_NUM" "$PLIST" >/dev/null 2>&1
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

alert "Tjänsten svarar inte på port 8001. Se $RUNTIME/.runtime/cinemind.error.log"
exit 1
