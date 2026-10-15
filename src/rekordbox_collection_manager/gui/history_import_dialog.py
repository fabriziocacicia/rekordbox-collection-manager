"""Modal dialog for importing and merging external Rekordbox history databases.

Features:
- Drag & Drop zone and native file browser for external master.db files.
- Non-blocking background analysis of external sessions against the active database.
- Segmented category filters:
  - 🟡 Da Unire (To Merge): Sessions active in current DB but missing tracks.
  - 🗑️ Nel Cestino (In Trash): Sessions soft-deleted in current DB (rb_local_deleted = 1).
  - 🟢 Nuove (New): Sessions completely missing from current DB.
  - ⚪ Identiche (Identical): Already synchronized sessions.
- Direct invocation of the 3-column HistoryDiffDialog for granular session review.
- Safe batch merge / restore execution with automated pre-merge backup.
"""

import os
from typing import Any, Dict, List, Optional, Set
from PySide6.QtCore import QEvent, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.engine import Engine

from rekordbox_collection_manager.gui.history_diff_dialog import (
    CheckBoxCellWidget,
    HistoryDiffDialog,
    TableCheckBox,
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
    COLOR_BORDER_FOCUS,
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
    FONT_FAMILY_MONO,
)
from rekordbox_collection_manager.gui.workers import (
    ExternalHistoryAnalyzeWorker,
    ExternalHistoryMergeWorker,
    stop_worker,
)
from rekordbox_collection_manager.history_import import (
    ExternalHistoryAnalysis,
    HistorySessionAnalysis,
    TrackDiffItem,
    build_current_track_index,
    calculate_history_session_diff,
    fetch_current_session_tracks,
    fetch_external_session_tracks,
)
from rekordbox_collection_manager.utils import format_file_size, is_rekordbox_running, open_database


class DropZoneWidget(QFrame):
    """Interactive drag & drop zone for external Rekordbox master.db files."""

    file_dropped = Signal(str)
    clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMinimumHeight(135)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("dropZone")
        self._set_normal_style()

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(6)

        self.icon_label = QLabel("📥")
        self.icon_label.setStyleSheet("font-size: 32px; background: transparent; border: none;")
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.icon_label)

        self.text_label = QLabel(
            "Trascina qui il file <b>master.db</b> oppure clicca per sfogliare"
        )
        self.text_label.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-size: 13px; background: transparent; border: none;")
        self.text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.text_label)

        self.hint_label = QLabel("Supporta file database Rekordbox 6 e 7 esportati o locali")
        self.hint_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px; background: transparent; border: none;")
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.hint_label)

    def _set_normal_style(self) -> None:
        self.setStyleSheet(f"""
            QFrame#dropZone {{
                background-color: {COLOR_BG_CARD};
                border: 2px dashed {COLOR_BORDER};
                border-radius: 8px;
            }}
            QFrame#dropZone:hover {{
                border-color: {COLOR_BLUE};
                background-color: {COLOR_BG_HOVER};
            }}
        """)

    def _set_drag_active_style(self) -> None:
        self.setStyleSheet(f"""
            QFrame#dropZone {{
                background-color: {COLOR_BLUE_BG};
                border: 2px dashed {COLOR_BLUE};
                border-radius: 8px;
            }}
        """)

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                fn = url.toLocalFile()
                if fn.lower().endswith(".db") or "master" in fn.lower():
                    event.acceptProposedAction()
                    self._set_drag_active_style()
                    return
        event.ignore()

    def dragLeaveEvent(self, event: Any) -> None:
        self._set_normal_style()
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:
        self._set_normal_style()
        for url in event.mimeData().urls():
            fn = url.toLocalFile()
            if os.path.isfile(fn):
                event.acceptProposedAction()
                self.file_dropped.emit(fn)
                return
        event.ignore()


class HistoryImportDialog(QDialog):
    """Main wizard dialog for importing and merging external Rekordbox history sessions."""

    db_updated = Signal()

    def __init__(
        self,
        current_engine: Engine,
        current_db_path: str,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.current_engine = current_engine
        self.current_db_path = current_db_path

        self.external_db_path: Optional[str] = None
        self.external_engine: Optional[Engine] = None
        self.analysis_data: Optional[ExternalHistoryAnalysis] = None
        self.current_track_index: Optional[Dict[str, Any]] = None

        self.current_category_filter = "to_merge"  # "to_merge", "in_trash", "new", "identical"
        self.selected_session_ids: Set[str] = set()

        self.active_worker: Optional[Any] = None

        self.setWindowTitle("Importa & Unisci History da Database Esterno")
        self.resize(760, 400)
        self.setMinimumSize(660, 340)
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {COLOR_BG_DARK};
                color: {COLOR_TEXT_PRIMARY};
            }}
        """)

        self._init_ui()

    def _init_ui(self) -> None:
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(20, 16, 20, 16)
        self.main_layout.setSpacing(14)

        # 1. Header
        header = QFrame()
        header.setObjectName("importHeader")
        header.setStyleSheet(
            f"QFrame#importHeader {{ background-color: {COLOR_BG_PANEL}; border: 1px solid {COLOR_BORDER}; border-radius: 8px; padding: 10px 16px; }}"
        )
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(0, 0, 0, 0)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title_lbl = QLabel("Importa & Unisci History da Database Esterno")
        title_lbl.setStyleSheet("font-size: 15px; font-weight: 700; color: #ffffff; background: transparent; border: none;")
        sub_lbl = QLabel(
            "Confronta e unisci le sessioni storiche con la libreria attuale, con recupero dal cestino e visualizzazione diff."
        )
        sub_lbl.setStyleSheet(f"font-size: 11px; color: {COLOR_TEXT_SECONDARY}; background: transparent; border: none;")
        title_box.addWidget(title_lbl)
        title_box.addWidget(sub_lbl)
        h_layout.addLayout(title_box, stretch=1)

        self.db_status_badge = QLabel("Pronto per l'importazione")
        self.db_status_badge.setStyleSheet(
            f"background-color: {COLOR_BG_CARD}; color: {COLOR_TEXT_MUTED}; border: 1px solid {COLOR_BORDER}; border-radius: 4px; padding: 4px 10px; font-size: 11px; font-weight: 500;"
        )
        h_layout.addWidget(self.db_status_badge)

        self.main_layout.addWidget(header)

        # 2. File Selection Zone
        self.file_frame = QFrame()
        self.file_frame.setObjectName("importFileFrame")
        self.file_frame.setStyleSheet(
            f"QFrame#importFileFrame {{ background-color: {COLOR_BG_PANEL}; border: 1px solid {COLOR_BORDER}; border-radius: 8px; padding: 10px 14px; }}"
        )
        file_layout = QVBoxLayout(self.file_frame)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.setSpacing(8)

        self.drop_zone = DropZoneWidget(self)
        self.drop_zone.file_dropped.connect(self._load_external_file)
        self.drop_zone.clicked.connect(self._browse_file)
        file_layout.addWidget(self.drop_zone)

        browse_row = QHBoxLayout()
        browse_row.setSpacing(12)
        browse_row.setContentsMargins(4, 2, 4, 2)
        self.file_path_label = QLabel("Trascina qui il file master.db oppure usa il pulsante Sfoglia.")
        self.file_path_label.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-size: 12px; background: transparent; border: none;")
        browse_row.addWidget(self.file_path_label, stretch=1)

        self.btn_browse = QPushButton("📂 Sfoglia Database...")
        self.btn_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_browse.setStyleSheet(
            f"background-color: {COLOR_BG_CARD}; color: {COLOR_TEXT_PRIMARY}; border: 1px solid {COLOR_BORDER}; border-radius: 5px; padding: 6px 14px; font-size: 11px; font-weight: 600;"
        )
        self.btn_browse.clicked.connect(self._browse_file)
        browse_row.addWidget(self.btn_browse)

        file_layout.addLayout(browse_row)
        self.main_layout.addWidget(self.file_frame, stretch=1)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # indeterminate
        self.progress_bar.setFixedHeight(3)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet(f"QProgressBar {{ background-color: {COLOR_BG_CARD}; border: none; }} QProgressBar::chunk {{ background-color: {COLOR_BLUE}; }}")
        self.progress_bar.setVisible(False)
        self.main_layout.addWidget(self.progress_bar)

        # 3. Overview & Category Filter Tabs
        self.overview_container = QWidget()
        ov_layout = QVBoxLayout(self.overview_container)
        ov_layout.setContentsMargins(0, 0, 0, 0)
        ov_layout.setSpacing(8)

        # Filter bar
        filter_bar = QHBoxLayout()
        filter_bar.setSpacing(6)

        self.btn_tab_merge = QPushButton("🟡 Da Unire (0)")
        self.btn_tab_merge.setCheckable(True)
        self.btn_tab_merge.setChecked(True)
        self.btn_tab_merge.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_tab_merge.clicked.connect(lambda: self._set_category_filter("to_merge"))
        filter_bar.addWidget(self.btn_tab_merge)

        self.btn_tab_trash = QPushButton("🗑️ Nel Cestino (0)")
        self.btn_tab_trash.setCheckable(True)
        self.btn_tab_trash.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_tab_trash.clicked.connect(lambda: self._set_category_filter("in_trash"))
        filter_bar.addWidget(self.btn_tab_trash)

        self.btn_tab_new = QPushButton("🟢 Nuove (0)")
        self.btn_tab_new.setCheckable(True)
        self.btn_tab_new.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_tab_new.clicked.connect(lambda: self._set_category_filter("new"))
        filter_bar.addWidget(self.btn_tab_new)

        self.btn_tab_identical = QPushButton("⚪ Identiche (0)")
        self.btn_tab_identical.setCheckable(True)
        self.btn_tab_identical.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_tab_identical.clicked.connect(lambda: self._set_category_filter("identical"))
        filter_bar.addWidget(self.btn_tab_identical)

        filter_bar.addStretch(1)

        # Search bar
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Cerca per data o nome...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setFixedWidth(240)
        self.search_input.setStyleSheet(
            f"QLineEdit {{ background-color: {COLOR_BG_INPUT}; color: {COLOR_TEXT_PRIMARY}; border: 1px solid {COLOR_BORDER}; border-radius: 5px; padding: 5px 8px; font-size: 11px; }}"
        )
        self.search_input.textChanged.connect(self._filter_table_rows)
        filter_bar.addWidget(self.search_input)

        ov_layout.addLayout(filter_bar)
        self._update_tab_styles()

        # Sessions Table
        self.table = QTableWidget()
        headers = ["✓", "DATA", "SESSIONE", "TRACCE VECCHIE", "TRACCE ATTUALI", "STATO", "AZIONI"]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)

        self.table.setStyleSheet(f"""
            QTableWidget {{
                background-color: {COLOR_BG_CARD};
                alternate-background-color: {COLOR_BG_PANEL};
                color: {COLOR_TEXT_PRIMARY};
                border: 1px solid {COLOR_BORDER};
                border-radius: 6px;
                gridline-color: transparent;
                font-size: 12px;
            }}
            QTableWidget::item {{
                padding: 2px 6px;
                border-bottom: 1px solid rgba(255, 255, 255, 0.04);
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
                padding: 5px 4px;
                border: none;
                border-bottom: 1px solid {COLOR_BORDER};
            }}
        """)

        th = self.table.horizontalHeader()
        th.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 40)
        th.setCursor(Qt.CursorShape.PointingHandCursor)
        th.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(1, 145)
        th.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        th.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(3, 115)
        th.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(4, 115)
        th.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(5, 145)
        th.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(6, 160)

        th.sectionClicked.connect(self._on_header_section_clicked)
        th.setToolTip("Clicca sulla colonna '✓' per selezionare o deselezionare tutte le sessioni visibili (oppure usa Cmd+A / Spazio)")

        # Keyboard shortcuts & event filter on table
        self.shortcut_select_all = QShortcut(QKeySequence.SelectAll, self.table)
        self.shortcut_select_all.activated.connect(lambda: self._select_all_visible(True))
        self.table.installEventFilter(self)

        ov_layout.addWidget(self.table, stretch=1)
        self.overview_container.setVisible(False)
        self.main_layout.addWidget(self.overview_container)

        # 4. Global Settings & Actions Area (shown only after database is loaded and analyzed)
        self.bottom_frame = QFrame()
        self.bottom_frame.setObjectName("bottomActionsFrame")
        self.bottom_frame.setStyleSheet(f"""
            QFrame#bottomActionsFrame {{
                background-color: {COLOR_BG_PANEL};
                border: 1px solid {COLOR_BORDER};
                border-radius: 8px;
                padding: 10px 14px;
            }}
        """)
        b_layout = QVBoxLayout(self.bottom_frame)
        b_layout.setContentsMargins(0, 0, 0, 0)
        b_layout.setSpacing(10)

        opt_row = QHBoxLayout()
        opt_row.setSpacing(24)

        self.cb_placeholders = QCheckBox(
            "Crea tracce segnaposto (file mancanti ⚠️) per conservare Titolo e Artista"
        )
        self.cb_placeholders.setChecked(True)
        self.cb_placeholders.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cb_placeholders.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-size: 11px; background: transparent; border: none;")
        opt_row.addWidget(self.cb_placeholders)

        self.cb_backup = QCheckBox("Esegui backup di sicurezza automatico prima di modificare il database")
        self.cb_backup.setChecked(True)
        self.cb_backup.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cb_backup.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-size: 11px; background: transparent; border: none;")
        opt_row.addWidget(self.cb_backup)
        opt_row.addStretch(1)

        b_layout.addLayout(opt_row)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)

        self.btn_select_all = QPushButton("Seleziona Tutte")
        self.btn_select_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_select_all.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_BG_CARD};
                color: {COLOR_TEXT_PRIMARY};
                border: 1px solid {COLOR_BORDER};
                border-radius: 6px;
                padding: 6px 14px;
                font-size: 11px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {COLOR_BG_HOVER};
                border-color: {COLOR_BLUE};
            }}
        """)
        self.btn_select_all.clicked.connect(lambda: self._select_all_visible(True))
        action_row.addWidget(self.btn_select_all)

        self.btn_deselect_all = QPushButton("Deseleziona Tutte")
        self.btn_deselect_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_deselect_all.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_BG_CARD};
                color: {COLOR_TEXT_SECONDARY};
                border: 1px solid {COLOR_BORDER};
                border-radius: 6px;
                padding: 6px 14px;
                font-size: 11px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {COLOR_BG_HOVER};
                color: {COLOR_TEXT_PRIMARY};
                border-color: rgba(255, 255, 255, 0.2);
            }}
        """)
        self.btn_deselect_all.clicked.connect(lambda: self._select_all_visible(False))
        action_row.addWidget(self.btn_deselect_all)

        self.lbl_selected_count = QLabel("0 sessioni selezionate")
        self.lbl_selected_count.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-size: 12px; margin-left: 8px; background: transparent; border: none;")
        action_row.addWidget(self.lbl_selected_count)

        action_row.addStretch(1)

        self.btn_close = QPushButton("Chiudi")
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_BG_CARD};
                color: {COLOR_TEXT_SECONDARY};
                border: 1px solid {COLOR_BORDER};
                border-radius: 6px;
                padding: 7px 18px;
                font-size: 12px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {COLOR_BG_HOVER};
                color: {COLOR_TEXT_PRIMARY};
                border-color: rgba(255, 255, 255, 0.2);
            }}
        """)
        self.btn_close.clicked.connect(self.close)
        action_row.addWidget(self.btn_close)

        self.btn_execute = QPushButton("⚡ Applica Merge a Selezionate")
        self.btn_execute.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_execute.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_GREEN};
                color: #ffffff;
                border: 1px solid {COLOR_GREEN};
                border-radius: 6px;
                padding: 7px 22px;
                font-size: 12px;
                font-weight: 700;
            }}
            QPushButton:hover {{
                background-color: #059669;
                border-color: #059669;
            }}
            QPushButton:disabled {{
                background-color: #16241f;
                color: #436354;
                border: 1px solid #1c3329;
            }}
        """)
        self.btn_execute.clicked.connect(self._on_execute_clicked)
        action_row.addWidget(self.btn_execute)

        b_layout.addLayout(action_row)
        self.bottom_frame.setVisible(False)
        self.main_layout.addWidget(self.bottom_frame)

        # 5. Initial Footer (only visible before file is selected)
        self.initial_footer = QWidget()
        init_footer_layout = QHBoxLayout(self.initial_footer)
        init_footer_layout.setContentsMargins(0, 0, 0, 0)
        init_footer_layout.addStretch(1)

        self.btn_close_initial = QPushButton("Chiudi")
        self.btn_close_initial.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close_initial.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_BG_CARD};
                color: {COLOR_TEXT_SECONDARY};
                border: 1px solid {COLOR_BORDER};
                border-radius: 6px;
                padding: 7px 20px;
                font-size: 12px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {COLOR_BG_HOVER};
                color: {COLOR_TEXT_PRIMARY};
                border-color: rgba(255, 255, 255, 0.2);
            }}
        """)
        self.btn_close_initial.clicked.connect(self.close)
        init_footer_layout.addWidget(self.btn_close_initial)
        self.main_layout.addWidget(self.initial_footer)

        self._update_selected_count_label()

    def _browse_file(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Seleziona Rekordbox Database",
            os.path.expanduser("~/Downloads"),
            "Rekordbox Database (*.db);;Tutti i File (*)",
        )
        if file_path:
            self._load_external_file(file_path)

    def _load_external_file(self, file_path: str) -> None:
        if not os.path.isfile(file_path):
            QMessageBox.warning(self, "File Non Valido", f"Il percorso specificato non esiste:\n{file_path}")
            return

        self.external_db_path = file_path
        sz = format_file_size(os.path.getsize(file_path))
        self.file_path_label.setText(f"📄 {file_path} ({sz})")

        # Start analysis worker
        self.progress_bar.setVisible(True)
        self.btn_browse.setEnabled(False)

        stop_worker(self.active_worker)
        self.active_worker = ExternalHistoryAnalyzeWorker(
            self.current_engine, file_path, parent=self
        )
        self.active_worker.success.connect(self._on_analysis_success)
        self.active_worker.error.connect(self._on_analysis_error)
        self.active_worker.start()

    def _on_analysis_success(self, analysis: ExternalHistoryAnalysis) -> None:
        self.progress_bar.setVisible(False)
        self.btn_browse.setEnabled(True)
        self.analysis_data = analysis

        # Collapse drop zone to give space to table
        self.drop_zone.setVisible(False)
        if self.external_db_path:
            fname = os.path.basename(self.external_db_path)
            sz = format_file_size(os.path.getsize(self.external_db_path))
            self.file_path_label.setText(
                f"📄 <b>{fname}</b> <span style='color: {COLOR_TEXT_SECONDARY};'>({sz})</span> &nbsp;•&nbsp; "
                f"<span style='color: {COLOR_BLUE}; font-weight: 600;'>{analysis.total_external_sessions} sessioni storiche trovate</span>"
            )
            self.btn_browse.setText("📂 Cambia Database...")
            self.db_status_badge.setText(f"📁 {analysis.total_external_sessions} sessioni")
            self.db_status_badge.setStyleSheet(
                f"background-color: {COLOR_BLUE_BG}; color: {COLOR_BLUE}; border: 1px solid #005f9e; border-radius: 4px; padding: 4px 10px; font-size: 11px; font-weight: 600;"
            )

        # Also open external engine for fetching tracks
        self.external_engine, _ = open_database(self.external_db_path)

        # Build current track index once in memory
        if not self.current_track_index:
            self.current_track_index = build_current_track_index(self.current_engine)

        # Transition layout from initial file-picker to full analysis review
        self.main_layout.setStretchFactor(self.file_frame, 0)
        self.main_layout.setStretchFactor(self.overview_container, 1)
        self.initial_footer.setVisible(False)
        self.overview_container.setVisible(True)
        self.bottom_frame.setVisible(True)

        self.setMinimumSize(950, 620)
        screen = self.screen() or (self.parent().screen() if self.parent() else None)
        if screen:
            screen_geom = screen.availableGeometry()
            new_w = min(1180, screen_geom.width() - 40)
            new_h = min(780, screen_geom.height() - 60)
            new_x = max(screen_geom.left(), self.x() - (new_w - self.width()) // 2)
            new_y = max(screen_geom.top(), self.y() - (new_h - self.height()) // 2)
            self.setGeometry(new_x, new_y, new_w, new_h)
        else:
            self.resize(1180, 780)

        self._update_tab_buttons_count()
        self._populate_table()
        self._update_selected_count_label()

    def _on_analysis_error(self, err_msg: str) -> None:
        self.progress_bar.setVisible(False)
        self.btn_browse.setEnabled(True)
        QMessageBox.critical(
            self,
            "Errore Apertura Database",
            f"Impossibile aprire o decrittare il database specificato:\n\n{err_msg}",
        )

    def _update_tab_buttons_count(self) -> None:
        if not self.analysis_data:
            return
        cnts = self.analysis_data.counts
        self.btn_tab_merge.setText(f"🟡 Da Unire ({cnts.get('to_merge', 0)})")
        self.btn_tab_trash.setText(f"🗑️ Nel Cestino ({cnts.get('in_trash', 0)})")
        self.btn_tab_new.setText(f"🟢 Nuove ({cnts.get('new', 0)})")
        self.btn_tab_identical.setText(f"⚪ Identiche ({cnts.get('identical', 0)})")

    def _set_category_filter(self, cat: str) -> None:
        self.current_category_filter = cat
        self._update_tab_styles()
        self._populate_table()

    def _update_tab_styles(self) -> None:
        tabs = [
            (self.btn_tab_merge, "to_merge", COLOR_AMBER, COLOR_AMBER_BG),
            (self.btn_tab_trash, "in_trash", COLOR_RED, COLOR_RED_BG),
            (self.btn_tab_new, "new", COLOR_BLUE, COLOR_BLUE_BG),
            (self.btn_tab_identical, "identical", COLOR_TEXT_PRIMARY, COLOR_BG_CARD),
        ]
        for btn, cat, active_color, active_bg in tabs:
            is_active = (self.current_category_filter == cat)
            btn.setChecked(is_active)
            if is_active:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {active_bg};
                        color: {active_color};
                        border: 1px solid {active_color};
                        border-radius: 6px;
                        padding: 6px 14px;
                        font-weight: 700;
                        font-size: 12px;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {COLOR_BG_CARD};
                        color: {COLOR_TEXT_SECONDARY};
                        border: 1px solid {COLOR_BORDER};
                        border-radius: 6px;
                        padding: 6px 14px;
                        font-weight: 600;
                        font-size: 12px;
                    }}
                    QPushButton:hover {{
                        background-color: {COLOR_BG_HOVER};
                        color: {COLOR_TEXT_PRIMARY};
                    }}
                """)

    def _populate_table(self) -> None:
        if not self.analysis_data:
            return

        matching_sessions = [
            s for s in self.analysis_data.sessions
            if s.category == self.current_category_filter
        ]

        self.table.setRowCount(len(matching_sessions))

        for row, sess in enumerate(matching_sessions):
            # 0. Checkbox
            cb = TableCheckBox(checked=(sess.external_id in self.selected_session_ids))
            cb.toggled_state.connect(lambda checked, s_id=sess.external_id: self._on_session_checked(s_id, checked))
            cell_widget = CheckBoxCellWidget(cb)
            self.table.setCellWidget(row, 0, cell_widget)

            # 1. Date
            d_item = QTableWidgetItem(sess.date_created)
            d_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            d_item.setForeground(QColor("#9da2b2"))
            self.table.setItem(row, 1, d_item)

            # 2. Name
            n_item = QTableWidgetItem(sess.name)
            self.table.setItem(row, 2, n_item)

            # 3. External Tracks
            ext_t_item = QTableWidgetItem(f"{sess.external_track_count} tracce")
            ext_t_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            ext_t_item.setForeground(QColor("#d1d5e0"))
            self.table.setItem(row, 3, ext_t_item)

            # 4. Current Tracks
            cur_t_str = f"{sess.current_track_count} tracce" if sess.current_id else "—"
            cur_t_item = QTableWidgetItem(cur_t_str)
            cur_t_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            cur_t_item.setForeground(QColor("#9da2b2" if sess.current_id else "#5a5e6d"))
            self.table.setItem(row, 4, cur_t_item)

            # 5. Diff Badge
            if sess.category == "in_trash":
                diff_str = f"🗑️ +{sess.external_track_count} da cestino"
                color_text = "#c084fc"
            elif sess.diff_count > 0:
                diff_str = f"➕ +{sess.diff_count} da unire"
                color_text = "#f59e0b"
            elif sess.category == "new":
                diff_str = f"✨ +{sess.external_track_count} nuove"
                color_text = "#10b981"
            else:
                diff_str = "✓ Sincronizzata"
                color_text = "#717686"

            badge_lbl = QLabel(diff_str)
            badge_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge_lbl.setFixedHeight(22)
            badge_lbl.setStyleSheet(f"""
                QLabel {{
                    background-color: transparent;
                    color: {color_text};
                    border: 1px solid transparent;
                    border-radius: 4px;
                    padding: 0 6px;
                    font-size: 11px;
                    font-weight: 600;
                }}
            """)
            badge_holder = QWidget()
            badge_holder.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            b_l = QHBoxLayout(badge_holder)
            b_l.setContentsMargins(0, 0, 0, 0)
            b_l.setAlignment(Qt.AlignmentFlag.AlignCenter)
            b_l.addWidget(badge_lbl)
            self.table.setCellWidget(row, 5, badge_holder)

            # 6. Action Buttons
            act_holder = QWidget()
            act_holder.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            act_l = QHBoxLayout(act_holder)
            act_l.setContentsMargins(2, 2, 2, 2)
            act_l.setSpacing(6)
            act_l.setAlignment(Qt.AlignmentFlag.AlignCenter)

            if sess.category in ("to_merge", "in_trash"):
                btn_diff = QPushButton("🔀 Diff")
                btn_diff.setFixedHeight(24)
                btn_diff.setCursor(Qt.CursorShape.PointingHandCursor)
                btn_diff.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {COLOR_BLUE_BG};
                        color: {COLOR_BLUE};
                        border: 1px solid #0284c7;
                        border-radius: 4px;
                        padding: 0 9px;
                        font-size: 11px;
                        font-weight: 600;
                    }}
                    QPushButton:hover {{
                        background-color: {COLOR_BLUE};
                        color: #ffffff;
                    }}
                """)
                btn_diff.clicked.connect(lambda _, s=sess: self._open_diff_dialog(s))
                act_l.addWidget(btn_diff)

            btn_preview = QPushButton("🔍 Tracce")
            btn_preview.setFixedHeight(24)
            btn_preview.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_preview.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR_BG_CARD};
                    color: {COLOR_TEXT_PRIMARY};
                    border: 1px solid {COLOR_BORDER};
                    border-radius: 4px;
                    padding: 0 9px;
                    font-size: 11px;
                    font-weight: 500;
                }}
                QPushButton:hover {{
                    background-color: {COLOR_BG_HOVER};
                    border-color: {COLOR_BORDER_LIGHT};
                }}
            """)
            btn_preview.clicked.connect(lambda _, s=sess: self._preview_session_tracks(s))
            act_l.addWidget(btn_preview)

            self.table.setCellWidget(row, 6, act_holder)

        self._filter_table_rows()
        self._update_selected_count_label()
        self._update_header_check_state()

    def _on_session_checked(self, session_id: str, checked: bool) -> None:
        if checked:
            self.selected_session_ids.add(session_id)
        else:
            self.selected_session_ids.discard(session_id)
        self._update_selected_count_label()
        self._update_header_check_state()

    def _select_all_visible(self, select: bool) -> None:
        for r in range(self.table.rowCount()):
            if not self.table.isRowHidden(r):
                holder = self.table.cellWidget(r, 0)
                if holder:
                    btn = holder.findChild(TableCheckBox)
                    if btn:
                        btn.setChecked(select)
        self._update_selected_count_label()
        self._update_header_check_state()

    def _on_header_section_clicked(self, logical_index: int) -> None:
        if logical_index == 0:
            self._toggle_select_all()

    def _toggle_select_all(self) -> None:
        # If any visible row is currently unselected, select all visible; otherwise deselect all
        visible_unselected = False
        for r in range(self.table.rowCount()):
            if not self.table.isRowHidden(r):
                holder = self.table.cellWidget(r, 0)
                if holder:
                    btn = holder.findChild(TableCheckBox)
                    if btn and not btn.isChecked():
                        visible_unselected = True
                        break
        self._select_all_visible(visible_unselected)

    def _update_header_check_state(self) -> None:
        total_visible = 0
        checked_visible = 0
        for r in range(self.table.rowCount()):
            if not self.table.isRowHidden(r):
                total_visible += 1
                holder = self.table.cellWidget(r, 0)
                if holder:
                    btn = holder.findChild(TableCheckBox)
                    if btn and btn.isChecked():
                        checked_visible += 1

        item = self.table.horizontalHeaderItem(0)
        if item:
            item.setText("✓")
            if total_visible > 0 and checked_visible == total_visible:
                item.setForeground(QColor(COLOR_BLUE))
            elif checked_visible > 0:
                item.setForeground(QColor("#7dd3fc"))
            else:
                item.setForeground(QColor(COLOR_TEXT_MUTED))

    def _toggle_selected_rows_check(self) -> None:
        sel_rows = set(idx.row() for idx in self.table.selectedIndexes())
        if not sel_rows:
            return
        first_row = min(sel_rows)
        w = self.table.cellWidget(first_row, 0)
        new_state = True
        if w:
            btn = w.findChild(TableCheckBox)
            if btn:
                new_state = not btn.isChecked()
        for r in sel_rows:
            w = self.table.cellWidget(r, 0)
            if w:
                btn = w.findChild(TableCheckBox)
                if btn:
                    btn.setChecked(new_state)
        self._update_selected_count_label()
        self._update_header_check_state()

    def eventFilter(self, watched: Any, event: Any) -> bool:
        if watched == self.table and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Space:
                self._toggle_selected_rows_check()
                return True
        return super().eventFilter(watched, event)

    def _update_selected_count_label(self) -> None:
        cnt = len(self.selected_session_ids)
        self.lbl_selected_count.setText(f"{cnt} {'sessione selezionata' if cnt == 1 else 'sessioni selezionate'}")
        self.btn_execute.setEnabled(cnt > 0)

        if self.current_category_filter == "in_trash":
            self.btn_execute.setText(f"♻️ Ripristina Selezionate ({cnt})")
        elif self.current_category_filter == "new":
            self.btn_execute.setText(f"📥 Importa Selezionate ({cnt})")
        else:
            self.btn_execute.setText(f"⚡ Applica Merge a Selezionate ({cnt})")

    def _filter_table_rows(self) -> None:
        q = self.search_input.text().strip().lower()
        for r in range(self.table.rowCount()):
            date_item = self.table.item(r, 1)
            name_item = self.table.item(r, 2)
            d_text = date_item.text().lower() if date_item else ""
            n_text = name_item.text().lower() if name_item else ""
            matches = (not q) or (q in d_text) or (q in n_text)
            self.table.setRowHidden(r, not matches)
        self._update_header_check_state()

    def _open_diff_dialog(self, sess: HistorySessionAnalysis) -> None:
        if not self.external_engine:
            return

        ext_tracks = fetch_external_session_tracks(self.external_engine, sess.external_id)
        cur_tracks: List[Dict[str, Any]] = []
        if sess.current_id:
            cur_tracks = fetch_current_session_tracks(self.current_engine, sess.current_id)

        if not self.current_track_index:
            self.current_track_index = build_current_track_index(self.current_engine)

        dialog = HistoryDiffDialog(
            session_name=sess.name,
            session_date=sess.date_created,
            current_tracks=cur_tracks,
            external_tracks=ext_tracks,
            current_track_index=self.current_track_index,
            parent=self,
            current_engine=self.current_engine,
        )

        def _on_single_merge(tracks_to_insert: List[TrackDiffItem]) -> None:
            # Build single session plan
            action = "restore_and_merge" if sess.is_current_deleted else ("merge" if sess.current_id else "create_new")
            plan = [
                {
                    "external_history_id": sess.external_id,
                    "current_history_id": sess.current_id,
                    "action": action,
                    "tracks_to_insert": tracks_to_insert,
                }
            ]
            self._execute_plan(plan)

        dialog.merge_confirmed.connect(_on_single_merge)
        dialog.exec()

    def _preview_session_tracks(self, sess: HistorySessionAnalysis) -> None:
        if not self.external_engine:
            return
        tracks = fetch_external_session_tracks(self.external_engine, sess.external_id)
        if not tracks:
            QMessageBox.information(self, "Sessione Vuota", f"Nessuna traccia trovata per la sessione:\n{sess.name}")
            return

        msg_lines = [f"<b>{sess.name}</b> ({sess.date_created})", f"Totale: {len(tracks)} tracce<br><hr>"]
        for t in tracks[:25]:
            msg_lines.append(f"#{t['track_no']} [{t['played_time'] or '--:--'}] <b>{t['title']}</b> - {t['artist']} ({t['duration_str']})")
        if len(tracks) > 25:
            msg_lines.append(f"<i>...e altre {len(tracks) - 25} tracce.</i>")

        box = QMessageBox(self)
        box.setWindowTitle(f"Anteprima: {sess.name}")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText("<br>".join(msg_lines))
        box.exec()

    def _on_execute_clicked(self) -> None:
        if not self.selected_session_ids or not self.analysis_data or not self.external_engine:
            return

        if is_rekordbox_running():
            QMessageBox.critical(
                self,
                "Rekordbox In Esecuzione",
                "Rekordbox è attualmente in esecuzione. Chiudi l'applicazione prima di procedere per evitare conflitti nel database.",
            )
            return

        selected_sessions = [
            s for s in self.analysis_data.sessions
            if s.external_id in self.selected_session_ids
        ]

        msg = (
            f"Stai per applicare l'operazione a <b>{len(selected_sessions)} sessioni storiche</b>.<br><br>"
            f"Verrà effettuato automaticamente un <b>backup di sicurezza</b> del tuo database prima di procedere.<br><br>"
            "Vuoi continuare?"
        )
        reply = QMessageBox.question(
            self,
            "Conferma Operazione",
            msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Build plan for all selected sessions
        if not self.current_track_index:
            self.current_track_index = build_current_track_index(self.current_engine)

        plan: List[Dict[str, Any]] = []
        for sess in selected_sessions:
            ext_tracks = fetch_external_session_tracks(self.external_engine, sess.external_id)
            cur_tracks: List[Dict[str, Any]] = []
            if sess.current_id:
                cur_tracks = fetch_current_session_tracks(self.current_engine, sess.current_id)

            diff = calculate_history_session_diff(
                cur_tracks, ext_tracks, self.current_track_index
            )

            # Collect items to insert (new items from external)
            to_insert = [
                it for it in diff["merged_column"]
                if it.origin == "external" and it.included
            ]

            action = (
                "restore_and_merge"
                if sess.is_current_deleted
                else ("merge" if sess.current_id else "create_new")
            )

            plan.append({
                "external_history_id": sess.external_id,
                "current_history_id": sess.current_id,
                "action": action,
                "tracks_to_insert": to_insert,
            })

        self._execute_plan(plan)

    def _execute_plan(self, plan: List[Dict[str, Any]]) -> None:
        self.progress_bar.setVisible(True)
        self.btn_execute.setEnabled(False)
        self.btn_close.setEnabled(False)

        create_placeholders = self.cb_placeholders.isChecked()
        create_backup = self.cb_backup.isChecked()

        stop_worker(self.active_worker)
        self.active_worker = ExternalHistoryMergeWorker(
            current_engine=self.current_engine,
            external_engine=self.external_engine,
            plan=plan,
            db_path=self.current_db_path,
            create_placeholders=create_placeholders,
            backup=create_backup,
            parent=self,
        )
        self.active_worker.success.connect(self._on_execution_success)
        self.active_worker.error.connect(self._on_execution_error)
        self.active_worker.start()

    def _on_execution_success(self, stats: Dict[str, Any]) -> None:
        self.progress_bar.setVisible(False)
        self.btn_execute.setEnabled(True)
        self.btn_close.setEnabled(True)

        backup_msg = f"<br><b>Backup creato:</b> {stats.get('backup_path')}" if stats.get("backup_path") else ""
        msg = (
            f"<b>Operazione completata con successo!</b><br><br>"
            f"• Sessioni unite: {stats.get('sessions_merged', 0)}<br>"
            f"• Sessioni ripristinate dal cestino: {stats.get('sessions_restored', 0)}<br>"
            f"• Nuove sessioni create: {stats.get('sessions_created', 0)}<br>"
            f"• Tracce aggiunte: {stats.get('tracks_added', 0)}<br>"
            f"• Segnaposto per file mancanti: {stats.get('placeholders_created', 0)}"
            f"{backup_msg}"
        )
        QMessageBox.information(self, "Operazione Completata", msg)

        # Notify parent to reload and re-analyze
        self.db_updated.emit()
        self.selected_session_ids.clear()
        if self.external_db_path:
            self._load_external_file(self.external_db_path)

    def _on_execution_error(self, err_msg: str) -> None:
        self.progress_bar.setVisible(False)
        self.btn_execute.setEnabled(True)
        self.btn_close.setEnabled(True)
        QMessageBox.critical(
            self,
            "Errore durante l'operazione",
            f"Si è verificato un errore durante l'applicazione delle modifiche:\n\n{err_msg}",
        )
