#!/bin/bash
# Make http://localhost:8001 on this Mac reach CineMind on the NAS (scripts/nas_forward.py).
#
#   scripts/install_nas_forward.sh            install / reinstall and start
#   scripts/install_nas_forward.sh --remove   stop it and allow the Mac backend again
#
# The old Mac backend (com.cinemind.local) is disabled, not deleted: with its own
# stale database it must not start next to the NAS copy (HANDOFF.md omgång 7),
# and at the next login launchd would otherwise have started it again.
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME="${CINEMIND_RUNTIME:-$HOME/CineMind}"
UID_NUM="$(id -u)"
LABEL="com.cinemind.nas-forward"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
NAS="${CINEMIND_NAS:-192.168.50.94:8001}"

if [ "${1:-}" = "--remove" ]; then
  /bin/launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  /bin/launchctl enable "gui/$UID_NUM/com.cinemind.local"
  echo "Vidarebefordran borttagen; com.cinemind.local är tillåten igen (startas inte automatiskt nu)."
  exit 0
fi

# launchd agents may not read ~/Documents, so the script runs from the runtime copy.
mkdir -p "$RUNTIME/scripts" "$RUNTIME/.runtime"
cp "$SRC/scripts/nas_forward.py" "$RUNTIME/scripts/nas_forward.py"

/bin/launchctl disable "gui/$UID_NUM/com.cinemind.local"
/bin/launchctl bootout "gui/$UID_NUM/com.cinemind.local" 2>/dev/null || true

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>$RUNTIME/scripts/nas_forward.py</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>CINEMIND_NAS</key>
    <string>$NAS</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>5</integer>
  <key>ProcessType</key>
  <string>Background</string>
  <key>StandardOutPath</key>
  <string>$RUNTIME/.runtime/nas-forward.log</string>
  <key>StandardErrorPath</key>
  <string>$RUNTIME/.runtime/nas-forward.log</string>
</dict>
</plist>
PLIST_EOF

/bin/launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || true
/bin/launchctl bootstrap "gui/$UID_NUM" "$PLIST"
/bin/launchctl kickstart -k "gui/$UID_NUM/$LABEL"

for _ in $(seq 1 20); do
  if /usr/bin/curl -fsS -o /dev/null --max-time 3 "http://localhost:8001/api/"; then
    echo "Klart — http://localhost:8001 går nu till CineMind på NAS:en ($NAS)"
    exit 0
  fi
  sleep 0.5
done
echo "localhost:8001 svarar inte; se $RUNTIME/.runtime/nas-forward.log" >&2
exit 1
