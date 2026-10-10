"""Refresh the bundled Tabler icon webfont.

Usage: python tools/update_tabler.py <path to extracted @tabler/icons-webfont package dir>

Copies the outline + filled TTFs and the licence into src/hubdeck/assets/tabler
and writes icons.json ({"version", "outline": {name: codepoint}, "filled": {...}}).
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

DEST = Path(__file__).resolve().parents[1] / "src" / "hubdeck" / "assets" / "tabler"
RULE = re.compile(r'\.ti-([a-z0-9-]+):before\s*\{\s*content:\s*"\\([0-9a-f]+)"')


def parse_css(path: Path) -> dict[str, int]:
    return {name: int(cp, 16) for name, cp in RULE.findall(path.read_text(encoding="utf-8"))}


def main():
    pkg = Path(sys.argv[1])
    dist = pkg / "dist"
    version = json.loads((pkg / "package.json").read_text(encoding="utf-8"))["version"]
    DEST.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(dist / "fonts" / "tabler-icons.ttf", DEST / "tabler-icons.ttf")
    shutil.copyfile(dist / "fonts" / "tabler-icons-filled.ttf", DEST / "tabler-icons-filled.ttf")
    shutil.copyfile(pkg / "LICENSE", DEST / "LICENSE")
    data = {
        "version": version,
        "outline": parse_css(dist / "tabler-icons.css"),
        "filled": parse_css(dist / "tabler-icons-filled.css"),
    }
    (DEST / "icons.json").write_text(json.dumps(data, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    print(f"Tabler {version}: {len(data['outline'])} outline, {len(data['filled'])} filled icons -> {DEST}")


if __name__ == "__main__":
    main()
