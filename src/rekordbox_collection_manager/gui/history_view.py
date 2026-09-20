"""History Session Rename & Realign Screen."""

import re
from typing import Any, Dict, List, Optional
from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.engine import Engine

from rekordbox_collection_manager.gui.history_detail_view import HistoryDetailView
from rekordbox_collection_manager.gui.track_detail_view import TrackDetailView
from rekordbox_collection_manager.gui.theme import (
    COLOR_AMBER,
    COLOR_AMBER_BG,
    COLOR_AMBER_BORDER,
    COLOR_BLUE,
    COLOR_GREEN,
    COLOR_GREEN_BG,
    COLOR_GREEN_BORDER,
    COLOR_RED,
    COLOR_RED_BG,
    COLOR_RED_BORDER,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_SECONDARY,
)
from rekordbox_collection_manager.gui.workers import (
    HistoryBatchSaveWorker,
    HistoryDeleteWorker,
    HistoryRealignWorker,
    HistoryRestoreWorker,
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
    replace_file_requested = Signal(dict)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.engine: Optional[Engine] = None
        self.original_records: List[Dict[str, Any]] = []
        # Key: record id, Value: {'record': dict, 'name': str, 'date': str}
        self.staged_edits: Dict[str, Dict[str, Any]] = {}
        self.active_folder_filter: Optional[str] = None  # None for All, or 'active_all', 'active_year_...', 'deleted_all', etc.
        self.search_filter_text: str = ""
        self.active_session_ids_in_table: List[str] = []
        self.current_detail_id: Optional[str] = None
        self.db_path: Optional[str] = None
        self.show_deleted: bool = False
        self.active_worker: Optional[Any] = None

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

        self.show_deleted_btn = QPushButton("🗑️ Show Deleted (0)")
        self.show_deleted_btn.setCheckable(True)
        self.show_deleted_btn.setChecked(False)
        self.show_deleted_btn.setToolTip("Toggle display of soft-deleted history sessions and folders")
        self.show_deleted_btn.toggled.connect(self._on_toggle_show_deleted)
        top_bar.addWidget(self.show_deleted_btn)

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
        self.table.verticalHeader().setToolTip("Click to open session options")
        self.table.verticalHeader().setCursor(Qt.CursorShape.PointingHandCursor)
        self.table.verticalHeader().sectionClicked.connect(self._on_row_header_clicked)
        self.table.verticalHeader().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.verticalHeader().customContextMenuRequested.connect(self._on_row_header_context_menu)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(3, 150)
        self.table.setColumnWidth(4, 180)
        self.table.setColumnWidth(6, 160)

        actions_hdr = self.table.horizontalHeaderItem(6)
        if actions_hdr:
            actions_hdr.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        self.table.cellChanged.connect(self._on_cell_changed)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context_menu)
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
        self.detail_view.open_history_session_requested.connect(self._open_session_detail)
        self.detail_view.track_detail_requested.connect(self._open_track_detail)
        self.detail_view.restore_session_requested.connect(self._on_restore_from_detail)
        self.detail_view.delete_session_requested.connect(self._on_delete_from_detail)
        self.stack.addWidget(self.detail_view)

        # ----------------------------------------------------------------------
        # Page 2: Track Details View Screen
        # ----------------------------------------------------------------------
        self.track_detail_view = TrackDetailView(parent=self, back_button_text="← Back to Session")
        self.track_detail_view.back_clicked.connect(self._on_back_from_track_detail)
        self.track_detail_view.open_history_session_requested.connect(self._open_session_detail)
        self.track_detail_view.status_message.connect(self.notify_status.emit)
        self.track_detail_view.track_changed.connect(self._on_track_detail_changed)
        self.track_detail_view.replace_file_requested.connect(self.replace_file_requested.emit)
        self.stack.addWidget(self.track_detail_view)

    def set_data(
        self,
        engine: Engine,
        records: List[Dict[str, Any]],
        db_path: Optional[str] = None,
    ) -> None:
        """Populate the view with records loaded from database."""
        self.engine = engine
        self.db_path = db_path
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

    def _on_toggle_show_deleted(self, checked: bool) -> None:
        """Handle toggling the Show Deleted button."""
        self.show_deleted = checked
        if not checked and self.active_folder_filter and str(self.active_folder_filter).startswith("deleted_"):
            self.active_folder_filter = "active_all"
        self._update_folder_tree()
        self._refresh_table()

    def _update_folder_tree(self) -> None:
        """Rebuild Year/Month folder tree from current records."""
        self.folder_tree.clear()

        active_records = [r for r in self.original_records if not r.get("is_deleted")]
        deleted_records = [r for r in self.original_records if r.get("is_deleted")]

        # Update show_deleted button text
        action_word = "Hide" if self.show_deleted else "Show"
        self.show_deleted_btn.setText(f"🗑️ {action_word} Deleted ({len(deleted_records)})")
        if len(deleted_records) == 0:
            self.show_deleted_btn.setEnabled(False)
            if self.show_deleted:
                self.show_deleted = False
                self.show_deleted_btn.setChecked(False)
        else:
            self.show_deleted_btn.setEnabled(True)

        # 1. Root Item: All Sessions
        all_item = QTreeWidgetItem([f"All Sessions ({len(active_records)})"])
        all_item.setData(0, Qt.ItemDataRole.UserRole, "active_all")
        self.folder_tree.addTopLevelItem(all_item)

        # Group active records by Year and Month
        year_months: Dict[str, Dict[str, int]] = {}
        for r in active_records:
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
            year_item.setData(0, Qt.ItemDataRole.UserRole, f"active_year_{year}")
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
                m_item.setData(0, Qt.ItemDataRole.UserRole, f"active_month_{m_key}")
                year_item.addChild(m_item)

            year_item.setExpanded(True)

        # 2. Section: Deleted Sessions (replicates year & month hierarchy)
        if self.show_deleted and deleted_records:
            del_root = QTreeWidgetItem([f"🗑️ Deleted Sessions ({len(deleted_records)})"])
            del_root.setData(0, Qt.ItemDataRole.UserRole, "deleted_all")
            del_root.setForeground(0, Qt.GlobalColor.red)
            font = del_root.font(0)
            font.setBold(True)
            del_root.setFont(0, font)
            self.folder_tree.addTopLevelItem(del_root)

            del_year_months: Dict[str, Dict[str, int]] = {}
            for r in deleted_records:
                name = r.get("name") or ""
                date = r.get("date") or ""
                y, m = extract_year_month_from_name_or_date(name, date)
                if y and m:
                    y_str = str(y)
                    m_str = f"{y}{m:02d}"
                    if y_str not in del_year_months:
                        del_year_months[y_str] = {}
                    del_year_months[y_str][m_str] = del_year_months[y_str].get(m_str, 0) + 1
                else:
                    if "Other" not in del_year_months:
                        del_year_months["Other"] = {}
                    del_year_months["Other"]["Other"] = del_year_months["Other"].get("Other", 0) + 1

            for year in sorted(del_year_months.keys(), reverse=True):
                year_count = sum(del_year_months[year].values())
                year_item = QTreeWidgetItem([f"📁 {year} ({year_count})"])
                year_item.setData(0, Qt.ItemDataRole.UserRole, f"deleted_year_{year}")
                year_item.setForeground(0, Qt.GlobalColor.red)
                del_root.addChild(year_item)

                for m_key in sorted(del_year_months[year].keys(), reverse=True):
                    count = del_year_months[year][m_key]
                    if m_key != "Other" and len(m_key) == 6:
                        m_num = int(m_key[4:])
                        m_name = MONTH_INT_TO_NAME.get(m_num, f"Month {m_num}")
                        label = f"  └ 📅 {m_num:02d} {m_name} ({count})"
                    else:
                        label = f"  └ 📄 Uncategorized ({count})"
                    m_item = QTreeWidgetItem([label])
                    m_item.setData(0, Qt.ItemDataRole.UserRole, f"deleted_month_{m_key}")
                    m_item.setForeground(0, Qt.GlobalColor.red)
                    year_item.addChild(m_item)

                year_item.setExpanded(True)

            del_root.setExpanded(True)

        # Highlight current selected filter or default to all_item
        selected_found = False
        if self.active_folder_filter:
            iterator = QTreeWidgetItemIterator(self.folder_tree)
            while iterator.value():
                item = iterator.value()
                if item.data(0, Qt.ItemDataRole.UserRole) == self.active_folder_filter:
                    item.setSelected(True)
                    selected_found = True
                    break
                iterator += 1

        if not selected_found:
            all_item.setSelected(True)
            self.active_folder_filter = "active_all"

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

        filter_key = self.active_folder_filter or "active_all"
        is_viewing_deleted = str(filter_key).startswith("deleted_")

        for r in self.original_records:
            rec_id = r["id"]
            is_deleted = bool(r.get("is_deleted", False))

            # 1. Active vs Deleted partition filter
            if is_viewing_deleted:
                if not is_deleted:
                    continue
            else:
                if is_deleted:
                    continue

            orig_name = r.get("name") or ""
            orig_date = r.get("date") or ""

            # Check if staged
            is_staged = rec_id in self.staged_edits
            cur_name = self.staged_edits[rec_id]["name"] if is_staged else orig_name
            cur_date = self.staged_edits[rec_id]["date"] if is_staged else orig_date

            orig_base, orig_loc, orig_art = parse_history_name(orig_name)
            cur_base, cur_loc, cur_art = parse_history_name(cur_name)

            # 2. Specific Folder Filter (Year / Month)
            y, m = extract_year_month_from_name_or_date(cur_name, cur_date)
            folder_m_key = f"{y}{m:02d}" if (y and m) else "Other"
            folder_y_key = str(y) if y else "Other"

            if is_viewing_deleted:
                if filter_key.startswith("deleted_year_"):
                    target_year = filter_key.replace("deleted_year_", "")
                    if folder_y_key != target_year:
                        continue
                elif filter_key.startswith("deleted_month_"):
                    target_month = filter_key.replace("deleted_month_", "")
                    if folder_m_key != target_month:
                        continue
            else:
                if filter_key.startswith("active_year_"):
                    target_year = filter_key.replace("active_year_", "")
                    if folder_y_key != target_year:
                        continue
                elif filter_key.startswith("active_month_"):
                    target_month = filter_key.replace("active_month_", "")
                    if folder_m_key != target_month:
                        continue

            # 3. Text Search Filter
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
            if is_deleted:
                status_item.setText("🗑️ Deleted")
                status_item.setForeground(Qt.GlobalColor.red)
            elif is_staged:
                status_item.setText("● Staged")
                status_item.setForeground(Qt.GlobalColor.yellow)
            else:
                status_item.setText("Clean")
                status_item.setForeground(Qt.GlobalColor.gray)
            status_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            status_item.setData(Qt.ItemDataRole.UserRole, rec_id)
            self.table.setItem(row_idx, 0, status_item)

            # Cells are editable ONLY if active
            cell_flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            if not is_deleted:
                cell_flags |= Qt.ItemFlag.ItemIsEditable

            text_color = Qt.GlobalColor.gray if is_deleted else Qt.GlobalColor.white

            # Col 1: Date
            date_item = QTableWidgetItem(cur_date)
            date_item.setData(Qt.ItemDataRole.UserRole, rec_id)
            date_item.setFlags(cell_flags)
            if is_staged and cur_date != orig_date:
                date_item.setForeground(Qt.GlobalColor.yellow)
            else:
                date_item.setForeground(text_color)
            self.table.setItem(row_idx, 1, date_item)

            # Col 2: Session Name
            name_item = QTableWidgetItem(cur_base)
            name_item.setData(Qt.ItemDataRole.UserRole, rec_id)
            name_item.setFlags(cell_flags)
            if is_staged and cur_base != orig_base:
                name_item.setForeground(Qt.GlobalColor.yellow)
            else:
                name_item.setForeground(text_color)
            self.table.setItem(row_idx, 2, name_item)

            # Col 3: Location
            loc_item = QTableWidgetItem(cur_loc)
            loc_item.setData(Qt.ItemDataRole.UserRole, rec_id)
            loc_item.setFlags(cell_flags)
            if is_staged and cur_loc != orig_loc:
                loc_item.setForeground(Qt.GlobalColor.yellow)
            else:
                loc_item.setForeground(text_color)
            self.table.setItem(row_idx, 3, loc_item)

            # Col 4: Played With
            art_item = QTableWidgetItem(cur_art)
            art_item.setData(Qt.ItemDataRole.UserRole, rec_id)
            art_item.setFlags(cell_flags)
            if is_staged and cur_art != orig_art:
                art_item.setForeground(Qt.GlobalColor.yellow)
            else:
                art_item.setForeground(text_color)
            self.table.setItem(row_idx, 4, art_item)

            # Col 5: Original Created At
            orig_created = orig_date or "(None)"
            created_item = QTableWidgetItem(orig_created)
            created_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            created_item.setForeground(text_color)
            self.table.setItem(row_idx, 5, created_item)

            # Col 6: Actions
            action_widget = QWidget()
            action_widget.setStyleSheet("background: transparent;")
            action_layout = QHBoxLayout(action_widget)
            action_layout.setContentsMargins(4, 2, 4, 2)
            action_layout.setSpacing(6)
            action_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

            detail_btn = QPushButton("🔍 Details")
            detail_btn.setObjectName("detailActionBtn")
            detail_btn.setFixedHeight(24)
            detail_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            detail_btn.setToolTip("View session details and full tracklist")
            detail_btn.clicked.connect(lambda _, rid=rec_id: self._open_session_detail(rid))
            action_layout.addWidget(detail_btn)

            if is_deleted:
                restore_btn = QPushButton("♻️")
                restore_btn.setObjectName("restoreActionBtn")
                restore_btn.setFixedHeight(24)
                restore_btn.setFixedWidth(28)
                restore_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                restore_btn.setToolTip("Restore session back to active collection")
                restore_btn.clicked.connect(lambda _, rid=rec_id, sn=cur_name: self._confirm_and_restore_session(rid, sn))
                action_layout.addWidget(restore_btn)
            else:
                del_btn = QPushButton("🗑️")
                del_btn.setObjectName("deleteActionBtn")
                del_btn.setFixedHeight(24)
                del_btn.setFixedWidth(28)
                del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                del_btn.setToolTip("Soft-delete session from Rekordbox")
                del_btn.clicked.connect(lambda _, rid=rec_id, sn=cur_name: self._confirm_and_delete_session(rid, sn))
                action_layout.addWidget(del_btn)

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

    def open_session_by_id(self, rec_id: str) -> None:
        """Public method to navigate directly to a history session by ID."""
        self._open_session_detail(rec_id)

    def _open_session_detail(self, rec_id: str) -> None:
        """Open the HistoryDetailView for a given session ID."""
        if not self.engine:
            return
        self.current_detail_id = rec_id
        record = next((r for r in self.original_records if str(r["id"]) == str(rec_id)), None)
        if not record:
            name = f"History Session {rec_id}"
            date = ""
        else:
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
            db_path=getattr(self, "db_path", None),
        )
        self.stack.setCurrentIndex(1)

    def _on_back_from_detail(self) -> None:
        """Return from detail view to main sessions list."""
        self.stack.setCurrentIndex(0)
        self.current_detail_id = None

    def _open_track_detail(self, track: Dict[str, Any], track_idx: int) -> None:
        """Open TrackDetailView for a track within the active history session."""
        if not self.engine or not track:
            return
        session_name = self.detail_view.session_data.get("name") or "Session"
        btn_label = (
            f"← Back to {session_name[:24]}..."
            if len(session_name) > 24
            else f"← Back to {session_name}"
        )
        self.track_detail_view.load_track(
            engine=self.engine,
            track_data=track,
            db_path=self.db_path,
            tracks_list=self.detail_view.displayed_tracks,
            current_index=track_idx,
            back_button_text=btn_label,
        )
        self.stack.setCurrentIndex(2)

    def _on_back_from_track_detail(self) -> None:
        """Return from track details view to active history session view."""
        self.stack.setCurrentIndex(1)

    def _on_track_detail_changed(self, idx: int, track: Dict[str, Any]) -> None:
        """Select track in history detail view table when navigating in track detail view."""
        self.detail_view.select_track_at_index(idx)

    def reset_to_list(self) -> None:
        """Reset view to history sessions list."""
        self.stack.setCurrentIndex(0)

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
        active_count = sum(1 for r in self.original_records if not r.get("is_deleted"))
        del_count = sum(1 for r in self.original_records if r.get("is_deleted"))
        staged = len(self.staged_edits)
        del_info = f" ({del_count} deleted)" if del_count > 0 else ""
        self.status_label.setText(f"Sessions: {active_count} active{del_info}  •  Staged changes: {staged}")
        self.save_btn.setEnabled(staged > 0)
        self.discard_btn.setEnabled(staged > 0)

    def _on_table_context_menu(self, pos: QPoint) -> None:
        """Handle right-click context menu on history session row."""
        item = self.table.itemAt(pos)
        if not item:
            return
        row = item.row()
        self.table.selectRow(row)
        self._show_session_context_menu(row, self.table.viewport().mapToGlobal(pos))

    def _on_row_header_clicked(self, logical_index: int) -> None:
        """Handle left-click on row number header to show session actions menu."""
        if logical_index < 0 or logical_index >= self.table.rowCount():
            return
        self.table.selectRow(logical_index)
        cursor_pos = QCursor.pos()
        v_header = self.table.verticalHeader()
        header_rect = v_header.rect()
        local_cursor = v_header.mapFromGlobal(cursor_pos)
        if header_rect.contains(local_cursor):
            menu_pos = cursor_pos
        else:
            x = v_header.width()
            y = v_header.sectionViewportPosition(logical_index)
            menu_pos = v_header.viewport().mapToGlobal(QPoint(x, y))
        self._show_session_context_menu(logical_index, menu_pos)

    def _on_row_header_context_menu(self, pos: QPoint) -> None:
        """Handle right-click context menu on row number header."""
        v_header = self.table.verticalHeader()
        row = v_header.logicalIndexAt(pos)
        if row >= 0:
            self.table.selectRow(row)
            global_pos = v_header.mapToGlobal(pos)
            self._show_session_context_menu(row, global_pos)

    def _show_session_context_menu(self, row: int, global_pos: QPoint) -> None:
        """Show session actions menu (Delete, Restore, Details) for a given row."""
        if row < 0 or row >= self.table.rowCount():
            return

        rec_id = None
        for c in range(self.table.columnCount()):
            cand = self.table.item(row, c)
            if cand and cand.data(Qt.ItemDataRole.UserRole):
                rec_id = cand.data(Qt.ItemDataRole.UserRole)
                break
        if not rec_id:
            return

        record = next((r for r in self.original_records if str(r["id"]) == str(rec_id)), None)
        if not record:
            return

        is_deleted = record.get("is_deleted", False)
        session_name = record.get("name") or f"Session {rec_id}"

        menu = QMenu(self)
        detail_action = menu.addAction("🔍 View Details")
        menu.addSeparator()

        if is_deleted:
            action_item = menu.addAction("♻️ Restore Session")
        else:
            action_item = menu.addAction("🗑️ Delete Session")

        selected = menu.exec(global_pos)
        if selected == detail_action:
            self._open_session_detail(rec_id)
        elif selected == action_item:
            if is_deleted:
                self._confirm_and_restore_session(rec_id, session_name)
            else:
                self._confirm_and_delete_session(rec_id, session_name)

    def _confirm_and_delete_session(self, rec_id: str, session_name: str) -> None:
        """Confirm and execute soft-delete of a history session."""
        if not self.engine:
            return

        if is_rekordbox_running():
            QMessageBox.warning(
                self,
                "Rekordbox is Running",
                "⚠️ Please close Rekordbox (Cmd + Q) before deleting history sessions.",
            )
            return

        res = QMessageBox.question(
            self,
            "Delete History Session",
            f"Are you sure you want to delete '{session_name}'?\n\n"
            "This will safely soft-delete the session and its tracks from Rekordbox, and decrease the DJ play count of played tracks accordingly.\n"
            "You can restore it at any time from Deleted Sessions.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if res != QMessageBox.StandardButton.Yes:
            return

        self.notify_status.emit(f"Deleting session '{session_name}'...")
        worker = HistoryDeleteWorker(self.engine, rec_id, parent=self)
        worker.success.connect(self._on_delete_success)
        worker.error.connect(self._on_action_error)
        self.active_worker = worker
        worker.start()

    def _confirm_and_restore_session(self, rec_id: str, session_name: str) -> None:
        """Confirm and execute restoration of a soft-deleted history session."""
        if not self.engine:
            return

        if is_rekordbox_running():
            QMessageBox.warning(
                self,
                "Rekordbox is Running",
                "⚠️ Please close Rekordbox (Cmd + Q) before restoring history sessions.",
            )
            return

        res = QMessageBox.question(
            self,
            "Restore History Session",
            f"Are you sure you want to restore '{session_name}'?\n\n"
            "This will reactivate the session and all its tracks back into your active Rekordbox collection, and restore their DJ play count accordingly.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if res != QMessageBox.StandardButton.Yes:
            return

        self.notify_status.emit(f"Restoring session '{session_name}'...")
        worker = HistoryRestoreWorker(self.engine, rec_id, parent=self)
        worker.success.connect(self._on_restore_success)
        worker.error.connect(self._on_action_error)
        self.active_worker = worker
        worker.start()

    def _on_restore_from_detail(self, history_id: str) -> None:
        """Handle restore triggered from HistoryDetailView."""
        record = next((r for r in self.original_records if str(r["id"]) == str(history_id)), None)
        name = record.get("name", f"Session {history_id}") if record else f"Session {history_id}"
        self._confirm_and_restore_session(history_id, name)

    def _on_delete_from_detail(self, history_id: str) -> None:
        """Handle delete triggered from HistoryDetailView."""
        record = next((r for r in self.original_records if str(r["id"]) == str(history_id)), None)
        name = record.get("name", f"Session {history_id}") if record else f"Session {history_id}"
        self._confirm_and_delete_session(history_id, name)

    def _on_delete_success(self, history_id: str) -> None:
        self.notify_status.emit(f"🗑️ Session {history_id} soft-deleted.")
        rec = next((r for r in self.original_records if str(r["id"]) == str(history_id)), None)
        if rec:
            rec["is_deleted"] = True
        self.staged_edits.pop(history_id, None)
        self._update_folder_tree()
        self._refresh_table()
        if self.current_detail_id == history_id:
            self._open_session_detail(history_id)
        self.request_reload.emit()

    def _on_restore_success(self, history_id: str) -> None:
        self.notify_status.emit(f"✅ Session {history_id} restored.")
        rec = next((r for r in self.original_records if str(r["id"]) == str(history_id)), None)
        if rec:
            rec["is_deleted"] = False
        self._update_folder_tree()
        self._refresh_table()
        if self.current_detail_id == history_id:
            self._open_session_detail(history_id)
        self.request_reload.emit()

    def _on_action_error(self, history_id: str, err_msg: str) -> None:
        self.notify_status.emit(f"❌ Error for session {history_id}: {err_msg}")
        QMessageBox.critical(self, "Operation Failed", f"Database error:\n{err_msg}")

