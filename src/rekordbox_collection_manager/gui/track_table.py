"""Shared customizable track table component and delegates.

Provides a unified, rich, and highly customizable table widget for presenting
track lists across different application views (e.g. Collection Tracks and History Sessions).
Includes custom badge delegates (file formats, tags, playlists), Cmd/Ctrl+click link
navigation to Finder, contextual menus, numeric sorting, and clipboard export.
"""

from dataclasses import dataclass, field
import os
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import (
    QEvent,
    QModelIndex,
    QObject,
    QPoint,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication,
    QHeaderView,
    QMenu,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from rekordbox_collection_manager.gui.theme import (
    COLOR_BLUE,
    COLOR_GREEN,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_SECONDARY,
)
from rekordbox_collection_manager.utils import (
    open_in_folder,
    reveal_in_file_manager,
)


class TrackColumn:
    """Standard column identifier constants."""

    INDEX = "#"
    TITLE = "Title"
    ARTIST = "Artist"
    ALBUM = "Album"
    KEY = "Key"
    BPM = "BPM"
    TIME = "Time"
    PLAYS = "Plays"
    BITRATE = "Bitrate"
    FILE_TYPE = "Type"
    TAGS = "Tags"
    PLAYLISTS = "Playlists"
    FOLDER = "Folder"
    FILE_NAME = "File Name"


DEFAULT_COLLECTION_COLUMNS: List[str] = [
    TrackColumn.INDEX,
    TrackColumn.TITLE,
    TrackColumn.ARTIST,
    TrackColumn.KEY,
    TrackColumn.BPM,
    TrackColumn.TIME,
    TrackColumn.PLAYS,
    TrackColumn.BITRATE,
    TrackColumn.FILE_TYPE,
    TrackColumn.TAGS,
    TrackColumn.PLAYLISTS,
    TrackColumn.FOLDER,
    TrackColumn.FILE_NAME,
]

DEFAULT_HISTORY_COLUMNS: List[str] = [
    TrackColumn.INDEX,
    TrackColumn.TITLE,
    TrackColumn.ARTIST,
    TrackColumn.ALBUM,
    TrackColumn.KEY,
    TrackColumn.BPM,
    TrackColumn.TIME,
    TrackColumn.PLAYS,
    TrackColumn.BITRATE,
    TrackColumn.FILE_TYPE,
    TrackColumn.TAGS,
    TrackColumn.PLAYLISTS,
    TrackColumn.FOLDER,
    TrackColumn.FILE_NAME,
]


@dataclass
class TrackTableConfig:
    """Configuration options to customize the track table widget for different view intents."""

    columns: List[str] = field(default_factory=lambda: list(DEFAULT_COLLECTION_COLUMNS))
    index_mode: str = "row_number"  # "row_number" (1..N) or "track_no" (session play order)
    index_header_tooltip: str = "Sort by collection order / ID"
    sorting_mode: str = "server"  # "server" (emits sort_requested) or "client" (sorts local rows)
    enable_context_menu: bool = True
    enable_finder_clicks: bool = True


class NumericTableWidgetItem(QTableWidgetItem):
    """QTableWidgetItem with proper numeric sorting support."""

    def __init__(self, text: str, sort_value: float = 0.0):
        super().__init__(text)
        self.sort_value = sort_value

    def __lt__(self, other: Any) -> bool:
        if isinstance(other, NumericTableWidgetItem):
            return self.sort_value < other.sort_value
        return super().__lt__(other)


class ModifierKeyFilter(QObject):
    """Watches key events across the application to update link hover states immediately when Cmd/Ctrl is pressed."""

    def __init__(self, callback: Any, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.callback = callback

    def eventFilter(self, obj: Any, event: QEvent) -> bool:
        if event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            if isinstance(event, QKeyEvent) and event.key() in (
                Qt.Key.Key_Control,
                Qt.Key.Key_Meta,
            ):
                self.callback()
        return False


class PathLinkDelegate(QStyledItemDelegate):
    """Renders Folder and File cells with a subtle link underline when hovered with Cmd/Ctrl held."""

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
        style.drawPrimitive(
            QStyle.PrimitiveElement.PE_PanelItemViewItem, opt, painter, widget
        )

        text_str = index.data(Qt.ItemDataRole.DisplayRole) or ""
        path_data = index.data(Qt.ItemDataRole.UserRole)
        is_link_active = (
            bool(path_data)
            and text_str not in ("", "—")
            and getattr(widget, "hovered_link_cell", None)
            == (index.row(), index.column())
        )

        font = QFont(painter.font())
        if is_link_active:
            font.setUnderline(True)
            text_color = QColor("#82b4ea")
        else:
            if opt.state & QStyle.StateFlag.State_Selected:
                text_color = QColor(COLOR_TEXT_PRIMARY)
            else:
                text_color = QColor(COLOR_TEXT_MUTED)
        painter.setFont(font)
        fm = QFontMetrics(font)

        text_rect = option.rect.adjusted(6, 0, -6, 0)
        elided = fm.elidedText(
            text_str, Qt.TextElideMode.ElideMiddle, text_rect.width()
        )

        painter.setPen(text_color)
        painter.drawText(
            text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided
        )
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        text_str = index.data(Qt.ItemDataRole.DisplayRole) or ""
        if not text_str:
            return QSize(60, 36)
        font = QFont(option.font)
        fm = QFontMetrics(font)
        return QSize(min(fm.horizontalAdvance(text_str) + 20, 300), 36)


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
        self.badge_font = QFont()
        self.badge_font.setPointSize(9)
        self.badge_font.setWeight(QFont.Weight.DemiBold)
        self.fm = QFontMetrics(self.badge_font)

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
        style.drawPrimitive(
            QStyle.PrimitiveElement.PE_PanelItemViewItem, opt, painter, widget
        )

        items = index.data(Qt.ItemDataRole.UserRole)
        if not items:
            painter.setPen(QColor(COLOR_TEXT_MUTED))
            painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, "—")
            painter.restore()
            return

        if isinstance(items, str):
            items = [items]

        chip_h = 18
        y = option.rect.y() + (option.rect.height() - chip_h) // 2
        x = option.rect.x() + 5
        max_x = option.rect.right() - 5

        painter.setFont(self.badge_font)
        fm = self.fm

        max_chip_w = 140

        for i, text_val in enumerate(items):
            text_str = str(text_val)
            raw_w = fm.horizontalAdvance(text_str)
            if raw_w + 10 > max_chip_w:
                display_str = fm.elidedText(
                    text_str, Qt.TextElideMode.ElideRight, max_chip_w - 10
                )
                chip_w = fm.horizontalAdvance(display_str) + 10
            else:
                display_str = text_str
                chip_w = raw_w + 10

            if x + chip_w > max_x:
                remaining = len(items) - i
                if remaining > 0:
                    rem_str = f"+{remaining}"
                    rem_w = fm.horizontalAdvance(rem_str) + 8
                    if x + rem_w <= max_x:
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
            painter.drawText(chip_rect, Qt.AlignmentFlag.AlignCenter, display_str)
            x += chip_w + 4

        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        items = index.data(Qt.ItemDataRole.UserRole)
        if not items:
            return QSize(50, 36)
        if isinstance(items, str):
            items = [items]
        fm = self.fm
        total_w = 10
        for t in items:
            total_w += min(fm.horizontalAdvance(str(t)) + 10, 140) + 4
        return QSize(min(total_w, 400), 36)


class FileTypeBadgeDelegate(QStyledItemDelegate):
    """Renders audio file types as colored badge pills with lossless distinctions."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.badge_font = QFont()
        self.badge_font.setPointSize(9)
        self.badge_font.setBold(True)
        self.fm = QFontMetrics(self.badge_font)

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
        style.drawPrimitive(
            QStyle.PrimitiveElement.PE_PanelItemViewItem, opt, painter, widget
        )

        text_str = index.data(Qt.ItemDataRole.DisplayRole) or ""
        if not text_str or text_str == "—":
            painter.setPen(QColor(COLOR_TEXT_MUTED))
            painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, "—")
            painter.restore()
            return

        fmt = text_str.upper()
        if fmt in ("AIFF", "AIF", "FLAC", "WAV", "ALAC"):
            bg_color = QColor(14, 165, 233, 40)
            border_color = QColor(56, 189, 248, 120)
            text_color = QColor("#7dd3fc")
        elif fmt in ("MP3", "M4A", "AAC", "OGG"):
            bg_color = QColor(100, 116, 139, 35)
            border_color = QColor(148, 163, 184, 80)
            text_color = QColor("#cbd5e1")
        else:
            bg_color = QColor(255, 255, 255, 15)
            border_color = QColor(255, 255, 255, 40)
            text_color = QColor(COLOR_TEXT_MUTED)

        painter.setFont(self.badge_font)
        fm = self.fm

        badge_w = fm.horizontalAdvance(text_str) + 14
        badge_h = 20
        badge_x = option.rect.x() + (option.rect.width() - badge_w) // 2
        badge_y = option.rect.y() + (option.rect.height() - badge_h) // 2
        badge_rect = QRectF(badge_x, badge_y, badge_w, badge_h)

        painter.setBrush(QBrush(bg_color))
        painter.setPen(QPen(border_color, 1))
        painter.drawRoundedRect(badge_rect, 4, 4)

        painter.setPen(text_color)
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, text_str)
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        text_str = index.data(Qt.ItemDataRole.DisplayRole) or ""
        if not text_str:
            return QSize(50, 36)
        fm = self.fm
        return QSize(fm.horizontalAdvance(text_str) + 24, 36)


class TracksTableWidget(QTableWidget):
    """Customizable tracklist table widget supporting rich badges, Cmd-clicks, and context menu."""

    folder_clicked = Signal(str)
    file_clicked = Signal(str)
    sort_requested = Signal(str, Qt.SortOrder)
    status_message = Signal(str)
    track_detail_requested = Signal(dict)
    replace_file_requested = Signal(dict)

    def __init__(
        self,
        config: Optional[TrackTableConfig] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.config: TrackTableConfig = config or TrackTableConfig()
        self.records: List[Dict[str, Any]] = []
        self.hovered_link_cell: Optional[Tuple[int, int]] = None
        self.current_sort_col: int = 1
        self.current_sort_order: Qt.SortOrder = Qt.SortOrder.AscendingOrder

        self._folder_col_idx: int = -1
        self._file_col_idx: int = -1

        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)

        app = QApplication.instance()
        if app:
            self._key_filter = ModifierKeyFilter(self._on_modifier_changed, self)
            app.installEventFilter(self._key_filter)

        self._init_table_structure()

    def configure(self, config: TrackTableConfig) -> None:
        """Reconfigure table columns and behavior."""
        self.config = config
        self._init_table_structure()
        if self.records:
            self.set_tracks(self.records)

    def _init_table_structure(self) -> None:
        """Set up columns, resize modes, alignments, tooltips, and delegates."""
        cols = self.config.columns
        self.setColumnCount(len(cols))
        self.setHorizontalHeaderLabels(cols)

        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.verticalHeader().setDefaultSectionSize(36)
        self.verticalHeader().setVisible(False)

        header = self.horizontalHeader()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)

        if self.config.sorting_mode == "client":
            self.setSortingEnabled(True)
        else:
            self.setSortingEnabled(False)
            header.sectionClicked.connect(self._on_header_section_clicked)

        if self.config.enable_context_menu:
            self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.customContextMenuRequested.connect(self._on_table_context_menu)

        self.cellDoubleClicked.connect(self._on_cell_double_clicked)

        # Delegate singletons
        file_type_delegate = FileTypeBadgeDelegate(parent=self)
        tags_delegate = ChipBadgeDelegate(
            bg_color=QColor(139, 92, 246, 38),
            border_color=QColor(167, 139, 250, 95),
            text_color=QColor("#d8b4fe"),
            parent=self,
        )
        playlist_delegate = ChipBadgeDelegate(
            bg_color=QColor(20, 184, 166, 38),
            border_color=QColor(45, 212, 191, 95),
            text_color=QColor("#5eead4"),
            parent=self,
        )
        path_link_delegate = PathLinkDelegate(parent=self)

        self._folder_col_idx = -1
        self._file_col_idx = -1

        for col_idx, col_name in enumerate(cols):
            # Center alignments
            if col_name in (
                TrackColumn.INDEX,
                TrackColumn.KEY,
                TrackColumn.BPM,
                TrackColumn.TIME,
                TrackColumn.PLAYS,
                TrackColumn.BITRATE,
                TrackColumn.FILE_TYPE,
            ):
                h_item = self.horizontalHeaderItem(col_idx)
                if h_item:
                    h_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            # Resize modes and widths
            if col_name == TrackColumn.INDEX:
                header.setSectionResizeMode(col_idx, QHeaderView.ResizeMode.ResizeToContents)
            elif col_name == TrackColumn.TITLE:
                header.setSectionResizeMode(col_idx, QHeaderView.ResizeMode.Interactive)
                self.setColumnWidth(col_idx, 190)
            elif col_name == TrackColumn.ARTIST:
                header.setSectionResizeMode(col_idx, QHeaderView.ResizeMode.Interactive)
                self.setColumnWidth(col_idx, 130)
            elif col_name == TrackColumn.ALBUM:
                header.setSectionResizeMode(col_idx, QHeaderView.ResizeMode.Interactive)
                self.setColumnWidth(col_idx, 130)
            elif col_name in (
                TrackColumn.KEY,
                TrackColumn.BPM,
                TrackColumn.TIME,
                TrackColumn.PLAYS,
                TrackColumn.BITRATE,
                TrackColumn.FILE_TYPE,
            ):
                header.setSectionResizeMode(col_idx, QHeaderView.ResizeMode.ResizeToContents)
            elif col_name == TrackColumn.TAGS:
                header.setSectionResizeMode(col_idx, QHeaderView.ResizeMode.Interactive)
                self.setColumnWidth(col_idx, 220)
            elif col_name == TrackColumn.PLAYLISTS:
                header.setSectionResizeMode(col_idx, QHeaderView.ResizeMode.Interactive)
                self.setColumnWidth(col_idx, 270)
            elif col_name == TrackColumn.FOLDER:
                header.setSectionResizeMode(col_idx, QHeaderView.ResizeMode.Interactive)
                self.setColumnWidth(col_idx, 150)
            elif col_name == TrackColumn.FILE_NAME:
                header.setSectionResizeMode(col_idx, QHeaderView.ResizeMode.Stretch)

            # Assign delegates
            if col_name == TrackColumn.FILE_TYPE:
                self.setItemDelegateForColumn(col_idx, file_type_delegate)
            elif col_name == TrackColumn.TAGS:
                self.setItemDelegateForColumn(col_idx, tags_delegate)
            elif col_name == TrackColumn.PLAYLISTS:
                self.setItemDelegateForColumn(col_idx, playlist_delegate)
            elif col_name == TrackColumn.FOLDER:
                self.setItemDelegateForColumn(col_idx, path_link_delegate)
                self._folder_col_idx = col_idx
            elif col_name == TrackColumn.FILE_NAME:
                self.setItemDelegateForColumn(col_idx, path_link_delegate)
                self._file_col_idx = col_idx

            # Header Tooltips
            tip = self._get_header_tooltip(col_name)
            h_item = self.horizontalHeaderItem(col_idx)
            if h_item and tip:
                h_item.setToolTip(tip)

    def _get_header_tooltip(self, col_name: str) -> str:
        if col_name == TrackColumn.INDEX:
            return self.config.index_header_tooltip
        tooltips = {
            TrackColumn.TITLE: "Sort by Title",
            TrackColumn.ARTIST: "Sort by Artist",
            TrackColumn.ALBUM: "Sort by Album",
            TrackColumn.KEY: "Sort by Musical / Camelot Key",
            TrackColumn.BPM: "Sort by BPM (Tempos)",
            TrackColumn.TIME: "Sort by Track Duration",
            TrackColumn.PLAYS: "Sort by Play Count",
            TrackColumn.BITRATE: "Sort by Audio Bitrate",
            TrackColumn.FILE_TYPE: "Sort by File Type (MP3, AIFF, FLAC, etc.)",
            TrackColumn.TAGS: "Sort by Tags",
            TrackColumn.PLAYLISTS: "Sort by Real Playlists",
            TrackColumn.FOLDER: "Sort by Folder (Ctrl+Click or Cmd+Click cell to open in Finder)",
            TrackColumn.FILE_NAME: "Sort by File Name (Ctrl+Click or Cmd+Click cell to reveal with highlight in Finder)",
        }
        return tooltips.get(col_name, "")

    def _on_modifier_changed(self) -> None:
        self._update_hover_state()

    def _update_hover_state(self, mouse_pos: Optional[QPoint] = None) -> None:
        if not self.isVisible() or not self.config.enable_finder_clicks:
            return
        if mouse_pos is None:
            mouse_pos = self.viewport().mapFromGlobal(QCursor.pos())

        new_cell: Optional[Tuple[int, int]] = None
        if self.viewport().rect().contains(mouse_pos):
            mod = QApplication.keyboardModifiers()
            is_cmd = bool(
                mod & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)
            )
            if is_cmd:
                item = self.itemAt(mouse_pos)
                if item and item.column() in (self._folder_col_idx, self._file_col_idx):
                    path_data = item.data(Qt.ItemDataRole.UserRole)
                    if path_data and str(path_data) not in ("", "—"):
                        new_cell = (item.row(), item.column())

        if new_cell != self.hovered_link_cell:
            self.hovered_link_cell = new_cell
            if new_cell is not None:
                self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self.viewport().unsetCursor()
            self.viewport().update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._update_hover_state(event.position().toPoint())
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        if self.hovered_link_cell is not None:
            self.hovered_link_cell = None
            self.viewport().unsetCursor()
            self.viewport().update()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.config.enable_finder_clicks:
            mod = event.modifiers()
            if mod & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
                item = self.itemAt(event.position().toPoint())
                if item:
                    col = item.column()
                    if col == self._folder_col_idx:
                        path_data = item.data(Qt.ItemDataRole.UserRole)
                        if path_data and str(path_data) not in ("", "—"):
                            self.open_folder(str(path_data))
                            self.folder_clicked.emit(str(path_data))
                            event.accept()
                            return
                    elif col == self._file_col_idx:
                        path_data = item.data(Qt.ItemDataRole.UserRole)
                        if path_data and str(path_data) not in ("", "—"):
                            self.reveal_file(str(path_data))
                            self.file_clicked.emit(str(path_data))
                            event.accept()
                            return
        super().mousePressEvent(event)

    def _on_header_section_clicked(self, logical_index: int) -> None:
        """Handle header click for server-side sorting."""
        if self.config.sorting_mode != "server":
            return

        cols = self.config.columns
        if logical_index < 0 or logical_index >= len(cols):
            return

        col_name = cols[logical_index]
        field_map = {
            TrackColumn.INDEX: "title",
            TrackColumn.TITLE: "title",
            TrackColumn.ARTIST: "artist",
            TrackColumn.ALBUM: "album",
            TrackColumn.KEY: "key",
            TrackColumn.BPM: "bpm",
            TrackColumn.TIME: "duration",
            TrackColumn.PLAYS: "play_count",
            TrackColumn.BITRATE: "bitrate",
            TrackColumn.FILE_TYPE: "file_type",
            TrackColumn.TAGS: "tags",
            TrackColumn.PLAYLISTS: "playlists",
            TrackColumn.FOLDER: "folder",
            TrackColumn.FILE_NAME: "filename",
        }
        sort_field = field_map.get(col_name, "title")

        if self.current_sort_col == logical_index:
            if self.current_sort_order == Qt.SortOrder.AscendingOrder:
                self.current_sort_order = Qt.SortOrder.DescendingOrder
            else:
                self.current_sort_order = Qt.SortOrder.AscendingOrder
        else:
            self.current_sort_col = logical_index
            if col_name in (
                TrackColumn.BPM,
                TrackColumn.TIME,
                TrackColumn.PLAYS,
                TrackColumn.BITRATE,
            ):
                self.current_sort_order = Qt.SortOrder.DescendingOrder
            else:
                self.current_sort_order = Qt.SortOrder.AscendingOrder

        self.sort_requested.emit(sort_field, self.current_sort_order)

    def set_tracks(self, records: List[Dict[str, Any]]) -> None:
        """Populate the table with records according to the active configuration."""
        self.records = records
        self.setSortingEnabled(False)
        self.setRowCount(0)

        cols = self.config.columns

        for row_idx, t in enumerate(records):
            self.insertRow(row_idx)

            for col_idx, col_name in enumerate(cols):
                item = self._create_item_for_column(col_name, t, row_idx)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.setItem(row_idx, col_idx, item)

        if self.config.sorting_mode == "client":
            self.setSortingEnabled(True)

    def update_track_playlists(self, row_idx: int, playlists: List[str]) -> None:
        """Update the playlists column cell for a specific row without rebuilding table."""
        if TrackColumn.PLAYLISTS in self.config.columns and 0 <= row_idx < self.rowCount():
            col_idx = self.config.columns.index(TrackColumn.PLAYLISTS)
            item = QTableWidgetItem(", ".join(playlists) if playlists else "—")
            item.setData(Qt.ItemDataRole.UserRole, playlists)
            item.setToolTip(
                f"Playlists: {', '.join(playlists)}"
                if playlists
                else "Not in any playlist"
            )
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.setItem(row_idx, col_idx, item)
            if 0 <= row_idx < len(self.records):
                self.records[row_idx]["playlists"] = list(playlists)

    def _create_item_for_column(
        self, col_name: str, t: Dict[str, Any], row_idx: int
    ) -> QTableWidgetItem:
        if col_name == TrackColumn.INDEX:
            if self.config.index_mode == "track_no":
                seq_val = int(t.get("track_no") or (row_idx + 1))
            else:
                seq_val = row_idx + 1
            item = NumericTableWidgetItem(str(seq_val), float(seq_val))
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setForeground(QColor(COLOR_TEXT_MUTED))
            return item

        if col_name == TrackColumn.TITLE:
            title_text = t.get("title") or "(Untitled)"
            item = QTableWidgetItem(title_text)
            font = item.font()
            font.setBold(True)
            item.setFont(font)
            item.setForeground(QColor("#ffffff"))
            item.setToolTip(title_text)
            return item

        if col_name == TrackColumn.ARTIST:
            artist_text = t.get("artist") or "Unknown Artist"
            item = QTableWidgetItem(artist_text)
            item.setForeground(QColor(COLOR_TEXT_SECONDARY))
            item.setToolTip(artist_text)
            return item

        if col_name == TrackColumn.ALBUM:
            album_text = t.get("album") or "—"
            item = QTableWidgetItem(album_text)
            item.setForeground(QColor(COLOR_TEXT_MUTED))
            item.setToolTip(t.get("album") or "")
            return item

        if col_name == TrackColumn.KEY:
            key_text = t.get("key") or "—"
            item = QTableWidgetItem(key_text)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if t.get("key"):
                item.setForeground(QColor(COLOR_BLUE))
            else:
                item.setForeground(QColor(COLOR_TEXT_MUTED))
            return item

        if col_name == TrackColumn.BPM:
            bpm_val = float(t.get("bpm") or 0.0)
            bpm_text = f"{bpm_val:.1f}" if bpm_val > 0 else "—"
            item = NumericTableWidgetItem(bpm_text, bpm_val)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            return item

        if col_name == TrackColumn.TIME:
            dur_sec = float(t.get("duration_sec") or 0)
            item = NumericTableWidgetItem(t.get("duration_str") or "--:--", dur_sec)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            return item

        if col_name == TrackColumn.PLAYS:
            plays_val = int(t.get("play_count") or 0)
            item = NumericTableWidgetItem(str(plays_val), float(plays_val))
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if plays_val > 0:
                item.setForeground(QColor(COLOR_GREEN))
            return item

        if col_name == TrackColumn.BITRATE:
            br_val = int(t.get("bitrate") or 0)
            br_text = f"{br_val} kbps" if br_val > 0 else "—"
            item = NumericTableWidgetItem(br_text, float(br_val))
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setForeground(QColor(COLOR_TEXT_MUTED))
            return item

        if col_name == TrackColumn.FILE_TYPE:
            file_type = t.get("file_type") or "—"
            item = QTableWidgetItem(file_type)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setToolTip(f"Format: {file_type}")
            return item

        if col_name == TrackColumn.TAGS:
            tags = t.get("tags") or []
            item = QTableWidgetItem(", ".join(tags) if tags else "—")
            item.setData(Qt.ItemDataRole.UserRole, tags)
            item.setToolTip(f"Tags: {', '.join(tags)}" if tags else "No tags")
            return item

        if col_name == TrackColumn.PLAYLISTS:
            playlists = t.get("playlists") or []
            item = QTableWidgetItem(", ".join(playlists) if playlists else "—")
            item.setData(Qt.ItemDataRole.UserRole, playlists)
            item.setToolTip(
                f"Playlists: {', '.join(playlists)}"
                if playlists
                else "Not in any playlist"
            )
            return item

        if col_name == TrackColumn.FOLDER:
            folder_name = t.get("folder_name") or "—"
            folder_dir = t.get("folder_dir") or t.get("folder_path") or ""
            item = QTableWidgetItem(folder_name)
            item.setData(Qt.ItemDataRole.UserRole, folder_dir)
            item.setToolTip(
                f"Folder: {folder_dir}\n💡 Ctrl+Click or Cmd+Click to open folder in Finder"
                if folder_dir
                else "Folder: (None)"
            )
            item.setForeground(QColor(COLOR_TEXT_MUTED))
            return item

        if col_name == TrackColumn.FILE_NAME:
            file_name = t.get("file_name") or t.get("filename") or "—"
            file_path = t.get("file_path") or t.get("folder_path") or ""
            item = QTableWidgetItem(file_name)
            item.setData(Qt.ItemDataRole.UserRole, file_path)
            item.setToolTip(
                f"File: {file_path}\n💡 Ctrl+Click or Cmd+Click to reveal with highlight in Finder"
                if file_path
                else "File: (None)"
            )
            item.setForeground(QColor(COLOR_TEXT_MUTED))
            return item

        return QTableWidgetItem("—")

    def open_folder(self, folder_path: str) -> None:
        """Open folder in Finder/File Manager and emit status update."""
        if not folder_path or folder_path == "—":
            msg = "⚠️ No folder path available."
            self.status_message.emit(msg)
            return
        if folder_path.startswith("apple-music:") or folder_path.startswith("spotify:"):
            msg = f"Streaming track URI: {folder_path}"
            self.status_message.emit(msg)
            return

        success = open_in_folder(folder_path)
        if success:
            msg = f"📂 Opened folder: {os.path.basename(folder_path) or folder_path}"
        else:
            msg = f"❌ Folder not found on disk: {folder_path}"
        self.status_message.emit(msg)

    def reveal_file(self, file_path: str) -> None:
        """Reveal file in Finder/File Manager and emit status update."""
        if not file_path or file_path == "—":
            msg = "⚠️ No file path available."
            self.status_message.emit(msg)
            return
        if file_path.startswith("apple-music:") or file_path.startswith("spotify:"):
            msg = f"Streaming track URI: {file_path}"
            self.status_message.emit(msg)
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
        self.status_message.emit(msg)

    def _on_cell_double_clicked(self, row: int, column: int) -> None:
        """Double clicking a track row emits track_detail_requested with track record."""
        if 0 <= row < len(self.records):
            self.track_detail_requested.emit(self.records[row])

    def _on_table_context_menu(self, pos: QPoint) -> None:
        """Right-click menu on track rows."""
        item = self.itemAt(pos)
        if not item:
            return
        row = item.row()
        if row < 0 or row >= len(self.records):
            return

        track = self.records[row]
        folder_dir = track.get("folder_dir") or track.get("folder_path") or ""
        file_path = track.get("file_path") or track.get("filename") or ""
        title = track.get("title") or ""
        artist = track.get("artist") or ""

        menu = QMenu(self)
        act_details = menu.addAction("ℹ️ View Track Details...")

        is_mp3 = (
            str(track.get("file_type", "")).upper() == "MP3"
            or str(track.get("file_name", "")).lower().endswith(".mp3")
            or str(file_path).lower().endswith(".mp3")
        )
        if is_mp3:
            act_replace = menu.addAction("🔄 Upgrade MP3 to AIFF...")
        else:
            act_replace = menu.addAction("🔄 Upgrade to AIFF (MP3 only)")
            act_replace.setEnabled(False)
        menu.addSeparator()
        act_open_folder = menu.addAction("📁 Open Containing Folder")
        act_reveal_file = menu.addAction("🔍 Reveal in Finder")
        menu.addSeparator()
        act_copy_file = menu.addAction("📋 Copy File Path")
        act_copy_folder = menu.addAction("📋 Copy Folder Path")
        act_copy_track = menu.addAction("📋 Copy Track (Artist - Title)")

        action = menu.exec(self.viewport().mapToGlobal(pos))
        if action == act_details:
            self.track_detail_requested.emit(track)
        elif action == act_replace:
            self.replace_file_requested.emit(track)
        elif action == act_open_folder:
            self.open_folder(folder_dir)
        elif action == act_reveal_file:
            self.reveal_file(file_path)
        elif action == act_copy_file:
            cb = QApplication.clipboard()
            if cb and file_path:
                cb.setText(file_path)
                self.status_message.emit("📋 Copied file path to clipboard!")
        elif action == act_copy_folder:
            cb = QApplication.clipboard()
            if cb and folder_dir:
                cb.setText(folder_dir)
                self.status_message.emit("📋 Copied folder path to clipboard!")
        elif action == act_copy_track:
            cb = QApplication.clipboard()
            if cb:
                cb.setText(f"{artist} - {title}")
                self.status_message.emit(f"📋 Copied '{artist} - {title}' to clipboard!")

    def copy_tracklist_to_clipboard(
        self, records: Optional[List[Dict[str, Any]]] = None
    ) -> int:
        """Copy formatted tracklist (01. Artist - Title) to system clipboard."""
        target_records = records if records is not None else self.records
        if not target_records:
            self.status_message.emit("No tracks to copy.")
            return 0

        lines = []
        for idx, t in enumerate(target_records, 1):
            artist = t.get("artist") or "Unknown Artist"
            title = t.get("title") or "(Untitled)"
            lines.append(f"{idx:02d}. {artist} - {title}")

        tracklist_str = "\n".join(lines)
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(tracklist_str)
            self.status_message.emit(
                f"📋 Copied {len(target_records)} tracks to clipboard!"
            )
        return len(target_records)
