# Models

## Overview
Value-type domain models for Rekordbox tracks, playlists, MyTag metadata, sync changelog events, and live DJ set sessions inside the `RekordboxCompanion` iOS application target.

## Interface
- `struct ChangelogEvent: Identifiable, Hashable, Codable` — Sync queue event record (`id`, `action`, `payload`, `status`, `createdAt`, `processedAt?`, `errorMessage?`) (`[ChangelogEvent.swift:ChangelogEvent]`), with status/formatting properties `isPending` (`[ChangelogEvent.swift:ChangelogEvent.isPending]`), `isProcessed` (`[ChangelogEvent.swift:ChangelogEvent.isProcessed]`), `isFailed` (`[ChangelogEvent.swift:ChangelogEvent.isFailed]`), `statusColor` (`[ChangelogEvent.swift:ChangelogEvent.statusColor]`), `formattedDate` (`[ChangelogEvent.swift:ChangelogEvent.formattedDate]`), and `formattedProcessedDate` (`[ChangelogEvent.swift:ChangelogEvent.formattedProcessedDate]`).
- `enum LiveSessionStatus: String, Codable, CaseIterable` — Live set lifecycle states `active = "ACTIVE"`, `completed = "COMPLETED"`, and `discarded = "DISCARDED"` (`[LiveSession.swift:LiveSessionStatus]`).
- `struct LiveSession: Identifiable, Hashable, Codable` — Live DJ session metadata (`id`, `name`, `dateCreated`, `location?`, `collaborators?`, `status`, `startedAt`, `endedAt?`, `incrementPlayCounts`, `trackCount = 0`) (`[LiveSession.swift:LiveSession]`), with computed `isLive` (`[LiveSession.swift:LiveSession.isLive]`), `formattedStartDate` (`[LiveSession.swift:LiveSession.formattedStartDate]`), and `durationFormatted` (`[LiveSession.swift:LiveSession.durationFormatted]`).
- `struct LiveSessionTrack: Identifiable, Hashable, Codable` — Played track entry within a session (`id`, `sessionId`, `trackId`, `trackNo`, `playedAt`, `track?`) (`[LiveSession.swift:LiveSessionTrack]`), with `formattedTime` (`[LiveSession.swift:LiveSessionTrack.formattedTime]`) and `shortTime` (`[LiveSession.swift:LiveSessionTrack.shortTime]`).
- `struct Playlist: Identifiable, Hashable, Codable` — Rekordbox playlist or folder (`id`, `name`, `parentId?`, `seq`, `isFolder`, `attribute`, `updatedAt`) (`[Playlist.swift:Playlist]`), with `isSmart` (`[Playlist.swift:Playlist.isSmart]`).
- `struct PlaylistTrack: Identifiable, Hashable, Codable` — Ordered join record linking `playlistId`, `trackId`, and `trackNo` (`[Playlist.swift:PlaylistTrack]`).
- `struct Tag: Identifiable, Hashable, Codable` — MyTag category/tag node (`id`, `name`, `parentId?`, `seq`, `updatedAt`) (`[Tag.swift:Tag]`).
- `struct TrackTag: Identifiable, Hashable, Codable` — Join record linking `trackId` and `tagId` (`[Tag.swift:TrackTag]`).
- `struct Track: Identifiable, Hashable, Codable` — Track entity (`id`, `title`, `artist`, `album`, `genre`, `bpm`, `keyName`, `rating`, `comment`, `colorId`, `djPlayCount`, `dateAdded`, `updatedAt`) (`[Track.swift:Track]`), with `formattedBpm` (`[Track.swift:Track.formattedBpm]`), `formattedDateAdded` (`[Track.swift:Track.formattedDateAdded]`), and `isStreaming` (`[Track.swift:Track.isStreaming]`).

## Invariants
- All 8 model structs use synthesized `Codable` without custom `CodingKeys` or `init(from:)`, requiring camelCase keys matching Swift property names for every non-optional field (including `LiveSession.trackCount` despite its `= 0` default) (`[ChangelogEvent.swift:ChangelogEvent]`, `[LiveSession.swift:LiveSession]`, `[LiveSession.swift:LiveSessionTrack]`, `[Playlist.swift:Playlist]`, `[Playlist.swift:PlaylistTrack]`, `[Tag.swift:Tag]`, `[Tag.swift:TrackTag]`, `[Track.swift:Track]`).
- `ChangelogEvent` status checks compare `status.uppercased()` against `"PENDING"`, `"PROCESSED"`, and `"FAILED"`, and `statusColor` defaults to `.orange` for any status that is neither processed nor failed (`[ChangelogEvent.swift:ChangelogEvent.statusColor]`).
- `LiveSession.status` is constrained to `LiveSessionStatus` raw values `"ACTIVE"`, `"COMPLETED"`, or `"DISCARDED"` (`[LiveSession.swift:LiveSessionStatus]`).
- `LiveSession.durationFormatted` clamps elapsed seconds to `>= 0` via `max(0, Int(end.timeIntervalSince(startedAt)))` (`[LiveSession.swift:LiveSession.durationFormatted]`).
- `Playlist.isSmart` identifies smart playlists strictly by `attribute == 4` (`[Playlist.swift:Playlist.isSmart]`).
- `Track.formattedBpm` treats `bpm <= 0` as missing and returns `"-"` (`[Track.swift:Track.formattedBpm]`).
- `Track.isStreaming` classifies a track as streaming if `id` matches hardcoded IDs (`"14939874"`, `"218663293"`, `"152484359"`, `"61897480"`, `"65048245"`), trimmed/lowercased `title` is empty or `"(untitled)"`, or trimmed/lowercased `title`, `artist`, or `comment` contains `"spotify"`, `"apple-music"`, `"beatport"`, `"beatsource"`, `"soundcloud"`, or `"tidal"` (`[Track.swift:Track.isStreaming]`).

## Side Effects
- None

## Verification
- Run: `<test-command>` — No XCTest target exists in `RekordboxCompanion.xcodeproj` (only the `RekordboxCompanion` application target is defined); verify compilation in Xcode or via `xcodebuild -project ios/RekordboxCompanion/RekordboxCompanion.xcodeproj -scheme RekordboxCompanion build`.
