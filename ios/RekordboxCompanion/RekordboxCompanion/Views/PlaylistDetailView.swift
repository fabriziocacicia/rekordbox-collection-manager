import SwiftUI

struct PlaylistDetailView: View {
    @Environment(AppState.self) private var appState
    let playlist: Playlist

    @State private var tracks: [Track] = []
    @State private var searchText = ""
    @State private var showingAddTracks = false
    @State private var selectedTrackForDetail: Track?

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
            if playlist.isSmart {
                Section {
                    HStack(spacing: 8) {
                        Image(systemName: "gearshape.fill")
                            .foregroundStyle(.purple)
                        Text("Smart Playlist • Managed dynamically by Rekordbox rules")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 2)
                }
            }

            if tracks.isEmpty {
                ContentUnavailableView(
                    playlist.isSmart ? "No Matching Tracks" : "Empty Playlist",
                    systemImage: playlist.isSmart ? "gearshape" : "music.note.list",
                    description: Text(
                        playlist.isSmart
                            ? "No tracks in your collection currently match this smart playlist's criteria."
                            : "Add tracks to this playlist using the + button."
                    )
                )
            } else if filteredTracks.isEmpty {
                ContentUnavailableView(
                    "No Matching Tracks",
                    systemImage: "magnifyingglass",
                    description: Text("No tracks in \"\(playlist.name)\" matching \"\(searchText)\".")
                )
            } else {
                ForEach(Array(filteredTracks.enumerated()), id: \.element.id) { index, track in
                    HStack(spacing: 12) {
                        Text("\(index + 1)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .frame(width: 24, alignment: .trailing)
                            .monospacedDigit()

                        TrackRow(track: track)
                    }
                    .contentShape(Rectangle())
                    .onTapGesture {
                        selectedTrackForDetail = track
                    }
                    .swipeActions(edge: .trailing) {
                        if !playlist.isSmart {
                            Button(role: .destructive) {
                                appState.removeTrackFromPlaylist(trackId: track.id, playlistId: playlist.id)
                                loadPlaylistTracks()
                            } label: {
                                Label("Remove", systemImage: "trash")
                            }
                        }
                    }
                }
                .onMove { sourceIndices, destinationIndex in
                    guard !playlist.isSmart && searchText.isEmpty else { return }
                    tracks.move(fromOffsets: sourceIndices, toOffset: destinationIndex)
                    let newOrderIds = tracks.map { $0.id }
                    appState.reorderPlaylist(playlistId: playlist.id, trackIds: newOrderIds)
                }
            }
        }
        .searchable(text: $searchText, prompt: "Search in \(playlist.name)...")
        .listStyle(.plain)
        .navigationTitle("\(playlist.name) (\(filteredTracks.count))")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                HStack(spacing: 12) {
                    if !tracks.isEmpty && !playlist.isSmart && searchText.isEmpty {
                        EditButton()
                    }
                    if !playlist.isSmart {
                        Button {
                            showingAddTracks = true
                        } label: {
                            Image(systemName: "plus")
                        }
                    }
                }
            }
        }
        .onAppear {
            loadPlaylistTracks()
        }
        .onChange(of: appState.lastSyncDate) {
            loadPlaylistTracks()
        }
        .onChange(of: appState.isSyncing) { _, isSyncing in
            if !isSyncing {
                loadPlaylistTracks()
            }
        }
        .sheet(isPresented: $showingAddTracks) {
            AddTracksMultiSelectSheet(playlist: playlist) {
                loadPlaylistTracks()
            }
        }
        .sheet(item: $selectedTrackForDetail) { track in
            TrackDetailSheet(track: track)
                .onDisappear {
                    loadPlaylistTracks()
                }
        }
    }

    private func loadPlaylistTracks() {
        tracks = LocalDatabase.shared.getTracksForPlaylist(playlistId: playlist.id)
    }
}

struct AddTracksMultiSelectSheet: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(AppState.self) private var appState
    let playlist: Playlist
    var onDone: () -> Void

    @State private var searchText = ""
    @State private var selectedTrackIds: Set<String> = []

    var availableTracks: [Track] {
        let base = appState.tracks.filter { !$0.isStreaming }
        if searchText.isEmpty {
            return base
        }
        let q = searchText.lowercased()
        return base.filter {
            $0.title.lowercased().contains(q) ||
            $0.artist.lowercased().contains(q) ||
            $0.genre.lowercased().contains(q) ||
            $0.keyName.lowercased().contains(q)
        }
    }

    var body: some View {
        NavigationStack {
            List(availableTracks) { track in
                HStack {
                    TrackRow(track: track)
                    Spacer()
                    if selectedTrackIds.contains(track.id) {
                        Image(systemName: "checkmark.circle.fill")
                            .foregroundStyle(.blue)
                    } else {
                        Image(systemName: "circle")
                            .foregroundStyle(.gray.opacity(0.4))
                    }
                }
                .contentShape(Rectangle())
                .onTapGesture {
                    if selectedTrackIds.contains(track.id) {
                        selectedTrackIds.remove(track.id)
                    } else {
                        selectedTrackIds.insert(track.id)
                    }
                }
            }
            .searchable(text: $searchText, prompt: "Search tracks to add...")
            .navigationTitle("Add to \(playlist.name)")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Add (\(selectedTrackIds.count))") {
                        for tid in selectedTrackIds {
                            appState.addTrackToPlaylist(trackId: tid, playlistId: playlist.id)
                        }
                        onDone()
                        dismiss()
                    }
                    .disabled(selectedTrackIds.isEmpty)
                }
            }
        }
    }
}
