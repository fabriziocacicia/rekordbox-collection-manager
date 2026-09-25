import Foundation

struct Track: Identifiable, Hashable, Codable {
    let id: String
    var title: String
    var artist: String
    var album: String
    var genre: String
    var bpm: Double
    var keyName: String
    var rating: Int
    var comment: String
    var colorId: Int
    var djPlayCount: Int
    var dateAdded: String
    var updatedAt: String

    var formattedBpm: String {
        if bpm <= 0 { return "-" }
        return String(format: "%.1f", bpm)
    }

    var formattedDateAdded: String {
        if dateAdded.isEmpty { return "-" }
        let clean = dateAdded.replacingOccurrences(of: "T", with: " ")
        if clean.count >= 19 {
            return String(clean.prefix(19))
        }
        return clean
    }

    var isStreaming: Bool {
        // Fallback explicit streaming IDs
        if ["14939874", "218663293", "152484359", "61897480", "65048245"].contains(id) {
            return true
        }

        let t = title.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        let a = artist.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        let c = comment.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()

        // Empty titles or untitled tracks
        if t.isEmpty || t == "(untitled)" {
            return true
        }

        let streamingKeywords = [
            "spotify",
            "apple-music",
            "beatport",
            "beatsource",
            "soundcloud",
            "tidal",
        ]
        for kw in streamingKeywords {
            if t.contains(kw) || a.contains(kw) || c.contains(kw) {
                return true
            }
        }
        return false
    }
}
