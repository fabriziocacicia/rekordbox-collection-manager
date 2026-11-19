# Rekordbox Collection Manager - Agent Guidelines & Context

## Project Overview
A native macOS desktop application (PySide6 / Qt6), background sync daemon (`launchd` / Cocoa `NSWorkspace`), offline-first iOS companion app (SwiftUI), and CLI suite for managing, inspecting, curating, and repairing Pioneer / AlphaTheta Rekordbox databases (`master.db` SQLCipher and `exportLibrary.db`) with Turso (`libSQL`) cloud synchronization.

## Tech Stack
- **Desktop / Core**: Python >= 3.11, `uv`, PySide6 (Qt6), `pyrekordbox` (SQLCipher/SQLite), `mutagen` (audio metadata), `libsql-client` (Turso HTTP pipeline), `pyobjc-framework-cocoa` (macOS workspace observer), `questionary` (CLI prompts).
- **Testing**: `pytest` (Headless Qt configured via `QT_QPA_PLATFORM=offscreen` in `tests/conftest.py`).
- **Mobile Companion**: Swift 5.0, SwiftUI, SQLite, iOS >= 17.0 (`ios/RekordboxCompanion/`).
- **Package & Build Management**: `uv_build`, `pyproject.toml`, `uv.lock`, PyInstaller standalone packaging (`scripts/rekordbox_manager.spec`, `scripts/build_app.sh`).

---

## Essential Commands

### Development & Testing
- Run test suite: `uv run pytest`
- Run single test file: `uv run pytest tests/test_replace_file.py`
- Run test matching keyword: `uv run pytest -k "test_play_count"`

### Desktop Application & CLIs
- Launch Desktop GUI: `uv run rekordbox-manager` (or `uv run rekordbox-gui`)
- Rename / curate history sessions: `uv run rename-history`
- Modify track play counts: `uv run edit-play-count`
- Lossless audio replacement: `uv run replace-file`
- Background daemon: `uv run rekordbox-daemon status` (or `run`, `sync`, `install`, `uninstall`)

### Operational & Release Scripts
- Build standalone `.app` bundle: `./scripts/build_app.sh` (or `./scripts/build_app.sh --package` to include `.zip`)
- Desktop version bump & changelog rotate: `uv run python scripts/desktop_version.py [patch|minor|major] [--commit] [--tag]`
- Changelog backfill & check: `uv run python scripts/changelog.py [--backfill|--unreleased|--check]`
- iOS version bump: `uv run python scripts/ios_version.py [patch|minor|major] [--commit] [--tag]`
- Manual backup: `./scripts/backup_rekordbox_mac.sh`
- Manual restore: `./scripts/restore_rekordbox_mac.sh [-y]`

---

## Critical Invariants & System Boundaries

1. **Rekordbox Process Mutual Exclusion**:
   - Never mutate local databases (`master.db`, `exportLibrary.db`) or run restore/sync while Pioneer Rekordbox is running.
   - Always call `is_rekordbox_running()` / `ensure_rekordbox_not_running()`.
   - Never bypass the process check unless explicit `--force` or `force=True` is passed by user intent.
   - Ignore current PID and processes matching `rekordbox-collection` in process scans.

2. **Database Engine & Connection Safety**:
   - Local DB connections must use `NullPool` with `timeout=30.0` (configured in `utils.py:open_database`).
   - Turso cloud sync: local SQLite connections must be closed prior to executing HTTP `/v2/pipeline` batch requests (100–300 statements). Remote `changelog_events` updates occur only after releasing local SQLite write locks.
   - Always resolve signed/unsigned 32-bit `MyTag` and smart playlist IDs using `utils.py:resolve_tag_matching_ids`.

3. **Destructive Operations & Safeguards**:
   - Always create a pre-restore safety snapshot before touching live Rekordbox directories (`backup_restore.py:perform_restore`, `scripts/restore_rekordbox_mac.sh`).
   - File replacement requires an existing uncompressed AIFF target file. If a database transaction fails during replacement, moved/renamed files must roll back immediately.
   - File trashing on macOS must use Finder Trash via AppleScript (`osascript`), with fallback to `.bak` rename.

4. **Data Consistency & Clamping**:
   - Play count mutations must clamp `DJPlayCount >= 0`.
   - Streaming tracks (`STREAMING_FILE_TYPES`, `STREAMING_URI_PREFIXES`) are excluded from analytics, search, and cloud sync.
   - In PySide6 GUI, changes must prompt `confirm_unsaved_changes()` before navigation or destructive actions.

---

## Codebase Map & Module Boundaries

- `src/rekordbox_collection_manager/`:
  - `utils.py`: Process check, DB path discovery, SQLCipher `open_database`, string/date formatters, audio file inspection.
  - `replace_file.py`: Lossless audio replacement, track merge, metadata transfer, Finder trash.
  - `edit_play_count.py`: Track search, play count adjustment CLI, playlist/tag membership queries.
  - `rename_history.py`: History session renumbering, chronological folder realignment (`YYYY/MM`), soft deletion.
  - `history_import.py`: External DB history session import, diffing, and merging.
  - `playlists.py`: Playlist tree CRUD, smart playlist query parsing.
  - `tags.py`: MyTag tree CRUD, tag category normalization.
  - `analytics.py`: Gig session analytics, Camelot key distribution, turnover, survival curve, XML/M3U8 exports.
  - `backup_restore.py`: Snapshot creation, directory sync via `rsync`, restore verification.
  - `turso_sync.py`: HTTP client for Turso `/v2/pipeline`, changelog event synchronization.
  - `daemon.py`: Cocoa `NSWorkspace` app observer + periodic mobile changelog poll.
- `src/rekordbox_collection_manager/gui/`:
  - `app.py`: Desktop entry point, QApplication setup, custom dark theme.
  - `main_window.py`: Window frame, navigation sidebar, Rekordbox safety polling timer (`safety_timer`).
  - `workers.py`: `QThread` async workers for long-running DB and sync tasks.
  - `*_view.py`: Views for Tracks, Playlists, Tags, History, Analytics, PlayCount, ReplaceFile.
  - `analytics_widgets/`: Custom PySide6 QPainter chart widgets (Camelot wheel, turnover graph, etc.).
- `ios/RekordboxCompanion/`:
  - SwiftUI companion application target with local SQLite offline cache and Turso sync.
- `scripts/`:
  - Deployment, packaging, and version synchronization utilities.
- `tests/`:
  - Unit and integration tests (isolated test databases and mock fixtures).

---

## Working on Specific Subsystems (Selective Context)

- **When editing Desktop GUI**: Focus on `src/rekordbox_collection_manager/gui/*`, `workers.py`, and `tests/test_gui_models.py` / `test_history_gui.py`. Remember Qt headless flag in tests.
- **When editing Audio Replacement / Metadata**: Focus on `src/rekordbox_collection_manager/replace_file.py`, `utils.py`, and `tests/test_replace_file.py`. Keep rollback guarantees intact.
- **When editing Cloud Sync / Daemon**: Focus on `turso_sync.py`, `daemon.py`, and `tests/test_turso_sync.py`. Pay attention to connection pooling and HTTP batching.
- **When editing Analytics**: Focus on `analytics.py`, `gui/analytics_widgets/`, and `tests/test_analytics.py`. Keep 24 Camelot slices seeded.
