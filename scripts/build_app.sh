#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "🔨 Building standalone macOS App Bundle for Rekordbox Collection Manager..."

# 1. Generate icon.icns if needed
if [ ! -f "$PROJECT_ROOT/assets/icon.icns" ] && [ -f "$PROJECT_ROOT/assets/icon.png" ]; then
    echo "🎨 Generating Apple Icon (icon.icns)..."
    ICONSET="$PROJECT_ROOT/assets/icon.iconset"
    mkdir -p "$ICONSET"
    sips -z 16 16     "$PROJECT_ROOT/assets/icon.png" --out "$ICONSET/icon_16x16.png" > /dev/null 2>&1
    sips -z 32 32     "$PROJECT_ROOT/assets/icon.png" --out "$ICONSET/icon_16x16@2x.png" > /dev/null 2>&1
    sips -z 32 32     "$PROJECT_ROOT/assets/icon.png" --out "$ICONSET/icon_32x32.png" > /dev/null 2>&1
    sips -z 64 64     "$PROJECT_ROOT/assets/icon.png" --out "$ICONSET/icon_32x32@2x.png" > /dev/null 2>&1
    sips -z 128 128   "$PROJECT_ROOT/assets/icon.png" --out "$ICONSET/icon_128x128.png" > /dev/null 2>&1
    sips -z 256 256   "$PROJECT_ROOT/assets/icon.png" --out "$ICONSET/icon_128x128@2x.png" > /dev/null 2>&1
    sips -z 256 256   "$PROJECT_ROOT/assets/icon.png" --out "$ICONSET/icon_256x256.png" > /dev/null 2>&1
    sips -z 512 512   "$PROJECT_ROOT/assets/icon.png" --out "$ICONSET/icon_256x256@2x.png" > /dev/null 2>&1
    sips -z 512 512   "$PROJECT_ROOT/assets/icon.png" --out "$ICONSET/icon_512x512.png" > /dev/null 2>&1
    sips -z 1024 1024 "$PROJECT_ROOT/assets/icon.png" --out "$ICONSET/icon_512x512@2x.png" > /dev/null 2>&1
    iconutil -c icns "$ICONSET" -o "$PROJECT_ROOT/assets/icon.icns"
    rm -rf "$ICONSET"
fi
rm -rf "$PROJECT_ROOT/assets/icon.iconset"

# 2. Extract Application Version
APP_VERSION=$(grep -m 1 '^version = ' "$PROJECT_ROOT/pyproject.toml" | cut -d '"' -f 2)
if [ -z "$APP_VERSION" ]; then
    APP_VERSION="0.20.0"
fi

# 3. Compile Standalone Bundle via PyInstaller
echo "📦 Compiling standalone Python & PySide6 bundle via PyInstaller..."
cd "$PROJECT_ROOT"
uv run pyinstaller --noconfirm "$SCRIPT_DIR/rekordbox_manager.spec"

APP_DIR="$PROJECT_ROOT/dist/Rekordbox Collection Manager.app"

if [ ! -d "$APP_DIR" ]; then
    echo "❌ Error: App bundle was not created at $APP_DIR" >&2
    exit 1
fi

# 4. Ad-hoc Code Sign App Bundle
echo "🔏 Code signing app bundle..."
codesign --force --deep -s - "$APP_DIR" > /dev/null 2>&1 || true

# 5. Package Distributable Zip Archive
ZIP_NAME="Rekordbox-Collection-Manager-v${APP_VERSION}-macOS-arm64.zip"
ZIP_PATH="$PROJECT_ROOT/dist/$ZIP_NAME"
echo "🗜️  Archiving bundle with ditto ($ZIP_NAME)..."
rm -f "$ZIP_PATH"
ditto -c -k --sequesterRsrc --keepParent "$APP_DIR" "$ZIP_PATH"

APP_SIZE=$(du -sh "$APP_DIR" | cut -f1)
ZIP_SIZE=$(du -sh "$ZIP_PATH" | cut -f1)

echo "✅ Standalone App bundle successfully built:"
echo "   • Bundle:  $APP_DIR ($APP_SIZE)"
echo "   • Archive: $ZIP_PATH ($ZIP_SIZE)"
echo ""
echo "🚀 Portable Distribution:"
echo "   • To install locally: cp -R \"$APP_DIR\" /Applications/"
echo "   • To test on another Mac: unzip $ZIP_NAME and open"
