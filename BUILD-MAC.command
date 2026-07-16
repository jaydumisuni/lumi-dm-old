#!/usr/bin/env bash
# Double-click this in Finder on macOS to build the Mac .dmg installer
cd "$(dirname "$0")" || exit 1

echo ""
echo " Lumi DM - macOS DMG Builder"
echo " ===================================="
echo ""

# ── Check Python ─────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo " ERROR: Python 3 not found."
    echo " Install from https://www.python.org or: brew install python"
    read -r -p "Press Enter to close..."; exit 1
fi
echo "[1/7] Python: $(python3 --version)"

# ── Check Node ───────────────────────────────────────────────────────────────
if ! command -v node &>/dev/null; then
    echo ""
    echo " ERROR: Node.js not found. Install from https://nodejs.org"
    read -r -p "Press Enter to close..."; exit 1
fi
echo "      Node: $(node --version)"

# ── aria2c (torrent support) ─────────────────────────────────────────────────
echo ""
echo "[2/7] Checking aria2c (torrent engine)..."
ARIA2C_BIN="$(pwd)/tools/aria2c"
if [ ! -f "$ARIA2C_BIN" ]; then
    if command -v brew &>/dev/null; then
        brew install aria2 --quiet
    fi
    if command -v aria2c &>/dev/null; then
        cp "$(which aria2c)" "$ARIA2C_BIN"
        echo " Found and copied aria2c"
    else
        echo " WARNING: aria2c not found — torrent disabled. Install: brew install aria2"
        ARIA2C_BIN=""
    fi
else
    echo " Found: tools/aria2c"
fi

# ── Python deps ──────────────────────────────────────────────────────────────
echo ""
echo "[3/7] Installing Python dependencies..."
python3 -m pip install -r requirements.txt -q
python3 -m pip install pyinstaller -q
echo " Done."

# ── PyInstaller ──────────────────────────────────────────────────────────────
echo ""
echo "[4/7] Bundling Python server..."
rm -rf dist/server

PYINST_ARGS=(
    --onefile
    --name LUMIDM-server
    --distpath dist/server
    --workpath build/pyinstaller
    --specpath build
    --noconfirm
    --exclude-module PyQt5
    --exclude-module PyQt6
    --exclude-module PySide2
    --exclude-module PySide6
    --exclude-module tkinter
    --add-data "core:core"
)
if [ -n "$ARIA2C_BIN" ] && [ -f "$ARIA2C_BIN" ]; then
    PYINST_ARGS+=(--add-binary "$ARIA2C_BIN:.")
fi
PYINST_ARGS+=(server.py)

if ! python3 -m PyInstaller "${PYINST_ARGS[@]}"; then
    echo " ERROR: PyInstaller failed."
    read -r -p "Press Enter to close..."; exit 1
fi
echo " Server binary: dist/server/LUMIDM-server"

# ── Icons ────────────────────────────────────────────────────────────────────
echo ""
echo "[5/7] Generating icons..."
python3 tools/generate_icons.py 2>/dev/null || echo " (using existing icons)"

# ── npm install ──────────────────────────────────────────────────────────────
echo ""
echo "[6/7] Installing Electron dependencies..."
if ! ( cd electron && npm install --prefer-offline 2>/dev/null || npm install ); then
    echo " ERROR: npm install failed."
    read -r -p "Press Enter to close..."; exit 1
fi
echo " Done."

# ── electron-builder ─────────────────────────────────────────────────────────
echo ""
echo "[7/7] Building macOS DMG..."
if ! ( cd electron && npm run build ); then
    echo " ERROR: electron-builder failed."
    read -r -p "Press Enter to close..."; exit 1
fi

echo ""
echo " ============================================"
echo "  BUILD COMPLETE"
echo " ============================================"
echo ""
ls dist/electron/*.dmg 2>/dev/null
echo ""
echo " Drag the .dmg to Applications to install."
echo ""
read -r -p "Press Enter to close..."
