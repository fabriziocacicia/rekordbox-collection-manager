import SwiftUI

enum TrackSortOption: String, CaseIterable, Identifiable {
    case artistAZ = "Artist (A-Z)"
    case titleAZ = "Title (A-Z)"
    case bpmAsc = "BPM (Low to High)"
    case bpmDesc = "BPM (High to Low)"
    case playCountDesc = "Play Count (Most Played)"
    case dateAddedDesc = "Date Added (Newest)"

    var id: String { rawValue }
}

enum PlayStatusFilter: String, CaseIterable, Identifiable {
    case all = "All Plays"
    case played = "Played (> 0)"
    case unplayed = "Unplayed (0)"

    var id: String { rawValue }
}

enum BpmFilterOption: String, CaseIterable, Identifiable {
    case all = "All BPM"
    case slow = "< 120"
    case house = "120 – 125"
    case upbeat = "125 – 130"
    case fast = "> 130"

    var id: String { rawValue }
}

struct TracksView: View {
    @Environment(AppState.self) private var appState
    @State private var searchText = ""
    @State private var selectedSortOption: TrackSortOption = .artistAZ
    @State private var playStatusFilter: PlayStatusFilter = .all
    @State private var bpmRangeFilter: BpmFilterOption = .all
    @State private var selectedKey: String? = nil
    @State private var selectedTagId: String? = nil
    @State private var showingFilterSheet = false

    @State private var selectedTrackForDetail: Track?
    @State private var trackForPlaylistSelection: Track?

    private let targetCategoryNames = ["Genre", "Style"]

    var categoryFolders: [Tag] {
        appState.tags.filter { tag in
            (tag.parentId == nil || tag.parentId == "root" || tag.parentId == "0" || tag.parentId == "") &&
            targetCategoryNames.contains { $0.caseInsensitiveCompare(tag.name) == .orderedSame }
        }.sorted { $0.seq < $1.seq }
    }

    var availableKeys: [String] {
        let keys = Set(appState.tracks.compactMap { $0.keyName.isEmpty ? nil : $0.keyName })
        return keys.sorted { $0.localizedStandardCompare($1) == .orderedAscending }
    }

    var activeFilterCount: Int {
        var count = 0
        if playStatusFilter != .all { count += 1 }
        if bpmRangeFilter != .all { count += 1 }
        if selectedKey != nil { count += 1 }
        if selectedTagId != nil { count += 1 }
        return count
    }

    func resetFilters() {
        withAnimation {
            playStatusFilter = .all
            bpmRangeFilter = .all
            selectedKey = nil
            selectedTagId = nil
            selectedSortOption = .artistAZ
        }
    }

    var filteredTracks: [Track] {
        var result = appState.tracks.filter { !$0.isStreaming }

        // 1. Search Query
        if !searchText.isEmpty {
            let q = searchText.lowercased()
            result = result.filter {
                $0.title.lowercased().contains(q) ||
                $0.artist.lowercased().contains(q) ||
                $0.album.lowercased().contains(q) ||
                $0.keyName.lowercased().contains(q)
            }
        }

        // 2. Play Status Filter
        switch playStatusFilter {
        case .all:
            break
        case .played:
            result = result.filter { $0.djPlayCount > 0 }
        case .unplayed:
            result = result.filter { $0.djPlayCount == 0 }
        }

        // 3. BPM Range Filter
        switch bpmRangeFilter {
        case .all:
            break
        case .slow:
            result = result.filter { $0.bpm > 0 && $0.bpm < 120 }
        case .house:
            result = result.filter { $0.bpm >= 120 && $0.bpm <= 125 }
        case .upbeat:
            result = result.filter { $0.bpm > 125 && $0.bpm <= 130 }
        case .fast:
            result = result.filter { $0.bpm > 130 }
        }

        // 4. Musical Key Filter
        if let key = selectedKey {
            result = result.filter { $0.keyName.caseInsensitiveCompare(key) == .orderedSame }
        }

        // 5. MyTag Filter
        if let tagId = selectedTagId {
            let matchingIds = LocalDatabase.shared.getTrackIdsForTag(tagId: tagId)
            result = result.filter { matchingIds.contains($0.id) }
        }

        // 6. Sorting
        switch selectedSortOption {
        case .artistAZ:
            result.sort {
                if $0.artist.caseInsensitiveCompare($1.artist) == .orderedSame {
                    return $0.title.caseInsensitiveCompare($1.title) == .orderedAscending
                }
                return $0.artist.caseInsensitiveCompare($1.artist) == .orderedAscending
            }
        case .titleAZ:
            result.sort { $0.title.caseInsensitiveCompare($1.title) == .orderedAscending }
        case .bpmAsc:
            result.sort { $0.bpm < $1.bpm }
        case .bpmDesc:
            result.sort { $0.bpm > $1.bpm }
        case .playCountDesc:
            result.sort { $0.djPlayCount > $1.djPlayCount }
        case .dateAddedDesc:
            result.sort { $0.dateAdded > $1.dateAdded }
        }

        return result
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                // Horizontal Filter & Sort Bar
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        // 1. Master Filter Button Pill (Opens Full Filter Sheet)
                        Button {
                            showingFilterSheet = true
                        } label: {
                            HStack(spacing: 5) {
                                Image(systemName: activeFilterCount > 0 ? "line.3.horizontal.decrease.circle.fill" : "line.3.horizontal.decrease.circle")
                                    .font(.subheadline)
                                Text("Filters")
                                    .fontWeight(.semibold)

                                if activeFilterCount > 0 {
                                    Text("\(activeFilterCount)")
                                        .font(.caption2)
                                        .fontWeight(.bold)
                                        .padding(.horizontal, 6)
                                        .padding(.vertical, 2)
                                        .background(Color.white)
                                        .foregroundStyle(.blue)
                                        .clipShape(Capsule())
                                }
                            }
                            .font(.caption)
                            .padding(.horizontal, 12)
                            .padding(.vertical, 7)
                            .background(activeFilterCount > 0 ? Color.blue : Color(.secondarySystemBackground))
                            .foregroundStyle(activeFilterCount > 0 ? .white : .primary)
                            .clipShape(Capsule())
                        }

                        // 2. Sort Pill Menu
                        Menu {
                            ForEach(TrackSortOption.allCases) { opt in
                                Button {
                                    selectedSortOption = opt
                                } label: {
                                    if selectedSortOption == opt {
                                        Label(opt.rawValue, systemImage: "checkmark")
                                    } else {
                                        Text(opt.rawValue)
                                    }
                                }
                            }
                        } label: {
                            HStack(spacing: 4) {
                                Image(systemName: "arrow.up.arrow.down")
                                Text(selectedSortOption.rawValue)
                            }
                            .font(.caption)
                            .fontWeight(.medium)
                            .padding(.horizontal, 12)
                            .padding(.vertical, 7)
                            .background(Color(.secondarySystemBackground))
                            .foregroundStyle(.primary)
                            .clipShape(Capsule())
                        }

                        // 3. Play Status Filter Pill
                        Menu {
                            Button("All Plays") { playStatusFilter = .all }
                            Button("Played Only (> 0)") { playStatusFilter = .played }
                            Button("Unplayed Only (0)") { playStatusFilter = .unplayed }
                        } label: {
                            HStack(spacing: 4) {
                                Image(systemName: "play.circle")
                                Text(playStatusFilter.rawValue)
                            }
                            .font(.caption)
                            .fontWeight(.medium)
                            .padding(.horizontal, 12)
                            .padding(.vertical, 7)
                            .background(playStatusFilter != .all ? Color.blue : Color(.secondarySystemBackground))
                            .foregroundStyle(playStatusFilter != .all ? .white : .primary)
                            .clipShape(Capsule())
                        }

                        // 4. BPM Range Filter Pill
                        Menu {
                            ForEach(BpmFilterOption.allCases) { opt in
                                Button(opt.rawValue) { bpmRangeFilter = opt }
                            }
                        } label: {
                            HStack(spacing: 4) {
                                Image(systemName: "metronome")
                                Text(bpmRangeFilter == .all ? "BPM" : bpmRangeFilter.rawValue)
                            }
                            .font(.caption)
                            .fontWeight(.medium)
                            .padding(.horizontal, 12)
                            .padding(.vertical, 7)
                            .background(bpmRangeFilter != .all ? Color.blue : Color(.secondarySystemBackground))
                            .foregroundStyle(bpmRangeFilter != .all ? .white : .primary)
                            .clipShape(Capsule())
                        }

                        // 5. Musical Key Filter Pill
                        if !availableKeys.isEmpty {
                            Menu {
                                Button("All Keys") { selectedKey = nil }
                                ForEach(availableKeys, id: \.self) { key in
                                    Button(key) { selectedKey = key }
                                }
                            } label: {
                                HStack(spacing: 4) {
                                    Image(systemName: "music.note")
                                    Text(selectedKey != nil ? "Key: \(selectedKey!)" : "Key")
                                }
                                .font(.caption)
                                .fontWeight(.medium)
                                .padding(.horizontal, 12)
                                .padding(.vertical, 7)
                                .background(selectedKey != nil ? Color.blue : Color(.secondarySystemBackground))
                                .foregroundStyle(selectedKey != nil ? .white : .primary)
                                .clipShape(Capsule())
                            }
                        }

                        // 6. MyTag Filter Pill
                        if !categoryFolders.isEmpty {
                            Menu {
                                Button("All Tags") { selectedTagId = nil }
                                ForEach(categoryFolders) { folder in
                                    let tags = appState.tags.filter { $0.parentId == folder.id }.sorted { $0.seq < $1.seq }
                                    if !tags.isEmpty {
                                        Section(folder.name) {
                                            ForEach(tags) { tag in
                                                Button(tag.name) { selectedTagId = tag.id }
                                            }
                                        }
                                    }
                                }
                            } label: {
                                HStack(spacing: 4) {
                                    Image(systemName: "tag.fill")
                                    let tagName = appState.tags.first(where: { $0.id == selectedTagId })?.name
                                    Text(tagName != nil ? "Tag: \(tagName!)" : "Tag")
                                }
                                .font(.caption)
                                .fontWeight(.medium)
                                .padding(.horizontal, 12)
                                .padding(.vertical, 7)
                                .background(selectedTagId != nil ? Color.purple : Color(.secondarySystemBackground))
                                .foregroundStyle(selectedTagId != nil ? .white : .primary)
                                .clipShape(Capsule())
                            }
                        }

                        // 7. Clear All Filters Button
                        if activeFilterCount > 0 {
                            Button {
                                resetFilters()
                            } label: {
                                HStack(spacing: 4) {
                                    Image(systemName: "xmark.circle")
                                    Text("Reset")
                                }
                                .font(.caption)
                                .fontWeight(.semibold)
                                .padding(.horizontal, 10)
                                .padding(.vertical, 7)
                                .background(Color.red.opacity(0.12))
                                .foregroundStyle(.red)
                                .clipShape(Capsule())
                            }
                        }
                    }
                    .padding(.horizontal)
                    .padding(.vertical, 6)
                }
                .background(Color(.systemBackground))

                Divider()

                // Tracks List or Empty State
                if appState.tracks.isEmpty && appState.isSyncing {
                    VStack(spacing: 16) {
                        ProgressView()
                        Text("Downloading Rekordbox collection...")
                            .foregroundStyle(.secondary)
                    }
                    .frame(maxHeight: .infinity)
                } else if filteredTracks.isEmpty {
                    ContentUnavailableView {
                        Label("No Matching Tracks", systemImage: "line.3.horizontal.decrease.circle")
                    } description: {
                        Text(activeFilterCount > 0 || !searchText.isEmpty
                             ? "Try clearing some filters or changing your search keywords."
                             : "No tracks in library.")
                    } actions: {
                        if activeFilterCount > 0 || !searchText.isEmpty {
                            Button("Reset Filters & Search") {
                                searchText = ""
                                resetFilters()
                            }
                            .buttonStyle(.borderedProminent)
                        }
                    }
                } else {
                    List {
                        ForEach(filteredTracks) { track in
                            TrackRow(track: track)
                                .contentShape(Rectangle())
                                .onTapGesture {
                                    selectedTrackForDetail = track
                                }
                                .swipeActions(edge: .trailing) {
                                    Button {
                                        selectedTrackForDetail = track
                                    } label: {
                                        Label("Details", systemImage: "info.circle")
                                    }
                                    .tint(.blue)

                                    Button {
                                        trackForPlaylistSelection = track
                                    } label: {
                                        Label("Playlist", systemImage: "folder.badge.plus")
                                    }
                                    .tint(.purple)
                                }
                        }
                    }
                    .listStyle(.plain)
                }
            }
            .navigationTitle("Tracks (\(filteredTracks.count))")
            .searchable(text: $searchText, prompt: "Search title, artist, album, key...")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    if appState.isSyncing {
                        ProgressView()
                    } else {
                        Button {
                            Task { await appState.sync() }
                        } label: {
                            Image(systemName: "arrow.clockwise")
                        }
                    }
                }
            }
            .sheet(isPresented: $showingFilterSheet) {
                TrackFilterSheet(
                    selectedSortOption: $selectedSortOption,
                    playStatusFilter: $playStatusFilter,
                    bpmRangeFilter: $bpmRangeFilter,
                    selectedKey: $selectedKey,
                    selectedTagId: $selectedTagId,
                    availableKeys: availableKeys,
                    categoryFolders: categoryFolders,
                    onReset: resetFilters
                )
            }
            .sheet(item: $selectedTrackForDetail) { track in
                TrackDetailSheet(track: track)
            }
            .sheet(item: $trackForPlaylistSelection) { track in
                AddToPlaylistSheet(track: track)
            }
        }
    }
}

// MARK: - Dedicated Comprehensive Filter Sheet
struct TrackFilterSheet: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(AppState.self) private var appState

    @Binding var selectedSortOption: TrackSortOption
    @Binding var playStatusFilter: PlayStatusFilter
    @Binding var bpmRangeFilter: BpmFilterOption
    @Binding var selectedKey: String?
    @Binding var selectedTagId: String?

    let availableKeys: [String]
    let categoryFolders: [Tag]
    var onReset: () -> Void

    var body: some View {
        NavigationStack {
            Form {
                // 1. Sort Options
                Section("Sort By") {
                    Picker("Sort Order", selection: $selectedSortOption) {
                        ForEach(TrackSortOption.allCases) { opt in
                            Text(opt.rawValue).tag(opt)
                        }
                    }
                    .pickerStyle(.menu)
                }

                // 2. Play Status Filter
                Section("Play Status") {
                    Picker("Play Count", selection: $playStatusFilter) {
                        ForEach(PlayStatusFilter.allCases) { opt in
                            Text(opt.rawValue).tag(opt)
                        }
                    }
                    .pickerStyle(.segmented)
                }

                // 3. BPM Range Filter
                Section("Tempo (BPM)") {
                    Picker("BPM Range", selection: $bpmRangeFilter) {
                        ForEach(BpmFilterOption.allCases) { opt in
                            Text(opt.rawValue).tag(opt)
                        }
                    }
                    .pickerStyle(.menu)
                }

                // 4. Musical Key Filter
                if !availableKeys.isEmpty {
                    Section("Musical Key") {
                        Picker("Select Key", selection: $selectedKey) {
                            Text("All Keys").tag(String?.none)
                            ForEach(availableKeys, id: \.self) { key in
                                Text(key).tag(Optional(key))
                            }
                        }
                        .pickerStyle(.menu)
                    }
                }

                // 5. MyTags Filter
                if !categoryFolders.isEmpty {
                    Section("MyTags") {
                        Picker("Filter by Tag", selection: $selectedTagId) {
                            Text("All Tags").tag(String?.none)
                            ForEach(categoryFolders) { folder in
                                let tags = appState.tags.filter { $0.parentId == folder.id }.sorted { $0.seq < $1.seq }
                                if !tags.isEmpty {
                                    Section(folder.name) {
                                        ForEach(tags) { tag in
                                            Text("\(folder.name): \(tag.name)").tag(Optional(tag.id))
                                        }
                                    }
                                }
                            }
                        }
                        .pickerStyle(.menu)
                    }
                }

                // 6. Reset Section
                Section {
                    Button(role: .destructive) {
                        onReset()
                    } label: {
                        HStack {
                            Spacer()
                            Text("Reset All Filters")
                            Spacer()
                        }
                    }
                }
            }
            .navigationTitle("Filter & Sort")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Reset") {
                        onReset()
                    }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") {
                        dismiss()
                    }
                    .fontWeight(.bold)
                }
            }
        }
    }
}

// MARK: - Track Row View
struct TrackRow: View {
    let track: Track

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(track.title)
                    .font(.body)
                    .fontWeight(.semibold)
                    .lineLimit(1)

                Text(track.artist.isEmpty ? "Unknown Artist" : track.artist)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)

                if track.djPlayCount > 0 {
                    HStack(spacing: 3) {
                        Image(systemName: "play.fill")
                            .font(.system(size: 8))
                        Text("\(track.djPlayCount)")
                            .font(.caption2)
                            .fontWeight(.medium)
                    }
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 5)
                    .padding(.vertical, 1)
                    .background(Color(.secondarySystemBackground))
                    .clipShape(Capsule())
                    .padding(.top, 2)
                }
            }

            Spacer()

            VStack(alignment: .trailing, spacing: 4) {
                if !track.keyName.isEmpty {
                    Text(track.keyName)
                        .font(.caption2)
                        .fontWeight(.bold)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(Color.blue.opacity(0.15))
                        .foregroundStyle(.blue)
                        .clipShape(RoundedRectangle(cornerRadius: 4))
                }

                Text(track.formattedBpm)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
            }
        }
        .padding(.vertical, 4)
    }
}
