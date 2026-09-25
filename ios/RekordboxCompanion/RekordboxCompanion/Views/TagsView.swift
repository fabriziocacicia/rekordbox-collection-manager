import SwiftUI

struct TagsView: View {
    @Environment(AppState.self) private var appState
    @State private var searchText = ""

    // Desktop parity: Only show "Genre" (ID 1) and "Style" (ID 4) categories
    private let targetCategoryNames = ["Genre", "Style"]

    var categoryFolders: [Tag] {
        appState.tags.filter { tag in
            (tag.parentId == nil || tag.parentId == "root" || tag.parentId == "0" || tag.parentId == "") &&
            targetCategoryNames.contains { $0.caseInsensitiveCompare(tag.name) == .orderedSame }
        }.sorted { $0.seq < $1.seq }
    }

    func filteredChildTags(for folder: Tag) -> [Tag] {
        let base = appState.tags.filter { $0.parentId == folder.id }.sorted { $0.seq < $1.seq }
        if searchText.isEmpty {
            return base
        }
        let q = searchText.lowercased()
        return base.filter { $0.name.lowercased().contains(q) }
    }

    var totalMatchingTagsCount: Int {
        categoryFolders.reduce(0) { $0 + filteredChildTags(for: $1).count }
    }

    var body: some View {
        NavigationStack {
            List {
                if categoryFolders.isEmpty && appState.tags.isEmpty {
                    ContentUnavailableView(
                        "No MyTags",
                        systemImage: "tag",
                        description: Text("Sync with Rekordbox to view your MyTags.")
                    )
                } else if !searchText.isEmpty && totalMatchingTagsCount == 0 {
                    ContentUnavailableView(
                        "No Matching Tags",
                        systemImage: "magnifyingglass",
                        description: Text("No tags matching \"\(searchText)\".")
                    )
                } else {
                    ForEach(categoryFolders) { folder in
                        let childTags = filteredChildTags(for: folder)
                        if !childTags.isEmpty {
                            Section(header: Text(folder.name).font(.headline).foregroundStyle(.primary)) {
                                ForEach(childTags) { tag in
                                    NavigationLink {
                                        TagTracksListView(tag: tag)
                                    } label: {
                                        HStack {
                                            Label(tag.name, systemImage: "tag.fill")
                                                .font(.body)
                                            Spacer()
                                            let count = LocalDatabase.shared.getTrackCountForTag(tagId: tag.id)
                                            Text("\(count)")
                                                .font(.caption)
                                                .fontWeight(.medium)
                                                .foregroundStyle(.secondary)
                                                .padding(.horizontal, 8)
                                                .padding(.vertical, 2)
                                                .background(Color(.secondarySystemBackground))
                                                .clipShape(Capsule())
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
            .searchable(text: $searchText, prompt: "Search MyTags...")
            .listStyle(.insetGrouped)
            .navigationTitle("MyTags")
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
        }
    }
}

struct TagTracksListView: View {
    @Environment(AppState.self) private var appState
    let tag: Tag

    @State private var searchText = ""
    @State private var selectedTrackForDetail: Track?
    @State private var tracks: [Track] = []

    var filteredTracks: [Track] {
        if searchText.isEmpty {
            return tracks
        }
        let q = searchText.lowercased()
        return tracks.filter {
            $0.title.lowercased().contains(q) ||
            $0.artist.lowercased().contains(q) ||
            $0.genre.lowercased().contains(q) ||
            $0.album.lowercased().contains(q) ||
            $0.keyName.lowercased().contains(q)
        }
    }

    var body: some View {
        List {
            if tracks.isEmpty {
                ContentUnavailableView(
                    "No Tracks with Tag",
                    systemImage: "tag",
                    description: Text("No tracks in your library currently have the \"\(tag.name)\" tag.")
                )
            } else if filteredTracks.isEmpty {
                ContentUnavailableView(
                    "No Matching Tracks",
                    systemImage: "magnifyingglass",
                    description: Text("No tracks in \"\(tag.name)\" matching \"\(searchText)\".")
                )
            } else {
                ForEach(filteredTracks) { track in
                    TrackRow(track: track)
                        .contentShape(Rectangle())
                        .onTapGesture {
                            selectedTrackForDetail = track
                        }
                }
            }
        }
        .searchable(text: $searchText, prompt: "Search in \(tag.name)...")
        .navigationTitle("\(tag.name) (\(filteredTracks.count))")
        .navigationBarTitleDisplayMode(.inline)
        .listStyle(.plain)
        .onAppear {
            loadTracks()
        }
        .onChange(of: appState.lastSyncDate) {
            loadTracks()
        }
        .onChange(of: appState.isSyncing) { _, isSyncing in
            if !isSyncing {
                loadTracks()
            }
        }
        .sheet(item: $selectedTrackForDetail) { track in
            TrackDetailSheet(track: track)
                .onDisappear {
                    loadTracks()
                }
        }
    }

    private func loadTracks() {
        tracks = LocalDatabase.shared.getTracksForTag(tagId: tag.id)
    }
}
