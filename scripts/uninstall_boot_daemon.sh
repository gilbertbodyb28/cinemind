#!/bin/bash
# Reverts install_boot_daemon.sh: removes the system daemon and puts the
# per-login LaunchAgent back.
#
#   sudo /Users/gilbert/CineMind/scripts/uninstall_boot_daemon.sh
set -uo pipefail

APP_DIR="/Users/gilbert/CineMind"
OWNER="gilbert"
AGENT_LABEL="com.cinemind.app"
DAEMON_LABEL="com.cinemind.boot"
OWNER_UID="$(/usr/bin/id -u "$OWNER")"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo:  sudo $0" >&2
  exit 1
fi

/bin/launchctl bootout "system/$DAEMON_LABEL" 2>/dev/null
/bin/rm -f "/Library/LaunchDaemons/$DAEMON_LABEL.plist"

# MongoDB back to a login agent (Homebrew refuses to run as root).
MONGO_LABEL="homebrew.mxcl.mongodb-community"
/bin/launchctl bootout "system/$MONGO_LABEL" 2>/dev/null
/bin/rm -f "/Library/LaunchDaemons/$MONGO_LABEL.plist"
MONGO_SRC="/opt/homebrew/opt/mongodb-community/$MONGO_LABEL.plist"
if [ -f "$MONGO_SRC" ]; then
  /bin/cp "$MONGO_SRC" "/Users/$OWNER/Library/LaunchAgents/$MONGO_LABEL.plist"
  /usr/sbin/chown "$OWNER":staff "/Users/$OWNER/Library/LaunchAgents/$MONGO_LABEL.plist"
  /usr/bin/sudo -u "$OWNER" /bin/launchctl bootstrap "gui/$OWNER_UID" "/Users/$OWNER/Library/LaunchAgents/$MONGO_LABEL.plist" 2>/dev/null
fi

/bin/cp "$APP_DIR/scripts/com.cinemind.app.plist" "/Users/$OWNER/Library/LaunchAgents/$AGENT_LABEL.plist"
/usr/sbin/chown "$OWNER":staff "/Users/$OWNER/Library/LaunchAgents/$AGENT_LABEL.plist"
/usr/bin/sudo -u "$OWNER" /bin/launchctl bootstrap "gui/$OWNER_UID" "/Users/$OWNER/Library/LaunchAgents/$AGENT_LABEL.plist" 2>/dev/null

echo "Reverted: the login LaunchAgent is back, the boot daemon is gone."
