#!/usr/bin/env bash
# Run this on Linux to build the Linux AppImage
# First time: chmod +x BUILD-LINUX.sh && ./BUILD-LINUX.sh
cd "$(dirname "$0")" || exit 1

echo ""
echo " Lumi DM - Linux AppImage Builder"
echo " ========================================="
echo ""

# ── Check Python ─────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo " ERROR: Python 3 not found. sudo apt install python3 python3-pip"
    exit 1
fi
echo "[1/7] Python: $(python3 --version)"

# ── Check Node ───────────────────────────────────────────────────────────────
if ! command -v node &>/dev/null; then
    echo ""
    echo " ERROR: Node.js not found."
    echo " Install: sudo apt install nodejs npm  OR  https://nodejs.org"
    exit 1
fi
echo "      Node: $(node --version)"

# ── aria2c (torrent support) ─────────────────────────────────────────────────
echo ""
echo "[2/7] Checking aria2c (torrent engine)..."
ARIA2C_BIN="$(pwd)/tools/aria2c"
if [ ! -f "$ARIA2C_BIN" ]; then
    if command -v aria2c &>/dev/null; then
        cp "$(which aria2c)" "$ARIA2C_BIN"
        echo " Found and copied aria2c"
    else
        echo " aria2c not found — attempting install..."
        if command -v apt-get &>/dev/null; then
            sudo apt-get install -y aria2 -q
        elif command -v dnf &>/dev/null; then
            sudo dnf install -y aria2 -q
        elif command -v pacman &>/dev/null; then
            sudo pacman -S --noconfirm aria2
        fi
        if command -v aria2c &>/dev/null; then
            cp "$(which aria2c)" "$ARIA2C_BIN"
            echo " Installed and copied aria2c"
        else
            echo " WARNING: aria2c not available — torrent disabled."
            ARIA2C_BIN=""
        fi
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
    exit 1
fi
echo " Server binary: dist/server/LUMIDM-server"

# ── Icons ────────────────────────────────────────────────────────────────────
echo ""
echo "[5/7] Generating icons..."
python3 tools/generate_icons.py 2>/dev/null || echo " (using existing icons)"

# ── npm install ──────────────────────────────────────────────────────────────
echo ""
echo "[6/7] Installing Electron dependencies..."
if ! ( cd electron && { npm install --prefer-offline 2>/dev/null || npm install; } ); then
    echo " ERROR: npm install failed."
    exit 1
fi
echo " Done."

# ── electron-builder ─────────────────────────────────────────────────────────
echo ""
echo "[7/7] Building Linux AppImage..."
if ! ( cd electron && npm run build ); then
    echo " ERROR: electron-builder failed."
    exit 1
fi

echo ""
echo " ============================================"
echo "  BUILD COMPLETE"
echo " ============================================"
echo ""
ls dist/electron/*.AppImage 2>/dev/null
echo ""
echo " To run: chmod +x lumi*.AppImage && ./lumi*.AppImage"
echo ""
