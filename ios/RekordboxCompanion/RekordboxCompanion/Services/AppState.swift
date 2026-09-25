import Foundation
import SwiftUI

@Observable
final class AppState {
    var tracks: [Track] = []
    var playlists: [Playlist] = []
    var tags: [Tag] = []
    var changelogEvents: [ChangelogEvent] = []

    var isSyncing: Bool = false
    var lastSyncDate: Date?
    var syncErrorMessage: String?

    var pendingCount: Int {
        changelogEvents.filter { $0.isPending }.count
    }

    init() {
        loadFromCache()
        Task {
            await sync()
        }
    }

    func loadFromCache() {
        tracks = LocalDatabase.shared.getAllTracks()
        playlists = LocalDatabase.shared.getAllPlaylists()
        tags = LocalDatabase.shared.getAllTags()
        changelogEvents = LocalDatabase.shared.getAllChangelogEvents()
    }

    @MainActor
    func sync() async {
        guard !isSyncing else { return }
        isSyncing = true
        syncErrorMessage = nil

        do {
            // 1. Push any pending local events to Turso
            _ = try await TursoSyncService.shared.pushPendingEvents()

            // 2. Pull fresh collection from Turso
            _ = try await TursoSyncService.shared.pullLatestCollection()

            // 3. Reload UI state from local database
            loadFromCache()
            lastSyncDate = Date()
        } catch {
            print("Sync failed: \(error)")
            syncErrorMessage = error.localizedDescription
        }

        isSyncing = false
    }

    // MARK: - User Actions (Optimistic Update + Changelog Event)
    func updateTrackMetadata(track: Track, title: String, comment: String, rating: Int, djPlayCount: Int, tagIds: [String]) {
        // 1. Optimistic Local Update
        LocalDatabase.shared.updateTrackLocally(trackId: track.id, title: title, comment: comment, rating: rating, djPlayCount: djPlayCount)
        LocalDatabase.shared.setTrackTagsLocally(trackId: track.id, tagIds: tagIds)

        // 2. Add Changelog Event
        let payload: [String: Any] = [
            "track_id": track.id,
            "title": title,
            "comment": comment,
            "rating": rating,
            "dj_play_count": max(0, djPlayCount),
            "tag_ids": tagIds,
        ]
        LocalDatabase.shared.addChangelogEvent(action: "UPDATE_TRACK", payload: payload)

        // 3. Reload cache
        loadFromCache()

        // 4. Background push
        Task {
            _ = try? await TursoSyncService.shared.pushPendingEvents()
        }
    }

    func adjustPlayCount(track: Track, delta: Int) {
        let newCount = max(0, track.djPlayCount + delta)
        LocalDatabase.shared.updatePlayCountLocally(trackId: track.id, newCount: newCount)
        let payload: [String: Any] = [
            "track_id": track.id,
            "count": newCount,
        ]
        LocalDatabase.shared.addChangelogEvent(action: "UPDATE_PLAY_COUNT", payload: payload)
        loadFromCache()
        Task {
            _ = try? await TursoSyncService.shared.pushPendingEvents()
        }
    }

    func reorderPlaylist(playlistId: String, trackIds: [String]) {
        LocalDatabase.shared.updatePlaylistTrackOrderLocally(playlistId: playlistId, trackIds: trackIds)
        let payload: [String: Any] = [
            "playlist_id": playlistId,
            "track_ids": trackIds,
        ]
        LocalDatabase.shared.addChangelogEvent(action: "UPDATE_PLAYLIST_ORDER", payload: payload)
        loadFromCache()
        Task {
            _ = try? await TursoSyncService.shared.pushPendingEvents()
        }
    }

    func addTrackToPlaylist(trackId: String, playlistId: String) {
        LocalDatabase.shared.addTrackToPlaylistLocally(playlistId: playlistId, trackId: trackId)
        let payload: [String: Any] = [
            "track_id": trackId,
            "playlist_id": playlistId,
        ]
        LocalDatabase.shared.addChangelogEvent(action: "ADD_TO_PLAYLIST", payload: payload)
        loadFromCache()
        Task {
            _ = try? await TursoSyncService.shared.pushPendingEvents()
        }
    }

    func removeTrackFromPlaylist(trackId: String, playlistId: String) {
        LocalDatabase.shared.removeTrackFromPlaylistLocally(playlistId: playlistId, trackId: trackId)
        let payload: [String: Any] = [
            "track_id": trackId,
            "playlist_id": playlistId,
        ]
        LocalDatabase.shared.addChangelogEvent(action: "REMOVE_FROM_PLAYLIST", payload: payload)
        loadFromCache()
        Task {
            _ = try? await TursoSyncService.shared.pushPendingEvents()
        }
    }

    func createPlaylist(name: String, parentId: String?) {
        let _ = LocalDatabase.shared.createPlaylistLocally(name: name, parentId: parentId)
        var payload: [String: Any] = ["name": name]
        if let pid = parentId {
            payload["parent_id"] = pid
        }
        LocalDatabase.shared.addChangelogEvent(action: "CREATE_PLAYLIST", payload: payload)
        loadFromCache()
        Task {
            _ = try? await TursoSyncService.shared.pushPendingEvents()
        }
    }
}
