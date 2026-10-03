import Foundation
import SwiftUI

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

    var isProcessed: Bool {
        status.uppercased() == "PROCESSED"
    }

    var isFailed: Bool {
        status.uppercased() == "FAILED"
    }

    var statusColor: Color {
        if isProcessed { return .green }
        if isFailed { return .red }
        return .orange
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

    var formattedProcessedDate: String? {
        guard let p = processedAt, !p.isEmpty else { return nil }
        let isoFormatter = ISO8601DateFormatter()
        isoFormatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        var date = isoFormatter.date(from: p)
        if date == nil {
            date = ISO8601DateFormatter().date(from: p)
        }
        guard let validDate = date else { return p }
        let formatter = DateFormatter()
        formatter.dateStyle = .short
        formatter.timeStyle = .short
        return formatter.string(from: validDate)
    }
}
