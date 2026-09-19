#!/bin/bash
# Installs CineMind as a system LaunchDaemon so it runs from boot, before anyone
# logs in — and removes the per-login LaunchAgent so nothing starts twice.
#
#   sudo /Users/gilbert/CineMind/scripts/install_boot_daemon.sh
#
# Undo: sudo /Users/gilbert/CineMind/scripts/uninstall_boot_daemon.sh
set -uo pipefail

APP_DIR="/Users/gilbert/CineMind"
OWNER="gilbert"
AGENT_LABEL="com.cinemind.app"
DAEMON_LABEL="com.cinemind.boot"
PLIST_SRC="$APP_DIR/scripts/com.cinemind.boot.plist"
PLIST_DST="/Library/LaunchDaemons/$DAEMON_LABEL.plist"
OWNER_UID="$(/usr/bin/id -u "$OWNER")"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo:  sudo $0" >&2
  exit 1
fi

step() { printf '\n▸ %s\n' "$1"; }
as_owner() { /usr/bin/sudo -u "$OWNER" "$@"; }

step "Stopping the per-login LaunchAgent"
as_owner /bin/launchctl bootout "gui/$OWNER_UID/$AGENT_LABEL" 2>/dev/null
/bin/rm -f "/Users/$OWNER/Library/LaunchAgents/$AGENT_LABEL.plist"
/usr/bin/pkill -f "uvicorn server:app" 2>/dev/null
sleep 2

step "Moving MongoDB from a login service to a boot service"
as_owner /opt/homebrew/bin/brew services stop mongodb-community >/dev/null 2>&1
/opt/homebrew/bin/brew services start mongodb-community >/dev/null 2>&1 \
  || echo "  (could not start mongodb-community as a boot service — check: brew services list)"

step "Installing $DAEMON_LABEL"
/bin/cp "$PLIST_SRC" "$PLIST_DST"
/usr/sbin/chown root:wheel "$PLIST_DST"
/bin/chmod 644 "$PLIST_DST"
/bin/launchctl bootout "system/$DAEMON_LABEL" 2>/dev/null
/bin/launchctl bootstrap system "$PLIST_DST" || {
  echo "  bootstrap failed — see the error above" >&2
  exit 1
}
/bin/launchctl enable "system/$DAEMON_LABEL"

step "Waiting for CineMind to answer on http://localhost:8001"
ok=0
for _ in $(seq 1 40); do
  if /usr/bin/curl -fsS -o /dev/null --max-time 2 http://localhost:8001/api/; then ok=1; break; fi
  sleep 2
done

printf '\n── status ─────────────────────────────\n'
/bin/launchctl print "system/$DAEMON_LABEL" 2>/dev/null | /usr/bin/grep -E '^\s+(state|pid|program)\s' | /usr/bin/head -3
if as_owner /bin/launchctl print "gui/$OWNER_UID/$AGENT_LABEL" >/dev/null 2>&1; then
  echo "  WARNING: the old LaunchAgent is still loaded"
else
  echo "  old LaunchAgent: removed"
fi
/opt/homebrew/bin/brew services list 2>/dev/null | /usr/bin/grep -i mongodb

if [ "$ok" -eq 1 ]; then
  echo "  http://localhost:8001 : answering"
  echo
  echo "Done — CineMind now starts at boot, before login."
  exit 0
fi

echo "  http://localhost:8001 : NOT answering"
echo "  Check: tail -20 /Users/$OWNER/Library/Logs/cinemind.error.log"
exit 1
