import Foundation

struct Tag: Identifiable, Hashable, Codable {
    let id: String
    var name: String
    var parentId: String?
    var seq: Int
    var updatedAt: String
}

struct TrackTag: Identifiable, Hashable, Codable {
    let id: String
    let trackId: String
    let tagId: String
}
