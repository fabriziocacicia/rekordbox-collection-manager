"""Collection Tracks View Screen.

Presents complete collection tracklist with rich metadata in a 10-column table
identical to History Details View, combined with real-time search, play count filtering,
configurable limits, and aggregate KPI statistics cards.
"""

from typing import Any, Dict, List, Optional
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.engine import Engine

from rekordbox_collection_manager.gui.history_detail_view import NumericTableWidgetItem
from rekordbox_collection_manager.gui.theme import (
    COLOR_BLUE,
    COLOR_GREEN,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_SECONDARY,
)
from rekordbox_collection_manager.gui.workers import TrackSearchWorker


class TracksView(QWidget):
    """View to search, filter, and inspect collection tracks in rich read-only format."""

    notify_status = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.engine: Optional[Engine] = None
        self.total_collection_tracks: int = 0
        self.records: List[Dict[str, Any]] = []
        self.active_search_worker: Optional[TrackSearchWorker] = None
        self.current_sort_col: int = 1  # Title by default
        self.current_sort_field: str = "title"
        self.current_sort_order: Qt.SortOrder = Qt.SortOrder.AscendingOrder

        # Debounce timer for live search
        self.search_debounce_timer = QTimer(self)
        self.search_debounce_timer.setSingleShot(True)
        self.search_debounce_timer.setInterval(250)
        self.search_debounce_timer.timeout.connect(self._execute_search)

        self._init_ui()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # Search and Filter Toolbar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)

        self.tracks_header_label = QLabel("TRACKS")
        self.tracks_header_label.setStyleSheet(
            f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 0.8px;"
        )
        toolbar.addWidget(self.tracks_header_label)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Search track title, artist, album, key, or filename...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._on_search_text_changed)
        toolbar.addWidget(self.search_input, stretch=3)

        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["All Tracks", "Played Tracks (> 0)", "Unplayed (0)"])
        self.filter_combo.currentIndexChanged.connect(lambda _: self._execute_search())
        toolbar.addWidget(self.filter_combo, stretch=1)

        self.limit_combo = QComboBox()
        self.limit_combo.addItems([
            "Limit: 50",
            "Limit: 100",
            "Limit: 250",
            "Limit: 500",
            "Limit: 1000",
            "Limit: 2500",
            "All Tracks",
        ])
        self.limit_combo.setCurrentIndex(1)  # Default: Limit 100
        self.limit_combo.currentIndexChanged.connect(lambda _: self._execute_search())
        toolbar.addWidget(self.limit_combo, stretch=1)

        self.search_btn = QPushButton("Search")
        self.search_btn.clicked.connect(self._execute_search)
        toolbar.addWidget(self.search_btn)

        self.copy_btn = QPushButton("📋 Copy Tracklist")
        self.copy_btn.setToolTip("Copy formatted tracklist to clipboard (e.g. 01. Artist - Title)")
        self.copy_btn.clicked.connect(self._on_copy_tracklist)
        toolbar.addWidget(self.copy_btn)

        main_layout.addLayout(toolbar)

        # 3. Main Tracks Table (10 Columns, matching History Details View)
        self.table = QTableWidget()
        self.table.setColumnCount(10)
        self.table.setHorizontalHeaderLabels([
            "#",
            "Title",
            "Artist",
            "Album",
            "Key",
            "BPM",
            "Time",
            "Plays",
            "Bitrate",
            "Location",
        ])
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.verticalHeader().setVisible(False)

        # Column resize modes
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(8, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(9, QHeaderView.ResizeMode.Stretch)

        # Center align headers
        for col in (0, 4, 5, 6, 7, 8):
            item = self.table.horizontalHeaderItem(col)
            if item:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        # Interactive column header sorting
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.sectionClicked.connect(self._on_header_section_clicked)

        header_tooltips = {
            0: "Sort by collection order / ID",
            1: "Sort by Title",
            2: "Sort by Artist",
            3: "Sort by Album",
            4: "Sort by Musical / Camelot Key",
            5: "Sort by BPM (Tempos)",
            6: "Sort by Track Duration",
            7: "Sort by Play Count",
            8: "Sort by Audio Bitrate",
            9: "Sort by File Location",
        }
        for col, tip in header_tooltips.items():
            h_item = self.table.horizontalHeaderItem(col)
            if h_item:
                h_item.setToolTip(tip)

        main_layout.addWidget(self.table, stretch=1)

        # 4. Bottom Status Bar
        self.status_label = QLabel("0 tracks")
        self.status_label.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-size: 11px;")
        main_layout.addWidget(self.status_label)

    def set_engine(self, engine: Engine, total_tracks: int = 0) -> None:
        """Assign active database engine and initiate initial track search."""
        self.engine = engine
        self.total_collection_tracks = total_tracks
        self._execute_search()

    def _on_search_text_changed(self, text: str) -> None:
        self.search_debounce_timer.start()

    def _execute_search(self) -> None:
        """Run track query via non-blocking background worker."""
        if not self.engine:
            return

        query = self.search_input.text().strip()

        # Limit
        limit_map = [50, 100, 250, 500, 1000, 2500, 0]
        idx = self.limit_combo.currentIndex()
        limit = limit_map[idx] if idx < len(limit_map) else 100

        # Filter mode
        filter_mode_idx = self.filter_combo.currentIndex()
        if filter_mode_idx == 1:
            play_filter = "played"
        elif filter_mode_idx == 2:
            play_filter = "unplayed"
        else:
            play_filter = "all"

        sort_order_str = "desc" if self.current_sort_order == Qt.SortOrder.DescendingOrder else "asc"

        if self.active_search_worker and self.active_search_worker.isRunning():
            self.active_search_worker.terminate()

        self.status_label.setText("Searching tracks in database...")
        self.active_search_worker = TrackSearchWorker(
            self.engine,
            query,
            limit=limit if limit > 0 else None,
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
        """Render records into the 10-column read-only table."""
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)

        for row_idx, t in enumerate(self.records):
            self.table.insertRow(row_idx)

            # Col 0: # (Row Index 1, 2, 3...)
            item_no = NumericTableWidgetItem(str(row_idx + 1), float(row_idx + 1))
            item_no.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_no.setForeground(QColor(COLOR_TEXT_MUTED))
            item_no.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row_idx, 0, item_no)

            # Col 1: Title
            item_title = QTableWidgetItem(t.get("title") or "(Untitled)")
            font = item_title.font()
            font.setBold(True)
            item_title.setFont(font)
            item_title.setForeground(QColor("#ffffff"))
            item_title.setToolTip(t.get("title") or "")
            item_title.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row_idx, 1, item_title)

            # Col 2: Artist
            item_artist = QTableWidgetItem(t.get("artist") or "Unknown Artist")
            item_artist.setForeground(QColor(COLOR_TEXT_SECONDARY))
            item_artist.setToolTip(t.get("artist") or "")
            item_artist.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row_idx, 2, item_artist)

            # Col 3: Album
            album_text = t.get("album") or "—"
            item_album = QTableWidgetItem(album_text)
            item_album.setForeground(QColor(COLOR_TEXT_MUTED))
            item_album.setToolTip(t.get("album") or "")
            item_album.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row_idx, 3, item_album)

            # Col 4: Key
            key_text = t.get("key") or "—"
            item_key = QTableWidgetItem(key_text)
            item_key.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if t.get("key"):
                item_key.setForeground(QColor(COLOR_BLUE))
            else:
                item_key.setForeground(QColor(COLOR_TEXT_MUTED))
            item_key.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row_idx, 4, item_key)

            # Col 5: BPM
            bpm_val = float(t.get("bpm") or 0.0)
            bpm_text = f"{bpm_val:.1f}" if bpm_val > 0 else "—"
            item_bpm = NumericTableWidgetItem(bpm_text, bpm_val)
            item_bpm.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_bpm.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row_idx, 5, item_bpm)

            # Col 6: Time
            dur_sec = float(t.get("duration_sec") or 0)
            item_time = NumericTableWidgetItem(t.get("duration_str") or "--:--", dur_sec)
            item_time.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_time.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row_idx, 6, item_time)

            # Col 7: Plays
            plays_val = int(t.get("play_count") or 0)
            item_plays = NumericTableWidgetItem(str(plays_val), float(plays_val))
            item_plays.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if plays_val > 0:
                item_plays.setForeground(QColor(COLOR_GREEN))
            item_plays.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row_idx, 7, item_plays)

            # Col 8: Bitrate
            br_val = int(t.get("bitrate") or 0)
            br_text = f"{br_val} kbps" if br_val > 0 else "—"
            item_br = NumericTableWidgetItem(br_text, float(br_val))
            item_br.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_br.setForeground(QColor(COLOR_TEXT_MUTED))
            item_br.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row_idx, 8, item_br)

            # Col 9: Location
            loc_text = t.get("filename") or t.get("folder_path") or "—"
            item_loc = QTableWidgetItem(loc_text)
            item_loc.setToolTip(t.get("folder_path") or t.get("filename") or "")
            item_loc.setForeground(QColor(COLOR_TEXT_MUTED))
            item_loc.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row_idx, 9, item_loc)

        self.table.setSortingEnabled(True)

    def _on_header_section_clicked(self, logical_index: int) -> None:
        """Handle column header click for SQLite-level sorting."""
        col_map = {
            0: "title",
            1: "title",
            2: "artist",
            3: "album",
            4: "key",
            5: "bpm",
            6: "duration",
            7: "play_count",
            8: "bitrate",
            9: "location",
        }
        if logical_index not in col_map:
            return

        sort_field = col_map[logical_index]
        if self.current_sort_col == logical_index:
            if self.current_sort_order == Qt.SortOrder.AscendingOrder:
                self.current_sort_order = Qt.SortOrder.DescendingOrder
            else:
                self.current_sort_order = Qt.SortOrder.AscendingOrder
        else:
            self.current_sort_col = logical_index
            # Default descending for numeric metrics (bpm, duration, plays, bitrate)
            if logical_index in (5, 6, 7, 8):
                self.current_sort_order = Qt.SortOrder.DescendingOrder
            else:
                self.current_sort_order = Qt.SortOrder.AscendingOrder

        self.current_sort_field = sort_field
        self._execute_search()

    def _on_copy_tracklist(self) -> None:
        """Copy formatted tracklist (01. Artist - Title) to system clipboard."""
        if not self.records:
            self.notify_status.emit("No tracks to copy.")
            return

        lines = []
        for idx, t in enumerate(self.records, 1):
            artist = t.get("artist") or "Unknown Artist"
            title = t.get("title") or "(Untitled)"
            lines.append(f"{idx:02d}. {artist} - {title}")

        tracklist_str = "\n".join(lines)
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(tracklist_str)
            self.notify_status.emit(f"📋 Copied {len(self.records)} tracks to clipboard!")
