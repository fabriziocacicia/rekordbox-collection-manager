"""Track Play Count Manager Screen."""

from typing import Any, Dict, List, Optional
from PySide6.QtCore import QEvent, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.engine import Engine

from rekordbox_collection_fixer.gui.theme import (
    COLOR_BG_CARD,
    COLOR_BLUE,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_SECONDARY,
)
from rekordbox_collection_fixer.gui.workers import (
    TrackPlayCountWorker,
    TrackSearchWorker,
)
from rekordbox_collection_fixer.utils import is_rekordbox_running


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
    """View to filter, search, and edit track play counts interactively."""

    notify_status = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.engine: Optional[Engine] = None
        self.records: List[Dict[str, Any]] = []
        self.active_search_worker: Optional[TrackSearchWorker] = None

        # Debounce timer for search input
        self.search_debounce_timer = QTimer(self)
        self.search_debounce_timer.setSingleShot(True)
        self.search_debounce_timer.setInterval(250)
        self.search_debounce_timer.timeout.connect(self._execute_search)

        self._init_ui()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
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
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Title", "Artist", "Play Count", "Quick Adjust", "File Location"])
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)

        # Ensure comfortable row height for buttons and readability
        self.table.verticalHeader().setDefaultSectionSize(42)

        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(3, 120)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)

        # Center align headers for Play Count and Quick Adjust columns
        for col in (2, 3):
            header_item = self.table.horizontalHeaderItem(col)
            if header_item:
                header_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        self.table.shortcut_delta.connect(self._on_keyboard_shortcut)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)

        main_layout.addWidget(self.table, stretch=1)

        # 3. Bottom Multi-Selection Action Toolbar
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

        main_layout.addLayout(bottom_bar)

    def set_engine(self, engine: Engine) -> None:
        """Assign active database engine and trigger initial track load."""
        self.engine = engine
        self._execute_search()

    def _on_search_text_changed(self, text: str) -> None:
        self.search_debounce_timer.start()

    def _execute_search(self) -> None:
        """Run search query via background worker."""
        if not self.engine:
            return

        query = self.search_input.text().strip()
        limit_map = [50, 100, 250, 500]
        limit = limit_map[self.limit_combo.currentIndex()]

        if self.active_search_worker and self.active_search_worker.isRunning():
            self.active_search_worker.terminate()

        self.status_label.setText("Searching tracks in database...")
        self.active_search_worker = TrackSearchWorker(self.engine, query, limit=limit, parent=self)
        self.active_search_worker.success.connect(self._on_search_success)
        self.active_search_worker.error.connect(self._on_search_error)
        self.active_search_worker.start()

    def _on_search_success(self, query: str, records: List[Dict[str, Any]]) -> None:
        # Apply filter combo
        filter_mode = self.filter_combo.currentIndex()
        filtered = []
        for r in records:
            count = r.get("play_count", 0)
            if filter_mode == 1 and count <= 0:  # Played only
                continue
            if filter_mode == 2 and count > 0:  # Unplayed only
                continue
            filtered.append(r)

        self.records = filtered
        self._populate_table()
        count = len(filtered)
        if query:
            self.status_label.setText(f"Found {count} tracks matching '{query}'.")
        else:
            self.status_label.setText(f"Loaded {count} tracks from collection.")

    def _on_search_error(self, err_msg: str) -> None:
        self.status_label.setText(f"Search error: {err_msg}")

    def _populate_table(self) -> None:
        """Fill table with current records."""
        self.table.blockSignals(True)
        self.table.setRowCount(0)

        for r in self.records:
            row_idx = self.table.rowCount()
            self.table.insertRow(row_idx)

            track_id = r["id"]

            # Col 0: Title (Read-only)
            title_item = QTableWidgetItem(r.get("title") or "(Untitled)")
            title_item.setData(Qt.ItemDataRole.UserRole, track_id)
            title_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row_idx, 0, title_item)

            # Col 1: Artist (Read-only)
            artist_item = QTableWidgetItem(r.get("artist") or "Unknown Artist")
            artist_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row_idx, 1, artist_item)

            # Col 2: Play Count (Read-only visual info)
            count = int(r.get("play_count", 0))
            count_item = QTableWidgetItem(str(count))
            count_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            count_item.setData(Qt.ItemDataRole.UserRole, track_id)
            count_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row_idx, 2, count_item)

            # Col 3: Quick Stepper Buttons [-] and [+]
            stepper = StepperWidget(track_id, self.table)
            stepper.stepper_clicked.connect(self._on_stepper_clicked)
            self.table.setCellWidget(row_idx, 3, stepper)

            # Col 4: Location (Read-only)
            loc = r.get("filename") or r.get("folder_path") or ""
            loc_item = QTableWidgetItem(loc)
            loc_item.setForeground(Qt.GlobalColor.gray)
            loc_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row_idx, 4, loc_item)

        self.table.blockSignals(False)
        self._on_selection_changed()

    def _on_selection_changed(self) -> None:
        selected_rows = set(index.row() for index in self.table.selectedIndexes())
        count = len(selected_rows)
        self.batch_label.setText(f"Selected: {count}")
        has_selection = count > 0
        self.batch_inc_btn.setEnabled(has_selection)
        self.batch_dec_btn.setEnabled(has_selection)

    def _check_rekordbox_safety(self) -> bool:
        if is_rekordbox_running():
            QMessageBox.warning(
                self,
                "Rekordbox is Running",
                "⚠️ Rekordbox is currently running!\n\n"
                "Please close Rekordbox (Cmd + Q) before editing track play counts.",
            )
            return False
        return True

    def _on_stepper_clicked(self, track_id: str, delta: int) -> None:
        """Triggered when row stepper [-] or [+] button is clicked."""
        if not self._check_rekordbox_safety():
            return
        self._apply_track_delta(track_id, delta)

    def _on_keyboard_shortcut(self, delta: int) -> None:
        """Triggered by + or - keyboard shortcuts on selected rows."""
        if not self._check_rekordbox_safety():
            return
        selected_rows = set(index.row() for index in self.table.selectedIndexes())
        for row in selected_rows:
            track_id = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if track_id:
                self._apply_track_delta(track_id, delta)

    def _apply_track_delta(self, track_id: str, delta: int) -> None:
        """Apply delta to track in database and update cell."""
        if not self.engine:
            return

        # Find row and current record
        record = next((r for r in self.records if r["id"] == track_id), None)
        if not record:
            return

        current_count = record.get("play_count", 0)
        new_count = max(0, current_count + delta)
        record["play_count"] = new_count

        # Update cell in UI immediately
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == track_id:
                count_item = self.table.item(row, 2)
                if count_item:
                    self.table.blockSignals(True)
                    count_item.setText(str(new_count))
                    self.table.blockSignals(False)
                break

        # Fire worker in background
        worker = TrackPlayCountWorker(self.engine, track_id, delta=delta, parent=self)
        worker.success.connect(self._on_play_count_saved)
        worker.error.connect(self._on_play_count_error)
        worker.start()

    def _on_play_count_saved(self, track_id: str, old_count: int, new_count: int) -> None:
        self.notify_status.emit(f"Updated play count: {old_count} ➔ {new_count}")
        self.status_label.setText(f"✓ Saved to database: play count updated ({old_count} ➔ {new_count})")

    def _on_play_count_error(self, track_id: str, err_msg: str) -> None:
        self.notify_status.emit(f"❌ Error updating play count: {err_msg}")
        self.status_label.setText(f"❌ Error saving to database: {err_msg}")
        QMessageBox.warning(self, "Update Error", f"Failed to update play count:\n{err_msg}")

    # Batch Operations
    def _apply_batch_delta(self, delta: int) -> None:
        if not self._check_rekordbox_safety():
            return
        selected_rows = set(index.row() for index in self.table.selectedIndexes())
        for row in selected_rows:
            track_id = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if track_id:
                self._apply_track_delta(track_id, delta)
