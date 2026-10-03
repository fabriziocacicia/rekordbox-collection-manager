import Foundation

final class TursoSyncService {
    static let shared = TursoSyncService()

    private init() {}

    private var endpointURL: URL? {
        var base = TursoConfig.databaseURL.trimmingCharacters(in: .whitespacesAndNewlines)
        if base.hasPrefix("libsql://") {
            base = "https://" + base.dropFirst("libsql://".count)
        }
        if base.hasSuffix("/") {
            base.removeLast()
        }
        return URL(string: "\(base)/v2/pipeline")
    }

    private func executePipeline(requests: [[String: Any]]) async throws -> [[String: Any]] {
        guard let url = endpointURL else {
            throw URLError(.badURL)
        }

        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("Bearer \(TursoConfig.authToken)", forHTTPHeaderField: "Authorization")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")

        var allRequests = requests
        allRequests.append(["type": "close"])

        let body = ["requests": allRequests]
        req.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, response) = try await URLSession.shared.data(for: req)
        guard let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode == 200 else {
            let msg = String(data: data, encoding: .utf8) ?? "Unknown HTTP error"
            throw NSError(domain: "TursoSyncService", code: (response as? HTTPURLResponse)?.statusCode ?? -1, userInfo: [NSLocalizedDescriptionKey: msg])
        }

        guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let results = json["results"] as? [[String: Any]] else {
            throw NSError(domain: "TursoSyncService", code: -2, userInfo: [NSLocalizedDescriptionKey: "Invalid Turso response payload"])
        }

        for item in results {
            if let type = item["type"] as? String, type == "error",
               let err = item["error"] as? [String: Any],
               let errMsg = err["message"] as? String {
                throw NSError(domain: "TursoSyncService", code: -3, userInfo: [NSLocalizedDescriptionKey: errMsg])
            }
        }

        return results
    }

    // MARK: - Pull Snapshot
    func pullLatestCollection() async throws -> (tracks: Int, playlists: Int, tags: Int) {
        let requests: [[String: Any]] = [
            ["type": "execute", "stmt": ["sql": "SELECT id, title, artist, album, genre, bpm, key_name, rating, comment, color_id, dj_play_count, date_added, updated_at FROM tracks;"]],
            ["type": "execute", "stmt": ["sql": "SELECT id, name, parent_id, seq, is_folder, attribute, updated_at FROM playlists;"]],
            ["type": "execute", "stmt": ["sql": "SELECT id, playlist_id, track_id, track_no FROM playlist_tracks;"]],
            ["type": "execute", "stmt": ["sql": "SELECT id, name, parent_id, seq, updated_at FROM tags;"]],
            ["type": "execute", "stmt": ["sql": "SELECT id, track_id, tag_id FROM track_tags;"]]
        ]

        let results = try await executePipeline(requests: requests)
        guard results.count >= 5 else {
            throw NSError(domain: "TursoSyncService", code: -4, userInfo: [NSLocalizedDescriptionKey: "Unexpected pipeline results count"])
        }

        // 1. Tracks
        var tracks: [Track] = []
        if let res = results[0]["response"] as? [String: Any],
           let result = res["result"] as? [String: Any],
           let rows = result["rows"] as? [[[String: Any]]] {
            for r in rows {
                guard r.count >= 13 else { continue }
                tracks.append(Track(
                    id: r[0]["value"] as? String ?? "",
                    title: r[1]["value"] as? String ?? "",
                    artist: r[2]["value"] as? String ?? "",
                    album: r[3]["value"] as? String ?? "",
                    genre: r[4]["value"] as? String ?? "",
                    bpm: (r[5]["value"] as? NSNumber)?.doubleValue ?? Double(r[5]["value"] as? String ?? "") ?? 0.0,
                    keyName: r[6]["value"] as? String ?? "",
                    rating: Int(r[7]["value"] as? String ?? "") ?? (r[7]["value"] as? Int ?? 0),
                    comment: r[8]["value"] as? String ?? "",
                    colorId: Int(r[9]["value"] as? String ?? "") ?? (r[9]["value"] as? Int ?? 0),
                    djPlayCount: Int(r[10]["value"] as? String ?? "") ?? (r[10]["value"] as? Int ?? 0),
                    dateAdded: r[11]["value"] as? String ?? "",
                    updatedAt: r[12]["value"] as? String ?? ""
                ))
            }
        }
        LocalDatabase.shared.upsertTracks(tracks)

        // 2. Playlists
        var playlists: [Playlist] = []
        if let res = results[1]["response"] as? [String: Any],
           let result = res["result"] as? [String: Any],
           let rows = result["rows"] as? [[[String: Any]]] {
            for r in rows {
                guard r.count >= 7 else { continue }
                let isFolderVal = Int(r[4]["value"] as? String ?? "") ?? (r[4]["value"] as? Int ?? 0)
                playlists.append(Playlist(
                    id: r[0]["value"] as? String ?? "",
                    name: r[1]["value"] as? String ?? "",
                    parentId: r[2]["value"] as? String,
                    seq: Int(r[3]["value"] as? String ?? "") ?? (r[3]["value"] as? Int ?? 0),
                    isFolder: isFolderVal == 1,
                    attribute: Int(r[5]["value"] as? String ?? "") ?? (r[5]["value"] as? Int ?? 0),
                    updatedAt: r[6]["value"] as? String ?? ""
                ))
            }
        }
        LocalDatabase.shared.upsertPlaylists(playlists)

        // 3. Playlist Tracks
        var ptList: [PlaylistTrack] = []
        if let res = results[2]["response"] as? [String: Any],
           let result = res["result"] as? [String: Any],
           let rows = result["rows"] as? [[[String: Any]]] {
            for r in rows {
                guard r.count >= 4 else { continue }
                let id = (r[0]["value"] as? String) ?? (r[0]["value"] as? NSNumber)?.stringValue ?? ""
                let plId = (r[1]["value"] as? String) ?? (r[1]["value"] as? NSNumber)?.stringValue ?? ""
                let trkId = (r[2]["value"] as? String) ?? (r[2]["value"] as? NSNumber)?.stringValue ?? ""
                let trkNo = Int(r[3]["value"] as? String ?? "") ?? (r[3]["value"] as? Int ?? (r[3]["value"] as? NSNumber)?.intValue ?? 0)
                guard !id.isEmpty, !plId.isEmpty, !trkId.isEmpty else { continue }
                ptList.append(PlaylistTrack(
                    id: id,
                    playlistId: plId,
                    trackId: trkId,
                    trackNo: trkNo
                ))
            }
        }
        LocalDatabase.shared.replacePlaylistTracks(ptList)

        // 4. Tags
        var tags: [Tag] = []
        if let res = results[3]["response"] as? [String: Any],
           let result = res["result"] as? [String: Any],
           let rows = result["rows"] as? [[[String: Any]]] {
            for r in rows {
                guard r.count >= 5 else { continue }
                tags.append(Tag(
                    id: r[0]["value"] as? String ?? "",
                    name: r[1]["value"] as? String ?? "",
                    parentId: r[2]["value"] as? String,
                    seq: Int(r[3]["value"] as? String ?? "") ?? (r[3]["value"] as? Int ?? 0),
                    updatedAt: r[4]["value"] as? String ?? ""
                ))
            }
        }
        LocalDatabase.shared.upsertTags(tags)

        // 5. Track Tags
        var ttList: [TrackTag] = []
        if let res = results[4]["response"] as? [String: Any],
           let result = res["result"] as? [String: Any],
           let rows = result["rows"] as? [[[String: Any]]] {
            for r in rows {
                guard r.count >= 3 else { continue }
                let id = (r[0]["value"] as? String) ?? (r[0]["value"] as? NSNumber)?.stringValue ?? ""
                let trkId = (r[1]["value"] as? String) ?? (r[1]["value"] as? NSNumber)?.stringValue ?? ""
                let tagId = (r[2]["value"] as? String) ?? (r[2]["value"] as? NSNumber)?.stringValue ?? ""
                guard !id.isEmpty, !trkId.isEmpty, !tagId.isEmpty else { continue }
                ttList.append(TrackTag(
                    id: id,
                    trackId: trkId,
                    tagId: tagId
                ))
            }
        }
        LocalDatabase.shared.replaceTrackTags(ttList)

        // 6. Safely Pull History Sessions & Event Statuses
        Task {
            _ = try? await self.pullHistorySessions()
            _ = try? await self.pullEventStatuses()
        }

        return (tracks.count, playlists.count, tags.count)
    }

    func pullHistorySessions() async throws {
        let req: [[String: Any]] = [
            ["type": "execute", "stmt": ["sql": "CREATE TABLE IF NOT EXISTS history_sessions (id TEXT PRIMARY KEY, name TEXT NOT NULL, date_created TEXT, location TEXT, collaborators TEXT, track_count INTEGER DEFAULT 0, updated_at TEXT);"]],
            ["type": "execute", "stmt": ["sql": "SELECT id, name, date_created, location, collaborators, track_count, updated_at FROM history_sessions;"]]
        ]
        let results = try await executePipeline(requests: req)
        guard results.count >= 2,
              let res = results[1]["response"] as? [String: Any],
              let result = res["result"] as? [String: Any],
              let rows = result["rows"] as? [[[String: Any]]] else { return }

        var historyList: [(id: String, name: String, dateCreated: String, location: String?, collaborators: String?, trackCount: Int, updatedAt: String)] = []
        for r in rows {
            guard r.count >= 7 else { continue }
            let id = r[0]["value"] as? String ?? ""
            let name = r[1]["value"] as? String ?? ""
            let dateCreated = r[2]["value"] as? String ?? ""
            let location = r[3]["value"] as? String
            let collaborators = r[4]["value"] as? String
            let trackCount = Int(r[5]["value"] as? String ?? "") ?? (r[5]["value"] as? Int ?? 0)
            let updatedAt = r[6]["value"] as? String ?? ""
            historyList.append((id: id, name: name, dateCreated: dateCreated, location: location, collaborators: collaborators, trackCount: trackCount, updatedAt: updatedAt))
        }
        if !historyList.isEmpty {
            LocalDatabase.shared.upsertHistorySessions(historyList)
        }
    }

    // MARK: - Push Pending Events
    func pushPendingEvents() async throws -> Int {
        let pending = LocalDatabase.shared.getPendingChangelogEvents()
        if pending.isEmpty { return 0 }

        var requests: [[String: Any]] = []
        for event in pending {
            requests.append([
                "type": "execute",
                "stmt": [
                    "sql": "INSERT OR IGNORE INTO changelog_events (id, action, payload, status, created_at) VALUES (?, ?, ?, 'PENDING', ?);",
                    "args": [
                        ["type": "text", "value": event.id],
                        ["type": "text", "value": event.action],
                        ["type": "text", "value": event.payload],
                        ["type": "text", "value": event.createdAt]
                    ]
                ]
            ])
        }

        _ = try await executePipeline(requests: requests)
        return pending.count
    }

    // MARK: - Pull Changelog Event Statuses
    func pullEventStatuses() async throws {
        let requests: [[String: Any]] = [
            ["type": "execute", "stmt": ["sql": "SELECT id, action, payload, status, created_at, processed_at, error_message FROM changelog_events ORDER BY created_at DESC LIMIT 50;"]]
        ]
        let results = try await executePipeline(requests: requests)
        guard let res = results.first?["response"] as? [String: Any],
              let result = res["result"] as? [String: Any],
              let rows = result["rows"] as? [[[String: Any]]] else { return }

        for r in rows {
            guard r.count >= 7 else { continue }
            let id = r[0]["value"] as? String ?? ""
            let action = r[1]["value"] as? String ?? ""
            let payload = r[2]["value"] as? String ?? ""
            let status = r[3]["value"] as? String ?? ""
            let createdAt = r[4]["value"] as? String ?? ""
            let processedAt = r[5]["value"] as? String
            let errorMsg = r[6]["value"] as? String

            guard !id.isEmpty, !status.isEmpty else { continue }
            LocalDatabase.shared.upsertChangelogEvent(
                id: id,
                action: action,
                payload: payload,
                status: status,
                createdAt: createdAt,
                processedAt: processedAt,
                errorMessage: errorMsg
            )
        }
    }

    // MARK: - Fetch Status
    func fetchRemoteQueueStatus() async throws -> (pending: Int, processed: Int, failed: Int) {
        let requests: [[String: Any]] = [
            ["type": "execute", "stmt": ["sql": "SELECT COUNT(*) FROM changelog_events WHERE status = 'PENDING';"]],
            ["type": "execute", "stmt": ["sql": "SELECT COUNT(*) FROM changelog_events WHERE status = 'PROCESSED';"]],
            ["type": "execute", "stmt": ["sql": "SELECT COUNT(*) FROM changelog_events WHERE status = 'FAILED';"]]
        ]
        let results = try await executePipeline(requests: requests)

        func extractCount(_ item: [String: Any]) -> Int {
            guard let res = item["response"] as? [String: Any],
                  let result = res["result"] as? [String: Any],
                  let rows = result["rows"] as? [[[String: Any]]],
                  let firstRow = rows.first,
                  let firstCol = firstRow.first,
                  let val = firstCol["value"] else { return 0 }
            if let num = val as? Int { return num }
            if let str = val as? String { return Int(str) ?? 0 }
            return 0
        }

        let p = extractCount(results[0])
        let pr = extractCount(results[1])
        let f = extractCount(results[2])
        return (p, pr, f)
    }
}
