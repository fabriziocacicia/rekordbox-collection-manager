import Foundation
import SQLite3

final class LocalDatabase {
    static let shared = LocalDatabase()
    private var db: OpaquePointer?

    private init() {
        openDatabase()
        createTables()
        pruneStreamingTracks()
    }

    func pruneStreamingTracks() {
        let sql = """
        DELETE FROM tracks WHERE title = '' OR title = '(untitled)' OR title LIKE '%spotify%' OR artist LIKE '%spotify%' OR title LIKE '%apple-music%' OR artist LIKE '%apple-music%' OR id IN ('14939874', '218663293', '152484359', '61897480', '65048245');
        DELETE FROM playlist_tracks WHERE track_id IN ('14939874', '218663293', '152484359', '61897480', '65048245');
        DELETE FROM track_tags WHERE track_id IN ('14939874', '218663293', '152484359', '61897480', '65048245');
        """
        sqlite3_exec(db, sql, nil, nil, nil)
    }

    deinit {
        if db != nil {
            sqlite3_close(db)
        }
    }

    private func openDatabase() {
        let fileManager = FileManager.default
        guard let docsUrl = fileManager.urls(for: .documentDirectory, in: .userDomainMask).first else {
            fatalError("Could not find documents directory")
        }
        let dbUrl = docsUrl.appendingPathComponent("rekordbox_companion.db")
        if sqlite3_open(dbUrl.path, &db) != SQLITE_OK {
            print("Error opening local sqlite database at: \(dbUrl.path)")
        } else {
            print("Local database ready at: \(dbUrl.path)")
        }
    }

    private func createTables() {
        let sql = """
        CREATE TABLE IF NOT EXISTS tracks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            artist TEXT,
            album TEXT,
            genre TEXT,
            bpm REAL,
            key_name TEXT,
            rating INTEGER DEFAULT 0,
            comment TEXT,
            color_id INTEGER DEFAULT 0,
            dj_play_count INTEGER DEFAULT 0,
            date_added TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS playlists (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            parent_id TEXT,
            seq INTEGER DEFAULT 0,
            is_folder INTEGER DEFAULT 0,
            attribute INTEGER DEFAULT 0,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS playlist_tracks (
            id TEXT PRIMARY KEY,
            playlist_id TEXT NOT NULL,
            track_id TEXT NOT NULL,
            track_no INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS tags (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            parent_id TEXT,
            seq INTEGER DEFAULT 0,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS track_tags (
            id TEXT PRIMARY KEY,
            track_id TEXT NOT NULL,
            tag_id TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS changelog_events (
            id TEXT PRIMARY KEY,
            action TEXT NOT NULL,
            payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            created_at TEXT NOT NULL,
            processed_at TEXT,
            error_message TEXT
        );
        """
        sqlite3_exec(db, sql, nil, nil, nil)
    }

    // MARK: - Upsert Functions
    func upsertTracks(_ tracks: [Track]) {
        let nonStreaming = tracks.filter { !$0.isStreaming }
        let sql = """
        INSERT OR REPLACE INTO tracks 
        (id, title, artist, album, genre, bpm, key_name, rating, comment, color_id, dj_play_count, date_added, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return }
        sqlite3_exec(db, "BEGIN TRANSACTION;", nil, nil, nil)
        for t in nonStreaming {
            sqlite3_bind_text(stmt, 1, (t.id as NSString).utf8String, -1, nil)
            sqlite3_bind_text(stmt, 2, (t.title as NSString).utf8String, -1, nil)
            sqlite3_bind_text(stmt, 3, (t.artist as NSString).utf8String, -1, nil)
            sqlite3_bind_text(stmt, 4, (t.album as NSString).utf8String, -1, nil)
            sqlite3_bind_text(stmt, 5, (t.genre as NSString).utf8String, -1, nil)
            sqlite3_bind_double(stmt, 6, t.bpm)
            sqlite3_bind_text(stmt, 7, (t.keyName as NSString).utf8String, -1, nil)
            sqlite3_bind_int(stmt, 8, Int32(t.rating))
            sqlite3_bind_text(stmt, 9, (t.comment as NSString).utf8String, -1, nil)
            sqlite3_bind_int(stmt, 10, Int32(t.colorId))
            sqlite3_bind_int(stmt, 11, Int32(max(0, t.djPlayCount)))
            sqlite3_bind_text(stmt, 12, (t.dateAdded as NSString).utf8String, -1, nil)
            sqlite3_bind_text(stmt, 13, (t.updatedAt as NSString).utf8String, -1, nil)
            sqlite3_step(stmt)
            sqlite3_reset(stmt)
        }
        sqlite3_exec(db, "COMMIT;", nil, nil, nil)
        sqlite3_finalize(stmt)
    }

    func upsertPlaylists(_ playlists: [Playlist]) {
        let sql = "INSERT OR REPLACE INTO playlists (id, name, parent_id, seq, is_folder, attribute, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?);"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return }
        sqlite3_exec(db, "BEGIN TRANSACTION;", nil, nil, nil)
        for p in playlists {
            sqlite3_bind_text(stmt, 1, (p.id as NSString).utf8String, -1, nil)
            sqlite3_bind_text(stmt, 2, (p.name as NSString).utf8String, -1, nil)
            if let pid = p.parentId {
                sqlite3_bind_text(stmt, 3, (pid as NSString).utf8String, -1, nil)
            } else {
                sqlite3_bind_null(stmt, 3)
            }
            sqlite3_bind_int(stmt, 4, Int32(p.seq))
            sqlite3_bind_int(stmt, 5, p.isFolder ? 1 : 0)
            sqlite3_bind_int(stmt, 6, Int32(p.attribute))
            sqlite3_bind_text(stmt, 7, (p.updatedAt as NSString).utf8String, -1, nil)
            sqlite3_step(stmt)
            sqlite3_reset(stmt)
        }
        sqlite3_exec(db, "COMMIT;", nil, nil, nil)
        sqlite3_finalize(stmt)
    }

    func upsertPlaylistTracks(_ ptList: [PlaylistTrack]) {
        let sql = "INSERT OR REPLACE INTO playlist_tracks (id, playlist_id, track_id, track_no) VALUES (?, ?, ?, ?);"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return }
        sqlite3_exec(db, "BEGIN TRANSACTION;", nil, nil, nil)
        for pt in ptList {
            sqlite3_bind_text(stmt, 1, (pt.id as NSString).utf8String, -1, nil)
            sqlite3_bind_text(stmt, 2, (pt.playlistId as NSString).utf8String, -1, nil)
            sqlite3_bind_text(stmt, 3, (pt.trackId as NSString).utf8String, -1, nil)
            sqlite3_bind_int(stmt, 4, Int32(pt.trackNo))
            sqlite3_step(stmt)
            sqlite3_reset(stmt)
        }
        sqlite3_exec(db, "COMMIT;", nil, nil, nil)
        sqlite3_finalize(stmt)
    }

    func replacePlaylistTracks(_ ptList: [PlaylistTrack]) {
        let sql = "INSERT INTO playlist_tracks (id, playlist_id, track_id, track_no) VALUES (?, ?, ?, ?);"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return }
        sqlite3_exec(db, "BEGIN TRANSACTION;", nil, nil, nil)
        sqlite3_exec(db, "DELETE FROM playlist_tracks;", nil, nil, nil)
        for pt in ptList {
            sqlite3_bind_text(stmt, 1, (pt.id as NSString).utf8String, -1, nil)
            sqlite3_bind_text(stmt, 2, (pt.playlistId as NSString).utf8String, -1, nil)
            sqlite3_bind_text(stmt, 3, (pt.trackId as NSString).utf8String, -1, nil)
            sqlite3_bind_int(stmt, 4, Int32(pt.trackNo))
            sqlite3_step(stmt)
            sqlite3_reset(stmt)
        }
        sqlite3_exec(db, "COMMIT;", nil, nil, nil)
        sqlite3_finalize(stmt)
    }

    func upsertTags(_ tags: [Tag]) {
        let sql = "INSERT OR REPLACE INTO tags (id, name, parent_id, seq, updated_at) VALUES (?, ?, ?, ?, ?);"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return }
        sqlite3_exec(db, "BEGIN TRANSACTION;", nil, nil, nil)
        for t in tags {
            sqlite3_bind_text(stmt, 1, (t.id as NSString).utf8String, -1, nil)
            sqlite3_bind_text(stmt, 2, (t.name as NSString).utf8String, -1, nil)
            if let pid = t.parentId {
                sqlite3_bind_text(stmt, 3, (pid as NSString).utf8String, -1, nil)
            } else {
                sqlite3_bind_null(stmt, 3)
            }
            sqlite3_bind_int(stmt, 4, Int32(t.seq))
            sqlite3_bind_text(stmt, 5, (t.updatedAt as NSString).utf8String, -1, nil)
            sqlite3_step(stmt)
            sqlite3_reset(stmt)
        }
        sqlite3_exec(db, "COMMIT;", nil, nil, nil)
        sqlite3_finalize(stmt)
    }

    func upsertTrackTags(_ ttList: [TrackTag]) {
        let sql = "INSERT OR REPLACE INTO track_tags (id, track_id, tag_id) VALUES (?, ?, ?);"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return }
        sqlite3_exec(db, "BEGIN TRANSACTION;", nil, nil, nil)
        for tt in ttList {
            sqlite3_bind_text(stmt, 1, (tt.id as NSString).utf8String, -1, nil)
            sqlite3_bind_text(stmt, 2, (tt.trackId as NSString).utf8String, -1, nil)
            sqlite3_bind_text(stmt, 3, (tt.tagId as NSString).utf8String, -1, nil)
            sqlite3_step(stmt)
            sqlite3_reset(stmt)
        }
        sqlite3_exec(db, "COMMIT;", nil, nil, nil)
        sqlite3_finalize(stmt)
    }

    func replaceTrackTags(_ ttList: [TrackTag]) {
        let sql = "INSERT INTO track_tags (id, track_id, tag_id) VALUES (?, ?, ?);"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return }
        sqlite3_exec(db, "BEGIN TRANSACTION;", nil, nil, nil)
        sqlite3_exec(db, "DELETE FROM track_tags;", nil, nil, nil)
        for tt in ttList {
            sqlite3_bind_text(stmt, 1, (tt.id as NSString).utf8String, -1, nil)
            sqlite3_bind_text(stmt, 2, (tt.trackId as NSString).utf8String, -1, nil)
            sqlite3_bind_text(stmt, 3, (tt.tagId as NSString).utf8String, -1, nil)
            sqlite3_step(stmt)
            sqlite3_reset(stmt)
        }
        sqlite3_exec(db, "COMMIT;", nil, nil, nil)
        sqlite3_finalize(stmt)
    }

    // MARK: - Query Functions
    func getAllTracks() -> [Track] {
        var list: [Track] = []
        let sql = """
        SELECT id, title, artist, album, genre, bpm, key_name, rating, comment, color_id, dj_play_count, date_added, updated_at 
        FROM tracks 
        WHERE title != '' AND title != '(untitled)' 
          AND title NOT LIKE '%spotify%' AND artist NOT LIKE '%spotify%'
          AND title NOT LIKE '%apple-music%' AND artist NOT LIKE '%apple-music%'
          AND id NOT IN ('14939874', '218663293', '152484359', '61897480', '65048245')
        ORDER BY title COLLATE NOCASE, artist COLLATE NOCASE;
        """
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }
        while sqlite3_step(stmt) == SQLITE_ROW {
            let id = String(cString: sqlite3_column_text(stmt, 0))
            let title = String(cString: sqlite3_column_text(stmt, 1))
            let artist = sqlite3_column_text(stmt, 2).map { String(cString: $0) } ?? ""
            let album = sqlite3_column_text(stmt, 3).map { String(cString: $0) } ?? ""
            let genre = sqlite3_column_text(stmt, 4).map { String(cString: $0) } ?? ""
            let bpm = sqlite3_column_double(stmt, 5)
            let key = sqlite3_column_text(stmt, 6).map { String(cString: $0) } ?? ""
            let rating = Int(sqlite3_column_int(stmt, 7))
            let comment = sqlite3_column_text(stmt, 8).map { String(cString: $0) } ?? ""
            let colorId = Int(sqlite3_column_int(stmt, 9))
            let playCount = max(0, Int(sqlite3_column_int(stmt, 10)))
            let dateAdded = sqlite3_column_text(stmt, 11).map { String(cString: $0) } ?? ""
            let updatedAt = sqlite3_column_text(stmt, 12).map { String(cString: $0) } ?? ""

            let track = Track(
                id: id,
                title: title,
                artist: artist,
                album: album,
                genre: genre,
                bpm: bpm,
                keyName: key,
                rating: rating,
                comment: comment,
                colorId: colorId,
                djPlayCount: playCount,
                dateAdded: dateAdded,
                updatedAt: updatedAt
            )
            if !track.isStreaming {
                list.append(track)
            }
        }
        sqlite3_finalize(stmt)
        return list
    }

    func getAllPlaylists() -> [Playlist] {
        var list: [Playlist] = []
        let sql = "SELECT id, name, parent_id, seq, is_folder, attribute, updated_at FROM playlists ORDER BY is_folder DESC, seq ASC, name COLLATE NOCASE;"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }
        while sqlite3_step(stmt) == SQLITE_ROW {
            let id = String(cString: sqlite3_column_text(stmt, 0))
            let name = String(cString: sqlite3_column_text(stmt, 1))
            let parentId = sqlite3_column_text(stmt, 2).map { String(cString: $0) }
            let seq = Int(sqlite3_column_int(stmt, 3))
            let isFolder = sqlite3_column_int(stmt, 4) == 1
            let attribute = Int(sqlite3_column_int(stmt, 5))
            let updatedAt = sqlite3_column_text(stmt, 6).map { String(cString: $0) } ?? ""

            list.append(Playlist(id: id, name: name, parentId: parentId, seq: seq, isFolder: isFolder, attribute: attribute, updatedAt: updatedAt))
        }
        sqlite3_finalize(stmt)
        return list
    }

    func getTracksForPlaylist(playlistId: String) -> [Track] {
        var list: [Track] = []
        let sql = """
        SELECT t.id, t.title, t.artist, t.album, t.genre, t.bpm, t.key_name, t.rating, t.comment, t.color_id, t.dj_play_count, t.date_added, t.updated_at
        FROM playlist_tracks pt
        JOIN tracks t ON pt.track_id = t.id
        WHERE pt.playlist_id = ?
          AND t.title != '' AND t.title != '(untitled)'
          AND t.title NOT LIKE '%spotify%' AND t.artist NOT LIKE '%spotify%'
          AND t.title NOT LIKE '%apple-music%' AND t.artist NOT LIKE '%apple-music%'
          AND t.id NOT IN ('14939874', '218663293', '152484359', '61897480', '65048245')
        ORDER BY pt.track_no ASC;
        """
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }
        sqlite3_bind_text(stmt, 1, (playlistId as NSString).utf8String, -1, nil)
        while sqlite3_step(stmt) == SQLITE_ROW {
            let id = String(cString: sqlite3_column_text(stmt, 0))
            let title = String(cString: sqlite3_column_text(stmt, 1))
            let artist = sqlite3_column_text(stmt, 2).map { String(cString: $0) } ?? ""
            let album = sqlite3_column_text(stmt, 3).map { String(cString: $0) } ?? ""
            let genre = sqlite3_column_text(stmt, 4).map { String(cString: $0) } ?? ""
            let bpm = sqlite3_column_double(stmt, 5)
            let key = sqlite3_column_text(stmt, 6).map { String(cString: $0) } ?? ""
            let rating = Int(sqlite3_column_int(stmt, 7))
            let comment = sqlite3_column_text(stmt, 8).map { String(cString: $0) } ?? ""
            let colorId = Int(sqlite3_column_int(stmt, 9))
            let playCount = max(0, Int(sqlite3_column_int(stmt, 10)))
            let dateAdded = sqlite3_column_text(stmt, 11).map { String(cString: $0) } ?? ""
            let updatedAt = sqlite3_column_text(stmt, 12).map { String(cString: $0) } ?? ""

            let track = Track(id: id, title: title, artist: artist, album: album, genre: genre, bpm: bpm, keyName: key, rating: rating, comment: comment, colorId: colorId, djPlayCount: playCount, dateAdded: dateAdded, updatedAt: updatedAt)
            if !track.isStreaming {
                list.append(track)
            }
        }
        sqlite3_finalize(stmt)
        return list
    }

    func getTrackCountForPlaylist(playlistId: String) -> Int {
        let sql = """
        SELECT COUNT(*)
        FROM playlist_tracks pt
        JOIN tracks t ON pt.track_id = t.id
        WHERE pt.playlist_id = ?
          AND t.title != '' AND t.title != '(untitled)'
          AND t.title NOT LIKE '%spotify%' AND t.artist NOT LIKE '%spotify%'
          AND t.title NOT LIKE '%apple-music%' AND t.artist NOT LIKE '%apple-music%'
          AND t.id NOT IN ('14939874', '218663293', '152484359', '61897480', '65048245');
        """
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return 0 }
        sqlite3_bind_text(stmt, 1, (playlistId as NSString).utf8String, -1, nil)
        var count = 0
        if sqlite3_step(stmt) == SQLITE_ROW {
            count = Int(sqlite3_column_int(stmt, 0))
        }
        sqlite3_finalize(stmt)
        return count
    }

    func getAllTags() -> [Tag] {
        var list: [Tag] = []
        let sql = "SELECT id, name, parent_id, seq, updated_at FROM tags ORDER BY seq ASC, name COLLATE NOCASE;"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }
        while sqlite3_step(stmt) == SQLITE_ROW {
            let id = String(cString: sqlite3_column_text(stmt, 0))
            let name = String(cString: sqlite3_column_text(stmt, 1))
            let parentId = sqlite3_column_text(stmt, 2).map { String(cString: $0) }
            let seq = Int(sqlite3_column_int(stmt, 3))
            let updatedAt = sqlite3_column_text(stmt, 4).map { String(cString: $0) } ?? ""
            list.append(Tag(id: id, name: name, parentId: parentId, seq: seq, updatedAt: updatedAt))
        }
        sqlite3_finalize(stmt)
        return list
    }

    func getTags(forParentId parentId: String) -> [Tag] {
        var list: [Tag] = []
        let sql = "SELECT id, name, parent_id, seq, updated_at FROM tags WHERE parent_id = ? ORDER BY seq ASC, name COLLATE NOCASE;"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }
        sqlite3_bind_text(stmt, 1, (parentId as NSString).utf8String, -1, nil)
        while sqlite3_step(stmt) == SQLITE_ROW {
            let id = String(cString: sqlite3_column_text(stmt, 0))
            let name = String(cString: sqlite3_column_text(stmt, 1))
            let pid = sqlite3_column_text(stmt, 2).map { String(cString: $0) }
            let seq = Int(sqlite3_column_int(stmt, 3))
            let updatedAt = sqlite3_column_text(stmt, 4).map { String(cString: $0) } ?? ""
            list.append(Tag(id: id, name: name, parentId: pid, seq: seq, updatedAt: updatedAt))
        }
        sqlite3_finalize(stmt)
        return list
    }

    func getTrackCountForTag(tagId: String) -> Int {
        let sql = """
        SELECT COUNT(*)
        FROM track_tags tt
        JOIN tracks t ON tt.track_id = t.id
        WHERE tt.tag_id = ?
          AND t.title != '' AND t.title != '(untitled)'
          AND t.title NOT LIKE '%spotify%' AND t.artist NOT LIKE '%spotify%'
          AND t.title NOT LIKE '%apple-music%' AND t.artist NOT LIKE '%apple-music%'
          AND t.id NOT IN ('14939874', '218663293', '152484359', '61897480', '65048245');
        """
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return 0 }
        sqlite3_bind_text(stmt, 1, (tagId as NSString).utf8String, -1, nil)
        var count = 0
        if sqlite3_step(stmt) == SQLITE_ROW {
            count = Int(sqlite3_column_int(stmt, 0))
        }
        sqlite3_finalize(stmt)
        return count
    }

    func getTrackIdsForTag(tagId: String) -> Set<String> {
        var set: Set<String> = []
        let sql = "SELECT track_id FROM track_tags WHERE tag_id = ?;"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }
        sqlite3_bind_text(stmt, 1, (tagId as NSString).utf8String, -1, nil)
        while sqlite3_step(stmt) == SQLITE_ROW {
            let trkId = String(cString: sqlite3_column_text(stmt, 0))
            set.insert(trkId)
        }
        sqlite3_finalize(stmt)
        return set
    }

    func getTrackIdsForPlaylist(playlistId: String) -> Set<String> {
        var set: Set<String> = []
        let sql = "SELECT track_id FROM playlist_tracks WHERE playlist_id = ?;"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }
        sqlite3_bind_text(stmt, 1, (playlistId as NSString).utf8String, -1, nil)
        while sqlite3_step(stmt) == SQLITE_ROW {
            let trkId = String(cString: sqlite3_column_text(stmt, 0))
            set.insert(trkId)
        }
        sqlite3_finalize(stmt)
        return set
    }

    func getTracksForTag(tagId: String) -> [Track] {
        var list: [Track] = []
        let sql = """
        SELECT t.id, t.title, t.artist, t.album, t.genre, t.bpm, t.key_name, t.rating, t.comment, t.color_id, t.dj_play_count, t.date_added, t.updated_at
        FROM track_tags tt
        JOIN tracks t ON tt.track_id = t.id
        WHERE tt.tag_id = ?
          AND t.title != '' AND t.title != '(untitled)'
          AND t.title NOT LIKE '%spotify%' AND t.artist NOT LIKE '%spotify%'
          AND t.title NOT LIKE '%apple-music%' AND t.artist NOT LIKE '%apple-music%'
          AND t.id NOT IN ('14939874', '218663293', '152484359', '61897480', '65048245')
        ORDER BY t.title COLLATE NOCASE, t.artist COLLATE NOCASE;
        """
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }
        sqlite3_bind_text(stmt, 1, (tagId as NSString).utf8String, -1, nil)
        while sqlite3_step(stmt) == SQLITE_ROW {
            let id = String(cString: sqlite3_column_text(stmt, 0))
            let title = String(cString: sqlite3_column_text(stmt, 1))
            let artist = sqlite3_column_text(stmt, 2).map { String(cString: $0) } ?? ""
            let album = sqlite3_column_text(stmt, 3).map { String(cString: $0) } ?? ""
            let genre = sqlite3_column_text(stmt, 4).map { String(cString: $0) } ?? ""
            let bpm = sqlite3_column_double(stmt, 5)
            let key = sqlite3_column_text(stmt, 6).map { String(cString: $0) } ?? ""
            let rating = Int(sqlite3_column_int(stmt, 7))
            let comment = sqlite3_column_text(stmt, 8).map { String(cString: $0) } ?? ""
            let colorId = Int(sqlite3_column_int(stmt, 9))
            let playCount = max(0, Int(sqlite3_column_int(stmt, 10)))
            let dateAdded = sqlite3_column_text(stmt, 11).map { String(cString: $0) } ?? ""
            let updatedAt = sqlite3_column_text(stmt, 12).map { String(cString: $0) } ?? ""

            let track = Track(id: id, title: title, artist: artist, album: album, genre: genre, bpm: bpm, keyName: key, rating: rating, comment: comment, colorId: colorId, djPlayCount: playCount, dateAdded: dateAdded, updatedAt: updatedAt)
            if !track.isStreaming {
                list.append(track)
            }
        }
        sqlite3_finalize(stmt)
        return list
    }

    func getTagIds(forTrackId trackId: String) -> Set<String> {
        var set: Set<String> = []
        let sql = "SELECT tag_id FROM track_tags WHERE track_id = ?;"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }
        sqlite3_bind_text(stmt, 1, (trackId as NSString).utf8String, -1, nil)
        while sqlite3_step(stmt) == SQLITE_ROW {
            set.insert(String(cString: sqlite3_column_text(stmt, 0)))
        }
        sqlite3_finalize(stmt)
        return set
    }

    func getPlaylistIds(forTrackId trackId: String) -> Set<String> {
        var set: Set<String> = []
        let sql = "SELECT playlist_id FROM playlist_tracks WHERE track_id = ?;"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }
        sqlite3_bind_text(stmt, 1, (trackId as NSString).utf8String, -1, nil)
        while sqlite3_step(stmt) == SQLITE_ROW {
            set.insert(String(cString: sqlite3_column_text(stmt, 0)))
        }
        sqlite3_finalize(stmt)
        return set
    }

    // MARK: - Changelog Queue
    func addChangelogEvent(action: String, payload: [String: Any]) {
        guard let jsonData = try? JSONSerialization.data(withJSONObject: payload),
              let jsonString = String(data: jsonData, encoding: .utf8) else {
            return
        }
        let id = UUID().uuidString
        let now = ISO8601DateFormatter().string(from: Date())
        let sql = "INSERT INTO changelog_events (id, action, payload, status, created_at) VALUES (?, ?, ?, 'PENDING', ?);"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return }
        sqlite3_bind_text(stmt, 1, (id as NSString).utf8String, -1, nil)
        sqlite3_bind_text(stmt, 2, (action as NSString).utf8String, -1, nil)
        sqlite3_bind_text(stmt, 3, (jsonString as NSString).utf8String, -1, nil)
        sqlite3_bind_text(stmt, 4, (now as NSString).utf8String, -1, nil)
        sqlite3_step(stmt)
        sqlite3_finalize(stmt)
    }

    func getPendingChangelogEvents() -> [ChangelogEvent] {
        var list: [ChangelogEvent] = []
        let sql = "SELECT id, action, payload, status, created_at, processed_at, error_message FROM changelog_events WHERE status = 'PENDING' ORDER BY created_at ASC;"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }
        while sqlite3_step(stmt) == SQLITE_ROW {
            let id = String(cString: sqlite3_column_text(stmt, 0))
            let action = String(cString: sqlite3_column_text(stmt, 1))
            let payload = String(cString: sqlite3_column_text(stmt, 2))
            let status = String(cString: sqlite3_column_text(stmt, 3))
            let createdAt = String(cString: sqlite3_column_text(stmt, 4))
            let processedAt = sqlite3_column_text(stmt, 5).map { String(cString: $0) }
            let errorMsg = sqlite3_column_text(stmt, 6).map { String(cString: $0) }
            list.append(ChangelogEvent(id: id, action: action, payload: payload, status: status, createdAt: createdAt, processedAt: processedAt, errorMessage: errorMsg))
        }
        sqlite3_finalize(stmt)
        return list
    }

    func getAllChangelogEvents() -> [ChangelogEvent] {
        var list: [ChangelogEvent] = []
        let sql = "SELECT id, action, payload, status, created_at, processed_at, error_message FROM changelog_events ORDER BY created_at DESC LIMIT 50;"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }
        while sqlite3_step(stmt) == SQLITE_ROW {
            let id = String(cString: sqlite3_column_text(stmt, 0))
            let action = String(cString: sqlite3_column_text(stmt, 1))
            let payload = String(cString: sqlite3_column_text(stmt, 2))
            let status = String(cString: sqlite3_column_text(stmt, 3))
            let createdAt = String(cString: sqlite3_column_text(stmt, 4))
            let processedAt = sqlite3_column_text(stmt, 5).map { String(cString: $0) }
            let errorMsg = sqlite3_column_text(stmt, 6).map { String(cString: $0) }
            list.append(ChangelogEvent(id: id, action: action, payload: payload, status: status, createdAt: createdAt, processedAt: processedAt, errorMessage: errorMsg))
        }
        sqlite3_finalize(stmt)
        return list
    }

    func updateEventStatus(id: String, status: String, errorMessage: String? = nil) {
        let sql = "UPDATE changelog_events SET status = ?, error_message = ? WHERE id = ?;"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return }
        sqlite3_bind_text(stmt, 1, (status as NSString).utf8String, -1, nil)
        if let err = errorMessage {
            sqlite3_bind_text(stmt, 2, (err as NSString).utf8String, -1, nil)
        } else {
            sqlite3_bind_null(stmt, 2)
        }
        sqlite3_bind_text(stmt, 3, (id as NSString).utf8String, -1, nil)
        sqlite3_step(stmt)
        sqlite3_finalize(stmt)
    }

    // MARK: - Optimistic Local Modifications
    func updateTrackLocally(trackId: String, title: String, comment: String, rating: Int, djPlayCount: Int? = nil) {
        let sql: String
        if djPlayCount != nil {
            sql = "UPDATE tracks SET title = ?, comment = ?, rating = ?, dj_play_count = ? WHERE id = ?;"
        } else {
            sql = "UPDATE tracks SET title = ?, comment = ?, rating = ? WHERE id = ?;"
        }
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return }
        sqlite3_bind_text(stmt, 1, (title as NSString).utf8String, -1, nil)
        sqlite3_bind_text(stmt, 2, (comment as NSString).utf8String, -1, nil)
        sqlite3_bind_int(stmt, 3, Int32(rating))
        if let count = djPlayCount {
            sqlite3_bind_int(stmt, 4, Int32(max(0, count)))
            sqlite3_bind_text(stmt, 5, (trackId as NSString).utf8String, -1, nil)
        } else {
            sqlite3_bind_text(stmt, 4, (trackId as NSString).utf8String, -1, nil)
        }
        sqlite3_step(stmt)
        sqlite3_finalize(stmt)
    }

    func updatePlayCountLocally(trackId: String, newCount: Int) {
        let safeCount = max(0, newCount)
        let sql = "UPDATE tracks SET dj_play_count = ? WHERE id = ?;"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return }
        sqlite3_bind_int(stmt, 1, Int32(safeCount))
        sqlite3_bind_text(stmt, 2, (trackId as NSString).utf8String, -1, nil)
        sqlite3_step(stmt)
        sqlite3_finalize(stmt)
    }

    func setTrackTagsLocally(trackId: String, tagIds: [String]) {
        let delSql = "DELETE FROM track_tags WHERE track_id = ?;"
        var delStmt: OpaquePointer?
        if sqlite3_prepare_v2(db, delSql, -1, &delStmt, nil) == SQLITE_OK {
            sqlite3_bind_text(delStmt, 1, (trackId as NSString).utf8String, -1, nil)
            sqlite3_step(delStmt)
            sqlite3_finalize(delStmt)
        }
        for tid in tagIds {
            let id = UUID().uuidString
            let insSql = "INSERT INTO track_tags (id, track_id, tag_id) VALUES (?, ?, ?);"
            var insStmt: OpaquePointer?
            if sqlite3_prepare_v2(db, insSql, -1, &insStmt, nil) == SQLITE_OK {
                sqlite3_bind_text(insStmt, 1, (id as NSString).utf8String, -1, nil)
                sqlite3_bind_text(insStmt, 2, (trackId as NSString).utf8String, -1, nil)
                sqlite3_bind_text(insStmt, 3, (tid as NSString).utf8String, -1, nil)
                sqlite3_step(insStmt)
                sqlite3_finalize(insStmt)
            }
        }
    }

    func addTrackToPlaylistLocally(playlistId: String, trackId: String) {
        let id = UUID().uuidString
        let sql = "INSERT OR IGNORE INTO playlist_tracks (id, playlist_id, track_id, track_no) VALUES (?, ?, ?, 999);"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return }
        sqlite3_bind_text(stmt, 1, (id as NSString).utf8String, -1, nil)
        sqlite3_bind_text(stmt, 2, (playlistId as NSString).utf8String, -1, nil)
        sqlite3_bind_text(stmt, 3, (trackId as NSString).utf8String, -1, nil)
        sqlite3_step(stmt)
        sqlite3_finalize(stmt)
    }

    func removeTrackFromPlaylistLocally(playlistId: String, trackId: String) {
        let sql = "DELETE FROM playlist_tracks WHERE playlist_id = ? AND track_id = ?;"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return }
        sqlite3_bind_text(stmt, 1, (playlistId as NSString).utf8String, -1, nil)
        sqlite3_bind_text(stmt, 2, (trackId as NSString).utf8String, -1, nil)
        sqlite3_step(stmt)
        sqlite3_finalize(stmt)
    }

    func updatePlaylistTrackOrderLocally(playlistId: String, trackIds: [String]) {
        sqlite3_exec(db, "BEGIN TRANSACTION;", nil, nil, nil)
        let sql = "UPDATE playlist_tracks SET track_no = ? WHERE playlist_id = ? AND track_id = ?;"
        var stmt: OpaquePointer?
        if sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK {
            for (index, tid) in trackIds.enumerated() {
                sqlite3_bind_int(stmt, 1, Int32(index + 1))
                sqlite3_bind_text(stmt, 2, (playlistId as NSString).utf8String, -1, nil)
                sqlite3_bind_text(stmt, 3, (tid as NSString).utf8String, -1, nil)
                sqlite3_step(stmt)
                sqlite3_reset(stmt)
            }
            sqlite3_finalize(stmt)
        }
        sqlite3_exec(db, "COMMIT;", nil, nil, nil)
    }

    func createPlaylistLocally(name: String, parentId: String?) -> String {
        let id = "mobile_" + UUID().uuidString.prefix(8)
        let sql = "INSERT INTO playlists (id, name, parent_id, seq, is_folder, attribute, updated_at) VALUES (?, ?, ?, 999, 0, 0, ?);"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return id }
        sqlite3_bind_text(stmt, 1, (id as NSString).utf8String, -1, nil)
        sqlite3_bind_text(stmt, 2, (name as NSString).utf8String, -1, nil)
        if let pid = parentId {
            sqlite3_bind_text(stmt, 3, (pid as NSString).utf8String, -1, nil)
        } else {
            sqlite3_bind_null(stmt, 3)
        }
        let now = ISO8601DateFormatter().string(from: Date())
        sqlite3_bind_text(stmt, 4, (now as NSString).utf8String, -1, nil)
        sqlite3_step(stmt)
        sqlite3_finalize(stmt)
        return id
    }
}
