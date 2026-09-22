#!/bin/bash
# CRA dev server on :3000, talking to the backend already running on :8001.
# Node lives in .runtime/node (there is no system Node on this machine), so it
# has to be put on PATH before npm's `env node` shebang can find it.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$ROOT/.runtime/node/bin:$PATH"
export BROWSER=none
export PORT="${PORT:-3000}"

cd "$ROOT/frontend"
exec npm run start
