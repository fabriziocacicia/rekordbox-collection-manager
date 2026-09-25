import Foundation

struct Playlist: Identifiable, Hashable, Codable {
    let id: String
    var name: String
    var parentId: String?
    var seq: Int
    var isFolder: Bool
    var attribute: Int
    var updatedAt: String

    var isSmart: Bool {
        attribute == 4
    }
}

struct PlaylistTrack: Identifiable, Hashable, Codable {
    let id: String
    let playlistId: String
    let trackId: String
    var trackNo: Int
}
