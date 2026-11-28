"""Prepare root-level PocketBolt frontend files for Flask's /static routes.

The repo currently keeps the web assets at its root, while app.py serves them
from a static/ directory. This script stages the expected layout during build
and creates the PWA icons. It is safe to run repeatedly.
"""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
ICONS = STATIC / "icons"
STATIC.mkdir(exist_ok=True)
ICONS.mkdir(parents=True, exist_ok=True)

for name in ("index.html", "app.js", "styles.css", "sw.js", "manifest.webmanifest"):
    source = ROOT / name
    if not source.is_file():
        raise SystemExit(f"Required PocketBolt frontend asset is missing: {source}")
    (STATIC / name).write_bytes(source.read_bytes())

subprocess.run([sys.executable, str(ROOT / "make_icons.py")], cwd=ICONS, check=True)
print("Prepared Flask static assets and PWA icons.")
