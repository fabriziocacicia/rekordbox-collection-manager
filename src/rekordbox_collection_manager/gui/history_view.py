"""History Session Rename & Realign Screen."""

import re
from typing import Any, Dict, List, Optional, Set
from PySide6.QtCore import (
    QAbstractItemModel,
    QModelIndex,
    QPoint,
    QStringListModel,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QCursor, QKeyEvent, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QCompleter,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.engine import Engine

from rekordbox_collection_manager.gui.chip import DJChipsContainer
from rekordbox_collection_manager.gui.history_detail_view import HistoryDetailView
from rekordbox_collection_manager.gui.track_detail_view import TrackDetailView
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
    COLOR_GREEN_BG,
    COLOR_GREEN_BORDER,
    COLOR_RED,
    COLOR_RED_BG,
    COLOR_RED_BORDER,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_PRIMARY,
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
    compose_played_with,
    extract_year_month_from_name_or_date,
    is_rekordbox_running,
    parse_history_name,
    parse_played_with,
)


class LocationLineEdit(QLineEdit):
    """Line edit with completer support for history session locations."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setStyleSheet(
            f"QLineEdit {{ "
            f"  background-color: {COLOR_BG_INPUT}; "
            f"  color: {COLOR_TEXT_PRIMARY}; "
            f"  border: 1px solid {COLOR_BORDER_FOCUS}; "
            f"  border-radius: 4px; "
            f"  padding: 2px 6px; "
            f"  font-size: 12px; "
            f"}}"
        )

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Down:
            completer = self.completer()
            if completer and not completer.popup().isVisible():
                completer.setCompletionPrefix(self.text())
                completer.complete()
                event.accept()
                return
        super().keyPressEvent(event)


class LocationDelegate(QStyledItemDelegate):
    """Item delegate for Location column providing autocompletion with existing locations."""

    def __init__(self, history_view: "HistoryView", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.history_view = history_view

    def createEditor(
        self,
        parent: QWidget,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> QWidget:
        editor = LocationLineEdit(parent)
        locs = self.history_view.get_all_available_locations()
        completer_model = QStringListModel(locs, editor)
        completer = QCompleter(completer_model, editor)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)

        popup = completer.popup()
        if popup:
            fm = popup.fontMetrics()
            max_w = max((fm.horizontalAdvance(loc) for loc in locs), default=0)
            popup.setMinimumWidth(max(240, min(max_w + 48, 450)))
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

        completer.activated.connect(
            lambda text: self._on_completer_activated(editor, text)
        )
        editor.setCompleter(completer)
        QTimer.singleShot(0, lambda: self._show_popup_if_needed(editor, completer))
        return editor

    def _show_popup_if_needed(self, editor: QLineEdit, completer: QCompleter) -> None:
        if editor and completer:
            completer.setCompletionPrefix(editor.text())
            completer.complete()

    def _on_completer_activated(self, editor: QLineEdit, text: str) -> None:
        editor.setText(text)
        self.commitData.emit(editor)
        self.closeEditor.emit(editor, QStyledItemDelegate.EndEditHint.SubmitModelCache)

    def setEditorData(self, editor: QWidget, index: QModelIndex) -> None:
        if isinstance(editor, QLineEdit):
            val = index.data(Qt.ItemDataRole.DisplayRole) or ""
            editor.setText(val)
            editor.selectAll()

    def setModelData(
        self,
        editor: QWidget,
        model: QAbstractItemModel,
        index: QModelIndex,
    ) -> None:
        if isinstance(editor, QLineEdit):
            raw_text = editor.text().strip()
            completer = editor.completer()
            resolved = raw_text
            if raw_text:
                locs = self.history_view.get_all_available_locations()
                matched = None
                for loc in locs:
                    if loc.lower() == raw_text.lower():
                        matched = loc
                        break
                if not matched and completer:
                    popup = completer.popup()
                    if popup and popup.isVisible() and popup.currentIndex().isValid():
                        matched = popup.currentIndex().data()
                    elif completer.currentCompletion():
                        cand = completer.currentCompletion()
                        if cand.lower().startswith(raw_text.lower()):
                            matched = cand
                if matched:
                    resolved = matched

            model.setData(index, resolved, Qt.ItemDataRole.EditRole)
            if resolved:
                self.history_view.register_new_location(resolved)


class PlayedWithChipDelegate(QStyledItemDelegate):
    """Item delegate for Played With column ensuring no underlying item text is rendered behind chips."""

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""  # Never render text behind chip cell widgets
        widget = option.widget
        style = widget.style() if widget else QApplication.style()
        style.drawPrimitive(
            QStyle.PrimitiveElement.PE_PanelItemViewItem, opt, painter, widget
        )


class HistoryView(QWidget):
    """View to batch edit and realign Rekordbox history sessions."""

    notify_status = Signal(str)
    request_reload = Signal()
    replace_file_requested = Signal(dict)
    open_playlist_requested = Signal(str)
    open_tag_requested = Signal(str)
    db_saved = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.engine: Optional[Engine] = None
        self.original_records: List[Dict[str, Any]] = []
        # Key: record id, Value: {'record': dict, 'name': str, 'date': str}
        self.staged_edits: Dict[str, Dict[str, Any]] = {}
        self.all_available_djs: Set[str] = set()
        self.all_available_locations: Set[str] = set()
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
        tree_container.setMinimumWidth(180)
        tree_layout = QVBoxLayout(tree_container)
        tree_layout.setContentsMargins(0, 0, 0, 0)
        tree_layout.setSpacing(6)

        tree_header = QLabel("HISTORY FOLDERS")
        tree_header.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 600; padding: 4px;")
        tree_layout.addWidget(tree_header)

        self.folder_tree = QTreeWidget()
        self.folder_tree.setHeaderHidden(True)
        self.folder_tree.setIndentation(14)
        self.folder_tree.currentItemChanged.connect(self._on_folder_current_item_changed)
        self.folder_tree.itemClicked.connect(self._on_folder_item_clicked)
        tree_layout.addWidget(self.folder_tree)

        splitter.addWidget(tree_container)

        # Right: Sessions Table
        table_container = QWidget()
        table_layout = QVBoxLayout(table_container)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.setSpacing(6)

        table_header = QLabel("HISTORY SESSIONS (Double-click date, name, or location to edit • Manage played with chips • Click Details to view tracks)")
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
        self.table.setColumnWidth(4, 290)
        self.table.setColumnWidth(6, 160)
        self.table.setItemDelegateForColumn(3, LocationDelegate(self, self.table))
        self.table.setItemDelegateForColumn(4, PlayedWithChipDelegate(self.table))
        self.table.horizontalHeader().sectionResized.connect(self._on_col_section_resized)

        actions_hdr = self.table.horizontalHeaderItem(6)
        if actions_hdr:
            actions_hdr.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        self.table.cellChanged.connect(self._on_cell_changed)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context_menu)
        table_layout.addWidget(self.table)

        splitter.addWidget(table_container)
        splitter.setSizes([210, 790])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setCollapsible(0, False)

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
        self.detail_view.open_playlist_requested.connect(self.open_playlist_requested.emit)
        self.detail_view.open_tag_requested.connect(self.open_tag_requested.emit)
        self.detail_view.track_detail_requested.connect(self._open_track_detail)
        self.detail_view.restore_session_requested.connect(self._on_restore_from_detail)
        self.detail_view.delete_session_requested.connect(self._on_delete_from_detail)
        self.detail_view.session_metadata_updated.connect(self._on_detail_metadata_updated)
        self.detail_view.save_session_requested.connect(self._on_detail_save_session)
        self.detail_view.discard_session_requested.connect(self._on_detail_discard_session)
        self.detail_view.location_added.connect(self.register_new_location)
        self.detail_view.dj_added.connect(self.register_new_dj)
        self.detail_view.db_saved.connect(self.db_saved.emit)
        self.stack.addWidget(self.detail_view)

        # ----------------------------------------------------------------------
        # Page 2: Track Details View Screen
        # ----------------------------------------------------------------------
        self.track_detail_view = TrackDetailView(parent=self, back_button_text="← Back to Session")
        self.track_detail_view.back_clicked.connect(self._on_back_from_track_detail)
        self.track_detail_view.open_history_session_requested.connect(self._open_session_detail)
        self.track_detail_view.open_playlist_requested.connect(self.open_playlist_requested.emit)
        self.track_detail_view.open_tag_requested.connect(self.open_tag_requested.emit)
        self.track_detail_view.status_message.connect(self.notify_status.emit)
        self.track_detail_view.track_changed.connect(self._on_track_detail_changed)
        self.track_detail_view.replace_file_requested.connect(self.replace_file_requested.emit)
        self.track_detail_view.db_saved.connect(self.db_saved.emit)
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
        self._rebuild_all_available_djs()
        self._rebuild_all_available_locations()
        self._update_folder_tree()
        self._refresh_table()
        self._update_status_bar()

    def _rebuild_all_available_djs(self) -> None:
        """Extract all unique DJ names from original and staged records."""
        self.all_available_djs.clear()
        for r in self.original_records:
            name = r.get("name") or ""
            _, _, art = parse_history_name(name)
            if art:
                for dj in parse_played_with(art):
                    self.all_available_djs.add(dj)
        for edit in self.staged_edits.values():
            name = edit.get("name") or ""
            _, _, art = parse_history_name(name)
            if art:
                for dj in parse_played_with(art):
                    self.all_available_djs.add(dj)

    def get_all_available_djs(self) -> List[str]:
        """Return sorted unique list of all known DJ names."""
        return sorted(list(self.all_available_djs), key=lambda s: s.lower())

    def register_new_dj(self, name: str) -> None:
        """Register a newly typed DJ name and update available options across table."""
        clean = name.strip()
        if clean and clean not in self.all_available_djs:
            self.all_available_djs.add(clean)
            cand_list = self.get_all_available_djs()
            for r_idx in range(self.table.rowCount()):
                w = self.table.cellWidget(r_idx, 4)
                if isinstance(w, DJChipsContainer):
                    w.set_available_djs(cand_list)
            if hasattr(self, "detail_view"):
                self.detail_view.set_available_djs(cand_list)

    def _rebuild_all_available_locations(self) -> None:
        """Extract all unique location names from original and staged records."""
        self.all_available_locations.clear()
        for r in self.original_records:
            name = r.get("name") or ""
            _, loc, _ = parse_history_name(name)
            if loc and loc.strip():
                self.all_available_locations.add(loc.strip())
        for edit in self.staged_edits.values():
            name = edit.get("name") or ""
            _, loc, _ = parse_history_name(name)
            if loc and loc.strip():
                self.all_available_locations.add(loc.strip())

    def get_all_available_locations(self) -> List[str]:
        """Return sorted unique list of all known location names."""
        return sorted(list(self.all_available_locations), key=lambda s: s.lower())

    def register_new_location(self, name: str) -> None:
        """Register a newly typed location name into the pool of available locations."""
        clean = name.strip()
        if clean and clean not in self.all_available_locations:
            self.all_available_locations.add(clean)
            if hasattr(self, "detail_view"):
                self.detail_view.set_available_locations(self.get_all_available_locations())

    def _update_row_height(self, row: int) -> None:
        """Dynamically adjust row height if DJ chips wrap to multiple lines."""
        if row < 0 or row >= self.table.rowCount():
            return
        widget = self.table.cellWidget(row, 4)
        if isinstance(widget, DJChipsContainer):
            col_w = self.table.columnWidth(4)
            avail_w = max(60, col_w - 16)
            needed_h = widget.flow_layout.heightForWidth(avail_w)
            target_h = max(42, needed_h + 8)
            if self.table.rowHeight(row) != target_h:
                self.table.setRowHeight(row, target_h)

    def _update_row_height_for_container(self, container: QWidget) -> None:
        """Find the row containing container and update its height."""
        for r in range(self.table.rowCount()):
            if self.table.cellWidget(r, 4) is container:
                self._update_row_height(r)
                break

    def _on_col_section_resized(self, logical_index: int, old_size: int, new_size: int) -> None:
        """Recalculate row heights when Played With column is resized."""
        if logical_index == 4:
            for r in range(self.table.rowCount()):
                self._update_row_height(r)

    def _on_toggle_show_deleted(self, checked: bool) -> None:
        """Handle toggling the Show Deleted button."""
        self.show_deleted = checked
        if not checked and self.active_folder_filter and str(self.active_folder_filter).startswith("deleted_"):
            self.active_folder_filter = "active_all"
        self._update_folder_tree()
        self._refresh_table()

    def _update_folder_tree(self) -> None:
        """Rebuild Year/Month folder tree from current records."""
        self.folder_tree.blockSignals(True)
        try:
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
                        label = f"📅 {m_num:02d} {m_name} ({count})"
                    else:
                        label = f"📄 Uncategorized ({count})"
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
                            label = f"📅 {m_num:02d} {m_name} ({count})"
                        else:
                            label = f"📄 Uncategorized ({count})"
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
                        self.folder_tree.setCurrentItem(item)
                        self.folder_tree.scrollToItem(item)
                        selected_found = True
                        break
                    iterator += 1

            if not selected_found:
                all_item.setSelected(True)
                self.folder_tree.setCurrentItem(all_item)
                self.folder_tree.scrollToItem(all_item)
                self.active_folder_filter = "active_all"
        finally:
            self.folder_tree.blockSignals(False)

    def _on_folder_current_item_changed(
        self, current: Optional[QTreeWidgetItem], previous: Optional[QTreeWidgetItem]
    ) -> None:
        """Filter table when folder tree current item changes (keyboard or mouse)."""
        if not current:
            return
        folder_key = current.data(0, Qt.ItemDataRole.UserRole)
        if folder_key and folder_key != self.active_folder_filter:
            self.active_folder_filter = folder_key
            self._refresh_table()

    def _on_folder_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """Filter table when folder tree item is clicked."""
        folder_key = item.data(0, Qt.ItemDataRole.UserRole)
        if folder_key and folder_key != self.active_folder_filter:
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

            # Col 4: Played With (Interactive Chips + Underlying Item)
            art_item = QTableWidgetItem("")
            art_item.setData(Qt.ItemDataRole.UserRole, rec_id)
            art_item.setFlags(cell_flags)
            self.table.setItem(row_idx, 4, art_item)

            cur_djs = parse_played_with(cur_art)
            dj_container = DJChipsContainer(parent=self.table, allow_custom=not is_deleted)
            dj_container.set_available_djs(self.get_all_available_djs())
            dj_container.set_djs(cur_djs)
            if is_deleted:
                dj_container.setEnabled(False)
            else:
                dj_container.djs_changed.connect(
                    lambda djs, r_id=rec_id, r_idx=row_idx: self._on_djs_changed(r_id, r_idx, djs)
                )
                dj_container.editing_state_changed.connect(
                    lambda _, c=dj_container: self._update_row_height_for_container(c)
                )
            self.table.setCellWidget(row_idx, 4, dj_container)
            self._update_row_height(row_idx)

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

        orig_name = record.get("name", "") if record else name
        is_del = bool(record.get("is_deleted", False)) if record else False
        is_staged = rec_id in self.staged_edits

        self.detail_view.load_session(
            self.engine,
            rec_id,
            session_title=name,
            session_date=date,
            has_prev=has_prev,
            has_next=has_next,
            db_path=getattr(self, "db_path", None),
            available_locations=self.get_all_available_locations(),
            available_djs=self.get_all_available_djs(),
            is_staged=is_staged,
            original_name=orig_name,
            is_deleted=is_del,
        )
        self.stack.setCurrentIndex(1)

    def _on_back_from_detail(self) -> None:
        """Return from detail view to main sessions list."""
        if not self.detail_view.confirm_unsaved_changes():
            return
        self.stack.setCurrentIndex(0)
        self.current_detail_id = None

    def _open_track_detail(self, track: Dict[str, Any], track_idx: int) -> None:
        """Open TrackDetailView for a track within the active history session."""
        if not self.engine or not track:
            return
        if not self.detail_view.confirm_unsaved_changes():
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
        self.detail_view.table.sync_saved_track(track)
        self.detail_view.select_track_at_index(idx)

    def confirm_unsaved_changes(self) -> bool:
        """Prompt user if there are unsaved changes in track detail view or session detail view."""
        if self.stack.currentIndex() == 2:
            return self.track_detail_view._confirm_unsaved_changes()
        elif self.stack.currentIndex() == 1:
            return self.detail_view.confirm_unsaved_changes()
        return True

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

        # If column 4 was changed, synchronize cell widget and read DJ list
        if column == 4:
            text_val = art_item.text().strip() if art_item else ""
            widget = self.table.cellWidget(row, 4)
            if text_val:
                new_art = text_val
                if isinstance(widget, DJChipsContainer):
                    widget.blockSignals(True)
                    widget.set_djs(parse_played_with(new_art))
                    widget.blockSignals(False)
            elif isinstance(widget, DJChipsContainer):
                new_art = compose_played_with(widget.get_djs())
            else:
                new_art = ""
        elif column in (1, 2, 3):
            widget = self.table.cellWidget(row, 4)
            if isinstance(widget, DJChipsContainer):
                new_art = compose_played_with(widget.get_djs())

        # Compose full name according to schema
        new_name = compose_history_name(new_base, location=new_loc, artist=new_art)

        if new_loc:
            self.register_new_location(new_loc)

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

    def _on_djs_changed(self, rec_id: str, row: int, new_djs: List[str]) -> None:
        """Handle DJ chips addition or removal in a row."""
        record = next((r for r in self.original_records if r["id"] == rec_id), None)
        if not record:
            return

        new_art = compose_played_with(new_djs)

        # Register any new DJ names in the global available pool
        for dj in new_djs:
            if dj not in self.all_available_djs:
                self.register_new_dj(dj)

        # Robustly locate row index
        row_idx = row
        if row_idx >= self.table.rowCount() or (
            self.table.item(row_idx, 1) and self.table.item(row_idx, 1).data(Qt.ItemDataRole.UserRole) != rec_id
        ):
            for i in range(self.table.rowCount()):
                item = self.table.item(i, 1) or self.table.item(i, 4)
                if item and item.data(Qt.ItemDataRole.UserRole) == rec_id:
                    row_idx = i
                    break

        # Synchronize underlying QTableWidgetItem text (delegate ensures it never renders underneath)
        art_item = self.table.item(row_idx, 4)
        if art_item:
            self.table.blockSignals(True)
            art_item.setText(new_art)
            self.table.blockSignals(False)

        date_item = self.table.item(row_idx, 1)
        name_item = self.table.item(row_idx, 2)
        loc_item = self.table.item(row_idx, 3)

        new_date = date_item.text().strip() if date_item else ""
        new_base = name_item.text().strip() if name_item else ""
        new_loc = loc_item.text().strip() if loc_item else ""

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
        status_item = self.table.item(row_idx, 0)
        if status_item:
            if is_staged:
                status_item.setText("● Staged")
                status_item.setForeground(Qt.GlobalColor.yellow)
            else:
                status_item.setText("Clean")
                status_item.setForeground(Qt.GlobalColor.gray)
        self.table.blockSignals(False)

        self._update_status_bar()
        self._update_row_height(row_idx)

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
            self._rebuild_all_available_djs()
            self._rebuild_all_available_locations()
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
        self.active_worker = worker
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
        self.active_worker = worker
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
        self.db_saved.emit()

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
        self.db_saved.emit()

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
        self.db_saved.emit()

    def _on_action_error(self, history_id: str, err_msg: str) -> None:
        self.notify_status.emit(f"❌ Error for session {history_id}: {err_msg}")
        QMessageBox.critical(self, "Operation Failed", f"Database error:\n{err_msg}")

    def _on_detail_metadata_updated(
        self,
        history_id: str,
        new_base: str,
        new_loc: str,
        new_art: str,
    ) -> None:
        """Handle base name, location and played-with DJ updates originating from HistoryDetailView."""
        record = next((r for r in self.original_records if str(r["id"]) == str(history_id)), None)
        if not record:
            return

        orig_name = record.get("name") or ""
        orig_date = record.get("date") or ""
        orig_base, orig_loc, orig_art = parse_history_name(orig_name)

        # Preserve any staged date
        if history_id in self.staged_edits:
            cur_date = self.staged_edits[history_id].get("date", orig_date)
        else:
            cur_date = orig_date

        cur_base = new_base or orig_base
        new_name = compose_history_name(cur_base, location=new_loc, artist=new_art)

        # Register any new location / DJs
        if new_loc:
            self.register_new_location(new_loc)
        if new_art:
            for dj in parse_played_with(new_art):
                self.register_new_dj(dj)

        if new_name != orig_name or cur_date != orig_date:
            self.staged_edits[history_id] = {
                "record": record,
                "name": new_name,
                "date": cur_date,
            }
            is_staged = True
        else:
            self.staged_edits.pop(history_id, None)
            is_staged = False

        # Synchronize table row
        self._sync_table_row_for_history(history_id, cur_base, new_loc, new_art, cur_date, is_staged)
        self._update_status_bar()

    def _sync_table_row_for_history(
        self,
        history_id: str,
        base_name: str,
        location: str,
        artist: str,
        date_str: str,
        is_staged: bool,
    ) -> None:
        """Update table cells for a session when modified from detail view."""
        row_idx = -1
        for i in range(self.table.rowCount()):
            cand = self.table.item(i, 1) or self.table.item(i, 4)
            if cand and str(cand.data(Qt.ItemDataRole.UserRole)) == str(history_id):
                row_idx = i
                break

        if row_idx < 0:
            return

        record = next((r for r in self.original_records if str(r["id"]) == str(history_id)), None)
        orig_name = record.get("name", "") if record else ""
        orig_date = record.get("date", "") if record else ""
        orig_base, orig_loc, orig_art = parse_history_name(orig_name)

        self.table.blockSignals(True)
        # Session Name
        name_item = self.table.item(row_idx, 2)
        if name_item:
            name_item.setText(base_name)
            name_item.setForeground(Qt.GlobalColor.yellow if (is_staged and base_name != orig_base) else Qt.GlobalColor.white)

        # Location
        loc_item = self.table.item(row_idx, 3)
        if loc_item:
            loc_item.setText(location)
            loc_item.setForeground(Qt.GlobalColor.yellow if (is_staged and location != orig_loc) else Qt.GlobalColor.white)

        # Played With
        art_item = self.table.item(row_idx, 4)
        if art_item:
            art_item.setText(artist)
            art_item.setForeground(Qt.GlobalColor.yellow if (is_staged and artist != orig_art) else Qt.GlobalColor.white)
        w = self.table.cellWidget(row_idx, 4)
        if isinstance(w, DJChipsContainer):
            w.blockSignals(True)
            w.set_djs(parse_played_with(artist))
            w.blockSignals(False)

        # Status cell
        status_item = self.table.item(row_idx, 0)
        if status_item:
            if is_staged:
                status_item.setText("● Staged")
                status_item.setForeground(Qt.GlobalColor.yellow)
            else:
                status_item.setText("Clean")
                status_item.setForeground(Qt.GlobalColor.gray)
        self.table.blockSignals(False)

    def _on_detail_discard_session(self, history_id: str) -> None:
        """Handle discard triggered from HistoryDetailView."""
        record = next((r for r in self.original_records if str(r["id"]) == str(history_id)), None)
        self.staged_edits.pop(history_id, None)
        if record:
            orig_name = record.get("name", "")
            orig_date = record.get("date", "")
            orig_base, orig_loc, orig_art = parse_history_name(orig_name)
            self._sync_table_row_for_history(history_id, orig_base, orig_loc, orig_art, orig_date, False)
        self._update_status_bar()

    def _on_detail_save_session(self, history_id: str) -> None:
        """Commit staged updates for a single session directly from HistoryDetailView."""
        if not self.engine:
            return

        if is_rekordbox_running():
            QMessageBox.warning(
                self,
                "Rekordbox is Running",
                "⚠️ Rekordbox is currently running!\n\n"
                "Saving changes now may cause database locking or corruption.\n"
                "Please close Rekordbox (Cmd + Q) and try again.",
                QMessageBox.StandardButton.Cancel,
            )
            return

        record = next((r for r in self.original_records if str(r["id"]) == str(history_id)), None)
        if not record:
            return

        is_staged = history_id in self.staged_edits
        new_name = self.staged_edits[history_id]["name"] if is_staged else record.get("name", "")
        new_date = self.staged_edits[history_id]["date"] if is_staged else record.get("date", "")

        updates = [{
            "record": record,
            "new_name": new_name,
            "new_date": new_date,
        }]

        self.notify_status.emit(f"Saving changes for session {history_id}...")

        worker = HistoryBatchSaveWorker(self.engine, updates, auto_realign=True, parent=self)
        self.active_worker = worker
        def on_success(stats: Dict[str, Any]) -> None:
            self.staged_edits.pop(history_id, None)
            record["name"] = new_name
            record["date"] = new_date
            b, l, a = parse_history_name(new_name)
            self._sync_table_row_for_history(history_id, b, l, a, new_date, False)
            self._update_status_bar()
            self._update_folder_tree()
            self.notify_status.emit("✅ Saved session changes successfully.")
            self.detail_view.original_base_name = b
            self.detail_view.base_name = b
            self.detail_view.original_location = l
            self.detail_view.original_artist = a
            self.detail_view.current_location = l
            self.detail_view.current_artist = a
            self.detail_view.is_staged = False
            self.detail_view.bottom_bar.setVisible(False)
            self.detail_view._render_title()
            QMessageBox.information(
                self,
                "Changes Saved",
                f"Session '{b}' updated successfully.",
            )
            self.request_reload.emit()
            self.db_saved.emit()

        def on_error(err_msg: str) -> None:
            self.notify_status.emit(f"❌ Save failed: {err_msg}")
            QMessageBox.critical(self, "Save Failed", f"Database error while saving changes:\n{err_msg}")

        worker.success.connect(on_success)
        worker.error.connect(on_error)
        worker.start()

    def cleanup(self) -> None:
        """Stop background history workers and timers cleanly."""
        if hasattr(self, "search_debounce_timer"):
            self.search_debounce_timer.stop()
        for attr in ("active_worker", "active_history_worker"):
            w = getattr(self, attr, None)
            if w:
                stop_worker(w, wait_ms=100)
                setattr(self, attr, None)
        if hasattr(self, "detail_view") and hasattr(self.detail_view, "cleanup"):
            self.detail_view.cleanup()

