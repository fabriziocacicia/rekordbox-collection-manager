#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

XCODE_PROJ="$PROJECT_ROOT/ios/RekordboxCompanion/RekordboxCompanion.xcodeproj"
PBXPROJ_PATH="$XCODE_PROJ/project.pbxproj"
DIST_DIR="$PROJECT_ROOT/dist"
BUILD_DIR="$PROJECT_ROOT/build/ios"
DERIVED_DATA="$PROJECT_ROOT/build/DerivedData"

PACKAGE_IPA=false
CLEAN_BUILD=false
OUTPUT_DIR="$DIST_DIR"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --package|--ipa)
            PACKAGE_IPA=true
            shift
            ;;
        --clean)
            CLEAN_BUILD=true
            shift
            ;;
        --output)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 [--package] [--clean] [--output <dir>]"
            echo "  (default)     Compiles RekordboxCompanion.app for iOS devices in build/DerivedData"
            echo "  --package     Packages the app bundle into a distributable .ipa file in dist/"
            echo "  --clean       Cleans build cache and previously compiled .ipa artifacts"
            echo "  --output DIR  Custom destination directory for .ipa archive (default: dist/)"
            exit 0
            ;;
        *)
            echo "⚠️  Unknown argument: $1 (ignored)" >&2
            shift
            ;;
    esac
done

echo "📱 Building Rekordbox Companion for iOS..."

# 1. Verify required tools
for tool in xcodebuild ditto; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "❌ Error: Required tool '$tool' not found in PATH." >&2
        exit 1
    fi
done

if [ ! -d "$XCODE_PROJ" ]; then
    echo "❌ Error: Xcode project not found at $XCODE_PROJ" >&2
    exit 1
fi

# 2. Extract Version Metadata
if [ -z "${APP_VERSION:-}" ]; then
    APP_VERSION=$(grep -m 1 'MARKETING_VERSION' "$PBXPROJ_PATH" | sed -E 's/.*MARKETING_VERSION = ([^;]+);/\1/' | tr -d ' "' || true)
fi
if [ -z "$APP_VERSION" ]; then
    APP_VERSION="0.1.0"
fi

BUILD_NUMBER=$(grep -m 1 'CURRENT_PROJECT_VERSION' "$PBXPROJ_PATH" | sed -E 's/.*CURRENT_PROJECT_VERSION = ([^;]+);/\1/' | tr -d ' "' || true)
if [ -z "$BUILD_NUMBER" ]; then
    BUILD_NUMBER="1"
fi

echo "   • Marketing Version: $APP_VERSION"
echo "   • Build Number:      $BUILD_NUMBER"

# 3. Clean if requested
if [ "$CLEAN_BUILD" = true ]; then
    echo "🧹 Cleaning previous build artifacts..."
    rm -rf "$BUILD_DIR" "$DERIVED_DATA"
    rm -f "$OUTPUT_DIR"/RekordboxCompanion*.ipa
fi

# 4. Compile with xcodebuild
echo "🔨 Compiling RekordboxCompanion target via xcodebuild..."
mkdir -p "$DERIVED_DATA"

xcodebuild -project "$XCODE_PROJ" \
    -scheme "RekordboxCompanion" \
    -configuration Release \
    -destination "generic/platform=iOS" \
    -derivedDataPath "$DERIVED_DATA" \
    CODE_SIGN_IDENTITY="" \
    CODE_SIGNING_REQUIRED=NO \
    CODE_SIGNING_ALLOWED=NO \
    build | grep -E '\*\* BUILD |error:|warning: All interface' || true

APP_BUNDLE="$DERIVED_DATA/Build/Products/Release-iphoneos/RekordboxCompanion.app"

if [ ! -d "$APP_BUNDLE" ]; then
    echo "❌ Error: Compiled .app bundle was not found at $APP_BUNDLE" >&2
    exit 1
fi

APP_SIZE=$(du -sh "$APP_BUNDLE" | cut -f1)

# 5. Package into .ipa (Optional / on --package)
IPA_PATH=""
if [ "$PACKAGE_IPA" = true ]; then
    mkdir -p "$OUTPUT_DIR"
    mkdir -p "$BUILD_DIR/Payload"

    IPA_NAME="RekordboxCompanion-v${APP_VERSION}.ipa"
    IPA_PATH="$OUTPUT_DIR/$IPA_NAME"

    echo "📦 Packaging iOS archive into $IPA_NAME..."
    rm -rf "$BUILD_DIR/Payload"/*
    cp -R "$APP_BUNDLE" "$BUILD_DIR/Payload/"

    export COPYFILE_DISABLE=1
    ditto -c -k --norsrc --keepParent "$BUILD_DIR/Payload" "$IPA_PATH"
    rm -rf "$BUILD_DIR/Payload"

    IPA_SIZE=$(du -sh "$IPA_PATH" | cut -f1)
    SHA256=$(shasum -a 256 "$IPA_PATH" | cut -d ' ' -f 1)
fi

echo ""
echo "✅ iOS Build Succeeded:"
echo "   • App Bundle: $APP_BUNDLE ($APP_SIZE)"
if [ "$PACKAGE_IPA" = true ]; then
    echo "   • IPA Archive: $IPA_PATH ($IPA_SIZE)"
    echo "   • SHA256:      $SHA256"
    echo ""
    echo "📲 Distribution:"
    echo "   • To install on iPhone: AirDrop or download '$IPA_NAME' to iPhone and open in SideStore"
else
    echo ""
    echo "💡 To generate an installable .ipa archive, pass --package:"
    echo "   $0 --package"
fi
