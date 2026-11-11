# Rekordbox Collection Manager

A high-performance macOS desktop application, event-driven background sync daemon, native iOS companion app, and collection of standalone utilities for managing, inspecting, and fixing Pioneer / AlphaTheta Rekordbox databases (`master.db` and DeviceSQL `exportLibrary.db`).

---

## System Overview

Rekordbox Collection Manager bridges local Pioneer/AlphaTheta SQLite/SQLCipher databases (`master.db` and `exportLibrary.db`) on macOS with an offline-first SwiftUI iOS companion app via an event-sourced Turso (`libSQL`) cloud pipeline, alongside PySide6 desktop tooling, collection analytics, lossless audio replacement, backup/restore automation, and release scripts.

```mermaid
flowchart TD
    subgraph Mobile ["📱 iOS Device (Offline-First)"]
        iOS["Rekordbox Companion App (SwiftUI)"]
        LocalCache[("Local SQLite Cache\n(rekordbox_companion.db)")]
        iOS <--> LocalCache
    end

    subgraph Cloud ["☁️ Turso Cloud Database (libSQL)"]
        TursoDB[("Turso Cloud DB\n(Tracks, Playlists, Tags, History)")]
        ChangelogQueue[("changelog_events Queue\n(PENDING / PROCESSED / FAILED)")]
    end

    subgraph Mac ["💻 macOS System"]
        Daemon["rekordbox-daemon\n(Cocoa NSWorkspace Observer)"]
        RB["Rekordbox (macOS)"]
        MasterDB[("master.db\n~/Library/Pioneer/rekordbox/")]
        Backups["~/Music/rekordbox/Backups/\n(Timestamped Safety Snapshots)"]
        DesktopGUI["Rekordbox Collection Manager\n(PySide6 Desktop App & CLIs)"]
    end

    iOS -- "HTTP /v2/pipeline (Batch Events)" --> ChangelogQueue
    TursoDB -- "HTTP /v2/pipeline (Pull Snapshots)" --> iOS

    RB -. "NSWorkspace Lifecycle\n+ 30s Idle Poll" .-> Daemon
    Daemon -- "1. Auto-Backup" --> Backups
    Daemon -- "2. Apply Pending Events" --> MasterDB
    Daemon -- "3. Export Fresh Snapshot" --> TursoDB

    DesktopGUI <--> MasterDB
```

## Subdirectories & System Boundaries
- `src`: [src](./src/README.md) - Source root (`module-root = "src"` in `pyproject.toml`) housing the core Python library, CLI entry points, macOS `launchd` sync daemon, analytics engine, backup/restore, Turso sync ([`rekordbox_collection_manager`](./src/rekordbox_collection_manager/README.md)), PySide6 desktop GUI ([`gui`](./src/rekordbox_collection_manager/gui/README.md)), and custom analytics chart widgets ([`analytics_widgets`](./src/rekordbox_collection_manager/gui/analytics_widgets/README.md)).
- `ios`: [ios](./ios/README.md) - SwiftUI iOS companion subtree containing the Xcode project container ([`RekordboxCompanion`](./ios/RekordboxCompanion/README.md)) and single application target (`iOS 17.0`, `Swift 5.0`) with Turso config, domain models, SQLite/HTTP sync services, and SwiftUI views.
- `scripts`: [scripts](./scripts/README.md) - Operational bash/Python/C utilities for macOS Rekordbox backup and restore (`backup_rekordbox_mac.sh`, `restore_rekordbox_mac.sh`), native `.app` bundle compilation (`build_app.sh`, `launcher.c`), and desktop/iOS SemVer release management (`desktop_version.py`, `ios_version.py`).
- Non-contract directories: `tests/` (pytest test suite) and `assets/` (application icon assets: `icon.icns`, `icon.png`, `icon.svg`).

## Global Invariants
- **Rekordbox Process Mutual Exclusion**: Database mutations, backups, restores, and Turso syncs abort whenever Pioneer Rekordbox is running (`[src/rekordbox_collection_manager/utils.py:is_rekordbox_running]`, `[src/rekordbox_collection_manager/utils.py:ensure_rekordbox_not_running]`, `[src/rekordbox_collection_manager/backup_restore.py:create_database_backup]`, `[src/rekordbox_collection_manager/backup_restore.py:perform_restore]`, `[src/rekordbox_collection_manager/turso_sync.py:run_sync_cycle]`, `[src/rekordbox_collection_manager/daemon.py:RekordboxAppObserver]`, `[scripts/backup_rekordbox_mac.sh:pgrep]`, `[scripts/restore_rekordbox_mac.sh:SKIP_PROCESS_CHECK]`) unless explicitly bypassed (`force=True` / `--force` or `SKIP_PROCESS_CHECK=1`). The desktop GUI enforces this with a `3000ms` `safety_timer` across views (`[src/rekordbox_collection_manager/gui/main_window.py:MainWindow]`).
- **SQLite Lock Separation & Serialized Cloud Sync**: `[src/rekordbox_collection_manager/turso_sync.py:export_collection_to_turso]` closes the local SQLite connection prior to sending HTTP `/v2/pipeline` batches (`100`–`300` statements), and `[src/rekordbox_collection_manager/turso_sync.py:apply_changelog_events]` updates remote `changelog_events` (`'PROCESSED'`/`'FAILED'`) only after releasing the SQLite write lock. On iOS, `[ios/RekordboxCompanion/RekordboxCompanion/Services/AppState.swift:AppState.sync()]` is `@MainActor`-isolated, guards `!isSyncing`, and runs `pushPendingEvents -> pullLatestCollection -> pullEventStatuses -> loadFromCache` sequentially against `[ios/RekordboxCompanion/RekordboxCompanion/Services/LocalDatabase.swift:LocalDatabase.shared]`.
- **Streaming Track Exclusion & Play-Count Floor**: Streaming tracks (`[src/rekordbox_collection_manager/utils.py:STREAMING_FILE_TYPES]`, `[src/rekordbox_collection_manager/utils.py:build_exclude_streaming_clause]`, `[ios/RekordboxCompanion/RekordboxCompanion/Models/Track.swift:Track.isStreaming]`, `[ios/RekordboxCompanion/RekordboxCompanion/Services/LocalDatabase.swift:pruneStreamingTracks()]`) are excluded across desktop analytics/search/sync and iOS track/playlist/live-set views; play-count mutations on both platforms clamp `DJPlayCount >= 0` (`[src/rekordbox_collection_manager/edit_play_count.py:update_track_play_count]`, `[src/rekordbox_collection_manager/rename_history.py:soft_delete_history_session]`, `[ios/RekordboxCompanion/RekordboxCompanion/Services/AppState.swift:AppState.adjustPlayCount(track:delta:)]`, `[ios/RekordboxCompanion/RekordboxCompanion/Services/LocalDatabase.swift:updatePlayCountLocally(trackId:newCount:)]`).
- **Destructive Action & Unsaved-Edit Safeguards**: Both Python (`[src/rekordbox_collection_manager/backup_restore.py:perform_restore]`) and shell (`[scripts/restore_rekordbox_mac.sh:SAFETY_BACKUP_DIR]`) restores create a pre-restore safety snapshot before overwriting live Rekordbox directories; `[src/rekordbox_collection_manager/replace_file.py:replace_track_audio_file]` requires an AIFF target and rolls back file moves on DB failure; GUI navigation invokes `confirm_unsaved_changes()` before discarding staged edits (`[src/rekordbox_collection_manager/gui/tracks_view.py:TracksView.confirm_unsaved_changes]`, `[src/rekordbox_collection_manager/gui/track_table.py:TracksTableWidget.confirm_unsaved_changes]`).
- **Signed/Unsigned 32-bit MyTag & Smart Playlist Boundaries**: Desktop MyTag and smartlist queries resolve signed/unsigned 32-bit tag IDs via `[src/rekordbox_collection_manager/utils.py:resolve_tag_matching_ids]` and `[src/rekordbox_collection_manager/playlists.py:parse_smartlist_to_sql]`; iOS treats smart playlists (`[ios/RekordboxCompanion/RekordboxCompanion/Models/Playlist.swift:Playlist.isSmart]` where `attribute == 4`) as read-only (`[ios/RekordboxCompanion/RekordboxCompanion/Views/PlaylistDetailView.swift:PlaylistDetailView.body]`) and scopes MyTag category browsing/editing to root `Genre` and `Style` folders (`[ios/RekordboxCompanion/RekordboxCompanion/Views/TagsView.swift:TagsView.categoryFolders]`, `[ios/RekordboxCompanion/RekordboxCompanion/Views/TrackDetailSheet.swift:TrackDetailSheet.categoryFolders]`).
- **Release Versioning Synchronization**: Desktop SemVer (`pyproject.toml` and `uv.lock`) is updated atomically by `[scripts/desktop_version.py:update_pyproject]`/`[scripts/desktop_version.py:update_uv_lock]` and read by `[src/rekordbox_collection_manager/__init__.py:_get_version]` and `[scripts/build_app.sh:APP_VERSION]`; iOS `MARKETING_VERSION` and `CURRENT_PROJECT_VERSION` in `project.pbxproj` are updated atomically by `[scripts/ios_version.py:update_pbxproj]`.

## Global Side Effects
- **Rekordbox Local Databases**: Reads and mutates SQLCipher/SQLite `master.db` (`~/Library/Pioneer/rekordbox/`) and DeviceSQL `exportLibrary.db` across `djmdContent`, `djmdPlaylist`, `djmdSongPlaylist`, `djmdMyTag`, `djmdSongMyTag`, `djmdHistory`, `djmdSongHistory`, `djmdCue`, `djmdArtist`/`djmdAlbum`/`djmdGenre`/`djmdLabel`, `agentRegistry`, and legacy `content`/`history`/`songHistory` tables (`[src/rekordbox_collection_manager/utils.py:open_database]`).
- **Turso Cloud Database (`libSQL`)**: Executes HTTPS `POST` requests against `{url}/v2/pipeline` from macOS (`[src/rekordbox_collection_manager/turso_sync.py:TursoHttpClient]`, managing 7 tables and 9 indexes) and iOS (`[ios/RekordboxCompanion/RekordboxCompanion/Services/TursoSyncService.swift:TursoSyncService.executePipeline(requests:)]`), reading credentials from `.env`/environment on macOS and `[ios/RekordboxCompanion/RekordboxCompanion/Config/TursoConfig.swift:TursoConfig]`/`UserDefaults.standard` on iOS.
- **iOS Local Persistence**: Creates and mutates SQLite `rekordbox_companion.db` across 9 tables and 3 indexes in `.documentDirectory` (`[ios/RekordboxCompanion/RekordboxCompanion/Services/LocalDatabase.swift:openDatabase()]`) alongside `UserDefaults.standard` keys `"turso_database_url"` and `"turso_auth_token"`.
- **macOS Filesystem, Backups, Artwork & OS Integrations**: Creates/restores backup trees under `~/Music/rekordbox/Backups/`, removes stale `-wal`/`-shm` files on restore, moves/renames/Finder-trashes audio files (`osascript`), writes `PIONEER/Artwork` JPEGs and `~/.cache/rekordbox-collection-manager/artwork_cache`, persists `~/.config/rekordbox-collection-manager/sync_state.json`, writes `.m3u8`/XML exports, manages the `launchd` service (`~/Library/LaunchAgents/...` via `launchctl`), compiles/signs `dist/Rekordbox Collection Manager.app`, and mutates `pyproject.toml`/`uv.lock`/`project.pbxproj` plus git commits/tags when release scripts are run with `--commit`/`--tag`.

## System Verification
- Run: `uv run pytest`
- Optional iOS build verification (no XCTest target exists in `RekordboxCompanion.xcodeproj`): `xcodebuild -project ios/RekordboxCompanion/RekordboxCompanion.xcodeproj -scheme RekordboxCompanion build`

---

## Table of Contents
- [1. Desktop Application (`rekordbox-manager` / `rekordbox-gui`)](#1-desktop-application-rekordbox-manager--rekordbox-gui)
  - [Views & Features](#views--features)
  - [Launching the Desktop App](#launching-the-desktop-app)
  - [Desktop App Versioning & SemVer CLI](#desktop-app-versioning--semver-cli)
- [2. Rekordbox Companion iOS App](#2-rekordbox-companion-ios-app)
  - [Key Features & Desktop Parity](#key-features--desktop-parity)
  - [Building & Installing on iPhone](#building--installing-on-iphone)
  - [App Versioning & SemVer CLI](#app-versioning--semver-cli)
- [3. Rekordbox Sync Daemon (`rekordbox-daemon`)](#3-rekordbox-sync-daemon-rekordbox-daemon)
  - [Event-Driven Cocoa Observer & Mobile Poll](#event-driven-cocoa-observer--mobile-poll)
  - [Configuration (`.env`)](#configuration-env)
  - [CLI Commands](#cli-commands)
- [4. Replace / Upgrade Audio File (`replace-file`)](#4-replace--upgrade-audio-file-replace-file)
- [5. Rename History (`rename-history`)](#5-rename-history-rename-history)
- [6. Edit Play Count (`edit-play-count`)](#6-edit-play-count-edit-play-count)
- [7. Backup & Restore Operations](#7-backup--restore-operations)
- [Development & Testing](#development--testing)

---

## 1. Desktop Application (`rekordbox-manager` / `rekordbox-gui`)

**Rekordbox Collection Manager** is a native macOS desktop application built with **PySide6 (Qt6)** featuring a Pioneer DJ-inspired dark theme. It combines collection inspection, history curation, play count modification, lossless file upgrades, and cloud sync into a single unified desktop interface.

### Views & Features

#### 🛡️ Live Safety Guard Banner
- Continuously inspects whether the official Rekordbox desktop process is running.
- Safely disables write transactions when Rekordbox is open to protect SQLite journals (`master.db-wal` / `master.db-shm`) from corruption or concurrent lock errors.

#### 🎵 Tracks View & Track Detail View
- **Instant Search & Multi-Filtering**: Search across Title, Artist, Key, Tags, Playlists, or Filename, plus filter by purchase status (`Downloaded`, `Purchased Only`, `Purchasable >=3 plays`), store, play count, audio format, musical key, BPM range, MyTags (`ANY`/`ALL`), and playlists (`ANY`/`ALL`).
- **Multi-Column Sorting**: Sort by Title, Artist, Key, BPM, Duration, Play Count, Bitrate, Audio Format, Purchase Store, Folder, or File Name.
- **Track Detail View**:
  - Inspect full audio tech specs (sample rate, bit depth, bitrate, format, file size, file path), artwork, star rating, BPM, key, and play count.
  - Manage MyTag chips, playlist memberships, and purchase store attribution (`Purchased At`, stored in Rekordbox track comments).
  - View the history log listing every DJ session and venue where the track was played, with click-through jump-to-session navigation.

#### 📁 Playlists View
- Full hierarchical tree navigation of playlist folders, standard playlists, and smart playlists (`attribute = 4`).
- Search tracks within a playlist, view track counts and total duration, create or delete empty playlists/folders, stage inline playlist/tag chip edits, and copy tracklists to clipboard.

#### 🏷️ Tags View
- Visual manager for Pioneer Rekordbox **MyTags**.
- Organized by **Genre** and **Style** category folders, with tag creation, deletion of empty tags, and alphabetical sorting (`A to Z` / `Z to A`) per folder.
- Click any tag to inspect all collection tracks assigned to it.

#### 🕒 History Sessions View
- **Full Hierarchy Loading**: Browses years, months, and individual DJ history playlists without sluggish pagination.
- **Chronological Tree Ordering**: Automatically realigns Rekordbox `djmdHistory.Seq` so months are strictly chronological (January to December) instead of alphabetical, and sessions are sorted from day 01 to day 31.
- **In-Place Editing**: Quick editing of session dates and custom names with `● Staged` visual diff indicators.
- **One-Click Tools**: Auto-extract dates from naming conventions, realign sequence numbers, and execute atomic batch commits.
- **Session Inspector & History Details**:
  - View full tracklists with cue times and duration.
  - **Played With Chips**: Shows transition context (which track was played immediately before or after).
  - **DJ Location Tagging**: Assign or autocomplete venue and gig locations to entire sessions or specific tracks.

#### 🔢 Play Count Manager View
- In-line numeric play count editing with dedicated `[-]` and `[+]` quick stepper buttons on every row.
- Keyboard shortcuts: `+` and `-` keys adjust the highlighted track count.
- Multi-select batch actions (+1, -1, reset to 0, or set custom play count).
- Floor-at-zero safeguard: Play counts can never become negative.

#### 🔄 Replace / Upgrade Audio File View
- Safely upgrade MP3 tracks to AIFF or replace existing AIFF files with another AIFF release (plus a Merge Mode for imported duplicate tracks).
- **100% Data Preservation**: Preserves Rekordbox cue points, hot cues, beatgrids, play counts, original "Date Added" (`StockDate`), playlist memberships, and history session entries.
- Automated audio metadata inspection and side-by-side technical comparison (sample rate, bit depth, bitrate, channels, duration).
- Configurable action for the old audio file: keep, move to macOS Trash, or rename in place to `.bak`.

#### 📊 Analytics View
- Interactive library and live-set analytics dashboard powered by five custom charts: **Crate Health Matrix**, **Camelot Wheel**, **Set Turnover & DNA**, **Survival Decay**, and **Transitions & Combos**.
- Click chart regions or KPI cards to drill down into matching tracks and export purge crates to `.m3u8`, Rekordbox XML, or a new Rekordbox playlist.

#### ℹ️ Collection Info View
- Summary statistics of your Rekordbox library: total track count, total playlists, history sessions, MyTags, active database path, and file size.

### Launching the Desktop App

You can launch the app from the terminal or build a native macOS `.app` bundle:

**Option A: Native macOS App (Dock & Applications)**
```bash
# Build the native macOS .app bundle
./scripts/build_app.sh

# Open the native macOS app
open "dist/Rekordbox Collection Manager.app"
```
> **Tip:** Drag `dist/Rekordbox Collection Manager.app` to your **Applications** folder or Dock to use it like any native macOS app with custom Pioneer-themed app icon and shortcuts.

**Option B: From Terminal**
```bash
# Launch with default master.db
uv run rekordbox-manager
# or
uv run rekordbox-gui

# Launch with custom database (e.g. USB exportLibrary.db)
uv run rekordbox-manager -d /Volumes/USB/PIONEER/exportLibrary.db
```

### Desktop App Versioning & SemVer CLI

The desktop application follows **Semantic Versioning** (`MAJOR.MINOR.PATCH`) stored in [`pyproject.toml`](pyproject.toml) and synchronized with [`uv.lock`](uv.lock).

A dedicated CLI script at [`scripts/desktop_version.py`](scripts/desktop_version.py) manages version bumps, syncs the lockfile, and automates Git commits and tags:

```bash
# Check current desktop version
./scripts/desktop_version.py
# 💻 Rekordbox Collection Manager Desktop:
#    Version (SemVer): 0.9.0

# Bump PATCH: 0.9.0 -> 0.9.1
./scripts/desktop_version.py patch

# Bump MINOR: 0.9.0 -> 0.10.0
./scripts/desktop_version.py minor

# Bump MAJOR: 0.9.0 -> 1.0.0
./scripts/desktop_version.py major

# Set an explicit SemVer version
./scripts/desktop_version.py set 1.0.0

# Preview changes without modifying project files
./scripts/desktop_version.py patch --dry-run

# Bump version, sync uv.lock, auto-commit to git, and create annotated tag 'v0.9.1'
./scripts/desktop_version.py patch --commit --tag
```

---

## 2. Rekordbox Companion iOS App

The **Rekordbox Companion iOS App** (`ios/RekordboxCompanion/`) is a native mobile application built with **SwiftUI (iOS 17+)** that allows you to manage, organize, and curate your Rekordbox library anywhere from your iPhone.

```
ios/RekordboxCompanion/
├── RekordboxCompanion.xcodeproj    # Native Xcode Project
└── RekordboxCompanion/
    ├── Config/
    │   └── TursoConfig.swift        # Cloud DB connection configuration
    ├── Models/
    │   ├── Track.swift              # Track model & rating helpers
    │   ├── Playlist.swift           # Playlist & folder models
    │   ├── Tag.swift                # MyTag models
    │   ├── LiveSession.swift        # Live DJ set session & track models
    │   └── ChangelogEvent.swift     # Mutation events
    ├── Services/
    │   ├── LocalDatabase.swift      # 0ms SQLite3 local storage & mutation queue
    │   ├── TursoSyncService.swift   # Turso libSQL HTTP pipeline client
    │   └── AppState.swift           # App-wide state & sync coordinator
    └── Views/
        ├── MainTabView.swift        # 5-Tab root navigation
        ├── TracksView.swift         # Search, filter, swipe actions, track details
        ├── TrackDetailSheet.swift   # Playlist & MyTag chip assignment editor + track specs
        ├── PlaylistsView.swift      # Folder & playlist browser, playlist creation
        ├── PlaylistDetailView.swift # Track reordering & removal
        ├── LiveSetView.swift        # Mobile DJ live set tracker, venue tagging & history logging
        ├── TagsView.swift           # MyTag categories & tagged tracks
        └── SyncQueueView.swift      # Real-time sync status, queue monitor, manual sync & settings
```

### Key Features & Desktop Parity

- **Excludes Streaming Tracks**: Exactly like the desktop app, tracks from streaming providers (Spotify, Apple Music, Beatport, Beatsource, SoundCloud, Tidal) are completely filtered out from collection queries, playlists, and cloud sync.
- **Offline-First Architecture (0ms Latency)**: Uses an embedded SQLite3 engine on the device (`LocalDatabase.swift`). Searching across tracks, filtering by play status/BPM/key/MyTags, and queuing playlist/MyTag edits responds locally with zero network delay.
- **In-Playlist Drag & Drop Reordering**: Reorder tracks directly within standard playlists using iOS native drag-and-drop (`.onMove`) and remove tracks via trailing swipe actions. New track positions are queued and synced back to `djmdSongPlaylist.TrackNo` in `master.db`.
- **Quick Track Actions & Live Set Logging**: Swipe trailing on tracks in `TracksView` to jump straight to `TrackDetailSheet` or assign a playlist, or track played songs in real time in `LiveSetView` (`LiveSession`).
- **Genre & Style Tag Categorization**: MyTags are strictly categorized into **Genre** and **Style** sections (matching desktop behavior, hiding Components and Situation) with live track counts per tag.
- **Event-Sourced Cloud Sync**: Mobile edits (playlist track additions/removals/reordering, playlist creation, `MyTag` assignments, and completed `Live Set` sessions) are logged into a local mutation queue (`changelog_events`) and synced to Turso cloud via an HTTP pipeline (`TursoSyncService.swift`).
- **5 Tabbed Interface**:
  1. **Tracks**: Collection browser with 6 sort options (Title A–Z, Artist A–Z, BPM ascending/descending, Play Count, Date Added), live search, play-status/BPM/key/MyTag filters, trailing swipe actions (`Details`, `Playlist`), and `TrackDetailSheet` for viewing track specs and editing playlist/MyTag chips.
  2. **Playlists**: Complete folder and playlist hierarchy navigation with live track count badges, drag-and-drop track reordering, and creation of new mobile playlists.
  3. **Live Set**: Real-time mobile DJ set tracker. Log played tracks during a gig, track transitions, tag venues/dates, and sync completed sessions into Rekordbox history.
  4. **MyTags**: Browse your collection grouped strictly by Genre and Style categories with track count badges.
  5. **Sync & Queue**: Cloud connection health check, pending change counter, processed event audit history, dynamic version display, Turso settings sheet, and manual cloud sync trigger.

### Building & Installing on iPhone

1. **Open the project in Xcode**:
   ```bash
   open ios/RekordboxCompanion/RekordboxCompanion.xcodeproj
   ```
2. **Select your Development Team**:
   - In Xcode, select the `RekordboxCompanion` project in the left navigator.
   - Go to **Signing & Capabilities** tab.
   - Under **Signing**, select your personal Apple ID team.
3. **Run on Physical iPhone or Simulator**:
   - Connect your iPhone via USB or select an iOS 17+ Simulator from the scheme dropdown.
   - Press **Cmd + R** (or click the Play button) to build and install.
4. **Cloud Credentials**:
   - The app comes pre-configured with your default Turso database URL and auth token compiled into `TursoConfig.swift`.
   - Inside **Sync & Queue > Settings** (gear icon), you can view or override the Database URL, replace the masked Auth Token (`SecureField`), or tap **Reset to .env Defaults** to restore the `TursoConfig.swift` defaults.

### App Versioning & SemVer CLI

The companion app follows **Semantic Versioning** (`MAJOR.MINOR.PATCH`) mapped directly to Apple's native build settings:
- **Marketing Version (`MARKETING_VERSION` / `CFBundleShortVersionString`)**: SemVer string (`0.1.0`) shown on the App Store, TestFlight, and dynamically rendered in the app footer.
- **Build Number (`CURRENT_PROJECT_VERSION` / `CFBundleVersion`)**: Monotonically increasing build integer (`1, 2, 3...`) required for TestFlight/App Store uploads.

A dedicated CLI script at [`scripts/ios_version.py`](scripts/ios_version.py) manages version bumps atomically across both `Debug` and `Release` build configurations in `project.pbxproj`:

```bash
# Check current version and build number
./scripts/ios_version.py
# 📱 Rekordbox Companion iOS:
#    Marketing Version (SemVer): 0.1.0
#    Build Number (Internal):    1

# Bump PATCH: 0.1.0 -> 0.1.1 (build incremented: 1 -> 2)
./scripts/ios_version.py patch

# Bump MINOR: 0.1.0 -> 0.2.0 (build incremented: 1 -> 2)
./scripts/ios_version.py minor

# Bump MAJOR: 0.1.0 -> 1.0.0 (build incremented: 1 -> 2)
./scripts/ios_version.py major

# Bump only the internal build number (keeps SemVer, increments build)
./scripts/ios_version.py build

# Set an explicit SemVer version
./scripts/ios_version.py set 0.2.0

# Preview changes without modifying project files
./scripts/ios_version.py patch --dry-run

# Bump version, auto-commit to git, and create annotated tag 'ios-v0.1.1'
./scripts/ios_version.py patch --commit --tag
```

> **Why a dedicated CLI instead of Apple's `agvtool`?**
> Apple's built-in `agvtool` (from 2001/2017) has known parsing bugs with modern Xcode projects that use `GENERATE_INFOPLIST_FILE = YES`. It fails searching for `.../../YES` and silently leaves `MARKETING_VERSION` untouched in `project.pbxproj`. The `scripts/ios_version.py` script replaces it with a fast, safe, atomic SemVer manager.

---

## 3. Rekordbox Sync Daemon (`rekordbox-daemon`)

The sync daemon is an event-driven macOS background service that bridges your desktop `master.db` with the **Turso (libSQL)** cloud database. It guarantees that on-the-go mobile edits are safely applied to your collection **without risking SQLite file corruption or file-locking conflicts**.

### Event-Driven Cocoa Observer & Mobile Poll
- Integrates directly with macOS `NSWorkspace` notifications (`NSWorkspaceDidTerminateApplicationNotification` and `NSWorkspaceDidLaunchApplicationNotification`) via `pyobjc-framework-cocoa`.
- **Responsive Cocoa Run Loop**: Dispatches launch/quit notifications natively via `NSRunLoop` in 1-second signal-aware slices and polls Turso for `PENDING` mobile changelog events every `30` seconds while Rekordbox is closed (falling back to polling via `poll_interval` if PyObjC is unavailable).
- **Automated Lifecycle**:
  1. Detects when you quit Rekordbox.
  2. Immediately creates a timestamped safety backup in `~/Music/rekordbox/Backups/`.
  3. Pulls pending change events (`changelog_events`) from Turso and applies them atomically to `master.db`.
  4. Exports an updated snapshot of tracks, playlists, and tags back to Turso.

### Configuration (`.env`)
Create a `.env` file in the root of the repository (automatically ignored by git):
```env
TURSO_DATABASE_URL="https://your-database-name.turso.io"
TURSO_AUTH_TOKEN="your-turso-jwt-token"
```

### CLI Commands

```bash
# Check current daemon & cloud sync status (process state, counts, pending queue):
uv run rekordbox-daemon status

# Run a one-time sync cycle right now:
uv run rekordbox-daemon sync

# Run the daemon in foreground (watches Rekordbox events in real-time):
uv run rekordbox-daemon run

# Install as a persistent macOS background service (auto-starts on login via launchd):
uv run rekordbox-daemon install

# Remove the macOS background service:
uv run rekordbox-daemon uninstall
```

---

## 4. Replace / Upgrade Audio File (`replace-file`)

A specialized utility to replace or upgrade collection tracks (upgrading an MP3 file to AIFF, or replacing an existing AIFF track with another AIFF file) without losing any Rekordbox metadata.

### Highlights
- Transfers and preserves:
  - Cue points & Hot cues (`djmdCue`)
  - Beatgrids & Analysis data
  - Historical DJ play counts and session appearances
  - Original "Date Added" (`StockDate`)
  - Playlist memberships
  - MyTag assignments
- Can be used via the **Replace File** view in the desktop GUI or as a standalone CLI tool.

### CLI Usage
```bash
# Upgrade a specific track by ID with a new lossless file:
uv run replace-file --track-id 12345 --new-file "/path/to/track.aiff" --action keep

# Actions for the old audio file:
#   --action keep     (leave old file intact)
#   --action trash    (move old file to macOS Trash)
#   --action backup   (rename old file in place with a .bak suffix)
```

---

## 5. Rename History (`rename-history`)

Interactively rename and reorder recorded DJ history playlists/sessions in your Rekordbox collection or USB export database.

### Features
- **Chronological Tree Ordering**: Automatically realigns Rekordbox `djmdHistory.Seq` so months are strictly chronological (January to December, month 1 to 12) instead of alphabetical, and history sessions are sorted chronologically from day 01 (top) to day 31 (bottom).
- **Auto-folder Hierarchy**: Ensures target Year and Month folders exist when moving/renaming sessions.
- **Standalone Realign**: Realign all months and sessions anytime via CLI.

### CLI Usage
```bash
# Interactive mode
uv run rename-history

# Realign/sort all months and history sessions without interactive prompt:
uv run rename-history --sort

# Custom database path:
uv run rename-history /Volumes/USB/PIONEER/exportLibrary.db
```

---

## 6. Edit Play Count (`edit-play-count`)

Interactively increment (+1) or decrement (-1) the play count of songs in your Rekordbox collection (`master.db`) or USB export database (`exportLibrary.db`).

### Features
- **Interactive track search**: Type track title keywords to filter matches.
- **Arrow-key selection**: Navigate matching results with arrow keys (↑ / ↓) and press Enter.
- **Safety check**: Automatically checks whether Rekordbox is running before database modifications.
- **Floor at zero**: Decrementing will never cause play count to go negative.

### CLI Usage
```bash
# Interactive mode:
uv run edit-play-count

# Search directly for a track title:
uv run edit-play-count "Titanium"

# Automatically increment (+1) on selection:
uv run edit-play-count "Titanium" --inc

# Automatically decrement (-1) on selection:
uv run edit-play-count "Titanium" --dec

# With custom database path:
uv run edit-play-count -d /Volumes/USB/PIONEER/exportLibrary.db
```

---

## 7. Backup & Restore Operations

Safeguard and restore your entire macOS Rekordbox collection (`master.db`, playlists, sync caches, and configs) both inside the GUI application and via standalone CLI scripts:

### Dedicated Storage Location
Backups are organized in `~/Music/rekordbox/Backups/` (alongside Rekordbox's `Recording/` and `Sampler/` folders), keeping your Desktop clean and preventing unwanted iCloud Drive auto-sync uploads.

### In the Desktop App
- **`📦 Quick Backup` Header Button**: 1-click snapshot saved straight to `~/Music/rekordbox/Backups/` with prompt to reveal in Finder.
- **`Database` Menu**:
  - `📁 Switch Database...` (`Cmd+O`)
  - `🔄 Reload Database` (`Cmd+R`)
  - `📦 Create Backup...` (`Cmd+Shift+B`)
  - `📥 Restore from Backup...`: Opens the guided **Restore Dialog** listing all snapshots with creation timestamps, sizes, Rekordbox process safety guard, and automatic collection reloading.
  - `📂 Reveal Database in Finder` & `🗂️ Reveal Backups in Finder`

### Standalone Shell Scripts (`scripts/`)
```bash
# Create timestamped collection snapshot:
./scripts/backup_rekordbox_mac.sh

# Guided interactive restore (lists backups, prompts for choice):
./scripts/restore_rekordbox_mac.sh

# Restore specific backup directly:
./scripts/restore_rekordbox_mac.sh ~/Music/rekordbox/Backups/rekordbox_mac_backup_20260920_002628

# Non-interactive mode (auto-confirms):
./scripts/restore_rekordbox_mac.sh -y ~/Music/rekordbox/Backups/rekordbox_mac_backup_20260920_002628
```

---

## Development & Testing

### Running Tests
```bash
# Run full pytest test suite (420+ tests)
uv run pytest
```

### Adding New Tools
Each CLI tool is self-contained in a single file under `src/rekordbox_collection_manager/`:
1. **Create the tool file**: `src/rekordbox_collection_manager/<your_tool>.py` with a `main()` function.
2. **Register the tool by name**: In `pyproject.toml`, add an entry under `[project.scripts]`:
   ```toml
   [project.scripts]
   your-tool = "rekordbox_collection_manager.your_tool:main"
   ```
