# Technical Plan: Standalone macOS Desktop Application Bundling (Issue #11)

**Issue**: [#11](https://github.com/fabriziocacicia/rekordbox-collection-manager/issues/11)  
**PR**: [#14](https://github.com/fabriziocacicia/rekordbox-collection-manager/pull/14)  
**Branch**: `build/standalone-app-bundle`  
**Specification**: [standalone-macos-app-spec.md](../specs/standalone-macos-app-spec.md)  
**Status**: DRAFT (Phase 2: Plan)

---

## 1. Tech Stack & Packaging Architecture

### Core Tooling
* **`pyinstaller` (>= 6.11.0)**: Added to `pyproject.toml` under `[dependency-groups] dev`.
* **Execution Environment**: Invoked via `uv run pyinstaller scripts/rekordbox_manager.spec`. This guarantees that PyInstaller runs using the exact Python 3.12 Apple Silicon virtual environment and resolves all pinned wheels from `uv.lock`.
* **Packaging Strategy**: Standard macOS `.app` bundle via PyInstaller `BUNDLE` + `COLLECT` (one-dir structure inside the bundle). 
  * *Why not `--onefile`*: `--onefile` uncompresses libraries to `/var/folders/` or `/tmp` on every cold boot, adding 2–4 seconds of latency. A native macOS `.app` is already a directory structure, so bundling dependencies directly into `Contents/MacOS` and `Contents/Frameworks` yields instant sub-second cold starts.
* **Archive Utility**: macOS native `ditto -c -k --sequesterRsrc --keepParent` to preserve Mach-O signatures, extended attributes, and directory hierarchy.

---

## 2. Technical Architecture & Component Layout

```
Rekordbox Collection Manager.app/
└── Contents/
    ├── Info.plist                     # Generated application metadata (version, bundle id, high-dpi)
    ├── MacOS/
    │   ├── Rekordbox Collection Manager   # Mach-O 64-bit arm64 PyInstaller bootloader
    │   ├── PySide6/                   # Qt runtime, dynamic libraries, and plugins
    │   │   └── Qt/plugins/
    │   │       ├── platforms/libqcocoa.dylib
    │   │       ├── imageformats/
    │   │       └── iconengines/
    │   ├── _decimal.cpython-312-darwin.so
    │   ├── sqlcipher3/                # SQLCipher native bindings & drivers
    │   └── ...                        # Bundled shared libraries (.dylib / .so)
    ├── Frameworks/                    # Python.framework and Qt Frameworks (QtCore, QtGui, QtWidgets, QtSvg)
    └── Resources/
        ├── icon.icns                  # Native macOS dock & finder icon
        └── assets/
            └── icon.png               # Resolved by gui.assets.get_asset_path() via sys._MEIPASS
```

---

## 3. Edge Cases & Failure Modes

### Edge Case 1: Dynamic Database Drivers (`sqlcipher3` & `SQLAlchemy`)
* **Risk**: In `src/rekordbox_collection_manager/utils.py`, `open_database()` dynamically imports `from sqlcipher3 import dbapi2 as sqlcipher_driver` and invokes `sqlite+pysqlcipher://`. PyInstaller static analysis will not discover these runtime imports.
* **Mitigation**:
  * Explicitly register in `scripts/rekordbox_manager.spec`:
    ```python
    hiddenimports = [
        'sqlcipher3',
        'sqlcipher3.dbapi2',
        'sqlalchemy.dialects.sqlite',
        'sqlalchemy.dialects.sqlite.pysqlcipher',
        'pyrekordbox.config',
        'pyrekordbox.db6.database',
        'pyrekordbox.db6.models',
        'pyrekordbox.utils',
    ]
    ```

### Edge Case 2: Asset Path Resolution in Packaged Layout
* **Risk**: When running in a frozen app, `__file__` points inside `Contents/MacOS`, which breaks relative paths to root `assets/`.
* **Mitigation**:
  * [src/rekordbox_collection_manager/gui/assets.py](../../src/rekordbox_collection_manager/gui/assets.py#L17-L20) already contains PyInstaller support via `sys._MEIPASS`:
    ```python
    if hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "assets" / filename)
        candidates.append(Path(sys._MEIPASS) / filename)
    ```
  * In the spec file, we map `('assets/icon.png', 'assets')` and `('assets/icon.icns', 'assets')` in `datas`.

### Edge Case 3: PySide6 Cocoa Platform Plugin Loading
* **Risk**: On clean Macs, PySide6 may fail to locate `libqcocoa.dylib`, producing the classic Qt error: *"This application failed to start because no Qt platform plugin could be initialized."*
* **Mitigation**:
  * PyInstaller 6.x includes built-in hooks for PySide6 (`PySide6.QtCore`, `PySide6.QtWidgets`). We ensure `PySide6` hooks are active and explicitly verify that `PySide6/Qt/plugins/platforms/libqcocoa.dylib` is present in the output bundle.

### Edge Case 4: Ad-hoc Signing & Gatekeeper Quarantine
* **Risk**: Any `.zip` downloaded via Safari/Chrome receives `com.apple.quarantine`. When unzipped, macOS Gatekeeper blocks ad-hoc signed binaries with *"App is damaged and cannot be opened"*.
* **Mitigation**:
  * `scripts/build_app.sh` executes deep ad-hoc signing: `codesign --force --deep -s - "$APP_DIR"`.
  * For personal multi-Mac distribution, provide a 1-line de-quarantine instruction in release notes: `xattr -cr "/Applications/Rekordbox Collection Manager.app"`.

---

## 4. Test & Verification Strategy

### 1. Build Verification
* Run `./scripts/build_app.sh`.
* Check exit code equals 0.
* Verify `dist/Rekordbox Collection Manager.app` exists.
* Verify `dist/Rekordbox-Collection-Manager-v<version>-macOS-arm64.zip` exists and is between 80MB and 200MB.

### 2. Dependency Leak Inspection
* Run `otool -L "dist/Rekordbox Collection Manager.app/Contents/MacOS/Rekordbox Collection Manager"`.
* Inspect dynamic loader paths: All libraries must resolve via `@rpath`, `@executable_path`, or system frameworks (`/usr/lib`, `/System/Library/Frameworks`).
* Assert **zero** references to `.venv` or `/Users/fabriziocacicia/`.

### 3. Isolated Runtime Execution
* Execute the bundle under a completely scrubbed shell:
  ```bash
  env -i HOME="$HOME" USER="$USER" PATH="/usr/bin:/bin:/usr/sbin:/sbin" \
      open -W "dist/Rekordbox Collection Manager.app"
  ```
* Verify:
  - App window renders with custom dark theme.
  - Navigation between views works without errors.
  - Audio files can be inspected via `mutagen`.
  - Closing app produces 0 crash dumps.

### 4. Unit Test Suite Regression
* Run `uv run pytest` to ensure no changes broke headless tests.

---

## 5. Security & Performance Considerations

* **Size Optimization**: By excluding unneeded Qt modules (`QtWebEngine`, `QtQuick`, `Qt3D`), the bundle stays under ~120MB (compressed zip ~45MB).
* **Launch Latency**: Directory bundles execute immediately upon double-click without temporary file decompression overhead.
* **Apple Silicon Native**: 100% native arm64 Mach-O binaries; zero Rosetta 2 translation overhead.

---

## 6. Rollout & Rollback Plan

* **Rollout**:
  1. Add `pyinstaller` to dev dependencies via `uv add --dev pyinstaller`.
  2. Commit `scripts/rekordbox_manager.spec`.
  3. Update `scripts/build_app.sh` to run PyInstaller and `ditto`.
  4. Remove `scripts/launcher.c`.
  5. Test build and verify in isolated directory.
* **Rollback**:
  All changes live in feature branch `build/standalone-app-bundle`. Reverting is as simple as checking out `main`.
