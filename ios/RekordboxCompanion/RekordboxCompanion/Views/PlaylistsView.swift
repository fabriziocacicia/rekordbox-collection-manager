import SwiftUI

struct PlaylistsView: View {
    @Environment(AppState.self) private var appState
    @State private var showingCreateSheet = false
    @State private var searchText = ""

    var rootPlaylists: [Playlist] {
        let base = appState.playlists.filter {
            $0.parentId == nil || $0.parentId == "root" || $0.parentId == "0" || $0.parentId == ""
        }
        if searchText.isEmpty {
            return base
        }
        let q = searchText.lowercased()
        return base.filter { $0.name.lowercased().contains(q) }
    }

    var body: some View {
        NavigationStack {
            List {
                if rootPlaylists.isEmpty && appState.playlists.isEmpty {
                    ContentUnavailableView(
                        "No Playlists",
                        systemImage: "music.note.list",
                        description: Text("Sync with Rekordbox to view your playlists.")
                    )
                } else if rootPlaylists.isEmpty {
                    ContentUnavailableView(
                        "No Matching Playlists",
                        systemImage: "magnifyingglass",
                        description: Text("No playlists matching \"\(searchText)\".")
                    )
                } else {
                    ForEach(rootPlaylists) { playlist in
                        PlaylistRowItem(playlist: playlist)
                    }
                }
            }
            .searchable(text: $searchText, prompt: "Search playlists...")
            .listStyle(.insetGrouped)
            .navigationTitle("Playlists (\(appState.playlists.count))")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    HStack(spacing: 12) {
                        if appState.isSyncing {
                            ProgressView()
                        } else {
                            Button {
                                Task { await appState.sync() }
                            } label: {
                                Image(systemName: "arrow.clockwise")
                            }
                        }
                        Button {
                            showingCreateSheet = true
                        } label: {
                            Image(systemName: "plus")
                        }
                    }
                }
            }
            .sheet(isPresented: $showingCreateSheet) {
                CreatePlaylistSheet()
            }
        }
    }
}

struct PlaylistRowItem: View {
    @Environment(AppState.self) private var appState
    let playlist: Playlist

    var childPlaylists: [Playlist] {
        appState.playlists.filter { $0.parentId == playlist.id }
    }

    var body: some View {
        if playlist.isFolder {
            DisclosureGroup {
                ForEach(childPlaylists) { child in
                    PlaylistRowItem(playlist: child)
                }
            } label: {
                Label(playlist.name, systemImage: "folder.fill")
                    .fontWeight(.semibold)
                    .foregroundStyle(.primary)
            }
        } else {
            NavigationLink {
                PlaylistDetailView(playlist: playlist)
            } label: {
                HStack {
                    Label {
                        Text(playlist.name)
                            .foregroundStyle(.primary)
                    } icon: {
                        Image(systemName: playlist.isSmart ? "gearshape.fill" : "music.note.list")
                            .foregroundStyle(playlist.isSmart ? .purple : .accentColor)
                    }
                    Spacer()
                    let count = LocalDatabase.shared.getTrackCountForPlaylist(playlistId: playlist.id)
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

struct CreatePlaylistSheet: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(AppState.self) private var appState
    @State private var name = ""
    @State private var selectedParentId: String? = nil

    var body: some View {
        NavigationStack {
            Form {
                Section("Playlist Details") {
                    TextField("Playlist Name", text: $name)
                }

                Section("Location (Folder)") {
                    Picker("Folder", selection: $selectedParentId) {
                        Text("Root Level").tag(String?.none)
                        ForEach(appState.playlists.filter { $0.isFolder }) { f in
                            Text(f.name).tag(String?(f.id))
                        }
                    }
                }
            }
            .navigationTitle("New Playlist")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Create") {
                        if !name.trimmingCharacters(in: .whitespaces).isEmpty {
                            appState.createPlaylist(name: name, parentId: selectedParentId)
                            dismiss()
                        }
                    }
                    .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
        }
    }
}

struct AddToPlaylistSheet: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(AppState.self) private var appState
    let track: Track
    @State private var assignedPlaylistIds: Set<String> = []

    var regularPlaylists: [Playlist] {
        appState.playlists.filter { !$0.isFolder }
    }

    var body: some View {
        NavigationStack {
            List(regularPlaylists) { pl in
                HStack {
                    Label(pl.name, systemImage: "music.note.list")
                    Spacer()
                    let count = LocalDatabase.shared.getTrackCountForPlaylist(playlistId: pl.id)
                    Text("\(count)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .padding(.trailing, 8)

                    if assignedPlaylistIds.contains(pl.id) {
                        Image(systemName: "checkmark")
                            .foregroundStyle(.blue)
                            .fontWeight(.semibold)
                    }
                }
                .contentShape(Rectangle())
                .onTapGesture {
                    if assignedPlaylistIds.contains(pl.id) {
                        assignedPlaylistIds.remove(pl.id)
                        appState.removeTrackFromPlaylist(trackId: track.id, playlistId: pl.id)
                    } else {
                        assignedPlaylistIds.insert(pl.id)
                        appState.addTrackToPlaylist(trackId: track.id, playlistId: pl.id)
                    }
                }
            }
            .navigationTitle("Add to Playlist")
            .navigationBarTitleDisplayMode(.inline)
            .onAppear {
                assignedPlaylistIds = LocalDatabase.shared.getPlaylistIds(forTrackId: track.id)
            }
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }
}
