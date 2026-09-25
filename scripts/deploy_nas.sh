#!/bin/bash
# Deploy CineMind into MediaManager's compose project on the NAS.
#
#   scripts/deploy_nas.sh            # build frontend, ship code, rebuild + restart cinemind
#   scripts/deploy_nas.sh --env      # also (re)write cinemind.env from backend/.env
#
# Code goes to /vol2/1000/MediaManager/cinemind over SSH (tar, not the SMB
# mount: SMB writes have come back empty/wrong there). Only the cinemind
# services are touched; MediaManager itself is never rebuilt by this script.
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
NAS_HOST="gibbe21@192.168.50.94"
NAS_DIR="/vol2/1000/MediaManager"
NAS_URL="http://192.168.50.94:8001"
nas() {
  ssh -i "$HOME/.ssh/id_ed25519_mediamanager_nas" -o IdentitiesOnly=yes -o BatchMode=yes \
      -o PreferredAuthentications=publickey -p 22 "$NAS_HOST" "$@"
}

echo "Bygger frontend ..."
(cd "$SRC/frontend" && PATH="$SRC/.runtime/node/bin:$PATH" npm run build >/dev/null)

echo "Skickar koden till NAS:en ..."
COPYFILE_DISABLE=1 tar --no-xattrs -C "$SRC" --exclude '__pycache__' --exclude '.pytest_cache' --exclude '.DS_Store' \
    --exclude 'backend/.env' --exclude 'backend/tests' -czf - backend frontend/build deploy/nas \
  | nas "set -e; mkdir -p $NAS_DIR/cinemind.new && tar -xzf - -C $NAS_DIR/cinemind.new \
         && mkdir -p $NAS_DIR/cinemind && cp -p $NAS_DIR/cinemind/cinemind.env $NAS_DIR/cinemind.new/ 2>/dev/null || true; \
         rm -rf $NAS_DIR/cinemind.old; [ -d $NAS_DIR/cinemind ] && mv $NAS_DIR/cinemind $NAS_DIR/cinemind.old; \
         mv $NAS_DIR/cinemind.new $NAS_DIR/cinemind"

if [ "${1:-}" = "--env" ]; then
  echo "Skriver cinemind.env (hemligheterna visas inte) ..."
  # The NAS serves CineMind on its LAN address; everything else is the Mac's .env.
  grep -vE '^(MONGO_URL|FRONTEND_URL|CORS_ORIGINS)=' "$SRC/backend/.env" \
    | { cat; echo "FRONTEND_URL=\"$NAS_URL\""; \
        echo "CORS_ORIGINS=\"$NAS_URL,http://192.168.50.94:8000,http://localhost:8001\""; } \
    | nas "umask 077; cat > $NAS_DIR/cinemind/.cinemind.env.tmp && mv $NAS_DIR/cinemind/.cinemind.env.tmp $NAS_DIR/cinemind/cinemind.env"
fi

[ -n "${CINEMIND_SKIP_UP:-}" ] && { echo "Hoppar över start (CINEMIND_SKIP_UP)"; exit 0; }
echo "Bygger och startar cinemind på NAS:en ..."
nas "cd $NAS_DIR && docker compose up -d --build cinemind cinemind-mongo"
echo "Klart — CineMind på $NAS_URL"
