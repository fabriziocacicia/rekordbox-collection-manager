import SwiftUI

// MARK: - Flow Layout for Chips
struct FlowLayout: Layout {
    var spacing: CGFloat = 8

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? 0
        var height: CGFloat = 0
        var x: CGFloat = 0
        var y: CGFloat = 0
        var rowHeight: CGFloat = 0

        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if x + size.width > width && x > 0 {
                x = 0
                y += rowHeight + spacing
                rowHeight = 0
            }
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
        height = y + rowHeight
        return CGSize(width: width, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX
        var y = bounds.minY
        var rowHeight: CGFloat = 0

        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if x + size.width > bounds.maxX && x > bounds.minX {
                x = bounds.minX
                y += rowHeight + spacing
                rowHeight = 0
            }
            subview.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
    }
}

// MARK: - Reusable Removable Chip View
struct ChipView: View {
    let title: String
    var subtitle: String? = nil
    let systemImage: String
    let tintColor: Color
    var onRemove: () -> Void

    var body: some View {
        HStack(spacing: 5) {
            Image(systemName: systemImage)
                .font(.system(size: 10, weight: .bold))
                .foregroundStyle(tintColor)

            if let subtitle = subtitle, !subtitle.isEmpty {
                Text(subtitle)
                    .font(.caption2)
                    .fontWeight(.semibold)
                    .foregroundStyle(tintColor.opacity(0.85))
                Text("›")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }

            Text(title)
                .font(.caption)
                .fontWeight(.medium)
                .foregroundStyle(.primary)

            Button {
                withAnimation(.spring(response: 0.25, dampingFraction: 0.7)) {
                    onRemove()
                }
            } label: {
                Image(systemName: "xmark.circle.fill")
                    .font(.system(size: 13))
                    .foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 9)
        .padding(.vertical, 5)
        .background(tintColor.opacity(0.12))
        .clipShape(Capsule())
        .overlay(
            Capsule()
                .stroke(tintColor.opacity(0.28), lineWidth: 1)
        )
    }
}

// MARK: - Track Detail Sheet
struct TrackDetailSheet: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(AppState.self) private var appState

    let track: Track

    @State private var selectedTagIds: Set<String>
    @State private var selectedPlaylistIds: Set<String>
    private let initialPlaylistIds: Set<String>
    private let initialTagIds: Set<String>

    @State private var playlistSearchText = ""
    @State private var tagSearchText = ""

    private enum ActiveField: Hashable {
        case playlist
        case tag
    }
    @FocusState private var focusedField: ActiveField?

    init(track: Track) {
        self.track = track
        let tIds = LocalDatabase.shared.getTagIds(forTrackId: track.id)
        _selectedTagIds = State(initialValue: tIds)
        self.initialTagIds = tIds
        let pIds = LocalDatabase.shared.getPlaylistIds(forTrackId: track.id)
        _selectedPlaylistIds = State(initialValue: pIds)
        self.initialPlaylistIds = pIds
    }

    // Desktop parity: Target "Genre" and "Style" tag categories
    private let targetCategoryNames = ["Genre", "Style"]

    var categoryFolders: [Tag] {
        appState.tags.filter { tag in
            (tag.parentId == nil || tag.parentId == "root" || tag.parentId == "0" || tag.parentId == "") &&
            targetCategoryNames.contains { $0.caseInsensitiveCompare(tag.name) == .orderedSame }
        }.sorted { $0.seq < $1.seq }
    }

    var categoryFolderIds: Set<String> {
        Set(categoryFolders.map { $0.id })
    }

    func categoryName(for tag: Tag) -> String? {
        guard let pId = tag.parentId else { return nil }
        return categoryFolders.first(where: { $0.id == pId })?.name
    }

    // Playlists Helpers
    var allRegularPlaylists: [Playlist] {
        appState.playlists.filter { !$0.isFolder && !$0.isSmart }
    }

    var selectedPlaylistsList: [Playlist] {
        appState.playlists.filter { selectedPlaylistIds.contains($0.id) }
            .sorted { $0.name.localizedCompare($1.name) == .orderedAscending }
    }

    var availablePlaylists: [Playlist] {
        allRegularPlaylists.filter { !selectedPlaylistIds.contains($0.id) }
    }

    var matchingPlaylists: [Playlist] {
        if playlistSearchText.isEmpty {
            return availablePlaylists
        }
        let q = playlistSearchText.lowercased()
        return availablePlaylists.filter { $0.name.lowercased().contains(q) }
            .sorted { $0.name.localizedCompare($1.name) == .orderedAscending }
    }

    // Tags Helpers
    var allAvailableTags: [Tag] {
        appState.tags.filter { tag in
            guard let pId = tag.parentId else { return false }
            return categoryFolderIds.contains(pId) && !selectedTagIds.contains(tag.id)
        }
    }

    var selectedTagsList: [Tag] {
        appState.tags.filter { selectedTagIds.contains($0.id) }
            .sorted { ($0.name).localizedCompare($1.name) == .orderedAscending }
    }

    var matchingTags: [Tag] {
        if tagSearchText.isEmpty {
            return allAvailableTags
        }
        let q = tagSearchText.lowercased()
        return allAvailableTags.filter { tag in
            tag.name.lowercased().contains(q) || (categoryName(for: tag)?.lowercased().contains(q) == true)
        }.sorted { $0.name.localizedCompare($1.name) == .orderedAscending }
    }

    var body: some View {
        NavigationStack {
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(spacing: 20) {
                        // 1. Core Track Information
                        trackInformationSection

                        // 2. Playlists Chips & Searchable Section (Teal theme)
                        playlistsSection
                            .id("playlistSection")

                        // 3. MyTags Chips & Searchable Section (Purple theme)
                        myTagsSection
                            .id("tagSection")
                    }
                    .padding(.horizontal, 16)
                    .padding(.top, 16)
                    .padding(.bottom, focusedField != nil ? 340 : 32)
                }
                .scrollDismissesKeyboard(.interactively)
                .background(Color(.systemGroupedBackground))
                .onChange(of: tagSearchText) { _, newValue in
                    if !newValue.isEmpty {
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) {
                            withAnimation(.easeInOut(duration: 0.2)) {
                                proxy.scrollTo("tagSection", anchor: .top)
                            }
                        }
                    }
                }
                .onChange(of: playlistSearchText) { _, newValue in
                    if !newValue.isEmpty {
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) {
                            withAnimation(.easeInOut(duration: 0.2)) {
                                proxy.scrollTo("playlistSection", anchor: .top)
                            }
                        }
                    }
                }
                .onChange(of: focusedField) { _, newValue in
                    if let field = newValue {
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.12) {
                            withAnimation(.easeInOut(duration: 0.25)) {
                                switch field {
                                case .playlist:
                                    proxy.scrollTo("playlistSection", anchor: .top)
                                case .tag:
                                    proxy.scrollTo("tagSection", anchor: .top)
                                }
                            }
                        }
                    }
                }
            }
            .navigationTitle("Track Details")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") {
                        // 1. Update track tags if changed
                        if selectedTagIds != initialTagIds {
                            appState.updateTrackMetadata(
                                track: track,
                                title: track.title,
                                comment: track.comment,
                                rating: track.rating,
                                djPlayCount: track.djPlayCount,
                                tagIds: Array(selectedTagIds)
                            )
                        }

                        // 2. Apply playlist diffs
                        for pid in selectedPlaylistIds where !initialPlaylistIds.contains(pid) {
                            appState.addTrackToPlaylist(trackId: track.id, playlistId: pid)
                        }
                        for pid in initialPlaylistIds where !selectedPlaylistIds.contains(pid) {
                            appState.removeTrackFromPlaylist(trackId: track.id, playlistId: pid)
                        }

                        dismiss()
                    }
                    .fontWeight(.bold)
                }
            }
        }
    }

    // MARK: - Section 1: Track Information
    private var trackInformationSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Track Information")
                .font(.footnote)
                .fontWeight(.medium)
                .foregroundStyle(.secondary)
                .textCase(.uppercase)
                .padding(.horizontal, 4)

            VStack(spacing: 0) {
                infoRow(label: "Title", value: track.title.isEmpty ? "(Untitled)" : track.title, isBold: true)
                Divider().padding(.leading, 16)

                infoRow(label: "Artist", value: track.artist.isEmpty ? "Unknown Artist" : track.artist)
                Divider().padding(.leading, 16)

                if !track.album.isEmpty {
                    infoRow(label: "Album", value: track.album)
                    Divider().padding(.leading, 16)
                }

                infoRow(label: "Tempo (BPM)", value: track.formattedBpm, isMonospaced: true)
                Divider().padding(.leading, 16)

                infoRow(label: "Musical Key", value: track.keyName.isEmpty ? "—" : track.keyName, valueColor: .blue, isBold: true)
                Divider().padding(.leading, 16)

                infoRow(label: "DJ Play Count", value: "\(track.djPlayCount)", isMonospaced: true)
                Divider().padding(.leading, 16)

                infoRow(label: "Date Added", value: track.formattedDateAdded, isCaption: true)
            }
            .background(Color(.secondarySystemGroupedBackground))
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        }
    }

    private func infoRow(
        label: String,
        value: String,
        valueColor: Color = .primary,
        isBold: Bool = false,
        isMonospaced: Bool = false,
        isCaption: Bool = false
    ) -> some View {
        HStack {
            Text(label)
                .font(.subheadline)
                .foregroundStyle(.secondary)
            Spacer()
            Text(value)
                .font(isCaption ? .caption : .subheadline)
                .fontWeight(isBold ? .semibold : .regular)
                .monospacedDigit()
                .foregroundStyle(valueColor)
                .multilineTextAlignment(.trailing)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 11)
    }

    // MARK: - Section 2: Playlists
    private var playlistsSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Playlists (\(selectedPlaylistIds.count))")
                .font(.footnote)
                .fontWeight(.medium)
                .foregroundStyle(.secondary)
                .textCase(.uppercase)
                .padding(.horizontal, 4)

            VStack(alignment: .leading, spacing: 12) {
                // Chips area
                if selectedPlaylistsList.isEmpty {
                    Text("Not in any playlist")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .padding(.vertical, 2)
                } else {
                    FlowLayout(spacing: 8) {
                        ForEach(selectedPlaylistsList) { pl in
                            ChipView(
                                title: pl.name,
                                systemImage: "music.note.list",
                                tintColor: .teal
                            ) {
                                _ = selectedPlaylistIds.remove(pl.id)
                            }
                        }
                    }
                    .padding(.vertical, 2)
                }

                // Search input & browse menu
                HStack(spacing: 8) {
                    HStack(spacing: 6) {
                        Image(systemName: "magnifyingglass")
                            .foregroundStyle(.secondary)
                            .font(.caption)
                        TextField("Search playlist to add...", text: $playlistSearchText)
                            .font(.subheadline)
                            .focused($focusedField, equals: .playlist)
                            .submitLabel(.done)
                            .onSubmit {
                                if let first = matchingPlaylists.first, matchingPlaylists.count == 1 {
                                    withAnimation {
                                        _ = selectedPlaylistIds.insert(first.id)
                                        playlistSearchText = ""
                                    }
                                }
                                focusedField = nil
                            }

                        if !playlistSearchText.isEmpty {
                            Button {
                                playlistSearchText = ""
                            } label: {
                                Image(systemName: "xmark.circle.fill")
                                    .foregroundStyle(.secondary)
                                    .font(.caption)
                            }
                        }
                    }
                    .padding(.horizontal, 10)
                    .padding(.vertical, 8)
                    .background(Color(.secondarySystemBackground))
                    .clipShape(RoundedRectangle(cornerRadius: 8))

                    Menu {
                        ForEach(availablePlaylists.sorted { $0.name.localizedCompare($1.name) == .orderedAscending }) { pl in
                            Button {
                                withAnimation {
                                    _ = selectedPlaylistIds.insert(pl.id)
                                }
                            } label: {
                                Label(pl.name, systemImage: "music.note.list")
                            }
                        }
                    } label: {
                        Image(systemName: "plus.circle.fill")
                            .font(.title3)
                            .foregroundStyle(availablePlaylists.isEmpty ? Color.secondary : Color.teal)
                    }
                    .disabled(availablePlaylists.isEmpty)

                    if focusedField == .playlist {
                        Button("Done") {
                            focusedField = nil
                        }
                        .font(.subheadline)
                        .fontWeight(.semibold)
                        .foregroundStyle(.teal)
                        .transition(.move(edge: .trailing).combined(with: .opacity))
                    }
                }

                // Search suggestions dropdown
                if !playlistSearchText.isEmpty {
                    VStack(alignment: .leading, spacing: 0) {
                        if matchingPlaylists.isEmpty {
                            Text("No playlists matching \"\(playlistSearchText)\"")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .padding(12)
                        } else {
                            ForEach(matchingPlaylists.prefix(6)) { pl in
                                Button {
                                    withAnimation {
                                        _ = selectedPlaylistIds.insert(pl.id)
                                        playlistSearchText = ""
                                    }
                                } label: {
                                    HStack {
                                        Label(pl.name, systemImage: "music.note.list")
                                            .font(.subheadline)
                                            .foregroundStyle(.primary)
                                        Spacer()
                                        Image(systemName: "plus")
                                            .font(.caption)
                                            .fontWeight(.bold)
                                            .foregroundStyle(.teal)
                                    }
                                    .contentShape(Rectangle())
                                    .padding(.horizontal, 12)
                                    .padding(.vertical, 9)
                                }
                                .buttonStyle(.plain)

                                if pl.id != matchingPlaylists.prefix(6).last?.id {
                                    Divider()
                                }
                            }

                            if matchingPlaylists.count > 6 {
                                Text("+ \(matchingPlaylists.count - 6) more (keep typing to refine)")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                                    .padding(.horizontal, 12)
                                    .padding(.vertical, 6)
                            }
                        }
                    }
                    .background(Color(.secondarySystemBackground))
                    .clipShape(RoundedRectangle(cornerRadius: 10))
                    .overlay(
                        RoundedRectangle(cornerRadius: 10)
                            .stroke(Color.teal.opacity(0.35), lineWidth: 1)
                    )
                }
            }
            .padding(16)
            .background(Color(.secondarySystemGroupedBackground))
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            .animation(.easeInOut(duration: 0.2), value: focusedField)
        }
    }

    // MARK: - Section 3: MyTags
    private var myTagsSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("MyTags (\(selectedTagIds.count))")
                .font(.footnote)
                .fontWeight(.medium)
                .foregroundStyle(.secondary)
                .textCase(.uppercase)
                .padding(.horizontal, 4)

            VStack(alignment: .leading, spacing: 12) {
                // Chips area
                if selectedTagsList.isEmpty {
                    Text("No MyTags assigned")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .padding(.vertical, 2)
                } else {
                    FlowLayout(spacing: 8) {
                        ForEach(selectedTagsList) { tag in
                            let folder = categoryName(for: tag)
                            ChipView(
                                title: tag.name,
                                subtitle: folder,
                                systemImage: "tag.fill",
                                tintColor: .purple
                            ) {
                                _ = selectedTagIds.remove(tag.id)
                            }
                        }
                    }
                    .padding(.vertical, 2)
                }

                // Search input & browse menu
                HStack(spacing: 8) {
                    HStack(spacing: 6) {
                        Image(systemName: "magnifyingglass")
                            .foregroundStyle(.secondary)
                            .font(.caption)
                        TextField("Search tag to add...", text: $tagSearchText)
                            .font(.subheadline)
                            .focused($focusedField, equals: .tag)
                            .submitLabel(.done)
                            .onSubmit {
                                if let first = matchingTags.first, matchingTags.count == 1 {
                                    withAnimation {
                                        _ = selectedTagIds.insert(first.id)
                                        tagSearchText = ""
                                    }
                                }
                                focusedField = nil
                            }

                        if !tagSearchText.isEmpty {
                            Button {
                                tagSearchText = ""
                            } label: {
                                Image(systemName: "xmark.circle.fill")
                                    .foregroundStyle(.secondary)
                                    .font(.caption)
                            }
                        }
                    }
                    .padding(.horizontal, 10)
                    .padding(.vertical, 8)
                    .background(Color(.secondarySystemBackground))
                    .clipShape(RoundedRectangle(cornerRadius: 8))

                    Menu {
                        ForEach(categoryFolders) { folder in
                            let tagsInFolder = allAvailableTags.filter { $0.parentId == folder.id }.sorted { $0.seq < $1.seq }
                            if !tagsInFolder.isEmpty {
                                Section(folder.name) {
                                    ForEach(tagsInFolder) { tag in
                                        Button {
                                            withAnimation {
                                                _ = selectedTagIds.insert(tag.id)
                                            }
                                        } label: {
                                            Label(tag.name, systemImage: "tag.fill")
                                        }
                                    }
                                }
                            }
                        }
                    } label: {
                        Image(systemName: "plus.circle.fill")
                            .font(.title3)
                            .foregroundStyle(allAvailableTags.isEmpty ? Color.secondary : Color.purple)
                    }
                    .disabled(allAvailableTags.isEmpty)

                    if focusedField == .tag {
                        Button("Done") {
                            focusedField = nil
                        }
                        .font(.subheadline)
                        .fontWeight(.semibold)
                        .foregroundStyle(.purple)
                        .transition(.move(edge: .trailing).combined(with: .opacity))
                    }
                }

                // Search suggestions dropdown
                if !tagSearchText.isEmpty {
                    VStack(alignment: .leading, spacing: 0) {
                        if matchingTags.isEmpty {
                            Text("No tags matching \"\(tagSearchText)\"")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .padding(12)
                        } else {
                            ForEach(matchingTags.prefix(6)) { tag in
                                let folder = categoryName(for: tag)
                                Button {
                                    withAnimation {
                                        _ = selectedTagIds.insert(tag.id)
                                        tagSearchText = ""
                                    }
                                } label: {
                                    HStack {
                                        Label {
                                            HStack(spacing: 4) {
                                                if let f = folder {
                                                    Text(f)
                                                        .font(.caption2)
                                                        .foregroundStyle(.secondary)
                                                    Text("›")
                                                        .font(.caption2)
                                                        .foregroundStyle(.secondary)
                                                }
                                                Text(tag.name)
                                                    .font(.subheadline)
                                                    .foregroundStyle(.primary)
                                            }
                                        } icon: {
                                            Image(systemName: "tag.fill")
                                                .foregroundStyle(.purple)
                                        }
                                        Spacer()
                                        Image(systemName: "plus")
                                            .font(.caption)
                                            .fontWeight(.bold)
                                            .foregroundStyle(.purple)
                                    }
                                    .contentShape(Rectangle())
                                    .padding(.horizontal, 12)
                                    .padding(.vertical, 9)
                                }
                                .buttonStyle(.plain)

                                if tag.id != matchingTags.prefix(6).last?.id {
                                    Divider()
                                }
                            }

                            if matchingTags.count > 6 {
                                Text("+ \(matchingTags.count - 6) more (keep typing to refine)")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                                    .padding(.horizontal, 12)
                                    .padding(.vertical, 6)
                            }
                        }
                    }
                    .background(Color(.secondarySystemBackground))
                    .clipShape(RoundedRectangle(cornerRadius: 10))
                    .overlay(
                        RoundedRectangle(cornerRadius: 10)
                            .stroke(Color.purple.opacity(0.35), lineWidth: 1)
                    )
                }
            }
            .padding(16)
            .background(Color(.secondarySystemGroupedBackground))
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            .animation(.easeInOut(duration: 0.2), value: focusedField)
        }
    }
}
