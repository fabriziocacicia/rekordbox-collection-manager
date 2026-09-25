import Foundation

struct ChangelogEvent: Identifiable, Hashable, Codable {
    let id: String
    let action: String
    let payload: String
    var status: String
    let createdAt: String
    var processedAt: String?
    var errorMessage: String?

    var isPending: Bool {
        status.uppercased() == "PENDING"
    }

    var formattedDate: String {
        guard let date = ISO8601DateFormatter().date(from: createdAt) else {
            return createdAt
        }
        let formatter = DateFormatter()
        formatter.dateStyle = .short
        formatter.timeStyle = .short
        return formatter.string(from: date)
    }
}
