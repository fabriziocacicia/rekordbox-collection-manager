"""Reusable, high-DPI vector badge chips for the Rekordbox Collection Manager GUI.

Provides smooth anti-aliased rendering for metadata badges (Tags, Playlists,
Play Count, History Sessions) with consistent styling, zero rasterization artifacts,
and interactive hover/removal capabilities.
"""

from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, QStringListModel, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QCompleter,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from rekordbox_collection_manager.gui.theme import (
    COLOR_BG_HOVER,
    COLOR_BG_INPUT,
    COLOR_BG_PANEL,
    COLOR_BLUE,
    COLOR_BLUE_BG,
    COLOR_BORDER,
    COLOR_BORDER_FOCUS,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_PRIMARY,
)

# Predefined curated color schemes for badges
CHIP_SCHEMES: Dict[str, Dict[str, QColor]] = {
    "teal": {
        "bg": QColor(20, 184, 166, 28),
        "bg_hover": QColor(20, 184, 166, 48),
        "border": QColor(45, 212, 191, 75),
        "border_hover": QColor(45, 212, 191, 150),
        "text": QColor("#5eead4"),
    },
    "purple": {
        "bg": QColor(139, 92, 246, 28),
        "bg_hover": QColor(139, 92, 246, 48),
        "border": QColor(167, 139, 250, 75),
        "border_hover": QColor(167, 139, 250, 150),
        "text": QColor("#d8b4fe"),
    },
    "green": {
        "bg": QColor(16, 185, 129, 28),
        "bg_hover": QColor(16, 185, 129, 48),
        "border": QColor(52, 211, 153, 75),
        "border_hover": QColor(52, 211, 153, 150),
        "text": QColor("#6ee7b7"),
    },
    "blue": {
        "bg": QColor(14, 165, 233, 28),
        "bg_hover": QColor(14, 165, 233, 48),
        "border": QColor(56, 189, 248, 75),
        "border_hover": QColor(56, 189, 248, 150),
        "text": QColor("#7dd3fc"),
    },
    "amber": {
        "bg": QColor(245, 158, 11, 38),
        "bg_hover": QColor(245, 158, 11, 70),
        "border": QColor(251, 191, 36, 110),
        "border_hover": QColor(251, 191, 36, 200),
        "text": QColor("#fde68a"),
    },
}


class ChipDeleteButton(QPushButton):
    """Anti-aliased vector delete button for removable badge chips."""

    def __init__(
        self,
        border_color: Optional[QColor] = None,
        text_color: Optional[QColor] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("chipDeleteBtn")
        self.setFixedSize(14, 14)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Remove")
        self.border_color = border_color or QColor(255, 255, 255, 100)
        self.text_color = text_color or QColor("#ffffff")
        self._hovered = False

    def set_colors(self, border_color: QColor, text_color: QColor) -> None:
        self.border_color = border_color
        self.text_color = text_color
        self.update()

    def enterEvent(self, event: Any) -> None:
        try:
            super().enterEvent(event)
        except Exception:
            pass
        self._hovered = True
        self.update()

    def leaveEvent(self, event: Any) -> None:
        try:
            super().leaveEvent(event)
        except Exception:
            pass
        self._hovered = False
        self.update()

    def paintEvent(self, event: Any) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0, 0, 14, 14)
        if self._hovered:
            p.setBrush(QColor("#ef4444"))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(rect)
            pen = QPen(QColor("#ffffff"), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        else:
            p.setBrush(QColor(22, 24, 32, 235))
            p.setPen(QPen(self.border_color, 1.0))
            p.drawEllipse(rect.adjusted(0.5, 0.5, -0.5, -0.5))
            pen = QPen(self.text_color, 1.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        pad = 4.0
        p.drawLine(pad, pad, 14.0 - pad, 14.0 - pad)
        p.drawLine(pad, 14.0 - pad, 14.0 - pad, pad)
        p.end()


class BadgeChip(QFrame):
    """Universal anti-aliased badge chip for displaying tags, metadata, and playlists.

    Features vector-drawn rounded borders, completely balanced text padding (no blank space),
    and an elegant top-right corner removal badge on hover.
    """

    delete_requested = Signal(str)

    def __init__(
        self,
        text: str,
        scheme: str = "teal",
        removable: bool = False,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.name = text
        self.text = text
        self.scheme_name = scheme
        self.scheme = CHIP_SCHEMES.get(scheme, CHIP_SCHEMES["teal"])
        self.removable = removable
        self._hovered = False

        self.headroom_top = 4 if removable else 0
        self.headroom_bottom = 4 if removable else 0
        self.headroom_right = 5 if removable else 0
        self.pill_h = 24
        self.setFixedHeight(self.pill_h + self.headroom_top + self.headroom_bottom)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent; border: none;")

        layout = QHBoxLayout(self)
        # Symmetrical 10px horizontal padding inside pill (no right-side blank space)
        layout.setContentsMargins(10, self.headroom_top, 10 + self.headroom_right, self.headroom_bottom)
        layout.setSpacing(0)

        self.label = QLabel(text)
        self.label.setStyleSheet(
            f"color: {self.scheme['text'].name()}; font-size: 11px; font-weight: 500; "
            "background: transparent; border: none;"
        )
        layout.addWidget(self.label)

        if self.removable:
            self.x_btn = ChipDeleteButton(
                border_color=self.scheme["border"],
                text_color=self.scheme["text"],
                parent=self,
            )
            self.x_btn.setToolTip(f"Remove '{text}'")
            self.x_btn.setVisible(False)
            self.x_btn.clicked.connect(lambda: self.delete_requested.emit(self.text))
            self._position_x_btn()

    def text(self) -> str:
        return self.label.text()

    def setText(self, text: str) -> None:
        self.set_text(text)

    def set_text(self, text: str) -> None:
        self.name = text
        self.label.setText(text)
        if self.removable and hasattr(self, "x_btn"):
            self.x_btn.setToolTip(f"Remove '{text}'")
            self._position_x_btn()

    def set_scheme(self, scheme: str) -> None:
        self.scheme_name = scheme
        self.scheme = CHIP_SCHEMES.get(scheme, CHIP_SCHEMES["teal"])
        self.label.setStyleSheet(
            f"color: {self.scheme['text'].name()}; font-size: 11px; font-weight: 500; "
            "background: transparent; border: none;"
        )
        if self.removable and hasattr(self, "x_btn"):
            self.x_btn.set_colors(self.scheme["border"], self.scheme["text"])
        self.update()

    def _position_x_btn(self) -> None:
        if self.removable and hasattr(self, "x_btn"):
            pill_right = self.width() - self.headroom_right - 1.0
            pill_top = self.headroom_top + 0.5
            btn_w = self.x_btn.width()
            btn_h = self.x_btn.height()
            self.x_btn.move(int(pill_right - btn_w / 2), int(pill_top - btn_h / 2 + 1))

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._position_x_btn()

    def enterEvent(self, event: Any) -> None:
        try:
            super().enterEvent(event)
        except Exception:
            pass
        self._hovered = True
        if self.removable and hasattr(self, "x_btn"):
            self._position_x_btn()
            self.x_btn.setVisible(True)
        self.update()

    def leaveEvent(self, event: Any) -> None:
        try:
            super().leaveEvent(event)
        except Exception:
            pass
        self._hovered = False
        if self.removable and hasattr(self, "x_btn"):
            self.x_btn.setVisible(False)
        self.update()

    def paintEvent(self, event: Any) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pill_rect = QRectF(
            0.5,
            self.headroom_top + 0.5,
            self.width() - self.headroom_right - 1.0,
            self.pill_h - 1.0,
        )
        radius = 5.0
        if self._hovered:
            p.setBrush(self.scheme["bg_hover"])
            p.setPen(QPen(self.scheme["border_hover"], 1.0))
        else:
            p.setBrush(self.scheme["bg"])
            p.setPen(QPen(self.scheme["border"], 1.0))
        p.drawRoundedRect(pill_rect, radius, radius)
        p.end()


class PlaylistChip(BadgeChip):
    """Specialized removable badge chip for playlist membership (teal color scheme)."""

    def __init__(self, name: str, parent: Optional[QWidget] = None):
        super().__init__(text=name, scheme="teal", removable=True, parent=parent)


class TagChip(BadgeChip):
    """Specialized removable badge chip for My Tags (purple color scheme)."""

    def __init__(self, name: str, parent: Optional[QWidget] = None):
        super().__init__(text=name, scheme="purple", removable=True, parent=parent)


class DJChip(BadgeChip):
    """Specialized removable badge chip for played with DJ / artist (amber color scheme)."""

    def __init__(self, name: str, parent: Optional[QWidget] = None):
        super().__init__(text=name, scheme="amber", removable=True, parent=parent)


class FlowLayout(QLayout):
    """Layout that arranges child widgets horizontally, wrapping onto new lines."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        margin: int = 0,
        spacing: int = 6,
    ):
        super().__init__(parent)
        self._items: List[QLayoutItem] = []
        self.setContentsMargins(margin, margin, margin, margin)
        self._spacing = spacing

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> Optional[QLayoutItem]:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> Optional[QLayoutItem]:
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            widget = item.widget()
            if widget and widget.isHidden():
                continue
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        effective_rect = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        spacing_x = self._spacing
        spacing_y = self._spacing

        lines: List[List[Tuple[QLayoutItem, int, int]]] = []
        current_line: List[Tuple[QLayoutItem, int, int]] = []
        current_line_w = 0
        current_line_h = 0

        for item in self._items:
            widget = item.widget()
            if widget and widget.isHidden():
                continue
            item_w = item.sizeHint().width()
            item_h = item.sizeHint().height()

            if current_line and (current_line_w + spacing_x + item_w > effective_rect.width()):
                lines.append(current_line)
                current_line = []
                current_line_w = 0
                current_line_h = 0

            current_line.append((item, item_w, item_h))
            if current_line_w > 0:
                current_line_w += spacing_x
            current_line_w += item_w
            current_line_h = max(current_line_h, item_h)

        if current_line:
            lines.append(current_line)

        line_heights: List[int] = [max(h for _, _, h in line) for line in lines] if lines else []
        total_content_h = sum(line_heights)
        if len(lines) > 1:
            total_content_h += spacing_y * (len(lines) - 1)

        if test_only:
            return total_content_h + m.top() + m.bottom()

        # Vertically center content if the container is taller than total lines
        start_y = effective_rect.y()
        if effective_rect.height() > total_content_h:
            start_y += (effective_rect.height() - total_content_h) // 2

        cur_y = start_y
        for line, lh in zip(lines, line_heights):
            cur_x = effective_rect.x()
            for item, item_w, item_h in line:
                item_y = cur_y + (lh - item_h) // 2
                item.setGeometry(QRect(cur_x, item_y, item_w, item_h))
                cur_x += item_w + spacing_x
            cur_y += lh + spacing_y

        return total_content_h + m.top() + m.bottom()


class ChipSearchLineEdit(QLineEdit):
    """Line edit that notifies parent when Escape is pressed or focus is lost."""

    escape_pressed = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.textChanged.connect(lambda _: self.updateGeometry())

    def sizeHint(self) -> QSize:
        base = super().sizeHint()
        fm = self.fontMetrics()
        text_w = fm.horizontalAdvance(self.text()) if self.text() else 0
        ph_w = fm.horizontalAdvance(self.placeholderText()) if self.placeholderText() else 0
        needed_w = max(text_w, ph_w) + 24
        w = max(self.minimumWidth(), min(needed_w, self.maximumWidth()))
        return QSize(w, base.height())

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.escape_pressed.emit()
            event.accept()
            return
        elif event.key() == Qt.Key.Key_Down and not self.text():
            completer = self.completer()
            if completer:
                completer.setCompletionPrefix("")
                completer.complete()
                event.accept()
                return
        super().keyPressEvent(event)

    def focusOutEvent(self, event: Any) -> None:
        completer = self.completer()
        if completer and completer.popup() and completer.popup().isVisible():
            super().focusOutEvent(event)
            return
        super().focusOutEvent(event)
        self.escape_pressed.emit()


# Backward-compatible alias
PlaylistSearchLineEdit = ChipSearchLineEdit


class EditableChipsContainer(QWidget):
    """Generic editable chips container with autocompletable dropdown and inline search."""

    item_added = Signal(str)
    item_removed = Signal(str)
    items_changed = Signal(list)
    editing_state_changed = Signal(bool)

    def __init__(
        self,
        scheme: str = "teal",
        chip_class: type = PlaylistChip,
        placeholder: str = "Search to add...",
        empty_text: str = "None assigned (click here to add)",
        object_name: str = "editableChipsContainer",
        allow_custom: bool = False,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.scheme = scheme
        self.chip_class = chip_class
        self.allow_custom = allow_custom
        self.setObjectName(object_name)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent; border: none;")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(28)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.flow_layout = FlowLayout(self, margin=0, spacing=6)

        self.items: List[str] = []
        self.all_available_items: List[str] = []
        self.chips: List[BadgeChip] = []

        # Empty state prompt
        self.empty_label = QLabel(empty_text)
        self.empty_label.setStyleSheet(
            f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-style: italic; background: transparent;"
        )
        self.empty_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.flow_layout.addWidget(self.empty_label)

        # Inline search input
        self.input_edit = ChipSearchLineEdit(self)
        self.input_edit.setPlaceholderText(placeholder)
        self.input_edit.setFixedHeight(24)
        self.input_edit.setMinimumWidth(80)
        self.input_edit.setMaximumWidth(200)
        self.input_edit.setStyleSheet(
            f"QLineEdit {{ "
            f"  background-color: {COLOR_BG_INPUT}; "
            f"  color: {COLOR_TEXT_PRIMARY}; "
            f"  border: 1px solid {COLOR_BORDER_FOCUS}; "
            f"  border-radius: 6px; "
            f"  padding: 2px 8px; "
            f"  font-size: 11px; "
            f"}}"
        )
        self.input_edit.setVisible(False)
        self.input_edit.escape_pressed.connect(self.hide_add_input)
        self.input_edit.returnPressed.connect(self._on_input_return_pressed)

        # Autocompleter
        self.completer_model = QStringListModel(self)
        self.completer = QCompleter(self.completer_model, self.input_edit)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)

        popup = self.completer.popup()
        if popup:
            popup.setMinimumWidth(240)
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
                f"  padding: 6px 10px; "
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

        # input_edit is appended after chips
        self.flow_layout.addWidget(self.input_edit)

    def _update_popup_width(self, candidates: Optional[List[str]] = None) -> None:
        """Ensure completer popup is wide enough to display items without clipping."""
        popup = self.completer.popup()
        if not popup:
            return
        items = candidates if candidates is not None else self.completer_model.stringList()
        fm = popup.fontMetrics()
        max_item_w = max((fm.horizontalAdvance(item) for item in items), default=0)
        needed_w = max_item_w + 48
        popup.setMinimumWidth(max(240, min(needed_w, 450)))

    def set_available_items(self, items: List[str]) -> None:
        self.all_available_items = list(items)
        if self.is_input_active():
            assigned_lower = {p.lower() for p in self.items}
            candidates = [p for p in self.all_available_items if p.lower() not in assigned_lower]
            self.completer_model.setStringList(candidates)
            self._update_popup_width(candidates)
        else:
            self._update_popup_width(self.all_available_items)

    def set_items(self, items: List[str]) -> None:
        self.items = list(items)
        for chip in list(self.chips):
            self.flow_layout.removeWidget(chip)
            chip.deleteLater()
        self.chips.clear()

        self.flow_layout.removeWidget(self.input_edit)

        for name in self.items:
            chip = self.chip_class(name, self)
            chip.delete_requested.connect(self._on_chip_delete)
            self.chips.append(chip)
            self.flow_layout.addWidget(chip)

        self.flow_layout.addWidget(self.input_edit)
        self._refresh_visibility()
        self.updateGeometry()
        self.flow_layout.activate()

    def get_items(self) -> List[str]:
        return list(self.items)

    def _refresh_visibility(self) -> None:
        has_chips = len(self.chips) > 0
        is_editing = not self.input_edit.isHidden()
        if is_editing:
            self.empty_label.setVisible(False)
        else:
            self.empty_label.setVisible(not has_chips)

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            child = self.childAt(pos)
            if child is None or child is self or child is self.empty_label:
                self.show_add_input()
                event.accept()
                return
        super().mousePressEvent(event)

    def show_add_input(self) -> None:
        if self.is_input_active():
            self.input_edit.setFocus()
            return

        assigned_lower = {p.lower() for p in self.items}
        candidates = [p for p in self.all_available_items if p.lower() not in assigned_lower]
        self.completer_model.setStringList(candidates)
        self._update_popup_width(candidates)

        self.empty_label.setVisible(False)
        self.input_edit.clear()
        self.input_edit.setVisible(True)
        self.input_edit.setFocus()
        self._refresh_visibility()
        self.updateGeometry()
        self.flow_layout.activate()
        self.editing_state_changed.emit(True)

    def hide_add_input(self) -> None:
        self.input_edit.setVisible(False)
        self._refresh_visibility()
        self.updateGeometry()
        self.flow_layout.activate()
        self.editing_state_changed.emit(False)

    def is_input_active(self) -> bool:
        return not self.input_edit.isHidden()

    def _add_single_item(self, name: str) -> bool:
        name = name.strip()
        if not name or name in self.items:
            return False

        self.items.append(name)
        if name not in self.all_available_items:
            self.all_available_items.append(name)
        chip = self.chip_class(name, self)
        chip.delete_requested.connect(self._on_chip_delete)
        self.chips.append(chip)

        self.flow_layout.removeWidget(self.input_edit)
        self.flow_layout.addWidget(chip)
        self.flow_layout.addWidget(self.input_edit)

        self._refresh_visibility()
        self.updateGeometry()
        self.flow_layout.activate()
        self.item_added.emit(name)
        return True

    def _on_input_return_pressed(self) -> None:
        text = self.input_edit.text().strip()
        if not text:
            self.hide_add_input()
            return

        # If comma-separated tokens were entered (e.g. pasted "DJ Alpha, DJ Beta")
        tokens = [t.strip() for t in text.split(",") if t.strip()]
        if len(tokens) > 1 and self.allow_custom:
            self.hide_add_input()
            added_any = False
            for token in tokens:
                if self._add_single_item(token):
                    added_any = True
            if added_any:
                self.items_changed.emit(list(self.items))
            return

        matched = None
        for p in self.all_available_items:
            if p.lower() == text.lower():
                matched = p
                break
        if not matched and self.completer.currentCompletion():
            cand = self.completer.currentCompletion()
            if cand.lower().startswith(text.lower()):
                matched = cand

        if matched:
            self._on_item_chosen(matched)
        elif self.allow_custom:
            self._on_item_chosen(text)
        else:
            self.hide_add_input()

    def _on_item_chosen(self, name: str) -> None:
        self.hide_add_input()
        if self._add_single_item(name):
            self.items_changed.emit(list(self.items))

    def _on_chip_delete(self, name: str) -> None:
        for chip in list(self.chips):
            if chip.name == name:
                self.chips.remove(chip)
                self.flow_layout.removeWidget(chip)
                chip.deleteLater()
        if name in self.items:
            self.items.remove(name)
        self._refresh_visibility()
        self.updateGeometry()
        self.flow_layout.activate()
        self.item_removed.emit(name)
        self.items_changed.emit(list(self.items))


class PlaylistChipsContainer(EditableChipsContainer):
    """Editable playlist chips container with autocompletable dropdown and inline search."""

    playlist_added = Signal(str)
    playlist_removed = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(
            scheme="teal",
            chip_class=PlaylistChip,
            placeholder="Search playlist to add...",
            empty_text="Not in any playlist (click here to add)",
            object_name="playlistChipsContainer",
            allow_custom=False,
            parent=parent,
        )
        self.item_added.connect(self.playlist_added.emit)
        self.item_removed.connect(self.playlist_removed.emit)

    @property
    def playlists(self) -> List[str]:
        return self.items

    @playlists.setter
    def playlists(self, val: List[str]) -> None:
        self.set_items(val)

    @property
    def all_available_playlists(self) -> List[str]:
        return self.all_available_items

    @all_available_playlists.setter
    def all_available_playlists(self, val: List[str]) -> None:
        self.all_available_items = val

    def set_available_playlists(self, playlists: List[str]) -> None:
        self.set_available_items(playlists)

    def set_playlists(self, playlists: List[str]) -> None:
        self.set_items(playlists)

    def get_playlists(self) -> List[str]:
        return self.get_items()

    def _on_playlist_chosen(self, name: str) -> None:
        self._on_item_chosen(name)


class TagChipsContainer(EditableChipsContainer):
    """Editable My Tags chips container with autocompletable dropdown and inline search."""

    tag_added = Signal(str)
    tag_removed = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(
            scheme="purple",
            chip_class=TagChip,
            placeholder="Search tag to add...",
            empty_text="No tags assigned (click here to add)",
            object_name="tagChipsContainer",
            allow_custom=False,
            parent=parent,
        )
        self.item_added.connect(self.tag_added.emit)
        self.item_removed.connect(self.tag_removed.emit)

    @property
    def tags(self) -> List[str]:
        return self.items

    @tags.setter
    def tags(self, val: List[str]) -> None:
        self.set_items(val)

    @property
    def all_available_tags(self) -> List[str]:
        return self.all_available_items

    @all_available_tags.setter
    def all_available_tags(self, val: List[str]) -> None:
        self.all_available_items = val

    def set_available_tags(self, tags: List[str]) -> None:
        self.set_available_items(tags)

    def set_tags(self, tags: List[str]) -> None:
        self.set_items(tags)

    def get_tags(self) -> List[str]:
        return self.get_items()

    def _on_tag_chosen(self, name: str) -> None:
        self._on_item_chosen(name)


class DJChipsContainer(EditableChipsContainer):
    """Editable played with DJ chips container with autocompletable dropdown and inline search."""

    djs_added = Signal(str)
    djs_removed = Signal(str)
    djs_changed = Signal(list)

    def __init__(self, parent: Optional[QWidget] = None, allow_custom: bool = True):
        super().__init__(
            scheme="amber",
            chip_class=DJChip,
            placeholder="+ Add DJ...",
            empty_text="None (click to add DJ)",
            object_name="djChipsContainer",
            allow_custom=allow_custom,
            parent=parent,
        )
        self.item_added.connect(self.djs_added.emit)
        self.item_removed.connect(self.djs_removed.emit)
        self.items_changed.connect(self.djs_changed.emit)

    @property
    def djs(self) -> List[str]:
        return self.items

    @djs.setter
    def djs(self, val: List[str]) -> None:
        self.set_items(val)

    @property
    def all_available_djs(self) -> List[str]:
        return self.all_available_items

    @all_available_djs.setter
    def all_available_djs(self, val: List[str]) -> None:
        self.all_available_items = val

    def set_available_djs(self, djs: List[str]) -> None:
        self.set_available_items(djs)

    def set_djs(self, djs: List[str]) -> None:
        self.set_items(djs)

    def get_djs(self) -> List[str]:
        return self.get_items()

    def _on_dj_chosen(self, name: str) -> None:
        self._on_item_chosen(name)


