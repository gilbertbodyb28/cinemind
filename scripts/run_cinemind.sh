#!/bin/bash
# CineMind service entry point, used by the LaunchAgent com.cinemind.app.
# Serves the API and the built frontend from one uvicorn process on :8001.
set -euo pipefail

APP_DIR="/Users/gilbert/CineMind"
PORT="${CINEMIND_PORT:-8001}"

cd "$APP_DIR/backend"

# MongoDB is a brew service; give it a moment after a reboot before connecting.
for _ in $(seq 1 30); do
  if /usr/bin/nc -z 127.0.0.1 27017 >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

exec "$APP_DIR/venv/bin/uvicorn" server:app \
  --host 0.0.0.0 \
  --port "$PORT" \
  --log-level info
