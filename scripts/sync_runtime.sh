#!/bin/bash
# Push the git working tree into the runtime copy the LaunchAgent serves from.
#
# The agent cannot read ~/Documents (macOS denies launchd processes access to
# it), so the code it runs lives in ~/CineMind. Run this from Terminal after a
# backend change or a `yarn build`; it syncs and restarts the service.
set -euo pipefail

SRC="${CINEMIND_SRC:-$HOME/Documents/CineMind}"
DST="${CINEMIND_RUNTIME:-$HOME/CineMind}"
LABEL="com.cinemind.local"
UID_NUM="$(id -u)"
URL="http://localhost:8001"

[ -d "$SRC/backend" ] || { echo "Hittar inte källkoden i $SRC"; exit 1; }
[ -d "$SRC/frontend/build" ] || { echo "Ingen frontend-build i $SRC/frontend/build — kör 'cd frontend && yarn build' först"; exit 1; }

mkdir -p "$DST/backend" "$DST/frontend/build" "$DST/.runtime/python" "$DST/scripts"

echo "Synkar backend ..."
rsync -a --delete \
  --exclude '__pycache__' --exclude '.pytest_cache' --exclude '.DS_Store' \
  --exclude 'tests' \
  "$SRC/backend/" "$DST/backend/"

echo "Synkar frontend-build ..."
rsync -a --delete --exclude '.DS_Store' "$SRC/frontend/build/" "$DST/frontend/build/"

echo "Synkar python-beroenden ..."
rsync -a --delete --exclude '__pycache__' "$SRC/.runtime/python/" "$DST/.runtime/python/"

install -m 755 "$SRC/scripts/run_cinemind_local.sh" "$DST/scripts/run_cinemind_local.sh"
install -m 755 "$SRC/scripts/run_mongod_local.sh"  "$DST/scripts/run_mongod_local.sh"

# The live database in $DST/data/mongo is never synced — it is the original,
# and the copy under the repo is only the backup it was seeded from.

echo "Startar om tjänsten ..."
launchctl kickstart -k "gui/$UID_NUM/$LABEL" >/dev/null 2>&1 || true

for _ in $(seq 1 30); do
  if curl -fs -o /dev/null --max-time 2 "$URL/api/"; then
    echo "Klart — CineMind svarar på $URL"
    exit 0
  fi
  sleep 1
done

echo "Tjänsten svarar inte ännu. Logg:"
tail -20 "$DST/.runtime/cinemind.error.log" 2>/dev/null
exit 1
