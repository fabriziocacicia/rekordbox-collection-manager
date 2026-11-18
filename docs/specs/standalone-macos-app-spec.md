# Specification: Standalone macOS Desktop Application Bundling (Issue #11)

**Issue**: [#11](https://github.com/fabriziocacicia/rekordbox-collection-manager/issues/11)  
**PR**: [#14](https://github.com/fabriziocacicia/rekordbox-collection-manager/pull/14)  
**Branch**: `build/standalone-app-bundle`  
**Status**: DRAFT (Phase 1: Specify)

---

## Assumptions
1. **Target Environment**: macOS >= 14.0 running on Apple Silicon (`arm64`, M1–M4).
2. **Packaging Engine**: PyInstaller installed as a project development dependency via `uv`.
3. **Execution Context**: The user on target machines does not have Python, `uv`, `git`, or Xcode Command Line Tools installed.
4. **Code Signing**: Ad-hoc code signing (`codesign --force --deep -s -`). Gatekeeper quarantine attribute (`com.apple.quarantine`) on downloaded archives is cleared by the user via standard macOS dialog ("Right-click > Open") or `xattr -cr`.
5. **Obsolescence**: `scripts/launcher.c` is fully deprecated and removed.

---

## 1. Objective
Transform Rekordbox Collection Manager into an autonomous, self-contained macOS `.app` bundle and `.zip` distribution. 

* **User**: Project maintainer (Fabrizio) running the application across personal Macs without needing local repository clones or virtual environments.
* **Success Criteria**:
  1. Running `./scripts/build_app.sh` compiles a standalone `.app` in `dist/Rekordbox Collection Manager.app`.
  2. Running `./scripts/build_app.sh --package` (or `--zip`) additionally archives it to `dist/Rekordbox-Collection-Manager-v<version>-macOS-arm64.zip`.
  3. The packaged app launches cleanly when extracted to an isolated folder on a system with no virtualenv or Python environment active.
  4. All database drivers (`pyrekordbox`, SQLCipher/SQLite), audio metadata inspect tools (`mutagen`), Qt plugins (Cocoa platform, styles), and Cocoa workspace observers function without runtime import errors.

---

## 2. Executable Commands

### Build Commands
* **Run standalone build (creates .app bundle only)**:
  ```bash
  ./scripts/build_app.sh
  ```
* **Run standalone build with release zip archive**:
  ```bash
  ./scripts/build_app.sh --package
  # or: ./scripts/build_app.sh --zip
  ```
* **Direct PyInstaller compilation**:
  ```bash
  uv run pyinstaller --noconfirm scripts/rekordbox_manager.spec
  ```

### Development & Test Commands
* **Run app in development mode** (unchanged):
  ```bash
  uv run rekordbox-manager
  ```
* **Run test suite**:
  ```bash
  uv run pytest
  ```
* **Verify standalone bundle in isolated environment**:
  ```bash
  # Launch app with blanked Python environment
  env -i HOME="$HOME" USER="$USER" PATH="/usr/bin:/bin:/usr/sbin:/sbin" \
      open -W "dist/Rekordbox Collection Manager.app"
  ```

---

## 3. Project Structure & Artifacts

```
rekordbox-collection-manager/
├── pyproject.toml                     # Adds pyinstaller to [dependency-groups] dev
├── uv.lock                            # Synchronized lockfile
├── scripts/
│   ├── build_app.sh                   # Main build script: generates icon, runs PyInstaller, codesigns, zips
│   ├── rekordbox_manager.spec         # PyInstaller spec: Analysis, PYZ, EXE, BUNDLE
│   └── launcher.c                     # [DELETED] Replaced by PyInstaller bootloader
├── assets/
│   ├── icon.png                       # Source icon for application
│   └── icon.icns                      # Generated Apple Iconset
└── dist/
    ├── Rekordbox Collection Manager.app                          # Standalone macOS app bundle
    └── Rekordbox-Collection-Manager-v<version>-macOS-arm64.zip    # Distributable release archive
```

---

## 4. Module & Packaging Boundaries

### Entry Point & Bootstrapping
* **Script**: `src/rekordbox_collection_manager/gui/app.py`
* **Entry Function**: `main()`

### Hidden Imports & Dynamic Loading
PyInstaller static analysis misses dynamic imports used by our dependencies. The spec explicitly includes:
* `pyrekordbox`: `pyrekordbox.config`, `pyrekordbox.db6.database`, `pyrekordbox.utils`, `pyrekordbox.db6.models`
* `mutagen`: `mutagen.aiff`, `mutagen.mp3`, `mutagen.id3`, `mutagen.flac`
* `libsql_client`: `libsql_client.http`, `libsql_client.sqlite3`
* `pyobjc`: `AppKit`, `Foundation`, `Cocoa`
* `PySide6`: `PySide6.QtCore`, `PySide6.QtGui`, `PySide6.QtWidgets`, `PySide6.QtSvg`

### Excluded Modules (Bundle Size Optimization)
To keep the bundle size compact (under ~120MB) and prevent unused dynamic library dependencies:
* Exclude Qt Web & 3D frameworks: `PySide6.QtWebEngine`, `PySide6.QtWebEngineCore`, `PySide6.QtWebEngineWidgets`, `PySide6.QtQuick`, `PySide6.QtQml`, `PySide6.Qt3D*`, `PySide6.QtSensors`, `PySide6.QtMultimedia`, `PySide6.QtNetworkAuth`
* Exclude testing frameworks: `pytest`, `unittest`

### Runtime Resources & Assets
* Source `assets/icon.icns` bundled to `Contents/Resources/icon.icns`.
* Source `assets/icon.png` bundled to `Contents/Resources/assets/icon.png` (resolved via `get_asset_path()`).

---

## 5. User Scenarios & Acceptance Criteria

### Scenario 1: Standalone Build Execution
* **Given** a clean git working directory on branch `build/standalone-app-bundle`
* **When** the developer executes `./scripts/build_app.sh`
* **Then** `scripts/rekordbox_manager.spec` is executed via `uv run pyinstaller`
* **And** `dist/Rekordbox Collection Manager.app` is produced
* **And** intermediate staging folders are cleaned up
* **When** the developer executes `./scripts/build_app.sh --package`
* **Then** `dist/Rekordbox-Collection-Manager-v<version>-macOS-arm64.zip` is created via `ditto`
* **And** the script exits with status code `0`.

### Scenario 2: Portable Execution Outside Repository
* **Given** the built `.zip` is moved to a directory outside the repository (e.g., `/tmp/isolated_test/`) and unzipped
* **And** the current terminal has `PYTHONPATH`, `PYTHONHOME`, and `VIRTUAL_ENV` completely unset
* **When** `Rekordbox Collection Manager.app` is executed
* **Then** the native PySide6 dark GUI window renders within 3 seconds
* **And** navigating between Tabs (Tracks, Playlists, Tags, History, Analytics) operates without missing module crashes
* **And** connecting to a Rekordbox `master.db` or `exportLibrary.db` executes successfully.

### Scenario 3: Bundle Integrity & Security
* **Given** the output bundle `dist/Rekordbox Collection Manager.app`
* **When** inspecting with `codesign -vvv --deep --strict`
* **Then** the bundle passes code signature validation (`valid on disk`).
* **When** inspecting with `file` on the main Mach-O binary
* **Then** the binary is an Apple Silicon Mach-O 64-bit executable (`arm64`).

---

## 6. Verification Strategy

1. **Automated Build Test**:
   Execute `./scripts/build_app.sh` and assert zero errors.
2. **Dependency Leak Test**:
   Run `otool -L "dist/Rekordbox Collection Manager.app/Contents/MacOS/Rekordbox Collection Manager"` and inspect loaded dynamic libraries to ensure no links point to `.venv` or local user home directories.
3. **Blackbox Launch Test**:
   Execute via `open -W "dist/Rekordbox Collection Manager.app"` under `env -i` and verify clean process exit and zero crash logs in `~/Library/Logs/DiagnosticReports/`.
4. **Existing Test Suite Regression**:
   Verify `uv run pytest` continues to pass 100% of headless Qt and unit tests.
