#!/bin/bash
# One command that sets CineMind up to run by itself:
#
#   1. copies the code into the runtime folder ~/CineMind (launchd is denied
#      access to ~/Documents, so it cannot run the working tree directly),
#   2. makes sure MongoDB is unpacked and seeds the data directory once,
#   3. installs two LaunchAgents — com.cinemind.mongodb and com.cinemind.local.
#      Both start at every login and are restarted if the process dies,
#   4. builds /Applications/CineMind.app, the icon that opens the app.
#
# Run it from Terminal (not from launchd) so macOS can grant access to
# ~/Documents. Safe to run again at any time; it never touches the live
# database once it exists.
set -euo pipefail

SRC="${CINEMIND_SRC:-$(cd "$(dirname "$0")/.." && pwd)}"
RUNTIME="${CINEMIND_RUNTIME:-$HOME/CineMind}"
APP="${CINEMIND_APP_BUNDLE:-/Applications/CineMind.app}"
UID_NUM="$(id -u)"
URL="http://localhost:8001"

load_agent() {   # label  plist-template
  local label="$1" template="$2"
  local plist="$HOME/Library/LaunchAgents/$label.plist"
  mkdir -p "$HOME/Library/LaunchAgents"
  sed "s#__RUNTIME__#$RUNTIME#g" "$template" > "$plist"
  plutil -lint "$plist" >/dev/null
  launchctl bootout "gui/$UID_NUM/$label" >/dev/null 2>&1 || true
  # launchd needs a moment to let go; bootstrapping too early fails with EIO.
  for _ in $(seq 1 10); do
    launchctl print "gui/$UID_NUM/$label" >/dev/null 2>&1 || break
    sleep 1
  done
  for attempt in 1 2 3 4 5; do
    launchctl bootstrap "gui/$UID_NUM" "$plist" 2>/dev/null && break
    if [ "$attempt" = 5 ]; then
      echo "Kunde inte ladda $label:"
      launchctl bootstrap "gui/$UID_NUM" "$plist"
      exit 1
    fi
    sleep 2
  done
  launchctl enable "gui/$UID_NUM/$label"
}

echo "== 1/5  Runtime-kopia i $RUNTIME"
mkdir -p "$RUNTIME/backend" "$RUNTIME/frontend/build" "$RUNTIME/.runtime/python" \
         "$RUNTIME/scripts" "$RUNTIME/data"
rsync -a --delete --exclude '__pycache__' --exclude '.pytest_cache' --exclude '.DS_Store' \
  --exclude 'tests' "$SRC/backend/" "$RUNTIME/backend/"
rsync -a --delete --exclude '.DS_Store' "$SRC/frontend/build/" "$RUNTIME/frontend/build/"
rsync -a --delete --exclude '__pycache__' "$SRC/.runtime/python/" "$RUNTIME/.runtime/python/"
install -m 755 "$SRC/scripts/run_cinemind_local.sh" "$RUNTIME/scripts/run_cinemind_local.sh"
install -m 755 "$SRC/scripts/run_mongod_local.sh"  "$RUNTIME/scripts/run_mongod_local.sh"

echo "== 2/5  MongoDB"
CINEMIND_RUNTIME="$RUNTIME" "$SRC/scripts/install_mongodb.sh"
if [ ! -f "$RUNTIME/data/mongo/storage.bson" ]; then
  if [ -f "$SRC/data/mongo/storage.bson" ]; then
    echo "Kopierar databasen från $SRC/data/mongo (engångs) ..."
    rsync -a --sparse --exclude '.DS_Store' "$SRC/data/mongo/" "$RUNTIME/data/mongo/"
  else
    echo "Ingen befintlig databas — mongod skapar en ny i $RUNTIME/data/mongo"
    mkdir -p "$RUNTIME/data/mongo"
  fi
fi

echo "== 3/5  LaunchAgents"
# Older agents from earlier setups would fight over port 8001.
for old in com.cinemind.app com.cinemind.boot; do
  launchctl bootout "gui/$UID_NUM/$old" >/dev/null 2>&1 || true
  rm -f "$HOME/Library/LaunchAgents/$old.plist"
done
load_agent com.cinemind.mongodb "$SRC/scripts/com.cinemind.mongodb.plist"
for _ in $(seq 1 60); do
  /usr/bin/nc -z 127.0.0.1 27017 >/dev/null 2>&1 && break
  sleep 1
done
load_agent com.cinemind.local "$SRC/scripts/com.cinemind.local.plist"

echo "== 4/5  $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
install -m 755 "$SRC/scripts/cinemind_launcher.sh" "$APP/Contents/MacOS/CineMind"
install -m 644 "$SRC/scripts/icon/CineMind.icns" "$APP/Contents/Resources/CineMind.icns"
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>CineMind</string>
  <key>CFBundleDisplayName</key><string>CineMind</string>
  <key>CFBundleIdentifier</key><string>com.cinemind.launcher</string>
  <key>CFBundleExecutable</key><string>CineMind</string>
  <key>CFBundleIconFile</key><string>CineMind</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST
plutil -lint "$APP/Contents/Info.plist" >/dev/null
codesign --force --sign - "$APP" >/dev/null 2>&1 || true
xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true
touch "$APP"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
  -f "$APP" >/dev/null 2>&1 || true

echo "== 5/5  Väntar på att tjänsten svarar"
for _ in $(seq 1 45); do
  if curl -fs -o /dev/null --max-time 2 "$URL/api/"; then
    echo
    echo "Klart."
    echo "  Tjänsten:  $URL  (startar vid varje inloggning, startas om automatiskt)"
    echo "  Databasen: $RUNTIME/data/mongo  (mongod på 127.0.0.1:27017)"
    echo "  Ikonen:    $APP"
    echo "  Loggar:    $RUNTIME/.runtime/cinemind.log, $RUNTIME/.runtime/mongod.log"
    exit 0
  fi
  sleep 1
done

echo "Tjänsten svarar inte ännu. Loggar:"
tail -20 "$RUNTIME/.runtime/cinemind.error.log" 2>/dev/null
tail -10 "$RUNTIME/.runtime/mongod.error.log" 2>/dev/null
exit 1
