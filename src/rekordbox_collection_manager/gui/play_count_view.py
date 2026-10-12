"""Track Play Count Manager Screen."""

import os
from typing import Any, Dict, List, Optional
from PySide6.QtCore import QEvent, QPoint, QTimer, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.engine import Engine

from rekordbox_collection_manager.gui.theme import (
    COLOR_AMBER,
    COLOR_AMBER_BG,
    COLOR_BG_CARD,
    COLOR_BLUE,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_SECONDARY,
)
from rekordbox_collection_manager.gui.track_detail_view import TrackDetailView
from rekordbox_collection_manager.gui.workers import (
    PlayCountBatchSaveWorker,
    TrackSearchWorker,
    stop_worker,
)
from rekordbox_collection_manager.utils import (
    is_rekordbox_running,
    open_in_folder,
    reveal_in_file_manager,
)

COLOR_STAGED_ROW_BG = QColor(245, 158, 11, 30)


class StepperWidget(QWidget):
    """Row widget containing [-] and [+] quick stepper buttons."""

    stepper_clicked = Signal(str, int)  # track_id, delta (-1 or +1)

    def __init__(self, track_id: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.track_id = track_id
        self.setStyleSheet("background: transparent;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.dec_btn = QPushButton("−")
        self.dec_btn.setObjectName("stepperButton")
        self.dec_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dec_btn.setToolTip("Decrement play count (-1)")
        self.dec_btn.clicked.connect(lambda: self.stepper_clicked.emit(self.track_id, -1))
        layout.addWidget(self.dec_btn)

        self.inc_btn = QPushButton("+")
        self.inc_btn.setObjectName("stepperButton")
        self.inc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.inc_btn.setToolTip("Increment play count (+1)")
        self.inc_btn.clicked.connect(lambda: self.stepper_clicked.emit(self.track_id, 1))
        layout.addWidget(self.inc_btn)


class PlayCountTable(QTableWidget):
    """Subclassed table to handle keyboard shortcuts (+ and -)."""

    shortcut_delta = Signal(int)  # delta: -1 or +1

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.text() in ("+", "="):
            self.shortcut_delta.emit(1)
            event.accept()
            return
        elif event.text() in ("-", "_"):
            self.shortcut_delta.emit(-1)
            event.accept()
            return
        super().keyPressEvent(event)


class PlayCountView(QWidget):
    """View to filter, search, and edit track play counts interactively with staged edits."""

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
        self.records: List[Dict[str, Any]] = []
        self.staged_edits: Dict[str, Dict[str, Any]] = {}
        self.active_search_worker: Optional[TrackSearchWorker] = None
        self.active_batch_worker: Optional[PlayCountBatchSaveWorker] = None
        self.current_sort_col: Optional[int] = None
        self.current_sort_order: Qt.SortOrder = Qt.SortOrder.AscendingOrder

        # Debounce timer for search input
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
        # Page 0: Play Count Adjuster List View
        # ----------------------------------------------------------------------
        self.list_widget = QWidget()
        main_layout = QVBoxLayout(self.list_widget)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # 1. Top Search and Filter Controls
        top_bar = QHBoxLayout()
        top_bar.setSpacing(10)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Search track title, artist, album, or filename...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._on_search_text_changed)
        top_bar.addWidget(self.search_input, stretch=3)

        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["All Tracks", "Played Tracks (> 0)", "Unplayed (0)"])
        self.filter_combo.currentIndexChanged.connect(lambda _: self._execute_search())
        top_bar.addWidget(self.filter_combo, stretch=1)

        self.limit_combo = QComboBox()
        self.limit_combo.addItems(["Limit: 50", "Limit: 100", "Limit: 250", "Limit: 500"])
        self.limit_combo.currentIndexChanged.connect(lambda _: self._execute_search())
        top_bar.addWidget(self.limit_combo, stretch=1)

        self.search_btn = QPushButton("Search")
        self.search_btn.clicked.connect(self._execute_search)
        top_bar.addWidget(self.search_btn)

        main_layout.addLayout(top_bar)

        # 2. Main Tracks Table
        self.table = PlayCountTable()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            "Status",
            "Title",
            "Artist",
            "Play Count",
            "Quick Adjust",
            "File Location",
        ])
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)

        # Ensure comfortable row height for buttons and readability
        self.table.verticalHeader().setDefaultSectionSize(42)

        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(4, 120)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)

        # Center align headers for Status, Play Count, and Quick Adjust columns
        for col in (0, 3, 4):
            header_item = self.table.horizontalHeaderItem(col)
            if header_item:
                header_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        # Interactive column header sorting
        horizontal_header = self.table.horizontalHeader()
        horizontal_header.setSectionsClickable(True)
        horizontal_header.setSortIndicatorShown(True)
        horizontal_header.sectionClicked.connect(self._on_header_section_clicked)

        header_tooltips = {
            0: "Click to sort by Status (Staged vs Clean)",
            1: "Click to sort by Title (A-Z / Z-A)",
            2: "Click to sort by Artist (A-Z / Z-A)",
            3: "Click to sort by Play Count (Highest / Lowest)",
            5: "Click to sort by File Location",
        }
        for col, tip in header_tooltips.items():
            header_item = self.table.horizontalHeaderItem(col)
            if header_item:
                header_item.setToolTip(tip)

        self.table.shortcut_delta.connect(self._on_keyboard_shortcut)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context_menu)

        main_layout.addWidget(self.table, stretch=1)

        # 3. Bottom Multi-Selection & Staging Action Toolbar
        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(10)

        self.status_label = QLabel("Ready. Type keywords above to search tracks.")
        self.status_label.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY};")
        bottom_bar.addWidget(self.status_label, stretch=2)

        self.batch_label = QLabel("Selected: 0")
        self.batch_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px;")
        bottom_bar.addWidget(self.batch_label)

        self.batch_inc_btn = QPushButton("➕ +1 to Selected")
        self.batch_inc_btn.setEnabled(False)
        self.batch_inc_btn.clicked.connect(lambda: self._apply_batch_delta(1))
        bottom_bar.addWidget(self.batch_inc_btn)

        self.batch_dec_btn = QPushButton("➖ -1 to Selected")
        self.batch_dec_btn.setEnabled(False)
        self.batch_dec_btn.clicked.connect(lambda: self._apply_batch_delta(-1))
        bottom_bar.addWidget(self.batch_dec_btn)

        self.discard_btn = QPushButton("Discard Changes")
        self.discard_btn.setEnabled(False)
        self.discard_btn.clicked.connect(self._on_discard_changes)
        bottom_bar.addWidget(self.discard_btn)

        self.save_btn = QPushButton("💾 Save Changes")
        self.save_btn.setObjectName("primaryButton")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._on_save_all_changes)
        bottom_bar.addWidget(self.save_btn)

        main_layout.addLayout(bottom_bar)
        self.stack.addWidget(self.list_widget)

        # ----------------------------------------------------------------------
        # Page 1: Track Details View Screen
        # ----------------------------------------------------------------------
        self.track_detail_view = TrackDetailView(parent=self, back_button_text="← Back to Play Counts")
        self.track_detail_view.back_clicked.connect(self._on_back_from_detail)
        self.track_detail_view.open_history_session_requested.connect(self.open_history_session_requested.emit)
        self.track_detail_view.open_playlist_requested.connect(self.open_playlist_requested.emit)
        self.track_detail_view.open_tag_requested.connect(self.open_tag_requested.emit)
        self.track_detail_view.status_message.connect(self.notify_status.emit)
        self.track_detail_view.track_changed.connect(self._on_detail_track_changed)
        self.track_detail_view.replace_file_requested.connect(self.replace_file_requested.emit)
        self.track_detail_view.db_saved.connect(self.db_saved.emit)
        self.stack.addWidget(self.track_detail_view)

    def reset_to_list(self) -> None:
        """Reset view to play count adjuster table list."""
        self.stack.setCurrentIndex(0)

    def set_engine(self, engine: Engine, db_path: Optional[str] = None) -> None:
        """Assign active database engine and trigger initial track load."""
        self.engine = engine
        self.db_path = db_path
        self.staged_edits.clear()
        self._execute_search()

    def _on_search_text_changed(self, text: str) -> None:
        self.search_debounce_timer.start()

    def refresh(self, preserve_scroll: bool = True) -> None:
        """Refresh track play counts from database."""
        if not self.engine:
            return
        if self.staged_edits:
            if not self.confirm_unsaved_changes():
                return
        self._execute_search(preserve_scroll=preserve_scroll)

    def _execute_search(self, preserve_scroll: bool = False) -> None:
        """Run search query via background worker."""
        if not self.engine:
            return

        if preserve_scroll and hasattr(self, "table"):
            self._pending_restore_scroll = self.table.verticalScrollBar().value()
        else:
            self._pending_restore_scroll = None

        query = self.search_input.text().strip()
        limit_map = [50, 100, 250, 500]
        limit = limit_map[self.limit_combo.currentIndex()]

        col_map = {
            1: "title",
            2: "artist",
            3: "play_count",
            5: "location",
        }
        sort_by = col_map.get(self.current_sort_col, "title")
        sort_order = "desc" if self.current_sort_order == Qt.SortOrder.DescendingOrder else "asc"
        stop_worker(self.active_search_worker)

        self.status_label.setText("Searching tracks in database...")
        self.active_search_worker = TrackSearchWorker(
            self.engine,
            query,
            limit=limit,
            sort_by=sort_by,
            sort_order=sort_order,
            parent=self,
        )
        self.active_search_worker.success.connect(self._on_search_success)
        self.active_search_worker.error.connect(self._on_search_error)
        self.active_search_worker.start()

    def _on_search_success(self, query: str, records: List[Dict[str, Any]]) -> None:
        # Apply filter combo
        filter_mode = self.filter_combo.currentIndex()
        filtered = []
        for r in records:
            track_id = str(r["id"])
            count = (
                self.staged_edits[track_id]["new_count"]
                if track_id in self.staged_edits
                else int(r.get("play_count", 0))
            )
            if filter_mode == 1 and count <= 0:  # Played only
                continue
            if filter_mode == 2 and count > 0:  # Unplayed only
                continue
            filtered.append(r)

        self.records = filtered
        if self.current_sort_col is not None:
            reverse = (self.current_sort_order == Qt.SortOrder.DescendingOrder)
            self.records.sort(key=self._record_sort_key, reverse=reverse)
        self._populate_table()
        self._update_status_bar()

        restore_scroll = getattr(self, "_pending_restore_scroll", None)
        if restore_scroll is not None and hasattr(self, "table"):
            self.table.verticalScrollBar().setValue(restore_scroll)
            self._pending_restore_scroll = None

    def _on_search_error(self, err_msg: str) -> None:
        self.status_label.setText(f"Search error: {err_msg}")

    def _populate_table(self) -> None:
        """Fill table with current records."""
        self.table.blockSignals(True)
        self.table.setRowCount(0)

        for r in self.records:
            row_idx = self.table.rowCount()
            self.table.insertRow(row_idx)

            track_id = str(r["id"])
            orig_count = int(r.get("play_count", 0))

            is_staged = track_id in self.staged_edits
            cur_count = self.staged_edits[track_id]["new_count"] if is_staged else orig_count

            # Col 0: Status
            status_item = QTableWidgetItem("● Staged" if is_staged else "Clean")
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            status_item.setData(Qt.ItemDataRole.UserRole, track_id)
            status_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row_idx, 0, status_item)

            # Col 1: Title
            title_item = QTableWidgetItem(r.get("title") or "(Untitled)")
            title_item.setData(Qt.ItemDataRole.UserRole, track_id)
            title_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row_idx, 1, title_item)

            # Col 2: Artist
            artist_item = QTableWidgetItem(r.get("artist") or "Unknown Artist")
            artist_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row_idx, 2, artist_item)

            # Col 3: Play Count
            count_item = QTableWidgetItem(str(cur_count))
            count_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            count_item.setData(Qt.ItemDataRole.UserRole, track_id)
            count_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row_idx, 3, count_item)

            # Col 4: Quick Stepper Buttons [-] and [+]
            stepper = StepperWidget(track_id, self.table)
            stepper.stepper_clicked.connect(self._on_stepper_clicked)
            self.table.setCellWidget(row_idx, 4, stepper)

            # Col 5: Location
            loc = r.get("filename") or r.get("folder_path") or ""
            loc_item = QTableWidgetItem(loc)
            loc_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row_idx, 5, loc_item)

            self._apply_row_styling(row_idx, track_id, is_staged, orig_count, cur_count)

        self.table.blockSignals(False)
        if self.current_sort_col is not None:
            self.table.horizontalHeader().setSortIndicator(
                self.current_sort_col, self.current_sort_order
            )
        self._on_selection_changed()

    def _apply_row_styling(
        self,
        row_idx: int,
        track_id: str,
        is_staged: bool,
        orig_count: int,
        cur_count: int,
    ) -> None:
        """Apply visual styling and highlights to a table row."""
        status_item = self.table.item(row_idx, 0)
        title_item = self.table.item(row_idx, 1)
        artist_item = self.table.item(row_idx, 2)
        count_item = self.table.item(row_idx, 3)
        loc_item = self.table.item(row_idx, 5)
        stepper = self.table.cellWidget(row_idx, 4)

        if is_staged:
            bg_brush = QBrush(COLOR_STAGED_ROW_BG)
            if status_item:
                status_item.setText("● Staged")
                status_item.setForeground(QColor(COLOR_AMBER))
                status_item.setToolTip(f"Staged modification: {orig_count} ➔ {cur_count}")
            if count_item:
                count_item.setText(str(cur_count))
                count_item.setForeground(QColor(COLOR_AMBER))
                count_item.setToolTip(f"Original: {orig_count} ➔ Staged: {cur_count}")
            if loc_item:
                loc_item.setForeground(QColor(COLOR_TEXT_MUTED))
            if stepper:
                stepper.setStyleSheet("background: rgba(245, 158, 11, 0.12); border-radius: 4px;")
        else:
            bg_brush = QBrush(Qt.GlobalColor.transparent)
            if status_item:
                status_item.setText("Clean")
                status_item.setForeground(QColor(Qt.GlobalColor.gray))
                status_item.setToolTip("No unsaved changes")
            if count_item:
                count_item.setText(str(cur_count))
                count_item.setForeground(QColor(COLOR_TEXT_PRIMARY))
                count_item.setToolTip("")
            if loc_item:
                loc_item.setForeground(QColor(Qt.GlobalColor.gray))
            if stepper:
                stepper.setStyleSheet("background: transparent;")

        for item in (status_item, title_item, artist_item, count_item, loc_item):
            if item:
                item.setBackground(bg_brush)

    def _find_row_by_track_id(self, track_id: str) -> Optional[int]:
        """Locate table row index matching track_id."""
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == track_id:
                return r
        return None

    def _update_status_bar(self) -> None:
        """Refresh bottom status label and save/discard buttons."""
        staged_count = len(self.staged_edits)
        if staged_count > 0:
            self.status_label.setText(
                f"⚠️ {staged_count} track{'s' if staged_count != 1 else ''} modified (unsaved) — click Save Changes to commit."
            )
            self.status_label.setStyleSheet(f"color: {COLOR_AMBER}; font-weight: 600;")
            self.save_btn.setText(f"💾 Save Changes ({staged_count})")
            self.discard_btn.setText(f"Discard Changes ({staged_count})")
            self.save_btn.setEnabled(True)
            self.discard_btn.setEnabled(True)
        else:
            visible_count = len(self.records)
            query = self.search_input.text().strip()
            if query:
                self.status_label.setText(f"Found {visible_count} tracks matching '{query}'.")
            else:
                self.status_label.setText(f"Loaded {visible_count} tracks from collection.")
            self.status_label.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY};")
            self.save_btn.setText("💾 Save Changes")
            self.discard_btn.setText("Discard Changes")
            self.save_btn.setEnabled(False)
            self.discard_btn.setEnabled(False)

    def _on_selection_changed(self) -> None:
        selected_rows = set(index.row() for index in self.table.selectedIndexes())
        count = len(selected_rows)
        self.batch_label.setText(f"Selected: {count}")
        has_selection = count > 0
        self.batch_inc_btn.setEnabled(has_selection)
        self.batch_dec_btn.setEnabled(has_selection)

    def _on_stepper_clicked(self, track_id: str, delta: int) -> None:
        """Triggered when row stepper [-] or [+] button is clicked."""
        self._apply_track_delta(track_id, delta)

    def _on_keyboard_shortcut(self, delta: int) -> None:
        """Triggered by + or - keyboard shortcuts on selected rows."""
        selected_rows = set(index.row() for index in self.table.selectedIndexes())
        for row in selected_rows:
            item = self.table.item(row, 0)
            if item:
                track_id = item.data(Qt.ItemDataRole.UserRole)
                if track_id:
                    self._apply_track_delta(track_id, delta)

    def _apply_track_delta(self, track_id: str, delta: int) -> None:
        """Stage a play count delta for track in memory."""
        if track_id in self.staged_edits:
            staged = self.staged_edits[track_id]
            record = staged["record"]
            orig_count = staged["original_count"]
            cur_count = staged["new_count"]
        else:
            record = next((r for r in self.records if str(r["id"]) == track_id), None)
            if not record:
                return
            orig_count = int(record.get("play_count", 0))
            cur_count = orig_count

        new_count = max(0, cur_count + delta)

        if new_count == orig_count:
            self.staged_edits.pop(track_id, None)
            is_staged = False
        else:
            self.staged_edits[track_id] = {
                "record": record,
                "original_count": orig_count,
                "new_count": new_count,
            }
            is_staged = True

        row_idx = self._find_row_by_track_id(track_id)
        if row_idx is not None:
            self.table.blockSignals(True)
            self._apply_row_styling(row_idx, track_id, is_staged, orig_count, new_count)
            self.table.blockSignals(False)

        self._update_status_bar()

    def _apply_batch_delta(self, delta: int) -> None:
        """Stage deltas to all currently selected tracks."""
        selected_rows = set(index.row() for index in self.table.selectedIndexes())
        for row in selected_rows:
            item = self.table.item(row, 0)
            if item:
                track_id = item.data(Qt.ItemDataRole.UserRole)
                if track_id:
                    self._apply_track_delta(track_id, delta)

    def _on_discard_changes(self) -> None:
        """Discard all staged edits and restore original values."""
        staged_count = len(self.staged_edits)
        if staged_count == 0:
            return

        res = QMessageBox.question(
            self,
            "Discard Changes",
            f"Are you sure you want to discard {staged_count} staged play count modification{'s' if staged_count != 1 else ''}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if res != QMessageBox.StandardButton.Yes:
            return

        self.staged_edits.clear()
        if self.current_sort_col is not None:
            reverse = (self.current_sort_order == Qt.SortOrder.DescendingOrder)
            self.records.sort(key=self._record_sort_key, reverse=reverse)
        self._populate_table()
        self._update_status_bar()
        self.notify_status.emit("Staged play count changes discarded.")

    def _on_save_all_changes(self) -> None:
        """Save all staged play count modifications in a single atomic transaction."""
        if not self.engine or not self.staged_edits:
            return

        if is_rekordbox_running():
            QMessageBox.warning(
                self,
                "Rekordbox is Running",
                "⚠️ Rekordbox is currently running!\n\n"
                "Saving changes now may cause database locking or corruption.\n"
                "Please close Rekordbox (Cmd + Q) and try again.",
                QMessageBox.StandardButton.Ok,
            )
            return

        count = len(self.staged_edits)
        res = QMessageBox.question(
            self,
            "Confirm Save",
            f"Are you ready to commit {count} track play count update{'s' if count != 1 else ''} to Rekordbox?\n\n"
            "This will update the database in a single atomic transaction.",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
        )
        if res != QMessageBox.StandardButton.Save:
            return

        updates = [
            {"id": item["record"]["id"], "play_count": item["new_count"]}
            for item in self.staged_edits.values()
        ]

        self.save_btn.setEnabled(False)
        self.discard_btn.setEnabled(False)
        self.notify_status.emit(f"Saving {count} play count updates to database...")
        self.status_label.setText(f"Saving {count} updates to database...")

        worker = PlayCountBatchSaveWorker(self.engine, updates, parent=self)
        self.active_batch_worker = worker
        worker.success.connect(self._on_save_success)
        worker.error.connect(self._on_save_error)
        worker.start()

    def _on_save_success(self, updated_count: int) -> None:
        for track_id, item in self.staged_edits.items():
            new_count = item["new_count"]
            item["record"]["play_count"] = new_count
            for r in self.records:
                if str(r["id"]) == track_id:
                    r["play_count"] = new_count

        self.staged_edits.clear()
        if self.current_sort_col is not None:
            reverse = (self.current_sort_order == Qt.SortOrder.DescendingOrder)
            self.records.sort(key=self._record_sort_key, reverse=reverse)
        self._populate_table()
        self._update_status_bar()
        self.db_saved.emit()
        self.notify_status.emit(f"✅ Successfully saved play count updates for {updated_count} tracks.")
        QMessageBox.information(
            self,
            "Changes Saved",
            f"Successfully updated play counts for {updated_count} tracks in Rekordbox.",
        )

    def _on_save_error(self, err_msg: str) -> None:
        self._update_status_bar()
        self.notify_status.emit(f"❌ Save failed: {err_msg}")
        QMessageBox.critical(self, "Save Failed", f"Database error while saving changes:\n{err_msg}")

    def _record_sort_key(self, r: Dict[str, Any]) -> Any:
        track_id = str(r["id"])
        is_staged = track_id in self.staged_edits

        if self.current_sort_col == 0:  # Status: staged first vs clean
            return (1 if is_staged else 0, (r.get("title") or "").lower())
        elif self.current_sort_col == 1:  # Title (alphabetical)
            return ((r.get("title") or "").lower(), (r.get("artist") or "").lower())
        elif self.current_sort_col == 2:  # Artist (alphabetical)
            return ((r.get("artist") or "").lower(), (r.get("title") or "").lower())
        elif self.current_sort_col == 3:  # Play Count (numerical)
            count = (
                self.staged_edits[track_id]["new_count"]
                if is_staged
                else int(r.get("play_count", 0))
            )
            return (count, (r.get("title") or "").lower())
        elif self.current_sort_col == 5:  # Location
            return (r.get("filename") or r.get("folder_path") or "").lower()
        return 0

    def _on_header_section_clicked(self, logical_index: int) -> None:
        """Handle header column click to sort table records."""
        if logical_index == 4:  # Quick Adjust column - no sort
            return

        if self.current_sort_col == logical_index:
            self.current_sort_order = (
                Qt.SortOrder.DescendingOrder
                if self.current_sort_order == Qt.SortOrder.AscendingOrder
                else Qt.SortOrder.AscendingOrder
            )
        else:
            self.current_sort_col = logical_index
            # Default to Descending for Play Count (highest first) and Status (staged first)
            if logical_index in (0, 3):
                self.current_sort_order = Qt.SortOrder.DescendingOrder
            else:
                self.current_sort_order = Qt.SortOrder.AscendingOrder

        self.table.horizontalHeader().setSortIndicator(
            self.current_sort_col, self.current_sort_order
        )

        if self.engine and logical_index in (1, 2, 3, 5):
            # Database-backed columns: re-query database to fetch top/bottom across entire collection
            self._execute_search()
        else:
            self._sort_and_refresh()

    def _sort_and_refresh(self) -> None:
        """Sort records according to active sort column and order, then refresh table."""
        if self.current_sort_col is None:
            return

        reverse = (self.current_sort_order == Qt.SortOrder.DescendingOrder)
        self.records.sort(key=self._record_sort_key, reverse=reverse)
        self._populate_table()

    def _on_cell_double_clicked(self, row: int, col: int) -> None:
        """Double clicking non-editable columns opens TrackDetailDialog."""
        if col != 3 and 0 <= row < len(self.records):
            self._open_track_detail(self.records[row])

    def _open_track_detail(self, track: Dict[str, Any]) -> None:
        """Switch to in-place TrackDetailView for track."""
        if not self.engine or not track:
            return

        if self.staged_edits:
            if not self.confirm_unsaved_changes():
                return

        track_id = track.get("id") or track.get("content_id")
        curr_idx = 0
        for idx, r in enumerate(self.records):
            if (r.get("id") or r.get("content_id")) == track_id:
                curr_idx = idx
                break

        self.track_detail_view.load_track(
            engine=self.engine,
            track_data=track,
            db_path=self.db_path,
            tracks_list=self.records,
            current_index=curr_idx,
            back_button_text="← Back to Play Counts",
        )
        self.stack.setCurrentIndex(1)

    def _on_back_from_detail(self) -> None:
        """Return to play count editor table view."""
        self.stack.setCurrentIndex(0)

    def confirm_unsaved_changes(self) -> bool:
        """Prompt user if there are unsaved play count modifications or detail modifications."""
        if self.stack.currentIndex() == 1:
            return self.track_detail_view._confirm_unsaved_changes()
        if self.staged_edits:
            reply = QMessageBox.question(
                self,
                "Unsaved Modifications",
                "You have unsaved play count modifications. Do you want to save them before leaving?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
            )
            if reply == QMessageBox.StandardButton.Save:
                self._on_save_clicked()
                return len(self.staged_edits) == 0
            elif reply == QMessageBox.StandardButton.Discard:
                self._on_discard_clicked()
                return True
            else:
                return False
        return True

    def _on_detail_track_changed(self, idx: int, track: Dict[str, Any]) -> None:
        """Select and scroll to track in table when detail view navigates."""
        if 0 <= idx < self.table.rowCount():
            self.table.selectRow(idx)
            self.table.scrollToItem(self.table.item(idx, 0))

    def _on_table_context_menu(self, pos: QPoint) -> None:
        item = self.table.itemAt(pos)
        if not item:
            return
        row = item.row()
        if row < 0 or row >= len(self.records):
            return
        track = self.records[row]
        menu = QMenu(self)
        act_details = menu.addAction("ℹ️ View Track Details...")
        menu.addSeparator()
        act_copy_track = menu.addAction("📋 Copy Track (Artist - Title)")
        act_open_folder = menu.addAction("📁 Open Containing Folder")
        act_reveal_file = menu.addAction("🔍 Reveal in Finder")

        action = menu.exec(self.table.viewport().mapToGlobal(pos))
        if action == act_details:
            self._open_track_detail(track)
        elif action == act_copy_track:
            artist = track.get("artist") or "Unknown Artist"
            title = track.get("title") or ""
            cb = QApplication.clipboard()
            if cb:
                cb.setText(f"{artist} - {title}")
                self.notify_status.emit(f"📋 Copied '{artist} - {title}' to clipboard!")
        elif action == act_open_folder:
            folder = track.get("folder_dir") or track.get("folder_path") or ""
            if folder and open_in_folder(folder):
                self.notify_status.emit(f"📂 Opened folder: {os.path.basename(folder) or folder}")
            else:
                self.notify_status.emit(f"❌ Folder not found: {folder}")
        elif action == act_reveal_file:
            fp = track.get("file_path") or track.get("filename") or ""
            if fp and reveal_in_file_manager(fp):
                self.notify_status.emit(f"🔍 Revealed in Finder: {os.path.basename(fp)}")
            else:
                self.notify_status.emit(f"❌ File not found: {fp}")

    def cleanup(self) -> None:
        """Stop background play count workers and timers cleanly."""
        if hasattr(self, "search_debounce_timer"):
            self.search_debounce_timer.stop()
        for attr in ("active_search_worker", "active_batch_worker"):
            w = getattr(self, attr, None)
            if w:
                stop_worker(w, wait_ms=100)
                setattr(self, attr, None)
        if hasattr(self, "track_detail_view") and hasattr(self.track_detail_view, "cleanup"):
            self.track_detail_view.cleanup()


