"""Replace File View Screen.

Enables replacing or upgrading collection audio files (e.g. from MP3 to AIFF/WAV/FLAC)
while preserving 100% of history sessions, play counts, original "Date Added" (StockDate),
cue points, playlists, and tags.
"""

import os
from typing import Any, Dict, List, Optional
from PySide6.QtCore import QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.engine import Engine

from rekordbox_collection_manager.edit_play_count import fetch_track_full_details, search_tracks
from rekordbox_collection_manager.gui.replacement_diff_dialog import (
    TrackReplacementDiffDialog,
    calculate_metadata_diff,
)
from rekordbox_collection_manager.replace_file import fetch_track_full_info
from rekordbox_collection_manager.gui.theme import (
    COLOR_AMBER,
    COLOR_AMBER_BG,
    COLOR_AMBER_BORDER,
    COLOR_BG_CARD,
    COLOR_BG_DARK,
    COLOR_BG_HOVER,
    COLOR_BG_PANEL,
    COLOR_BLUE,
    COLOR_BLUE_BG,
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
from rekordbox_collection_manager.gui.workers import (
    AudioInspectWorker,
    TrackMergeWorker,
    TrackReplaceWorker,
    TrackSearchWorker,
)
from rekordbox_collection_manager.utils import (
    format_file_size,
    is_aiff_track,
    is_mp3_track,
    is_rekordbox_running,
    is_replaceable_track,
    open_in_folder,
    resolve_artwork_path,
    reveal_in_file_manager,
)


class FileDropZone(QFrame):
    """Interactive drag-and-drop zone for dropping or browsing replacement AIFF files."""

    file_selected = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setObjectName("dropZone")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(130)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.icon_label = QLabel("📥")
        self.icon_label.setStyleSheet("font-size: 32px;")
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.icon_label)

        self.title_label = QLabel("Drag & Drop Replacement AIFF File Here")
        self.title_label.setStyleSheet(
            f"color: {COLOR_TEXT_PRIMARY}; font-size: 14px; font-weight: 600;"
        )
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title_label)

        self.sub_label = QLabel(
            "Supports AIFF (.aiff, .aif) · Click anywhere to browse"
        )
        self.sub_label.setStyleSheet(
            f"color: {COLOR_TEXT_MUTED}; font-size: 11px;"
        )
        self.sub_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.sub_label)

        self._set_idle_style()

    def _set_idle_style(self) -> None:
        self.setStyleSheet(
            f"""
            QFrame#dropZone {{
                background-color: #16161d;
                border: 2px dashed {COLOR_BORDER_LIGHT};
                border-radius: 10px;
            }}
            QFrame#dropZone:hover {{
                background-color: {COLOR_BG_CARD};
                border-color: {COLOR_BLUE};
            }}
            """
        )

    def _set_drag_active_style(self) -> None:
        self.setStyleSheet(
            f"""
            QFrame#dropZone {{
                background-color: {COLOR_BLUE_BG};
                border: 2px dashed {COLOR_BLUE};
                border-radius: 10px;
            }}
            """
        )

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and urls[0].isLocalFile():
                ext = os.path.splitext(urls[0].toLocalFile())[1].lower()
                if ext in (".aiff", ".aif"):
                    event.acceptProposedAction()
                    self._set_drag_active_style()
                    return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._set_idle_style()

    def dropEvent(self, event: QDropEvent) -> None:
        self._set_idle_style()
        urls = event.mimeData().urls()
        if urls and urls[0].isLocalFile():
            local_path = urls[0].toLocalFile()
            self.file_selected.emit(local_path)
            event.acceptProposedAction()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            chosen, _ = QFileDialog.getOpenFileName(
                self,
                "Select Replacement AIFF Audio File",
                "",
                "AIFF Audio Files (*.aiff *.aif);;All Files (*)",
            )
            if chosen:
                self.file_selected.emit(chosen)


class ReplaceFileView(QWidget):
    """Main view for replacing and upgrading track audio files in collection."""

    notify_status = Signal(str)
    open_track_detail_requested = Signal(dict)
    open_history_session_requested = Signal(str)
    db_saved = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.engine: Optional[Engine] = None
        self.db_path: Optional[str] = None

        # Mode 1 State
        self.selected_track: Optional[Dict[str, Any]] = None
        self.replacement_file_path: Optional[str] = None
        self.replacement_specs: Optional[Dict[str, Any]] = None

        # Mode 2 State
        self.merge_source_track: Optional[Dict[str, Any]] = None
        self.merge_target_track: Optional[Dict[str, Any]] = None

        # Workers
        self.search_worker: Optional[TrackSearchWorker] = None
        self.inspect_worker: Optional[AudioInspectWorker] = None
        self.replace_worker: Optional[TrackReplaceWorker] = None
        self.merge_worker: Optional[TrackMergeWorker] = None

        # Search debounce timers
        self.mode1_search_timer = QTimer(self)
        self.mode1_search_timer.setSingleShot(True)
        self.mode1_search_timer.setInterval(250)
        self.mode1_search_timer.timeout.connect(self._execute_mode1_search)

        self.merge_src_search_timer = QTimer(self)
        self.merge_src_search_timer.setSingleShot(True)
        self.merge_src_search_timer.setInterval(250)
        self.merge_src_search_timer.timeout.connect(self._execute_merge_src_search)

        self.merge_tgt_search_timer = QTimer(self)
        self.merge_tgt_search_timer.setSingleShot(True)
        self.merge_tgt_search_timer.setInterval(250)
        self.merge_tgt_search_timer.timeout.connect(self._execute_merge_tgt_search)

        self._init_ui()

    def set_engine(self, engine: Engine, db_path: Optional[str] = None) -> None:
        """Set active database engine and reset selections."""
        self.engine = engine
        self.db_path = db_path
        self._reset_mode1()
        self._reset_mode2()

    def preselect_track(self, track_dict: Dict[str, Any]) -> None:
        """Pre-populate a track for replacement (e.g. from Track Details or context menu)."""
        self.tab_group.button(0).setChecked(True)
        self.stacked_modes.setCurrentIndex(0)
        if not is_replaceable_track(track_dict):
            fmt = track_dict.get("file_type") or "non-MP3/AIFF"
            QMessageBox.warning(
                self,
                "Unsupported File Format",
                f"Currently, Replace File supports MP3 and AIFF tracks.\n\n"
                f"The selected track '{track_dict.get('title')}' is a {fmt} file.",
            )
            return
        self._on_track_selected_mode1(track_dict)

    def _init_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # 1. Top Header & Mode Switcher
        header_widget = QFrame()
        header_widget.setObjectName("replaceFileHeader")
        header_widget.setStyleSheet(
            f"QFrame#replaceFileHeader {{ background-color: {COLOR_BG_PANEL}; border: none; border-bottom: 1px solid {COLOR_BORDER}; }} "
            f"QFrame#replaceFileHeader QLabel {{ border: none; text-decoration: none; background: transparent; }}"
        )
        header_layout = QVBoxLayout(header_widget)
        header_layout.setContentsMargins(20, 16, 20, 14)
        header_layout.setSpacing(10)

        # Title Row
        title_row = QHBoxLayout()
        title_row.setSpacing(12)

        icon_label = QLabel("🔄")
        icon_label.setStyleSheet("font-size: 22px; border: none;")
        title_row.addWidget(icon_label)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        main_title = QLabel("Replace Audio File")
        main_title.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-size: 16px; font-weight: bold; border: none;")
        title_col.addWidget(main_title)

        sub_title = QLabel(
            "Upgrade MP3 tracks or replace AIFF tracks with lossless AIFF audio while preserving 100% of "
            "play counts, history sessions, original date added, cues, and playlists."
        )
        sub_title.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-size: 12px; border: none;")
        title_col.addWidget(sub_title)
        title_row.addLayout(title_col, stretch=1)

        header_layout.addLayout(title_row)

        # Mode Switcher Tabs
        mode_btn_row = QHBoxLayout()
        mode_btn_row.setSpacing(8)

        tab_btn_style = f"""
            QPushButton {{
                background-color: {COLOR_BG_CARD};
                color: {COLOR_TEXT_SECONDARY};
                border: 1px solid {COLOR_BORDER};
                border-radius: 6px;
                padding: 7px 20px;
                font-size: 12px;
                font-weight: 600;
                text-decoration: none;
            }}
            QPushButton:hover {{
                background-color: {COLOR_BG_HOVER};
                color: {COLOR_TEXT_PRIMARY};
                border-color: {COLOR_BORDER_LIGHT};
            }}
            QPushButton:checked {{
                background-color: {COLOR_BLUE_BG};
                color: {COLOR_BLUE};
                border: 1px solid {COLOR_BLUE};
                font-weight: 600;
            }}
        """

        self.tab_group = QButtonGroup(self)
        self.tab_group.setExclusive(True)

        self.btn_mode_file = QPushButton("📁  Replace with File on Disk")
        self.btn_mode_file.setStyleSheet(tab_btn_style)
        self.btn_mode_file.setCheckable(True)
        self.btn_mode_file.setChecked(True)
        self.btn_mode_file.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_mode_file.setToolTip("Pick an existing MP3 or AIFF track and choose a new AIFF audio file on disk to replace it directly.")
        self.tab_group.addButton(self.btn_mode_file, 0)
        mode_btn_row.addWidget(self.btn_mode_file)

        self.btn_mode_merge = QPushButton("🔀  Replace with Imported Track")
        self.btn_mode_merge.setStyleSheet(tab_btn_style)
        self.btn_mode_merge.setCheckable(True)
        self.btn_mode_merge.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_mode_merge.setToolTip("If you already imported the AIFF into Rekordbox, transfer history & plays from the old MP3 or AIFF track into the new AIFF track.")
        self.tab_group.addButton(self.btn_mode_merge, 1)
        mode_btn_row.addWidget(self.btn_mode_merge)

        mode_btn_row.addStretch(1)
        header_layout.addLayout(mode_btn_row)

        root_layout.addWidget(header_widget)

        # 2. Main Stacked Modes (Scrollable)
        self.stacked_modes = QStackedWidget()

        # Mode 1 Page
        mode1_scroll = QScrollArea()
        mode1_scroll.setWidgetResizable(True)
        mode1_scroll.setFrameShape(QFrame.Shape.NoFrame)
        mode1_scroll.setStyleSheet("background-color: transparent;")
        self.mode1_widget = self._build_mode1_widget()
        mode1_scroll.setWidget(self.mode1_widget)
        self.stacked_modes.addWidget(mode1_scroll)

        # Mode 2 Page
        mode2_scroll = QScrollArea()
        mode2_scroll.setWidgetResizable(True)
        mode2_scroll.setFrameShape(QFrame.Shape.NoFrame)
        mode2_scroll.setStyleSheet("background-color: transparent;")
        self.mode2_widget = self._build_mode2_widget()
        mode2_scroll.setWidget(self.mode2_widget)
        self.stacked_modes.addWidget(mode2_scroll)

        root_layout.addWidget(self.stacked_modes, stretch=1)

        self.tab_group.idClicked.connect(self.stacked_modes.setCurrentIndex)

    # ==========================================================================
    # MODE 1: Direct File Replacement on Disk
    # ==========================================================================

    def _build_mode1_widget(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 16, 20, 24)
        layout.setSpacing(18)

        # ----------------------------------------------------------------------
        # Section 1: Choose Track to Replace
        # ----------------------------------------------------------------------
        sec1_header = QLabel("1. SELECT TRACK IN COLLECTION TO REPLACE (MP3 OR AIFF)")
        sec1_header.setStyleSheet(
            f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 0.8px;"
        )
        layout.addWidget(sec1_header)

        # Search Bar
        search_box = QHBoxLayout()
        search_box.setSpacing(10)

        self.mode1_search_input = QLineEdit()
        self.mode1_search_input.setPlaceholderText("🔍 Search MP3 or AIFF track by title, artist, or filename to replace...")
        self.mode1_search_input.setClearButtonEnabled(True)
        self.mode1_search_input.textChanged.connect(lambda: self.mode1_search_timer.start())
        search_box.addWidget(self.mode1_search_input, stretch=1)

        self.mode1_search_btn = QPushButton("Search")
        self.mode1_search_btn.clicked.connect(self._execute_mode1_search)
        search_box.addWidget(self.mode1_search_btn)
        layout.addLayout(search_box)

        # Track Search Results Dropdown/Table
        self.mode1_results_table = QTableWidget()
        self.mode1_results_table.setColumnCount(6)
        self.mode1_results_table.setHorizontalHeaderLabels(["Title", "Artist", "Format", "Plays", "Added", "File Path"])
        self.mode1_results_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.mode1_results_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.mode1_results_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.mode1_results_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.mode1_results_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.mode1_results_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.mode1_results_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.mode1_results_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.mode1_results_table.setFixedHeight(140)
        self.mode1_results_table.itemSelectionChanged.connect(self._on_mode1_table_selection_changed)
        layout.addWidget(self.mode1_results_table)

        # Selected Track Display Card
        self.mode1_track_card = QFrame()
        self.mode1_track_card.setObjectName("cardFrame")
        self.mode1_track_card.setStyleSheet(
            f"QFrame#cardFrame {{ background-color: {COLOR_BG_CARD}; border: 1px solid {COLOR_BORDER}; border-radius: 8px; padding: 12px; }}"
        )
        mode1_card_layout = QHBoxLayout(self.mode1_track_card)
        mode1_card_layout.setContentsMargins(12, 10, 12, 10)
        mode1_card_layout.setSpacing(14)

        self.mode1_art_label = QLabel()
        self.mode1_art_label.setFixedSize(54, 54)
        self.mode1_art_label.setStyleSheet("background-color: #15151a; border-radius: 6px; font-size: 24px;")
        self.mode1_art_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mode1_art_label.setText("🎵")
        mode1_card_layout.addWidget(self.mode1_art_label)

        track_meta_col = QVBoxLayout()
        track_meta_col.setSpacing(4)

        self.mode1_track_title = QLabel("No Track Selected")
        self.mode1_track_title.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-size: 14px; font-weight: bold;")
        track_meta_col.addWidget(self.mode1_track_title)

        self.mode1_track_artist = QLabel("Select an MP3 or AIFF track from search results above")
        self.mode1_track_artist.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-size: 12px;")
        track_meta_col.addWidget(self.mode1_track_artist)

        self.mode1_track_path_label = QLabel("")
        self.mode1_track_path_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px;")
        track_meta_col.addWidget(self.mode1_track_path_label)

        mode1_card_layout.addLayout(track_meta_col, stretch=1)

        # Chips column
        self.mode1_badges_col = QVBoxLayout()
        self.mode1_badges_col.setSpacing(4)
        self.mode1_badges_col.setAlignment(Qt.AlignmentFlag.AlignRight)

        self.mode1_format_chip = QLabel("—")
        self.mode1_format_chip.setObjectName("amberBadge")
        self.mode1_badges_col.addWidget(self.mode1_format_chip, alignment=Qt.AlignmentFlag.AlignRight)

        self.mode1_plays_chip = QLabel("0 plays")
        self.mode1_plays_chip.setObjectName("blueBadge")
        self.mode1_badges_col.addWidget(self.mode1_plays_chip, alignment=Qt.AlignmentFlag.AlignRight)

        self.mode1_added_chip = QLabel("Added: —")
        self.mode1_added_chip.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px;")
        self.mode1_badges_col.addWidget(self.mode1_added_chip, alignment=Qt.AlignmentFlag.AlignRight)

        mode1_card_layout.addLayout(self.mode1_badges_col)
        layout.addWidget(self.mode1_track_card)

        # ----------------------------------------------------------------------
        # Section 2: Choose Replacement File on Disk
        # ----------------------------------------------------------------------
        sec2_header = QLabel("2. CHOOSE REPLACEMENT AIFF FILE ON DISK")
        sec2_header.setStyleSheet(
            f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 0.8px;"
        )
        layout.addWidget(sec2_header)

        self.drop_zone = FileDropZone(self)
        self.drop_zone.file_selected.connect(self._on_new_file_selected)
        layout.addWidget(self.drop_zone)

        # Comparison Card (Diff between old and new)
        self.diff_card = QFrame()
        self.diff_card.setObjectName("diffCard")
        self.diff_card.setStyleSheet(
            f"QFrame#diffCard {{ background-color: #181822; border: 1px solid {COLOR_BORDER}; border-radius: 8px; padding: 14px; }}"
        )
        diff_layout = QVBoxLayout(self.diff_card)
        diff_layout.setContentsMargins(14, 12, 14, 12)
        diff_layout.setSpacing(10)

        diff_title = QLabel("Comparison & Audio Replacement Details")
        diff_title.setStyleSheet(f"color: {COLOR_BLUE}; font-size: 13px; font-weight: bold;")
        diff_layout.addWidget(diff_title)

        diff_grid = QHBoxLayout()
        diff_grid.setSpacing(16)

        # Old File Summary
        self.old_diff_col = QVBoxLayout()
        self.old_diff_col.setSpacing(4)
        old_lbl = QLabel("CURRENT FILE (OLD)")
        old_lbl.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 10px; font-weight: bold;")
        self.old_diff_col.addWidget(old_lbl)
        self.old_specs_label = QLabel("No track selected")
        self.old_specs_label.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-size: 12px;")
        self.old_diff_col.addWidget(self.old_specs_label)
        diff_grid.addLayout(self.old_diff_col, stretch=1)

        arrow_label = QLabel("➔")
        arrow_label.setStyleSheet(f"color: {COLOR_BLUE}; font-size: 20px; font-weight: bold;")
        arrow_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        diff_grid.addWidget(arrow_label)

        # New File Summary
        self.new_diff_col = QVBoxLayout()
        self.new_diff_col.setSpacing(4)
        new_lbl = QLabel("REPLACEMENT FILE (NEW)")
        new_lbl.setStyleSheet(f"color: {COLOR_GREEN}; font-size: 10px; font-weight: bold;")
        self.new_diff_col.addWidget(new_lbl)
        self.new_specs_label = QLabel("No replacement file chosen yet")
        self.new_specs_label.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-size: 12px;")
        self.new_diff_col.addWidget(self.new_specs_label)
        diff_grid.addLayout(self.new_diff_col, stretch=1)

        diff_layout.addLayout(diff_grid)

        # Duration & Match Status Banner
        self.duration_match_banner = QLabel("Waiting for replacement file...")
        self.duration_match_banner.setStyleSheet(
            f"background-color: {COLOR_BG_DARK}; border: 1px solid {COLOR_BORDER}; border-radius: 6px; padding: 8px 12px; font-size: 12px; color: {COLOR_TEXT_SECONDARY};"
        )
        diff_layout.addWidget(self.duration_match_banner)

        # Conflict Status & Diff Summary Banner
        self.mode1_diff_summary = QLabel("Select both a track and a replacement file to compare metadata.")
        self.mode1_diff_summary.setStyleSheet(
            f"background-color: {COLOR_BG_CARD}; border: 1px solid {COLOR_BORDER}; border-radius: 6px; padding: 8px 12px; font-size: 12px; color: {COLOR_TEXT_SECONDARY};"
        )
        diff_layout.addWidget(self.mode1_diff_summary)

        # Inspect 3-column diff tool button
        self.btn_mode1_inspect_diff = QPushButton("🔍  Inspect 3-Column Metadata Diff & Resolve Conflicts...")
        self.btn_mode1_inspect_diff.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {COLOR_BG_CARD};
                color: {COLOR_BLUE};
                border: 1px solid {COLOR_BORDER_LIGHT};
                border-radius: 6px;
                padding: 8px 14px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {COLOR_BLUE_BG};
                border-color: {COLOR_BLUE};
                color: #ffffff;
            }}
            QPushButton:disabled {{
                background-color: transparent;
                color: {COLOR_TEXT_MUTED};
                border-color: {COLOR_BORDER};
            }}
            """
        )
        self.btn_mode1_inspect_diff.setEnabled(False)
        self.btn_mode1_inspect_diff.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_mode1_inspect_diff.clicked.connect(self._on_execute_replace)
        diff_layout.addWidget(self.btn_mode1_inspect_diff)

        layout.addWidget(self.diff_card)

        # ----------------------------------------------------------------------
        # Section 3: Preservation Guarantee & Options
        # ----------------------------------------------------------------------
        sec3_header = QLabel("3. PRESERVATION GUARANTEE & OPTIONS")
        sec3_header.setStyleSheet(
            f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 0.8px;"
        )
        layout.addWidget(sec3_header)

        options_card = QFrame()
        options_card.setStyleSheet(
            f"background-color: {COLOR_BG_CARD}; border: 1px solid {COLOR_BORDER}; border-radius: 8px; padding: 14px;"
        )
        opt_layout = QVBoxLayout(options_card)
        opt_layout.setContentsMargins(14, 12, 14, 12)
        opt_layout.setSpacing(10)

        # Guaranteed Badges Row
        guarantee_box = QHBoxLayout()
        guarantee_box.setSpacing(12)

        g1 = QLabel("🔒 Play Count Preserved")
        g1.setObjectName("safeBadge")
        guarantee_box.addWidget(g1)

        g2 = QLabel("🔒 Date Added Preserved")
        g2.setObjectName("safeBadge")
        guarantee_box.addWidget(g2)

        g3 = QLabel("🔒 All History Plays Preserved")
        g3.setObjectName("safeBadge")
        guarantee_box.addWidget(g3)

        g4 = QLabel("🔒 Cue Points Preserved")
        g4.setObjectName("safeBadge")
        guarantee_box.addWidget(g4)

        g5 = QLabel("🔒 Playlists & Tags Preserved")
        g5.setObjectName("safeBadge")
        guarantee_box.addWidget(g5)

        guarantee_box.addStretch(1)
        opt_layout.addLayout(guarantee_box)

        # Action for old file
        action_row = QHBoxLayout()
        action_row.setSpacing(10)

        act_label = QLabel("Old Audio File on Disk:")
        act_label.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-size: 12px; font-weight: 500;")
        action_row.addWidget(act_label)

        self.old_file_combo = QComboBox()
        self.old_file_combo.addItems([
            "Keep old file on disk (Safe)",
            "Move old file to macOS Trash (Clean space)",
            "Rename old file to .bak (Backup)",
        ])
        action_row.addWidget(self.old_file_combo, stretch=1)
        opt_layout.addLayout(action_row)

        # Checkbox: Move new file into track folder
        self.chk_move_to_folder = QCheckBox("Move/Copy replacement audio file into original track's folder")
        self.chk_move_to_folder.setToolTip(
            "If your downloaded file is in Downloads, moves it next to your existing library collection."
        )
        opt_layout.addWidget(self.chk_move_to_folder)

        layout.addWidget(options_card)

        # ----------------------------------------------------------------------
        # Section 4: Action Button & Status Banner
        # ----------------------------------------------------------------------
        action_bar = QHBoxLayout()
        action_bar.setSpacing(12)

        self.btn_execute_replace = QPushButton("🔍  Review Differences & Replace Audio File...")
        self.btn_execute_replace.setToolTip("Opens the 3-column metadata diff screen to inspect conflicts and confirm replacement.")
        self.btn_execute_replace.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {COLOR_BLUE};
                color: #ffffff;
                border: none;
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
                padding: 12px 24px;
            }}
            QPushButton:hover {{
                background-color: #2bb2ff;
            }}
            QPushButton:disabled {{
                background-color: #2e2e38;
                color: #686c7a;
            }}
            """
        )
        self.btn_execute_replace.setEnabled(False)
        self.btn_execute_replace.clicked.connect(self._on_execute_replace)
        action_bar.addWidget(self.btn_execute_replace)

        self.mode1_status_label = QLabel("")
        self.mode1_status_label.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-size: 12px;")
        action_bar.addWidget(self.mode1_status_label, stretch=1)

        layout.addLayout(action_bar)

        layout.addStretch(1)
        return container

    # ==========================================================================
    # MODE 2: Merge with Already Imported Track
    # ==========================================================================

    def _build_mode2_widget(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 16, 20, 24)
        layout.setSpacing(18)

        desc = QLabel(
            "Use this mode if you have already dragged the new AIFF track into Rekordbox, resulting in "
            "a duplicate track. This tool transfers all history plays, play count, and date added "
            "from the old track (MP3 or AIFF) to the new AIFF track, and soft-deletes the old duplicate."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-size: 12px;")
        layout.addWidget(desc)

        cols_layout = QHBoxLayout()
        cols_layout.setSpacing(16)

        # Left: Source Old Track
        left_box = QVBoxLayout()
        left_box.setSpacing(8)
        left_title = QLabel("1. SOURCE TRACK (Old MP3 or AIFF with History & Plays)")
        left_title.setStyleSheet(f"color: {COLOR_AMBER}; font-size: 11px; font-weight: bold;")
        left_box.addWidget(left_title)

        self.merge_src_input = QLineEdit()
        self.merge_src_input.setPlaceholderText("Search source track (MP3 or AIFF)...")
        self.merge_src_input.textChanged.connect(lambda: self.merge_src_search_timer.start())
        left_box.addWidget(self.merge_src_input)

        self.merge_src_table = QTableWidget()
        self.merge_src_table.setColumnCount(4)
        self.merge_src_table.setHorizontalHeaderLabels(["Title", "Artist", "Plays", "Type"])
        self.merge_src_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.merge_src_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.merge_src_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.merge_src_table.setFixedHeight(180)
        self.merge_src_table.itemSelectionChanged.connect(self._on_merge_src_selected)
        left_box.addWidget(self.merge_src_table)

        self.merge_src_summary = QLabel("No source track selected")
        self.merge_src_summary.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-size: 11px; padding: 6px;")
        left_box.addWidget(self.merge_src_summary)

        cols_layout.addLayout(left_box, stretch=1)

        # Right: Target New Track
        right_box = QVBoxLayout()
        right_box.setSpacing(8)
        right_title = QLabel("2. TARGET TRACK (Newly Imported AIFF)")
        right_title.setStyleSheet(f"color: {COLOR_GREEN}; font-size: 11px; font-weight: bold;")
        right_box.addWidget(right_title)

        self.merge_tgt_input = QLineEdit()
        self.merge_tgt_input.setPlaceholderText("Search target track...")
        self.merge_tgt_input.textChanged.connect(lambda: self.merge_tgt_search_timer.start())
        right_box.addWidget(self.merge_tgt_input)

        self.merge_tgt_table = QTableWidget()
        self.merge_tgt_table.setColumnCount(4)
        self.merge_tgt_table.setHorizontalHeaderLabels(["Title", "Artist", "Plays", "Type"])
        self.merge_tgt_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.merge_tgt_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.merge_tgt_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.merge_tgt_table.setFixedHeight(180)
        self.merge_tgt_table.itemSelectionChanged.connect(self._on_merge_tgt_selected)
        right_box.addWidget(self.merge_tgt_table)

        self.merge_tgt_summary = QLabel("No target track selected")
        self.merge_tgt_summary.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-size: 11px; padding: 6px;")
        right_box.addWidget(self.merge_tgt_summary)

        cols_layout.addLayout(right_box, stretch=1)
        layout.addLayout(cols_layout)

        # Mode 2 Metadata Comparison & Diff Card
        self.mode2_diff_card = QFrame()
        self.mode2_diff_card.setObjectName("mode2DiffCard")
        self.mode2_diff_card.setStyleSheet(
            f"QFrame#mode2DiffCard {{ background-color: #181822; border: 1px solid {COLOR_BORDER}; border-radius: 8px; padding: 14px; }}"
        )
        m2_diff_layout = QVBoxLayout(self.mode2_diff_card)
        m2_diff_layout.setContentsMargins(14, 12, 14, 12)
        m2_diff_layout.setSpacing(10)

        m2_diff_title = QLabel("Metadata Comparison & Merge Diff")
        m2_diff_title.setStyleSheet(f"color: {COLOR_GREEN}; font-size: 13px; font-weight: bold;")
        m2_diff_layout.addWidget(m2_diff_title)

        self.mode2_diff_summary = QLabel("Select both source track and target AIFF track above to compare metadata.")
        self.mode2_diff_summary.setStyleSheet(
            f"background-color: {COLOR_BG_CARD}; border: 1px solid {COLOR_BORDER}; border-radius: 6px; padding: 8px 12px; font-size: 12px; color: {COLOR_TEXT_SECONDARY};"
        )
        m2_diff_layout.addWidget(self.mode2_diff_summary)

        # Inspect 3-column diff tool button
        self.btn_mode2_inspect_diff = QPushButton("🔍  Inspect 3-Column Metadata Diff & Resolve Conflicts...")
        self.btn_mode2_inspect_diff.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {COLOR_BG_CARD};
                color: {COLOR_GREEN};
                border: 1px solid {COLOR_BORDER_LIGHT};
                border-radius: 6px;
                padding: 8px 14px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {COLOR_GREEN_BG};
                border-color: {COLOR_GREEN};
                color: #ffffff;
            }}
            QPushButton:disabled {{
                background-color: transparent;
                color: {COLOR_TEXT_MUTED};
                border-color: {COLOR_BORDER};
            }}
            """
        )
        self.btn_mode2_inspect_diff.setEnabled(False)
        self.btn_mode2_inspect_diff.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_mode2_inspect_diff.clicked.connect(self._on_execute_merge)
        m2_diff_layout.addWidget(self.btn_mode2_inspect_diff)

        layout.addWidget(self.mode2_diff_card)

        # Merge Action Button
        merge_action_row = QHBoxLayout()
        merge_action_row.setSpacing(12)

        self.btn_execute_merge = QPushButton("🔍  Review Differences & Replace Track...")
        self.btn_execute_merge.setToolTip("Opens the 3-column metadata diff screen to inspect conflicts and confirm replacement.")
        self.btn_execute_merge.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {COLOR_GREEN};
                color: #ffffff;
                border: none;
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
                padding: 12px 24px;
            }}
            QPushButton:hover {{
                background-color: #1cd496;
            }}
            QPushButton:disabled {{
                background-color: #2e2e38;
                color: #686c7a;
            }}
            """
        )
        self.btn_execute_merge.setEnabled(False)
        self.btn_execute_merge.clicked.connect(self._on_execute_merge)
        merge_action_row.addWidget(self.btn_execute_merge)

        self.merge_status_label = QLabel("")
        self.merge_status_label.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-size: 12px;")
        merge_action_row.addWidget(self.merge_status_label, stretch=1)

        layout.addLayout(merge_action_row)
        layout.addStretch(1)
        return container

    # ==========================================================================
    # Logic & Event Handlers
    # ==========================================================================

    def _reset_mode1(self) -> None:
        self.selected_track = None
        self.replacement_file_path = None
        self.replacement_specs = None
        self.mode1_track_title.setText("No Track Selected")
        self.mode1_track_artist.setText("Select a track from search results above")
        self.mode1_track_path_label.setText("")
        self.mode1_format_chip.setText("—")
        self.mode1_plays_chip.setText("0 plays")
        self.mode1_added_chip.setText("Added: —")
        self.mode1_art_label.setText("🎵")
        self.old_specs_label.setText("No track selected")
        self.new_specs_label.setText("No replacement file chosen yet")
        self.duration_match_banner.setText("Waiting for replacement file...")
        self.duration_match_banner.setStyleSheet(
            f"background-color: {COLOR_BG_DARK}; border: 1px solid {COLOR_BORDER}; border-radius: 6px; padding: 8px 12px; font-size: 12px; color: {COLOR_TEXT_SECONDARY};"
        )
        if hasattr(self, "mode1_diff_summary"):
            self.mode1_diff_summary.setText("Select both a track and a replacement file to compare metadata.")
            self.mode1_diff_summary.setStyleSheet(
                f"background-color: {COLOR_BG_CARD}; border: 1px solid {COLOR_BORDER}; border-radius: 6px; padding: 8px 12px; font-size: 12px; color: {COLOR_TEXT_SECONDARY};"
            )
        if hasattr(self, "btn_mode1_inspect_diff"):
            self.btn_mode1_inspect_diff.setEnabled(False)
        self.btn_execute_replace.setEnabled(False)
        self.mode1_results_table.setRowCount(0)

    def _reset_mode2(self) -> None:
        self.merge_source_track = None
        self.merge_target_track = None
        self.merge_src_summary.setText("No source track selected")
        self.merge_tgt_summary.setText("No target track selected")
        if hasattr(self, "mode2_diff_summary"):
            self.mode2_diff_summary.setText("Select both source track and target AIFF track above to compare metadata.")
            self.mode2_diff_summary.setStyleSheet(
                f"background-color: {COLOR_BG_CARD}; border: 1px solid {COLOR_BORDER}; border-radius: 6px; padding: 8px 12px; font-size: 12px; color: {COLOR_TEXT_SECONDARY};"
            )
        if hasattr(self, "btn_mode2_inspect_diff"):
            self.btn_mode2_inspect_diff.setEnabled(False)
        self.btn_execute_merge.setEnabled(False)
        self.merge_src_table.setRowCount(0)
        self.merge_tgt_table.setRowCount(0)

    def _execute_mode1_search(self) -> None:
        if not self.engine:
            return
        query = self.mode1_search_input.text().strip()
        if not query:
            return

        self.mode1_search_btn.setEnabled(False)
        self.search_worker = TrackSearchWorker(self.engine, query, limit=50)
        self.search_worker.success.connect(self._on_mode1_search_success)
        self.search_worker.error.connect(lambda err: self.notify_status.emit(f"Search error: {err}"))
        self.search_worker.start()

    def _on_mode1_search_success(self, query: str, records: List[Dict[str, Any]]) -> None:
        self.mode1_search_btn.setEnabled(True)
        self.mode1_results_table.setRowCount(0)
        matching_records = [r for r in records if is_replaceable_track(r)]
        if records and not matching_records:
            self.mode1_status_label.setText(
                f"ℹ️ Found {len(records)} track(s) matching '{query}', but none are MP3 or AIFF format. (Currently only MP3 and AIFF tracks can be replaced)."
            )
        else:
            self.mode1_status_label.setText("")

        for row_idx, r in enumerate(matching_records):
            self.mode1_results_table.insertRow(row_idx)

            t_item = QTableWidgetItem(r.get("title") or "")
            t_item.setData(Qt.ItemDataRole.UserRole, r)
            self.mode1_results_table.setItem(row_idx, 0, t_item)

            self.mode1_results_table.setItem(row_idx, 1, QTableWidgetItem(r.get("artist") or ""))
            self.mode1_results_table.setItem(row_idx, 2, QTableWidgetItem(r.get("file_type") or "—"))
            self.mode1_results_table.setItem(row_idx, 3, QTableWidgetItem(str(r.get("play_count") or 0)))
            self.mode1_results_table.setItem(row_idx, 4, QTableWidgetItem(r.get("date_added") or "—"))
            self.mode1_results_table.setItem(row_idx, 5, QTableWidgetItem(r.get("file_path") or ""))

    def _on_mode1_table_selection_changed(self) -> None:
        sel = self.mode1_results_table.selectedItems()
        if sel:
            row = sel[0].row()
            item = self.mode1_results_table.item(row, 0)
            if item:
                track_data = item.data(Qt.ItemDataRole.UserRole)
                if track_data:
                    self._on_track_selected_mode1(track_data)

    def _on_track_selected_mode1(self, track: Dict[str, Any]) -> None:
        if not is_replaceable_track(track):
            fmt = track.get("file_type") or "non-MP3/AIFF"
            QMessageBox.warning(
                self,
                "Unsupported Track Format",
                f"Currently, Replace File supports MP3 and AIFF tracks.\n\n"
                f"The selected track '{track.get('title')}' is a {fmt} file.",
            )
            return

        self.selected_track = dict(track)
        self.mode1_track_title.setText(track.get("title") or "(Untitled)")
        self.mode1_track_artist.setText(track.get("artist") or "Unknown Artist")
        self.mode1_track_path_label.setText(track.get("file_path") or "")

        ft = track.get("file_type") or "Audio"
        br = track.get("bitrate")
        br_str = f" · {br} kbps" if br else ""
        self.mode1_format_chip.setText(f"{ft}{br_str}")
        if is_aiff_track(track):
            self.mode1_format_chip.setStyleSheet(f"background-color: {COLOR_BLUE_BG}; color: {COLOR_BLUE}; border: 1px solid {COLOR_BLUE}; border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: bold;")
        else:
            self.mode1_format_chip.setStyleSheet(f"background-color: {COLOR_AMBER_BG}; color: {COLOR_AMBER}; border: 1px solid {COLOR_AMBER_BORDER}; border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: bold;")
        self.mode1_plays_chip.setText(f"{track.get('play_count', 0)} plays")
        self.mode1_added_chip.setText(f"Added: {track.get('date_added') or '—'}")

        # Check if already chosen replacement file is identical to this track
        if self.replacement_file_path and track.get("file_path"):
            if os.path.abspath(self.replacement_file_path) == os.path.abspath(track.get("file_path") or ""):
                self.replacement_file_path = None
                self.replacement_specs = None
                self.new_specs_label.setText("⚠️ Replacement file was the same as the selected track. Please choose a new AIFF file.")
                self.duration_match_banner.setText("Waiting for replacement file...")
                self.duration_match_banner.setStyleSheet(
                    f"background-color: {COLOR_BG_DARK}; border: 1px solid {COLOR_BORDER}; border-radius: 6px; padding: 8px 12px; font-size: 12px; color: {COLOR_TEXT_SECONDARY};"
                )

        # Artwork
        img_p = resolve_artwork_path(track.get("image_path"), db_path=self.db_path)
        if img_p and os.path.exists(img_p):
            pix = QPixmap(img_p).scaled(
                54,
                54,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.mode1_art_label.setPixmap(pix)
            self.mode1_art_label.setText("")
        else:
            self.mode1_art_label.setPixmap(QPixmap())
            self.mode1_art_label.setText("🎵")

        # Update Old File Summary in Diff Card
        size_str = format_file_size(track.get("file_size", 0))
        dur_str = track.get("duration_str", "--:--")
        self.old_specs_label.setText(
            f"<b>{ft}</b> {br_str}<br>"
            f"Duration: <b>{dur_str}</b> · Size: <b>{size_str}</b><br>"
            f"<span style='color:{COLOR_TEXT_MUTED}; font-size:11px;'>{os.path.basename(track.get('file_path') or '')}</span>"
        )

        self._check_mode1_ready()

    def _on_new_file_selected(self, path: str) -> None:
        ext = os.path.splitext(path)[1].lower()
        if ext not in (".aiff", ".aif"):
            QMessageBox.warning(
                self,
                "Unsupported Replacement Format",
                f"Currently only AIFF replacement files (.aiff, .aif) are supported.\n\n"
                f"The selected file has format '{ext.upper()}'. Please select an AIFF file.",
            )
            return

        if self.selected_track and self.selected_track.get("file_path"):
            if os.path.abspath(path) == os.path.abspath(self.selected_track["file_path"]):
                QMessageBox.warning(
                    self,
                    "Identical File Selected",
                    "The selected replacement file is identical to the current track's audio file.\n\n"
                    "Please choose a different AIFF audio file to replace it.",
                )
                return

        self.replacement_file_path = path
        self.new_specs_label.setText(f"Inspecting audio file: {os.path.basename(path)}...")

        self.inspect_worker = AudioInspectWorker(path, parent=self)
        self.inspect_worker.success.connect(self._on_audio_inspected)
        self.inspect_worker.error.connect(lambda err: self.new_specs_label.setText(f"❌ Error reading file: {err}"))
        self.inspect_worker.start()

    def _on_audio_inspected(self, info: Dict[str, Any]) -> None:
        if info.get("file_type") != 12 and info.get("extension") not in ("aiff", "aif"):
            self.new_specs_label.setText(
                f"<b style='color:{COLOR_RED};'>Unsupported File Format ({info.get('format_name')})</b><br>"
                f"Currently only AIFF files (.aiff, .aif) are supported."
            )
            self.duration_match_banner.setText("❌ Replacement file must be an AIFF audio file (.aiff, .aif)")
            self.duration_match_banner.setStyleSheet(
                f"background-color: {COLOR_RED_BG}; border: 1px solid {COLOR_RED_BORDER}; border-radius: 6px; padding: 8px 12px; font-size: 12px; color: {COLOR_TEXT_PRIMARY};"
            )
            self.replacement_specs = None
            self._check_mode1_ready()
            return

        self.replacement_specs = info

        fmt = info["format_name"]
        br = info["bitrate"]
        srate = info["sample_rate"]
        bdepth = info["bit_depth"]
        dur_str = info["duration_str"]
        size_str = format_file_size(info["file_size"])

        self.new_specs_label.setText(
            f"<b style='color:{COLOR_GREEN}; font-size:13px;'>{fmt}</b> · {br} kbps · {bdepth}-bit · {srate / 1000.0:.1f} kHz<br>"
            f"Duration: <b>{dur_str}</b> · Size: <b>{size_str}</b><br>"
            f"<span style='color:{COLOR_TEXT_MUTED}; font-size:11px;'>{info['file_name']}</span>"
        )

        # Compare Durations
        if self.selected_track:
            old_dur = int(self.selected_track.get("duration_sec") or 0)
            new_dur = int(info["duration_sec"] or 0)

            if old_dur > 0 and new_dur > 0:
                diff = new_dur - old_dur
                if abs(diff) <= 1:
                    self.duration_match_banner.setText(
                        f"✅ <b>Perfect Timing Match!</b> (Old: {self.selected_track.get('duration_str')}, New: {dur_str} · Diff: {diff:+}s). "
                        "Beatgrids and Hot Cues will align seamlessly."
                    )
                    self.duration_match_banner.setStyleSheet(
                        f"background-color: {COLOR_GREEN_BG}; border: 1px solid {COLOR_GREEN_BORDER}; border-radius: 6px; padding: 8px 12px; font-size: 12px; color: {COLOR_TEXT_PRIMARY};"
                    )
                elif abs(diff) <= 5:
                    self.duration_match_banner.setText(
                        f"⚠️ <b>Minor Timing Difference:</b> {diff:+} seconds. (Old: {self.selected_track.get('duration_str')}, New: {dur_str}). "
                        "Compatible audio file."
                    )
                    self.duration_match_banner.setStyleSheet(
                        f"background-color: {COLOR_AMBER_BG}; border: 1px solid {COLOR_AMBER_BORDER}; border-radius: 6px; padding: 8px 12px; font-size: 12px; color: {COLOR_TEXT_PRIMARY};"
                    )
                else:
                    self.duration_match_banner.setText(
                        f"⚠️ <b>Significant Duration Difference:</b> {diff:+} seconds. (Old: {self.selected_track.get('duration_str')}, New: {dur_str}). "
                        "Please verify this is the same version/mix of the track before proceeding."
                    )
                    self.duration_match_banner.setStyleSheet(
                        f"background-color: {COLOR_RED_BG}; border: 1px solid {COLOR_RED_BORDER}; border-radius: 6px; padding: 8px 12px; font-size: 12px; color: {COLOR_TEXT_PRIMARY};"
                    )

        self._check_mode1_ready()

    def _check_mode1_ready(self) -> None:
        is_same_file = False
        if self.selected_track and self.replacement_file_path and self.selected_track.get("file_path"):
            is_same_file = os.path.abspath(self.replacement_file_path) == os.path.abspath(self.selected_track["file_path"])

        can_execute = (
            self.engine is not None
            and self.selected_track is not None
            and is_replaceable_track(self.selected_track)
            and self.replacement_file_path is not None
            and not is_same_file
            and self.replacement_specs is not None
            and (self.replacement_specs.get("file_type") == 12 or self.replacement_specs.get("extension") in ("aiff", "aif"))
        )
        self.btn_execute_replace.setEnabled(can_execute)
        if hasattr(self, "btn_mode1_inspect_diff"):
            self.btn_mode1_inspect_diff.setEnabled(can_execute)

        if hasattr(self, "mode1_diff_summary"):
            if can_execute and self.engine:
                try:
                    content_id = str(self.selected_track.get("id") or self.selected_track.get("content_id"))
                    full_old = fetch_track_full_info(self.engine, content_id) or self.selected_track
                    diffs = calculate_metadata_diff(full_old, self.replacement_specs, mode="file")
                    conflicts = [d for d in diffs if d.category == "info" and d.status == "conflict"]
                    new_tags = [d for d in diffs if d.category == "info" and d.status == "new_data"]
                    if conflicts:
                        conflict_names = ", ".join(d.label for d in conflicts[:4])
                        more_str = f" (+{len(conflicts) - 4} more)" if len(conflicts) > 4 else ""
                        self.mode1_diff_summary.setText(
                            f"⚠️ <b>{len(conflicts)} Metadata conflict(s) detected:</b> {conflict_names}{more_str}.<br>"
                            "<span style='color:#a0a0b2;'>Click below to review the 3-column diff and choose which values to keep.</span>"
                        )
                        self.mode1_diff_summary.setStyleSheet(
                            f"background-color: {COLOR_AMBER_BG}; border: 1px solid {COLOR_AMBER_BORDER}; border-radius: 6px; padding: 8px 12px; font-size: 12px; color: {COLOR_TEXT_PRIMARY};"
                        )
                    elif new_tags:
                        self.mode1_diff_summary.setText(
                            f"⚡ <b>{len(new_tags)} New metadata tag(s) found in replacement file.</b> All existing tags match."
                        )
                        self.mode1_diff_summary.setStyleSheet(
                            f"background-color: {COLOR_BLUE_BG}; border: 1px solid {COLOR_BLUE}; border-radius: 6px; padding: 8px 12px; font-size: 12px; color: {COLOR_TEXT_PRIMARY};"
                        )
                    else:
                        self.mode1_diff_summary.setText("✅ <b>All track metadata tags match perfectly!</b> Audio will be upgraded cleanly.")
                        self.mode1_diff_summary.setStyleSheet(
                            f"background-color: {COLOR_GREEN_BG}; border: 1px solid {COLOR_GREEN_BORDER}; border-radius: 6px; padding: 8px 12px; font-size: 12px; color: {COLOR_TEXT_PRIMARY};"
                        )
                except Exception:
                    self.mode1_diff_summary.setText("Ready to review differences and replace audio file.")
            elif not self.selected_track:
                self.mode1_diff_summary.setText("Select a track in your collection above to compare metadata.")
                self.mode1_diff_summary.setStyleSheet(
                    f"background-color: {COLOR_BG_CARD}; border: 1px solid {COLOR_BORDER}; border-radius: 6px; padding: 8px 12px; font-size: 12px; color: {COLOR_TEXT_SECONDARY};"
                )
            else:
                self.mode1_diff_summary.setText("Choose a replacement AIFF file on disk above to compare metadata.")
                self.mode1_diff_summary.setStyleSheet(
                    f"background-color: {COLOR_BG_CARD}; border: 1px solid {COLOR_BORDER}; border-radius: 6px; padding: 8px 12px; font-size: 12px; color: {COLOR_TEXT_SECONDARY};"
                )

    def _on_execute_replace(self) -> None:
        if not self.engine or not self.selected_track or not self.replacement_file_path or not self.replacement_specs:
            return

        if is_rekordbox_running():
            QMessageBox.warning(
                self,
                "Rekordbox Running",
                "⚠️ Rekordbox is currently running!\n\n"
                "Please quit Rekordbox (Cmd + Q) before replacing collection files to prevent database corruption.",
            )
            return

        content_id = str(self.selected_track.get("id") or self.selected_track.get("content_id"))
        full_old = fetch_track_full_info(self.engine, content_id) or self.selected_track

        # Prepare base options from form
        act_idx = self.old_file_combo.currentIndex()
        action_val = "keep" if act_idx == 0 else ("trash" if act_idx == 1 else "backup")
        options = {
            "old_file_action": action_val,
            "move_new_file_to_track_folder": self.chk_move_to_folder.isChecked(),
        }

        diff_dialog = TrackReplacementDiffDialog(
            engine=self.engine,
            mode="file",
            old_track=full_old,
            new_item=self.replacement_specs,
            options=options,
            db_path=self.db_path,
            parent=self,
        )

        if diff_dialog.exec() != QDialog.DialogCode.Accepted:
            return

        final_options = diff_dialog.get_options()

        self.btn_execute_replace.setEnabled(False)
        if hasattr(self, "btn_mode1_inspect_diff"):
            self.btn_mode1_inspect_diff.setEnabled(False)
        self.mode1_status_label.setText("Replacing audio file in master.db...")

        self.replace_worker = TrackReplaceWorker(
            self.engine,
            content_id,
            self.replacement_file_path,
            options=final_options,
            parent=self,
        )
        self.replace_worker.success.connect(self._on_replace_success)
        self.replace_worker.error.connect(self._on_replace_error)
        self.replace_worker.start()

    def _on_replace_success(self, res: Dict[str, Any]) -> None:
        self.btn_execute_replace.setEnabled(True)
        self.mode1_status_label.setText("✅ Audio file successfully replaced!")

        success_msg = (
            f"🎉 Successfully Replaced Audio File!\n\n"
            f"Track: {res['title']} (ID: {res['content_id']})\n"
            f"New File: {res['new_file']['filename']} ({res['new_file']['format']}, {res['new_file']['bitrate']} kbps)\n\n"
            f"✅ {res['play_count']} plays preserved\n"
            f"✅ Date added {res['stock_date']} preserved\n"
            f"✅ {res['history_plays_preserved']} history session plays preserved\n"
            f"✅ {res['cues_preserved']} cue points preserved\n"
            f"✅ {res['playlists_preserved']} playlist assignments preserved\n"
        )
        QMessageBox.information(self, "Replacement Complete", success_msg)
        self.notify_status.emit(f"Replaced audio file for '{res['title']}' with {res['new_file']['format']}")
        self.db_saved.emit()

        # Reset form
        self._reset_mode1()

    def _on_replace_error(self, err: str) -> None:
        self.btn_execute_replace.setEnabled(True)
        if hasattr(self, "btn_mode1_inspect_diff"):
            self.btn_mode1_inspect_diff.setEnabled(True)
        self.mode1_status_label.setText(f"❌ Error: {err}")
        QMessageBox.critical(self, "Replacement Error", f"Failed to replace audio file:\n\n{err}")

    # ==========================================================================
    # MODE 2: Merge Search & Handlers
    # ==========================================================================

    def _execute_merge_src_search(self) -> None:
        if not self.engine:
            return
        query = self.merge_src_input.text().strip()
        if not query:
            return
        worker = TrackSearchWorker(self.engine, query, limit=30, parent=self)
        worker.success.connect(
            lambda _, recs: self._populate_merge_table(
                self.merge_src_table, [r for r in recs if is_replaceable_track(r)]
            )
        )
        worker.start()

    def _execute_merge_tgt_search(self) -> None:
        if not self.engine:
            return
        query = self.merge_tgt_input.text().strip()
        if not query:
            return
        worker = TrackSearchWorker(self.engine, query, limit=30, parent=self)
        worker.success.connect(
            lambda _, recs: self._populate_merge_table(
                self.merge_tgt_table, [r for r in recs if is_aiff_track(r)]
            )
        )
        worker.start()

    def _populate_merge_table(self, table: QTableWidget, records: List[Dict[str, Any]]) -> None:
        table.setRowCount(0)
        for row_idx, r in enumerate(records):
            table.insertRow(row_idx)
            t_item = QTableWidgetItem(r.get("title") or "")
            t_item.setData(Qt.ItemDataRole.UserRole, r)
            table.setItem(row_idx, 0, t_item)
            table.setItem(row_idx, 1, QTableWidgetItem(r.get("artist") or ""))
            table.setItem(row_idx, 2, QTableWidgetItem(str(r.get("play_count", 0))))
            table.setItem(row_idx, 3, QTableWidgetItem(r.get("file_type") or "—"))

    def _on_merge_src_selected(self) -> None:
        sel = self.merge_src_table.selectedItems()
        if sel:
            track = sel[0].data(Qt.ItemDataRole.UserRole)
            if track:
                if not is_replaceable_track(track):
                    self.merge_source_track = None
                    self.merge_src_summary.setText("<span style='color:#ff5252;'>Selected source track must be an MP3 or AIFF file.</span>")
                else:
                    self.merge_source_track = track
                    self.merge_src_summary.setText(
                        f"Selected Source: <b>{track.get('title')}</b> — {track.get('play_count', 0)} plays, "
                        f"Added: {track.get('date_added')}, Type: {track.get('file_type')}"
                    )
        self._check_mode2_ready()

    def _on_merge_tgt_selected(self) -> None:
        sel = self.merge_tgt_table.selectedItems()
        if sel:
            track = sel[0].data(Qt.ItemDataRole.UserRole)
            if track:
                if not is_aiff_track(track):
                    self.merge_target_track = None
                    self.merge_tgt_summary.setText("<span style='color:#ff5252;'>Selected target track must be an AIFF file.</span>")
                else:
                    self.merge_target_track = track
                    self.merge_tgt_summary.setText(
                        f"Selected Target: <b>{track.get('title')}</b> — Type: {track.get('file_type')}, "
                        f"Path: {os.path.basename(track.get('file_path') or '')}"
                    )
        self._check_mode2_ready()

    def _check_mode2_ready(self) -> None:
        can_merge = (
            self.engine is not None
            and self.merge_source_track is not None
            and is_replaceable_track(self.merge_source_track)
            and self.merge_target_track is not None
            and is_aiff_track(self.merge_target_track)
            and str(self.merge_source_track.get("id")) != str(self.merge_target_track.get("id"))
        )
        self.btn_execute_merge.setEnabled(can_merge)
        if hasattr(self, "btn_mode2_inspect_diff"):
            self.btn_mode2_inspect_diff.setEnabled(can_merge)

        if hasattr(self, "mode2_diff_summary"):
            if can_merge and self.engine:
                try:
                    src_id = str(self.merge_source_track.get("id") or self.merge_source_track.get("content_id"))
                    tgt_id = str(self.merge_target_track.get("id") or self.merge_target_track.get("content_id"))
                    full_src = fetch_track_full_info(self.engine, src_id) or self.merge_source_track
                    full_tgt = fetch_track_full_info(self.engine, tgt_id) or self.merge_target_track
                    diffs = calculate_metadata_diff(full_src, full_tgt, mode="merge")
                    conflicts = [d for d in diffs if d.category == "info" and d.status == "conflict"]
                    new_tags = [d for d in diffs if d.category == "info" and d.status == "new_data"]
                    if conflicts:
                        conflict_names = ", ".join(d.label for d in conflicts[:4])
                        more_str = f" (+{len(conflicts) - 4} more)" if len(conflicts) > 4 else ""
                        self.mode2_diff_summary.setText(
                            f"⚠️ <b>{len(conflicts)} Metadata conflict(s) detected:</b> {conflict_names}{more_str}.<br>"
                            "<span style='color:#a0a0b2;'>Click below to review the 3-column diff and choose which values to keep.</span>"
                        )
                        self.mode2_diff_summary.setStyleSheet(
                            f"background-color: {COLOR_AMBER_BG}; border: 1px solid {COLOR_AMBER_BORDER}; border-radius: 6px; padding: 8px 12px; font-size: 12px; color: {COLOR_TEXT_PRIMARY};"
                        )
                    elif new_tags:
                        self.mode2_diff_summary.setText(
                            f"⚡ <b>{len(new_tags)} New metadata tag(s) found in target track.</b>"
                        )
                        self.mode2_diff_summary.setStyleSheet(
                            f"background-color: {COLOR_BLUE_BG}; border: 1px solid {COLOR_BLUE}; border-radius: 6px; padding: 8px 12px; font-size: 12px; color: {COLOR_TEXT_PRIMARY};"
                        )
                    else:
                        self.mode2_diff_summary.setText("✅ <b>Metadata tags match perfectly!</b> Ready to merge history and replace track.")
                        self.mode2_diff_summary.setStyleSheet(
                            f"background-color: {COLOR_GREEN_BG}; border: 1px solid {COLOR_GREEN_BORDER}; border-radius: 6px; padding: 8px 12px; font-size: 12px; color: {COLOR_TEXT_PRIMARY};"
                        )
                except Exception:
                    self.mode2_diff_summary.setText("Ready to review differences and merge tracks.")
            elif not self.merge_source_track:
                self.mode2_diff_summary.setText("Select source track (with plays & history) to compare.")
                self.mode2_diff_summary.setStyleSheet(
                    f"background-color: {COLOR_BG_CARD}; border: 1px solid {COLOR_BORDER}; border-radius: 6px; padding: 8px 12px; font-size: 12px; color: {COLOR_TEXT_SECONDARY};"
                )
            else:
                self.mode2_diff_summary.setText("Select target AIFF track to compare metadata.")
                self.mode2_diff_summary.setStyleSheet(
                    f"background-color: {COLOR_BG_CARD}; border: 1px solid {COLOR_BORDER}; border-radius: 6px; padding: 8px 12px; font-size: 12px; color: {COLOR_TEXT_SECONDARY};"
                )

    def _on_execute_merge(self) -> None:
        if not self.engine or not self.merge_source_track or not self.merge_target_track:
            return

        if is_rekordbox_running():
            QMessageBox.warning(
                self,
                "Rekordbox Running",
                "⚠️ Rekordbox is currently running!\nPlease quit Rekordbox before merging collection tracks.",
            )
            return

        src_id = str(self.merge_source_track.get("id") or self.merge_source_track.get("content_id"))
        tgt_id = str(self.merge_target_track.get("id") or self.merge_target_track.get("content_id"))

        full_src = fetch_track_full_info(self.engine, src_id) or self.merge_source_track
        full_tgt = fetch_track_full_info(self.engine, tgt_id) or self.merge_target_track

        options = {
            "old_file_action": "keep",
        }

        diff_dialog = TrackReplacementDiffDialog(
            engine=self.engine,
            mode="merge",
            old_track=full_src,
            new_item=full_tgt,
            options=options,
            db_path=self.db_path,
            parent=self,
        )

        if diff_dialog.exec() != QDialog.DialogCode.Accepted:
            return

        final_options = diff_dialog.get_options()

        self.btn_execute_merge.setEnabled(False)
        if hasattr(self, "btn_mode2_inspect_diff"):
            self.btn_mode2_inspect_diff.setEnabled(False)
        self.merge_status_label.setText("Merging tracks in master.db...")

        self.merge_worker = TrackMergeWorker(
            self.engine,
            src_id,
            tgt_id,
            options=final_options,
            parent=self,
        )
        self.merge_worker.success.connect(self._on_merge_success)
        self.merge_worker.error.connect(self._on_merge_error)
        self.merge_worker.start()

    def _on_merge_success(self, res: Dict[str, Any]) -> None:
        self.btn_execute_merge.setEnabled(True)
        self.merge_status_label.setText("✅ Tracks successfully merged!")

        msg = (
            f"🎉 Successfully Merged Tracks!\n\n"
            f"Target: {res['title']} (ID: {res['target_id']})\n\n"
            f"✅ Transferred {res['play_count_transferred']} plays\n"
            f"✅ Transferred Date Added: {res['stock_date_transferred']}\n"
            f"✅ Transferred {res['history_transferred']} history sessions\n"
            f"✅ Transferred {res['playlists_transferred']} playlist memberships\n"
            f"✅ Old duplicate track archived.\n"
        )
        QMessageBox.information(self, "Merge Complete", msg)
        self.notify_status.emit(f"Merged history and plays for '{res['title']}'")
        self.db_saved.emit()
        self._reset_mode2()

    def _on_merge_error(self, err: str) -> None:
        self.btn_execute_merge.setEnabled(True)
        if hasattr(self, "btn_mode2_inspect_diff"):
            self.btn_mode2_inspect_diff.setEnabled(True)
        self.merge_status_label.setText(f"❌ Error: {err}")
        QMessageBox.critical(self, "Merge Error", f"Failed to merge tracks:\n\n{err}")

    def cleanup(self) -> None:
        """Stop background replacement workers and timers cleanly."""
        for attr in ("search_worker", "inspect_worker", "replace_worker", "merge_worker"):
            w = getattr(self, attr, None)
            if w:
                stop_worker(w, wait_ms=100)
                setattr(self, attr, None)
