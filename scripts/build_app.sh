#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "🔨 Building native macOS App Bundle for Rekordbox Collection Manager..."

# 1. Generate icon.icns if needed
if [ -f "$PROJECT_ROOT/assets/icon.png" ]; then
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

# 2. Build .app Bundle Structure
APP_DIR="$PROJECT_ROOT/dist/Rekordbox Collection Manager.app"
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"

if [ -f "$PROJECT_ROOT/assets/icon.icns" ]; then
    cp "$PROJECT_ROOT/assets/icon.icns" "$APP_DIR/Contents/Resources/icon.icns"
fi

# 3. Write Info.plist
APP_VERSION=$(grep -m 1 '^version = ' "$PROJECT_ROOT/pyproject.toml" | cut -d '"' -f 2)
if [ -z "$APP_VERSION" ]; then
    APP_VERSION="0.2.0"
fi

cat << EOF > "$APP_DIR/Contents/Info.plist"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key>
    <string>en</string>
    <key>CFBundleDisplayName</key>
    <string>Rekordbox Collection Manager</string>
    <key>CFBundleExecutable</key>
    <string>Rekordbox Collection Manager</string>
    <key>CFBundleIconFile</key>
    <string>icon.icns</string>
    <key>CFBundleIdentifier</key>
    <string>com.fabriziocacicia.rekordbox-collection-manager</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleName</key>
    <string>Rekordbox Collection Manager</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>${APP_VERSION}</string>
    <key>CFBundleVersion</key>
    <string>${APP_VERSION}</string>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
EOF

# 4. Write Executable Launcher
cat << 'EOF' > "$APP_DIR/Contents/MacOS/Rekordbox Collection Manager"
#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -d "$SCRIPT_DIR/../../../.venv" ]; then
    PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
else
    PROJECT_ROOT="/Users/fabriziocacicia/Projects/Personal/Music/rekordbox-collection-manager"
fi

export PATH="$PROJECT_ROOT/.venv/bin:$PATH"
cd "$PROJECT_ROOT"
exec "$PROJECT_ROOT/.venv/bin/python" -m rekordbox_collection_manager.gui.app "$@"
EOF

chmod +x "$APP_DIR/Contents/MacOS/Rekordbox Collection Manager"

echo "✅ App bundle successfully built at:"
echo "   $APP_DIR"
echo ""
echo "🚀 You can now:"
echo "   • Open it with: open \"$APP_DIR\""
echo "   • Drag it into your Applications folder or Dock to keep it permanently."
