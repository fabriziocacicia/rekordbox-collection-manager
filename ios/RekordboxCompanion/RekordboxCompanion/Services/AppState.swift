import Foundation
import SwiftUI

@Observable
final class AppState {
    var tracks: [Track] = []
    var playlists: [Playlist] = []
    var tags: [Tag] = []
    var changelogEvents: [ChangelogEvent] = []

    // Live Session Tracking
    var activeSession: LiveSession?
    var activeSessionTracks: [LiveSessionTrack] = []
    var recentSessions: [LiveSession] = []
    var pastLocations: [String] = []
    var pastCollaborators: [String] = []

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

        activeSession = LocalDatabase.shared.getActiveLiveSession()
        if let current = activeSession {
            activeSessionTracks = LocalDatabase.shared.getLiveSessionTracks(sessionId: current.id)
        } else {
            activeSessionTracks = []
        }
        recentSessions = LocalDatabase.shared.getRecentLiveSessions(limit: 10)
        pastLocations = LocalDatabase.shared.getPastLocations()
        pastCollaborators = LocalDatabase.shared.getPastCollaborators()
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

            // 3. Pull latest event statuses from Turso
            try await TursoSyncService.shared.pullEventStatuses()

            // 4. Reload UI state from local database
            loadFromCache()
            lastSyncDate = Date()
        } catch {
            print("Sync failed: \(error)")
            syncErrorMessage = error.localizedDescription
        }

        isSyncing = false
    }

    private func triggerBackgroundPush() {
        Task {
            _ = try? await TursoSyncService.shared.pushPendingEvents()
            _ = try? await TursoSyncService.shared.pullEventStatuses()
            await MainActor.run {
                self.loadFromCache()
            }
        }
    }

    func deleteChangelogEvents(at offsets: IndexSet) {
        let idsToDelete = offsets.map { changelogEvents[$0].id }
        for id in idsToDelete {
            LocalDatabase.shared.deleteChangelogEvent(id: id)
        }
        loadFromCache()
    }

    func clearProcessedEvents() {
        LocalDatabase.shared.clearProcessedChangelogEvents()
        loadFromCache()
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
        triggerBackgroundPush()
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
        triggerBackgroundPush()
    }

    func reorderPlaylist(playlistId: String, trackIds: [String]) {
        LocalDatabase.shared.updatePlaylistTrackOrderLocally(playlistId: playlistId, trackIds: trackIds)
        let payload: [String: Any] = [
            "playlist_id": playlistId,
            "track_ids": trackIds,
        ]
        LocalDatabase.shared.addChangelogEvent(action: "UPDATE_PLAYLIST_ORDER", payload: payload)
        loadFromCache()
        triggerBackgroundPush()
    }

    func addTrackToPlaylist(trackId: String, playlistId: String) {
        LocalDatabase.shared.addTrackToPlaylistLocally(playlistId: playlistId, trackId: trackId)
        let payload: [String: Any] = [
            "track_id": trackId,
            "playlist_id": playlistId,
        ]
        LocalDatabase.shared.addChangelogEvent(action: "ADD_TO_PLAYLIST", payload: payload)
        loadFromCache()
        triggerBackgroundPush()
    }

    func removeTrackFromPlaylist(trackId: String, playlistId: String) {
        LocalDatabase.shared.removeTrackFromPlaylistLocally(playlistId: playlistId, trackId: trackId)
        let payload: [String: Any] = [
            "track_id": trackId,
            "playlist_id": playlistId,
        ]
        LocalDatabase.shared.addChangelogEvent(action: "REMOVE_FROM_PLAYLIST", payload: payload)
        loadFromCache()
        triggerBackgroundPush()
    }

    func createPlaylist(name: String, parentId: String?) {
        let _ = LocalDatabase.shared.createPlaylistLocally(name: name, parentId: parentId)
        var payload: [String: Any] = ["name": name]
        if let pid = parentId {
            payload["parent_id"] = pid
        }
        LocalDatabase.shared.addChangelogEvent(action: "CREATE_PLAYLIST", payload: payload)
        loadFromCache()
        triggerBackgroundPush()
    }

    // MARK: - Live Session Actions
    func startLiveSession(name: String, dateCreated: String, location: String?, collaborators: String?, startedAt: Date = Date()) {
        let session = LocalDatabase.shared.startLiveSession(
            name: name,
            dateCreated: dateCreated,
            location: location,
            collaborators: collaborators,
            startedAt: startedAt
        )
        activeSession = session
        activeSessionTracks = []
        loadFromCache()
    }

    func addTrackToActiveSession(track: Track) {
        guard let session = activeSession else { return }
        if let lt = LocalDatabase.shared.addTrackToLiveSession(sessionId: session.id, trackId: track.id, playedAt: Date()) {
            activeSessionTracks.append(lt)
            activeSession?.trackCount = activeSessionTracks.count
        }
    }

    func removeTrackFromActiveSession(liveTrackId: String) {
        guard let session = activeSession else { return }
        LocalDatabase.shared.removeTrackFromLiveSession(sessionId: session.id, liveTrackId: liveTrackId)
        activeSessionTracks = LocalDatabase.shared.getLiveSessionTracks(sessionId: session.id)
        activeSession?.trackCount = activeSessionTracks.count
    }

    func reorderActiveSession(orderedLiveTrackIds: [String]) {
        guard let session = activeSession else { return }
        LocalDatabase.shared.reorderLiveSessionTracks(sessionId: session.id, orderedLiveTrackIds: orderedLiveTrackIds)
        activeSessionTracks = LocalDatabase.shared.getLiveSessionTracks(sessionId: session.id)
    }

    func endActiveSession(incrementPlayCounts: Bool) {
        guard let session = activeSession else { return }
        LocalDatabase.shared.endLiveSession(sessionId: session.id, incrementPlayCounts: incrementPlayCounts)
        activeSession = nil
        activeSessionTracks = []
        loadFromCache()
        triggerBackgroundPush()
    }

    func discardActiveSession() {
        guard let session = activeSession else { return }
        LocalDatabase.shared.discardLiveSession(sessionId: session.id)
        activeSession = nil
        activeSessionTracks = []
        loadFromCache()
    }
}
