# Implementation Tasks: Standalone macOS Desktop Application Bundling (Issue #11)

**Issue**: [#11](https://github.com/fabriziocacicia/rekordbox-collection-manager/issues/11)  
**PR**: [#14](https://github.com/fabriziocacicia/rekordbox-collection-manager/pull/14)  
**Branch**: `build/standalone-app-bundle`  
**Specification**: [standalone-macos-app-spec.md](../specs/standalone-macos-app-spec.md)  
**Plan**: [standalone-macos-app-plan.md](../plans/standalone-macos-app-plan.md)  
**Status**: DRAFT (Phase 3: Tasks)

---

## Task List & Execution Order

### Task 1: Add `pyinstaller` development dependency
* **Goal**: Add PyInstaller to dev dependency group and synchronize lockfile.
* **Files to modify**:
  * `pyproject.toml`
  * `uv.lock`
* **Command**:
  ```bash
  uv add --dev pyinstaller
  ```
* **Verification**:
  ```bash
  uv run pyinstaller --version
  ```
* **Done when**: `pyproject.toml` lists `pyinstaller` in `[dependency-groups] dev` and `uv run pyinstaller --version` outputs version `6.x.x`.

---

### Task 2: Author `scripts/rekordbox_manager.spec`
* **Goal**: Create PyInstaller specification configured for PySide6, SQLCipher, and app assets.
* **Files to create**:
  * `scripts/rekordbox_manager.spec`
* **Configuration Details**:
  * **Entrypoint**: `src/rekordbox_collection_manager/gui/app.py`
  * **Datas**: `[('assets/icon.png', 'assets'), ('assets/icon.icns', 'assets')]`
  * **Hidden Imports**:
    * `sqlcipher3`, `sqlcipher3.dbapi2`
    * `sqlalchemy.dialects.sqlite`, `sqlalchemy.dialects.sqlite.pysqlcipher`
    * `pyrekordbox.config`, `pyrekordbox.db6.database`, `pyrekordbox.db6.models`, `pyrekordbox.utils`
    * `mutagen`, `mutagen.aiff`, `mutagen.mp3`, `mutagen.id3`, `mutagen.flac`
    * `libsql_client`, `libsql_client.http`, `libsql_client.sqlite3`
  * **Excludes**:
    * `pytest`, `unittest`
    * `PySide6.QtWebEngine`, `PySide6.QtWebEngineCore`, `PySide6.QtWebEngineWidgets`
    * `PySide6.QtQuick`, `PySide6.QtQml`, `PySide6.Qt3D*`, `PySide6.QtSensors`
  * **Bundle Metadata**:
    * `CFBundleDisplayName`: `Rekordbox Collection Manager`
    * `CFBundleIdentifier`: `com.fabriziocacicia.rekordbox-collection-manager`
    * `CFBundleIconFile`: `icon.icns`
    * `NSHighResolutionCapable`: `True`
* **Verification**:
  ```bash
  uv run pyinstaller --noconfirm scripts/rekordbox_manager.spec
  ```
* **Done when**: PyInstaller completes and outputs `dist/Rekordbox Collection Manager.app`.

---

### Task 3: Rewrite `scripts/build_app.sh` and remove `scripts/launcher.c`
* **Goal**: Streamline `build_app.sh` to run PyInstaller, ad-hoc codesign, package `.zip` via `ditto`, and remove obsolete C launcher.
* **Files to modify/delete**:
  * Modify `scripts/build_app.sh`
  * Delete `scripts/launcher.c`
* **Script Pipeline**:
  1. Generate `icon.icns` if missing.
  2. Extract `APP_VERSION` from `pyproject.toml`.
  3. Execute `uv run pyinstaller --noconfirm scripts/rekordbox_manager.spec`.
  4. Ad-hoc codesign: `codesign --force --deep -s - "dist/Rekordbox Collection Manager.app"`.
  5. Package archive:
     ```bash
     ditto -c -k --sequesterRsrc --keepParent \
         "dist/Rekordbox Collection Manager.app" \
         "dist/Rekordbox-Collection-Manager-v${APP_VERSION}-macOS-arm64.zip"
     ```
  6. Display output artifact paths and sizes.
* **Verification**:
  ```bash
  ./scripts/build_app.sh
  ```
* **Done when**: Script executes cleanly without errors, produces both `.app` and `.zip`, and `scripts/launcher.c` is removed from git.

---

### Task 4: Linkage isolation & runtime verification
* **Goal**: Verify that the generated bundle is truly standalone with zero hardcoded references to the local virtual environment.
* **Verification Steps**:
  1. **Dependency Leak Scan**:
     ```bash
     otool -L "dist/Rekordbox Collection Manager.app/Contents/MacOS/Rekordbox Collection Manager"
     ```
     Ensure all paths reference `@rpath`, `@executable_path`, or `/System/Library/` (zero references to `.venv` or local user home directories).
  2. **Isolated Directory Extraction**:
     ```bash
     mkdir -p /tmp/rb_isolated_test
     unzip -q "dist/Rekordbox-Collection-Manager-v0.20.0-macOS-arm64.zip" -d /tmp/rb_isolated_test/
     ```
  3. **Sanitized Environment Launch**:
     ```bash
     env -i HOME="$HOME" USER="$USER" PATH="/usr/bin:/bin:/usr/sbin:/sbin" \
         open -W "/tmp/rb_isolated_test/Rekordbox Collection Manager.app"
     ```
  4. **Regression Test Suite**:
     ```bash
     uv run pytest
     ```
* **Done when**: The app launches and functions cleanly in the isolated directory without python environment variables, and `uv run pytest` passes 100%.

---

### Task 5: PR readiness & Review transition
* **Goal**: Update PR #14 description with verification evidence, mark PR as ready for review, and update Issue #11.
* **Files**:
  * PR [#14](https://github.com/fabriziocacicia/rekordbox-collection-manager/pull/14)
* **Command**:
  ```bash
  gh pr ready 14
  ```
* **Done when**: All checklist items in PR #14 are checked, automated checks pass, and PR is marked Ready for Review.
