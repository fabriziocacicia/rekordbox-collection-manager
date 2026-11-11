# Config

## Overview
Configuration namespace for Turso libSQL remote synchronization endpoints and credentials inside the `RekordboxCompanion` iOS application target. It stores user-editable overrides in `UserDefaults.standard` and falls back to compiled default values when overrides are unset.

## Interface
- `enum TursoConfig` — Caseless namespace enum (`internal` target visibility) exposing static configuration properties and reset helpers (`[TursoConfig.swift:TursoConfig]`).
- `static let defaultDatabaseURL: String` — Default Turso database HTTPS endpoint constant (`[TursoConfig.swift:defaultDatabaseURL]`).
- `static let defaultAuthToken: String` — Default Turso authentication JWT constant (`[TursoConfig.swift:defaultAuthToken]`).
- `static var databaseURL: String` — Computed getter/setter reading from `UserDefaults.standard` (falling back to `defaultDatabaseURL`) and persisting trimmed strings (`[TursoConfig.swift:databaseURL]`).
- `static var authToken: String` — Computed getter/setter reading from `UserDefaults.standard` (falling back to `defaultAuthToken`) and persisting trimmed strings (`[TursoConfig.swift:authToken]`).
- `static func resetToDefaults()` — Clears persisted URL and token overrides from `UserDefaults.standard` (`[TursoConfig.swift:resetToDefaults]`).

## Invariants
- `TursoConfig` is a caseless `enum` and cannot be instantiated (`[TursoConfig.swift:TursoConfig]`).
- `databaseURL` setter trims leading and trailing `.whitespacesAndNewlines` before storing; getter falls back to `defaultDatabaseURL` only when the stored `UserDefaults.standard` string is `nil` (no URL syntax or non-empty string validation is enforced) (`[TursoConfig.swift:databaseURL]`).
- `authToken` setter trims leading and trailing `.whitespacesAndNewlines` before storing; getter falls back to `defaultAuthToken` only when the stored `UserDefaults.standard` string is `nil` (no token format or non-empty string validation is enforced) (`[TursoConfig.swift:authToken]`).

## Side Effects
- `databaseURL` reads and writes `UserDefaults.standard` under private key `"turso_database_url"` (`[TursoConfig.swift:databaseURL]`).
- `authToken` reads and writes `UserDefaults.standard` under private key `"turso_auth_token"` (`[TursoConfig.swift:authToken]`).
- `resetToDefaults()` removes `"turso_database_url"` and `"turso_auth_token"` entries from `UserDefaults.standard` (`[TursoConfig.swift:resetToDefaults]`).

## Verification
- Run: `<test-command>` — No XCTest target exists in `RekordboxCompanion.xcodeproj` (only the `RekordboxCompanion` application target is defined); verify compilation in Xcode or via `xcodebuild -project ios/RekordboxCompanion/RekordboxCompanion.xcodeproj -scheme RekordboxCompanion build`.
