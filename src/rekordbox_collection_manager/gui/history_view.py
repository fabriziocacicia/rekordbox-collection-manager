"""History Session Rename & Realign Screen."""

import re
from typing import Any, Dict, List, Optional
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.engine import Engine

from rekordbox_collection_manager.gui.history_detail_view import HistoryDetailView
from rekordbox_collection_manager.gui.theme import (
    COLOR_AMBER,
    COLOR_AMBER_BG,
    COLOR_AMBER_BORDER,
    COLOR_BLUE,
    COLOR_GREEN,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_SECONDARY,
)
from rekordbox_collection_manager.gui.workers import (
    HistoryBatchSaveWorker,
    HistoryRealignWorker,
)
from rekordbox_collection_manager.utils import (
    MONTH_INT_TO_NAME,
    compose_history_name,
    extract_year_month_from_name_or_date,
    is_rekordbox_running,
    parse_history_name,
)


class HistoryView(QWidget):
    """View to batch edit and realign Rekordbox history sessions."""

    notify_status = Signal(str)
    request_reload = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.engine: Optional[Engine] = None
        self.original_records: List[Dict[str, Any]] = []
        # Key: record id, Value: {'record': dict, 'name': str, 'date': str}
        self.staged_edits: Dict[str, Dict[str, Any]] = {}
        self.active_folder_filter: Optional[str] = None  # None for All, or 'YYYYMM' or 'YYYY'
        self.search_filter_text: str = ""
        self.active_session_ids_in_table: List[str] = []
        self.current_detail_id: Optional[str] = None

        self._init_ui()

    def _init_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.stack = QStackedWidget(self)
        root_layout.addWidget(self.stack)

        # ----------------------------------------------------------------------
        # Page 0: Sessions List & Batch Edit Screen
        # ----------------------------------------------------------------------
        self.sessions_widget = QWidget()
        sessions_layout = QVBoxLayout(self.sessions_widget)
        sessions_layout.setContentsMargins(16, 16, 16, 16)
        sessions_layout.setSpacing(12)

        # 1. Top Action Toolbar
        top_bar = QHBoxLayout()
        top_bar.setSpacing(10)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Filter sessions by title or date...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._on_search_text_changed)
        top_bar.addWidget(self.search_input, stretch=2)

        self.realign_btn = QPushButton("⚡ Realign Chronology")
        self.realign_btn.setToolTip("Recalculate Seq order in database (Months 1-12, Days 01-31)")
        self.realign_btn.clicked.connect(self._on_realign_chronology)
        top_bar.addWidget(self.realign_btn)

        sessions_layout.addLayout(top_bar)

        # 2. Main Content Splitter (Left: Folder Tree, Right: Session Table)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(4)

        # Left: Folder Tree Widget
        tree_container = QWidget()
        tree_layout = QVBoxLayout(tree_container)
        tree_layout.setContentsMargins(0, 0, 0, 0)
        tree_layout.setSpacing(6)

        tree_header = QLabel("HISTORY FOLDERS")
        tree_header.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 600; padding: 4px;")
        tree_layout.addWidget(tree_header)

        self.folder_tree = QTreeWidget()
        self.folder_tree.setHeaderHidden(True)
        self.folder_tree.itemClicked.connect(self._on_folder_item_clicked)
        tree_layout.addWidget(self.folder_tree)

        splitter.addWidget(tree_container)

        # Right: Sessions Table
        table_container = QWidget()
        table_layout = QVBoxLayout(table_container)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.setSpacing(6)

        table_header = QLabel("HISTORY SESSIONS (Double-click date, name, location or played with to edit • Click Details to view tracks)")
        table_header.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 600; padding: 4px;")
        table_layout.addWidget(table_header)

        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            "Status",
            "Date",
            "Session Name",
            "Location",
            "Played With",
            "Created At",
            "Actions",
        ])
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(3, 150)
        self.table.setColumnWidth(4, 180)
        self.table.setColumnWidth(6, 110)

        actions_hdr = self.table.horizontalHeaderItem(6)
        if actions_hdr:
            actions_hdr.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        self.table.cellChanged.connect(self._on_cell_changed)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        table_layout.addWidget(self.table)

        splitter.addWidget(table_container)
        splitter.setSizes([220, 780])

        sessions_layout.addWidget(splitter, stretch=1)

        # 3. Bottom Action Bar
        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(12)

        self.status_label = QLabel("No database loaded.")
        self.status_label.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-weight: 500;")
        bottom_bar.addWidget(self.status_label, stretch=1)

        self.discard_btn = QPushButton("Discard Changes")
        self.discard_btn.setEnabled(False)
        self.discard_btn.clicked.connect(self._on_discard_changes)
        bottom_bar.addWidget(self.discard_btn)

        self.save_btn = QPushButton("💾 Save All Changes")
        self.save_btn.setObjectName("primaryButton")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._on_save_all_changes)
        bottom_bar.addWidget(self.save_btn)

        sessions_layout.addLayout(bottom_bar)
        self.stack.addWidget(self.sessions_widget)

        # ----------------------------------------------------------------------
        # Page 1: History Session Detail View Screen
        # ----------------------------------------------------------------------
        self.detail_view = HistoryDetailView(self)
        self.detail_view.back_clicked.connect(self._on_back_from_detail)
        self.detail_view.prev_session_requested.connect(self._on_prev_session)
        self.detail_view.next_session_requested.connect(self._on_next_session)
        self.detail_view.notify_status.connect(self.notify_status.emit)
        self.stack.addWidget(self.detail_view)

    def set_data(self, engine: Engine, records: List[Dict[str, Any]]) -> None:
        """Populate the view with records loaded from database."""
        self.engine = engine
        self.original_records = records
        self.staged_edits.clear()
        self._update_folder_tree()
        self._refresh_table()
        self._update_status_bar()

        if self.current_detail_id:
            matching = next((r for r in records if r["id"] == self.current_detail_id), None)
            if matching:
                self._open_session_detail(self.current_detail_id)
            else:
                self._on_back_from_detail()

    def _update_folder_tree(self) -> None:
        """Rebuild Year/Month folder tree from current records."""
        self.folder_tree.clear()

        # Root Item: All Sessions
        all_item = QTreeWidgetItem(["All Sessions"])
        all_item.setData(0, Qt.ItemDataRole.UserRole, None)
        self.folder_tree.addTopLevelItem(all_item)

        # Group records by Year and Month
        year_months: Dict[str, Dict[str, int]] = {}
        for r in self.original_records:
            name = r.get("name") or ""
            date = r.get("date") or ""
            y, m = extract_year_month_from_name_or_date(name, date)
            if y and m:
                y_str = str(y)
                m_str = f"{y}{m:02d}"
                if y_str not in year_months:
                    year_months[y_str] = {}
                year_months[y_str][m_str] = year_months[y_str].get(m_str, 0) + 1
            else:
                if "Other" not in year_months:
                    year_months["Other"] = {}
                year_months["Other"]["Other"] = year_months["Other"].get("Other", 0) + 1

        for year in sorted(year_months.keys(), reverse=True):
            year_count = sum(year_months[year].values())
            year_item = QTreeWidgetItem([f"📁 {year} ({year_count})"])
            year_item.setData(0, Qt.ItemDataRole.UserRole, year)
            self.folder_tree.addTopLevelItem(year_item)

            for m_key in sorted(year_months[year].keys(), reverse=True):
                count = year_months[year][m_key]
                if m_key != "Other" and len(m_key) == 6:
                    m_num = int(m_key[4:])
                    m_name = MONTH_INT_TO_NAME.get(m_num, f"Month {m_num}")
                    label = f"  └ 📅 {m_num:02d} {m_name} ({count})"
                else:
                    label = f"  └ 📄 Uncategorized ({count})"
                m_item = QTreeWidgetItem([label])
                m_item.setData(0, Qt.ItemDataRole.UserRole, m_key)
                year_item.addChild(m_item)

            year_item.setExpanded(True)

        all_item.setSelected(True)

    def _on_folder_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """Filter table when folder tree item is clicked."""
        folder_key = item.data(0, Qt.ItemDataRole.UserRole)
        self.active_folder_filter = folder_key
        self._refresh_table()

    def _on_search_text_changed(self, text: str) -> None:
        self.search_filter_text = text.strip().lower()
        self._refresh_table()

    def _refresh_table(self) -> None:
        """Render session records in the table according to active filters."""
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self.active_session_ids_in_table.clear()

        for r in self.original_records:
            rec_id = r["id"]
            orig_name = r.get("name") or ""
            orig_date = r.get("date") or ""

            # Check if staged
            is_staged = rec_id in self.staged_edits
            cur_name = self.staged_edits[rec_id]["name"] if is_staged else orig_name
            cur_date = self.staged_edits[rec_id]["date"] if is_staged else orig_date

            orig_base, orig_loc, orig_art = parse_history_name(orig_name)
            cur_base, cur_loc, cur_art = parse_history_name(cur_name)

            # 1. Folder Tree Filter
            y, m = extract_year_month_from_name_or_date(cur_name, cur_date)
            folder_key = f"{y}{m:02d}" if (y and m) else "Other"
            year_key = str(y) if y else "Other"

            if self.active_folder_filter:
                if len(self.active_folder_filter) == 4 and self.active_folder_filter != year_key:
                    continue
                elif len(self.active_folder_filter) == 6 and self.active_folder_filter != folder_key:
                    continue

            # 2. Text Search Filter
            if self.search_filter_text:
                q = self.search_filter_text
                if (
                    q not in cur_name.lower()
                    and q not in cur_date.lower()
                    and q not in cur_base.lower()
                    and q not in cur_loc.lower()
                    and q not in cur_art.lower()
                ):
                    continue

            # Insert Row
            row_idx = self.table.rowCount()
            self.table.insertRow(row_idx)
            self.active_session_ids_in_table.append(rec_id)

            # Col 0: Status badge
            status_item = QTableWidgetItem()
            if is_staged:
                status_item.setText("● Staged")
                status_item.setForeground(Qt.GlobalColor.yellow)
            else:
                status_item.setText("Clean")
                status_item.setForeground(Qt.GlobalColor.gray)
            status_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row_idx, 0, status_item)

            # Col 1: Date (Editable)
            date_item = QTableWidgetItem(cur_date)
            date_item.setData(Qt.ItemDataRole.UserRole, rec_id)
            if is_staged and cur_date != orig_date:
                date_item.setForeground(Qt.GlobalColor.yellow)
            self.table.setItem(row_idx, 1, date_item)

            # Col 2: Session Name (Editable)
            name_item = QTableWidgetItem(cur_base)
            name_item.setData(Qt.ItemDataRole.UserRole, rec_id)
            if is_staged and cur_base != orig_base:
                name_item.setForeground(Qt.GlobalColor.yellow)
            self.table.setItem(row_idx, 2, name_item)

            # Col 3: Location (Editable)
            loc_item = QTableWidgetItem(cur_loc)
            loc_item.setData(Qt.ItemDataRole.UserRole, rec_id)
            if is_staged and cur_loc != orig_loc:
                loc_item.setForeground(Qt.GlobalColor.yellow)
            self.table.setItem(row_idx, 3, loc_item)

            # Col 4: Played With (Editable)
            art_item = QTableWidgetItem(cur_art)
            art_item.setData(Qt.ItemDataRole.UserRole, rec_id)
            if is_staged and cur_art != orig_art:
                art_item.setForeground(Qt.GlobalColor.yellow)
            self.table.setItem(row_idx, 4, art_item)

            # Col 5: Original Created At
            orig_created = orig_date or "(None)"
            created_item = QTableWidgetItem(orig_created)
            created_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row_idx, 5, created_item)

            # Col 6: Actions (View Details)
            action_widget = QWidget()
            action_widget.setStyleSheet("background: transparent;")
            action_layout = QHBoxLayout(action_widget)
            action_layout.setContentsMargins(0, 0, 0, 0)
            action_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

            detail_btn = QPushButton("🔍 Details")
            detail_btn.setObjectName("detailActionBtn")
            detail_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            detail_btn.setToolTip("View session details and full tracklist")
            detail_btn.clicked.connect(lambda _, rid=rec_id: self._open_session_detail(rid))
            action_layout.addWidget(detail_btn)

            self.table.setCellWidget(row_idx, 6, action_widget)

        self.table.blockSignals(False)
        self._update_status_bar()

    def _on_cell_double_clicked(self, row: int, column: int) -> None:
        """Double clicking non-editable columns (0, 5, 6) opens detail view."""
        if column in (1, 2, 3, 4):
            return
        item = self.table.item(row, 1) or self.table.item(row, 2)
        if item:
            rec_id = item.data(Qt.ItemDataRole.UserRole)
            if rec_id:
                self._open_session_detail(rec_id)

    def _open_session_detail(self, rec_id: str) -> None:
        """Open the HistoryDetailView for a given session ID."""
        if not self.engine:
            return
        self.current_detail_id = rec_id
        record = next((r for r in self.original_records if r["id"] == rec_id), None)
        if not record:
            return

        is_staged = rec_id in self.staged_edits
        name = self.staged_edits[rec_id]["name"] if is_staged else record.get("name", "")
        date = self.staged_edits[rec_id]["date"] if is_staged else record.get("date", "")

        has_prev = False
        has_next = False
        if rec_id in self.active_session_ids_in_table:
            idx = self.active_session_ids_in_table.index(rec_id)
            has_prev = idx > 0
            has_next = idx < len(self.active_session_ids_in_table) - 1

        self.detail_view.load_session(
            self.engine,
            rec_id,
            session_title=name,
            session_date=date,
            has_prev=has_prev,
            has_next=has_next,
        )
        self.stack.setCurrentIndex(1)

    def _on_back_from_detail(self) -> None:
        """Return from detail view to main sessions list."""
        self.stack.setCurrentIndex(0)
        self.current_detail_id = None

    def _on_prev_session(self) -> None:
        """Navigate to previous session in current list."""
        if not self.current_detail_id or self.current_detail_id not in self.active_session_ids_in_table:
            return
        idx = self.active_session_ids_in_table.index(self.current_detail_id)
        if idx > 0:
            prev_id = self.active_session_ids_in_table[idx - 1]
            self._open_session_detail(prev_id)

    def _on_next_session(self) -> None:
        """Navigate to next session in current list."""
        if not self.current_detail_id or self.current_detail_id not in self.active_session_ids_in_table:
            return
        idx = self.active_session_ids_in_table.index(self.current_detail_id)
        if idx < len(self.active_session_ids_in_table) - 1:
            next_id = self.active_session_ids_in_table[idx + 1]
            self._open_session_detail(next_id)

    def _on_cell_changed(self, row: int, column: int) -> None:
        """Handle inline editing of Date (1), Session Name (2), Location (3), or Played With (4)."""
        if column not in (1, 2, 3, 4):
            return

        item = self.table.item(row, column)
        if not item:
            return

        rec_id = item.data(Qt.ItemDataRole.UserRole)
        if not rec_id:
            for c in (1, 2, 3, 4):
                cand = self.table.item(row, c)
                if cand and cand.data(Qt.ItemDataRole.UserRole):
                    rec_id = cand.data(Qt.ItemDataRole.UserRole)
                    break
            if not rec_id:
                return

        # Find original record
        record = next((r for r in self.original_records if r["id"] == rec_id), None)
        if not record:
            return

        date_item = self.table.item(row, 1)
        name_item = self.table.item(row, 2)
        loc_item = self.table.item(row, 3)
        art_item = self.table.item(row, 4)

        new_date = date_item.text().strip() if date_item else ""
        new_base = name_item.text().strip() if name_item else ""
        new_loc = loc_item.text().strip() if loc_item else ""
        new_art = art_item.text().strip() if art_item else ""

        # Compose full name according to schema
        new_name = compose_history_name(new_base, location=new_loc, artist=new_art)

        orig_date = record.get("date") or ""
        orig_name = record.get("name") or ""
        orig_base, orig_loc, orig_art = parse_history_name(orig_name)

        # Check if modified from original
        if new_date != orig_date or new_name != orig_name:
            self.staged_edits[rec_id] = {
                "record": record,
                "name": new_name,
                "date": new_date,
            }
        else:
            self.staged_edits.pop(rec_id, None)

        self.table.blockSignals(True)
        # Highlight modified fields in yellow
        is_staged = rec_id in self.staged_edits
        if date_item:
            date_item.setForeground(Qt.GlobalColor.yellow if (is_staged and new_date != orig_date) else Qt.GlobalColor.white)
        if name_item:
            name_item.setForeground(Qt.GlobalColor.yellow if (is_staged and new_base != orig_base) else Qt.GlobalColor.white)
        if loc_item:
            loc_item.setForeground(Qt.GlobalColor.yellow if (is_staged and new_loc != orig_loc) else Qt.GlobalColor.white)
        if art_item:
            art_item.setForeground(Qt.GlobalColor.yellow if (is_staged and new_art != orig_art) else Qt.GlobalColor.white)

        # Update status cell
        status_item = self.table.item(row, 0)
        if status_item:
            if is_staged:
                status_item.setText("● Staged")
                status_item.setForeground(Qt.GlobalColor.yellow)
            else:
                status_item.setText("Clean")
                status_item.setForeground(Qt.GlobalColor.gray)
        self.table.blockSignals(False)

        self._update_status_bar()

    def _on_discard_changes(self) -> None:
        """Discard all staged edits."""
        if not self.staged_edits:
            return
        res = QMessageBox.question(
            self,
            "Discard Changes",
            f"Are you sure you want to discard {len(self.staged_edits)} staged modifications?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if res == QMessageBox.StandardButton.Yes:
            self.staged_edits.clear()
            self._refresh_table()
            self.notify_status.emit("Staged changes discarded.")

    def _on_realign_chronology(self) -> None:
        """Trigger database realignment."""
        if not self.engine:
            return

        if is_rekordbox_running():
            QMessageBox.warning(
                self,
                "Rekordbox is Running",
                "Please close Rekordbox (Cmd + Q) before realigning history ordering.",
            )
            return

        self.realign_btn.setEnabled(False)
        self.notify_status.emit("Realigning history order in database...")

        worker = HistoryRealignWorker(self.engine, self)
        worker.success.connect(self._on_realign_success)
        worker.error.connect(self._on_realign_error)
        worker.start()

    def _on_realign_success(self, stats: Dict[str, Any]) -> None:
        self.realign_btn.setEnabled(True)
        updated = stats.get("updated_rows", 0)
        parents = stats.get("parents_processed", 0)
        self.notify_status.emit(f"✅ Chronology realigned! {updated} rows updated across {parents} folders.")
        QMessageBox.information(
            self,
            "Realignment Complete",
            f"Successfully aligned chronological ordering.\n\n"
            f"• Updated entries: {updated}\n"
            f"• Folder groups processed: {parents}",
        )
        self.request_reload.emit()

    def _on_realign_error(self, err_msg: str) -> None:
        self.realign_btn.setEnabled(True)
        self.notify_status.emit(f"❌ Realignment failed: {err_msg}")
        QMessageBox.critical(self, "Realignment Failed", f"Database error during realignment:\n{err_msg}")

    def _on_save_all_changes(self) -> None:
        """Save all staged edits in a single atomic transaction."""
        if not self.engine or not self.staged_edits:
            return

        if is_rekordbox_running():
            res = QMessageBox.warning(
                self,
                "Rekordbox is Running",
                "⚠️ Rekordbox is currently running!\n\n"
                "Saving changes now may cause database locking or corruption.\n"
                "Please close Rekordbox (Cmd + Q) and try again.",
                QMessageBox.StandardButton.Cancel,
            )
            return

        count = len(self.staged_edits)
        res = QMessageBox.question(
            self,
            "Confirm Save",
            f"Are you ready to commit {count} session changes to Rekordbox?\n\n"
            "This will update names, create any necessary Month/Year folders, "
            "and realign the chronological sequence in the database.",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
        )
        if res != QMessageBox.StandardButton.Save:
            return

        updates = []
        for item in self.staged_edits.values():
            updates.append({
                "record": item["record"],
                "new_name": item["name"],
                "new_date": item["date"],
            })

        self.save_btn.setEnabled(False)
        self.discard_btn.setEnabled(False)
        self.notify_status.emit(f"Saving {count} history updates to database...")

        worker = HistoryBatchSaveWorker(self.engine, updates, auto_realign=True, parent=self)
        worker.success.connect(self._on_save_success)
        worker.error.connect(self._on_save_error)
        worker.start()

    def _on_save_success(self, stats: Dict[str, Any]) -> None:
        self.save_btn.setEnabled(True)
        self.discard_btn.setEnabled(True)
        updated_count = stats.get("updated_count", 0)
        self.staged_edits.clear()
        self.notify_status.emit(f"✅ Saved {updated_count} history changes successfully.")
        QMessageBox.information(
            self,
            "Changes Saved",
            f"Successfully updated {updated_count} sessions and realigned chronological tree order.",
        )
        self.request_reload.emit()

    def _on_save_error(self, err_msg: str) -> None:
        self.save_btn.setEnabled(True)
        self.discard_btn.setEnabled(True)
        self.notify_status.emit(f"❌ Save failed: {err_msg}")
        QMessageBox.critical(self, "Save Failed", f"Database error while saving changes:\n{err_msg}")

    def _update_status_bar(self) -> None:
        total = len(self.original_records)
        staged = len(self.staged_edits)
        self.status_label.setText(f"Sessions: {total} total  •  Staged changes: {staged}")
        self.save_btn.setEnabled(staged > 0)
        self.discard_btn.setEnabled(staged > 0)
