import SwiftUI

struct LiveSetView: View {
    @Environment(AppState.self) private var appState

    @State private var showingStartSheet = false
    @State private var showingEndSheet = false
    @State private var searchText = ""
    @State private var isBpmNearFilter = false
    @State private var isHarmonicKeyFilter = false
    @State private var isReverseOrder = true
    @State private var trackJustAddedId: String? = nil

    private let haptic = UIImpactFeedbackGenerator(style: .medium)

    var lastPlayedTrack: Track? {
        appState.activeSessionTracks.last?.track
    }

    // Helper for Camelot harmonic key compatibility
    private func getCompatibleKeys(for key: String) -> Set<String> {
        let clean = key.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard clean.count >= 2 else { return [clean] }

        let letter = clean.suffix(1)
        guard letter == "A" || letter == "B" else { return [clean] }
        guard let num = Int(clean.dropLast()) else { return [clean] }

        let sameKey = "\(num)\(letter)"
        let otherLetter = (letter == "A") ? "B" : "A"
        let relativeKey = "\(num)\(otherLetter)"

        let plusOne = (num % 12) + 1
        let minusOne = (num == 1) ? 12 : (num - 1)

        let upKey = "\(plusOne)\(letter)"
        let downKey = "\(minusOne)\(letter)"

        return [sameKey, relativeKey, upKey, downKey]
    }

    var quickSearchResults: [Track] {
        guard !searchText.isEmpty || isBpmNearFilter || isHarmonicKeyFilter else {
            return []
        }

        var list = appState.tracks.filter { !$0.isStreaming }

        if !searchText.isEmpty {
            let q = searchText.lowercased()
            list = list.filter {
                $0.title.lowercased().contains(q) ||
                $0.artist.lowercased().contains(q) ||
                $0.album.lowercased().contains(q) ||
                $0.keyName.lowercased().contains(q)
            }
        }

        if isBpmNearFilter, let lastBpm = lastPlayedTrack?.bpm, lastBpm > 0 {
            list = list.filter { abs($0.bpm - lastBpm) <= 4.0 }
        }

        if isHarmonicKeyFilter, let lastKey = lastPlayedTrack?.keyName, !lastKey.isEmpty {
            let compatible = getCompatibleKeys(for: lastKey)
            list = list.filter { compatible.contains($0.keyName.uppercased()) }
        }

        return Array(list.prefix(40))
    }

    var displayedSessionTracks: [LiveSessionTrack] {
        if isReverseOrder {
            return appState.activeSessionTracks.reversed()
        } else {
            return appState.activeSessionTracks
        }
    }

    var body: some View {
        NavigationStack {
            ZStack {
                Color(uiColor: .systemGroupedBackground)
                    .ignoresSafeArea()

                if let session = appState.activeSession {
                    activeSessionView(session: session)
                } else {
                    standbyView
                }
            }
            .navigationTitle(appState.activeSession != nil ? "Live Session" : "Live Set")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                if let _ = appState.activeSession {
                    ToolbarItem(placement: .topBarLeading) {
                        Button {
                            withAnimation {
                                isReverseOrder.toggle()
                            }
                        } label: {
                            Image(systemName: isReverseOrder ? "arrow.up.arrow.down.circle.fill" : "arrow.up.arrow.down.circle")
                                .font(.subheadline)
                        }
                    }

                    ToolbarItem(placement: .topBarTrailing) {
                        Button {
                            showingEndSheet = true
                        } label: {
                            Text("End Set")
                                .font(.subheadline.bold())
                                .foregroundColor(.white)
                                .padding(.horizontal, 12)
                                .padding(.vertical, 5)
                                .background(Color.red)
                                .clipShape(Capsule())
                        }
                    }
                }
            }
            .sheet(isPresented: $showingStartSheet) {
                StartLiveSetSheet()
            }
            .sheet(isPresented: $showingEndSheet) {
                EndLiveSetSheet()
            }
        }
    }

    // MARK: - Standby View
    private var standbyView: some View {
        ScrollView {
            VStack(spacing: 28) {
                Spacer().frame(height: 20)

                // Large Pioneer-style Deck Record Indicator
                VStack(spacing: 16) {
                    ZStack {
                        Circle()
                            .fill(
                                RadialGradient(
                                    colors: [Color.blue.opacity(0.2), Color.clear],
                                    center: .center,
                                    startRadius: 40,
                                    endRadius: 110
                                )
                            )
                            .frame(width: 200, height: 200)

                        Circle()
                            .strokeBorder(Color.blue.opacity(0.3), lineWidth: 2)
                            .frame(width: 140, height: 140)

                        Circle()
                            .fill(Color(uiColor: .secondarySystemGroupedBackground))
                            .frame(width: 120, height: 120)
                            .shadow(color: .black.opacity(0.2), radius: 10, x: 0, y: 5)

                        Image(systemName: "record.circle.fill")
                            .font(.system(size: 60))
                            .foregroundStyle(
                                LinearGradient(
                                    colors: [.blue, .cyan],
                                    startPoint: .topLeading,
                                    endPoint: .bottomTrailing
                                )
                            )
                    }

                    VStack(spacing: 6) {
                        Text("Record Live Set")
                            .font(.title2.bold())

                        Text("Playing live on a friend's gear?\nLog every track with precise timestamps & sync to Rekordbox.")
                            .font(.subheadline)
                            .foregroundColor(.secondary)
                            .multilineTextAlignment(.center)
                            .padding(.horizontal, 32)
                    }
                }

                // Start Button
                Button {
                    haptic.impactOccurred()
                    showingStartSheet = true
                } label: {
                    HStack(spacing: 10) {
                        Image(systemName: "play.fill")
                            .font(.headline)
                        Text("Start Live Set")
                            .font(.headline.weight(.semibold))
                    }
                    .foregroundColor(.white)
                    .frame(maxWidth: .infinity)
                    .frame(height: 54)
                    .background(
                        LinearGradient(
                            colors: [Color.blue, Color.cyan],
                            startPoint: .leading,
                            endPoint: .trailing
                        )
                    )
                    .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                    .shadow(color: Color.blue.opacity(0.3), radius: 8, x: 0, y: 4)
                }
                .padding(.horizontal, 24)

                // Recent Completed Sessions
                if !appState.recentSessions.isEmpty {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Past Sessions")
                            .font(.headline)
                            .padding(.horizontal, 24)

                        VStack(spacing: 8) {
                            ForEach(appState.recentSessions.prefix(5)) { session in
                                HStack {
                                    VStack(alignment: .leading, spacing: 4) {
                                        Text(session.name)
                                            .font(.subheadline.weight(.medium))
                                            .lineLimit(1)

                                        HStack(spacing: 10) {
                                            Label("\(session.trackCount) tracks", systemImage: "music.note")
                                                .font(.caption)
                                                .foregroundColor(.secondary)

                                            if let dur = session.endedAt {
                                                let elapsed = max(0, Int(dur.timeIntervalSince(session.startedAt)))
                                                let mins = elapsed / 60
                                                Label("\(mins) min", systemImage: "clock")
                                                    .font(.caption)
                                                    .foregroundColor(.secondary)
                                            }
                                        }
                                    }

                                    Spacer()

                                    Image(systemName: "checkmark.circle.fill")
                                        .foregroundColor(.green.opacity(0.8))
                                        .font(.caption)
                                }
                                .padding(.horizontal, 16)
                                .padding(.vertical, 12)
                                .background(Color(uiColor: .secondarySystemGroupedBackground))
                                .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                            }
                        }
                        .padding(.horizontal, 24)
                    }
                }

                Spacer().frame(height: 30)
            }
        }
    }

    // MARK: - Active Session View
    private func activeSessionView(session: LiveSession) -> some View {
        VStack(spacing: 0) {
            // Live Status Header Banner
            TimelineView(.periodic(from: .now, by: 1.0)) { context in
                liveHeaderBanner(session: session, currentTime: context.date)
            }

            // Quick Search and Compatibility Bar
            searchAndFilterBar

            // Results or Tracklist
            if !searchText.isEmpty || isBpmNearFilter || isHarmonicKeyFilter {
                searchResultsSection
            } else {
                playedTracksListSection(session: session)
            }
        }
    }

    // MARK: - Live Header Banner
    private func liveHeaderBanner(session: LiveSession, currentTime: Date) -> some View {
        VStack(spacing: 8) {
            HStack(alignment: .center) {
                // Pulsing Live Indicator
                HStack(spacing: 6) {
                    Circle()
                        .fill(Color.red)
                        .frame(width: 10, height: 10)
                        .opacity((Int(currentTime.timeIntervalSince1970) % 2 == 0) ? 1.0 : 0.4)

                    Text("LIVE")
                        .font(.caption.bold())
                        .foregroundColor(.red)
                }
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(Color.red.opacity(0.12))
                .clipShape(Capsule())

                // Stopwatch
                let elapsed = max(0, Int(currentTime.timeIntervalSince(session.startedAt)))
                let hrs = elapsed / 3600
                let mins = (elapsed % 3600) / 60
                let secs = elapsed % 60
                Text(String(format: "%02d:%02d:%02d", hrs, mins, secs))
                    .font(.system(.subheadline, design: .monospaced).bold())
                    .foregroundColor(.primary)

                Spacer()

                // Track Count
                HStack(spacing: 4) {
                    Image(systemName: "music.note.list")
                        .font(.caption2)
                    Text("\(appState.activeSessionTracks.count) played")
                        .font(.caption.weight(.semibold))
                }
                .foregroundColor(.secondary)
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(Color(uiColor: .tertiarySystemGroupedBackground))
                .clipShape(Capsule())
            }

            // Venue & Collaborators Subtitle
            HStack(spacing: 6) {
                if let loc = session.location, !loc.isEmpty {
                    Label(loc, systemImage: "mappin.and.ellipse")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                if let collabs = session.collaborators, !collabs.isEmpty {
                    Text("•")
                        .font(.caption)
                        .foregroundColor(.secondary)
                    Label(collabs, systemImage: "person.2")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                Spacer()
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(Color(uiColor: .secondarySystemGroupedBackground))
    }

    // MARK: - Search & Filter Bar
    private var searchAndFilterBar: some View {
        VStack(spacing: 8) {
            // Search Input Field
            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass")
                    .foregroundColor(.secondary)

                TextField("Quick search title, artist, key...", text: $searchText)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)

                if !searchText.isEmpty {
                    Button {
                        searchText = ""
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                            .foregroundColor(.secondary)
                    }
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(Color(uiColor: .tertiarySystemGroupedBackground))
            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))

            // DJ Assistance Quick Filters
            HStack(spacing: 8) {
                if let last = lastPlayedTrack {
                    if last.bpm > 0 {
                        Button {
                            withAnimation(.spring(response: 0.3)) {
                                isBpmNearFilter.toggle()
                            }
                        } label: {
                            HStack(spacing: 4) {
                                Image(systemName: "metronome")
                                    .font(.caption2)
                                Text("±4 BPM (\(Int(last.bpm)))")
                                    .font(.caption.weight(.medium))
                            }
                            .padding(.horizontal, 10)
                            .padding(.vertical, 5)
                            .background(isBpmNearFilter ? Color.blue : Color(uiColor: .tertiarySystemGroupedBackground))
                            .foregroundColor(isBpmNearFilter ? .white : .secondary)
                            .clipShape(Capsule())
                        }
                    }

                    if !last.keyName.isEmpty {
                        Button {
                            withAnimation(.spring(response: 0.3)) {
                                isHarmonicKeyFilter.toggle()
                            }
                        } label: {
                            HStack(spacing: 4) {
                                Image(systemName: "tuningfork")
                                    .font(.caption2)
                                Text("Key match (\(last.keyName))")
                                    .font(.caption.weight(.medium))
                            }
                            .padding(.horizontal, 10)
                            .padding(.vertical, 5)
                            .background(isHarmonicKeyFilter ? Color.cyan : Color(uiColor: .tertiarySystemGroupedBackground))
                            .foregroundColor(isHarmonicKeyFilter ? .white : .secondary)
                            .clipShape(Capsule())
                        }
                    }
                }

                Spacer()

                if isBpmNearFilter || isHarmonicKeyFilter || !searchText.isEmpty {
                    Button("Clear") {
                        withAnimation {
                            searchText = ""
                            isBpmNearFilter = false
                            isHarmonicKeyFilter = false
                        }
                    }
                    .font(.caption)
                    .foregroundColor(.secondary)
                }
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .background(Color(uiColor: .secondarySystemGroupedBackground))
        .overlay(
            Rectangle()
                .fill(Color(uiColor: .separator).opacity(0.4))
                .frame(height: 1),
            alignment: .bottom
        )
    }

    // MARK: - Search Results Section
    private var searchResultsSection: some View {
        List {
            Section(header: Text("Search Results (\(quickSearchResults.count))")) {
                if quickSearchResults.isEmpty {
                    Text("No matching tracks in library")
                        .foregroundColor(.secondary)
                        .font(.subheadline)
                        .listRowBackground(Color(uiColor: .secondarySystemGroupedBackground))
                } else {
                    ForEach(quickSearchResults) { track in
                        HStack(spacing: 12) {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(track.title)
                                    .font(.body.weight(.medium))
                                    .foregroundColor(.primary)
                                    .lineLimit(1)

                                HStack(spacing: 6) {
                                    Text(track.artist.isEmpty ? "Unknown Artist" : track.artist)
                                        .font(.caption)
                                        .foregroundColor(.secondary)
                                        .lineLimit(1)

                                    if !track.keyName.isEmpty {
                                        Text(track.keyName)
                                            .font(.caption2.bold())
                                            .padding(.horizontal, 5)
                                            .padding(.vertical, 2)
                                            .background(Color.cyan.opacity(0.15))
                                            .foregroundColor(.cyan)
                                            .clipShape(RoundedRectangle(cornerRadius: 4))
                                    }

                                    if track.bpm > 0 {
                                        Text(track.formattedBpm)
                                            .font(.caption2.monospaced())
                                            .foregroundColor(.secondary)
                                    }
                                }
                            }

                            Spacer()

                            // Direct 1-Tap Add Button
                            Button {
                                addTrackToSession(track: track)
                            } label: {
                                Image(systemName: "plus.circle.fill")
                                    .font(.title2)
                                    .foregroundStyle(Color.blue)
                            }
                            .buttonStyle(.borderless)
                        }
                        .padding(.vertical, 4)
                        .listRowBackground(
                            trackJustAddedId == track.id
                            ? Color.green.opacity(0.15)
                            : Color(uiColor: .secondarySystemGroupedBackground)
                        )
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
    }

    // MARK: - Played Tracks List Section
    private func playedTracksListSection(session: LiveSession) -> some View {
        Group {
            if appState.activeSessionTracks.isEmpty {
                VStack(spacing: 16) {
                    Spacer().frame(height: 40)
                    Image(systemName: "music.note.list")
                        .font(.system(size: 48))
                        .foregroundColor(.secondary.opacity(0.5))

                    Text("Ready for First Track")
                        .font(.headline)
                        .foregroundColor(.secondary)

                    Text("Use the search bar above to quickly add the tracks you drop into your set.")
                        .font(.subheadline)
                        .foregroundColor(.secondary.opacity(0.8))
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 36)
                    Spacer()
                }
            } else {
                List {
                    Section {
                        ForEach(displayedSessionTracks) { item in
                            playedTrackRow(item: item)
                                .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                                    Button(role: .destructive) {
                                        haptic.impactOccurred()
                                        withAnimation {
                                            appState.removeTrackFromActiveSession(liveTrackId: item.id)
                                        }
                                    } label: {
                                        Label("Remove", systemImage: "trash")
                                    }
                                }
                        }
                        .onMove { indices, newOffset in
                            var currentIds = appState.activeSessionTracks.map { $0.id }
                            currentIds.move(fromOffsets: indices, toOffset: newOffset)
                            appState.reorderActiveSession(orderedLiveTrackIds: currentIds)
                        }
                    } header: {
                        HStack {
                            Text(isReverseOrder ? "Latest Played First" : "Chronological Order")
                            Spacer()
                            Text("Swipe to remove")
                                .font(.caption2)
                        }
                    }
                }
                .listStyle(.insetGrouped)
            }
        }
    }

    // MARK: - Played Track Row
    private func playedTrackRow(item: LiveSessionTrack) -> some View {
        HStack(spacing: 12) {
            // Sequence Number Badge
            Text("#\(item.trackNo)")
                .font(.system(.caption, design: .monospaced).bold())
                .foregroundColor(.secondary)
                .frame(width: 32, alignment: .leading)

            VStack(alignment: .leading, spacing: 3) {
                Text(item.track?.title ?? "Unknown Track")
                    .font(.subheadline.weight(.semibold))
                    .lineLimit(1)

                HStack(spacing: 8) {
                    Text(item.track?.artist ?? "-")
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .lineLimit(1)

                    if let key = item.track?.keyName, !key.isEmpty {
                        Text(key)
                            .font(.caption2.bold())
                            .padding(.horizontal, 5)
                            .padding(.vertical, 1)
                            .background(Color.cyan.opacity(0.15))
                            .foregroundColor(.cyan)
                            .clipShape(RoundedRectangle(cornerRadius: 3))
                    }

                    if let bpm = item.track?.bpm, bpm > 0 {
                        Text(String(format: "%.1f", bpm))
                            .font(.caption2.monospaced())
                            .foregroundColor(.secondary)
                    }
                }
            }

            Spacer()

            // Time Added
            Text(item.shortTime)
                .font(.system(.caption2, design: .monospaced))
                .foregroundColor(.secondary)
        }
        .padding(.vertical, 2)
        .listRowBackground(Color(uiColor: .secondarySystemGroupedBackground))
    }

    // MARK: - Add Track Action
    private func addTrackToSession(track: Track) {
        haptic.impactOccurred()
        trackJustAddedId = track.id

        withAnimation(.spring(response: 0.25)) {
            appState.addTrackToActiveSession(track: track)
        }

        // Keep search open or clear query for next track
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
            withAnimation {
                trackJustAddedId = nil
                searchText = ""
            }
        }
    }
}

// MARK: - Start Live Set Sheet
struct StartLiveSetSheet: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(AppState.self) private var appState

    @State private var sessionDate: Date = Date()
    @State private var location: String = ""
    @State private var collaborators: String = ""
    @State private var selectedDJs: Set<String> = []
    @FocusState private var focusedField: FocusedField?

    enum FocusedField: Hashable {
        case location
        case collaborators
    }

    private static let previewDateFormatter: DateFormatter = {
        let df = DateFormatter()
        df.dateFormat = "yyyy-MM-dd"
        return df
    }()

    private static let sessionDateFormatter: DateFormatter = {
        let df = DateFormatter()
        df.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return df
    }()

    private var cleanCollaboratorsList: [String] {
        collaborators
            .components(separatedBy: ",")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
    }

    private var previewSessionName: String {
        let datePart = "HISTORY \(Self.previewDateFormatter.string(from: sessionDate))"
        var parts = [datePart]
        let cleanLoc = location.trimmingCharacters(in: .whitespacesAndNewlines)
        if !cleanLoc.isEmpty {
            parts.append("@ \(cleanLoc)")
        }
        let cleanCollab = cleanCollaboratorsList.joined(separator: ", ")
        if !cleanCollab.isEmpty {
            parts.append("w \(cleanCollab)")
        }
        return parts.joined(separator: " ")
    }

    // MARK: - Autocomplete Helpers
    private var filteredLocations: [String] {
        let q = location.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if q.isEmpty {
            return appState.pastLocations
        }
        return appState.pastLocations.filter { $0.lowercased().contains(q) }
    }

    private var currentDJQuery: String {
        let parts = collaborators.components(separatedBy: ",")
        return parts.last?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() ?? ""
    }

    private var filteredCollaborators: [String] {
        let q = currentDJQuery
        let selectedSet = Set(cleanCollaboratorsList.map { $0.lowercased() })
        let unselected = appState.pastCollaborators.filter { !selectedSet.contains($0.lowercased()) }
        if q.isEmpty {
            return unselected
        }
        return unselected.filter { $0.lowercased().contains(q) }
    }

    private func appendCollaborator(_ dj: String) {
        var rawParts = collaborators.components(separatedBy: ",")
        if !rawParts.isEmpty {
            let lastSegment = rawParts.removeLast().trimmingCharacters(in: .whitespacesAndNewlines)
            let isAlreadyConfirmed = selectedDJs.contains(where: { $0.caseInsensitiveCompare(lastSegment) == .orderedSame })
                || (appState.pastCollaborators.contains(where: { $0.caseInsensitiveCompare(lastSegment) == .orderedSame }) && lastSegment.caseInsensitiveCompare(dj) != .orderedSame)

            if isAlreadyConfirmed && !lastSegment.isEmpty {
                rawParts.append(lastSegment)
            }
        }

        var cleanParts = rawParts.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }

        if !cleanParts.contains(where: { $0.caseInsensitiveCompare(dj) == .orderedSame }) {
            cleanParts.append(dj)
            selectedDJs.insert(dj)
        }

        collaborators = cleanParts.joined(separator: ", ") + ", "
        focusedField = .collaborators
    }

    private func toggleCollaboratorChip(_ dj: String) {
        if cleanCollaboratorsList.contains(where: { $0.caseInsensitiveCompare(dj) == .orderedSame }) {
            let remaining = cleanCollaboratorsList.filter { $0.caseInsensitiveCompare(dj) != .orderedSame }
            selectedDJs.remove(dj)
            if remaining.isEmpty {
                collaborators = ""
            } else {
                collaborators = remaining.joined(separator: ", ") + ", "
            }
        } else {
            appendCollaborator(dj)
        }
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 20) {
                    // 1. Session Timing Card
                    VStack(alignment: .leading, spacing: 10) {
                        Label("SESSION TIMING", systemImage: "clock")
                            .font(.caption.bold())
                            .foregroundColor(.secondary)

                        DatePicker("Start Date & Time", selection: $sessionDate)
                            .datePickerStyle(.compact)
                            .padding(.horizontal, 14)
                            .padding(.vertical, 10)
                            .background(Color(uiColor: .secondarySystemGroupedBackground))
                            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                    }
                    .padding(.horizontal, 16)

                    // 2. Location / Venue Card
                    VStack(alignment: .leading, spacing: 10) {
                        HStack {
                            Label("LOCATION / VENUE (@)", systemImage: "mappin.and.ellipse")
                                .font(.caption.bold())
                                .foregroundColor(.secondary)
                            Spacer()
                            if !location.isEmpty {
                                Text("\(filteredLocations.count) matches")
                                    .font(.caption2)
                                    .foregroundColor(.secondary)
                            }
                        }

                        // Input Box
                        HStack(spacing: 8) {
                            Image(systemName: "mappin.circle.fill")
                                .foregroundColor(.blue)

                            TextField("Search or enter venue (e.g. Velvet, Amnesia)", text: $location)
                                .focused($focusedField, equals: .location)
                                .autocorrectionDisabled(true)
                                .textInputAutocapitalization(.words)

                            if !location.isEmpty {
                                Button {
                                    location = ""
                                } label: {
                                    Image(systemName: "xmark.circle.fill")
                                        .foregroundColor(.secondary)
                                }
                            }
                        }
                        .padding(.horizontal, 14)
                        .padding(.vertical, 12)
                        .background(Color(uiColor: .secondarySystemGroupedBackground))
                        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                        .overlay(
                            RoundedRectangle(cornerRadius: 12, style: .continuous)
                                .stroke(focusedField == .location ? Color.blue : Color.clear, lineWidth: 1.5)
                        )

                        // Real-time Autocomplete Dropdown or Quick Suggestion Chips
                        if focusedField == .location && !location.isEmpty && !filteredLocations.isEmpty {
                            VStack(spacing: 0) {
                                ForEach(filteredLocations.prefix(5), id: \.self) { loc in
                                    Button {
                                        location = loc
                                        focusedField = .collaborators
                                    } label: {
                                        HStack(spacing: 10) {
                                            Image(systemName: "mappin")
                                                .font(.caption)
                                                .foregroundColor(.blue)
                                            Text(loc)
                                                .font(.subheadline)
                                                .foregroundColor(.primary)
                                            Spacer()
                                            Image(systemName: "arrow.up.left")
                                                .font(.caption2)
                                                .foregroundColor(.secondary)
                                        }
                                        .padding(.horizontal, 14)
                                        .padding(.vertical, 11)
                                    }
                                    if loc != filteredLocations.prefix(5).last {
                                        Divider().padding(.leading, 34)
                                    }
                                }
                            }
                            .background(Color(uiColor: .secondarySystemGroupedBackground))
                            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                            .shadow(color: .black.opacity(0.1), radius: 6, x: 0, y: 3)
                        } else {
                            // Recent Chips
                            ScrollView(.horizontal, showsIndicators: false) {
                                HStack(spacing: 8) {
                                    ForEach(appState.pastLocations, id: \.self) { loc in
                                        Button {
                                            location = loc
                                        } label: {
                                            Text(loc)
                                                .font(.caption.weight(.medium))
                                                .padding(.horizontal, 12)
                                                .padding(.vertical, 6)
                                                .background(location.caseInsensitiveCompare(loc) == .orderedSame ? Color.blue : Color(uiColor: .secondarySystemGroupedBackground))
                                                .foregroundColor(location.caseInsensitiveCompare(loc) == .orderedSame ? .white : .primary)
                                                .clipShape(Capsule())
                                        }
                                    }
                                }
                                .padding(.vertical, 2)
                            }
                        }
                    }
                    .padding(.horizontal, 16)

                    // 3. Played With / B2B Card
                    VStack(alignment: .leading, spacing: 10) {
                        HStack {
                            Label("PLAYED WITH / B2B (w)", systemImage: "person.2")
                                .font(.caption.bold())
                                .foregroundColor(.secondary)
                            Spacer()
                            if !currentDJQuery.isEmpty {
                                Text("\(filteredCollaborators.count) matches")
                                    .font(.caption2)
                                    .foregroundColor(.secondary)
                            }
                        }

                        // Input Box
                        HStack(spacing: 8) {
                            Image(systemName: "person.wave.2.fill")
                                .foregroundColor(.cyan)

                            TextField("Search or enter DJs (e.g. Silvia, Marco)", text: $collaborators)
                                .focused($focusedField, equals: .collaborators)
                                .autocorrectionDisabled(true)
                                .textInputAutocapitalization(.words)

                            if !collaborators.isEmpty {
                                Button {
                                    collaborators = ""
                                    selectedDJs.removeAll()
                                } label: {
                                    Image(systemName: "xmark.circle.fill")
                                        .foregroundColor(.secondary)
                                }
                            }
                        }
                        .padding(.horizontal, 14)
                        .padding(.vertical, 12)
                        .background(Color(uiColor: .secondarySystemGroupedBackground))
                        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                        .overlay(
                            RoundedRectangle(cornerRadius: 12, style: .continuous)
                                .stroke(focusedField == .collaborators ? Color.cyan : Color.clear, lineWidth: 1.5)
                        )

                        // Real-time Autocomplete Dropdown or Quick Suggestion Chips
                        if focusedField == .collaborators && !currentDJQuery.isEmpty && !filteredCollaborators.isEmpty {
                            VStack(spacing: 0) {
                                ForEach(filteredCollaborators.prefix(5), id: \.self) { dj in
                                    Button {
                                        appendCollaborator(dj)
                                    } label: {
                                        HStack(spacing: 10) {
                                            Image(systemName: "headphones")
                                                .font(.caption)
                                                .foregroundColor(.cyan)
                                            Text(dj)
                                                .font(.subheadline)
                                                .foregroundColor(.primary)
                                            Spacer()
                                            Image(systemName: "plus.circle")
                                                .font(.caption2)
                                                .foregroundColor(.secondary)
                                        }
                                        .padding(.horizontal, 14)
                                        .padding(.vertical, 11)
                                    }
                                    if dj != filteredCollaborators.prefix(5).last {
                                        Divider().padding(.leading, 34)
                                    }
                                }
                            }
                            .background(Color(uiColor: .secondarySystemGroupedBackground))
                            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                            .shadow(color: .black.opacity(0.1), radius: 6, x: 0, y: 3)
                        } else {
                            // Recent Chips
                            ScrollView(.horizontal, showsIndicators: false) {
                                HStack(spacing: 8) {
                                    ForEach(appState.pastCollaborators, id: \.self) { dj in
                                        let isSelected = cleanCollaboratorsList.contains(where: { $0.caseInsensitiveCompare(dj) == .orderedSame })
                                        Button {
                                            toggleCollaboratorChip(dj)
                                        } label: {
                                            HStack(spacing: 4) {
                                                if isSelected {
                                                    Image(systemName: "checkmark")
                                                        .font(.caption2.bold())
                                                }
                                                Text(dj)
                                                    .font(.caption.weight(.medium))
                                            }
                                            .padding(.horizontal, 12)
                                            .padding(.vertical, 6)
                                            .background(isSelected ? Color.cyan : Color(uiColor: .secondarySystemGroupedBackground))
                                            .foregroundColor(isSelected ? .white : .primary)
                                            .clipShape(Capsule())
                                        }
                                    }
                                }
                                .padding(.vertical, 2)
                            }
                        }
                    }
                    .padding(.horizontal, 16)

                    // 4. Session Name Preview Card
                    VStack(alignment: .leading, spacing: 6) {
                        Text("REKORDBOX SESSION NAME PREVIEW")
                            .font(.caption.bold())
                            .foregroundColor(.secondary)

                        VStack(alignment: .leading, spacing: 4) {
                            Text(previewSessionName)
                                .font(.system(.subheadline, design: .monospaced).bold())
                                .foregroundColor(.blue)

                            Text("Matches your Rekordbox session format automatically.")
                                .font(.caption2)
                                .foregroundColor(.secondary)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 12)
                        .background(Color.blue.opacity(0.08))
                        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                        .overlay(
                            RoundedRectangle(cornerRadius: 12, style: .continuous)
                                .stroke(Color.blue.opacity(0.2), lineWidth: 1)
                        )
                    }
                    .padding(.horizontal, 16)

                    // 5. Start Button
                    Button {
                        let dateCreatedStr = Self.sessionDateFormatter.string(from: sessionDate)
                        let cleanLoc = location.trimmingCharacters(in: .whitespacesAndNewlines)
                        let cleanCollab = cleanCollaboratorsList.joined(separator: ", ")

                        appState.startLiveSession(
                            name: previewSessionName,
                            dateCreated: dateCreatedStr,
                            location: cleanLoc.isEmpty ? nil : cleanLoc,
                            collaborators: cleanCollab.isEmpty ? nil : cleanCollab,
                            startedAt: sessionDate
                        )
                        dismiss()
                    } label: {
                        HStack(spacing: 8) {
                            Image(systemName: "record.circle.fill")
                                .font(.headline)
                            Text("Start Live Set")
                                .font(.headline.bold())
                        }
                        .foregroundColor(.white)
                        .frame(maxWidth: .infinity)
                        .frame(height: 52)
                        .background(
                            LinearGradient(
                                colors: [.blue, .cyan],
                                startPoint: .leading,
                                endPoint: .trailing
                            )
                        )
                        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                        .shadow(color: Color.blue.opacity(0.3), radius: 6, x: 0, y: 3)
                    }
                    .padding(.horizontal, 16)
                    .padding(.top, 4)

                    Spacer().frame(height: 30)
                }
                .padding(.vertical, 16)
            }
            .background(Color(uiColor: .systemGroupedBackground).ignoresSafeArea())
            .navigationTitle("New Live Set")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cancel") {
                        dismiss()
                    }
                }
            }
        }
    }
}

// MARK: - End Live Set Sheet
struct EndLiveSetSheet: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(AppState.self) private var appState

    @State private var incrementPlayCounts: Bool = true
    @State private var showingDiscardConfirmation: Bool = false

    var body: some View {
        NavigationStack {
            Form {
                if let session = appState.activeSession {
                    Section(header: Text("Session Summary")) {
                        LabeledContent("Session Name", value: session.name)
                        LabeledContent("Tracks Played", value: "\(appState.activeSessionTracks.count)")

                        let elapsed = max(0, Int(Date().timeIntervalSince(session.startedAt)))
                        let hrs = elapsed / 3600
                        let mins = (elapsed % 3600) / 60
                        LabeledContent("Duration", value: "\(hrs)h \(mins)m")

                        if let loc = session.location, !loc.isEmpty {
                            LabeledContent("Venue", value: loc)
                        }
                        if let col = session.collaborators, !col.isEmpty {
                            LabeledContent("Collaborators", value: col)
                        }
                    }

                    Section(footer: Text("Automatically increments the play count (+1) in your Rekordbox collection for every track played.")) {
                        Toggle("Increment Play Counts", isOn: $incrementPlayCounts)
                    }

                    Section {
                        Button {
                            appState.endActiveSession(incrementPlayCounts: incrementPlayCounts)
                            dismiss()
                        } label: {
                            HStack {
                                Spacer()
                                Label("Finish & Sync to Rekordbox", systemImage: "arrow.triangle.2.circlepath")
                                    .font(.headline)
                                    .foregroundColor(.white)
                                Spacer()
                            }
                            .padding(.vertical, 4)
                        }
                        .listRowBackground(Color.blue)

                        Button(role: .destructive) {
                            showingDiscardConfirmation = true
                        } label: {
                            HStack {
                                Spacer()
                                Text("Discard Session")
                                    .font(.subheadline)
                                Spacer()
                            }
                        }
                    }
                }
            }
            .navigationTitle("End Set")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cancel") {
                        dismiss()
                    }
                }
            }
            .confirmationDialog(
                "Discard this session?",
                isPresented: $showingDiscardConfirmation,
                titleVisibility: .visible
            ) {
                Button("Discard Session", role: .destructive) {
                    appState.discardActiveSession()
                    dismiss()
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("This will discard all tracks logged during this live session without syncing.")
            }
        }
    }
}
