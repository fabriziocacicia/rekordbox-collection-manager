"""3-Column Diff & Merge Dialog for Rekordbox History Sessions.

Presents a clear 3-column view:
- Left Column: Current History tracks in the active database.
- Center Column: External History tracks from the imported database (with Match/New/Missing badges).
- Right Column: Live Merged Result preview with interactive track inclusion checkboxes and chronological alignment.
"""

from typing import Any, Dict, List, Optional
from sqlalchemy.engine import Engine

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from rekordbox_collection_manager.gui.theme import (
    COLOR_AMBER,
    COLOR_AMBER_BG,
    COLOR_AMBER_BORDER,
    COLOR_BG_CARD,
    COLOR_BG_DARK,
    COLOR_BG_HOVER,
    COLOR_BG_INPUT,
    COLOR_BG_PANEL,
    COLOR_BLUE,
    COLOR_BLUE_BG,
    COLOR_BLUE_HOVER,
    COLOR_BORDER,
    COLOR_BORDER_LIGHT,
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
from rekordbox_collection_manager.history_import import (
    TrackDiffItem,
    calculate_history_session_diff,
    normalize_string,
)
from rekordbox_collection_manager.gui.track_search_dialog import TrackSearchDialog


class TableCheckBox(QPushButton):
    """Refined, compact checkable square button for table rows with native dark theme aesthetics."""

    toggled_state = Signal(bool)

    def __init__(self, checked: bool = False, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(16, 16)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setChecked(checked)
        self._update_style()
        self.toggled.connect(self._on_toggled)

    def _on_toggled(self, checked: bool) -> None:
        self._update_style()
        self.toggled_state.emit(checked)

    def _update_style(self) -> None:
        if self.isChecked():
            self.setText("✓")
            self.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR_BLUE};
                    color: #ffffff;
                    border: 1px solid #38bdf8;
                    border-radius: 3px;
                    font-size: 11px;
                    font-weight: 900;
                    padding: 0px;
                }}
                QPushButton:hover {{
                    background-color: {COLOR_BLUE_HOVER};
                    border-color: #7dd3fc;
                }}
                QPushButton:disabled {{
                    background-color: #1a324d;
                    color: #7aa7d6;
                    border: 1px solid #23486f;
                }}
            """)
        else:
            self.setText("")
            self.setStyleSheet(f"""
                QPushButton {{
                    background-color: #16161f;
                    color: transparent;
                    border: 1px solid #52586b;
                    border-radius: 3px;
                    padding: 0px;
                }}
                QPushButton:hover {{
                    border-color: {COLOR_BLUE};
                    background-color: #1f2330;
                }}
                QPushButton:disabled {{
                    background-color: #141418;
                    border-color: #2b2e38;
                }}
            """)


class CheckBoxCellWidget(QWidget):
    """Full-cell clickable container for table checkboxes ensuring frictionless toggling anywhere in the cell."""

    def __init__(self, checkbox: TableCheckBox, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.checkbox = checkbox
        self.checkbox.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCursor(
            Qt.CursorShape.PointingHandCursor if checkbox.isEnabled() else Qt.CursorShape.ArrowCursor
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.checkbox)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.checkbox.isEnabled():
            self.checkbox.toggle()
            event.accept()
        else:
            super().mousePressEvent(event)


class StatusBadge(QLabel):
    """Refined flat status badge for track states with optional click-to-search functionality."""

    clicked = Signal()

    def __init__(
        self,
        text: str,
        fg_color: str,
        clickable: bool = False,
    ):
        super().__init__(text)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(22)
        self.clickable = clickable
        if clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(f"""
            QLabel {{
                background-color: transparent;
                color: {fg_color};
                border: 1px solid transparent;
                border-radius: 4px;
                padding: 1px 6px;
                font-size: 11px;
                font-weight: 600;
            }}
            {"QLabel:hover { background-color: rgba(255, 255, 255, 0.08); border-color: rgba(255, 255, 255, 0.18); }" if clickable else ""}
        """)

    def mousePressEvent(self, event) -> None:
        if self.clickable and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
        else:
            super().mousePressEvent(event)


def make_badge_for_status(
    status: str, is_manual: bool = False, clickable: bool = False
) -> StatusBadge:
    """Create clean flat status label without heavy background pills."""
    if status == "matched":
        if is_manual:
            return StatusBadge("🔗 Match", COLOR_GREEN, clickable=clickable)
        return StatusBadge("🟢 Match", COLOR_GREEN, clickable=clickable)
    elif status == "new":
        return StatusBadge("🔵 Nuova", COLOR_BLUE, clickable=clickable)
    elif status == "missing_file":
        return StatusBadge("🔍 Mancante", COLOR_AMBER, clickable=clickable)
    elif status == "current_only":
        return StatusBadge("⚪ Attuale", COLOR_TEXT_SECONDARY)
    return StatusBadge(status, COLOR_TEXT_MUTED)


class HistoryDiffDialog(QDialog):
    """3-Column History Session Diff and Merge Dialog."""

    # Emitted with list of selected tracks to insert when user confirms merge
    merge_confirmed = Signal(list)

    def __init__(
        self,
        session_name: str,
        session_date: str,
        current_tracks: List[Dict[str, Any]],
        external_tracks: List[Dict[str, Any]],
        current_track_index: Dict[str, Any],
        parent: Optional[QWidget] = None,
        current_engine: Optional[Engine] = None,
    ):
        super().__init__(parent)
        self.session_name = session_name
        self.session_date = session_date
        self.current_tracks = current_tracks
        self.external_tracks = external_tracks
        self.current_track_index = current_track_index
        self.current_engine = current_engine

        self.diff_data = calculate_history_session_diff(
            current_tracks, external_tracks, current_track_index
        )

        self.setWindowTitle(f"History Diff & Merge: {self.session_name}")

        screen = QApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            w = min(1560, max(1200, int(avail.width() * 0.94)))
            h = min(860, max(650, int(avail.height() * 0.88)))
            self.resize(w, h)
        else:
            self.resize(1520, 840)
        self.setMinimumSize(1150, 600)
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {COLOR_BG_DARK};
                color: {COLOR_TEXT_PRIMARY};
            }}
            QToolTip, QTipLabel {{
                background-color: #1a1e2b;
                color: #f8fafc;
                border: 1px solid #3b4255;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 11px;
                font-weight: 500;
                opacity: 255;
            }}
        """)

        self._init_ui()
        self._populate_tables()
        self._update_stats_display()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 12, 14, 12)
        main_layout.setSpacing(12)

        # 1. Header Bar
        header = QFrame()
        header.setStyleSheet(f"background-color: {COLOR_BG_PANEL}; border: 1px solid {COLOR_BORDER}; border-radius: 8px; padding: 10px 14px;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(4, 4, 4, 4)

        title_box = QVBoxLayout()
        title_box.setSpacing(3)
        self.title_label = QLabel(f"🔀 Diff Sessione: {self.session_name}")
        self.title_label.setStyleSheet("font-size: 16px; font-weight: 700; color: #ffffff;")
        self.subtitle_label = QLabel(f"Data Sessione: {self.session_date or 'N/D'}")
        self.subtitle_label.setStyleSheet(f"font-size: 12px; color: {COLOR_TEXT_SECONDARY};")
        title_box.addWidget(self.title_label)
        title_box.addWidget(self.subtitle_label)
        header_layout.addLayout(title_box, stretch=1)

        # Stats Chips
        self.stats_layout = QHBoxLayout()
        self.stats_layout.setSpacing(8)

        self.chip_matched = QLabel("🟢 0 Match")
        self.chip_matched.setStyleSheet(f"background-color: {COLOR_GREEN_BG}; color: {COLOR_GREEN}; border: 1px solid {COLOR_GREEN_BORDER}; border-radius: 6px; padding: 4px 10px; font-weight: 600; font-size: 11px;")
        self.stats_layout.addWidget(self.chip_matched)

        self.chip_new = QLabel("🔵 0 Nuove")
        self.chip_new.setStyleSheet(f"background-color: {COLOR_BLUE_BG}; color: {COLOR_BLUE}; border: 1px solid #38bdf8; border-radius: 6px; padding: 4px 10px; font-weight: 600; font-size: 11px;")
        self.stats_layout.addWidget(self.chip_new)

        self.chip_missing = QLabel("🟡 0 File Mancanti")
        self.chip_missing.setStyleSheet(f"background-color: {COLOR_AMBER_BG}; color: {COLOR_AMBER}; border: 1px solid {COLOR_AMBER_BORDER}; border-radius: 6px; padding: 4px 10px; font-weight: 600; font-size: 11px;")
        self.stats_layout.addWidget(self.chip_missing)

        self.chip_total = QLabel("⚪ Risultato: 0 tracce")
        self.chip_total.setStyleSheet(f"background-color: {COLOR_BG_CARD}; color: {COLOR_TEXT_PRIMARY}; border: 1px solid {COLOR_BORDER}; border-radius: 6px; padding: 4px 10px; font-weight: 700; font-size: 11px;")
        self.stats_layout.addWidget(self.chip_total)

        header_layout.addLayout(self.stats_layout)
        main_layout.addWidget(header)

        # 2. 3-Column Splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet(f"QSplitter::handle {{ background-color: {COLOR_BORDER}; width: 2px; }}")

        # Column 1: Current
        col1 = self._build_column_panel(
            title="1. History Attuale (DB Corrente)",
            badge_text=f"{len(self.current_tracks)} tracce",
            badge_color=COLOR_TEXT_SECONDARY,
        )
        self.cur_table = self._build_table(is_merged=False)
        col1.layout().addWidget(self.cur_table)
        splitter.addWidget(col1)

        # Column 2: External
        col2 = self._build_column_panel(
            title="2. Da Importare (Vecchio DB)",
            badge_text=f"{len(self.external_tracks)} tracce",
            badge_color=COLOR_BLUE,
        )
        self.ext_table = self._build_table(is_merged=False)
        col2.layout().addWidget(self.ext_table)
        splitter.addWidget(col2)

        # Column 3: Merged
        col3 = self._build_column_panel(
            title="3. Risultato Finale (Merged Preview)",
            badge_text="Anteprima unione",
            badge_color=COLOR_GREEN,
        )
        self.merged_table = self._build_table(is_merged=True)
        self.merged_table.cellClicked.connect(self._on_merged_cell_clicked)
        self.merged_table.horizontalHeader().sectionClicked.connect(self._on_merged_header_clicked)
        self.merged_table.horizontalHeader().setToolTip("Clicca sulla colonna '✓' per selezionare o deselezionare tutte le nuove tracce")
        col3.layout().addWidget(self.merged_table)
        splitter.addWidget(col3)

        col1.setMinimumWidth(370)
        col2.setMinimumWidth(370)
        col3.setMinimumWidth(400)
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        splitter.setCollapsible(2, False)
        splitter.setSizes([480, 480, 520])
        main_layout.addWidget(splitter, stretch=1)

        # 3. Bottom Controls & Action Bar
        bottom_bar = QFrame()
        bottom_bar.setStyleSheet(f"background-color: {COLOR_BG_PANEL}; border: 1px solid {COLOR_BORDER}; border-radius: 8px; padding: 8px 12px;")
        bottom_layout = QHBoxLayout(bottom_bar)
        bottom_layout.setContentsMargins(4, 4, 4, 4)

        # Quick toggles
        self.btn_select_all = QPushButton("Seleziona Tutte le Nuove")
        self.btn_select_all.setStyleSheet(f"background-color: {COLOR_BG_CARD}; color: {COLOR_TEXT_PRIMARY}; border: 1px solid {COLOR_BORDER}; border-radius: 5px; padding: 6px 12px; font-size: 11px;")
        self.btn_select_all.clicked.connect(lambda: self._set_all_new_included(True))
        bottom_layout.addWidget(self.btn_select_all)

        self.btn_deselect_all = QPushButton("Deseleziona Nuove")
        self.btn_deselect_all.setStyleSheet(f"background-color: {COLOR_BG_CARD}; color: {COLOR_TEXT_PRIMARY}; border: 1px solid {COLOR_BORDER}; border-radius: 5px; padding: 6px 12px; font-size: 11px;")
        self.btn_deselect_all.clicked.connect(lambda: self._set_all_new_included(False))
        bottom_layout.addWidget(self.btn_deselect_all)

        bottom_layout.addStretch(1)

        self.btn_cancel = QPushButton("Annulla")
        self.btn_cancel.setStyleSheet(f"background-color: {COLOR_BG_CARD}; color: {COLOR_TEXT_SECONDARY}; border: 1px solid {COLOR_BORDER}; border-radius: 6px; padding: 8px 18px; font-size: 12px;")
        self.btn_cancel.clicked.connect(self.reject)
        bottom_layout.addWidget(self.btn_cancel)

        self.btn_apply = QPushButton("⚡ Applica Questo Merge")
        self.btn_apply.setStyleSheet(f"background-color: {COLOR_GREEN}; color: #ffffff; border: none; border-radius: 6px; padding: 8px 22px; font-size: 12px; font-weight: 700;")
        self.btn_apply.clicked.connect(self._on_apply_clicked)
        bottom_layout.addWidget(self.btn_apply)

        main_layout.addWidget(bottom_bar)

    def _build_column_panel(self, title: str, badge_text: str, badge_color: str) -> QWidget:
        panel = QWidget()
        panel.setStyleSheet("background: transparent;")
        p_layout = QVBoxLayout(panel)
        p_layout.setContentsMargins(0, 0, 0, 0)
        p_layout.setSpacing(6)

        head = QHBoxLayout()
        head.setContentsMargins(4, 2, 4, 2)
        lbl = QLabel(title)
        lbl.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {COLOR_TEXT_PRIMARY};")
        head.addWidget(lbl)

        badge = QLabel(badge_text)
        badge.setStyleSheet(f"color: {badge_color}; font-size: 11px; font-weight: 600; padding: 2px 4px;")
        head.addWidget(badge)
        head.addStretch(1)

        p_layout.addLayout(head)
        return panel

    def _build_table(self, is_merged: bool) -> QTableWidget:
        table = QTableWidget()
        headers = ["✓", "#", "Ora", "Titolo", "Artista", "Durata", "Stato"] if is_merged else ["#", "Ora", "Titolo", "Artista", "Durata", "Stato"]
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(32)
        table.setShowGrid(False)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)

        tooltips = {
            "✓": "Seleziona/deseleziona tutte le nuove tracce da includere",
            "#": "Posizione traccia",
            "Ora": "Orario di esecuzione",
            "Titolo": "Titolo del brano",
            "Artista": "Artista",
            "Durata": "Durata del brano",
            "Stato": "Stato confronto",
        }
        for col_idx in range(table.columnCount()):
            h_item = table.horizontalHeaderItem(col_idx)
            if h_item and h_item.text() in tooltips:
                h_item.setToolTip(tooltips[h_item.text()])

        table.setStyleSheet(f"""
            QTableWidget {{
                background-color: {COLOR_BG_DARK};
                alternate-background-color: {COLOR_BG_PANEL};
                color: {COLOR_TEXT_PRIMARY};
                border: 1px solid {COLOR_BORDER};
                border-radius: 6px;
                gridline-color: transparent;
                font-size: 11px;
            }}
            QTableWidget::item {{
                padding: 2px 4px;
                border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            }}
            QTableWidget::item:selected {{
                background-color: {COLOR_BLUE_BG};
                color: #ffffff;
            }}
            QHeaderView::section {{
                background-color: {COLOR_BG_PANEL};
                color: {COLOR_TEXT_SECONDARY};
                font-size: 11px;
                font-weight: 600;
                padding: 4px 2px;
                border: none;
                border-bottom: 1px solid {COLOR_BORDER};
                border-right: 1px solid rgba(255, 255, 255, 0.04);
            }}
            QToolTip, QTipLabel {{
                background-color: #1a1e2b;
                color: #f8fafc;
                border: 1px solid #3b4255;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 11px;
                font-weight: 500;
                opacity: 255;
            }}
        """)

        header = table.horizontalHeader()
        header.setHighlightSections(False)
        if is_merged:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(0, 32)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(1, 28)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(2, 70)
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
            table.setColumnWidth(4, 105)
            header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(5, 60)
            header.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(6, 106)
        else:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(0, 28)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(1, 70)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
            table.setColumnWidth(3, 105)
            header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(4, 60)
            header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(5, 106)

        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        table.customContextMenuRequested.connect(
            lambda pos, t=table, is_m=is_merged: self._on_table_context_menu(pos, t, is_m)
        )
        table.cellDoubleClicked.connect(
            lambda r, c, is_m=is_merged: self._on_table_double_clicked(r, c, is_m)
        )

        return table

    def _populate_tables(self) -> None:
        # 1. Populate Current
        cur_items = self.diff_data["current_column"]
        self.cur_table.setRowCount(len(cur_items))
        for r, item in enumerate(cur_items):
            self._set_table_row(self.cur_table, r, item, is_merged=False)

        # 2. Populate External
        ext_items = self.diff_data["external_column"]
        self.ext_table.setRowCount(len(ext_items))
        for r, item in enumerate(ext_items):
            self._set_table_row(self.ext_table, r, item, is_merged=False)

        # 3. Populate Merged
        self._refresh_merged_table()

    def _set_table_row(
        self, table: QTableWidget, row: int, item: TrackDiffItem, is_merged: bool
    ) -> None:
        col_offset = 1 if is_merged else 0

        if is_merged:
            cb = TableCheckBox(checked=item.included)
            # If current item, always checked and disabled
            if item.origin == "current":
                cb.setEnabled(False)
                cb.setToolTip("Traccia già presente nel DB attuale (fissata)")
            else:
                cb.toggled_state.connect(lambda checked, it=item: self._on_item_toggled(it, checked))
            cell_widget = CheckBoxCellWidget(cb)
            table.setCellWidget(row, 0, cell_widget)

        # Track No
        t_no = QTableWidgetItem(str(item.track_no))
        t_no.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        table.setItem(row, 0 + col_offset, t_no)

        # Time
        t_time = QTableWidgetItem(item.played_time or "--:--")
        t_time.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        if item.played_time:
            t_time.setToolTip(f"Orario riproduzione: {item.played_time}")
        table.setItem(row, 1 + col_offset, t_time)

        # Title
        t_title = QTableWidgetItem(item.title)
        t_title.setToolTip(item.title)
        table.setItem(row, 2 + col_offset, t_title)

        # Artist
        t_artist = QTableWidgetItem(item.artist)
        t_artist.setToolTip(item.artist)
        table.setItem(row, 3 + col_offset, t_artist)

        # Duration
        t_dur = QTableWidgetItem(item.duration_str)
        t_dur.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        table.setItem(row, 4 + col_offset, t_dur)

        # Status badge
        is_manual = getattr(item, "is_manual_match", False)
        can_search = (item.origin == "external" and self.current_engine is not None)
        badge = make_badge_for_status(item.status, is_manual=is_manual, clickable=can_search)
        if can_search:
            badge.clicked.connect(lambda it=item: self._open_track_search(it))

        if item.status == "missing_file":
            badge.setToolTip(
                "File audio non presente nella collezione locale.\n👉 Clicca qui (o tasto destro) per cercare e associare a una traccia della tua collezione."
            )
        elif item.status == "new":
            badge.setToolTip(
                "Nuova traccia.\n👉 Clicca qui (o tasto destro) se desideri invece associarla a una traccia già presente nella collezione."
            )
        elif item.status == "matched":
            if is_manual:
                badge.setToolTip(
                    "Traccia associata manualmente.\n👉 Clicca per modificare o usa il tasto destro per rimuovere l'associazione."
                )
            else:
                badge.setToolTip(
                    "Match trovato con la collezione attuale.\n👉 Clicca o tasto destro per cambiare associazione."
                )

        badge_holder = QWidget()
        badge_holder.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        b_layout = QHBoxLayout(badge_holder)
        b_layout.setContentsMargins(2, 2, 2, 2)
        b_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        b_layout.addWidget(badge)
        if badge.toolTip():
            badge_holder.setToolTip(badge.toolTip())
        table.setCellWidget(row, 5 + col_offset, badge_holder)

    def _open_track_search(self, item: TrackDiffItem) -> None:
        if not self.current_engine:
            return

        dur_sec = 0
        if item.duration_str and ":" in item.duration_str:
            try:
                parts = item.duration_str.split(":")
                dur_sec = int(parts[0]) * 60 + int(parts[1])
            except Exception:
                dur_sec = 0

        dlg = TrackSearchDialog(
            current_engine=self.current_engine,
            target_title=item.title,
            target_artist=item.artist,
            target_duration_str=item.duration_str,
            target_duration_sec=dur_sec,
            parent=self,
        )

        def _on_track_picked(track: Dict[str, Any]) -> None:
            item.matched_content_id = track["id"]
            item.status = "matched"
            item.included = True
            setattr(item, "is_manual_match", True)

            # Sync matching item between external_column and merged_column
            for other_item in self.diff_data.get("merged_column", []):
                if other_item.track_no == item.track_no and other_item.origin == "external":
                    other_item.matched_content_id = track["id"]
                    other_item.status = "matched"
                    other_item.included = True
                    setattr(other_item, "is_manual_match", True)
            for other_item in self.diff_data.get("external_column", []):
                if other_item.track_no == item.track_no:
                    other_item.matched_content_id = track["id"]
                    other_item.status = "matched"
                    setattr(other_item, "is_manual_match", True)

            # Register in in-memory index for future matching
            if self.current_track_index:
                nt = normalize_string(item.title)
                na = normalize_string(item.artist)
                if nt:
                    self.current_track_index["by_title_artist"][(nt, na)] = track["id"]
                if track.get("file_name"):
                    self.current_track_index["by_filename"][track["file_name"].lower()] = track["id"]

            self._populate_tables()
            self._update_stats_display()

        dlg.track_selected.connect(_on_track_picked)
        dlg.exec()

    def _unlink_track(self, item: TrackDiffItem) -> None:
        item.matched_content_id = None
        item.status = "missing_file"
        setattr(item, "is_manual_match", False)

        for other_item in self.diff_data.get("merged_column", []):
            if other_item.track_no == item.track_no and other_item.origin == "external":
                other_item.matched_content_id = None
                other_item.status = "missing_file"
                setattr(other_item, "is_manual_match", False)
        for other_item in self.diff_data.get("external_column", []):
            if other_item.track_no == item.track_no:
                other_item.matched_content_id = None
                other_item.status = "missing_file"
                setattr(other_item, "is_manual_match", False)

        self._populate_tables()
        self._update_stats_display()

    def _on_table_context_menu(self, pos, table: QTableWidget, is_merged: bool) -> None:
        if not self.current_engine:
            return
        row = table.rowAt(pos.y())
        if row < 0:
            return

        items = self.diff_data["merged_column"] if is_merged else (
            self.diff_data["external_column"] if table == self.ext_table else self.diff_data["current_column"]
        )
        if row >= len(items):
            return
        item = items[row]

        if item.origin != "external":
            return

        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {COLOR_BG_CARD};
                color: {COLOR_TEXT_PRIMARY};
                border: 1px solid {COLOR_BORDER};
                border-radius: 6px;
                padding: 4px;
                font-size: 11px;
            }}
            QMenu::item {{
                padding: 6px 14px;
                border-radius: 4px;
            }}
            QMenu::item:selected {{
                background-color: {COLOR_BLUE_BG};
                color: #ffffff;
            }}
        """)

        act_search = menu.addAction("🔍 Cerca e Associa a Traccia della Collezione...")
        act_search.triggered.connect(lambda: self._open_track_search(item))

        if item.matched_content_id:
            act_unlink = menu.addAction("❌ Rimuovi Associazione")
            act_unlink.triggered.connect(lambda: self._unlink_track(item))

        menu.exec(table.viewport().mapToGlobal(pos))

    def _on_table_double_clicked(self, row: int, column: int, is_merged: bool) -> None:
        if is_merged and column == 0:
            return
        if not self.current_engine:
            return
        items = self.diff_data["merged_column"] if is_merged else self.diff_data["external_column"]
        if 0 <= row < len(items):
            item = items[row]
            if item.origin == "external":
                self._open_track_search(item)

    def _refresh_merged_table(self) -> None:
        merged_items = self.diff_data["merged_column"]
        self.merged_table.setRowCount(len(merged_items))
        for r, item in enumerate(merged_items):
            self._set_table_row(self.merged_table, r, item, is_merged=True)

    def _on_item_toggled(self, item: TrackDiffItem, checked: bool) -> None:
        item.included = checked
        self._update_stats_display()

    def _set_all_new_included(self, included: bool) -> None:
        for r, item in enumerate(self.diff_data["merged_column"]):
            if item.origin == "external":
                item.included = included
                holder = self.merged_table.cellWidget(r, 0)
                if holder:
                    btn = holder.findChild(TableCheckBox)
                    if btn:
                        btn.setChecked(included)
        self._update_stats_display()

    def _on_merged_header_clicked(self, logical_index: int) -> None:
        if logical_index == 0:
            has_unselected = any(
                it.origin == "external" and not it.included
                for it in self.diff_data["merged_column"]
            )
            self._set_all_new_included(has_unselected)

    def _on_merged_cell_clicked(self, row: int, column: int) -> None:
        pass

    def _update_stats_display(self) -> None:
        merged_items = self.diff_data.get("merged_column", [])
        matched = sum(1 for it in merged_items if it.status == "matched")
        new_cnt = sum(1 for it in merged_items if it.status == "new")
        missing = sum(1 for it in merged_items if it.status == "missing_file")

        self.chip_matched.setText(f"🟢 {matched} Match")
        self.chip_new.setText(f"🔵 {new_cnt} Nuove")
        self.chip_missing.setText(f"🟡 {missing} File Mancanti")

        active_count = sum(1 for it in merged_items if it.included)
        self.chip_total.setText(f"⚪ Risultato: {active_count} tracce")

    def _on_apply_clicked(self) -> None:
        selected_to_insert = [
            it for it in self.diff_data["merged_column"]
            if it.origin == "external" and it.included
        ]
        self.merge_confirmed.emit(selected_to_insert)
        self.accept()
