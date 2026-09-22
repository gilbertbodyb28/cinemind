#!/bin/bash
# Rebuilds CineMind.icns from cinemind-tile.svg.
# QuickLook flattens SVG onto white, so the tile is drawn full-bleed and the
# rounded corners are cut afterwards by round_corners.py, which also centres
# the 824px tile on the 1024px canvas macOS expects.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

qlmanage -t -s 824 -o "$WORK" "$HERE/cinemind-tile.svg" >/dev/null 2>&1
/usr/bin/python3 "$HERE/round_corners.py" "$WORK/cinemind-tile.svg.png" "$HERE/CineMind-1024.png"

ISET="$WORK/CineMind.iconset"
mkdir -p "$ISET"
for spec in "16 icon_16x16" "32 icon_16x16@2x" "32 icon_32x32" "64 icon_32x32@2x" \
            "128 icon_128x128" "256 icon_128x128@2x" "256 icon_256x256" \
            "512 icon_256x256@2x" "512 icon_512x512" "1024 icon_512x512@2x"; do
  sz="${spec%% *}"; nm="${spec##* }"
  sips -z "$sz" "$sz" "$HERE/CineMind-1024.png" --out "$ISET/$nm.png" >/dev/null
done

iconutil -c icns "$ISET" -o "$HERE/CineMind.icns"
echo "Skrev $HERE/CineMind.icns"
