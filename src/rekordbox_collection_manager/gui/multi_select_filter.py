"""Searchable multi-select filter widget with popup checklist and live reactive filtering for PyQt6/PySide6."""

import time
from typing import Any, List, Optional, Sequence, Set
from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class CheckListWidget(QListWidget):
    """Custom checklist that cleanly toggles item check state without double-toggling."""

    def mouseReleaseEvent(self, event) -> None:
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        item = self.itemAt(pos)
        if item:
            prev = item.checkState()
            super().mouseReleaseEvent(event)
            # If Qt did not toggle the checkbox (user clicked on text or row background),
            # toggle it explicitly on mouse release:
            if item.checkState() == prev:
                new_state = (
                    Qt.CheckState.Unchecked
                    if prev == Qt.CheckState.Checked
                    else Qt.CheckState.Checked
                )
                item.setCheckState(new_state)
        else:
            super().mouseReleaseEvent(event)


class MultiSelectPopup(QDialog):
    """Floating popup containing an instant-search checklist with live reactive filtering and ANY/ALL matching."""

    selection_committed = Signal(list, str)  # (selected_items: list[str], match_mode: str)

    def __init__(
        self,
        title: str,
        icon: str = "",
        enable_match_mode: bool = True,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(320, 360)

        self.title_text = title
        self.icon_text = icon
        self.enable_match_mode = enable_match_mode

        self._all_items: List[str] = []
        self._selected_items: Set[str] = set()
        self._match_mode: str = "any"
        self._last_hide_timestamp: float = 0.0

        # Debounce timer for fast, stutter-free text filtering
        self.search_debounce_timer = QTimer(self)
        self.search_debounce_timer.setSingleShot(True)
        self.search_debounce_timer.setInterval(60)
        self.search_debounce_timer.timeout.connect(self._apply_filter)

        self._init_ui()

    @property
    def _committed_items(self) -> Set[str]:
        return self._selected_items

    @_committed_items.setter
    def _committed_items(self, val: Any) -> None:
        self._selected_items = set(val)

    @property
    def _staged_items(self) -> Set[str]:
        return self._selected_items

    @_staged_items.setter
    def _staged_items(self, val: Any) -> None:
        self._selected_items = set(val)

    @property
    def _committed_match_mode(self) -> str:
        return self._match_mode

    @_committed_match_mode.setter
    def _committed_match_mode(self, val: str) -> None:
        self._match_mode = val

    @property
    def _staged_match_mode(self) -> str:
        return self._match_mode

    @_staged_match_mode.setter
    def _staged_match_mode(self, val: str) -> None:
        self._match_mode = val

    def _init_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        self.container = QFrame(self)
        self.container.setObjectName("popupContainer")
        self.container.setStyleSheet("""
            QFrame#popupContainer {
                background-color: #0f172a;
                border: 1px solid #334155;
                border-radius: 8px;
            }
        """)

        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(7)

        # 1. Header (Title + Close Button)
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)

        prefix = f"{self.icon_text} " if self.icon_text else ""
        self.header_label = QLabel(f"{prefix}Filter by {self.title_text}")
        self.header_label.setStyleSheet("color: #f8fafc; font-size: 13px; font-weight: bold;")
        header_layout.addWidget(self.header_label)

        header_layout.addStretch(1)

        self.btn_close = QPushButton("✕")
        self.btn_close.setFixedSize(22, 22)
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setToolTip("Close (Esc)")
        self.btn_close.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #94a3b8;
                border: none;
                font-size: 13px;
                font-weight: bold;
                border-radius: 4px;
            }
            QPushButton:hover {
                background: #334155;
                color: #f8fafc;
            }
        """)
        self.btn_close.clicked.connect(self.close)
        header_layout.addWidget(self.btn_close)
        layout.addLayout(header_layout)

        # 2. Match Mode Bar (Any / All)
        if self.enable_match_mode:
            match_layout = QHBoxLayout()
            match_layout.setContentsMargins(0, 0, 0, 0)
            match_layout.setSpacing(6)

            lbl_match = QLabel("Match:")
            lbl_match.setStyleSheet("color: #94a3b8; font-size: 11px;")
            match_layout.addWidget(lbl_match)

            self.btn_match_any = QPushButton("Any (OR)")
            self.btn_match_any.setFixedHeight(22)
            self.btn_match_any.setCursor(Qt.CursorShape.PointingHandCursor)
            self.btn_match_any.setToolTip("Show tracks matching AT LEAST ONE selected value")
            self.btn_match_any.clicked.connect(lambda: self._set_match_mode("any"))
            match_layout.addWidget(self.btn_match_any)

            self.btn_match_all = QPushButton("All (AND)")
            self.btn_match_all.setFixedHeight(22)
            self.btn_match_all.setCursor(Qt.CursorShape.PointingHandCursor)
            self.btn_match_all.setToolTip("Show tracks matching ALL selected values simultaneously")
            self.btn_match_all.clicked.connect(lambda: self._set_match_mode("all"))
            match_layout.addWidget(self.btn_match_all)

            match_layout.addStretch(1)
            layout.addLayout(match_layout)
            self._update_match_buttons_style()

        # 3. Search Box
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(f"🔍 Type to search {self.title_text.lower()}...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setStyleSheet("""
            QLineEdit {
                background-color: #1e293b;
                color: #f8fafc;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 5px 8px;
                font-size: 12px;
            }
            QLineEdit:focus {
                border: 1px solid #10b981;
            }
        """)
        self.search_input.textChanged.connect(lambda _: self.search_debounce_timer.start())
        self.search_input.returnPressed.connect(self.close)
        layout.addWidget(self.search_input)

        # 4. Selection Toolbar (Count + Select All / Clear)
        bar_layout = QHBoxLayout()
        bar_layout.setContentsMargins(0, 0, 0, 0)

        self.count_label = QLabel("0 selected")
        self.count_label.setStyleSheet("color: #94a3b8; font-size: 11px;")
        bar_layout.addWidget(self.count_label)

        bar_layout.addStretch(1)

        self.btn_select_all = QPushButton("Select All")
        self.btn_select_all.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #38bdf8;
                border: none;
                font-size: 11px;
                padding: 1px 4px;
            }
            QPushButton:hover {
                text-decoration: underline;
                color: #7dd3fc;
            }
        """)
        self.btn_select_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_select_all.clicked.connect(self._select_all_visible)
        bar_layout.addWidget(self.btn_select_all)

        self.btn_clear_sel = QPushButton("Clear")
        self.btn_clear_sel.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #f87171;
                border: none;
                font-size: 11px;
                padding: 1px 4px;
            }
            QPushButton:hover {
                text-decoration: underline;
                color: #fca5a5;
            }
        """)
        self.btn_clear_sel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear_sel.clicked.connect(self._clear_selection)
        bar_layout.addWidget(self.btn_clear_sel)

        layout.addLayout(bar_layout)

        # 5. List Widget
        self.list_widget = CheckListWidget()
        self.list_widget.setStyleSheet("""
            QListWidget {
                background-color: #0f172a;
                border: 1px solid #1e293b;
                border-radius: 6px;
                outline: none;
                color: #f8fafc;
            }
            QListWidget::item {
                padding: 4px 6px;
                border-radius: 4px;
                margin: 1px 2px;
            }
            QListWidget::item:hover {
                background-color: #1e293b;
            }
            QListWidget::item:selected {
                background-color: #1e293b;
                color: #f8fafc;
            }
            QScrollBar:vertical {
                background: #0f172a;
                width: 6px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background: #334155;
                min-height: 20px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical:hover {
                background: #475569;
            }
        """)
        self.list_widget.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.list_widget, stretch=1)

        # Empty state label
        self.empty_label = QLabel(f"No {self.title_text.lower()} found")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet("color: #64748b; font-size: 11px; padding: 20px;")
        self.empty_label.setVisible(False)
        layout.addWidget(self.empty_label)

        outer_layout.addWidget(self.container)

    def _update_match_buttons_style(self) -> None:
        if not self.enable_match_mode:
            return
        active_style = """
            QPushButton {
                background-color: #059669;
                color: #ffffff;
                font-weight: bold;
                font-size: 10px;
                border-radius: 4px;
                border: 1px solid #10b981;
                padding: 1px 8px;
            }
        """
        inactive_style = """
            QPushButton {
                background-color: #1e293b;
                color: #94a3b8;
                font-size: 10px;
                border-radius: 4px;
                border: 1px solid #334155;
                padding: 1px 8px;
            }
            QPushButton:hover {
                color: #f8fafc;
                background-color: #334155;
            }
        """
        if self._match_mode == "any":
            self.btn_match_any.setStyleSheet(active_style)
            self.btn_match_all.setStyleSheet(inactive_style)
        else:
            self.btn_match_any.setStyleSheet(inactive_style)
            self.btn_match_all.setStyleSheet(active_style)

    def _set_match_mode(self, mode: str) -> None:
        if self._match_mode == mode:
            return
        self._match_mode = mode
        self._update_match_buttons_style()
        if self._selected_items:
            self.selection_committed.emit(self.get_selected(), self._match_mode)

    def _set_staged_match_mode(self, mode: str) -> None:
        self._set_match_mode(mode)

    def set_items(self, items: Sequence[str]) -> None:
        """Populate the list with available item names, preserving active selection."""
        self._all_items = sorted(list(items), key=lambda x: str(x).lower())
        self._selected_items = {i for i in self._selected_items if i in self._all_items}
        self._rebuild_list_items()

    def _rebuild_list_items(self) -> None:
        self.list_widget.blockSignals(True)
        self.list_widget.clear()

        for name in self._all_items:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            state = Qt.CheckState.Checked if name in self._selected_items else Qt.CheckState.Unchecked
            item.setCheckState(state)
            self.list_widget.addItem(item)

        self.list_widget.blockSignals(False)
        self._apply_filter()
        self._update_count_label()

    def sync_staged_from_committed(self) -> None:
        """Clear search query and refresh view when opening popup."""
        self.search_input.clear()
        self._apply_filter()
        self._update_count_label()

    def _apply_filter(self) -> None:
        query_lower = self.search_input.text().strip().lower()
        self.list_widget.setUpdatesEnabled(False)
        visible_count = 0

        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            matches = not query_lower or query_lower in item.text().lower()
            item.setHidden(not matches)
            if matches:
                visible_count += 1

        self.list_widget.setUpdatesEnabled(True)

        if visible_count == 0 and self.list_widget.count() > 0:
            self.list_widget.setVisible(False)
            self.empty_label.setText(f"No {self.title_text.lower()} found for '{query_lower}'")
            self.empty_label.setVisible(True)
        else:
            self.list_widget.setVisible(True)
            self.empty_label.setVisible(False)

        if query_lower:
            self.btn_select_all.setText("Select Filtered")
        else:
            self.btn_select_all.setText("Select All")

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        name = item.text()
        if item.checkState() == Qt.CheckState.Checked:
            self._selected_items.add(name)
        else:
            self._selected_items.discard(name)
        self._update_count_label()
        self.selection_committed.emit(self.get_selected(), self._match_mode)

    def _select_all_visible(self) -> None:
        self.list_widget.blockSignals(True)
        changed = False
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if not item.isHidden():
                if item.checkState() != Qt.CheckState.Checked:
                    item.setCheckState(Qt.CheckState.Checked)
                    self._selected_items.add(item.text())
                    changed = True
        self.list_widget.blockSignals(False)
        self._update_count_label()
        if changed:
            self.selection_committed.emit(self.get_selected(), self._match_mode)

    def _clear_selection(self) -> None:
        if not self._selected_items:
            return
        self.list_widget.blockSignals(True)
        for i in range(self.list_widget.count()):
            self.list_widget.item(i).setCheckState(Qt.CheckState.Unchecked)
        self._selected_items.clear()
        self.list_widget.blockSignals(False)
        self._update_count_label()
        self.selection_committed.emit([], self._match_mode)

    def _clear_staged_selection(self) -> None:
        self._clear_selection()

    def _update_count_label(self) -> None:
        cnt = len(self._selected_items)
        total = len(self._all_items)
        if total > 0:
            self.count_label.setText(f"{cnt} of {total} selected")
        else:
            self.count_label.setText(f"{cnt} selected")

    def _apply_and_close(self) -> None:
        self.close()

    def _cancel_and_close(self) -> None:
        self.close()

    def hideEvent(self, event) -> None:
        self._last_hide_timestamp = time.monotonic()
        super().hideEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape):
            self.close()
        else:
            super().keyPressEvent(event)

    def get_selected(self) -> List[str]:
        return sorted(list(self._selected_items))

    def get_match_mode(self) -> str:
        return self._match_mode

    def set_selected(self, items: Sequence[str], match_mode: str = "any") -> None:
        self._selected_items = set(items)
        self._match_mode = match_mode

        self.list_widget.blockSignals(True)
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            state = Qt.CheckState.Checked if item.text() in self._selected_items else Qt.CheckState.Unchecked
            item.setCheckState(state)
        self.list_widget.blockSignals(False)

        self._update_match_buttons_style()
        self._update_count_label()
        self.selection_committed.emit(self.get_selected(), self._match_mode)

    def clear_all(self) -> None:
        self._clear_selection()


class MultiSelectSearchFilter(QWidget):
    """Button filter widget with searchable multi-select popup checklist for table filtering."""

    filter_changed = Signal()

    def __init__(
        self,
        title: str,
        icon: str = "",
        enable_match_mode: bool = True,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.title_text = title
        self.icon_text = icon

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.button = QPushButton(self)
        self.button.setFixedHeight(28)
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.clicked.connect(self._toggle_popup)
        layout.addWidget(self.button)

        self.popup = MultiSelectPopup(
            title=title,
            icon=icon,
            enable_match_mode=enable_match_mode,
            parent=self,
        )
        self.popup.selection_committed.connect(self._on_selection_committed)

        self._update_button_display()

    def _toggle_popup(self) -> None:
        # Prevent re-opening immediately if click was what dismissed the popup
        now = time.monotonic()
        if now - self.popup._last_hide_timestamp < 0.25:
            return

        if self.popup.isVisible():
            self.popup.close()
            return

        self.popup.sync_staged_from_committed()

        # Position popup directly beneath the button with screen edge bounds checking
        btn_geo = self.button.rect()
        bottom_left = self.button.mapToGlobal(btn_geo.bottomLeft())
        x = bottom_left.x()
        y = bottom_left.y() + 3

        screen = self.screen() or QApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            popup_w = self.popup.width() or 320
            popup_h = self.popup.height() or 360
            if x + popup_w > avail.right():
                x = max(avail.left() + 5, avail.right() - popup_w - 5)
            if y + popup_h > avail.bottom():
                top_left = self.button.mapToGlobal(btn_geo.topLeft())
                y = max(avail.top() + 5, top_left.y() - popup_h - 3)

        self.popup.move(x, y)
        self.popup.show()
        self.popup.raise_()
        self.popup.activateWindow()
        QTimer.singleShot(25, self.popup.search_input.setFocus)

    def _on_selection_committed(self, selected: List[str], match_mode: str) -> None:
        self._update_button_display()
        self.filter_changed.emit()

    def _update_button_display(self) -> None:
        selected = self.popup.get_selected()
        prefix = f"{self.icon_text} " if self.icon_text else ""
        count = len(selected)

        if count == 0:
            self.button.setText(f"{prefix}{self.title_text}: All ▾")
            self.button.setStyleSheet("""
                QPushButton {
                    background-color: #1e293b;
                    color: #f8fafc;
                    border: 1px solid #334155;
                    border-radius: 4px;
                    padding: 2px 10px;
                    font-size: 11px;
                    text-align: left;
                }
                QPushButton:hover {
                    background-color: #334155;
                }
            """)
            self.button.setToolTip(f"Filter by {self.title_text} (All)")
        else:
            match_mode = self.popup.get_match_mode()
            mode_lbl = "ALL" if match_mode == "all" else "ANY"

            if count == 1:
                display_val = selected[0]
                if len(display_val) > 16:
                    display_val = display_val[:14] + "…"
                self.button.setText(f"{prefix}{self.title_text}: {display_val} ▾")
            else:
                if self.popup.enable_match_mode:
                    self.button.setText(f"{prefix}{self.title_text} ({count} [{mode_lbl}]) ▾")
                else:
                    self.button.setText(f"{prefix}{self.title_text} ({count}) ▾")

            # Active emerald highlight
            self.button.setStyleSheet("""
                QPushButton {
                    background-color: #064e3b;
                    color: #a7f3d0;
                    border: 1px solid #10b981;
                    border-radius: 4px;
                    padding: 2px 10px;
                    font-size: 11px;
                    font-weight: bold;
                    text-align: left;
                }
                QPushButton:hover {
                    background-color: #047857;
                }
            """)
            items_preview = ", ".join(selected[:5])
            if count > 5:
                items_preview += f" (+{count - 5} more)"
            if self.popup.enable_match_mode:
                self.button.setToolTip(
                    f"{self.title_text} filter (Match: {mode_lbl}):\n{items_preview}"
                )
            else:
                self.button.setToolTip(
                    f"{self.title_text} filter:\n{items_preview}"
                )

    def currentData(self) -> Any:
        """Compatibility helper returning list of selected values or 'all' if empty."""
        sel = self.get_selected()
        return sel if sel else "all"

    def set_items(self, items: Sequence[str]) -> None:
        """Update available choices."""
        self.popup.set_items(items)
        self._update_button_display()

    def get_selected(self) -> List[str]:
        """Return list of currently committed item names."""
        return self.popup.get_selected()

    def get_match_mode(self) -> str:
        """Return 'any' or 'all'."""
        return self.popup.get_match_mode()

    def clear_selection(self) -> None:
        """Uncheck all items and reset to default."""
        self.popup.clear_all()
        self._update_button_display()

    def set_selected(self, items: Sequence[str], match_mode: str = "any") -> None:
        """Set selected items programmatically."""
        self.popup.set_selected(items, match_mode=match_mode)
        self._update_button_display()

    def is_active(self) -> bool:
        """Whether any items are currently selected."""
        return len(self.popup.get_selected()) > 0
