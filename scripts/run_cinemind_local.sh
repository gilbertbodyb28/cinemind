#!/bin/bash
# CineMind service entry point, started by the LaunchAgent com.cinemind.local.
# One uvicorn process serves the API and the built frontend on :8001.
#
# APP_DIR is the runtime copy (~/CineMind), not the git working tree in
# ~/Documents, because launchd is denied access to the Documents folder.
# Use scripts/sync_runtime.sh to push the working tree into the runtime copy.
set -euo pipefail

APP_DIR="${CINEMIND_APP_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
PORT="${CINEMIND_PORT:-8001}"
MONGO_PORT="${CINEMIND_MONGO_PORT:-27017}"
LIFESPAN="${CINEMIND_LIFESPAN:-on}"

mkdir -p "$APP_DIR/.runtime"
export PYTHONPATH="$APP_DIR/.runtime/python:$APP_DIR/backend"
export PYTHONUNBUFFERED=1

# MongoDB is its own LaunchAgent; after a reboot both start at once, so give
# the database a head start before the scheduler reaches for it.
for _ in $(seq 1 60); do
  /usr/bin/nc -z 127.0.0.1 "$MONGO_PORT" >/dev/null 2>&1 && break
  sleep 1
done

cd "$APP_DIR/backend"
exec /usr/bin/python3 -m uvicorn server:app \
  --host 0.0.0.0 \
  --port "$PORT" \
  --log-level info \
  --loop asyncio \
  --http h11 \
  --lifespan "$LIFESPAN"
