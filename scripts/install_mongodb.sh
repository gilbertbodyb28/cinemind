#!/bin/bash
# Downloads the official MongoDB build into the runtime folder.
#
# There is no Homebrew on this machine, so the server comes straight from
# fastdl.mongodb.org as a tarball, is checksum-verified, and is unpacked into
# ~/CineMind/.runtime/mongodb. Nothing is installed system-wide and no admin
# password is needed.
set -euo pipefail

VERSION="${CINEMIND_MONGO_VERSION:-8.3.7}"   # the version that wrote data/mongo
RUNTIME="${CINEMIND_RUNTIME:-$HOME/CineMind}"
DEST="$RUNTIME/.runtime/mongodb"
ARCH="$(uname -m)"
case "$ARCH" in
  arm64) PKG="mongodb-macos-arm64-$VERSION" ;;
  x86_64) PKG="mongodb-macos-x86_64-$VERSION" ;;
  *) echo "Okänd arkitektur: $ARCH"; exit 1 ;;
esac
URL="https://fastdl.mongodb.org/osx/$PKG.tgz"

if [ -x "$DEST/bin/mongod" ] && "$DEST/bin/mongod" --version 2>/dev/null | grep -q "v$VERSION"; then
  echo "MongoDB $VERSION finns redan i $DEST"
  exit 0
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "Hämtar $URL ..."
curl -fL --progress-bar --max-time 900 -o "$WORK/mongodb.tgz" "$URL"

echo "Kontrollerar checksumma ..."
EXPECTED="$(curl -fsL --max-time 60 "$URL.sha256" | awk '{print $1}')"
ACTUAL="$(shasum -a 256 "$WORK/mongodb.tgz" | awk '{print $1}')"
if [ -z "$EXPECTED" ] || [ "$EXPECTED" != "$ACTUAL" ]; then
  echo "Checksumman stämmer inte — avbryter."
  echo "  förväntad: ${EXPECTED:-<saknas>}"
  echo "  faktisk:   $ACTUAL"
  exit 1
fi

tar -xzf "$WORK/mongodb.tgz" -C "$WORK"
SRCDIR="$(find "$WORK" -maxdepth 1 -type d -name 'mongodb-macos-*' | head -1)"
[ -x "$SRCDIR/bin/mongod" ] || { echo "Hittar ingen mongod i arkivet"; exit 1; }

mkdir -p "$RUNTIME/.runtime"
rm -rf "$DEST"
mv "$SRCDIR" "$DEST"
xattr -dr com.apple.quarantine "$DEST" 2>/dev/null || true

"$DEST/bin/mongod" --version | head -1
echo "Klart — $DEST"
