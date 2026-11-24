# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **analytics**: Refine desktop Analytics dashboard UI layout, interactive KPI cards, Crate Health Matrix heatmap contrast, and track table density

### Fixed

- **analytics**: Localize desktop Analytics dashboard, status bar notifications, chart widgets, tooltips, and export dialogs into English for consistency with application language standards

## [0.23.0] - 2026-11-23

### Added

- **playlists**: Hide Rekordbox 7 system-internal 'CUE Analysis Playlist' across playlists view, search filters, track badges, unbound playlist reconciliation, and Turso cloud sync (#27)
- **playlists**: Add `πagged-Unbound` dynamic catch-all playlist for tagged tracks without a regular user playlist (#9)
- **cli**: Add standalone `sync-unbound` CLI command for local playlist reconciliation (#9)
- **daemon**: Trigger local `πagged-Unbound` reconciliation on Rekordbox termination, mobile change ingestion, and daemon startup (#9)
- **gui**: Automate `πagged-Unbound` reconciliation on collection load, app exit, and sync cycle (#9)

## [0.22.0] - 2026-11-23

### Changed

- **gui**: Compact Music Stores card layout with horizontal track badges and purchase ratio bars (#24)
- **replace**: Default old audio file operation to macOS Trash on file replacement and track merge (#7)

### Maintenance

- **release**: Clean up temporary release zip and release notes markdown post-release (#20)

## [0.21.0] - 2026-11-21

### Added

- **gui**: Add stores summary view and purchasable store filtering (#6)

### Maintenance

- **release**: Automate desktop version bump, packaging, and GitHub release pipeline (#16)
- **app**: Bundle standalone macOS .app with embedded Python dependencies (#14)
- **changelog**: Establish Keep a Changelog and backfill desktop releases (#15)
- **release**: Auto-merge unreleased git commits and enforce changelog hygiene (#18)

## [0.20.0] - 2026-11-18

### Added

- **filters**: Add NOT exclusion match mode for playlists and tags (#10)

## [0.19.0] - 2026-11-16

### Added

- **analytics**: Add Rekordbox Analytics dashboard with 5 visual vistas and crate pruning
- **gui**: Add planned purchase store tracking for purchasable tracks

### Fixed

- **analytics**: Suppress passive error modals, add teardown, and update 8-tab tests
- **gui**: Enforce unsaved prompts, restore safety guard, and cleanup timers

### Maintenance

- Reconcile README with current desktop, iOS, and daemon behavior
- **egm**: Add directory architecture contracts and zero-drift report
- Add AGENTS.md guidelines and context rules (#4)
- Delete tasks folder (#5)

## [0.18.4] - 2026-10-30

### Fixed

- Resolve track sorting by time and plays in tags and playlists views

## [0.18.3] - 2026-10-28

### Fixed

- **replace**: Resolve ExFAT Unicode normalization mismatch on file replacement

## [0.18.2] - 2026-10-28

### Fixed

- **replace**: Move replacement file instead of copying when checkbox is enabled

## [0.18.1] - 2026-10-27

### Fixed

- **replace**: Use numeric IDs for relational entities and support all Info fields

## [0.18.0] - 2026-10-22

### Added

- **diff**: Add 3-way preservation alternatives and rich hover tooltips
- **replace**: Allow choosing between current and replacement artwork in diff view

### Fixed

- **gui**: Replace generic sans-serif font in tooltips with platform font

## [0.17.2] - 2026-10-19

### Fixed

- **gui**: Preselect source track and synchronize tabs in Replace File view

## [0.17.1] - 2026-10-16

### Fixed

- **history**: Remove plus emoji from history import merge diff string

## [0.17.0] - 2026-10-15

### Added

- **filters**: Add purchasable status filter and hide play count filter
- **history**: Add external database history import, diff and merge tools

### Fixed

- **gui**: Auto-refresh views and track details on data changes and navigation
- **gui**: Prevent startup deadlock by decoupling SQLite queries from Turso HTTP calls and offloading filters to background worker

## [0.16.0] - 2026-10-10

### Added

- **gui**: Add purchase store detection, interactive chips, and filtering

## [0.15.3] - 2026-10-10

### Fixed

- **replace**: Exclude genre, bpm, and key from metadata diff and transfer

## [0.15.2] - 2026-10-10

### Fixed

- Remove new tag and new playlist buttons from tracks view toolbar

## [0.15.1] - 2026-10-10

### Fixed

- **gui**: Prevent segfault on tree clear and safe-stop background workers

## [0.15.0] - 2026-10-09

### Added

- Add 3-column metadata diff dialog and conflict resolution to replace view on desktop app

## [0.14.1] - 2026-10-09

### Fixed

- **build**: Replace shell script launcher with native Mach-O binary for window managers

## [0.14.0] - 2026-10-09

### Added

- Add purchased track status and searchable multi-select filters

## [0.13.0] - 2026-10-08

### Added

- Support deleting empty playlists and tags with disabled menu styling

## [0.12.0] - 2026-10-07

### Added

- Support AIFF to AIFF track replacement across desktop app

## [0.11.0] - 2026-10-07

### Added

- **playlists**: Add playlist and folder creation support across views and tables

## [0.10.0] - 2026-10-07

### Added

- **gui**: Stage tag and playlist edits in tracks table with save/discard bar
- **gui**: Enable Tags creation across track views and tables

## [0.9.4] - 2026-10-06

### Fixed

- **gui**: Ensure tag and playlist overflow badge is always displayed

## [0.9.3] - 2026-10-06

### Changed

- Moved history sessions view in 4th place in navigation and UI layout

### Fixed

- Prevent and clean up duplicate track tags and playlists

## [0.9.2] - 2026-10-05

### Fixed

- **gui**: Remove play count column from history session details

## [0.9.1] - 2026-10-05

### Added

- Add iOS versioning CLI script and update documentation for app release management
- Add desktop version management CLI script and documentation

### Fixed

- **gui**: Sync sidebar track count badges and auto-refresh on navigation

### Maintenance

- Update README to reflect 5-tab interface and LiveSetView addition

## [0.9.0] - 2026-09-30

### Added

- Implement Turso cloud sync engine and background daemon
- Add graceful SIGINT handling to daemon
- Add interactive chip badge addition and deletion for playlists and tags
- Integrate Turso cloud synchronization into GUI, workers, and application lifecycle
- **tags**: Add alphabetical sorting for tags in category folders

### Changed

- **sync**: Optimize Turso cloud sync with smart caching and multi-row batching

### Fixed

- **gui**: Resolve SIGSEGV startup crash from SQLite thread pool collision and unsafe worker termination
- **gui**: Remove dangling mouse press code causing NameError on chip deletion
- **gui**: Prevent crash and freeze on exit with graceful worker thread cleanup

### Maintenance

- Document Rekordbox sync daemon

## [0.8.1] - 2026-09-24

### Changed

- Remove track details button from toolbar
- Centralize track record building and formatting logic into shared utility functions

## [0.8.0] - 2026-09-24

### Added

- Add playlist management backend, GUI view, and comprehensive tests
- Implement My Tags feature with database queries, UI views, workers, and tests

## [0.7.0] - 2026-09-23

### Added

- Add utilities and worker options to exclude streaming tracks from queries

### Changed

- Migrate track table to QAbstractTableModel and update row scrolling in history detail view to infinite scrolling

### Maintenance

- **gui**: Upgrade PySide6 to 6.11.2 and modernize Qt runtime integration

## [0.6.0] - 2026-09-23

### Added

- Connect history detail view signals and optimize folder tree updates
- Add inline session title editing to history detail view
- Add full timestamp tooltip to date added field in track details

### Fixed

- Dynamically adjust completer popup width and chip input size based on content length

### Maintenance

- Add pytest as a development dependency and update test instructions

## [0.5.0] - 2026-09-21

### Added

- Add interactive playlist chips and inline search to track details view
- Add Rekordbox My Tags support to backend and GUI
- Add soft-delete and restore support for history sessions
- Allow bypassing rekordbox process check with SKIP_PROCESS_CHECK variable
- Add context menus and click handlers for history table row headers
- Add delete session functionality and button to history detail view
- Adjust track DJ play counts when deleting or restoring history sessions
- Add interactive DJ chips for collaborator artists in history views
- Add location autocomplete delegate and editor for history view

### Changed

- Reposition BadgeChip delete button to top-right corner and use set_items in track detail view setters
- Centralize history action button styles in theme and standardize button sizing

### Other Changes

- Style: narrow history view layout width and remove tree branch prefixes

## [0.4.0] - 2026-09-20

### Added

- Add file replacement feature with GUI integration
- Add macOS Rekordbox backup restoration script
- Implement backup and restore functionality with GUI and backend support

## [0.3.0] - 2026-09-20

### Added

- Implement dynamic version loading from pyproject.toml across app, build script, and tests
- Add TracksView component and play count filtering for track searches
- Add interactive database badge chip to main window header
- Add file type, location, tags, and playlist support to track search results
- Separate location into dedicated folder and file name columns with file manager actions
- Improve chip badge delegate with text elision and update default column widths
- Add comprehensive track detail view and history play tracking

### Changed

- Extract track table widget and delegates into dedicated module
- Redesign history detail view header with metadata chips and remove folder KPI card

### Maintenance

- Add the svg version of the icon to assets

## [0.2.0] - 2026-09-18

### Changed

- Rename package from rekordbox_collection_fixer to rekordbox_collection_manager

## [0.1.0] - 2026-09-18

### Added

- Add the rename history tool
- Allow editing history entry date in interactive rename session
- Add backup script for Rekordbox on macOS
- Add utility to check if Rekordbox is running and enforce check in CLI with --force support
- Add automatic parent folder creation and realignment for djmdHistory records
- Add play count editing and improve history sorting and ordering logic
- Prioritize history sessions without parentheses during sorting and add name utility functions
- Support parsing and composing history session names with location and artist fields
- Ignore soft-deleted records during history fetching and realignment
- Add PySide6 graphical user interface for collection management and play count editing
- Rebrand to Rekordbox Collection Manager and add native macOS application support
- Implement collapsible navigation sidebar with animated width transition and shortcut support
- Add support for staged edits and batch saving of track play counts
- Add interactive column header sorting to play count view
- Add history session detail view with background data fetching and styling
- Add vector SVG refresh icon and styling for header status check button

### Changed

- Remove redundant Italian warning message for running Rekordbox
- Consolidate date, text, and sorting utilities into shared module
- Remove automatic DateCreated synchronization from history names
- Improve play count stepper button styling and table layout
- Make play count column read-only and remove direct editing and batch set options
- Remove header sidebar toggle button and update info text font stack
- Remove auto-extract dates feature and update tests

[Unreleased]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.23.0...HEAD
[0.23.0]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.22.0...v0.23.0
[0.22.0]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.21.0...v0.22.0
[0.21.0]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.20.0...v0.21.0
[0.20.0]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.19.0...v0.20.0
[0.19.0]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.18.4...v0.19.0
[0.18.4]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.18.3...v0.18.4
[0.18.3]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.18.2...v0.18.3
[0.18.2]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.18.1...v0.18.2
[0.18.1]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.18.0...v0.18.1
[0.18.0]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.17.2...v0.18.0
[0.17.2]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.17.1...v0.17.2
[0.17.1]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.17.0...v0.17.1
[0.17.0]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.16.0...v0.17.0
[0.16.0]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.15.3...v0.16.0
[0.15.3]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.15.2...v0.15.3
[0.15.2]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.15.1...v0.15.2
[0.15.1]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.15.0...v0.15.1
[0.15.0]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.14.1...v0.15.0
[0.14.1]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.14.0...v0.14.1
[0.14.0]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.9.4...v0.10.0
[0.9.4]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.9.3...v0.9.4
[0.9.3]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.9.2...v0.9.3
[0.9.2]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.9.1...v0.9.2
[0.9.1]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.9.0...v0.9.1
[0.9.0]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.8.1...v0.9.0
[0.8.1]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/fabriziocacicia/rekordbox-collection-manager/releases/tag/v0.1.0
