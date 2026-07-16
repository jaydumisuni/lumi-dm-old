#!/usr/bin/env bash
set -euo pipefail
NAME=${1:-Lumi-DM}
echo "Building server binary with PyInstaller (name: $NAME)"
python -m pip install --upgrade pyinstaller
pyinstaller --onefile --noconsole --add-data "static:static" --name "$NAME" server.py
echo "Build finished. See dist/$NAME"
