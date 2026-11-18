# Standalone macOS Desktop Application Bundling (Issue #11)

## Problem Statement
The existing desktop build script (`scripts/build_app.sh`) compiles a native C wrapper (`scripts/launcher.c`) that hardcodes the developer machine directory and dynamically references the host virtual environment (`$project_root/.venv/lib/...`). Consequently, the compiled `.app` bundle fails to launch on any other Mac that lacks the local git clone and virtual environment.

## Recommended Direction: Pure PyInstaller Standalone Bundling (Direction B)
Replace `scripts/launcher.c` completely with a clean, maintained PyInstaller specification (`scripts/rekordbox_manager.spec`). Day-to-day coding continues to use `uv run rekordbox-manager` directly, while `scripts/build_app.sh` exists strictly to produce an autonomous, self-contained `.app` bundle and compressed `.zip` distribution.

## Key Assumptions
1. **Host & Architecture**: Apple Silicon (arm64, M1–M4) on macOS >= 14.0. No Intel (x86_64) or Universal 2 support needed.
2. **Development Workflow**: Daily development and feature iteration run via `uv run rekordbox-manager`; the build script is only invoked when building releases or testing packaging.
3. **Code Signing**: Ad-hoc signature (`codesign --force --deep -s -`) is sufficient for personal multi-Mac use. Standard macOS Gatekeeper de-quarantine (`xattr -cr` or Right-click > Open) is acceptable.
4. **Toolchain**: Packaged via `uv run pyinstaller` inside the existing `uv` project ecosystem.

## MVP Scope
1. **Dependencies**: Add `pyinstaller` to `[dependency-groups] dev` in `pyproject.toml` and synchronize `uv.lock`.
2. **PyInstaller Spec (`scripts/rekordbox_manager.spec`)**:
   - Entry point: `src/rekordbox_collection_manager/gui/app.py`.
   - Hidden imports: `mutagen`, `pyrekordbox`, `libsql_client`, `pyobjc-framework-cocoa`, and dynamic PySide6 modules.
   - Bundle assets: `assets/icon.icns` and `assets/icon.png`.
   - Plist configuration: `CFBundleDisplayName`, `CFBundleShortVersionString`, `CFBundleIdentifier`, `NSHighResolutionCapable`.
   - Exclusions: `tests`, `pytest`, unneeded Qt frameworks (`QtWebEngine`, `QtQuick`, `Qt3D`).
3. **Build Script Rewrite (`scripts/build_app.sh`)**:
   - Generate `icon.icns` from `assets/icon.png` if needed.
   - Run `uv run pyinstaller --noconfirm scripts/rekordbox_manager.spec`.
   - Ad-hoc codesign the resulting `dist/Rekordbox Collection Manager.app`.
   - Package into `dist/Rekordbox-Collection-Manager-v<version>-macOS-arm64.zip` using macOS `ditto -c -k --sequesterRsrc --keepParent`.
4. **Cleanup**: Remove `scripts/launcher.c`.
5. **Verification**:
   - Extract `.zip` into an isolated folder (e.g., `/tmp/test_isolated/`).
   - Run without virtual environment variables active.
   - Verify GUI launches, queries Rekordbox databases, renders analytics charts, and handles audio metadata.

## Not Doing List
- ❌ Not maintaining a C launcher `--dev` mode (`uv run` already handles this).
- ❌ Not building Intel x86_64 or Universal 2 binaries.
- ❌ Not configuring Apple Developer ID paid notarization (`notarytool`).
- ❌ Not integrating Sparkle or in-app auto-update frameworks.
