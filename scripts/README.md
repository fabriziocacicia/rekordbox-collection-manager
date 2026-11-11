# Scripts

## Overview
Operational and release-engineering utilities for `rekordbox-collection-manager`, covering macOS Rekordbox database backup and restore (`backup_rekordbox_mac.sh`, `restore_rekordbox_mac.sh`), native macOS `.app` bundle compilation and launch (`build_app.sh`, `launcher.c`), and semantic version management for both the desktop Python package and the iOS companion Xcode project (`desktop_version.py`, `ios_version.py`).

## Interface

### `backup_rekordbox_mac.sh`
- `[backup_rekordbox_mac.sh:TIMESTAMP]`: Backup timestamp formatted via `date +"%Y%m%d_%H%M%S"`.
- `[backup_rekordbox_mac.sh:BACKUP_PARENT_DIR]`: Destination root (`${HOME}/Music/rekordbox/Backups`).
- `[backup_rekordbox_mac.sh:BACKUP_DIR]`: Timestamped backup directory (`${BACKUP_PARENT_DIR}/rekordbox_mac_backup_${TIMESTAMP}`).
- `[backup_rekordbox_mac.sh:DIR_PIONEER]`: Source Rekordbox database directory (`${HOME}/Library/Pioneer/rekordbox`).
- `[backup_rekordbox_mac.sh:DIR_APPSUPPORT]`: Source Application Support directory (`${HOME}/Library/Application Support/Pioneer/rekordbox`).

### `restore_rekordbox_mac.sh`
- `[restore_rekordbox_mac.sh:show_help]`: Bash function printing CLI usage, options, and examples to stdout.
- `[restore_rekordbox_mac.sh:-h]` / `[restore_rekordbox_mac.sh:--help]`: Prints help output via `show_help` and exits `0`.
- `[restore_rekordbox_mac.sh:-y]` / `[restore_rekordbox_mac.sh:--yes]`: Sets `[restore_rekordbox_mac.sh:AUTO_YES]` to `true` to bypass interactive selection and confirmation prompts.
- `[restore_rekordbox_mac.sh:CUSTOM_BACKUP_PATH]`: Optional positional CLI argument specifying an explicit backup directory path.
- `[restore_rekordbox_mac.sh:SKIP_PROCESS_CHECK]`: Optional environment variable (default `0`); setting `"1"` bypasses the `pgrep` running-process check.
- `[restore_rekordbox_mac.sh:HOME]` / `[restore_rekordbox_mac.sh:BACKUP_ROOT]`: Uses `$HOME` to derive `${HOME}/Music/rekordbox/Backups` and expand leading `~` in `[restore_rekordbox_mac.sh:CUSTOM_BACKUP_PATH]`.
- `[restore_rekordbox_mac.sh:DIR_PIONEER]` / `[restore_rekordbox_mac.sh:DIR_APPSUPPORT]`: Target live Rekordbox directories under `${HOME}/Library/...`.
- `[restore_rekordbox_mac.sh:SELECTED_BACKUP]`, `[restore_rekordbox_mac.sh:PIONEER_SRC]`, `[restore_rekordbox_mac.sh:APP_SUPPORT_SRC]`, `[restore_rekordbox_mac.sh:SAFETY_BACKUP_DIR]`: Resolved backup source directories and pre-restore safety snapshot path.

### `build_app.sh`
- `[build_app.sh:SCRIPT_DIR]` / `[build_app.sh:PROJECT_ROOT]`: Directory containing `build_app.sh` and its parent repository root.
- `[build_app.sh:ICONSET]`: Temporary iconset directory (`$PROJECT_ROOT/assets/icon.iconset`).
- `[build_app.sh:APP_DIR]`: Output macOS application bundle (`$PROJECT_ROOT/dist/Rekordbox Collection Manager.app`).
- `[build_app.sh:APP_VERSION]`: Version parsed from the first `^version = ` line in `$PROJECT_ROOT/pyproject.toml` (defaults to `'0.2.0'` if empty).
- `[build_app.sh:PYTHON_BIN]`: Python interpreter (`$PROJECT_ROOT/.venv/bin/python` if executable, else `$(which python3)`).
- `[build_app.sh:PYTHON_HOME]`, `[build_app.sh:PYTHON_LIBDIR]`, `[build_app.sh:PYTHON_LDLIBRARY]`, `[build_app.sh:PYTHON_VERSION_STR]`, `[build_app.sh:DYLIB_PATH]`: Python installation metadata queried from `$PYTHON_BIN` and passed to `launcher.c` compilation / bundle Frameworks.

### `launcher.c`
- `[launcher.c:DEFAULT_PROJECT_ROOT]`, `[launcher.c:DEFAULT_PYTHON_HOME]`, `[launcher.c:DEFAULT_DYLIB_PATH]`, `[launcher.c:PYTHON_VERSION_STR]` (`"python3.12"`), `[launcher.c:APP_MODULE]` (`"rekordbox_collection_manager.gui.app"`), `[launcher.c:APP_NAME]` (`"Rekordbox Collection Manager"`): Overridable compile-time preprocessor macros.
- `[launcher.c:Py_BytesMain_t]`: Function pointer typedef `int (*Py_BytesMain_t)(int argc, char **argv)`.
- `[launcher.c:path_exists]`, `[launcher.c:get_parent_dir]`: Internal `static` filesystem helpers (`stat` existence check and `dirname` wrapper).
- `[launcher.c:main]`: Mach-O bundle entry point forwarding non-`-psn_` arguments to dynamically resolved `[launcher.c:Py_BytesMain]`.
- `[launcher.c:PYTHONHOME]`, `[launcher.c:VIRTUAL_ENV]`, `[launcher.c:PYTHONPATH]`, `[launcher.c:PATH]`, `[launcher.c:OBJC_DISABLE_INITIALIZE_FORK_SAFETY]`: Environment variables read/configured prior to launching Python.

### `desktop_version.py`
- `[desktop_version.py:SCRIPT_DIR]`, `[desktop_version.py:PROJECT_ROOT]`, `[desktop_version.py:PYPROJECT_PATH]` (`pyproject.toml`), `[desktop_version.py:UV_LOCK_PATH]` (`uv.lock`): Resolved manifest and lockfile paths.
- `[desktop_version.py:get_current_version]`, `[desktop_version.py:validate_semver]`, `[desktop_version.py:bump_semver]`: SemVer parsing, validation, and incrementing helpers.
- `[desktop_version.py:update_pyproject]`, `[desktop_version.py:update_uv_lock]`, `[desktop_version.py:git_commit_and_tag]`: Manifest updater, `uv lock` runner, and git commit/tag helper.
- `[desktop_version.py:main]`: CLI entry point accepting positional `[desktop_version.py:action]` (`current`, `show`, `patch`, `minor`, `major`, `set`; default `current`), positional `[desktop_version.py:version]`, `[desktop_version.py:--commit]`, `[desktop_version.py:--tag]`, `[desktop_version.py:--no-uv-lock]`, and `[desktop_version.py:--dry-run]`.

### `ios_version.py`
- `[ios_version.py:SCRIPT_DIR]`, `[ios_version.py:PROJECT_ROOT]`, `[ios_version.py:PBXPROJ_PATH]` (`ios/RekordboxCompanion/RekordboxCompanion.xcodeproj/project.pbxproj`): Resolved Xcode project manifest path.
- `[ios_version.py:get_current_versions]`, `[ios_version.py:validate_semver]`, `[ios_version.py:bump_semver]`: Xcode `MARKETING_VERSION` / `CURRENT_PROJECT_VERSION` parsing, SemVer validation, and incrementing helpers.
- `[ios_version.py:update_pbxproj]`, `[ios_version.py:git_commit_and_tag]`: Xcode project updater and git commit/tag helper.
- `[ios_version.py:main]`: CLI entry point accepting positional `[ios_version.py:action]` (`current`, `show`, `patch`, `minor`, `major`, `build`, `set`; default `current`), positional `[ios_version.py:version]`, `[ios_version.py:--build]`, `[ios_version.py:--reset-build]`, `[ios_version.py:--commit]`, `[ios_version.py:--tag]`, and `[ios_version.py:--dry-run]`.

### Subdirectories
- None (leaf component package).

## Invariants
- `[backup_rekordbox_mac.sh:set]`, `[restore_rekordbox_mac.sh:set]`, `[build_app.sh:set]`: All shell scripts enforce `set -euo pipefail`.
- `[backup_rekordbox_mac.sh:pgrep]`: Checks `pgrep -il rekordbox` and exits with status `1` if any matching process is running.
- `[backup_rekordbox_mac.sh:DIR_PIONEER]`, `[backup_rekordbox_mac.sh:DIR_APPSUPPORT]`: Checks `[ -d ... ]` before copying (`DIR_PIONEER` missing prints an error without exiting early; `DIR_APPSUPPORT` is copied only if present).
- `[restore_rekordbox_mac.sh:SKIP_PROCESS_CHECK]`: Unless `SKIP_PROCESS_CHECK="1"`, executes `pgrep -il "rekordbox"` (excluding `$$`) and exits `1` if any Rekordbox or Rekordbox Collection Manager process is active.
- `[restore_rekordbox_mac.sh:CUSTOM_BACKUP_PATH]`, `[restore_rekordbox_mac.sh:BACKUP_ROOT]`: Accepts at most one positional backup path (exiting `1` on extra unknown args); expands leading `~` via `$HOME`, resolves parent directory, and exits `1` if missing; otherwise scans `BACKUP_ROOT` (`-maxdepth 1 -type d`) for `rekordbox_mac_backup_*` or `rekordbox_backup_*` reverse-sorted newest-first and exits `1` if none are found.
- `[restore_rekordbox_mac.sh:AUTO_YES]`, `[restore_rekordbox_mac.sh:-y]`: When multiple backups exist, `AUTO_YES=true` selects `BACKUP_CANDIDATES[0]` and otherwise validates `[1-N]` input; interactive runs require confirmation matching `s`/`si`/`y`/`yes` (case-insensitive) and exit `0` cleanly on cancellation.
- `[restore_rekordbox_mac.sh:PIONEER_SRC]`, `[restore_rekordbox_mac.sh:APP_SUPPORT_SRC]`: Requires `master.db` in `<SELECTED_BACKUP>/Pioneer_Library/master.db`, `<SELECTED_BACKUP>/master.db`, or `<SELECTED_BACKUP>/master.db` when basename is `Pioneer_Library` (otherwise exits `1`); includes `Application_Support` only if that directory exists in `<SELECTED_BACKUP>`.
- `[restore_rekordbox_mac.sh:SAFETY_BACKUP_DIR]`, `[restore_rekordbox_mac.sh:DIR_PIONEER]`, `[restore_rekordbox_mac.sh:DIR_APPSUPPORT]`: Archives live directories into `SAFETY_BACKUP_DIR` before modifying them, protects fallback `rm -rf` paths with `:?` non-empty guards (`"${DIR_PIONEER:?}"/*`, `"${DIR_APPSUPPORT:?}"/*`), and verifies `[ -f "${DIR_PIONEER}/master.db" ]` post-restore (exiting `1` otherwise).
- `[build_app.sh:APP_VERSION]`, `[build_app.sh:PYTHON_BIN]`, `[build_app.sh:DYLIB_PATH]`, `[build_app.sh:Info.plist]`, `[build_app.sh:codesign]`: Falls back `APP_VERSION` to `'0.2.0'` if `pyproject.toml` yields empty; prefers executable `$PROJECT_ROOT/.venv/bin/python` over `which python3`; bundles `DYLIB_PATH` only when `PYTHON_LIBDIR`/`PYTHON_LDLIBRARY` point to an existing regular file; emits `CFBundleIdentifier` `com.fabriziocacicia.rekordbox-collection-manager` in `Info.plist`; suppresses `codesign` failure via `|| true`.
- `[launcher.c:main]`: Requires `_NSGetExecutablePath` to succeed (returns `1` on failure); walks up 5 parent directories from executable path and uses `candidate_project_root` if `<candidate>/.venv` exists, otherwise falls back to `DEFAULT_PROJECT_ROOT`.
- `[launcher.c:main]`: Resolves `python_home` from `DEFAULT_PYTHON_HOME` or `<project_root>/.venv/pyvenv.cfg` (`home = ...`), sets `PYTHONHOME` only if non-empty, preserves pre-existing `OBJC_DISABLE_INITIALIZE_FORK_SAFETY` (`overwrite=0`), and defaults `PATH` to `"/usr/bin:/bin"` if unset before prepending `<project_root>/.venv/bin`.
- `[launcher.c:main]`: Searches `lib<PYTHON_VERSION_STR>.dylib` in order `<contents_dir>/Frameworks/`, `DEFAULT_DYLIB_PATH`, `<python_home>/lib/`; exits `1` if missing or if `dlopen`/`dlsym("Py_BytesMain")` returns `NULL`; strips `-psn_*` LaunchServices arguments before calling `Py_BytesMain`.
- `[desktop_version.py:validate_semver]`, `[desktop_version.py:bump_semver]`, `[ios_version.py:validate_semver]`, `[ios_version.py:bump_semver]`: Enforces strict 3-part integer SemVer `^(\d+)\.(\d+)\.(\d+)$` and accepts only `major`, `minor`, or `patch` bump types.
- `[desktop_version.py:get_current_version]`, `[desktop_version.py:update_pyproject]`, `[desktop_version.py:update_uv_lock]`: Requires `^version\s*=\s*["']([^"']+)["']` in `pyproject.toml`; replaces at most `count=1` match via `.tmp` atomic replace; skips writes, `uv lock`, and git operations on `current`, `show`, or `--dry-run`; `update_uv_lock` returns `False` when `--dry-run` is set or `uv` is missing.
- `[ios_version.py:get_current_versions]`, `[ios_version.py:update_pbxproj]`, `[ios_version.py:--build]`, `[ios_version.py:--reset-build]`: Requires both `MARKETING_VERSION` and integer `CURRENT_PROJECT_VERSION` in `project.pbxproj`; updates all occurrences via `.tmp` atomic replace; explicit `--build` overrides automatic rules, while `--reset-build` resets to `1` only for `minor`/`major`/`set` (`patch` and `build` still increment by `1`).
- `[desktop_version.py:--tag]`, `[ios_version.py:--tag]`, `[desktop_version.py:git_commit_and_tag]`, `[ios_version.py:git_commit_and_tag]`, `[desktop_version.py:main]`, `[ios_version.py:main]`: Passing `--tag` also triggers `git_commit_and_tag` (`if args.commit or args.tag`); missing manifests, missing `set` version, `ValueError`, `CalledProcessError`, or generic `Exception` exit `1` (`130` on `KeyboardInterrupt`).

## Side Effects
- `[backup_rekordbox_mac.sh:pgrep]`, `[backup_rekordbox_mac.sh:BACKUP_DIR]`, `[backup_rekordbox_mac.sh:DIR_PIONEER]`, `[backup_rekordbox_mac.sh:DIR_APPSUPPORT]`: Executes `pgrep -il rekordbox`; creates `${BACKUP_DIR}/Pioneer_Library` (and `Application_Support` when present) via `mkdir -p`; copies Rekordbox trees via `cp -R`; prints `ls -lh` summary to stdout.
- `[restore_rekordbox_mac.sh:ACTIVE_PROCS]`, `[restore_rekordbox_mac.sh:BACKUP_ROOT]`, `[restore_rekordbox_mac.sh:SAFETY_BACKUP_DIR]`, `[restore_rekordbox_mac.sh:DIR_PIONEER]`, `[restore_rekordbox_mac.sh:DIR_APPSUPPORT]`: Runs `pgrep -il "rekordbox"`; reads interactive stdin prompts unless `--yes`; creates `BACKUP_ROOT` and `SAFETY_BACKUP_DIR` snapshots (`mkdir -p`, `cp -R`); destructively replaces `DIR_PIONEER` and optional `DIR_APPSUPPORT` via `rsync -aI --delete` (or `rm -rf` + `cp -R` fallback); removes stale `master.db-wal` and `master.db-shm` via `rm -f` when absent from `PIONEER_SRC`.
- `[build_app.sh:ICONSET]`, `[build_app.sh:APP_DIR]`, `[build_app.sh:Info.plist]`, `[build_app.sh:PYTHON_BIN]`, `[build_app.sh:DYLIB_PATH]`, `[build_app.sh:clang]`, `[build_app.sh:codesign]`: Converts `assets/icon.png` into `assets/icon.icns` via `sips` and `iconutil` (creating and deleting `assets/icon.iconset`); builds `.app` bundle directories under `dist/`; invokes `$PYTHON_BIN -c`; copies `icon.icns` and Python dylib into bundle; writes `Contents/Info.plist`; compiles `scripts/launcher.c` with `clang -O2` and `chmod +x`; ad-hoc signs bundle via `codesign`.
- `[launcher.c:main]`: Calls `chdir(project_root)`; reads `<project_root>/.venv/pyvenv.cfg`; mutates `PYTHONHOME`, `VIRTUAL_ENV`, `PYTHONPATH`, `PATH`, and `OBJC_DISABLE_INITIALIZE_FORK_SAFETY` via `setenv`; loads `lib<PYTHON_VERSION_STR>.dylib` via `dlopen(..., RTLD_NOW | RTLD_GLOBAL)` and resolves `Py_BytesMain` via `dlsym`; allocates `new_argv` (`malloc`, unfreed) and runs `-m rekordbox_collection_manager.gui.app`; writes errors to `stderr`.
- `[desktop_version.py:PYPROJECT_PATH]`, `[desktop_version.py:update_uv_lock]`, `[desktop_version.py:git_commit_and_tag]`: Reads and atomically rewrites `pyproject.toml` via `.tmp`; executes `uv lock` subprocess (mutating `uv.lock`); runs `git add`, `git commit`, and `git tag -a v{new_version}` in `PROJECT_ROOT`.
- `[ios_version.py:PBXPROJ_PATH]`, `[ios_version.py:git_commit_and_tag]`: Reads and atomically rewrites `ios/RekordboxCompanion/RekordboxCompanion.xcodeproj/project.pbxproj` via `.tmp`; runs `git add`, `git commit`, and `git tag -a ios-v{new_marketing}` in `PROJECT_ROOT`.

## Verification
- Run: `uv run pytest`
- Relevant script test modules: `uv run pytest tests/test_backup_restore.py tests/test_restore_script.py tests/test_version.py`
