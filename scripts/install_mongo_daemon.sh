#!/bin/bash
# Makes MongoDB start at boot instead of at login, so the CineMind daemon has a
# database before anyone logs in.
#
#   sudo /Users/gilbert/CineMind/scripts/install_mongo_daemon.sh
#
# Homebrew refuses to run `brew services` as root, so this writes the daemon
# plist directly. It keeps mongod running as gilbert, which owns the data dir.
set -uo pipefail

OWNER="gilbert"
LABEL="homebrew.mxcl.mongodb-community"
DST="/Library/LaunchDaemons/$LABEL.plist"
OWNER_UID="$(/usr/bin/id -u "$OWNER")"
CINEMIND="com.cinemind.boot"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo:  sudo $0" >&2
  exit 1
fi

step() { printf '\n▸ %s\n' "$1"; }

step "Stopping the per-login MongoDB agent"
/usr/bin/sudo -u "$OWNER" /bin/launchctl bootout "gui/$OWNER_UID/$LABEL" 2>/dev/null
/bin/rm -f "/Users/$OWNER/Library/LaunchAgents/$LABEL.plist"
/usr/bin/pkill -x mongod 2>/dev/null
sleep 2

step "Installing $LABEL as a boot daemon"
/bin/cat > "$DST" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>homebrew.mxcl.mongodb-community</string>
  <key>UserName</key>
  <string>gilbert</string>
  <key>ProgramArguments</key>
  <array>
    <string>/opt/homebrew/opt/mongodb-community/bin/mongod</string>
    <string>--config</string>
    <string>/opt/homebrew/etc/mongod.conf</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>WorkingDirectory</key>
  <string>/opt/homebrew</string>
  <key>StandardErrorPath</key>
  <string>/opt/homebrew/var/log/mongodb/output.log</string>
  <key>StandardOutPath</key>
  <string>/opt/homebrew/var/log/mongodb/output.log</string>
  <key>HardResourceLimits</key>
  <dict><key>NumberOfFiles</key><integer>64000</integer></dict>
  <key>SoftResourceLimits</key>
  <dict><key>NumberOfFiles</key><integer>64000</integer></dict>
</dict>
</plist>
PLIST
/usr/sbin/chown root:wheel "$DST"
/bin/chmod 644 "$DST"
/bin/launchctl bootout "system/$LABEL" 2>/dev/null
/bin/launchctl bootstrap system "$DST" || { echo "  bootstrap failed" >&2; exit 1; }
/bin/launchctl enable "system/$LABEL"

step "Waiting for MongoDB on port 27017"
for _ in $(seq 1 30); do
  /usr/bin/nc -z 127.0.0.1 27017 >/dev/null 2>&1 && break
  sleep 1
done

step "Restarting CineMind against the boot database"
/bin/launchctl kickstart -k "system/$CINEMIND" >/dev/null 2>&1
ok=0
for _ in $(seq 1 30); do
  /usr/bin/curl -fsS -o /dev/null --max-time 2 http://localhost:8001/api/ && { ok=1; break; }
  sleep 2
done

printf '\n── status ─────────────────────────────\n'
/bin/launchctl print "system/$LABEL" 2>/dev/null | /usr/bin/grep -E '^\s+(state|pid)\s' | /usr/bin/head -2
/bin/launchctl print "system/$CINEMIND" 2>/dev/null | /usr/bin/grep -E '^\s+(state|pid)\s' | /usr/bin/head -2
[ "$ok" -eq 1 ] && echo "  http://localhost:8001 : answering" || echo "  http://localhost:8001 : NOT answering"

if [ "$ok" -eq 1 ]; then
  echo
  echo "Done — MongoDB and CineMind both start at boot, before login."
  exit 0
fi
echo "  Check: tail -20 /Users/$OWNER/Library/Logs/cinemind.error.log"
exit 1
