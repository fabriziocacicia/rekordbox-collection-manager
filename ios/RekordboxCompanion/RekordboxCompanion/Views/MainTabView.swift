import SwiftUI

struct MainTabView: View {
    @Environment(AppState.self) private var appState

    var body: some View {
        TabView {
            TracksView()
                .tabItem {
                    Label("Tracks", systemImage: "music.note.list")
                }

            PlaylistsView()
                .tabItem {
                    Label("Playlists", systemImage: "folder")
                }

            TagsView()
                .tabItem {
                    Label("MyTags", systemImage: "tag")
                }

            SyncQueueView()
                .tabItem {
                    Label("Sync", systemImage: "arrow.triangle.2.circlepath")
                }
                .badge(appState.pendingCount > 0 ? "\(appState.pendingCount)" : nil)
        }
        .tint(.blue)
    }
}
