import SwiftUI

struct SyncQueueView: View {
    @Environment(AppState.self) private var appState
    @State private var showingSettings = false

    var body: some View {
        NavigationStack {
            List {
                Section("Status") {
                    HStack {
                        Text("Connection")
                        Spacer()
                        HStack(spacing: 6) {
                            Circle()
                                .fill(appState.syncErrorMessage == nil ? Color.green : Color.red)
                                .frame(width: 8, height: 8)
                            Text(appState.syncErrorMessage == nil ? "Connected to Turso" : "Error")
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                        }
                    }

                    if let lastSync = appState.lastSyncDate {
                        HStack {
                            Text("Last Synced")
                            Spacer()
                            Text(lastSync.formatted(date: .abbreviated, time: .shortened))
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                        }
                    }

                    HStack {
                        Text("Pending Events")
                        Spacer()
                        Text("\(appState.pendingCount)")
                            .font(.headline)
                            .foregroundStyle(appState.pendingCount > 0 ? .orange : .secondary)
                    }

                    Button {
                        Task { await appState.sync() }
                    } label: {
                        HStack {
                            Spacer()
                            if appState.isSyncing {
                                ProgressView()
                                    .padding(.trailing, 8)
                                Text("Syncing with Turso...")
                            } else {
                                Image(systemName: "arrow.triangle.2.circlepath")
                                Text("Sync with Cloud Now")
                            }
                            Spacer()
                        }
                    }
                    .disabled(appState.isSyncing)
                }

                if let err = appState.syncErrorMessage {
                    Section {
                        Text(err)
                            .font(.caption)
                            .foregroundStyle(.red)
                    }
                }

                Section {
                    if appState.changelogEvents.isEmpty {
                        Text("No events recorded yet.")
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(appState.changelogEvents) { event in
                            VStack(alignment: .leading, spacing: 4) {
                                HStack {
                                    Text(event.action)
                                        .font(.subheadline)
                                        .fontWeight(.bold)
                                    Spacer()
                                    Text(event.status)
                                        .font(.caption2)
                                        .fontWeight(.bold)
                                        .padding(.horizontal, 6)
                                        .padding(.vertical, 2)
                                        .background(event.statusColor.opacity(0.15))
                                        .foregroundStyle(event.statusColor)
                                        .clipShape(Capsule())
                                }

                                Text(event.payload)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(2)

                                HStack {
                                    Text("Created: \(event.formattedDate)")
                                        .font(.caption2)
                                        .foregroundStyle(.tertiary)
                                    if let applied = event.formattedProcessedDate {
                                        Spacer()
                                        Text("Applied: \(applied)")
                                            .font(.caption2)
                                            .foregroundStyle(.green)
                                    }
                                }

                                if let err = event.errorMessage, !err.isEmpty {
                                    Text("Error: \(err)")
                                        .font(.caption2)
                                        .foregroundStyle(.red)
                                }
                            }
                            .padding(.vertical, 4)
                        }
                        .onDelete { offsets in
                            appState.deleteChangelogEvents(at: offsets)
                        }
                    }
                } header: {
                    HStack {
                        Text("Changelog Queue")
                        Spacer()
                        if appState.changelogEvents.contains(where: { !$0.isPending }) {
                            Button("Clear Completed") {
                                appState.clearProcessedEvents()
                            }
                            .font(.caption)
                            .textCase(.none)
                        }
                    }
                }

                Section {
                    VStack(spacing: 4) {
                        Text("Rekordbox Companion")
                            .font(.footnote)
                            .fontWeight(.semibold)
                            .foregroundStyle(.secondary)
                        Text("Version \(appVersion)")
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    }
                    .frame(maxWidth: .infinity)
                    .listRowBackground(Color.clear)
                }
            }
            .navigationTitle("Sync & Queue")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showingSettings = true
                    } label: {
                        Image(systemName: "gearshape")
                    }
                }
            }
            .sheet(isPresented: $showingSettings) {
                SettingsSheet()
            }
        }
    }
}

struct SettingsSheet: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(AppState.self) private var appState

    @State private var dbURL: String = TursoConfig.databaseURL
    @State private var token: String = TursoConfig.authToken

    var body: some View {
        NavigationStack {
            Form {
                Section("Turso Cloud Configuration") {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Database URL")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        TextField("https://...", text: $dbURL)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                    }

                    VStack(alignment: .leading, spacing: 4) {
                        Text("Auth Token")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        SecureField("Token", text: $token)
                    }
                }

                Section {
                    Button("Reset to .env Defaults") {
                        TursoConfig.resetToDefaults()
                        dbURL = TursoConfig.databaseURL
                        token = TursoConfig.authToken
                    }
                    .foregroundStyle(.red)
                }

                Section("About") {
                    HStack {
                        Text("App Version")
                        Spacer()
                        Text(appVersion)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .navigationTitle("Turso Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        TursoConfig.databaseURL = dbURL
                        TursoConfig.authToken = token
                        Task { await appState.sync() }
                        dismiss()
                    }
                }
            }
        }
    }
}

fileprivate var appVersion: String {
    Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "—"
}
