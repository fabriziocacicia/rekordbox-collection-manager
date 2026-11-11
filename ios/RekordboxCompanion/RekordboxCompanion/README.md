# RekordboxCompanion

## Overview
Application bundle root source module for the `RekordboxCompanion` SwiftUI iOS client (`Swift 5.0`, `iOS 17.0`). It defines the `@main` SwiftUI app entry point (`RekordboxCompanionApp`) and groups the Turso configuration, domain models, SQLite/sync services, and SwiftUI views.

## Interface
- `@main struct RekordboxCompanionApp: App` — SwiftUI application entry point (`[RekordboxCompanionApp.swift:@main]`, `[RekordboxCompanionApp.swift:RekordboxCompanionApp]`).
- `var body: some Scene` — `WindowGroup` hosting `MainTabView()` with `.environment(appState)` injecting the `@State private var appState = AppState()` instance (`[RekordboxCompanionApp.swift:body]`, `[RekordboxCompanionApp.swift:WindowGroup]`, `[RekordboxCompanionApp.swift:MainTabView]`, `[RekordboxCompanionApp.swift:.environment]`, `[RekordboxCompanionApp.swift:appState]`).
- Child Subdirectories:
  - [Config](./Config/README.md) — `TursoConfig` static endpoint/token overrides stored in `UserDefaults.standard`.
  - [Models](./Models/README.md) — `Track`, `Playlist`, `PlaylistTrack`, `Tag`, `TrackTag`, `ChangelogEvent`, `LiveSession`, `LiveSessionTrack`, and `LiveSessionStatus` value types.
  - [Services](./Services/README.md) — `AppState` observable state store, `LocalDatabase` SQLite persistence (`rekordbox_companion.db`), and `TursoSyncService` HTTP `/v2/pipeline` client.
  - [Views](./Views/README.md) — `MainTabView` and SwiftUI screens/sheets for tracks, playlists, tags, live DJ sets, and sync queue diagnostics.

## Invariants
- None

## Side Effects
- Initializes `@State private var appState = AppState()` on app launch (`[RekordboxCompanionApp.swift:appState]`), triggering `AppState.init()` cache loading from `LocalDatabase.shared` and an asynchronous `sync()` task per [Services](./Services/README.md); no direct I/O, persistence, or network calls occur inside `RekordboxCompanionApp.swift`.

## Verification
- Run: `<test-command>` — No XCTest target exists in `RekordboxCompanion.xcodeproj` (only the `RekordboxCompanion` application target is defined); verify compilation in Xcode or via `xcodebuild -project ios/RekordboxCompanion/RekordboxCompanion.xcodeproj -scheme RekordboxCompanion build`.
