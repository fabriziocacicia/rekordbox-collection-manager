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

# 4. Compile Native Mach-O Executable Launcher
echo "⚙️  Compiling native Mach-O executable launcher..."
PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="$(which python3)"
fi

PYTHON_HOME=$("$PYTHON_BIN" -c "import sys; print(sys.base_prefix)")
PYTHON_LIBDIR=$("$PYTHON_BIN" -c "import sysconfig; print(sysconfig.get_config_var('LIBDIR') or '')")
PYTHON_LDLIBRARY=$("$PYTHON_BIN" -c "import sysconfig; print(sysconfig.get_config_var('LDLIBRARY') or '')")
PYTHON_VERSION_STR=$("$PYTHON_BIN" -c "import sys; print(f'python{sys.version_info.major}.{sys.version_info.minor}')")

DYLIB_PATH=""
if [ -n "$PYTHON_LIBDIR" ] && [ -n "$PYTHON_LDLIBRARY" ] && [ -f "$PYTHON_LIBDIR/$PYTHON_LDLIBRARY" ]; then
    DYLIB_PATH="$PYTHON_LIBDIR/$PYTHON_LDLIBRARY"
fi

mkdir -p "$APP_DIR/Contents/Frameworks"
if [ -n "$DYLIB_PATH" ] && [ -f "$DYLIB_PATH" ]; then
    echo "📦 Bundling Python runtime library ($PYTHON_LDLIBRARY)..."
    cp "$DYLIB_PATH" "$APP_DIR/Contents/Frameworks/"
fi

clang -O2 \
    -DDEFAULT_PROJECT_ROOT="\"$PROJECT_ROOT\"" \
    -DDEFAULT_PYTHON_HOME="\"$PYTHON_HOME\"" \
    -DDEFAULT_DYLIB_PATH="\"$DYLIB_PATH\"" \
    -DPYTHON_VERSION_STR="\"$PYTHON_VERSION_STR\"" \
    -DAPP_MODULE="\"rekordbox_collection_manager.gui.app\"" \
    -DAPP_NAME="\"Rekordbox Collection Manager\"" \
    "$PROJECT_ROOT/scripts/launcher.c" \
    -o "$APP_DIR/Contents/MacOS/Rekordbox Collection Manager"

chmod +x "$APP_DIR/Contents/MacOS/Rekordbox Collection Manager"

# 5. Ad-hoc Code Sign App Bundle
echo "🔏 Code signing app bundle..."
codesign --force --deep -s - "$APP_DIR" > /dev/null 2>&1 || true

echo "✅ App bundle successfully built at:"
echo "   $APP_DIR"
echo ""
echo "🚀 You can now:"
echo "   • Open it with: open \"$APP_DIR\""
echo "   • Drag it into your Applications folder or Dock to keep it permanently."
if [ -d "/Applications/Rekordbox Collection Manager.app" ]; then
    echo "   ⚠️  Note: If you previously copied this app to /Applications, replace it with:"
    echo "      cp -R \"$APP_DIR\" /Applications/"
fi
