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
    QAbstractTableModel,
    QEvent,
    QModelIndex,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    QStringListModel,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QHelpEvent,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication,
    QCompleter,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QToolTip,
    QWidget,
)
from sqlalchemy.engine import Engine

from rekordbox_collection_manager.edit_play_count import (
    add_track_to_playlist,
    add_track_to_tag,
    fetch_all_playlists,
    fetch_all_tags,
    remove_track_from_playlist,
    remove_track_from_tag,
)
from rekordbox_collection_manager.gui.chip import (
    CHIP_SCHEMES,
    ChipSearchLineEdit,
)
from rekordbox_collection_manager.gui.create_tag_dialog import CreateTagDialog
from rekordbox_collection_manager.gui.theme import (
    COLOR_AMBER,
    COLOR_AMBER_BG,
    COLOR_AMBER_BORDER,
    COLOR_BG_HOVER,
    COLOR_BG_INPUT,
    COLOR_BG_PANEL,
    COLOR_BLUE,
    COLOR_BLUE_BG,
    COLOR_BORDER,
    COLOR_BORDER_FOCUS,
    COLOR_GREEN,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_SECONDARY,
)
from rekordbox_collection_manager.utils import (
    is_rekordbox_running,
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


@dataclass
class _ChipLayoutItem:
    index: int
    text_str: str
    display_str: str
    rect: QRectF
    btn_rect: QRectF


@dataclass
class _PillLayoutResult:
    chips: List[_ChipLayoutItem]
    rem_count: int
    rem_str: str
    rem_rect: Optional[QRectF]
    end_x: int


class ChipBadgeDelegate(QStyledItemDelegate):
    """Renders a list of strings as sleek, rounded pill/chip badges with hover-to-delete support."""

    def __init__(
        self,
        bg_color: QColor,
        border_color: QColor,
        text_color: QColor,
        bg_hover: Optional[QColor] = None,
        border_hover: Optional[QColor] = None,
        removable: bool = True,
        is_item_staged: Optional[Any] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.bg_color = bg_color
        self.border_color = border_color
        self.text_color = text_color
        self.bg_hover = (
            bg_hover
            if bg_hover is not None
            else QColor(
                bg_color.red(),
                bg_color.green(),
                bg_color.blue(),
                min(255, max(45, int(bg_color.alpha() * 1.8))),
            )
        )
        self.border_hover = (
            border_hover
            if border_hover is not None
            else QColor(
                border_color.red(),
                border_color.green(),
                border_color.blue(),
                min(255, max(120, int(border_color.alpha() * 1.8))),
            )
        )
        self.removable = removable
        self.is_item_staged = is_item_staged

        self.hovered_cell: Optional[Tuple[int, int]] = None
        self.hovered_chip_idx: Optional[int] = None
        self.hovered_delete_btn: bool = False

        self.badge_font = QFont()
        self.badge_font.setPointSize(9)
        self.badge_font.setWeight(QFont.Weight.DemiBold)
        self.fm = QFontMetrics(self.badge_font)

    def _layout_chips(self, rect: QRectF, items: Any) -> _PillLayoutResult:
        """Compute the positions and visible items for badges/chips in the given cell rect.

        Guarantees that:
        1. If all items fit, all items are shown with no overflow badge.
        2. If not all items fit, the maximum number of items that fit ALONGSIDE the
           '+N' overflow badge are shown, and the '+N' overflow badge is ALWAYS
           rendered (preventing situations where items are omitted but '+N' disappears).
        3. If only 1 item exists and it overflows, it is gracefully elided to fit available width.
        """
        rect_f = QRectF(rect)
        start_x = rect_f.x() + 5.0
        chip_h = 20.0
        y = rect_f.y() + (rect_f.height() - chip_h) / 2.0
        max_x = rect_f.right() - 5.0
        avail_w = max_x - start_x

        if not items:
            return _PillLayoutResult(
                chips=[], rem_count=0, rem_str="", rem_rect=None, end_x=int(start_x)
            )

        if isinstance(items, str):
            item_list = [items]
        elif isinstance(items, (list, tuple)):
            item_list = list(dict.fromkeys(items))
        else:
            item_list = list(items)

        if not item_list:
            return _PillLayoutResult(
                chips=[], rem_count=0, rem_str="", rem_rect=None, end_x=int(start_x)
            )

        if avail_w <= 0:
            return _PillLayoutResult(
                chips=[],
                rem_count=len(item_list),
                rem_str=f"+{len(item_list)}",
                rem_rect=None,
                end_x=int(start_x),
            )

        max_chip_w = 140.0
        btn_size = 14.0
        gap = 8.0
        pad = 14.0
        fm = self.fm
        n = len(item_list)

        measured_items: List[Tuple[int, str, str, float]] = []
        for i, text_val in enumerate(item_list):
            text_str = str(text_val)
            raw_w = float(fm.horizontalAdvance(text_str))
            if raw_w + pad > max_chip_w:
                display_str = fm.elidedText(
                    text_str, Qt.TextElideMode.ElideRight, int(max_chip_w - pad)
                )
                chip_w = float(fm.horizontalAdvance(display_str) + pad)
            else:
                display_str = text_str
                chip_w = raw_w + pad
            measured_items.append((i, text_str, display_str, chip_w))

        # Special case: single item that overflows is elided to available width
        if n == 1:
            i, text_str, display_str, chip_w = measured_items[0]
            if chip_w > avail_w:
                display_str = fm.elidedText(
                    text_str, Qt.TextElideMode.ElideRight, max(0, int(avail_w - pad))
                )
                chip_w = min(avail_w, float(fm.horizontalAdvance(display_str) + pad))
            chip_rect = QRectF(start_x, y, chip_w, chip_h)
            btn_cx = chip_rect.right() - 2.0
            btn_cy = chip_rect.top() + 2.0
            btn_rect = QRectF(btn_cx - btn_size / 2.0, btn_cy - btn_size / 2.0, btn_size, btn_size)
            return _PillLayoutResult(
                chips=[_ChipLayoutItem(i, text_str, display_str, chip_rect, btn_rect)],
                rem_count=0,
                rem_str="",
                rem_rect=None,
                end_x=int(start_x + chip_w),
            )

        # Check if all items fit
        total_w_all = sum(item[3] for item in measured_items) + gap * (n - 1)
        if total_w_all <= avail_w:
            chips = []
            cur_x = start_x
            for i, text_str, display_str, chip_w in measured_items:
                chip_rect = QRectF(cur_x, y, chip_w, chip_h)
                btn_cx = chip_rect.right() - 2.0
                btn_cy = chip_rect.top() + 2.0
                btn_rect = QRectF(btn_cx - btn_size / 2.0, btn_cy - btn_size / 2.0, btn_size, btn_size)
                chips.append(_ChipLayoutItem(i, text_str, display_str, chip_rect, btn_rect))
                cur_x += chip_w + gap
            return _PillLayoutResult(
                chips=chips,
                rem_count=0,
                rem_str="",
                rem_rect=None,
                end_x=int(cur_x - gap) if chips else int(start_x),
            )

        # Not all items fit: find the largest k in [n - 1 .. 0] such that k chips
        # AND the '+{n - k}' badge fit within avail_w.
        best_k = 0
        for k in range(n - 1, -1, -1):
            rem = n - k
            rem_str = f"+{rem}"
            rem_w = float(fm.horizontalAdvance(rem_str) + 8)
            if k > 0:
                chips_w = sum(measured_items[j][3] for j in range(k))
                w_needed = chips_w + (gap * k) + rem_w
            else:
                w_needed = rem_w

            if w_needed <= avail_w:
                best_k = k
                break

        chips = []
        cur_x = start_x
        for j in range(best_k):
            i, text_str, display_str, chip_w = measured_items[j]
            chip_rect = QRectF(cur_x, y, chip_w, chip_h)
            btn_cx = chip_rect.right() - 2.0
            btn_cy = chip_rect.top() + 2.0
            btn_rect = QRectF(btn_cx - btn_size / 2.0, btn_cy - btn_size / 2.0, btn_size, btn_size)
            chips.append(_ChipLayoutItem(i, text_str, display_str, chip_rect, btn_rect))
            cur_x += chip_w + gap

        rem_count = n - best_k
        rem_str = f"+{rem_count}"
        rem_w = float(fm.horizontalAdvance(rem_str) + 8)
        if best_k == 0 and rem_w > avail_w:
            # Column is extremely tight; clip badge width to available space if possible
            if avail_w >= 10.0:
                rem_rect = QRectF(cur_x, y, avail_w, chip_h)
            else:
                rem_rect = None
        else:
            rem_rect = QRectF(cur_x, y, rem_w, chip_h)

        end_x = int(rem_rect.right()) if rem_rect is not None else int(cur_x)
        return _PillLayoutResult(
            chips=chips,
            rem_count=rem_count,
            rem_str=rem_str,
            rem_rect=rem_rect,
            end_x=end_x,
        )

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

        painter.setFont(self.badge_font)
        is_cell_hovered = self.hovered_cell == (index.row(), index.column())

        layout = self._layout_chips(QRectF(option.rect), items)

        for chip in layout.chips:
            is_chip_hovered = is_cell_hovered and (self.hovered_chip_idx == chip.index)
            is_btn_hovered = is_chip_hovered and self.hovered_delete_btn
            is_staged = False
            if callable(self.is_item_staged):
                try:
                    is_staged = bool(self.is_item_staged(index.row(), chip.text_str))
                except Exception:
                    is_staged = False

            # Pill background & border
            if is_staged:
                cur_bg = QColor(245, 158, 11, 40) if not is_chip_hovered else QColor(245, 158, 11, 75)
                cur_border = QColor(245, 158, 11, 210)
                pen = QPen(cur_border, 1.2, Qt.PenStyle.DashLine)
                cur_text = QColor("#fef3c7")
            else:
                cur_bg = self.bg_hover if is_chip_hovered else self.bg_color
                cur_border = self.border_hover if is_chip_hovered else self.border_color
                pen = QPen(cur_border, 1.0)
                cur_text = self.text_color

            painter.setBrush(QBrush(cur_bg))
            painter.setPen(pen)
            painter.drawRoundedRect(chip.rect, 4.0, 4.0)

            # Text
            painter.setPen(cur_text)
            painter.drawText(chip.rect, Qt.AlignmentFlag.AlignCenter, chip.display_str)

            # Vector delete button on hover
            if is_chip_hovered and self.removable:
                btn_rect = chip.btn_rect

                painter.save()
                if is_btn_hovered:
                    painter.setBrush(QBrush(QColor("#ef4444")))
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.drawEllipse(btn_rect)
                    pen = QPen(QColor("#ffffff"), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
                else:
                    painter.setBrush(QBrush(QColor(22, 24, 32, 240)))
                    del_border = QColor(245, 158, 11, 210) if is_staged else self.border_color
                    del_pen_color = QColor("#fef3c7") if is_staged else self.text_color
                    painter.setPen(QPen(del_border, 1.0))
                    painter.drawEllipse(btn_rect.adjusted(0.5, 0.5, -0.5, -0.5))
                    pen = QPen(del_pen_color, 1.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                pad = 4.0
                painter.drawLine(
                    QPointF(btn_rect.left() + pad, btn_rect.top() + pad),
                    QPointF(btn_rect.right() - pad, btn_rect.bottom() - pad),
                )
                painter.drawLine(
                    QPointF(btn_rect.left() + pad, btn_rect.bottom() - pad),
                    QPointF(btn_rect.right() - pad, btn_rect.top() + pad),
                )
                painter.restore()

        if layout.rem_rect is not None and layout.rem_count > 0:
            painter.setBrush(QBrush(QColor(40, 40, 50, 180)))
            painter.setPen(QPen(QColor(80, 80, 95), 1))
            painter.drawRoundedRect(layout.rem_rect, 4, 4)
            painter.setPen(QColor(COLOR_TEXT_SECONDARY))
            painter.drawText(layout.rem_rect, Qt.AlignmentFlag.AlignCenter, layout.rem_str)

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
            total_w += min(fm.horizontalAdvance(str(t)) + 14, 140) + 8
        return QSize(min(total_w, 400), 36)

    def delete_button_at_point(
        self, rect: QRectF, point: QPoint, items: List[Any]
    ) -> Optional[str]:
        """Detect which badge chip's delete button is located under the given point."""
        if not items or not self.removable:
            return None
        layout = self._layout_chips(rect, items)
        for chip in layout.chips:
            if chip.btn_rect.contains(point):
                return chip.text_str
        return None

    def item_at_point(
        self, rect: QRectF, point: QPoint, items: List[Any]
    ) -> Optional[str]:
        """Detect which badge chip text is located under the given point inside the cell rect."""
        if not items:
            return None
        layout = self._layout_chips(rect, items)
        for chip in layout.chips:
            if chip.rect.contains(point) or (self.removable and chip.btn_rect.contains(point)):
                return chip.text_str
        return None

    def get_chips_end_x(self, rect: QRectF, items: List[Any]) -> int:
        """Return the rightmost x coordinate occupied by chips in the cell."""
        if not items:
            return int(rect.x() + 5)
        layout = self._layout_chips(rect, items)
        return layout.end_x

    def update_hover(
        self, cell: Tuple[int, int], rect: QRectF, point: QPoint, items: List[Any]
    ) -> Tuple[Optional[str], bool, bool]:
        """Update hover state from mouse position.

        Returns (item_name, is_chip, is_delete_btn).
        """
        if not items:
            self.set_hover(None, None, False)
            return (None, False, False)

        layout = self._layout_chips(rect, items)
        for chip in layout.chips:
            if self.removable and chip.btn_rect.contains(point):
                self.set_hover(cell, chip.index, True)
                return (chip.text_str, True, True)
            elif chip.rect.contains(point):
                self.set_hover(cell, chip.index, False)
                return (chip.text_str, True, False)

        self.set_hover(None, None, False)
        return (None, False, False)

    def set_hover(
        self,
        cell: Optional[Tuple[int, int]],
        chip_idx: Optional[int],
        is_delete_btn: bool,
    ) -> bool:
        """Assign current hover location; returns True if state changed."""
        changed = (
            self.hovered_cell != cell
            or self.hovered_chip_idx != chip_idx
            or self.hovered_delete_btn != is_delete_btn
        )
        if changed:
            self.hovered_cell = cell
            self.hovered_chip_idx = chip_idx
            self.hovered_delete_btn = is_delete_btn
        return changed

    def clear_hover(self) -> bool:
        """Reset hover state; returns True if state was active."""
        if self.hovered_cell is not None:
            self.hovered_cell = None
            self.hovered_chip_idx = None
            self.hovered_delete_btn = False
            return True
        return False


class TableChipAddEditor(QWidget):
    """Floating inline search/add editor for adding tags or playlists directly inside table cells."""

    item_added = Signal(int, str, str)  # (row, category, item_name)
    tag_create_requested = Signal(int, str)  # (row, tag_name)
    editing_closed = Signal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("tableChipAddEditor")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setVisible(False)

        self.current_row: int = -1
        self.current_col: int = -1
        self.category: str = ""  # "tags" or "playlists"
        self.all_available_items: List[str] = []
        self.existing_items: List[str] = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.input_edit = ChipSearchLineEdit(self)
        self.input_edit.setFixedHeight(24)
        self.input_edit.setStyleSheet(
            f"QLineEdit {{ "
            f"  background-color: {COLOR_BG_INPUT}; "
            f"  color: {COLOR_TEXT_PRIMARY}; "
            f"  border: 1px solid {COLOR_BORDER_FOCUS}; "
            f"  border-radius: 5px; "
            f"  padding: 1px 7px; "
            f"  font-size: 11px; "
            f"}}"
        )
        layout.addWidget(self.input_edit)

        # Autocompleter
        self.completer_model = QStringListModel(self)
        self.completer = QCompleter(self.completer_model, self.input_edit)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)

        popup = self.completer.popup()
        if popup:
            popup.setMinimumWidth(220)
            popup.setStyleSheet(
                f"QAbstractItemView {{ "
                f"  background-color: {COLOR_BG_PANEL}; "
                f"  color: {COLOR_TEXT_PRIMARY}; "
                f"  border: 1px solid {COLOR_BORDER}; "
                f"  border-radius: 6px; "
                f"  padding: 4px; "
                f"  selection-background-color: {COLOR_BLUE_BG}; "
                f"  selection-color: {COLOR_BLUE}; "
                f"  font-size: 11px; "
                f"  outline: none; "
                f"}} "
                f"QAbstractItemView::item {{ "
                f"  padding: 5px 8px; "
                f"  border-radius: 4px; "
                f"}} "
                f"QAbstractItemView::item:hover {{ "
                f"  background-color: {COLOR_BG_HOVER}; "
                f"}} "
                f"QAbstractItemView::item:selected {{ "
                f"  background-color: {COLOR_BLUE_BG}; "
                f"  color: #38bdf8; "
                f"}}"
            )

        self.completer.activated.connect(self._on_item_chosen)
        self.input_edit.setCompleter(self.completer)
        self.input_edit.textChanged.connect(self._on_text_changed)
        self.input_edit.returnPressed.connect(self._on_return_pressed)
        self.input_edit.escape_pressed.connect(self.close_editor)

    def _on_text_changed(self, text: str) -> None:
        if self.category != "tags":
            return
        query = text.strip()
        assigned_lower = {e.lower() for e in self.existing_items}
        base_candidates = [
            item for item in self.all_available_items if item.lower() not in assigned_lower
        ]
        if query:
            exact = any(item.lower() == query.lower() for item in self.all_available_items)
            create_option = f"＋ Create \"{query}\"..."
            if not exact:
                candidates = [create_option] + base_candidates
            else:
                candidates = base_candidates
        else:
            candidates = base_candidates
        self.completer_model.setStringList(candidates)
        popup = self.completer.popup()
        if popup:
            fm = popup.fontMetrics()
            max_w = max((fm.horizontalAdvance(item) for item in candidates), default=0) + 40
            popup.setMinimumWidth(max(220, min(max_w, 400)))

    def show_at(
        self,
        rect: QRect,
        category: str,
        row: int,
        col: int,
        existing_items: List[str],
        available_items: List[str],
    ) -> None:
        self.current_row = row
        self.current_col = col
        self.category = category
        self.existing_items = list(existing_items)
        self.all_available_items = list(available_items)

        assigned_lower = {e.lower() for e in existing_items}
        candidates = [item for item in available_items if item.lower() not in assigned_lower]
        self.completer_model.setStringList(candidates)

        popup = self.completer.popup()
        if popup:
            fm = popup.fontMetrics()
            max_w = max((fm.horizontalAdvance(item) for item in candidates), default=0) + 40
            popup.setMinimumWidth(max(220, min(max_w, 400)))

        border_color = "#a78bfa" if category == "tags" else "#2dd4bf"
        self.input_edit.setStyleSheet(
            f"QLineEdit {{ "
            f"  background-color: {COLOR_BG_INPUT}; "
            f"  color: {COLOR_TEXT_PRIMARY}; "
            f"  border: 1px solid {border_color}; "
            f"  border-radius: 5px; "
            f"  padding: 1px 7px; "
            f"  font-size: 11px; "
            f"}}"
        )

        placeholder = "+ Add tag..." if category == "tags" else "+ Add playlist..."
        self.input_edit.setPlaceholderText(placeholder)
        self.input_edit.clear()

        self.setGeometry(rect)
        self.setVisible(True)
        self.raise_()
        self.input_edit.setFocus()

        # Show autocomplete options popup
        if candidates:
            self.completer.setCompletionPrefix("")
            self.completer.complete()

    def _on_item_chosen(self, text: str) -> None:
        item = text.strip()
        if not item:
            return
        row = self.current_row
        cat = self.category
        self.close_editor()
        if cat == "tags" and (item.startswith("＋ Create \"") or item.startswith("+ Create \"")):
            new_tag = item.split("\"", 2)[1] if "\"" in item else item.replace("＋ Create ", "").replace("+ Create ", "").rstrip(".").strip()
            self.tag_create_requested.emit(row, new_tag)
        else:
            self.item_added.emit(row, cat, item)

    def _on_return_pressed(self) -> None:
        text = self.input_edit.text().strip()
        if not text:
            self.close_editor()
            return

        row = self.current_row
        cat = self.category

        if cat == "tags" and (text.startswith("＋ Create \"") or text.startswith("+ Create \"")):
            new_tag = text.split("\"", 2)[1] if "\"" in text else text.replace("＋ Create ", "").replace("+ Create ", "").rstrip(".").strip()
            self.close_editor()
            self.tag_create_requested.emit(row, new_tag)
            return

        matched = None
        for cand in self.all_available_items:
            if cand.lower() == text.lower():
                matched = cand
                break

        if not matched and self.completer.currentCompletion():
            cur = self.completer.currentCompletion()
            if cur.startswith("＋ Create \"") or cur.startswith("+ Create \""):
                new_tag = cur.split("\"", 2)[1] if "\"" in cur else text
                self.close_editor()
                self.tag_create_requested.emit(row, new_tag)
                return
            elif cur.lower().startswith(text.lower()) and cur in self.all_available_items:
                matched = cur

        if matched:
            self._on_item_chosen(matched)
        elif cat == "tags":
            self.close_editor()
            self.tag_create_requested.emit(row, text)
        else:
            self.close_editor()

    def close_editor(self) -> None:
        if self.isVisible():
            self.setVisible(False)
            self.editing_closed.emit()


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


class _TableItemProxy:
    """Lightweight compatibility proxy simulating QTableWidgetItem for legacy callers/tests."""

    def __init__(
        self,
        row: int,
        col: int,
        text_val: str,
        user_data: Any = None,
        font_val: Optional[QFont] = None,
        fg_val: Optional[QColor] = None,
        align_val: Optional[Qt.AlignmentFlag] = None,
        tip_val: str = "",
    ):
        self._row = row
        self._col = col
        self._text = text_val
        self._user_data = user_data
        self._font = font_val or QFont()
        self._fg = fg_val
        self._align = align_val
        self._tip = tip_val

    def text(self) -> str:
        return self._text

    def data(self, role: int = Qt.ItemDataRole.UserRole) -> Any:
        if role == Qt.ItemDataRole.DisplayRole:
            return self._text
        elif role == Qt.ItemDataRole.ToolTipRole:
            return self._tip
        elif role == Qt.ItemDataRole.ForegroundRole:
            return self._fg
        elif role == Qt.ItemDataRole.FontRole:
            return self._font
        return self._user_data

    def setData(self, role: int, value: Any) -> None:
        if role == Qt.ItemDataRole.DisplayRole:
            self._text = str(value)
        elif role == Qt.ItemDataRole.ToolTipRole:
            self._tip = str(value)
        elif role == Qt.ItemDataRole.UserRole:
            self._user_data = value

    def row(self) -> int:
        return self._row

    def column(self) -> int:
        return self._col

    def font(self) -> QFont:
        return self._font

    def setFont(self, f: QFont) -> None:
        self._font = f

    def foreground(self) -> Optional[QBrush]:
        return QBrush(self._fg) if self._fg else None

    def setForeground(self, c: Any) -> None:
        self._fg = c if isinstance(c, QColor) else QColor(c)

    def toolTip(self) -> str:
        return self._tip

    def setToolTip(self, tip: str) -> None:
        self._tip = tip

    def textAlignment(self) -> Optional[Qt.AlignmentFlag]:
        return self._align

    def setTextAlignment(self, align: Any) -> None:
        self._align = align

    def flags(self) -> Qt.ItemFlags:
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def setFlags(self, flags: Any) -> None:
        pass


class TracksTableModel(QAbstractTableModel):
    """Virtualized, high-performance table model for track collections."""

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def __init__(
        self,
        records: Optional[List[Dict[str, Any]]] = None,
        config: Optional[TrackTableConfig] = None,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.records: List[Dict[str, Any]] = records if records is not None else []
        self.config: TrackTableConfig = config or TrackTableConfig()

        self._title_font = QFont()
        self._title_font.setBold(True)

        self._is_row_staged_fn: Optional[Any] = None

        self._color_white = QColor("#ffffff")
        self._color_secondary = QColor(COLOR_TEXT_SECONDARY)
        self._color_muted = QColor(COLOR_TEXT_MUTED)
        self._color_blue = QColor(COLOR_BLUE)
        self._color_green = QColor(COLOR_GREEN)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self.records)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self.config.columns)

    def set_config(self, config: TrackTableConfig) -> None:
        self.beginResetModel()
        self.config = config
        self.endResetModel()

    def set_records(self, records: List[Dict[str, Any]]) -> None:
        self.beginResetModel()
        self.records = records
        self.endResetModel()

    def update_track_playlists(self, row_idx: int, playlists: List[str]) -> None:
        if 0 <= row_idx < len(self.records):
            self.records[row_idx]["playlists"] = list(dict.fromkeys(playlists))
            if TrackColumn.PLAYLISTS in self.config.columns:
                col_idx = self.config.columns.index(TrackColumn.PLAYLISTS)
                idx = self.index(row_idx, col_idx)
                self.dataChanged.emit(
                    idx,
                    idx,
                    [
                        Qt.ItemDataRole.DisplayRole,
                        Qt.ItemDataRole.UserRole,
                        Qt.ItemDataRole.ToolTipRole,
                    ],
                )

    def update_track_tags(self, row_idx: int, tags: List[str]) -> None:
        if 0 <= row_idx < len(self.records):
            self.records[row_idx]["tags"] = list(dict.fromkeys(tags))
            if TrackColumn.TAGS in self.config.columns:
                col_idx = self.config.columns.index(TrackColumn.TAGS)
                idx = self.index(row_idx, col_idx)
                self.dataChanged.emit(
                    idx,
                    idx,
                    [
                        Qt.ItemDataRole.DisplayRole,
                        Qt.ItemDataRole.UserRole,
                        Qt.ItemDataRole.ToolTipRole,
                    ],
                )

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.config.columns):
            col_name = self.config.columns[section]
            if role == Qt.ItemDataRole.DisplayRole:
                return col_name
            elif role == Qt.ItemDataRole.TextAlignmentRole:
                if col_name in (
                    TrackColumn.INDEX,
                    TrackColumn.KEY,
                    TrackColumn.BPM,
                    TrackColumn.TIME,
                    TrackColumn.PLAYS,
                    TrackColumn.BITRATE,
                    TrackColumn.FILE_TYPE,
                ):
                    return Qt.AlignmentFlag.AlignCenter
                return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            elif role == Qt.ItemDataRole.ToolTipRole:
                return self._get_header_tooltip(col_name)
        return None

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

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None

        row = index.row()
        col = index.column()

        if not (0 <= row < len(self.records) and 0 <= col < len(self.config.columns)):
            return None

        if role == Qt.ItemDataRole.BackgroundRole:
            if callable(self._is_row_staged_fn) and self._is_row_staged_fn(row):
                return QBrush(QColor(245, 158, 11, 20))
            return None

        t = self.records[row]
        col_name = self.config.columns[col]

        # 1. INDEX (#)
        if col_name == TrackColumn.INDEX:
            if role == Qt.ItemDataRole.DisplayRole:
                if self.config.index_mode == "track_no":
                    return str(t.get("track_no") or (row + 1))
                return str(row + 1)
            elif role == Qt.ItemDataRole.UserRole:
                if self.config.index_mode == "track_no":
                    return float(t.get("track_no") or (row + 1))
                return float(row + 1)
            elif role == Qt.ItemDataRole.TextAlignmentRole:
                return Qt.AlignmentFlag.AlignCenter
            elif role == Qt.ItemDataRole.ForegroundRole:
                return self._color_muted
            elif role == Qt.ItemDataRole.SizeHintRole:
                return QSize(58, 36)

        # 2. TITLE
        elif col_name == TrackColumn.TITLE:
            title_text = t.get("title") or "(Untitled)"
            if role == Qt.ItemDataRole.DisplayRole:
                return title_text
            elif role == Qt.ItemDataRole.ToolTipRole:
                return title_text
            elif role == Qt.ItemDataRole.FontRole:
                return self._title_font
            elif role == Qt.ItemDataRole.ForegroundRole:
                return self._color_white
            elif role == Qt.ItemDataRole.UserRole:
                return title_text

        # 3. ARTIST
        elif col_name == TrackColumn.ARTIST:
            artist_text = t.get("artist") or "Unknown Artist"
            if role == Qt.ItemDataRole.DisplayRole:
                return artist_text
            elif role == Qt.ItemDataRole.ToolTipRole:
                return artist_text
            elif role == Qt.ItemDataRole.ForegroundRole:
                return self._color_secondary
            elif role == Qt.ItemDataRole.UserRole:
                return artist_text

        # 4. ALBUM
        elif col_name == TrackColumn.ALBUM:
            album_text = t.get("album") or "—"
            if role == Qt.ItemDataRole.DisplayRole:
                return album_text
            elif role == Qt.ItemDataRole.ToolTipRole:
                return t.get("album") or ""
            elif role == Qt.ItemDataRole.ForegroundRole:
                return self._color_muted
            elif role == Qt.ItemDataRole.UserRole:
                return album_text

        # 5. KEY
        elif col_name == TrackColumn.KEY:
            key_text = t.get("key") or "—"
            if role == Qt.ItemDataRole.DisplayRole:
                return key_text
            elif role == Qt.ItemDataRole.TextAlignmentRole:
                return Qt.AlignmentFlag.AlignCenter
            elif role == Qt.ItemDataRole.ForegroundRole:
                return self._color_blue if t.get("key") else self._color_muted
            elif role == Qt.ItemDataRole.UserRole:
                return key_text

        # 6. BPM
        elif col_name == TrackColumn.BPM:
            bpm_val = float(t.get("bpm") or 0.0)
            if role == Qt.ItemDataRole.DisplayRole:
                return f"{bpm_val:.1f}" if bpm_val > 0 else "—"
            elif role == Qt.ItemDataRole.UserRole:
                return bpm_val
            elif role == Qt.ItemDataRole.TextAlignmentRole:
                return Qt.AlignmentFlag.AlignCenter

        # 7. TIME
        elif col_name == TrackColumn.TIME:
            dur_sec = float(t.get("duration_sec") or 0)
            if role == Qt.ItemDataRole.DisplayRole:
                return t.get("duration_str") or "--:--"
            elif role == Qt.ItemDataRole.UserRole:
                return dur_sec
            elif role == Qt.ItemDataRole.TextAlignmentRole:
                return Qt.AlignmentFlag.AlignCenter

        # 8. PLAYS
        elif col_name == TrackColumn.PLAYS:
            plays_val = int(t.get("play_count") or 0)
            if role == Qt.ItemDataRole.DisplayRole:
                return str(plays_val)
            elif role == Qt.ItemDataRole.UserRole:
                return plays_val
            elif role == Qt.ItemDataRole.TextAlignmentRole:
                return Qt.AlignmentFlag.AlignCenter
            elif role == Qt.ItemDataRole.ForegroundRole:
                if plays_val > 0:
                    return self._color_green

        # 9. BITRATE
        elif col_name == TrackColumn.BITRATE:
            br_val = int(t.get("bitrate") or 0)
            if role == Qt.ItemDataRole.DisplayRole:
                return f"{br_val} kbps" if br_val > 0 else "—"
            elif role == Qt.ItemDataRole.UserRole:
                return float(br_val)
            elif role == Qt.ItemDataRole.TextAlignmentRole:
                return Qt.AlignmentFlag.AlignCenter
            elif role == Qt.ItemDataRole.ForegroundRole:
                return self._color_muted

        # 10. FILE_TYPE
        elif col_name == TrackColumn.FILE_TYPE:
            file_type = t.get("file_type") or "—"
            if role == Qt.ItemDataRole.DisplayRole:
                return file_type
            elif role == Qt.ItemDataRole.ToolTipRole:
                return f"Format: {file_type}"
            elif role == Qt.ItemDataRole.UserRole:
                return file_type
            elif role == Qt.ItemDataRole.TextAlignmentRole:
                return Qt.AlignmentFlag.AlignCenter

        # 11. TAGS
        elif col_name == TrackColumn.TAGS:
            tags = list(dict.fromkeys(t.get("tags") or []))
            if role == Qt.ItemDataRole.DisplayRole:
                return ", ".join(tags) if tags else "—"
            elif role == Qt.ItemDataRole.UserRole:
                return tags
            elif role == Qt.ItemDataRole.ToolTipRole:
                base = f"Tags: {', '.join(tags)}" if tags else "No tags"
                if callable(self._is_row_staged_fn) and self._is_row_staged_fn(row):
                    return f"{base} (⚠️ Unsaved modifications)"
                return base

        # 12. PLAYLISTS
        elif col_name == TrackColumn.PLAYLISTS:
            playlists = list(dict.fromkeys(t.get("playlists") or []))
            if role == Qt.ItemDataRole.DisplayRole:
                return ", ".join(playlists) if playlists else "—"
            elif role == Qt.ItemDataRole.UserRole:
                return playlists
            elif role == Qt.ItemDataRole.ToolTipRole:
                base = (
                    f"Playlists: {', '.join(playlists)}"
                    if playlists
                    else "Not in any playlist"
                )
                if callable(self._is_row_staged_fn) and self._is_row_staged_fn(row):
                    return f"{base} (⚠️ Unsaved modifications)"
                return base

        # 13. FOLDER
        elif col_name == TrackColumn.FOLDER:
            folder_name = t.get("folder_name") or t.get("folder") or "—"
            folder_dir = t.get("folder_dir") or t.get("folder_path") or ""
            if role == Qt.ItemDataRole.DisplayRole:
                return folder_name
            elif role == Qt.ItemDataRole.UserRole:
                return folder_dir
            elif role == Qt.ItemDataRole.ToolTipRole:
                return (
                    f"Folder: {folder_dir}\n💡 Ctrl+Click or Cmd+Click to open folder in Finder"
                    if folder_dir
                    else "Folder: (None)"
                )
            elif role == Qt.ItemDataRole.ForegroundRole:
                return self._color_muted

        # 14. FILE_NAME
        elif col_name == TrackColumn.FILE_NAME:
            file_name = t.get("file_name") or t.get("filename") or "—"
            file_path = t.get("file_path") or t.get("folder_path") or ""
            if role == Qt.ItemDataRole.DisplayRole:
                return file_name
            elif role == Qt.ItemDataRole.UserRole:
                return file_path
            elif role == Qt.ItemDataRole.ToolTipRole:
                return (
                    f"File: {file_path}\n💡 Ctrl+Click or Cmd+Click to reveal with highlight in Finder"
                    if file_path
                    else "File: (None)"
                )
            elif role == Qt.ItemDataRole.ForegroundRole:
                return self._color_muted

        return None

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        if not (0 <= column < len(self.config.columns)):
            return

        col_name = self.config.columns[column]
        is_reverse = (order == Qt.SortOrder.DescendingOrder)

        def get_sort_key(t: Dict[str, Any]) -> Any:
            if col_name == TrackColumn.INDEX:
                return int(t.get("track_no") or 0)
            elif col_name == TrackColumn.TITLE:
                return (t.get("title") or "").lower()
            elif col_name == TrackColumn.ARTIST:
                return (t.get("artist") or "").lower()
            elif col_name == TrackColumn.ALBUM:
                return (t.get("album") or "").lower()
            elif col_name == TrackColumn.KEY:
                return (t.get("key") or "").lower()
            elif col_name == TrackColumn.BPM:
                return float(t.get("bpm") or 0.0)
            elif col_name == TrackColumn.TIME:
                return float(t.get("duration_sec") or 0)
            elif col_name == TrackColumn.PLAYS:
                return int(t.get("play_count") or 0)
            elif col_name == TrackColumn.BITRATE:
                return int(t.get("bitrate") or 0)
            elif col_name == TrackColumn.FILE_TYPE:
                return (t.get("file_type") or "").lower()
            elif col_name == TrackColumn.TAGS:
                tags = t.get("tags") or []
                return ", ".join(tags).lower()
            elif col_name == TrackColumn.PLAYLISTS:
                playlists = t.get("playlists") or []
                return ", ".join(playlists).lower()
            elif col_name == TrackColumn.FOLDER:
                return (t.get("folder_name") or "").lower()
            elif col_name == TrackColumn.FILE_NAME:
                return (t.get("file_name") or t.get("filename") or "").lower()
            return ""

        self.layoutAboutToBeChanged.emit()
        self.records.sort(key=get_sort_key, reverse=is_reverse)
        self.layoutChanged.emit()


class TracksTableWidget(QTableView):
    """Customizable tracklist table view supporting rich badges, Cmd-clicks, and context menu."""

    folder_clicked = Signal(str)
    file_clicked = Signal(str)
    sort_requested = Signal(str, Qt.SortOrder)
    status_message = Signal(str)
    track_detail_requested = Signal(dict)
    replace_file_requested = Signal(dict)
    open_playlist_requested = Signal(str)
    open_tag_requested = Signal(str)
    track_playlists_changed = Signal(int, list)
    track_tags_changed = Signal(int, list)
    track_modified = Signal(dict)
    db_saved = Signal()
    tag_created = Signal(str)  # (tag_name)
    staged_changes_changed = Signal(bool)

    def __init__(
        self,
        config: Optional[TrackTableConfig] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.config: TrackTableConfig = config or TrackTableConfig()
        self.records: List[Dict[str, Any]] = []
        self.staged_edits: Dict[str, Dict[str, Any]] = {}
        self.hovered_link_cell: Optional[Tuple[int, int]] = None
        self.current_sort_col: int = 1
        self.current_sort_order: Qt.SortOrder = Qt.SortOrder.AscendingOrder

        self.engine: Optional[Engine] = None
        self.cached_available_playlists: List[str] = []
        self.cached_available_tags: List[str] = []

        self._folder_col_idx: int = -1
        self._file_col_idx: int = -1
        self._playlist_col_idx: int = -1
        self._tags_col_idx: int = -1

        self._tags_delegate: Optional[ChipBadgeDelegate] = None
        self._playlist_delegate: Optional[ChipBadgeDelegate] = None

        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)

        app = QApplication.instance()
        if app:
            self._key_filter = ModifierKeyFilter(self._on_modifier_changed, self)
            app.installEventFilter(self._key_filter)

        self._model = TracksTableModel(records=self.records, config=self.config, parent=self)
        self.setModel(self._model)

        self.doubleClicked.connect(self._on_cell_double_clicked)
        self.customContextMenuRequested.connect(self._on_table_context_menu)
        self.horizontalHeader().sectionClicked.connect(self._on_header_section_clicked)

        self._chip_editor = TableChipAddEditor(self.viewport())
        self._chip_editor.item_added.connect(self._on_chip_item_added)
        self._chip_editor.tag_create_requested.connect(self._on_tag_create_requested)
        self.verticalScrollBar().valueChanged.connect(self._chip_editor.close_editor)
        self.horizontalScrollBar().valueChanged.connect(self._chip_editor.close_editor)

        # Bottom Save/Discard Bar (Hidden by default; appears when staged edits exist)
        self.bottom_bar = QFrame()
        self.bottom_bar.setObjectName("bottomActionBar")
        self.bottom_bar.setStyleSheet(
            f"QFrame#bottomActionBar {{ "
            f"  background-color: {COLOR_BG_PANEL}; "
            f"  border-top: 1px solid {COLOR_BORDER}; "
            f"  border-radius: 6px; "
            f"}}"
        )
        bottom_layout = QHBoxLayout(self.bottom_bar)
        bottom_layout.setContentsMargins(16, 10, 16, 10)
        bottom_layout.setSpacing(12)

        self.staged_status_label = QLabel("⚠️ You have unsaved playlist or tag modifications")
        self.staged_status_label.setStyleSheet(f"color: {COLOR_AMBER}; font-size: 12px; font-weight: 600;")
        bottom_layout.addWidget(self.staged_status_label)
        bottom_layout.addStretch(1)

        self.discard_btn = QPushButton("Discard Changes")
        self.discard_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.discard_btn.clicked.connect(self.discard_changes)
        bottom_layout.addWidget(self.discard_btn)

        self.save_btn = QPushButton("💾 Save Changes")
        self.save_btn.setObjectName("primaryButton")
        self.save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_btn.clicked.connect(self.save_changes)
        bottom_layout.addWidget(self.save_btn)

        self.bottom_bar.setVisible(False)

        self._init_table_structure()

    def set_engine(self, engine: Any) -> None:
        """Assign active database engine and preload available tags and playlists."""
        self.engine = engine
        self._refresh_available_caches()

    def _refresh_available_caches(self) -> None:
        """Fetch and cache user playlists and tags from Rekordbox database."""
        if not self.engine:
            return
        try:
            pls = fetch_all_playlists(self.engine)
            self.cached_available_playlists = [p["name"] for p in pls if p.get("name")]
        except Exception:
            pass
        try:
            tags = fetch_all_tags(self.engine)
            self.cached_available_tags = [t["name"] for t in tags if t.get("name")]
        except Exception:
            pass

    def get_available_items(self, category: str) -> List[str]:
        """Return available playlists or tags, refreshing from database if engine is present."""
        if category == "playlists":
            if self.engine:
                try:
                    pls = fetch_all_playlists(self.engine)
                    self.cached_available_playlists = [p["name"] for p in pls if p.get("name")]
                except Exception:
                    pass
            return list(self.cached_available_playlists)
        elif category == "tags":
            if self.engine:
                try:
                    tags = fetch_all_tags(self.engine)
                    self.cached_available_tags = [t["name"] for t in tags if t.get("name")]
                except Exception:
                    pass
            return list(self.cached_available_tags)
        return []

    def set_available_playlists(self, playlists: List[str]) -> None:
        """Explicitly assign available playlists list."""
        self.cached_available_playlists = list(playlists)

    def set_available_tags(self, tags: List[str]) -> None:
        """Explicitly assign available tags list."""
        self.cached_available_tags = list(tags)

    def configure(self, config: TrackTableConfig) -> None:
        """Reconfigure table columns and behavior."""
        self.config = config
        self._model.set_config(config)
        self._init_table_structure()
        if self.records:
            self.set_tracks(self.records)

    def _init_table_structure(self) -> None:
        """Set up columns, resize modes, alignments, tooltips, and delegates."""
        cols = self.config.columns

        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.verticalHeader().setDefaultSectionSize(36)
        self.verticalHeader().setVisible(False)

        header = self.horizontalHeader()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)

        if self.config.sorting_mode == "client":
            self.setSortingEnabled(True)
        else:
            self.setSortingEnabled(False)

        if self.config.enable_context_menu:
            self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        else:
            self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)

        # Delegate singletons
        file_type_delegate = FileTypeBadgeDelegate(parent=self)
        self._tags_delegate = ChipBadgeDelegate(
            bg_color=QColor(139, 92, 246, 38),
            border_color=QColor(167, 139, 250, 95),
            text_color=QColor("#d8b4fe"),
            bg_hover=QColor(139, 92, 246, 68),
            border_hover=QColor(167, 139, 250, 180),
            removable=True,
            is_item_staged=self._is_tag_item_staged,
            parent=self,
        )
        self._playlist_delegate = ChipBadgeDelegate(
            bg_color=QColor(20, 184, 166, 38),
            border_color=QColor(45, 212, 191, 95),
            text_color=QColor("#5eead4"),
            bg_hover=QColor(20, 184, 166, 68),
            border_hover=QColor(45, 212, 191, 180),
            removable=True,
            is_item_staged=self._is_playlist_item_staged,
            parent=self,
        )
        self._model._is_row_staged_fn = self._is_row_staged
        path_link_delegate = PathLinkDelegate(parent=self)

        self._folder_col_idx = -1
        self._file_col_idx = -1
        self._playlist_col_idx = -1
        self._tags_col_idx = -1

        for col_idx, col_name in enumerate(cols):
            # Resize modes and widths
            if col_name == TrackColumn.INDEX:
                header.setSectionResizeMode(col_idx, QHeaderView.ResizeMode.Interactive)
                self.setColumnWidth(col_idx, 58)
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
                self.setColumnWidth(col_idx, 160)
            elif col_name == TrackColumn.FILE_NAME:
                header.setSectionResizeMode(col_idx, QHeaderView.ResizeMode.Interactive)
                self.setColumnWidth(col_idx, 280)

            # Assign delegates
            if col_name == TrackColumn.FILE_TYPE:
                self.setItemDelegateForColumn(col_idx, file_type_delegate)
            elif col_name == TrackColumn.TAGS:
                self.setItemDelegateForColumn(col_idx, self._tags_delegate)
                self._tags_col_idx = col_idx
            elif col_name == TrackColumn.PLAYLISTS:
                self.setItemDelegateForColumn(col_idx, self._playlist_delegate)
                self._playlist_col_idx = col_idx
            elif col_name == TrackColumn.FOLDER:
                self.setItemDelegateForColumn(col_idx, path_link_delegate)
                self._folder_col_idx = col_idx
            elif col_name == TrackColumn.FILE_NAME:
                self.setItemDelegateForColumn(col_idx, path_link_delegate)
                self._file_col_idx = col_idx

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
                idx = self.indexAt(mouse_pos)
                if idx.isValid() and idx.column() in (self._folder_col_idx, self._file_col_idx):
                    path_data = idx.data(Qt.ItemDataRole.UserRole)
                    if path_data and str(path_data) not in ("", "—"):
                        new_cell = (idx.row(), idx.column())

        if new_cell != self.hovered_link_cell:
            self.hovered_link_cell = new_cell
            if new_cell is not None:
                self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self.viewport().unsetCursor()
            self.viewport().update()

    def _update_chip_hover_state(self, mouse_pos: QPoint) -> None:
        """Update hover state on badge chips when mouse moves over tags/playlists columns."""
        if self.hovered_link_cell is not None:
            return

        if not self.viewport().rect().contains(mouse_pos):
            self._clear_chip_hover()
            return

        idx = self.indexAt(mouse_pos)
        if not idx.isValid() or idx.column() not in (self._tags_col_idx, self._playlist_col_idx):
            self._clear_chip_hover()
            return

        col = idx.column()
        delegate = self._playlist_delegate if col == self._playlist_col_idx else self._tags_delegate
        other_delegate = self._tags_delegate if col == self._playlist_col_idx else self._playlist_delegate

        if other_delegate:
            if other_delegate.clear_hover():
                self.viewport().update()

        if not delegate:
            return

        cell = (idx.row(), col)
        cell_rect = self.visualRect(idx)
        items = idx.data(Qt.ItemDataRole.UserRole) or []
        if isinstance(items, str):
            items = [items]

        item_name, is_chip, is_delete_btn = delegate.update_hover(
            cell, QRectF(cell_rect), mouse_pos, items
        )
        self.viewport().update(cell_rect)

        if is_delete_btn:
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
            self.viewport().setToolTip(f"Remove '{item_name}'")
        elif is_chip:
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
            cat_name = "playlist" if col == self._playlist_col_idx else "tag"
            self.viewport().setToolTip(f"Open {cat_name} '{item_name}' (click to view)")
        else:
            self.viewport().unsetCursor()
            cat_name = "to playlist" if col == self._playlist_col_idx else "tags"
            self.viewport().setToolTip(f"💡 Double-click empty space to add {cat_name}")

    def _clear_chip_hover(self) -> None:
        """Clear chip hover state across all chip delegates."""
        cleared = False
        if self._playlist_delegate and self._playlist_delegate.clear_hover():
            cleared = True
        if self._tags_delegate and self._tags_delegate.clear_hover():
            cleared = True
        if cleared:
            if self.hovered_link_cell is None:
                self.viewport().unsetCursor()
            self.viewport().update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position().toPoint()
        self._update_hover_state(pos)
        self._update_chip_hover_state(pos)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        if self.hovered_link_cell is not None:
            self.hovered_link_cell = None
            self.viewport().unsetCursor()
            self.viewport().update()
        self._clear_chip_hover()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._chip_editor and self._chip_editor.isVisible():
            pos = event.position().toPoint()
            if not self._chip_editor.geometry().contains(pos):
                self._chip_editor.close_editor()

        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            idx = self.indexAt(pos)
            if idx.isValid() and idx.column() in (self._playlist_col_idx, self._tags_col_idx):
                items = idx.data(Qt.ItemDataRole.UserRole)
                if items:
                    if isinstance(items, str):
                        items = [items]
                    delegate = (
                        self._playlist_delegate
                        if idx.column() == self._playlist_col_idx
                        else self._tags_delegate
                    )
                    if isinstance(delegate, ChipBadgeDelegate):
                        cell_rect = self.visualRect(idx)
                        del_item = delegate.delete_button_at_point(
                            QRectF(cell_rect), pos, items
                        )
                        if del_item:
                            category = "playlists" if idx.column() == self._playlist_col_idx else "tags"
                            self._on_chip_item_deleted(idx.row(), category, del_item)
                            event.accept()
                            return

                        clicked_item = delegate.item_at_point(
                            QRectF(cell_rect), pos, items
                        )
                        if clicked_item:
                            if self.has_staged_changes():
                                if not self.confirm_unsaved_changes():
                                    event.accept()
                                    return
                            if idx.column() == self._playlist_col_idx:
                                self.open_playlist_requested.emit(clicked_item)
                            else:
                                self.open_tag_requested.emit(clicked_item)
                            event.accept()
                            return

        if event.button() == Qt.MouseButton.LeftButton and self.config.enable_finder_clicks:
            mod = event.modifiers()
            if mod & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
                idx = self.indexAt(event.position().toPoint())
                if idx.isValid():
                    col = idx.column()
                    if col == self._folder_col_idx:
                        path_data = idx.data(Qt.ItemDataRole.UserRole)
                        if path_data and str(path_data) not in ("", "—"):
                            self.open_folder(str(path_data))
                            self.folder_clicked.emit(str(path_data))
                            event.accept()
                            return
                    elif col == self._file_col_idx:
                        path_data = idx.data(Qt.ItemDataRole.UserRole)
                        if path_data and str(path_data) not in ("", "—"):
                            self.reveal_file(str(path_data))
                            self.file_clicked.emit(str(path_data))
                            event.accept()
                            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            idx = self.indexAt(pos)
            if idx.isValid() and idx.column() in (self._playlist_col_idx, self._tags_col_idx):
                items = idx.data(Qt.ItemDataRole.UserRole) or []
                if isinstance(items, str):
                    items = [items]
                delegate = (
                    self._playlist_delegate
                    if idx.column() == self._playlist_col_idx
                    else self._tags_delegate
                )
                if isinstance(delegate, ChipBadgeDelegate):
                    cell_rect = self.visualRect(idx)
                    clicked_item = delegate.item_at_point(
                        QRectF(cell_rect), pos, items
                    )
                    if clicked_item:
                        # Double clicked on an existing chip: ignore to prevent opening track detail
                        event.accept()
                        return

                    # Double clicked on empty space of cell: open inline add editor
                    category = "playlists" if idx.column() == self._playlist_col_idx else "tags"
                    self._open_chip_add_editor(idx, category)
                    event.accept()
                    return

        super().mouseDoubleClickEvent(event)

    def _open_chip_add_editor(self, idx: QModelIndex, category: str) -> None:
        """Open floating inline add editor for tags or playlists at the specified cell."""
        row = idx.row()
        col = idx.column()
        if not (0 <= row < len(self.records)):
            return

        self.scrollTo(idx)
        track = self.records[row]
        existing_items = list(track.get(category) or [])
        available_items = self.get_available_items(category)

        cell_rect = self.visualRect(idx)
        delegate = self.itemDelegateForColumn(col)
        if isinstance(delegate, ChipBadgeDelegate):
            end_x = delegate.get_chips_end_x(QRectF(cell_rect), existing_items)
        else:
            end_x = cell_rect.x() + 5

        h = 24
        y = cell_rect.y() + (cell_rect.height() - h) // 2
        avail_w = cell_rect.right() - end_x - 6

        if avail_w >= 130:
            x = end_x + 4
            w = min(avail_w, 220)
        else:
            w = min(180, cell_rect.width() - 10)
            x = max(cell_rect.x() + 5, cell_rect.right() - w - 4)

        target_rect = QRect(int(x), int(y), int(w), int(h))
        self._chip_editor.show_at(
            target_rect,
            category=category,
            row=row,
            col=col,
            existing_items=existing_items,
            available_items=available_items,
        )

    def _find_row_by_id(self, content_id: str) -> int:
        """Find row index in self.records for a given track id."""
        for idx, r in enumerate(self.records):
            cid = str(r.get("id") or r.get("content_id") or "")
            if cid == content_id:
                return idx
        return -1

    def _is_tag_item_staged(self, row: Any, item_name: str) -> bool:
        """Return True if the specified tag on this row is a staged/pending addition."""
        row_idx = row[0] if isinstance(row, tuple) else (row.row() if hasattr(row, "row") else row)
        if isinstance(row_idx, int) and 0 <= row_idx < len(self.records):
            track = self.records[row_idx]
            content_id = str(track.get("id") or track.get("content_id") or "")
            if content_id in self.staged_edits:
                orig_tags = self.staged_edits[content_id].get("original_tags") or []
                return item_name not in orig_tags
        return False

    def _is_playlist_item_staged(self, row: Any, item_name: str) -> bool:
        """Return True if the specified playlist on this row is a staged/pending addition."""
        row_idx = row[0] if isinstance(row, tuple) else (row.row() if hasattr(row, "row") else row)
        if isinstance(row_idx, int) and 0 <= row_idx < len(self.records):
            track = self.records[row_idx]
            content_id = str(track.get("id") or track.get("content_id") or "")
            if content_id in self.staged_edits:
                orig_pls = self.staged_edits[content_id].get("original_playlists") or []
                return item_name not in orig_pls
        return False

    def _is_row_staged(self, row: Any) -> bool:
        """Return True if the track at this row has unsaved modifications."""
        row_idx = row[0] if isinstance(row, tuple) else (row.row() if hasattr(row, "row") else row)
        if isinstance(row_idx, int) and 0 <= row_idx < len(self.records):
            track = self.records[row_idx]
            content_id = str(track.get("id") or track.get("content_id") or "")
            return content_id in self.staged_edits
        return False

    def has_staged_changes(self) -> bool:
        """Return True if there are unsaved playlist or tag modifications in the table."""
        return len(self.staged_edits) > 0

    def get_staged_summary(self) -> str:
        """Produce a human-readable summary of all staged modifications."""
        num_tracks = len(self.staged_edits)
        if num_tracks == 0:
            return "No unsaved changes"

        total_added_pl = 0
        total_removed_pl = 0
        total_added_tags = 0
        total_removed_tags = 0

        for data in self.staged_edits.values():
            orig_pl = set(data["original_playlists"])
            cur_pl = set(data["current_playlists"])
            orig_tags = set(data["original_tags"])
            cur_tags = set(data["current_tags"])
            total_added_pl += len(cur_pl - orig_pl)
            total_removed_pl += len(orig_pl - cur_pl)
            total_added_tags += len(cur_tags - orig_tags)
            total_removed_tags += len(orig_tags - cur_tags)

        if num_tracks == 1:
            first_data = next(iter(self.staged_edits.values()))
            title = first_data["record"].get("title") or "Track"
            sections = []
            if total_added_pl or total_removed_pl:
                parts = []
                if total_added_pl:
                    parts.append(f"+{total_added_pl} added")
                if total_removed_pl:
                    parts.append(f"-{total_removed_pl} removed")
                sections.append(f"Playlists: {', '.join(parts)}")
            if total_added_tags or total_removed_tags:
                parts = []
                if total_added_tags:
                    parts.append(f"+{total_added_tags} added")
                if total_removed_tags:
                    parts.append(f"-{total_removed_tags} removed")
                sections.append(f"Tags: {', '.join(parts)}")
            inner = "; ".join(sections) if sections else "modified"
            return f"'{title}': {inner}"
        else:
            sections = []
            if total_added_pl or total_removed_pl:
                parts = []
                if total_added_pl:
                    parts.append(f"+{total_added_pl}")
                if total_removed_pl:
                    parts.append(f"-{total_removed_pl}")
                sections.append(f"Playlists: {', '.join(parts)}")
            if total_added_tags or total_removed_tags:
                parts = []
                if total_added_tags:
                    parts.append(f"+{total_added_tags}")
                if total_removed_tags:
                    parts.append(f"-{total_removed_tags}")
                sections.append(f"Tags: {', '.join(parts)}")
            inner = "; ".join(sections) if sections else "modified"
            return f"{num_tracks} tracks modified ({inner})"

    def _update_staged_ui(self) -> None:
        """Update bottom action bar visibility and message text based on staged edits."""
        staged = self.has_staged_changes()
        self.bottom_bar.setVisible(staged)
        if staged:
            num = len(self.staged_edits)
            summary = self.get_staged_summary()
            self.staged_status_label.setText(
                f"⚠️ Unsaved modifications ({summary}) — click Save Changes to commit."
            )
            self.save_btn.setText(f"💾 Save Changes ({num})" if num > 1 else "💾 Save Changes")
            self.discard_btn.setText(f"Discard Changes ({num})" if num > 1 else "Discard Changes")
        self.viewport().update()

    def _on_chip_item_added(self, row: int, category: str, item_name: str) -> None:
        """Handle adding a tag or playlist item from the inline editor as a staged edit."""
        if not (0 <= row < len(self.records)):
            return

        track = self.records[row]
        content_id = str(track.get("id") or track.get("content_id") or "")
        if not content_id:
            return

        title = track.get("title") or "Track"

        if content_id not in self.staged_edits:
            self.staged_edits[content_id] = {
                "record": track,
                "original_tags": list(dict.fromkeys(track.get("tags") or [])),
                "original_playlists": list(dict.fromkeys(track.get("playlists") or [])),
                "current_tags": list(dict.fromkeys(track.get("tags") or [])),
                "current_playlists": list(dict.fromkeys(track.get("playlists") or [])),
            }

        staged = self.staged_edits[content_id]
        if category == "tags":
            current_tags = staged["current_tags"]
            if item_name not in current_tags:
                current_tags.append(item_name)
            track["tags"] = list(current_tags)
            self._model.update_track_tags(row, current_tags)
            self.track_tags_changed.emit(row, current_tags)
            cat_singular = "tag"
        elif category == "playlists":
            current_pls = staged["current_playlists"]
            if item_name not in current_pls:
                current_pls.append(item_name)
            track["playlists"] = list(current_pls)
            self._model.update_track_playlists(row, current_pls)
            self.track_playlists_changed.emit(row, current_pls)
            cat_singular = "playlist"
        else:
            return

        if (
            set(staged["current_tags"]) == set(staged["original_tags"])
            and set(staged["current_playlists"]) == set(staged["original_playlists"])
        ):
            self.staged_edits.pop(content_id, None)

        self._update_staged_ui()
        self.track_modified.emit(track)
        self.staged_changes_changed.emit(self.has_staged_changes())
        self.status_message.emit(f"⚠️ Staged adding {cat_singular} '{item_name}' to '{title}' (Unsaved)")

    def _on_tag_create_requested(self, row: int, tag_name: str) -> None:
        """Handle creating a brand-new tag from the table cell editor or context menu."""
        if not self.engine:
            QMessageBox.warning(
                self,
                "Database Unavailable",
                "Database engine is not connected. Cannot create tag.",
            )
            return

        if is_rekordbox_running():
            QMessageBox.warning(
                self,
                "Rekordbox is Running",
                "Please close Rekordbox before creating tags in the database.",
            )
            return

        dlg = CreateTagDialog(
            engine=self.engine,
            parent=self.window() or self,
            initial_name=tag_name,
            default_category="Genre",
        )
        if dlg.exec():
            created = dlg.created_tag
            if created:
                new_name = created.get("name")
                # Reload available items in this table and refresh caches
                self._refresh_available_caches()
                # Stage the newly created tag on this track row!
                if new_name and 0 <= row < len(self.records):
                    self._on_chip_item_added(row, "tags", new_name)
                    self.status_message.emit(f"🏷️ Created and assigned tag '{new_name}'.")
                else:
                    self.status_message.emit(f"🏷️ Created tag '{new_name}'.")
                self.tag_created.emit(new_name)

    def _on_chip_item_deleted(self, row: int, category: str, item_name: str) -> None:
        """Handle removing a tag or playlist item as a staged edit."""
        if not (0 <= row < len(self.records)):
            return

        track = self.records[row]
        content_id = str(track.get("id") or track.get("content_id") or "")
        if not content_id:
            return

        title = track.get("title") or "Track"

        if content_id not in self.staged_edits:
            self.staged_edits[content_id] = {
                "record": track,
                "original_tags": list(dict.fromkeys(track.get("tags") or [])),
                "original_playlists": list(dict.fromkeys(track.get("playlists") or [])),
                "current_tags": list(dict.fromkeys(track.get("tags") or [])),
                "current_playlists": list(dict.fromkeys(track.get("playlists") or [])),
            }

        staged = self.staged_edits[content_id]
        if category == "tags":
            current_tags = staged["current_tags"]
            if item_name in current_tags:
                current_tags.remove(item_name)
            track["tags"] = list(current_tags)
            self._model.update_track_tags(row, current_tags)
            self._clear_chip_hover()
            self.track_tags_changed.emit(row, current_tags)
            cat_singular = "tag"
        elif category == "playlists":
            current_pls = staged["current_playlists"]
            if item_name in current_pls:
                current_pls.remove(item_name)
            track["playlists"] = list(current_pls)
            self._model.update_track_playlists(row, current_pls)
            self._clear_chip_hover()
            self.track_playlists_changed.emit(row, current_pls)
            cat_singular = "playlist"
        else:
            return

        if (
            set(staged["current_tags"]) == set(staged["original_tags"])
            and set(staged["current_playlists"]) == set(staged["original_playlists"])
        ):
            self.staged_edits.pop(content_id, None)

        self._update_staged_ui()
        self.track_modified.emit(track)
        self.staged_changes_changed.emit(self.has_staged_changes())
        self.status_message.emit(f"⚠️ Staged removing {cat_singular} '{item_name}' from '{title}' (Unsaved)")

    def discard_changes(self) -> None:
        """Discard all staged tag and playlist modifications and restore original state."""
        if not self.staged_edits:
            return

        for content_id, data in list(self.staged_edits.items()):
            track = data["record"]
            orig_tags = list(data["original_tags"])
            orig_pls = list(data["original_playlists"])
            track["tags"] = orig_tags
            track["playlists"] = orig_pls
            row = self._find_row_by_id(content_id)
            if row != -1:
                self._model.update_track_tags(row, orig_tags)
                self._model.update_track_playlists(row, orig_pls)
                self.track_tags_changed.emit(row, orig_tags)
                self.track_playlists_changed.emit(row, orig_pls)

        self.staged_edits.clear()
        self._clear_chip_hover()
        self._update_staged_ui()
        self.staged_changes_changed.emit(False)
        self.status_message.emit("Modifications discarded.")

    def save_changes(self) -> bool:
        """Commit all staged tag and playlist modifications to the database."""
        if not self.staged_edits:
            return True

        if is_rekordbox_running():
            QMessageBox.warning(
                self,
                "Rekordbox is Running",
                "Please close Rekordbox before saving modifications to the database.",
            )
            return False

        if not self.engine:
            QMessageBox.warning(
                self,
                "Database Unavailable",
                "Database engine is not connected. Cannot save modifications.",
            )
            return False

        try:
            for content_id, data in self.staged_edits.items():
                orig_pl = set(data["original_playlists"])
                cur_pl = set(data["current_playlists"])
                orig_tags = set(data["original_tags"])
                cur_tags = set(data["current_tags"])

                added_pl = cur_pl - orig_pl
                removed_pl = orig_pl - cur_pl
                added_tags = cur_tags - orig_tags
                removed_tags = orig_tags - cur_tags

                for name in added_pl:
                    add_track_to_playlist(self.engine, str(content_id), name)
                for name in removed_pl:
                    remove_track_from_playlist(self.engine, str(content_id), name)
                for name in added_tags:
                    add_track_to_tag(self.engine, str(content_id), name)
                for name in removed_tags:
                    remove_track_from_tag(self.engine, str(content_id), name)

                data["original_playlists"] = list(data["current_playlists"])
                data["original_tags"] = list(data["current_tags"])
                data["record"]["playlists"] = list(data["current_playlists"])
                data["record"]["tags"] = list(data["current_tags"])

        except Exception as e:
            QMessageBox.critical(
                self, "Save Failed", f"Database error while saving modifications:\n{e}"
            )
            return False

        saved_count = len(self.staged_edits)
        self.staged_edits.clear()
        self._refresh_available_caches()
        self._update_staged_ui()
        self.staged_changes_changed.emit(False)
        self.db_saved.emit()
        self.status_message.emit(
            f"✅ Saved modifications for {saved_count} track{'s' if saved_count != 1 else ''} successfully."
        )
        return True

    def confirm_unsaved_changes(self) -> bool:
        """Prompt user if there are unsaved playlist or tag changes in the table before navigating away.

        Returns True if safe to proceed, False if action was cancelled.
        """
        if not self.has_staged_changes():
            return True

        reply = QMessageBox.question(
            self,
            "Unsaved Modifications",
            "You have unsaved playlist or tag modifications in the table. Do you want to save them before leaving?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if reply == QMessageBox.StandardButton.Save:
            return self.save_changes()
        elif reply == QMessageBox.StandardButton.Discard:
            self.discard_changes()
            return True
        else:
            return False

    def sync_saved_track(self, track: Dict[str, Any]) -> None:
        """Update track data from external save (e.g. from TrackDetailView)."""
        content_id = str(track.get("id") or track.get("content_id") or "")
        if content_id and content_id in self.staged_edits:
            self.staged_edits.pop(content_id, None)
            self._update_staged_ui()
            self.staged_changes_changed.emit(self.has_staged_changes())

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
        self.staged_edits.clear()
        self._update_staged_ui()
        self._model.set_records(records)
        self._update_index_column_width()

    def _update_index_column_width(self) -> None:
        """Ensure column 0 (#) has plenty of room for all digits and never truncates with ellipsis."""
        if TrackColumn.INDEX in self.config.columns:
            col_idx = self.config.columns.index(TrackColumn.INDEX)
            max_val = max(len(self.records), 999)
            fm = self.fontMetrics()
            num_w = fm.horizontalAdvance(str(max_val))
            header_w = fm.horizontalAdvance(" # ")
            col_w = max(58, max(num_w, header_w) + 26)
            self.setColumnWidth(col_idx, col_w)

    def update_track_playlists(self, row_idx: int, playlists: List[str]) -> None:
        """Update the playlists column cell for a specific row without rebuilding table."""
        self._model.update_track_playlists(row_idx, playlists)

    def update_track_tags(self, row_idx: int, tags: List[str]) -> None:
        """Update the tags column cell for a specific row without rebuilding table."""
        self._model.update_track_tags(row_idx, tags)

    def rowCount(self) -> int:
        """Return the number of rows in the table model."""
        return self._model.rowCount()

    def columnCount(self) -> int:
        """Return the number of columns in the table model."""
        return self._model.columnCount()

    def setColumnCount(self, count: int) -> None:
        """Compatibility no-op (column count is defined by table config)."""
        pass

    def setHorizontalHeaderLabels(self, labels: List[str]) -> None:
        """Compatibility no-op (labels are managed by TracksTableModel)."""
        pass

    def horizontalHeaderItem(self, col_idx: int) -> Optional[_TableItemProxy]:
        """Compatibility accessor returning header item proxy for tooltips/text."""
        if 0 <= col_idx < self.columnCount():
            name = str(
                self._model.headerData(
                    col_idx, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole
                )
                or ""
            )
            tip = str(
                self._model.headerData(
                    col_idx, Qt.Orientation.Horizontal, Qt.ItemDataRole.ToolTipRole
                )
                or ""
            )
            return _TableItemProxy(row=-1, col=col_idx, text_val=name, user_data=tip, tip_val=tip)
        return None

    def item(self, row: int, col: int) -> Optional[_TableItemProxy]:
        """Compatibility accessor returning a lightweight item proxy for callers/tests."""
        if 0 <= row < self.rowCount() and 0 <= col < self.columnCount():
            idx = self._model.index(row, col)
            text_val = str(self._model.data(idx, Qt.ItemDataRole.DisplayRole) or "")
            user_data = self._model.data(idx, Qt.ItemDataRole.UserRole)
            font_val = self._model.data(idx, Qt.ItemDataRole.FontRole)
            fg_val = self._model.data(idx, Qt.ItemDataRole.ForegroundRole)
            align_val = self._model.data(idx, Qt.ItemDataRole.TextAlignmentRole)
            tip_val = str(self._model.data(idx, Qt.ItemDataRole.ToolTipRole) or "")
            return _TableItemProxy(
                row=row,
                col=col,
                text_val=text_val,
                user_data=user_data,
                font_val=font_val,
                fg_val=fg_val,
                align_val=align_val,
                tip_val=tip_val,
            )
        return None

    def selected_rows(self) -> List[int]:
        """Return list of selected row indices."""
        sel_model = self.selectionModel()
        if not sel_model:
            return []
        return [idx.row() for idx in sel_model.selectedRows()]

    def selected_records(self) -> List[Dict[str, Any]]:
        """Return list of track dictionary records for selected rows."""
        return [self.records[r] for r in self.selected_rows() if 0 <= r < len(self.records)]

    def selectedItems(self) -> List[_TableItemProxy]:
        """Compatibility accessor returning item proxies for selected rows."""
        return [_TableItemProxy(row=r, col=0, text_val="") for r in self.selected_rows()]

    def scrollToRow(self, row: int) -> None:
        """Scroll to the specified row smoothly."""
        if 0 <= row < self.rowCount():
            idx = self._model.index(row, 0)
            self.scrollTo(idx, QTableView.ScrollHint.EnsureVisible)

    def scrollToItem(self, item: Any) -> None:
        """Compatibility scroll method supporting row proxy or index."""
        if hasattr(item, "row"):
            self.scrollToRow(item.row())
        elif isinstance(item, int):
            self.scrollToRow(item)

    def _create_item_for_column(
        self, col_name: str, t: Dict[str, Any], row_idx: int
    ) -> _TableItemProxy:
        """Compatibility helper generating item proxy for a column."""
        if col_name in self.config.columns:
            col_idx = self.config.columns.index(col_name)
            idx = self._model.index(row_idx, col_idx)
            text_val = str(self._model.data(idx, Qt.ItemDataRole.DisplayRole) or "")
            user_data = self._model.data(idx, Qt.ItemDataRole.UserRole)
            font_val = self._model.data(idx, Qt.ItemDataRole.FontRole)
            fg_val = self._model.data(idx, Qt.ItemDataRole.ForegroundRole)
            align_val = self._model.data(idx, Qt.ItemDataRole.TextAlignmentRole)
            tip_val = str(self._model.data(idx, Qt.ItemDataRole.ToolTipRole) or "")
            return _TableItemProxy(
                row=row_idx,
                col=col_idx,
                text_val=text_val,
                user_data=user_data,
                font_val=font_val,
                fg_val=fg_val,
                align_val=align_val,
                tip_val=tip_val,
            )
        return _TableItemProxy(row=row_idx, col=0, text_val="—")

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

    def _on_cell_double_clicked(self, index_or_row: Any, column: Optional[int] = None) -> None:
        """Double clicking a track row emits track_detail_requested with track record."""
        if isinstance(index_or_row, QModelIndex):
            if index_or_row.column() in (self._tags_col_idx, self._playlist_col_idx):
                return
            row = index_or_row.row()
        elif isinstance(index_or_row, int):
            row = index_or_row
            if column is not None and column in (self._tags_col_idx, self._playlist_col_idx):
                return
        else:
            return
        if 0 <= row < len(self.records):
            if self.has_staged_changes():
                if not self.confirm_unsaved_changes():
                    return
            self.track_detail_requested.emit(self.records[row])

    def _on_table_context_menu(self, pos: QPoint) -> None:
        """Right-click menu on track rows."""
        idx = self.indexAt(pos)
        if not idx.isValid():
            return
        row = idx.row()
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

        playlists = track.get("playlists") or []
        act_pl_map = {}
        if playlists:
            menu.addSeparator()
            if len(playlists) == 1:
                pl_name = playlists[0]
                act_pl = menu.addAction(f"📑 Open Playlist '{pl_name}'")
                act_pl_map[act_pl] = pl_name
            else:
                pl_sub = menu.addMenu("📑 Open in Playlist")
                for pl_name in playlists:
                    sub_act = pl_sub.addAction(pl_name)
                    act_pl_map[sub_act] = pl_name

        tags = track.get("tags") or []
        act_tag_map = {}
        if tags:
            menu.addSeparator()
            if len(tags) == 1:
                t_name = tags[0]
                act_tag = menu.addAction(f"🏷️ Open Tag '{t_name}'")
                act_tag_map[act_tag] = t_name
            else:
                tag_sub = menu.addMenu("🏷️ Open in Tag")
                for t_name in tags:
                    sub_act = tag_sub.addAction(t_name)
                    act_tag_map[sub_act] = t_name

        menu.addSeparator()
        act_create_tag = menu.addAction("🏷️ ＋ Create New Tag...")
        act_add_tag = menu.addAction("🏷️ Add Existing Tag...")
        menu.addSeparator()
        act_open_folder = menu.addAction("📁 Open Containing Folder")
        act_reveal_file = menu.addAction("🔍 Reveal in Finder")
        menu.addSeparator()
        act_copy_file = menu.addAction("📋 Copy File Path")
        act_copy_folder = menu.addAction("📋 Copy Folder Path")
        act_copy_track = menu.addAction("📋 Copy Track (Artist - Title)")

        action = menu.exec(self.viewport().mapToGlobal(pos))
        if action == act_details:
            if self.has_staged_changes():
                if not self.confirm_unsaved_changes():
                    return
            self.track_detail_requested.emit(track)
        elif action == act_replace:
            if self.has_staged_changes():
                if not self.confirm_unsaved_changes():
                    return
            self.replace_file_requested.emit(track)
        elif action in act_pl_map:
            if self.has_staged_changes():
                if not self.confirm_unsaved_changes():
                    return
            self.open_playlist_requested.emit(act_pl_map[action])
        elif action in act_tag_map:
            if self.has_staged_changes():
                if not self.confirm_unsaved_changes():
                    return
            self.open_tag_requested.emit(act_tag_map[action])
        elif action == act_create_tag:
            self._on_tag_create_requested(row, "")
        elif action == act_add_tag:
            if self._tags_col_idx >= 0:
                idx = self.model().index(row, self._tags_col_idx)
                self.scrollTo(idx)
                self._open_chip_add_editor(idx, "tags")
            else:
                self._on_tag_create_requested(row, "")
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
