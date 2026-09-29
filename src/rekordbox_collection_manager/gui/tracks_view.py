"""Collection Tracks View Screen.

Presents complete collection tracklist with rich metadata in a customizable table
featuring audio file format badges, dedicated folder and filename columns,
Ctrl/Cmd-click direct folder opening and Finder file revealing,
tags rendered as chips, real playlist chips, play count filtering,
configurable limits, interactive column sorting, and instant search.
"""

import os
from typing import Any, Dict, List, Optional
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.engine import Engine

from rekordbox_collection_manager.gui.theme import (
    COLOR_TEXT_MUTED,
    COLOR_TEXT_SECONDARY,
)
from rekordbox_collection_manager.gui.track_detail_view import TrackDetailView
from rekordbox_collection_manager.gui.track_table import (
    DEFAULT_COLLECTION_COLUMNS,
    TrackTableConfig,
    TracksTableWidget,
)
from rekordbox_collection_manager.gui.workers import TrackSearchWorker, stop_worker
from rekordbox_collection_manager.utils import open_in_folder, reveal_in_file_manager


class TracksView(QWidget):
    """View to search, filter, and inspect collection tracks in rich read-only format."""

    notify_status = Signal(str)
    open_history_session_requested = Signal(str)
    open_playlist_requested = Signal(str)
    open_tag_requested = Signal(str)
    replace_file_requested = Signal(dict)
    db_saved = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.engine: Optional[Engine] = None
        self.db_path: Optional[str] = None
        self.total_collection_tracks: int = 0
        self.records: List[Dict[str, Any]] = []
        self.active_search_worker: Optional[TrackSearchWorker] = None
        self.current_sort_field: str = "title"
        self.current_sort_order: Qt.SortOrder = Qt.SortOrder.AscendingOrder

        # Debounce timer for live search
        self.search_debounce_timer = QTimer(self)
        self.search_debounce_timer.setSingleShot(True)
        self.search_debounce_timer.setInterval(250)
        self.search_debounce_timer.timeout.connect(self._execute_search)

        self._init_ui()

    def _init_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.stack = QStackedWidget(self)
        root_layout.addWidget(self.stack)

        # ----------------------------------------------------------------------
        # Page 0: Tracks Collection List View
        # ----------------------------------------------------------------------
        self.list_widget = QWidget()
        main_layout = QVBoxLayout(self.list_widget)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # 1. Search and Filter Toolbar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)

        self.tracks_header_label = QLabel("TRACKS")
        self.tracks_header_label.setStyleSheet(
            f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 0.8px;"
        )
        toolbar.addWidget(self.tracks_header_label)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Search title, artist, key, tags, playlist, or filename...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._on_search_text_changed)
        toolbar.addWidget(self.search_input, stretch=3)

        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["All Tracks", "Played Tracks (> 0)", "Unplayed (0)"])
        self.filter_combo.currentIndexChanged.connect(lambda _: self._execute_search())
        toolbar.addWidget(self.filter_combo, stretch=1)

        self.limit_combo = QComboBox()
        self.limit_combo.currentIndexChanged.connect(lambda _: self._execute_search())
        self.update_limit_options(0)  # Initially hidden until total collection size is loaded
        toolbar.addWidget(self.limit_combo, stretch=1)

        self.search_btn = QPushButton("Search")
        self.search_btn.clicked.connect(self._execute_search)
        toolbar.addWidget(self.search_btn)

        self.copy_btn = QPushButton("📋 Copy Tracklist")
        self.copy_btn.setToolTip("Copy formatted tracklist to clipboard (e.g. 01. Artist - Title)")
        self.copy_btn.clicked.connect(self._on_copy_tracklist)
        toolbar.addWidget(self.copy_btn)

        main_layout.addLayout(toolbar)

        # 2. Main Tracks Table (13 Columns via shared TracksTableWidget)
        table_config = TrackTableConfig(
            columns=list(DEFAULT_COLLECTION_COLUMNS),
            index_mode="row_number",
            index_header_tooltip="Sort by collection order / ID",
            sorting_mode="server",
            enable_context_menu=True,
            enable_finder_clicks=True,
        )
        self.table = TracksTableWidget(config=table_config, parent=self)
        self.table.sort_requested.connect(self._on_table_sort_requested)
        self.table.status_message.connect(self._on_table_status_message)
        self.table.track_detail_requested.connect(self._open_track_detail)
        self.table.replace_file_requested.connect(self.replace_file_requested.emit)
        self.table.open_playlist_requested.connect(self.open_playlist_requested.emit)
        self.table.open_tag_requested.connect(self.open_tag_requested.emit)
        main_layout.addWidget(self.table, stretch=1)

        # 3. Bottom Status Bar
        self.status_label = QLabel("0 tracks")
        self.status_label.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-size: 11px;")
        main_layout.addWidget(self.status_label)
        self.stack.addWidget(self.list_widget)

        # ----------------------------------------------------------------------
        # Page 1: Track Details View Screen
        # ----------------------------------------------------------------------
        self.detail_view = TrackDetailView(parent=self, back_button_text="← Back to Tracks")
        self.detail_view.back_clicked.connect(self._on_back_from_detail)
        self.detail_view.open_history_session_requested.connect(self.open_history_session_requested.emit)
        self.detail_view.open_playlist_requested.connect(self.open_playlist_requested.emit)
        self.detail_view.open_tag_requested.connect(self.open_tag_requested.emit)
        self.detail_view.status_message.connect(self._on_table_status_message)
        self.detail_view.track_changed.connect(self._on_detail_track_changed)
        self.detail_view.replace_file_requested.connect(self.replace_file_requested.emit)
        self.detail_view.db_saved.connect(self.db_saved.emit)
        self.stack.addWidget(self.detail_view)

    def reset_to_list(self) -> None:
        """Reset view to collection tracks table list."""
        self.stack.setCurrentIndex(0)

    def update_limit_options(self, total_tracks: int) -> None:
        """Dynamically configure limit dropdown based on total collection tracks.

        - If total_tracks <= 2500:
          Limiter is hidden from the top bar and defaults to 'All Tracks'.
        - If total_tracks > 2500:
          Limiter is shown with increments of 2500 strictly less than total_tracks,
          followed by 'All Tracks'. Defaults to 'Limit: 2500'.
        """
        self.total_collection_tracks = total_tracks
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
            self.limit_combo.setCurrentIndex(0)  # Always default to Limit: 2500

        self.limit_combo.blockSignals(False)

    def set_engine(
        self, engine: Engine, total_tracks: int = 0, db_path: Optional[str] = None
    ) -> None:
        """Assign active database engine and initiate initial track search."""
        self.engine = engine
        self.db_path = db_path
        self.table.set_engine(engine)
        self.update_limit_options(total_tracks)
        self._execute_search()

    def _on_search_text_changed(self, text: str) -> None:
        self.search_debounce_timer.start()

    def _on_table_sort_requested(self, sort_field: str, order: Qt.SortOrder) -> None:
        self.current_sort_field = sort_field
        self.current_sort_order = order
        self._execute_search()

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

    def _execute_search(self) -> None:
        """Run track query via non-blocking background worker."""
        if not self.engine:
            return

        query = self.search_input.text().strip()

        # Limit: None if hidden or 0 (All Tracks); otherwise int limit
        if self.limit_combo.isHidden():
            limit = None
        else:
            limit_data = self.limit_combo.currentData()
            limit = int(limit_data) if (limit_data is not None and limit_data > 0) else None

        # Filter mode
        filter_mode_idx = self.filter_combo.currentIndex()
        if filter_mode_idx == 1:
            play_filter = "played"
        elif filter_mode_idx == 2:
            play_filter = "unplayed"
        else:
            play_filter = "all"

        sort_order_str = "desc" if self.current_sort_order == Qt.SortOrder.DescendingOrder else "asc"
        stop_worker(self.active_search_worker)

        self.status_label.setText("Searching tracks in database...")
        self.active_search_worker = TrackSearchWorker(
            self.engine,
            query,
            limit=limit,
            sort_by=self.current_sort_field,
            sort_order=sort_order_str,
            play_count_filter=play_filter,
            parent=self,
        )
        self.active_search_worker.success.connect(self._on_search_success)
        self.active_search_worker.error.connect(self._on_search_error)
        self.active_search_worker.start()

    def _on_search_success(self, query: str, records: List[Dict[str, Any]]) -> None:
        self.records = records
        self.tracks_header_label.setText(f"TRACKS ({len(records)})")
        self._populate_table()

        total_cnt = self.total_collection_tracks or len(records)
        disp_cnt = len(records)
        if query:
            self.status_label.setText(
                f"Showing {disp_cnt} tracks matching '{query}' (Total collection: {total_cnt})"
            )
        else:
            self.status_label.setText(
                f"Showing {disp_cnt} tracks (Total collection: {total_cnt})"
            )

    def _on_search_error(self, err_msg: str) -> None:
        self.status_label.setText(f"❌ Error loading tracks: {err_msg}")
        self.notify_status.emit(f"Error loading tracks: {err_msg}")

    def _populate_table(self) -> None:
        """Render records into the table widget."""
        self.table.set_tracks(self.records)

    def _on_copy_tracklist(self) -> None:
        """Copy formatted tracklist to system clipboard."""
        self.table.copy_tracklist_to_clipboard(self.records)

    def _open_track_detail(self, track: Dict[str, Any]) -> None:
        """Switch to in-place TrackDetailView for the given track."""
        if not self.engine or not track:
            return

        track_id = track.get("id") or track.get("content_id")
        curr_idx = 0
        for idx, r in enumerate(self.records):
            if (r.get("id") or r.get("content_id")) == track_id:
                curr_idx = idx
                break

        self.detail_view.load_track(
            engine=self.engine,
            track_data=track,
            db_path=self.db_path,
            tracks_list=self.records,
            current_index=curr_idx,
            back_button_text="← Back to Tracks",
        )
        self.stack.setCurrentIndex(1)

    def _on_back_from_detail(self) -> None:
        """Return to tracks collection list view."""
        self.stack.setCurrentIndex(0)

    def _on_detail_track_changed(self, idx: int, track: Dict[str, Any]) -> None:
        """Select, scroll, and update track in table when detail view navigates or changes."""
        if 0 <= idx < len(self.records):
            self.records[idx].update(track)
            if "playlists" in track:
                self.table.update_track_playlists(idx, track["playlists"])
            if "tags" in track:
                self.table.update_track_tags(idx, track["tags"])
        if 0 <= idx < self.table.rowCount():
            self.table.selectRow(idx)
            self.table.scrollToRow(idx)

    def _on_open_selected_track_detail(self) -> None:
        """Open details for currently selected table row."""
        selected_rows = self.table.selected_rows()
        if not selected_rows:
            msg = "⚠️ Select a track in the table to view details."
            self.status_label.setText(msg)
            self.notify_status.emit(msg)
            return
        row = selected_rows[0]
        if 0 <= row < len(self.records):
            self._open_track_detail(self.records[row])

    def cleanup(self) -> None:
        """Stop background search workers and timers cleanly."""
        if hasattr(self, "search_debounce_timer"):
            self.search_debounce_timer.stop()
        if getattr(self, "active_search_worker", None):
            stop_worker(self.active_search_worker, wait_ms=100)
            self.active_search_worker = None
        if hasattr(self, "detail_view") and hasattr(self.detail_view, "cleanup"):
            self.detail_view.cleanup()

