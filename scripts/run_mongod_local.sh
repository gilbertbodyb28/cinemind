#!/bin/bash
# MongoDB entry point, started by the LaunchAgent com.cinemind.mongodb.
#
# The server is the official MongoDB 8.3.7 build unpacked into
# .runtime/mongodb (there is no Homebrew on this machine). It runs against the
# existing data directory, bound to localhost only.
set -euo pipefail

APP_DIR="${CINEMIND_APP_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
DBPATH="${CINEMIND_DBPATH:-$APP_DIR/data/mongo}"
PORT="${CINEMIND_MONGO_PORT:-27017}"

mkdir -p "$DBPATH" "$APP_DIR/.runtime"

exec "$APP_DIR/.runtime/mongodb/bin/mongod" \
  --dbpath "$DBPATH" \
  --bind_ip 127.0.0.1 \
  --port "$PORT" \
  --wiredTigerCacheSizeGB 1
