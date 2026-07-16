#!/usr/bin/env bash
# Double-click this in Finder on macOS to build the iOS app
cd "$(dirname "$0")" || exit 1

echo ""
echo " Lumi DM - iOS App Builder"
echo " =================================="
echo ""

# ── Must run on macOS ────────────────────────────────────────────────────────
if [[ "$(uname)" != "Darwin" ]]; then
    echo " ERROR: iOS apps can only be built on macOS."
    echo " Apple requires Xcode, which is only available on Mac."
    read -r -p "Press Enter to close..."; exit 1
fi

# ── Check Xcode ──────────────────────────────────────────────────────────────
echo "[1/4] Checking Xcode..."
if ! xcode-select -p &>/dev/null; then
    echo " ERROR: Xcode command line tools not found."
    echo " Run: xcode-select --install"
    echo " Or install Xcode from the App Store."
    read -r -p "Press Enter to close..."; exit 1
fi
XCODE_VER=$(xcodebuild -version 2>/dev/null | head -1)
echo " Found: $XCODE_VER"

# ── Check / install XcodeGen ─────────────────────────────────────────────────
echo ""
echo "[2/4] Checking XcodeGen..."
if ! command -v xcodegen &>/dev/null; then
    echo " XcodeGen not found — installing via Homebrew..."
    if ! command -v brew &>/dev/null; then
        echo " ERROR: Homebrew not found. Install from https://brew.sh then re-run."
        read -r -p "Press Enter to close..."; exit 1
    fi
    brew install xcodegen --quiet
    if ! command -v xcodegen &>/dev/null; then
        echo " ERROR: XcodeGen install failed."
        read -r -p "Press Enter to close..."; exit 1
    fi
fi
echo " Found: xcodegen $(xcodegen version 2>/dev/null)"

# ── Generate Xcode project ───────────────────────────────────────────────────
echo ""
echo "[3/4] Generating Xcode project..."
cd ios || exit 1
if ! xcodegen generate --quiet; then
    echo " ERROR: xcodegen failed. Check ios/project.yml for errors."
    read -r -p "Press Enter to close..."; exit 1
fi
echo " Generated: ios/LumiDM.xcodeproj"

# ── Build for simulator (no signing required) ────────────────────────────────
echo ""
echo "[4/4] Building for iOS Simulator..."
mkdir -p ../dist

if xcodebuild \
    -project LumiDM.xcodeproj \
    -scheme LumiDM \
    -configuration Release \
    -sdk iphonesimulator \
    -derivedDataPath ../build/ios \
    CODE_SIGNING_ALLOWED=NO \
    -quiet 2>/dev/null; then

    APP_PATH=$(find ../build/ios -name "LumiDM.app" 2>/dev/null | head -1)
    if [ -n "$APP_PATH" ]; then
        # Wrap in a .zip so it's easy to find in dist/
        cd ../dist
        zip -r -q "Lumi-DM-1.0.0-simulator.zip" "$APP_PATH"
        echo ""
        echo " ============================================"
        echo "  BUILD COMPLETE (Simulator)"
        echo " ============================================"
        echo ""
        echo "  dist/Lumi-DM-1.0.0-simulator.zip"
        echo ""
    fi
else
    echo ""
    echo " Simulator build failed — opening project in Xcode instead."
fi

# ── Open Xcode for device / App Store build ──────────────────────────────────
cd "$(dirname "$0")/ios" 2>/dev/null
open LumiDM.xcodeproj 2>/dev/null

echo ""
echo " ============================================"
echo "  TO INSTALL ON A REAL iPhone / iPad:"
echo " ============================================"
echo ""
echo "  1. Plug in your device via USB"
echo "  2. In Xcode: select your device in the toolbar"
echo "  3. Product → Run  (or Cmd+R)"
echo "     (free Apple ID works for personal testing)"
echo ""
echo "  FOR APP STORE / TESTFLIGHT:"
echo "  1. Product → Archive"
echo "  2. Distribute App → App Store Connect"
echo "     (requires paid Apple Developer account — \$99/year)"
echo ""
read -r -p "Press Enter to close..."
