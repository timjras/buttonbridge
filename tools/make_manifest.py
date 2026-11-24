"""Writes the ESP Web Tools manifest for the install page (docs/flasher/index.html).

Usage: python3 tools/make_manifest.py <folder with the -full.bin files> <version>

The page uses Tasmota's ESP Web Tools, which tells ESP32-P4 silicon revisions apart: v3.x chips
get the main P4 build, and any older P4 falls through to the build without a chipVariant.
"""
import json
import re
import sys
from pathlib import Path

folder, version = Path(sys.argv[1]), sys.argv[2]


def image(name: str) -> str:
    pattern = re.compile(rf"bridge-{re.escape(name)}-\d[^/]*-full\.bin")
    matches = sorted(p for p in folder.glob("*-full.bin") if pattern.fullmatch(p.name))
    if len(matches) != 1:
        sys.exit(f"Expected one bridge-{name}-*-full.bin in {folder}, found {len(matches)}")
    return f"firmware/{matches[0].name}"


manifest = {
    "name": "ButtonBridge bridge",
    "version": version,
    "new_install_prompt_erase": True,
    "builds": [
        {"chipFamily": "ESP32-P4", "chipVariant": "rev300", "parts": [{"path": image("esp32p4"), "offset": 0}]},
        {"chipFamily": "ESP32-P4", "parts": [{"path": image("esp32p4-rev1"), "offset": 0}]},
        {"chipFamily": "ESP32-S3", "parts": [{"path": image("esp32s3"), "offset": 0}]},
    ],
}
print(json.dumps(manifest, indent=2))
