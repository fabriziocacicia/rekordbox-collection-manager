# RekordboxCompanion

## Overview
Xcode project container directory for the `RekordboxCompanion` iOS companion application (`iOS 17.0`, `Swift 5.0`). The project bundle (`RekordboxCompanion.xcodeproj`) defines a single `RekordboxCompanion` application target (`com.apple.product-type.application`) with no XCTest target and compiles all Swift source code from the `RekordboxCompanion` subdirectory.

## Subdirectories & Boundaries
- `RekordboxCompanion`: [RekordboxCompanion](./RekordboxCompanion/README.md) - Root `@main` SwiftUI app module (`RekordboxCompanionApp`) and its `Config`, `Models`, `Services`, and `Views` subsystems (`TursoConfig` `UserDefaults` configuration, Rekordbox domain models, `LocalDatabase` SQLite cache + `TursoSyncService` HTTP pipeline sync + `AppState`, and SwiftUI views).

---

## Sideloading & Permanent Installation via SideStore

Without a paid $99/year Apple Developer membership, free personal team provisioning profiles expire after 7 days. `RekordboxCompanion` supports permanent installation using **SideStore**—an on-device sideloading manager that re-signs and refreshes the app automatically in the background over Wi-Fi without needing a Mac.

### 1. One-Time Setup
1. **Install SideStore**: Install SideStore onto your iPhone once from your Mac using `SideServer` or `AltServer` with your free Apple ID.
2. **Pairing File**: Export the `.mobiledevicepairing` file from your Mac to the Files app on your iPhone.
3. **Local VPN**: Enable SideStore's on-device WireGuard loopback VPN profile (allows SideStore to communicate with local developer services).

### 2. Installing Rekordbox Companion
- **Option A (GitHub Releases)**: Download the latest `RekordboxCompanion-v<version>.ipa` asset from the [GitHub Releases](https://github.com/fabriziocacicia/rekordbox-collection-manager/releases) tab in Safari on your iPhone, tap Share $\to$ **Open in SideStore**.
- **Option B (Local Build)**: Compile and package the IPA locally:
  ```bash
  ./scripts/build_ios.sh --package
  ```
  AirDrop the generated `dist/RekordboxCompanion-v<version>.ipa` to your iPhone and open in SideStore.

### 3. Overnight Hands-Off Refresh Automation
To ensure the 7-day expiration timer never runs out:
1. Open the built-in iOS **Shortcuts** app.
2. Go to the **Automation** tab $\to$ Create Personal Automation.
3. Choose **Time of Day** (e.g. *Every Day at 2:00 AM*).
4. Add Action: **SideStore** $\to$ *Refresh All Apps*.
5. Set "Run Immediately" (without confirmation).

Because you connect to home Wi-Fi daily, the app stays permanently refreshed and operational.
