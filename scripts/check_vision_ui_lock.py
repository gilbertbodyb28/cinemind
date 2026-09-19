"""Fail if Vision UI theme source files were modified."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "docs" / "vision-ui.lock.json"
FILES = [
    "frontend/src/index.css",
    "frontend/src/App.css",
    "frontend/tailwind.config.js",
    "frontend/public/index.html",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    expected = json.loads(LOCK.read_text())
    failed = []
    for rel in FILES:
        path = ROOT / rel
        if not path.is_file():
            failed.append(f"missing {rel}")
            continue
        digest = sha256(path)
        if expected.get(rel) != digest:
            failed.append(f"changed {rel}")
    if failed:
        print("Vision UI lock failed:")
        for row in failed:
            print(f"  - {row}")
        print("Revert those files. Only Gilbert can unlock the theme.")
        return 1
    print("Vision UI lock OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
