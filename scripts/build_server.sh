#!/usr/bin/env bash
set -euo pipefail

NAME=${1:-Lumi-DM}

printf '%s\n' "Normalizing Lumi branding"
python scripts/normalize_branding.py

printf '%s\n' "Building server binary with PyInstaller (name: $NAME)"
python -m pip install --upgrade pyinstaller
pyinstaller --onefile --noconsole --add-data "static:static" --name "$NAME" server.py

printf '%s\n' "Build finished. See dist/$NAME"
