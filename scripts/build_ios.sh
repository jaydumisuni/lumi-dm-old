#!/usr/bin/env bash
# Lumi DM — iOS IPA build script (macOS only)
# Requirements: Xcode + xcodegen (brew install xcodegen)

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IOS_DIR="$ROOT/ios"

echo ""
echo " Lumi DM — iOS IPA Builder"
echo " ──────────────────────────────────"
echo " iOS project: $IOS_DIR"
echo ""

# 1. Regenerate icons
echo "[1/4] Regenerating icons from Resouces/my_logo.png ..."
python3 "$ROOT/tools/generate_icons.py" || echo "WARNING: Icon generation failed."

# Convert iconset to .icns on macOS
if [ -d "$ROOT/macos/icons.iconset" ]; then
    iconutil -c icns "$ROOT/macos/icons.iconset" -o "$ROOT/assets/Lumi-DM.icns" 2>/dev/null || true
fi

# 2. Copy background
echo "[2/4] Copying background image ..."
cp -f "$ROOT/Resouces/background.png" "$ROOT/static/background.png" 2>/dev/null || true

# 3. Generate Xcode project with xcodegen
echo "[3/4] Generating Xcode project ..."
cd "$IOS_DIR"
if ! command -v xcodegen &>/dev/null; then
    echo "  xcodegen not found. Installing via Homebrew ..."
    brew install xcodegen
fi
xcodegen generate

echo ""
echo "[4/4] Opening Xcode ..."
open LumiDM.xcodeproj

echo ""
echo " Xcode project generated at:"
echo "   $IOS_DIR/LumiDM.xcodeproj"
echo ""
echo " To build IPA:"
echo "  1. In Xcode: Product > Archive"
echo "  2. Distribute App > Ad Hoc / App Store"
echo ""
echo " For TestFlight or App Store you need an Apple Developer account."
echo " For testing on your own device: Product > Run"
echo ""
