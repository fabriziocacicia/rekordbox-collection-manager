import Foundation

enum LiveSessionStatus: String, Codable, CaseIterable {
    case active = "ACTIVE"
    case completed = "COMPLETED"
    case discarded = "DISCARDED"
}

struct LiveSession: Identifiable, Hashable, Codable {
    let id: String
    var name: String
    var dateCreated: String       // e.g. "2026-10-02 23:30:00"
    var location: String?
    var collaborators: String?
    var status: LiveSessionStatus
    var startedAt: Date
    var endedAt: Date?
    var incrementPlayCounts: Bool
    var trackCount: Int = 0

    var isLive: Bool {
        status == .active
    }

    var formattedStartDate: String {
        let formatter = DateFormatter()
        formatter.dateStyle = .medium
        formatter.timeStyle = .short
        return formatter.string(from: startedAt)
    }

    var durationFormatted: String {
        let end = endedAt ?? Date()
        let elapsed = max(0, Int(end.timeIntervalSince(startedAt)))
        let hours = elapsed / 3600
        let minutes = (elapsed % 3600) / 60
        let seconds = elapsed % 60
        if hours > 0 {
            return String(format: "%02d:%02d:%02d", hours, minutes, seconds)
        } else {
            return String(format: "%02d:%02d", minutes, seconds)
        }
    }
}

struct LiveSessionTrack: Identifiable, Hashable, Codable {
    let id: String
    let sessionId: String
    let trackId: String
    var trackNo: Int
    var playedAt: Date
    var track: Track?

    var formattedTime: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "HH:mm:ss"
        return formatter.string(from: playedAt)
    }

    var shortTime: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "HH:mm"
        return formatter.string(from: playedAt)
    }
}
