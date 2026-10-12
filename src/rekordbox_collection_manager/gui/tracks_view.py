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
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.engine import Engine

from rekordbox_collection_manager.edit_play_count import (
    fetch_all_playlists,
    fetch_all_tags,
    fetch_distinct_file_types,
    fetch_distinct_keys,
)
from rekordbox_collection_manager.gui.theme import (
    COLOR_BG_INPUT,
    COLOR_BORDER,
    COLOR_BORDER_FOCUS,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_SECONDARY,
)
from rekordbox_collection_manager.gui.multi_select_filter import MultiSelectSearchFilter
from rekordbox_collection_manager.gui.track_detail_view import TrackDetailView
from rekordbox_collection_manager.gui.track_table import (
    DEFAULT_COLLECTION_COLUMNS,
    TrackColumn,
    TrackTableConfig,
    TracksTableWidget,
)
from rekordbox_collection_manager.gui.workers import TrackSearchWorker, stop_worker
from rekordbox_collection_manager.utils import (
    KNOWN_STORES,
    open_in_folder,
    reveal_in_file_manager,
)


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
        self._needs_table_refresh: bool = False

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

        # 1. Search and Action Toolbar (Top Row)
        top_toolbar = QHBoxLayout()
        top_toolbar.setSpacing(10)

        self.tracks_header_label = QLabel("TRACKS")
        self.tracks_header_label.setStyleSheet(
            f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 0.8px;"
        )
        top_toolbar.addWidget(self.tracks_header_label)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Search title, artist, key, tags, playlist, or filename...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._on_search_text_changed)
        top_toolbar.addWidget(self.search_input, stretch=3)

        self.search_btn = QPushButton("Search")
        self.search_btn.clicked.connect(self._execute_search)
        top_toolbar.addWidget(self.search_btn)

        self.btn_toggle_filters = QPushButton("🎛️ Filters")
        self.btn_toggle_filters.setToolTip("Toggle filter row visibility")
        self.btn_toggle_filters.setStyleSheet(
            f"QPushButton {{ background-color: {COLOR_BG_INPUT}; color: {COLOR_TEXT_SECONDARY}; "
            f"border: 1px solid {COLOR_BORDER}; border-radius: 4px; padding: 4px 10px; font-size: 11px; font-weight: 600; }} "
            f"QPushButton:hover {{ border-color: {COLOR_BORDER_FOCUS}; color: {COLOR_TEXT_PRIMARY}; }}"
        )
        self.btn_toggle_filters.clicked.connect(self._toggle_filter_panel)
        top_toolbar.addWidget(self.btn_toggle_filters)

        self.copy_btn = QPushButton("📋 Copy Tracklist")
        self.copy_btn.setToolTip("Copy formatted tracklist to clipboard (e.g. 01. Artist - Title)")
        self.copy_btn.clicked.connect(self._on_copy_tracklist)
        top_toolbar.addWidget(self.copy_btn)

        main_layout.addLayout(top_toolbar)

        # 2. Dedicated Filtering Row Panel
        self.filter_panel = QFrame()
        self.filter_panel.setObjectName("filterPanel")
        self.filter_panel.setStyleSheet(
            f"QFrame#filterPanel {{ background-color: #14141a; border: 1px solid {COLOR_BORDER}; "
            "border-radius: 6px; }"
        )
        filter_layout = QHBoxLayout(self.filter_panel)
        filter_layout.setContentsMargins(10, 6, 10, 6)
        filter_layout.setSpacing(8)

        lbl_filter = QLabel("🎛️ FILTERS:")
        lbl_filter.setStyleSheet(
            f"color: {COLOR_TEXT_MUTED}; font-size: 10px; font-weight: 700; letter-spacing: 0.6px;"
        )
        filter_layout.addWidget(lbl_filter)

        # Combo styling helper templates
        self._filter_style_base = (
            f"QComboBox {{ background-color: {COLOR_BG_INPUT}; color: {COLOR_TEXT_PRIMARY}; "
            f"border: 1px solid {COLOR_BORDER}; border-radius: 4px; padding: 2px 8px; font-size: 11px; min-height: 24px; }} "
            f"QComboBox:hover {{ border-color: {COLOR_BORDER_FOCUS}; }} "
            "QComboBox::drop-down { border: none; width: 14px; }"
        )
        self._filter_style_active = (
            "QComboBox { background-color: rgba(56, 189, 248, 0.12); color: #38bdf8; "
            "border: 1px solid #38bdf8; border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: 600; min-height: 24px; } "
            "QComboBox::drop-down { border: none; width: 14px; }"
        )

        # Purchase Filter (User Feature Request)
        self.purchase_combo = QComboBox()
        self.purchase_combo.addItem("All Statuses", userData="all")
        self.purchase_combo.addItem("📥 Downloaded (Exclude Purchased)", userData="downloaded")
        self.purchase_combo.addItem("🛒 Purchased Only", userData="purchased")
        self.purchase_combo.addItem("💳 Purchasable (≥3 plays)", userData="purchasable")
        self.purchase_combo.setStyleSheet(self._filter_style_base)
        self.purchase_combo.setToolTip("Filter by purchase status (Downloaded, Purchased, or Purchasable [Downloaded & ≥3 plays])")
        self.purchase_combo.currentIndexChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self.purchase_combo)

        # Store Filter (Multi-select, visible only when filtering by Purchased)
        self.store_filter_widget = MultiSelectSearchFilter(
            title="Store", icon="🏪", enable_match_mode=False, parent=self
        )
        self.store_filter_widget.set_items(list(KNOWN_STORES))
        self.store_filter_widget.filter_changed.connect(self._on_filter_changed)
        self.store_filter_widget.setVisible(False)
        filter_layout.addWidget(self.store_filter_widget)

        # Play Count Filter
        self.play_filter_combo = QComboBox()
        self.play_filter_combo.addItem("All Tracks", userData="all")
        self.play_filter_combo.addItem("Played Tracks (> 0)", userData="played")
        self.play_filter_combo.addItem("Unplayed (0)", userData="unplayed")
        self.play_filter_combo.setStyleSheet(self._filter_style_base)
        self.play_filter_combo.setToolTip("Filter by play count")
        self.play_filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        self.filter_combo = self.play_filter_combo  # Backwards compatibility alias
        filter_layout.addWidget(self.play_filter_combo)

        # Audio Format Filter (Multi-select with live search)
        self.format_filter_widget = MultiSelectSearchFilter(
            title="Format", icon="🎵", enable_match_mode=False, parent=self
        )
        self.format_filter_widget.filter_changed.connect(self._on_filter_changed)
        self.format_filter_combo = self.format_filter_widget  # Backwards compatibility alias
        filter_layout.addWidget(self.format_filter_widget)

        # Musical Key Filter (Multi-select with live search)
        self.key_filter_widget = MultiSelectSearchFilter(
            title="Key", icon="🎹", enable_match_mode=False, parent=self
        )
        self.key_filter_widget.filter_changed.connect(self._on_filter_changed)
        self.key_filter_combo = self.key_filter_widget  # Backwards compatibility alias
        filter_layout.addWidget(self.key_filter_widget)

        # BPM Range Filter
        self.bpm_filter_combo = QComboBox()
        self.bpm_filter_combo.addItem("All BPM", userData=(None, None))
        self.bpm_filter_combo.addItem("< 120 BPM", userData=(0.0, 119.9))
        self.bpm_filter_combo.addItem("120 – 124 BPM", userData=(120.0, 124.9))
        self.bpm_filter_combo.addItem("124 – 128 BPM", userData=(124.0, 128.9))
        self.bpm_filter_combo.addItem("128 – 132 BPM", userData=(128.0, 132.9))
        self.bpm_filter_combo.addItem("> 132 BPM", userData=(133.0, 999.0))
        self.bpm_filter_combo.setStyleSheet(self._filter_style_base)
        self.bpm_filter_combo.setToolTip("Filter by tempo (BPM)")
        self.bpm_filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self.bpm_filter_combo)

        # Tag Filter (Multi-select with live search & ANY/ALL match mode)
        self.tag_filter_widget = MultiSelectSearchFilter(
            title="Tags", icon="🏷️", enable_match_mode=True, parent=self
        )
        self.tag_filter_widget.filter_changed.connect(self._on_filter_changed)
        self.tag_filter_combo = self.tag_filter_widget  # Backwards compatibility alias
        filter_layout.addWidget(self.tag_filter_widget)

        # Playlist Filter (Multi-select with live search & ANY/ALL match mode)
        self.playlist_filter_widget = MultiSelectSearchFilter(
            title="Playlists", icon="📁", enable_match_mode=True, parent=self
        )
        self.playlist_filter_widget.filter_changed.connect(self._on_filter_changed)
        self.playlist_filter_combo = self.playlist_filter_widget  # Backwards compatibility alias
        filter_layout.addWidget(self.playlist_filter_widget)

        # Limit Dropdown
        self.limit_combo = QComboBox()
        self.limit_combo.setStyleSheet(self._filter_style_base)
        self.limit_combo.setToolTip("Result count limit")
        self.limit_combo.currentIndexChanged.connect(lambda _: self._execute_search())
        self.update_limit_options(0)  # Initially hidden until total collection size is loaded
        filter_layout.addWidget(self.limit_combo)

        filter_layout.addStretch(1)

        # Active Filters Counter Badge & Reset Button
        self.active_filters_badge = QLabel("")
        self.active_filters_badge.setStyleSheet(
            "color: #38bdf8; font-size: 11px; font-weight: 600; padding: 0 4px;"
        )
        filter_layout.addWidget(self.active_filters_badge)

        self.btn_reset_filters = QPushButton("✕ Clear Filters")
        self.btn_reset_filters.setToolTip("Reset all filters to defaults")
        self.btn_reset_filters.setStyleSheet(
            "QPushButton { background-color: transparent; color: #f87171; border: 1px solid rgba(248, 113, 113, 0.4); "
            "border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: 600; min-height: 24px; } "
            "QPushButton:hover { background-color: rgba(248, 113, 113, 0.15); border-color: #f87171; color: #ffffff; }"
        )
        self.btn_reset_filters.clicked.connect(self._on_reset_filters)
        self.btn_reset_filters.setVisible(False)
        filter_layout.addWidget(self.btn_reset_filters)

        main_layout.addWidget(self.filter_panel)

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
        self.table.db_saved.connect(self.db_saved.emit)
        self.table.tag_created.connect(lambda name: self.status_label.setText(f"🏷️ Created tag '{name}'."))
        self.table.playlist_created.connect(lambda name: self.status_label.setText(f"📑 Created playlist '{name}'."))
        main_layout.addWidget(self.table, stretch=1)
        main_layout.addWidget(self.table.bottom_bar)

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
        self.detail_view.db_saved.connect(self._on_detail_db_saved)
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
        self.populate_filter_options()
        self._execute_search()

    def _toggle_filter_panel(self) -> None:
        """Toggle visibility of the secondary filtering toolbar."""
        is_vis = self.filter_panel.isVisible()
        self.filter_panel.setVisible(not is_vis)

    def _on_filter_changed(self) -> None:
        """Handle any filter combo change: update active styling and query."""
        p_filter = self.purchase_combo.currentData() or "all"
        is_purchased_only = (p_filter == "purchased")
        is_purchasable = (p_filter in ("purchasable", "buyable"))

        if hasattr(self, "store_filter_widget"):
            if not is_purchased_only and self.store_filter_widget.is_active():
                self.store_filter_widget.clear_selection()
            self.store_filter_widget.setVisible(is_purchased_only)

        if hasattr(self, "play_filter_combo"):
            if is_purchasable:
                if self.play_filter_combo.currentIndex() != 0:
                    self.play_filter_combo.blockSignals(True)
                    self.play_filter_combo.setCurrentIndex(0)
                    self.play_filter_combo.blockSignals(False)
                self.play_filter_combo.setVisible(False)
            else:
                self.play_filter_combo.setVisible(True)

        if hasattr(self, "table"):
            self.table.set_column_visible(TrackColumn.PURCHASED_AT, is_purchased_only)

        self._update_all_filter_styles()
        self._execute_search()

    def _update_all_filter_styles(self) -> None:
        """Highlight active filters with an accent border and show badge/clear button."""
        combos = [
            self.purchase_combo,
            self.play_filter_combo,
            self.bpm_filter_combo,
        ]
        active_count = 0
        for c in combos:
            if c.currentIndex() > 0 and not c.isHidden():
                c.setStyleSheet(self._filter_style_active)
                active_count += 1
            else:
                c.setStyleSheet(self._filter_style_base)

        p_filter = self.purchase_combo.currentData() or "all"
        if p_filter == "purchased" and hasattr(self, "store_filter_widget") and self.store_filter_widget.is_active():
            active_count += 1
        if self.format_filter_widget.is_active():
            active_count += 1
        if self.key_filter_widget.is_active():
            active_count += 1
        if self.tag_filter_widget.is_active():
            active_count += 1
        if self.playlist_filter_widget.is_active():
            active_count += 1

        if active_count > 0:
            self.active_filters_badge.setText(f"{active_count} active")
            self.btn_reset_filters.setVisible(True)
            self.btn_toggle_filters.setText(f"🎛️ Filters ({active_count})")
        else:
            self.active_filters_badge.setText("")
            self.btn_reset_filters.setVisible(False)
            self.btn_toggle_filters.setText("🎛️ Filters")

    def _on_reset_filters(self) -> None:
        """Reset all filtering dropdowns to default ('All') in one operation."""
        combos = [
            self.purchase_combo,
            self.play_filter_combo,
            self.bpm_filter_combo,
        ]
        for c in combos:
            c.blockSignals(True)
            c.setCurrentIndex(0)
            c.blockSignals(False)

        if hasattr(self, "play_filter_combo"):
            self.play_filter_combo.setVisible(True)

        if hasattr(self, "store_filter_widget"):
            self.store_filter_widget.clear_selection()
            self.store_filter_widget.setVisible(False)
        if hasattr(self, "table"):
            self.table.set_column_visible(TrackColumn.PURCHASED_AT, False)

        self.format_filter_widget.clear_selection()
        self.key_filter_widget.clear_selection()
        self.tag_filter_widget.clear_selection()
        self.playlist_filter_widget.clear_selection()

        self._update_all_filter_styles()
        self._execute_search()

    def populate_filter_options(self) -> None:
        """Populate Format, Key, Tag, and Playlist filter dropdowns dynamically from the active database."""
        if not self.engine:
            return

        # 1. Formats (only audio file formats present in active database)
        try:
            formats = fetch_distinct_file_types(self.engine)
            self.format_filter_widget.set_items(formats)
        except Exception:
            pass

        # 2. Keys (only musical keys present on tracks in active database)
        try:
            keys = fetch_distinct_keys(self.engine)
            self.key_filter_widget.set_items(keys)
        except Exception:
            pass

        # 3. Tags
        try:
            tags = fetch_all_tags(self.engine)
            tag_names = [t.get("name") if isinstance(t, dict) else str(t) for t in tags]
            self.tag_filter_widget.set_items([n for n in tag_names if n])
        except Exception:
            pass

        # 4. Playlists
        try:
            playlists = fetch_all_playlists(self.engine)
            pl_names = [p.get("name") if isinstance(p, dict) else str(p) for p in playlists]
            self.playlist_filter_widget.set_items([n for n in pl_names if n])
        except Exception:
            pass

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

    def refresh(self, preserve_scroll: bool = True) -> None:
        """Refresh collection tracks from database and re-populate filter options."""
        if not self.engine:
            return
        try:
            self.populate_filter_options()
        except Exception:
            pass

        # If currently looking at TrackDetailView, reload active track directly from database
        if self.stack.currentIndex() == 1 and hasattr(self, "detail_view"):
            self._needs_table_refresh = True
            has_unsaved = (
                self.detail_view.has_staged_changes()
                if hasattr(self.detail_view, "has_staged_changes")
                else False
            )
            if not has_unsaved and hasattr(self.detail_view, "reload_track"):
                self.detail_view.reload_track()

        self._execute_search(preserve_scroll=preserve_scroll)

    def _execute_search(self, preserve_scroll: bool = False) -> None:
        """Run track query via non-blocking background worker."""
        if not self.engine:
            return

        if self.table.has_staged_changes():
            if not self.table.confirm_unsaved_changes():
                return

        if preserve_scroll:
            self._pending_restore_scroll = self.table.verticalScrollBar().value()
            selected = self.table.selected_records()
            self._pending_selected_track_id = (
                selected[0].get("id") or selected[0].get("content_id")
            ) if selected else None
        else:
            self._pending_restore_scroll = None
            self._pending_selected_track_id = None

        query = self.search_input.text().strip()

        # Limit: None if hidden or 0 (All Tracks); otherwise int limit
        if self.limit_combo.isHidden():
            limit = None
        else:
            limit_data = self.limit_combo.currentData()
            limit = int(limit_data) if (limit_data is not None and limit_data > 0) else None

        # Filter mode from dropdowns
        purchased_filter = self.purchase_combo.currentData() or "all"
        play_filter = (
            "all"
            if purchased_filter in ("purchasable", "buyable")
            else (self.play_filter_combo.currentData() or "all")
        )
        store_filter = (
            self.store_filter_widget.get_selected()
            if (purchased_filter == "purchased" and hasattr(self, "store_filter_widget"))
            else []
        )
        format_filter = self.format_filter_widget.get_selected()
        key_filter = self.key_filter_widget.get_selected()
        bpm_data = self.bpm_filter_combo.currentData()
        bpm_min, bpm_max = bpm_data if isinstance(bpm_data, tuple) else (None, None)
        tag_filter = self.tag_filter_widget.get_selected()
        tag_match_mode = self.tag_filter_widget.get_match_mode()
        playlist_filter = self.playlist_filter_widget.get_selected()
        playlist_match_mode = self.playlist_filter_widget.get_match_mode()

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
            purchased_filter=purchased_filter,
            store_filter=store_filter,
            key_filter=key_filter,
            bpm_min=bpm_min,
            bpm_max=bpm_max,
            file_type_filter=format_filter,
            tag_filter=tag_filter,
            playlist_filter=playlist_filter,
            tag_match_mode=tag_match_mode,
            playlist_match_mode=playlist_match_mode,
            parent=self,
        )
        self.active_search_worker.success.connect(self._on_search_success)
        self.active_search_worker.error.connect(self._on_search_error)
        self.active_search_worker.start()

    def _on_search_success(self, query: str, records: List[Dict[str, Any]]) -> None:
        self.records = records
        self.tracks_header_label.setText(f"TRACKS ({len(records)})")
        self._populate_table()

        restore_scroll = getattr(self, "_pending_restore_scroll", None)
        if restore_scroll is not None:
            self.table.verticalScrollBar().setValue(restore_scroll)
            self._pending_restore_scroll = None

        restore_id = getattr(self, "_pending_selected_track_id", None)
        if restore_id is not None:
            for idx, r in enumerate(records):
                if (r.get("id") or r.get("content_id")) == restore_id:
                    self.table.selectRow(idx)
                    break
            self._pending_selected_track_id = None

        # If currently looking at TrackDetailView, keep its displayed track in sync
        if self.stack.currentIndex() == 1 and hasattr(self, "detail_view"):
            has_unsaved = (
                self.detail_view.has_staged_changes()
                if hasattr(self.detail_view, "has_staged_changes")
                else False
            )
            if not has_unsaved and hasattr(self.detail_view, "reload_track"):
                self.detail_view.reload_track()

        total_cnt = self.total_collection_tracks or len(records)
        disp_cnt = len(records)

        # Informative filter breakdown in status text
        p_filter = self.purchase_combo.currentData() or "all"
        info_extras = []
        if p_filter == "downloaded":
            diff = total_cnt - disp_cnt
            if diff > 0 and not query:
                info_extras.append(f"{diff} purchased excluded")
            else:
                info_extras.append("purchased excluded")
        elif p_filter in ("purchasable", "buyable"):
            info_extras.append("purchasable: downloaded & ≥3 plays")
        elif p_filter == "purchased":
            store_sel = self.store_filter_widget.get_selected() if hasattr(self, "store_filter_widget") else []
            if store_sel:
                info_extras.append(f"purchased: {', '.join(store_sel)}")
            else:
                info_extras.append("purchased only")

        extra_str = f" • {', '.join(info_extras)}" if info_extras else ""
        if query:
            self.status_label.setText(
                f"Showing {disp_cnt} tracks matching '{query}'{extra_str} (Total collection: {total_cnt})"
            )
        else:
            self.status_label.setText(
                f"Showing {disp_cnt} tracks{extra_str} (Total collection: {total_cnt})"
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

        if self.table.has_staged_changes():
            if not self.table.confirm_unsaved_changes():
                return

        self._needs_table_refresh = False
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

    def _on_detail_db_saved(self) -> None:
        """Track that database modifications occurred while in detail view and bubble signal."""
        self._needs_table_refresh = True
        self.db_saved.emit()

    def _on_back_from_detail(self) -> None:
        """Return to tracks collection list view and refresh if changes were made."""
        self.stack.setCurrentIndex(0)
        if getattr(self, "_needs_table_refresh", False):
            self._needs_table_refresh = False
            self.refresh(preserve_scroll=True)

    def _on_detail_track_changed(self, idx: int, track: Dict[str, Any]) -> None:
        """Select, scroll, and update track in table when detail view navigates or changes."""
        self.table.sync_saved_track(track)
        track_id = str(track.get("id") or track.get("content_id") or "")

        match_idx = None
        if (
            0 <= idx < len(self.records)
            and str(self.records[idx].get("id") or self.records[idx].get("content_id") or "") == track_id
        ):
            match_idx = idx
        else:
            for i, r in enumerate(self.records):
                if str(r.get("id") or r.get("content_id") or "") == track_id:
                    match_idx = i
                    break

        if match_idx is not None:
            self.records[match_idx].update(track)
            self.table.update_track_record(match_idx, track)
            if "playlists" in track:
                self.table.update_track_playlists(match_idx, track["playlists"])
            if "tags" in track:
                self.table.update_track_tags(match_idx, track["tags"])
            if 0 <= match_idx < self.table.rowCount():
                self.table.selectRow(match_idx)
                self.table.scrollToRow(match_idx)

    def confirm_unsaved_changes(self) -> bool:
        """Prompt user if there are unsaved changes in detail view or tracks table."""
        if self.stack.currentIndex() == 1:
            return self.detail_view._confirm_unsaved_changes()
        return self.table.confirm_unsaved_changes()

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
            stop_worker(self.active_search_worker, wait_ms=1000)
            self.active_search_worker = None
        if hasattr(self, "detail_view") and hasattr(self.detail_view, "cleanup"):
            self.detail_view.cleanup()

