# RekordboxCompanion

## Overview
Xcode project container directory for the `RekordboxCompanion` iOS companion application (`iOS 17.0`, `Swift 5.0`). The project bundle (`RekordboxCompanion.xcodeproj`) defines a single `RekordboxCompanion` application target (`com.apple.product-type.application`) with no XCTest target and compiles all Swift source code from the `RekordboxCompanion` subdirectory.

## Subdirectories & Boundaries
- `RekordboxCompanion`: [RekordboxCompanion](./RekordboxCompanion/README.md) - Root `@main` SwiftUI app module (`RekordboxCompanionApp`) and its `Config`, `Models`, `Services`, and `Views` subsystems (`TursoConfig` `UserDefaults` configuration, Rekordbox domain models, `LocalDatabase` SQLite cache + `TursoSyncService` HTTP pipeline sync + `AppState`, and SwiftUI views).
