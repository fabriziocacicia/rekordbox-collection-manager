"""Tags Management & Tracks View Screen.

Provides a unified master-detail split layout for exploring Rekordbox My Tags,
specifically categorized into Genre and Style folders. Selecting any tag directly displays its
tracks table with full metadata, format badges, and infinite virtual scrolling,
as well as supporting in-place track details and universal deep-linking.
"""

import os
from typing import Any, Dict, List, Optional
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.engine import Engine

from rekordbox_collection_manager.gui.theme import (
    COLOR_BG_CARD,
    COLOR_BG_DARK,
    COLOR_BG_PANEL,
    COLOR_BORDER,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_SECONDARY,
)
from rekordbox_collection_manager.gui.track_detail_view import TrackDetailView
from rekordbox_collection_manager.gui.track_table import (
    DEFAULT_COLLECTION_COLUMNS,
    TrackColumn,
    TrackTableConfig,
    TracksTableWidget,
)
from rekordbox_collection_manager.gui.workers import (
    TagTracksWorker,
    TagsTreeLoaderWorker,
)
from rekordbox_collection_manager.utils import open_in_folder, reveal_in_file_manager


class TagsView(QWidget):
    """Primary view displaying the tags hierarchy (Genre & Style) and tag tracks table."""

    notify_status = Signal(str)
    open_history_session_requested = Signal(str)
    open_playlist_requested = Signal(str)
    open_tag_requested = Signal(str)
    replace_file_requested = Signal(dict)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.engine: Optional[Engine] = None
        self.db_path: Optional[str] = None
        self.tags_tree_data: List[Dict[str, Any]] = []
        self.current_tag: Optional[Dict[str, Any]] = None
        self.records: List[Dict[str, Any]] = []
        self.total_tag_tracks: int = 0
        self.show_deleted: bool = False

        self.current_sort_field: str = "track_no"
        self.current_sort_order: Qt.SortOrder = Qt.SortOrder.AscendingOrder

        self.active_loader_worker: Optional[TagsTreeLoaderWorker] = None
        self.active_tracks_worker: Optional[TagTracksWorker] = None

        # Debounce timer for in-tag search
        self.search_debounce_timer = QTimer(self)
        self.search_debounce_timer.setSingleShot(True)
        self.search_debounce_timer.setInterval(250)
        self.search_debounce_timer.timeout.connect(self._execute_tracks_search)

        self._init_ui()

    def _init_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Main Master-Detail Splitter
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.setHandleWidth(4)

        # ======================================================================
        # 1. Left Master Pane: Tag Hierarchy Explorer (Genre & Style)
        # ======================================================================
        left_container = QWidget()
        left_container.setMinimumWidth(220)
        left_layout = QVBoxLayout(left_container)
        left_layout.setContentsMargins(14, 14, 10, 14)
        left_layout.setSpacing(10)

        # Left Header (Title + Expand/Collapse tools)
        left_header = QHBoxLayout()
        left_header.setSpacing(6)

        self.tree_header_label = QLabel("TAGS")
        self.tree_header_label.setStyleSheet(
            f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 0.8px;"
        )
        left_header.addWidget(self.tree_header_label, stretch=1)

        self.btn_expand_all = QPushButton("▼")
        self.btn_expand_all.setToolTip("Expand all categories")
        self.btn_expand_all.setFixedSize(24, 24)
        self.btn_expand_all.setStyleSheet("font-size: 10px; padding: 0;")
        self.btn_expand_all.clicked.connect(lambda: self.tree.expandAll())
        left_header.addWidget(self.btn_expand_all)

        self.btn_collapse_all = QPushButton("▲")
        self.btn_collapse_all.setToolTip("Collapse all categories")
        self.btn_collapse_all.setFixedSize(24, 24)
        self.btn_collapse_all.setStyleSheet("font-size: 10px; padding: 0;")
        self.btn_collapse_all.clicked.connect(lambda: self.tree.collapseAll())
        left_header.addWidget(self.btn_collapse_all)

        left_layout.addLayout(left_header)

        # Filter Input for tags
        self.tag_filter_input = QLineEdit()
        self.tag_filter_input.setPlaceholderText("🔍 Filter tags...")
        self.tag_filter_input.setClearButtonEnabled(True)
        self.tag_filter_input.textChanged.connect(self._on_tag_filter_text_changed)
        left_layout.addWidget(self.tag_filter_input)

        # Tag Hierarchy Tree Widget
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(16)
        self.tree.setAnimated(True)
        self.tree.setStyleSheet(
            f"QTreeWidget {{ "
            f"  background-color: {COLOR_BG_PANEL}; "
            f"  border: 1px solid {COLOR_BORDER}; "
            f"  border-radius: 6px; "
            f"  padding: 4px; "
            f"}} "
            f"QTreeWidget::item {{ "
            f"  padding: 5px 6px; "
            f"  border-radius: 4px; "
            f"}} "
            f"QTreeWidget::item:selected {{ "
            f"  background-color: #0f3a63; "
            f"  color: #ffffff; "
            f"}}"
        )
        self.tree.itemClicked.connect(self._on_tree_item_clicked)
        self.tree.currentItemChanged.connect(self._on_tree_current_item_changed)
        left_layout.addWidget(self.tree, stretch=1)

        self.splitter.addWidget(left_container)

        # ======================================================================
        # 2. Right Detail Pane: Tag Tracks & In-place Detail
        # ======================================================================
        self.stack = QStackedWidget()

        # ----------------------------------------------------------------------
        # Page 0: Tag Tracks Table Screen
        # ----------------------------------------------------------------------
        self.tracks_page = QWidget()
        right_layout = QVBoxLayout(self.tracks_page)
        right_layout.setContentsMargins(10, 14, 14, 14)
        right_layout.setSpacing(10)

        # Top Header Bar: Title, breadcrumb, search, limit, and action buttons
        header_card = QFrame()
        header_card.setObjectName("tagHeaderCard")
        header_card.setStyleSheet(
            f"QFrame#tagHeaderCard {{ "
            f"  background-color: {COLOR_BG_PANEL}; "
            f"  border: 1px solid {COLOR_BORDER}; "
            f"  border-radius: 8px; "
            f"  padding: 10px 14px; "
            f"}}"
        )
        header_layout = QVBoxLayout(header_card)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        # Row 1: Title and breadcrumbs
        title_row = QHBoxLayout()
        title_row.setSpacing(8)

        self.tag_title_label = QLabel("Select a Tag")
        self.tag_title_label.setStyleSheet(
            f"color: {COLOR_TEXT_PRIMARY}; font-size: 16px; font-weight: 700; letter-spacing: -0.2px;"
        )
        title_row.addWidget(self.tag_title_label)

        self.tag_meta_label = QLabel("")
        self.tag_meta_label.setStyleSheet(
            f"color: {COLOR_TEXT_SECONDARY}; font-size: 12px;"
        )
        title_row.addWidget(self.tag_meta_label)
        title_row.addStretch(1)

        self.btn_copy_tracklist = QPushButton("📋 Copy Tracklist")
        self.btn_copy_tracklist.setToolTip("Copy formatted tag tracklist to clipboard")
        self.btn_copy_tracklist.clicked.connect(self._on_copy_tracklist)
        title_row.addWidget(self.btn_copy_tracklist)

        header_layout.addLayout(title_row)

        # Row 2: In-tag search and dynamic limit
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)

        self.track_search_input = QLineEdit()
        self.track_search_input.setPlaceholderText("🔍 Search tracks in tag (title, artist, key, filename)...")
        self.track_search_input.setClearButtonEnabled(True)
        self.track_search_input.textChanged.connect(self._on_track_search_text_changed)
        filter_row.addWidget(self.track_search_input, stretch=3)

        self.limit_combo = QComboBox()
        self.limit_combo.currentIndexChanged.connect(lambda _: self._execute_tracks_search())
        self.update_limit_options(0)
        filter_row.addWidget(self.limit_combo, stretch=1)

        self.btn_search = QPushButton("Filter")
        self.btn_search.clicked.connect(self._execute_tracks_search)
        filter_row.addWidget(self.btn_search)

        header_layout.addLayout(filter_row)
        right_layout.addWidget(header_card)

        # Tracks Table
        table_config = TrackTableConfig(
            columns=list(DEFAULT_COLLECTION_COLUMNS),
            index_mode="track_no",
            index_header_tooltip="Track sequence number in tag",
            sorting_mode="server",
            enable_context_menu=True,
            enable_finder_clicks=True,
        )
        self.table = TracksTableWidget(config=table_config, parent=self)
        self.table.sort_requested.connect(self._on_table_sort_requested)
        self.table.status_message.connect(self._on_table_status_message)
        self.table.folder_clicked.connect(self._on_open_folder)
        self.table.file_clicked.connect(self._on_reveal_file)
        self.table.track_detail_requested.connect(self._open_track_detail)
        self.table.replace_file_requested.connect(self.replace_file_requested.emit)
        self.table.open_playlist_requested.connect(self.open_playlist_requested.emit)
        self.table.open_tag_requested.connect(self.open_tag_by_id_or_name)
        right_layout.addWidget(self.table, stretch=1)

        # Status Bar
        self.status_label = QLabel("Select a tag to view tracks.")
        self.status_label.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-size: 11px;")
        right_layout.addWidget(self.status_label)

        self.stack.addWidget(self.tracks_page)

        # ----------------------------------------------------------------------
        # Page 1: Track Details View Screen (In-Place)
        # ----------------------------------------------------------------------
        self.detail_view = TrackDetailView(parent=self, back_button_text="← Back to Tag")
        self.detail_view.back_clicked.connect(self._on_back_from_detail)
        self.detail_view.open_history_session_requested.connect(self.open_history_session_requested.emit)
        self.detail_view.open_playlist_requested.connect(self.open_playlist_requested.emit)
        self.detail_view.open_tag_requested.connect(self.open_tag_by_id_or_name)
        self.detail_view.status_message.connect(self._on_table_status_message)
        self.detail_view.track_changed.connect(self._on_detail_track_changed)
        self.detail_view.replace_file_requested.connect(self.replace_file_requested.emit)
        self.stack.addWidget(self.detail_view)

        self.splitter.addWidget(self.stack)

        # Set default splitter proportions (280px left, remainder right)
        self.splitter.setSizes([280, 820])
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)

        root_layout.addWidget(self.splitter)

    # ==========================================================================
    # Engine & Data Initialization
    # ==========================================================================
    def set_engine(self, engine: Engine, db_path: Optional[str] = None) -> None:
        """Assign active database engine and initiate asynchronous tags tree load."""
        self.engine = engine
        self.db_path = db_path
        self._load_tags_tree()

    def _load_tags_tree(self) -> None:
        """Fetch Genre & Style tags hierarchy in background thread."""
        if not self.engine:
            return

        if self.active_loader_worker and self.active_loader_worker.isRunning():
            try:
                self.active_loader_worker.success.disconnect()
                self.active_loader_worker.error.disconnect()
            except Exception:
                pass
            self.active_loader_worker.wait(50)

        self.tree_header_label.setText("TAGS (Loading...)")
        self.active_loader_worker = TagsTreeLoaderWorker(
            self.engine,
            include_deleted=self.show_deleted,
            category_names=("Genre", "Style"),
            parent=self,
        )
        self.active_loader_worker.success.connect(self._on_tree_loaded)
        self.active_loader_worker.error.connect(self._on_tree_error)
        self.active_loader_worker.start()

    def _on_tree_loaded(self, tree_data: List[Dict[str, Any]]) -> None:
        self.tags_tree_data = tree_data
        total_count = self._count_total_tags(tree_data)
        self.tree_header_label.setText(f"TAGS ({total_count})")
        self._populate_tree()

        # If a tag was previously selected or deep-linked, restore it; otherwise select first standard tag
        if self.current_tag:
            found = self.open_tag_by_id_or_name(self.current_tag.get("id", ""))
            if not found:
                self._select_first_tag()
        else:
            self._select_first_tag()

    def _on_tree_error(self, err_msg: str) -> None:
        self.tree_header_label.setText("TAGS (Error)")
        self.status_label.setText(f"❌ Error loading tags: {err_msg}")
        self.notify_status.emit(f"Error loading tags: {err_msg}")

    def _count_total_tags(self, nodes: List[Dict[str, Any]]) -> int:
        count = 0
        for n in nodes:
            if n.get("attribute") != 1:  # Count individual tags, not category folders
                count += 1
            if n.get("children"):
                count += self._count_total_tags(n["children"])
        return count

    # ==========================================================================
    # Tree Population & Filtering
    # ==========================================================================
    def _populate_tree(self) -> None:
        """Build QTreeWidgetItem hierarchy from tags_tree_data."""
        self.tree.blockSignals(True)
        self.tree.clear()

        def add_nodes(parent_item: Optional[QTreeWidgetItem], nodes: List[Dict[str, Any]]) -> None:
            for node in nodes:
                attr = node.get("attribute", 0)
                name = node.get("name", "Untitled")
                track_cnt = node.get("track_count", 0)

                item = QTreeWidgetItem()
                if attr == 1:
                    # Category Folder (Genre, Style)
                    item.setText(0, f"📁 {name}")
                    item.setToolTip(0, f"Category: {name}")
                    font = item.font(0)
                    font.setBold(True)
                    item.setFont(0, font)
                    item.setData(0, Qt.ItemDataRole.UserRole, node)
                    if parent_item:
                        parent_item.addChild(item)
                    else:
                        self.tree.addTopLevelItem(item)
                    # Automatically expand top-level categories
                    item.setExpanded(True)
                    if node.get("children"):
                        add_nodes(item, node["children"])
                else:
                    # Individual Tag
                    cnt_str = f"({track_cnt})" if track_cnt > 0 else "(0)"
                    item.setText(0, f"🏷️ {name}  {cnt_str}")
                    item.setToolTip(0, f"Tag: {name}\nTracks: {track_cnt}")
                    item.setData(0, Qt.ItemDataRole.UserRole, node)
                    if parent_item:
                        parent_item.addChild(item)
                    else:
                        self.tree.addTopLevelItem(item)

        add_nodes(None, self.tags_tree_data)
        self.tree.blockSignals(False)

        # Apply any active text filter
        filter_text = self.tag_filter_input.text().strip()
        if filter_text:
            self._apply_tag_filter(filter_text)

    def _on_tag_filter_text_changed(self, text: str) -> None:
        self._apply_tag_filter(text.strip())

    def _apply_tag_filter(self, query: str) -> None:
        """Filter tree items by tag name, expanding ancestors of matching items."""
        q_lower = query.lower()
        if not q_lower:
            # Unhide all items
            it = QTreeWidgetItemIterator(self.tree)
            while it.value():
                it.value().setHidden(False)
                it += 1
            return

        def filter_item(item: QTreeWidgetItem) -> bool:
            node = item.data(0, Qt.ItemDataRole.UserRole) or {}
            name_matches = q_lower in node.get("name", "").lower()
            child_matches = False

            for i in range(item.childCount()):
                child = item.child(i)
                if filter_item(child):
                    child_matches = True

            visible = name_matches or child_matches
            item.setHidden(not visible)
            if visible and child_matches:
                item.setExpanded(True)
            return visible

        for i in range(self.tree.topLevelItemCount()):
            filter_item(self.tree.topLevelItem(i))

    def _select_first_tag(self) -> None:
        """Find and select the first tag in the tree."""
        it = QTreeWidgetItemIterator(self.tree)
        while it.value():
            item = it.value()
            node = item.data(0, Qt.ItemDataRole.UserRole)
            if node and node.get("attribute") != 1:  # Not a category folder
                self.tree.setCurrentItem(item)
                self.load_tag(node)
                break
            it += 1

    # ==========================================================================
    # Tree Interaction & Tag Loading
    # ==========================================================================
    def _on_tree_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        node = item.data(0, Qt.ItemDataRole.UserRole)
        if not node:
            return
        if node.get("attribute") == 1:
            # Clicked a category folder: toggle expand/collapse
            item.setExpanded(not item.isExpanded())
            self._show_folder_summary(node)
        else:
            self.load_tag(node)

    def _on_tree_current_item_changed(
        self, current: Optional[QTreeWidgetItem], previous: Optional[QTreeWidgetItem]
    ) -> None:
        if not current:
            return
        node = current.data(0, Qt.ItemDataRole.UserRole)
        if node and node.get("attribute") != 1:
            self.load_tag(node)

    def _show_folder_summary(self, folder_node: Dict[str, Any]) -> None:
        """Display informative summary when a category folder header is selected."""
        name = folder_node.get("name", "Category")
        child_count = len(folder_node.get("children", []))
        self.tag_title_label.setText(f"📁 {name}")
        self.tag_meta_label.setText(f"Category containing {child_count} tag(s)")
        self.records = []
        self.table.set_tracks([])
        self.status_label.setText("Select a tag inside this category to view its tracks.")

    def load_tag(self, tag_dict: Dict[str, Any]) -> None:
        """Switch to and load the specified tag's tracks."""
        self.current_tag = tag_dict
        name = tag_dict.get("name", "Untitled")

        # Set title
        self.tag_title_label.setText(f"🏷️ {name}")
        self.tag_meta_label.setText("Loading tracks...")

        # Reset search input for fresh tag view
        self.track_search_input.blockSignals(True)
        self.track_search_input.clear()
        self.track_search_input.blockSignals(False)

        # Reset stack to table view
        self.stack.setCurrentIndex(0)

        # Run query
        self._execute_tracks_search()

    # ==========================================================================
    # Tracks Query & Table Population
    # ==========================================================================
    def update_limit_options(self, total_tracks: int) -> None:
        """Dynamically configure limit dropdown identical to TracksView logic."""
        self.total_tag_tracks = total_tracks
        self.limit_combo.blockSignals(True)
        self.limit_combo.clear()

        if total_tracks <= 2500:
            self.limit_combo.setVisible(False)
            self.limit_combo.addItem("All Tracks", userData=0)
            self.limit_combo.setCurrentIndex(0)
        else:
            self.limit_combo.setVisible(True)
            limit_val = 2500
            while limit_val < total_tracks:
                self.limit_combo.addItem(f"Limit: {limit_val}", userData=limit_val)
                limit_val += 2500
            self.limit_combo.addItem("All Tracks", userData=0)
            self.limit_combo.setCurrentIndex(0)

        self.limit_combo.blockSignals(False)

    def _on_track_search_text_changed(self, text: str) -> None:
        self.search_debounce_timer.start()

    def _on_table_sort_requested(self, sort_field: str, order: Qt.SortOrder) -> None:
        self.current_sort_field = sort_field
        self.current_sort_order = order
        self._execute_tracks_search()

    def _execute_tracks_search(self) -> None:
        """Run non-blocking worker to query tracks for active tag."""
        if not self.engine or not self.current_tag:
            return

        tag_id = self.current_tag.get("id")
        if not tag_id:
            return

        query = self.track_search_input.text().strip()

        # Limit
        if self.limit_combo.isHidden():
            limit = None
        else:
            limit_data = self.limit_combo.currentData()
            limit = int(limit_data) if (limit_data is not None and limit_data > 0) else None

        sort_order_str = "desc" if self.current_sort_order == Qt.SortOrder.DescendingOrder else "asc"

        if self.active_tracks_worker and self.active_tracks_worker.isRunning():
            try:
                self.active_tracks_worker.success.disconnect()
                self.active_tracks_worker.error.disconnect()
            except Exception:
                pass
            self.active_tracks_worker.wait(50)

        self.status_label.setText("Loading tag tracks...")
        self.active_tracks_worker = TagTracksWorker(
            self.engine,
            tag_id=str(tag_id),
            query=query,
            limit=limit,
            sort_by=self.current_sort_field,
            sort_order=sort_order_str,
            parent=self,
        )
        self.active_tracks_worker.success.connect(self._on_tracks_loaded)
        self.active_tracks_worker.error.connect(self._on_tracks_error)
        self.active_tracks_worker.start()

    def _on_tracks_loaded(self, tag_id: str, records: List[Dict[str, Any]]) -> None:
        if not self.current_tag or str(self.current_tag.get("id")) != str(tag_id):
            return

        self.records = records
        self.update_limit_options(len(records))
        self.table.set_tracks(records)

        # Calculate total duration
        total_seconds = sum(int(t.get("duration_sec") or 0) for t in records)
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        duration_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"

        cnt = len(records)
        name = self.current_tag.get("name", "")
        self.tag_meta_label.setText(f"{cnt} tracks • {duration_str}")

        query = self.track_search_input.text().strip()
        if query:
            self.status_label.setText(
                f"Showing {cnt} tracks matching '{query}' in '{name}' ({duration_str})"
            )
        else:
            self.status_label.setText(
                f"Showing {cnt} tracks in '{name}' ({duration_str})"
            )

    def _on_tracks_error(self, err_msg: str) -> None:
        self.status_label.setText(f"❌ Error loading tag tracks: {err_msg}")
        self.notify_status.emit(f"Error loading tag tracks: {err_msg}")

    # ==========================================================================
    # Track Detail & Navigation Interactions
    # ==========================================================================
    def _open_track_detail(self, track: Dict[str, Any]) -> None:
        """Switch right pane to in-place TrackDetailView for the selected track."""
        if not self.engine or not track:
            return

        track_id = track.get("id") or track.get("content_id")
        curr_idx = 0
        for idx, r in enumerate(self.records):
            if (r.get("id") or r.get("content_id")) == track_id:
                curr_idx = idx
                break

        tag_name = self.current_tag.get("name") if self.current_tag else "Tag"
        self.detail_view.load_track(
            engine=self.engine,
            track_data=track,
            db_path=self.db_path,
            tracks_list=self.records,
            current_index=curr_idx,
            back_button_text=f"← Back to {tag_name}",
        )
        self.stack.setCurrentIndex(1)

    def _on_back_from_detail(self) -> None:
        """Return to tag tracks table list."""
        self.stack.setCurrentIndex(0)

    def _on_detail_track_changed(self, idx: int, track: Dict[str, Any]) -> None:
        """Update and scroll table when detail view navigates."""
        if 0 <= idx < len(self.records):
            self.records[idx].update(track)
            if "playlists" in track:
                self.table.update_track_playlists(idx, track["playlists"])
        if 0 <= idx < self.table.rowCount():
            self.table.selectRow(idx)
            self.table.scrollToRow(idx)

    def _on_open_selected_track_detail(self) -> None:
        """Open detail view for currently selected row."""
        selected_rows = self.table.selected_rows()
        if not selected_rows:
            msg = "⚠️ Select a track in the table to view details."
            self.status_label.setText(msg)
            self.notify_status.emit(msg)
            return
        row = selected_rows[0]
        if 0 <= row < len(self.records):
            self._open_track_detail(self.records[row])

    def _on_copy_tracklist(self) -> None:
        """Copy formatted tracklist to system clipboard."""
        self.table.copy_tracklist_to_clipboard(self.records)

    def _on_table_status_message(self, msg: str) -> None:
        self.status_label.setText(msg)
        self.notify_status.emit(msg)

    def _on_open_folder(self, folder_path: str) -> None:
        if not folder_path or folder_path == "—":
            msg = "⚠️ No folder path available."
            self.status_label.setText(msg)
            self.notify_status.emit(msg)
            return
        if folder_path.startswith("apple-music:") or folder_path.startswith("spotify:"):
            msg = f"Streaming track URI: {folder_path}"
            self.status_label.setText(msg)
            self.notify_status.emit(msg)
            return

        success = open_in_folder(folder_path)
        if success:
            msg = f"📂 Opened folder: {os.path.basename(folder_path) or folder_path}"
        else:
            msg = f"❌ Folder not found on disk: {folder_path}"
        self.status_label.setText(msg)
        self.notify_status.emit(msg)

    def _on_reveal_file(self, file_path: str) -> None:
        if not file_path or file_path == "—":
            msg = "⚠️ No file path available."
            self.status_label.setText(msg)
            self.notify_status.emit(msg)
            return
        if file_path.startswith("apple-music:") or file_path.startswith("spotify:"):
            msg = f"Streaming track URI: {file_path}"
            self.status_label.setText(msg)
            self.notify_status.emit(msg)
            return

        success = reveal_in_file_manager(file_path)
        if success:
            msg = f"🔍 Revealed in Finder: {os.path.basename(file_path)}"
        else:
            folder = os.path.dirname(file_path) if os.path.splitext(file_path)[1] else file_path
            if folder and open_in_folder(folder):
                msg = f"⚠️ File missing on disk, opened folder: {os.path.basename(folder)}"
            else:
                msg = f"❌ File not found on disk: {file_path}"
        self.status_label.setText(msg)
        self.notify_status.emit(msg)

    # ==========================================================================
    # Public Deep-Linking Interface
    # ==========================================================================
    def open_tag_by_id_or_name(self, tag_id_or_name: str) -> bool:
        """Find and select tag by ID or Name, expanding parent categories.

        Handles integer signed/unsigned conversions to guarantee matches.
        Returns True if tag was found and opened; False otherwise.
        """
        if not tag_id_or_name:
            return False

        q_str = str(tag_id_or_name).strip().lower()
        # Collect candidate matching strings
        match_keys = {q_str}
        try:
            num = int(tag_id_or_name)
            match_keys.add(str(num))
            if num < 0:
                match_keys.add(str(num + (1 << 32)))
            elif num >= (1 << 31):
                match_keys.add(str(num - (1 << 32)))
        except (ValueError, TypeError):
            pass

        it = QTreeWidgetItemIterator(self.tree)
        target_item: Optional[QTreeWidgetItem] = None
        target_node: Optional[Dict[str, Any]] = None

        while it.value():
            item = it.value()
            node = item.data(0, Qt.ItemDataRole.UserRole)
            if node and node.get("attribute") != 1:  # Only match tags, not category folders
                n_id = str(node.get("id", "")).strip().lower()
                n_name = str(node.get("name", "")).strip().lower()

                # Generate node keys
                node_keys = {n_id, n_name}
                try:
                    num = int(node.get("id"))
                    node_keys.add(str(num))
                    if num < 0:
                        node_keys.add(str(num + (1 << 32)))
                    elif num >= (1 << 31):
                        node_keys.add(str(num - (1 << 32)))
                except (ValueError, TypeError):
                    pass

                if match_keys & node_keys or q_str == n_name:
                    target_item = item
                    target_node = node
                    break
            it += 1

        if target_item and target_node:
            # Expand all parent categories
            p = target_item.parent()
            while p:
                p.setExpanded(True)
                p = p.parent()

            self.tree.setCurrentItem(target_item)
            self.load_tag(target_node)
            return True

        return False

    def reset_to_list(self) -> None:
        """Reset view stack to tracks table."""
        self.stack.setCurrentIndex(0)
