"""Collection Tracks View Screen.

Presents complete collection tracklist with rich metadata in a 12-column table
featuring audio file format badges, containing folder location paths,
tags rendered as chips, real playlist chips, play count filtering,
configurable limits, interactive column sorting, and instant search.
"""

from typing import Any, Dict, List, Optional
from PySide6.QtCore import QModelIndex, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
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


class ChipBadgeDelegate(QStyledItemDelegate):
    """Renders a list of strings as sleek, rounded pill/chip badges."""

    def __init__(
        self,
        bg_color: QColor,
        border_color: QColor,
        text_color: QColor,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.bg_color = bg_color
        self.border_color = border_color
        self.text_color = text_color

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Draw standard item background (selection / alternate rows)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        widget = option.widget
        style = widget.style() if widget else QApplication.style()
        style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, opt, painter, widget)

        items = index.data(Qt.ItemDataRole.UserRole)
        if not items:
            # Draw dash if empty
            painter.setPen(QColor(COLOR_TEXT_MUTED))
            painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, "—")
            painter.restore()
            return

        if isinstance(items, str):
            items = [items]

        chip_h = 20
        y = option.rect.y() + (option.rect.height() - chip_h) // 2
        x = option.rect.x() + 6
        max_x = option.rect.right() - 6

        font = QFont(painter.font())
        font.setPointSize(10)
        font.setBold(True)
        painter.setFont(font)
        fm = QFontMetrics(font)

        for i, text_val in enumerate(items):
            text_str = str(text_val)
            text_w = fm.horizontalAdvance(text_str)
            chip_w = text_w + 14  # 7px horizontal padding each side

            if x + chip_w > max_x:
                remaining = len(items) - i
                if remaining > 0 and x + 24 <= max_x:
                    rem_str = f"+{remaining}"
                    rem_w = fm.horizontalAdvance(rem_str) + 10
                    rem_rect = QRectF(x, y, rem_w, chip_h)
                    painter.setBrush(QBrush(QColor(40, 40, 50, 180)))
                    painter.setPen(QPen(QColor(80, 80, 95), 1))
                    painter.drawRoundedRect(rem_rect, 4, 4)
                    painter.setPen(QColor(COLOR_TEXT_SECONDARY))
                    painter.drawText(rem_rect, Qt.AlignmentFlag.AlignCenter, rem_str)
                break

            chip_rect = QRectF(x, y, chip_w, chip_h)
            painter.setBrush(QBrush(self.bg_color))
            painter.setPen(QPen(self.border_color, 1))
            painter.drawRoundedRect(chip_rect, 4, 4)

            painter.setPen(self.text_color)
            painter.drawText(chip_rect, Qt.AlignmentFlag.AlignCenter, text_str)
            x += chip_w + 5

        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        items = index.data(Qt.ItemDataRole.UserRole)
        if not items:
            return QSize(50, 36)
        if isinstance(items, str):
            items = [items]
        font = QFont(option.font)
        font.setPointSize(10)
        font.setBold(True)
        fm = QFontMetrics(font)
        total_w = 12
        for t in items:
            total_w += fm.horizontalAdvance(str(t)) + 14 + 5
        return QSize(min(total_w, 350), 36)


class FileTypeBadgeDelegate(QStyledItemDelegate):
    """Renders audio file types as colored badge pills with lossless distinctions."""

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        widget = option.widget
        style = widget.style() if widget else QApplication.style()
        style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, opt, painter, widget)

        text_str = index.data(Qt.ItemDataRole.DisplayRole) or ""
        if not text_str or text_str == "—":
            painter.setPen(QColor(COLOR_TEXT_MUTED))
            painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, "—")
            painter.restore()
            return

        fmt = text_str.upper()
        if fmt in ("AIFF", "AIF", "WAV"):
            # Lossless gold / amber
            bg = QColor(245, 158, 11, 38)
            border = QColor(251, 191, 36, 100)
            fg = QColor("#fde68a")
        elif fmt == "FLAC":
            # Lossless emerald
            bg = QColor(16, 185, 129, 38)
            border = QColor(52, 211, 153, 100)
            fg = QColor("#a7f3d0")
        elif fmt == "MP3":
            # Lossy blue
            bg = QColor(59, 130, 246, 35)
            border = QColor(96, 165, 250, 90)
            fg = QColor("#93c5fd")
        elif fmt in ("M4A", "AAC", "ALAC"):
            # Purple
            bg = QColor(168, 85, 247, 35)
            border = QColor(192, 132, 252, 90)
            fg = QColor("#e9d5ff")
        elif "APPLE" in fmt or "SPOTIFY" in fmt:
            # Pink / stream
            bg = QColor(236, 72, 153, 35)
            border = QColor(244, 114, 182, 90)
            fg = QColor("#fbcfe8")
        else:
            bg = QColor(100, 116, 139, 35)
            border = QColor(148, 163, 184, 90)
            fg = QColor("#cbd5e1")

        font = QFont(painter.font())
        font.setPointSize(10)
        font.setBold(True)
        painter.setFont(font)
        fm = QFontMetrics(font)

        text_w = fm.horizontalAdvance(text_str)
        badge_w = text_w + 14
        badge_h = 20
        x = option.rect.x() + (option.rect.width() - badge_w) // 2
        y = option.rect.y() + (option.rect.height() - badge_h) // 2

        badge_rect = QRectF(x, y, badge_w, badge_h)
        painter.setBrush(QBrush(bg))
        painter.setPen(QPen(border, 1))
        painter.drawRoundedRect(badge_rect, 4, 4)

        painter.setPen(fg)
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, text_str)
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        text_str = index.data(Qt.ItemDataRole.DisplayRole) or ""
        if not text_str:
            return QSize(50, 36)
        font = QFont(option.font)
        font.setPointSize(10)
        font.setBold(True)
        fm = QFontMetrics(font)
        return QSize(fm.horizontalAdvance(text_str) + 24, 36)


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
        self.search_input.setPlaceholderText("🔍 Search title, artist, key, tags, playlist, or filename...")
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

        # 3. Main Tracks Table (12 Columns)
        self.table = QTableWidget()
        self.table.setColumnCount(12)
        self.table.setHorizontalHeaderLabels([
            "#",
            "Title",
            "Artist",
            "Key",
            "BPM",
            "Time",
            "Plays",
            "Bitrate",
            "Type",
            "Tags",
            "Playlists",
            "Location",
        ])
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.verticalHeader().setVisible(False)

        # Custom delegates for chip badges and file type badges
        self.file_type_delegate = FileTypeBadgeDelegate(parent=self.table)
        self.tags_delegate = ChipBadgeDelegate(
            bg_color=QColor(139, 92, 246, 38),
            border_color=QColor(167, 139, 250, 95),
            text_color=QColor("#d8b4fe"),
            parent=self.table,
        )
        self.playlist_delegate = ChipBadgeDelegate(
            bg_color=QColor(20, 184, 166, 38),
            border_color=QColor(45, 212, 191, 95),
            text_color=QColor("#5eead4"),
            parent=self.table,
        )

        self.table.setItemDelegateForColumn(8, self.file_type_delegate)
        self.table.setItemDelegateForColumn(9, self.tags_delegate)
        self.table.setItemDelegateForColumn(10, self.playlist_delegate)

        # Column resize modes
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(8, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(9, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(10, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(11, QHeaderView.ResizeMode.Stretch)

        self.table.setColumnWidth(1, 200)  # Title
        self.table.setColumnWidth(2, 140)  # Artist
        self.table.setColumnWidth(9, 150)  # Tags
        self.table.setColumnWidth(10, 170)  # Playlists

        # Center align metric headers
        for col in (0, 3, 4, 5, 6, 7, 8):
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
            3: "Sort by Musical / Camelot Key",
            4: "Sort by BPM (Tempos)",
            5: "Sort by Track Duration",
            6: "Sort by Play Count",
            7: "Sort by Audio Bitrate",
            8: "Sort by File Type (MP3, AIFF, FLAC, etc.)",
            9: "Sort by Tags",
            10: "Sort by Real Playlists",
            11: "Sort by File Location (Folder & File)",
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
        """Render records into the 12-column read-only table."""
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

            # Col 3: Key
            key_text = t.get("key") or "—"
            item_key = QTableWidgetItem(key_text)
            item_key.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if t.get("key"):
                item_key.setForeground(QColor(COLOR_BLUE))
            else:
                item_key.setForeground(QColor(COLOR_TEXT_MUTED))
            item_key.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row_idx, 3, item_key)

            # Col 4: BPM
            bpm_val = float(t.get("bpm") or 0.0)
            bpm_text = f"{bpm_val:.1f}" if bpm_val > 0 else "—"
            item_bpm = NumericTableWidgetItem(bpm_text, bpm_val)
            item_bpm.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_bpm.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row_idx, 4, item_bpm)

            # Col 5: Time
            dur_sec = float(t.get("duration_sec") or 0)
            item_time = NumericTableWidgetItem(t.get("duration_str") or "--:--", dur_sec)
            item_time.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_time.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row_idx, 5, item_time)

            # Col 6: Plays
            plays_val = int(t.get("play_count") or 0)
            item_plays = NumericTableWidgetItem(str(plays_val), float(plays_val))
            item_plays.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if plays_val > 0:
                item_plays.setForeground(QColor(COLOR_GREEN))
            item_plays.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row_idx, 6, item_plays)

            # Col 7: Bitrate
            br_val = int(t.get("bitrate") or 0)
            br_text = f"{br_val} kbps" if br_val > 0 else "—"
            item_br = NumericTableWidgetItem(br_text, float(br_val))
            item_br.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_br.setForeground(QColor(COLOR_TEXT_MUTED))
            item_br.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row_idx, 7, item_br)

            # Col 8: Type (Audio file format: MP3, AIFF, FLAC, WAV, M4A...)
            file_type = t.get("file_type") or "—"
            item_type = QTableWidgetItem(file_type)
            item_type.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_type.setToolTip(f"Format: {file_type}")
            item_type.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row_idx, 8, item_type)

            # Col 9: Tags (Chips)
            tags = t.get("tags") or []
            item_tags = QTableWidgetItem(", ".join(tags) if tags else "—")
            item_tags.setData(Qt.ItemDataRole.UserRole, tags)
            item_tags.setToolTip(f"Tags: {', '.join(tags)}" if tags else "No tags")
            item_tags.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row_idx, 9, item_tags)

            # Col 10: Playlists (Chips - real playlists)
            playlists = t.get("playlists") or []
            item_pl = QTableWidgetItem(", ".join(playlists) if playlists else "—")
            item_pl.setData(Qt.ItemDataRole.UserRole, playlists)
            item_pl.setToolTip(f"Playlists: {', '.join(playlists)}" if playlists else "Not in any playlist")
            item_pl.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row_idx, 10, item_pl)

            # Col 11: Location (containing_folder/filename)
            loc_disp = t.get("location_display") or t.get("filename") or t.get("folder_path") or "—"
            item_loc = QTableWidgetItem(loc_disp)
            full_path = t.get("folder_path") or t.get("filename") or ""
            item_loc.setToolTip(full_path)
            item_loc.setForeground(QColor(COLOR_TEXT_MUTED))
            item_loc.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row_idx, 11, item_loc)

        self.table.setSortingEnabled(True)

    def _on_header_section_clicked(self, logical_index: int) -> None:
        """Handle column header click for SQLite-level sorting."""
        col_map = {
            0: "title",
            1: "title",
            2: "artist",
            3: "key",
            4: "bpm",
            5: "duration",
            6: "play_count",
            7: "bitrate",
            8: "file_type",
            9: "tags",
            10: "playlists",
            11: "location",
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
            if logical_index in (4, 5, 6, 7):
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
