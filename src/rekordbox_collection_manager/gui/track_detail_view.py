"""Track Details View Screen.

Presents comprehensive track metadata (high-DPI artwork, audio specs, tags, playlists,
file paths) and an interactive chronological breakdown of every history session
in which the track was played, with direct navigation to history sets.
Embedded as an in-place view within the application with breadcrumb and back navigation.
"""

import os
from typing import Any, Dict, List, Optional, Set

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, QStringListModel, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QIcon,
    QKeyEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QCompleter,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QLayoutItem,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.engine import Engine

from rekordbox_collection_manager.edit_play_count import (
    add_track_to_playlist,
    add_track_to_tag,
    fetch_all_playlists,
    fetch_all_tags,
    remove_track_from_playlist,
    remove_track_from_tag,
)
from rekordbox_collection_manager.gui.chip import (
    BadgeChip,
    ChipSearchLineEdit,
    EditableChipsContainer,
    FlowLayout,
    PlaylistChip,
    PlaylistChipsContainer,
    PlaylistSearchLineEdit,
    TagChip,
    TagChipsContainer,
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
    COLOR_BLUE_PRESSED,
    COLOR_BORDER,
    COLOR_BORDER_FOCUS,
    COLOR_BORDER_LIGHT,
    COLOR_GREEN,
    COLOR_GREEN_BG,
    COLOR_GREEN_BORDER,
    COLOR_RED,
    COLOR_RED_BG,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_SECONDARY,
)
from rekordbox_collection_manager.gui.workers import TrackDetailWorker
from rekordbox_collection_manager.utils import (
    is_rekordbox_running,
    open_in_folder,
    parse_history_name,
    reveal_in_file_manager,
)


def format_file_size(size_bytes: int) -> str:
    """Format file size in bytes to human-readable MB or KB string."""
    if not size_bytes or size_bytes <= 0:
        return "—"
    mb = size_bytes / (1024 * 1024)
    if mb >= 1.0:
        return f"{mb:.1f} MB"
    kb = size_bytes / 1024
    return f"{kb:.0f} KB"


def format_rating_stars(rating: int) -> str:
    """Format numeric rating (0..255 or 1..5) to 5-star string."""
    if not rating or rating <= 0:
        return "☆☆☆☆☆"
    if rating > 5:
        stars = max(1, min(5, round(rating / 51.0)))
    else:
        stars = max(1, min(5, rating))
    return "★" * stars + "☆" * (5 - stars)


class TrackDetailView(QWidget):
    """Rich in-place view displaying complete track details and live history sets."""

    back_clicked = Signal()
    open_history_session_requested = Signal(str)  # Emits history_id
    status_message = Signal(str)
    track_changed = Signal(int, dict)  # Emits (current_index, track_dict)
    replace_file_requested = Signal(dict)  # Emits track_dict to open Replace File tool

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        engine: Optional[Engine] = None,
        track_data: Optional[Dict[str, Any]] = None,
        db_path: Optional[str] = None,
        tracks_list: Optional[List[Dict[str, Any]]] = None,
        current_index: int = 0,
        back_button_text: str = "← Back to Tracks",
    ):
        super().__init__(parent)
        self.engine = engine
        self.track: Dict[str, Any] = dict(track_data) if track_data else {}
        self.db_path = db_path
        self.tracks_list = tracks_list or []
        self.current_index = current_index
        self.active_worker: Optional[TrackDetailWorker] = None

        self.original_playlists: List[str] = []
        self.current_playlists: List[str] = []
        self.cached_all_playlists: List[str] = []

        self.original_tags: List[str] = []
        self.current_tags: List[str] = []
        self.cached_all_tags: List[str] = []

        self._init_ui(back_button_text)
        if self.engine and self.track:
            self._load_available_playlists()
            self._load_available_tags()
            self._populate_track_data(self.track)
            self._load_full_details_async()

    def _init_ui(self, back_button_text: str = "← Back to Tracks") -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(16, 14, 16, 14)
        root_layout.setSpacing(12)

        # 1. Top Navigation & Action Toolbar
        top_bar = QHBoxLayout()
        top_bar.setSpacing(10)

        self.back_btn = QPushButton(back_button_text)
        self.back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_btn.setToolTip("Return to previous screen (Esc)")
        self.back_btn.clicked.connect(self._on_back_clicked)
        top_bar.addWidget(self.back_btn)

        self.breadcrumb_label = QLabel("TRACK DETAILS")
        self.breadcrumb_label.setStyleSheet(
            f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 0.8px;"
        )
        top_bar.addWidget(self.breadcrumb_label)

        self.pos_indicator = QLabel("")
        self.pos_indicator.setStyleSheet(
            f"color: {COLOR_TEXT_SECONDARY}; font-size: 11px; font-weight: 600;"
        )
        top_bar.addWidget(self.pos_indicator)

        top_bar.addStretch(1)

        # Prev / Next navigation buttons
        self.prev_btn = QPushButton("◀ Prev")
        self.prev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.prev_btn.setToolTip("Previous track in collection (Left Arrow)")
        self.prev_btn.clicked.connect(self._on_prev_track)
        top_bar.addWidget(self.prev_btn)

        self.next_btn = QPushButton("Next ▶")
        self.next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.next_btn.setToolTip("Next track in collection (Right Arrow)")
        self.next_btn.clicked.connect(self._on_next_track)
        top_bar.addWidget(self.next_btn)

        self.replace_btn = QPushButton("🔄 Replace File...")
        self.replace_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.replace_btn.setToolTip("Upgrade or replace this track's audio file on disk")
        self.replace_btn.clicked.connect(lambda: self.replace_file_requested.emit(self.track))
        top_bar.addWidget(self.replace_btn)

        root_layout.addLayout(top_bar)

        # 2. Scrollable Body Container
        scroll_area = QScrollArea(self)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setStyleSheet(
            f"QScrollArea {{ background-color: transparent; border: none; }} "
            f"QScrollBar:vertical {{ background: {COLOR_BG_PANEL}; width: 10px; border-radius: 5px; }} "
            f"QScrollBar::handle:vertical {{ background: {COLOR_BORDER_LIGHT}; border-radius: 5px; }} "
            f"QScrollBar::handle:vertical:hover {{ background: {COLOR_TEXT_MUTED}; }}"
        )

        content_widget = QWidget()
        content_widget.setStyleSheet("background-color: transparent;")
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 8, 0)
        content_layout.setSpacing(14)

        # 2.1 Hero Card: Artwork, Title, Artist, Album, Format Badges
        self.hero_card = QFrame()
        self.hero_card.setObjectName("heroCard")
        self.hero_card.setStyleSheet(
            f"QFrame#heroCard {{ "
            f"  background-color: {COLOR_BG_PANEL}; "
            f"  border: 1px solid {COLOR_BORDER}; "
            f"  border-radius: 10px; "
            f"  padding: 14px; "
            f"}}"
        )
        hero_layout = QHBoxLayout(self.hero_card)
        hero_layout.setContentsMargins(14, 14, 14, 14)
        hero_layout.setSpacing(18)

        # Artwork Thumbnail (110x110 px)
        self.artwork_label = QLabel()
        self.artwork_label.setFixedSize(110, 110)
        self.artwork_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.artwork_label.setStyleSheet(
            f"background-color: {COLOR_BG_CARD}; "
            f"border: 1px solid {COLOR_BORDER_LIGHT}; "
            f"border-radius: 8px;"
        )
        hero_layout.addWidget(self.artwork_label)

        # Track Primary Metadata Info
        track_info_layout = QVBoxLayout()
        track_info_layout.setSpacing(6)
        track_info_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self.title_label = QLabel("Track Title")
        self.title_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.title_label.setStyleSheet(
            "color: #ffffff; font-size: 19px; font-weight: 700; letter-spacing: -0.3px;"
        )
        track_info_layout.addWidget(self.title_label)

        self.artist_label = QLabel("Artist Name")
        self.artist_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.artist_label.setStyleSheet(
            f"color: {COLOR_TEXT_PRIMARY}; font-size: 14px; font-weight: 600;"
        )
        track_info_layout.addWidget(self.artist_label)

        self.album_year_label = QLabel("Album • Year")
        self.album_year_label.setStyleSheet(
            f"color: {COLOR_TEXT_MUTED}; font-size: 12px; font-weight: 500;"
        )
        track_info_layout.addWidget(self.album_year_label)

        # Hero Badges Row
        self.badges_layout = QHBoxLayout()
        self.badges_layout.setSpacing(8)
        self.badges_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self.format_badge = QLabel("MP3")
        self.format_badge.setStyleSheet(
            "background-color: #382305; color: #f59e0b; border: 1px solid #784b08; "
            "border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: 700;"
        )
        self.badges_layout.addWidget(self.format_badge)

        self.key_badge = QLabel("Key: —")
        self.key_badge.setStyleSheet(
            f"background-color: {COLOR_BLUE_BG}; color: {COLOR_BLUE}; border: 1px solid #005f9e; "
            "border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: 700;"
        )
        self.badges_layout.addWidget(self.key_badge)

        self.play_count_badge = QLabel("0 plays")
        self.play_count_badge.setStyleSheet(
            f"background-color: {COLOR_GREEN_BG}; color: {COLOR_GREEN}; border: 1px solid {COLOR_GREEN_BORDER}; "
            "border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: 700;"
        )
        self.badges_layout.addWidget(self.play_count_badge)

        self.rating_label = QLabel("☆☆☆☆☆")
        self.rating_label.setStyleSheet(f"color: {COLOR_AMBER}; font-size: 13px; font-weight: 700;")
        self.badges_layout.addWidget(self.rating_label)

        self.badges_layout.addStretch(1)
        track_info_layout.addLayout(self.badges_layout)

        hero_layout.addLayout(track_info_layout, stretch=1)
        content_layout.addWidget(self.hero_card)

        # 2.2 Quick Stats / KPI Metric Cards Grid (6 Cards)
        stats_frame = QFrame()
        stats_frame.setObjectName("statsFrame")
        stats_frame.setStyleSheet(
            f"QFrame#statsFrame {{ background-color: {COLOR_BG_CARD}; border: 1px solid {COLOR_BORDER}; "
            "border-radius: 8px; padding: 6px; }"
        )
        stats_layout = QHBoxLayout(stats_frame)
        stats_layout.setContentsMargins(10, 8, 10, 8)
        stats_layout.setSpacing(10)

        self.bpm_kpi_val = self._create_kpi_card(stats_layout, "TEMPO (BPM)", "—")
        self.duration_kpi_val = self._create_kpi_card(stats_layout, "DURATION", "--:--")
        self.audio_spec_kpi_val = self._create_kpi_card(stats_layout, "AUDIO QUALITY", "—")
        self.filesize_kpi_val = self._create_kpi_card(stats_layout, "FILE SIZE", "—")
        self.date_added_kpi_val = self._create_kpi_card(stats_layout, "DATE ADDED", "—")
        self.history_kpi_val = self._create_kpi_card(stats_layout, "HISTORY SETS", "0 sessions")

        content_layout.addWidget(stats_frame)

        # 2.3 Categorization Card: Tags, Playlists, Comments
        cat_card = QFrame()
        cat_card.setObjectName("catCard")
        cat_card.setStyleSheet(
            f"QFrame#catCard {{ background-color: {COLOR_BG_PANEL}; border: 1px solid {COLOR_BORDER}; "
            "border-radius: 8px; }"
        )
        cat_layout = QVBoxLayout(cat_card)
        cat_layout.setContentsMargins(14, 12, 14, 12)
        cat_layout.setSpacing(10)

        # Tags Row
        tags_header_layout = QHBoxLayout()
        tags_title = QLabel("MY TAGS")
        tags_title.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 10px; font-weight: 700; letter-spacing: 0.6px;")
        tags_header_layout.addWidget(tags_title)
        tags_header_layout.addStretch(1)
        cat_layout.addLayout(tags_header_layout)

        self.tags_container = TagChipsContainer(parent=self)
        self.tags_container.tag_added.connect(self._on_tag_added_or_removed)
        self.tags_container.tag_removed.connect(self._on_tag_added_or_removed)
        cat_layout.addWidget(self.tags_container)

        # Playlists Row
        playlists_header_layout = QHBoxLayout()
        playlists_title = QLabel("PLAYLISTS")
        playlists_title.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 10px; font-weight: 700; letter-spacing: 0.6px;")
        playlists_header_layout.addWidget(playlists_title)
        playlists_header_layout.addStretch(1)
        cat_layout.addLayout(playlists_header_layout)

        self.playlists_container = PlaylistChipsContainer(parent=self)
        self.playlists_container.playlist_added.connect(self._on_playlist_added_or_removed)
        self.playlists_container.playlist_removed.connect(self._on_playlist_added_or_removed)
        cat_layout.addWidget(self.playlists_container)

        # Comments (if present)
        self.comment_container = QWidget()
        comment_inner_layout = QVBoxLayout(self.comment_container)
        comment_inner_layout.setContentsMargins(0, 4, 0, 0)
        comment_inner_layout.setSpacing(4)

        comment_title = QLabel("COMMENT / NOTES")
        comment_title.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 10px; font-weight: 700; letter-spacing: 0.6px;")
        comment_inner_layout.addWidget(comment_title)

        self.comment_box = QLabel("")
        self.comment_box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.comment_box.setStyleSheet(
            f"background-color: {COLOR_BG_CARD}; border: 1px solid {COLOR_BORDER}; "
            f"border-radius: 6px; padding: 8px 10px; color: {COLOR_TEXT_PRIMARY}; font-size: 12px;"
        )
        self.comment_box.setWordWrap(True)
        comment_inner_layout.addWidget(self.comment_box)
        cat_layout.addWidget(self.comment_container)

        content_layout.addWidget(cat_card)

        # 2.4 File Location & Quick Actions Card
        loc_card = QFrame()
        loc_card.setObjectName("locCard")
        loc_card.setStyleSheet(
            f"QFrame#locCard {{ background-color: {COLOR_BG_PANEL}; border: 1px solid {COLOR_BORDER}; "
            "border-radius: 8px; }"
        )
        loc_layout = QVBoxLayout(loc_card)
        loc_layout.setContentsMargins(14, 12, 14, 12)
        loc_layout.setSpacing(8)

        loc_top = QHBoxLayout()
        loc_title = QLabel("FILE LOCATION")
        loc_title.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 10px; font-weight: 700; letter-spacing: 0.6px;")
        loc_top.addWidget(loc_title)
        loc_top.addStretch(1)

        # File actions
        self.btn_open_folder = QPushButton("📁 Open Folder")
        self.btn_open_folder.setToolTip("Open containing folder in Finder/Explorer")
        self.btn_open_folder.clicked.connect(self._on_open_folder)
        loc_top.addWidget(self.btn_open_folder)

        self.btn_reveal_file = QPushButton("🔍 Reveal File")
        self.btn_reveal_file.setToolTip("Reveal and highlight file in Finder/Explorer")
        self.btn_reveal_file.clicked.connect(self._on_reveal_file)
        loc_top.addWidget(self.btn_reveal_file)

        self.btn_copy_path = QPushButton("📋 Copy Path")
        self.btn_copy_path.setToolTip("Copy file path to clipboard")
        self.btn_copy_path.clicked.connect(self._on_copy_path)
        loc_top.addWidget(self.btn_copy_path)

        self.btn_copy_track = QPushButton("📋 Copy Track")
        self.btn_copy_track.setToolTip("Copy 'Artist - Title' to clipboard")
        self.btn_copy_track.clicked.connect(self._on_copy_track)
        loc_top.addWidget(self.btn_copy_track)

        loc_layout.addLayout(loc_top)

        self.filepath_label = QLabel("")
        self.filepath_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.filepath_label.setStyleSheet(
            f"color: {COLOR_TEXT_SECONDARY}; font-family: 'Menlo', 'Courier New', monospace; font-size: 11px;"
        )
        self.filepath_label.setWordWrap(True)
        loc_layout.addWidget(self.filepath_label)

        content_layout.addWidget(loc_card)

        # 2.5 History Sessions Played Section (The Star Feature)
        history_section = QFrame()
        history_section.setStyleSheet(
            f"background-color: {COLOR_BG_PANEL}; border: 1px solid {COLOR_BORDER}; "
            "border-radius: 8px;"
        )
        history_layout = QVBoxLayout(history_section)
        history_layout.setContentsMargins(14, 12, 14, 12)
        history_layout.setSpacing(10)

        history_header_layout = QHBoxLayout()
        self.history_title_label = QLabel("🎧 PLAYED IN HISTORY SESSIONS (0)")
        self.history_title_label.setStyleSheet(
            "color: #ffffff; font-size: 13px; font-weight: 700; letter-spacing: 0.3px;"
        )
        history_header_layout.addWidget(self.history_title_label)
        history_header_layout.addStretch(1)

        history_tip = QLabel("💡 Click 'View Session →' or double-click row to open session")
        history_tip.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px;")
        history_header_layout.addWidget(history_tip)

        history_layout.addLayout(history_header_layout)

        # History Table
        self.history_table = QTableWidget()
        self.history_table.setColumnCount(7)
        self.history_table.setHorizontalHeaderLabels([
            "#",
            "Session Date",
            "History Session",
            "Location",
            "Played With",
            "Played At",
            "Action",
        ])
        self.history_table.setAlternatingRowColors(True)
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.verticalHeader().setDefaultSectionSize(36)
        self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.history_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.history_table.setMinimumHeight(180)
        self.history_table.setMaximumHeight(360)
        self.history_table.cellDoubleClicked.connect(self._on_history_row_double_clicked)

        h_header = self.history_table.horizontalHeader()
        h_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        h_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.history_table.setColumnWidth(2, 175)
        h_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.history_table.setColumnWidth(3, 135)
        h_header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.history_table.setColumnWidth(4, 220)
        h_header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        h_header.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        self.history_table.setColumnWidth(6, 126)

        for col_idx in (0, 1, 5, 6):
            hdr = self.history_table.horizontalHeaderItem(col_idx)
            if hdr:
                hdr.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        history_layout.addWidget(self.history_table)

        # Empty State Message Frame
        self.history_empty_card = QFrame()
        self.history_empty_card.setStyleSheet(
            f"background-color: {COLOR_BG_CARD}; border: 1px dashed {COLOR_BORDER}; "
            "border-radius: 8px; padding: 20px;"
        )
        empty_layout = QVBoxLayout(self.history_empty_card)
        empty_layout.setContentsMargins(16, 20, 16, 20)
        empty_layout.setSpacing(6)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        empty_icon = QLabel("🕒")
        empty_icon.setStyleSheet("font-size: 24px;")
        empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_icon)

        empty_title = QLabel("No History Plays Recorded")
        empty_title.setStyleSheet("color: #ffffff; font-size: 13px; font-weight: 700;")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_title)

        empty_desc = QLabel(
            "This track has not appeared in any recorded Rekordbox history sessions yet."
        )
        empty_desc.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px;")
        empty_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_desc)

        history_layout.addWidget(self.history_empty_card)
        self.history_empty_card.setVisible(False)

        content_layout.addWidget(history_section)

        scroll_area.setWidget(content_widget)
        root_layout.addWidget(scroll_area, stretch=1)

        # 3. Bottom Save/Discard Bar (Hidden by default; appears when staged edits exist)
        self.bottom_bar = QFrame()
        self.bottom_bar.setObjectName("bottomActionBar")
        self.bottom_bar.setStyleSheet(
            f"QFrame#bottomActionBar {{ "
            f"  background-color: {COLOR_BG_PANEL}; "
            f"  border-top: 1px solid {COLOR_BORDER}; "
            f"  border-radius: 6px; "
            f"}}"
        )
        bottom_layout = QHBoxLayout(self.bottom_bar)
        bottom_layout.setContentsMargins(16, 10, 16, 10)
        bottom_layout.setSpacing(12)

        self.staged_status_label = QLabel("⚠️ You have unsaved playlist modifications")
        self.staged_status_label.setStyleSheet(f"color: {COLOR_AMBER}; font-size: 12px; font-weight: 600;")
        bottom_layout.addWidget(self.staged_status_label)
        bottom_layout.addStretch(1)

        self.discard_btn = QPushButton("Discard Changes")
        self.discard_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.discard_btn.clicked.connect(self._on_discard_changes)
        bottom_layout.addWidget(self.discard_btn)

        self.save_btn = QPushButton("💾 Save Changes")
        self.save_btn.setObjectName("primaryButton")
        self.save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_btn.clicked.connect(self._on_save_changes)
        bottom_layout.addWidget(self.save_btn)

        self.bottom_bar.setVisible(False)
        root_layout.addWidget(self.bottom_bar)

    def load_track(
        self,
        engine: Engine,
        track_data: Dict[str, Any],
        db_path: Optional[str] = None,
        tracks_list: Optional[List[Dict[str, Any]]] = None,
        current_index: int = 0,
        back_button_text: str = "← Back to Tracks",
    ) -> None:
        """Load and display the given track."""
        self._terminate_worker()
        self.engine = engine
        self.track = dict(track_data)
        self.db_path = db_path
        self.tracks_list = tracks_list or []
        self.current_index = current_index
        self.set_back_button_text(back_button_text)
        self._load_available_playlists()
        self._load_available_tags()
        self._populate_track_data(self.track)
        self._load_full_details_async()

    def set_back_button_text(self, text: str) -> None:
        """Update back button label."""
        self.back_btn.setText(text)

    def _create_kpi_card(
        self, parent_layout: QHBoxLayout, title: str, initial_val: str
    ) -> QLabel:
        """Helper to create a standardized KPI metric box."""
        card = QFrame()
        card.setStyleSheet(
            f"background-color: {COLOR_BG_PANEL}; border: 1px solid {COLOR_BORDER}; "
            "border-radius: 6px;"
        )
        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(8, 6, 8, 6)
        vbox.setSpacing(2)
        vbox.setAlignment(Qt.AlignmentFlag.AlignCenter)

        t_lbl = QLabel(title)
        t_lbl.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 9px; font-weight: 700; letter-spacing: 0.5px;")
        t_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vbox.addWidget(t_lbl)

        v_lbl = QLabel(initial_val)
        v_lbl.setStyleSheet("color: #ffffff; font-size: 13px; font-weight: 700;")
        v_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vbox.addWidget(v_lbl)

        parent_layout.addWidget(card, stretch=1)
        return v_lbl

    def _populate_track_data(self, t: Dict[str, Any]) -> None:
        """Populate UI widgets with track data."""
        title = t.get("title") or "(Untitled)"
        artist = t.get("artist") or "Unknown Artist"
        album = t.get("album") or ""
        year = t.get("year") or 0
        genre = t.get("genre") or ""

        self.title_label.setText(title)
        self.artist_label.setText(artist)

        album_year_parts = []
        if album:
            album_year_parts.append(album)
        if year and year > 0:
            album_year_parts.append(str(year))
        if genre:
            album_year_parts.append(genre)
        self.album_year_label.setText(" • ".join(album_year_parts) if album_year_parts else "—")

        # Format badge
        file_type = t.get("file_type") or "AUDIO"
        self.format_badge.setText(file_type)
        self._style_format_badge(file_type)

        # Key badge
        key = t.get("key") or "—"
        self.key_badge.setText(f"Key: {key}")

        # Play count badge
        plays = int(t.get("play_count") or 0)
        self.play_count_badge.setText(f"▶ {plays} {'play' if plays == 1 else 'plays'}")

        # Rating stars
        rating_val = int(t.get("rating") or 0)
        self.rating_label.setText(format_rating_stars(rating_val))

        # KPI cards
        bpm_val = float(t.get("bpm") or 0.0)
        self.bpm_kpi_val.setText(f"{bpm_val:.1f} BPM" if bpm_val > 0 else "—")
        self.duration_kpi_val.setText(t.get("duration_str") or "--:--")

        bitrate = int(t.get("bitrate") or 0)
        sample_rate = int(t.get("sample_rate") or 0)
        bit_depth = int(t.get("bit_depth") or 0)

        spec_parts = []
        if bitrate > 0:
            spec_parts.append(f"{bitrate} kbps")
        if sample_rate > 0:
            spec_parts.append(f"{sample_rate / 1000.0:.1f} kHz")
        if bit_depth > 0:
            spec_parts.append(f"{bit_depth}-bit")
        self.audio_spec_kpi_val.setText(" • ".join(spec_parts) if spec_parts else "—")

        file_size = int(t.get("file_size") or 0)
        self.filesize_kpi_val.setText(format_file_size(file_size))

        date_added = t.get("date_added") or ""
        self.date_added_kpi_val.setText(date_added if date_added else "—")

        # Tags
        self.original_tags = list(t.get("tags") or [])
        self.current_tags = list(self.original_tags)
        self.tags_container.set_tags(self.current_tags)

        # Playlists
        self.original_playlists = list(t.get("playlists") or [])
        self.current_playlists = list(self.original_playlists)
        self.playlists_container.set_playlists(self.current_playlists)
        self._update_staged_changes_ui()

        # Comments
        comments = (t.get("comments") or "").strip()
        if comments:
            self.comment_box.setText(comments)
            self.comment_container.setVisible(True)
        else:
            self.comment_container.setVisible(False)

        # File path
        fp = t.get("file_path") or t.get("folder_path") or "—"
        self.filepath_label.setText(fp)

        # Artwork
        self._load_artwork(t.get("artwork_path"))

        # Position indicator and Prev/Next button states
        if self.tracks_list and len(self.tracks_list) > 0:
            total = len(self.tracks_list)
            pos = self.current_index + 1
            self.pos_indicator.setText(f"Track {pos} of {total}")
            self.prev_btn.setEnabled(self.current_index > 0)
            self.next_btn.setEnabled(self.current_index < total - 1)
        else:
            self.pos_indicator.setText("")
            self.prev_btn.setEnabled(False)
            self.next_btn.setEnabled(False)

        # Replace File Button (MP3 to AIFF only)
        is_mp3 = (
            str(file_type).upper() == "MP3"
            or str(t.get("file_name", "")).lower().endswith(".mp3")
            or str(fp).lower().endswith(".mp3")
        )
        if is_mp3:
            self.replace_btn.setEnabled(True)
            self.replace_btn.setText("🔄 Replace File (MP3 ➔ AIFF)...")
            self.replace_btn.setToolTip("Upgrade this MP3 track to AIFF while preserving plays, date added, cues, and history")
        else:
            self.replace_btn.setEnabled(False)
            self.replace_btn.setText("🔄 Replace File (MP3 only)")
            self.replace_btn.setToolTip(f"Replace File currently only supports MP3 tracks ({file_type} not supported)")

        # History plays table (if preloaded)
        history_plays = t.get("history_plays")
        if history_plays is not None:
            self._populate_history_plays(history_plays)

    def _style_format_badge(self, file_type: str) -> None:
        """Apply color coded pill styles based on audio container."""
        ft = file_type.upper()
        if ft in ("AIFF", "AIF"):
            bg, fg, border = "#0c2b44", "#38bdf8", "#0369a1"
        elif ft == "FLAC":
            bg, fg, border = "#064e3b", "#34d399", "#059669"
        elif ft == "WAV":
            bg, fg, border = "#312e81", "#818cf8", "#4f46e5"
        elif ft in ("AAC", "M4A", "ALAC"):
            bg, fg, border = "#4a044e", "#f472b6", "#be185d"
        elif ft == "MP3":
            bg, fg, border = "#382305", "#f59e0b", "#784b08"
        else:
            bg, fg, border = COLOR_BG_CARD, COLOR_TEXT_SECONDARY, COLOR_BORDER

        self.format_badge.setStyleSheet(
            f"background-color: {bg}; color: {fg}; border: 1px solid {border}; "
            "border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: 700;"
        )

    def _populate_chips(
        self,
        layout: QHBoxLayout,
        items: List[str],
        scheme: str = "purple",
        empty_text: str = "None",
        bg_color: Optional[str] = None,
        border_color: Optional[str] = None,
        text_color: Optional[str] = None,
    ) -> None:
        """Render flowing badge chips inside the target layout."""
        # Clear existing chips
        while layout.count():
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        if not items:
            empty_lbl = QLabel(empty_text)
            empty_lbl.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-style: italic;")
            layout.addWidget(empty_lbl)
            layout.addStretch(1)
            return

        for item in items:
            chip = BadgeChip(item, scheme=scheme)
            layout.addWidget(chip)

        layout.addStretch(1)

    def _load_artwork(self, artwork_path: Optional[str]) -> None:
        """Render rounded artwork thumbnail or fallback vinyl placeholder."""
        size = 110
        if artwork_path and os.path.exists(artwork_path):
            src_pix = QPixmap(artwork_path)
            if not src_pix.isNull():
                scaled = src_pix.scaled(
                    size,
                    size,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                # Clip into smooth rounded rect
                rounded = QPixmap(size, size)
                rounded.fill(Qt.GlobalColor.transparent)
                painter = QPainter(rounded)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                path = QPainterPath()
                path.addRoundedRect(0, 0, size, size, 8, 8)
                painter.setClipPath(path)
                painter.drawPixmap(0, 0, scaled)
                painter.end()

                self.artwork_label.setPixmap(rounded)
                self.artwork_label.setText("")
                return

        # Fallback Vinyl Icon Placeholder
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Outer rounded dark bg
        p.setBrush(QColor("#1e1e28"))
        p.setPen(QColor(COLOR_BORDER_LIGHT))
        p.drawRoundedRect(0, 0, size, size, 8, 8)

        # Vinyl Record Circles
        center_x, center_y = size // 2, size // 2
        p.setBrush(QColor("#121218"))
        p.setPen(QColor("#2d2d3d"))
        p.drawEllipse(center_x - 42, center_y - 42, 84, 84)

        p.setBrush(Qt.GlobalColor.transparent)
        p.setPen(QColor("#222230"))
        p.drawEllipse(center_x - 32, center_y - 32, 64, 64)
        p.drawEllipse(center_x - 22, center_y - 22, 44, 44)

        # Vinyl Center Label
        p.setBrush(QColor(COLOR_BLUE))
        p.setPen(Qt.GlobalColor.transparent)
        p.drawEllipse(center_x - 12, center_y - 12, 24, 24)

        # Center Hole
        p.setBrush(QColor("#0d0d12"))
        p.drawEllipse(center_x - 4, center_y - 4, 8, 8)
        p.end()

        self.artwork_label.setPixmap(pix)
        self.artwork_label.setText("")

    def _terminate_worker(self) -> None:
        """Safely terminate any running detail worker thread."""
        if self.active_worker and self.active_worker.isRunning():
            self.active_worker.quit()
            self.active_worker.wait(500)
            self.active_worker = None

    def _load_full_details_async(self) -> None:
        """Fetch complete metadata and history plays in background thread."""
        if not self.engine:
            return

        content_id = self.track.get("id") or self.track.get("content_id")
        if not content_id:
            return

        self._terminate_worker()

        self.active_worker = TrackDetailWorker(
            engine=self.engine,
            content_id=str(content_id),
            db_path=self.db_path,
            parent=self,
        )
        self.active_worker.success.connect(self._on_details_loaded)
        self.active_worker.error.connect(self._on_details_error)
        self.active_worker.start()

    def _on_details_loaded(
        self,
        full_details: Dict[str, Any],
        history_plays: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Apply enriched metadata from worker to the UI."""
        self.track.update(full_details)
        if history_plays is None:
            history_plays = full_details.get("history_plays") or []
        self.track["history_plays"] = history_plays

        # Refresh artwork if newly resolved
        if full_details.get("artwork_path"):
            self._load_artwork(full_details["artwork_path"])

        # Update Audio Quality KPI
        spec_parts = []
        bitrate = int(self.track.get("bitrate") or 0)
        sample_rate = int(self.track.get("sample_rate") or 0)
        bit_depth = int(self.track.get("bit_depth") or 0)
        if bitrate > 0:
            spec_parts.append(f"{bitrate} kbps")
        if sample_rate > 0:
            spec_parts.append(f"{sample_rate / 1000.0:.1f} kHz")
        if bit_depth > 0:
            spec_parts.append(f"{bit_depth}-bit")
        if spec_parts:
            self.audio_spec_kpi_val.setText(" • ".join(spec_parts))

        # Update File Size KPI
        file_size = int(self.track.get("file_size") or 0)
        if file_size > 0:
            self.filesize_kpi_val.setText(format_file_size(file_size))

        # Update Date Added KPI
        date_added = self.track.get("date_added") or ""
        if date_added:
            self.date_added_kpi_val.setText(date_added)

        # Update History Sets count & Table
        self.history_kpi_val.setText(
            f"{len(history_plays)} {'session' if len(history_plays) == 1 else 'sessions'}"
        )
        self._populate_history_plays(history_plays)

        # Update playlists if not currently modified in staged edits
        if set(self.current_playlists) == set(self.original_playlists):
            db_playlists = full_details.get("playlists")
            if db_playlists is not None:
                self.original_playlists = list(db_playlists)
                self.current_playlists = list(self.original_playlists)
                self.playlists_container.set_playlists(self.current_playlists)

        # Update tags if not currently modified in staged edits
        if set(self.current_tags) == set(self.original_tags):
            db_tags = full_details.get("tags")
            if db_tags is not None:
                self.original_tags = list(db_tags)
                self.current_tags = list(self.original_tags)
                self.tags_container.set_tags(self.current_tags)

        self._update_staged_changes_ui()

    def _on_details_error(self, err_msg: str) -> None:
        self.status_message.emit(f"Error loading track details: {err_msg}")

    def _populate_history_plays(self, history_plays: List[Dict[str, Any]]) -> None:
        """Render the chronological history sets table."""
        count = len(history_plays)
        self.history_title_label.setText(f"🎧 PLAYED IN HISTORY SESSIONS ({count})")

        self.history_table.setRowCount(0)
        if count == 0:
            self.history_table.setVisible(False)
            self.history_empty_card.setVisible(True)
            return

        self.history_table.setVisible(True)
        self.history_empty_card.setVisible(False)

        for row_idx, hp in enumerate(history_plays):
            self.history_table.insertRow(row_idx)

            # Col 0: Track #
            track_no = hp.get("track_no") or (row_idx + 1)
            item_no = QTableWidgetItem(f"#{track_no}")
            item_no.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_no.setForeground(QColor(COLOR_TEXT_MUTED))
            self.history_table.setItem(row_idx, 0, item_no)

            # Col 1: Date
            date_str = hp.get("session_date") or ""
            date_disp = date_str[:10] if len(date_str) >= 10 else date_str
            item_date = QTableWidgetItem(date_disp if date_disp else "—")
            item_date.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_date.setForeground(QColor(COLOR_TEXT_SECONDARY))
            self.history_table.setItem(row_idx, 1, item_date)

            # Col 2: Session Name (parsed up to date/session number)
            raw_name = hp.get("history_name") or "(Untitled Session)"
            session_title = hp.get("session_title")
            if not session_title:
                session_title, _, _ = parse_history_name(raw_name)
            disp_title = session_title if session_title else raw_name
            item_name = QTableWidgetItem(disp_title)
            font = item_name.font()
            font.setBold(True)
            item_name.setFont(font)
            item_name.setForeground(QColor("#ffffff"))
            item_name.setToolTip(raw_name)
            self.history_table.setItem(row_idx, 2, item_name)

            # Col 3: Location
            loc = hp.get("location") or "—"
            item_loc = QTableWidgetItem(f"📍 {loc}" if loc != "—" else "—")
            item_loc.setForeground(QColor(COLOR_BLUE if loc != "—" else COLOR_TEXT_MUTED))
            self.history_table.setItem(row_idx, 3, item_loc)

            # Col 4: Played With
            pw = hp.get("played_with") or "—"
            item_pw = QTableWidgetItem(f"👥 {pw}" if pw != "—" else "—")
            item_pw.setForeground(QColor(COLOR_AMBER if pw != "—" else COLOR_TEXT_MUTED))
            self.history_table.setItem(row_idx, 4, item_pw)

            # Col 5: Played At
            played_time = hp.get("played_time") or ""
            item_time = QTableWidgetItem(played_time if played_time else "—")
            item_time.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_time.setForeground(QColor(COLOR_TEXT_MUTED))
            self.history_table.setItem(row_idx, 5, item_time)

            # Col 6: Action Button
            history_id = hp.get("history_id")
            btn_view = QPushButton("View Session →")
            btn_view.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn_view.setFixedHeight(24)
            btn_view.setFixedWidth(108)
            btn_view.setToolTip("Open full history session and tracklist")
            btn_view.setStyleSheet(
                f"QPushButton {{ "
                f"  background-color: {COLOR_BG_CARD}; "
                f"  border: 1px solid {COLOR_BORDER_LIGHT}; "
                f"  border-radius: 4px; "
                f"  padding: 0 4px; "
                f"  font-size: 11px; "
                f"  font-weight: 600; "
                f"  color: {COLOR_BLUE}; "
                f"}} "
                f"QPushButton:hover {{ "
                f"  background-color: {COLOR_BLUE}; "
                f"  border-color: {COLOR_BLUE}; "
                f"  color: #ffffff; "
                f"}} "
                f"QPushButton:pressed {{ "
                f"  background-color: {COLOR_BLUE_PRESSED}; "
                f"  border-color: {COLOR_BLUE_PRESSED}; "
                f"  color: #ffffff; "
                f"}}"
            )
            btn_view.clicked.connect(lambda _, hid=history_id: self._on_view_session(hid))

            btn_container = QWidget()
            btn_container.setStyleSheet("background: transparent;")
            btn_layout = QHBoxLayout(btn_container)
            btn_layout.setContentsMargins(0, 0, 0, 0)
            btn_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            btn_layout.addWidget(btn_view)

            self.history_table.setCellWidget(row_idx, 6, btn_container)

    def _on_history_row_double_clicked(self, row: int, col: int) -> None:
        """Double clicking any history row jumps directly to that history session."""
        history_plays = self.track.get("history_plays") or []
        if 0 <= row < len(history_plays):
            hid = history_plays[row].get("history_id")
            if hid:
                self._on_view_session(hid)

    def _on_view_session(self, history_id: Optional[str]) -> None:
        """Emit navigation request to open history session."""
        if not history_id:
            return
        self._terminate_worker()
        self.open_history_session_requested.emit(str(history_id))

    def _load_available_playlists(self) -> None:
        """Fetch all user playlists to feed autocomplete dropdown."""
        if not self.engine:
            return
        try:
            pls = fetch_all_playlists(self.engine)
            names = [p["name"] for p in pls if p.get("name")]
            self.cached_all_playlists = names
            self.playlists_container.set_available_playlists(names)
        except Exception:
            pass

    def _load_available_tags(self) -> None:
        """Fetch all user My Tags to feed autocomplete dropdown."""
        if not self.engine:
            return
        try:
            tags = fetch_all_tags(self.engine)
            names = [t["name"] for t in tags if t.get("name")]
            self.cached_all_tags = names
            self.tags_container.set_available_tags(names)
        except Exception:
            pass

    def _on_playlist_added_or_removed(self, name: str) -> None:
        """Update staged playlists state when a chip is added or deleted."""
        self.current_playlists = self.playlists_container.get_playlists()
        self._update_staged_changes_ui()

    def _on_tag_added_or_removed(self, name: str) -> None:
        """Update staged tags state when a chip is added or deleted."""
        self.current_tags = self.tags_container.get_tags()
        self._update_staged_changes_ui()

    def _update_staged_changes_ui(self) -> None:
        """Refresh the bottom bar visibility and message according to staged edits."""
        pl_staged = set(self.current_playlists) != set(self.original_playlists)
        tag_staged = set(self.current_tags) != set(self.original_tags)
        staged = pl_staged or tag_staged
        self.bottom_bar.setVisible(staged)
        if staged:
            sections = []
            if pl_staged:
                added = set(self.current_playlists) - set(self.original_playlists)
                removed = set(self.original_playlists) - set(self.current_playlists)
                parts = []
                if added:
                    parts.append(f"+{len(added)} added")
                if removed:
                    parts.append(f"-{len(removed)} removed")
                sections.append(f"Playlists: {', '.join(parts)}")
            if tag_staged:
                added = set(self.current_tags) - set(self.original_tags)
                removed = set(self.original_tags) - set(self.current_tags)
                parts = []
                if added:
                    parts.append(f"+{len(added)} added")
                if removed:
                    parts.append(f"-{len(removed)} removed")
                sections.append(f"Tags: {', '.join(parts)}")
            summary = "; ".join(sections)
            self.staged_status_label.setText(
                f"⚠️ Unsaved modifications ({summary}) — click Save Changes to commit."
            )

    def _on_discard_changes(self) -> None:
        """Discard staged playlist and tag modifications and revert chips to original state."""
        self.current_playlists = list(self.original_playlists)
        self.playlists_container.set_playlists(self.current_playlists)
        self.current_tags = list(self.original_tags)
        self.tags_container.set_tags(self.current_tags)
        self._update_staged_changes_ui()
        self.status_message.emit("Modifications discarded.")

    def _on_discard_playlist_changes(self) -> None:
        """Backward-compatible alias for discarding changes."""
        self._on_discard_changes()

    def _on_save_changes(self) -> None:
        """Save staged playlist and tag changes to the Rekordbox database."""
        if is_rekordbox_running():
            QMessageBox.warning(
                self,
                "Rekordbox is Running",
                "Please close Rekordbox before saving modifications to the database.",
            )
            return

        content_id = self.track.get("id") or self.track.get("content_id")
        if not content_id or not self.engine:
            return

        added_pl = set(self.current_playlists) - set(self.original_playlists)
        removed_pl = set(self.original_playlists) - set(self.current_playlists)
        added_tags = set(self.current_tags) - set(self.original_tags)
        removed_tags = set(self.original_tags) - set(self.current_tags)

        try:
            for name in added_pl:
                add_track_to_playlist(self.engine, str(content_id), name)
            for name in removed_pl:
                remove_track_from_playlist(self.engine, str(content_id), name)
            for name in added_tags:
                add_track_to_tag(self.engine, str(content_id), name)
            for name in removed_tags:
                remove_track_from_tag(self.engine, str(content_id), name)
        except Exception as e:
            QMessageBox.critical(
                self, "Save Failed", f"Database error while saving modifications:\n{e}"
            )
            return

        self.original_playlists = list(self.current_playlists)
        self.track["playlists"] = list(self.current_playlists)

        self.original_tags = list(self.current_tags)
        self.track["tags"] = list(self.current_tags)

        if self.tracks_list and 0 <= self.current_index < len(self.tracks_list):
            self.tracks_list[self.current_index]["playlists"] = list(self.current_playlists)
            self.tracks_list[self.current_index]["tags"] = list(self.current_tags)

        self._update_staged_changes_ui()
        self.track_changed.emit(self.current_index, self.track)
        self.status_message.emit("✅ Modifications saved successfully.")

    def _on_save_playlist_changes(self) -> None:
        """Backward-compatible alias for saving changes."""
        self._on_save_changes()

    def _confirm_unsaved_changes(self) -> bool:
        """Prompt user if there are unsaved playlist or tag changes before navigating away.

        Returns True if safe to navigate, False if action was cancelled.
        """
        pl_dirty = set(self.current_playlists) != set(self.original_playlists)
        tag_dirty = set(self.current_tags) != set(self.original_tags)
        if pl_dirty or tag_dirty:
            reply = QMessageBox.question(
                self,
                "Unsaved Modifications",
                "You have unsaved playlist or tag modifications. Do you want to save them before leaving?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
            )
            if reply == QMessageBox.StandardButton.Save:
                self._on_save_changes()
                saved = (
                    set(self.current_playlists) == set(self.original_playlists)
                    and set(self.current_tags) == set(self.original_tags)
                )
                return saved
            elif reply == QMessageBox.StandardButton.Discard:
                self._on_discard_changes()
                return True
            else:
                return False
        return True

    def _on_back_clicked(self) -> None:
        """Handle back button click."""
        if not self._confirm_unsaved_changes():
            return
        self._terminate_worker()
        self.back_clicked.emit()

    def _on_prev_track(self) -> None:
        """Switch view to display previous track in collection."""
        if self.tracks_list and self.current_index > 0:
            if not self._confirm_unsaved_changes():
                return
            self._terminate_worker()
            self.current_index -= 1
            self.track = dict(self.tracks_list[self.current_index])
            self._populate_track_data(self.track)
            self._load_full_details_async()
            self.track_changed.emit(self.current_index, self.track)

    def _on_next_track(self) -> None:
        """Switch view to display next track in collection."""
        if self.tracks_list and self.current_index < len(self.tracks_list) - 1:
            if not self._confirm_unsaved_changes():
                return
            self._terminate_worker()
            self.current_index += 1
            self.track = dict(self.tracks_list[self.current_index])
            self._populate_track_data(self.track)
            self._load_full_details_async()
            self.track_changed.emit(self.current_index, self.track)

    def _on_open_folder(self) -> None:
        """Open containing folder in system file manager."""
        fp = self.track.get("file_path") or self.track.get("folder_path") or ""
        folder = self.track.get("folder_dir") or (os.path.dirname(fp) if fp else "")
        if not folder or folder == "—":
            msg = "⚠️ No folder path available."
            self.status_message.emit(msg)
            return

        success = open_in_folder(folder)
        if success:
            msg = f"📂 Opened folder: {os.path.basename(folder) or folder}"
        else:
            msg = f"❌ Folder not found on disk: {folder}"
        self.status_message.emit(msg)

    def _on_reveal_file(self) -> None:
        """Reveal and select file in system file manager."""
        file_path = self.track.get("file_path") or self.track.get("folder_path") or ""
        if not file_path or file_path == "—":
            msg = "⚠️ No file path available."
            self.status_message.emit(msg)
            return

        success = reveal_in_file_manager(file_path)
        if success:
            msg = f"🔍 Revealed in Finder: {os.path.basename(file_path)}"
        else:
            folder = os.path.dirname(file_path) if os.path.splitext(file_path)[1] else file_path
            if folder and open_in_folder(folder):
                msg = f"⚠️ File missing on disk, opened folder: {os.path.basename(folder)}"
            else:
                msg = f"❌ File not found on disk: {file_path}"
        self.status_message.emit(msg)

    def _on_copy_path(self) -> None:
        """Copy full file path to system clipboard."""
        file_path = self.track.get("file_path") or self.track.get("folder_path") or ""
        if file_path and file_path != "—":
            cb = QApplication.clipboard()
            if cb:
                cb.setText(file_path)
                self.status_message.emit("📋 Copied file path to clipboard!")

    def _on_copy_track(self) -> None:
        """Copy formatted 'Artist - Title' to system clipboard."""
        artist = self.track.get("artist") or "Unknown Artist"
        title = self.track.get("title") or "(Untitled)"
        cb = QApplication.clipboard()
        if cb:
            cb.setText(f"{artist} - {title}")
            self.status_message.emit(f"📋 Copied '{artist} - {title}' to clipboard!")

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Handle keyboard navigation: Left/Right arrow keys for Prev/Next, Esc to go back."""
        if hasattr(self, "playlists_container") and self.playlists_container.is_input_active():
            if event.key() == Qt.Key.Key_Escape:
                self.playlists_container.hide_add_input()
                event.accept()
                return
        if hasattr(self, "tags_container") and self.tags_container.is_input_active():
            if event.key() == Qt.Key.Key_Escape:
                self.tags_container.hide_add_input()
                event.accept()
                return
        if event.key() == Qt.Key.Key_Escape:
            self._on_back_clicked()
            event.accept()
            return
        elif event.key() == Qt.Key.Key_Left:
            if self.prev_btn.isEnabled():
                self._on_prev_track()
                event.accept()
                return
        elif event.key() == Qt.Key.Key_Right:
            if self.next_btn.isEnabled():
                self._on_next_track()
                event.accept()
                return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        """Ensure background worker is terminated when view is closed."""
        self._terminate_worker()
        super().closeEvent(event)

    def hideEvent(self, event) -> None:
        """Ensure background worker is cleanly terminated when view is hidden."""
        self._terminate_worker()
        super().hideEvent(event)


class TrackDetailDialog(QDialog):
    """Compatibility dialog wrapper hosting TrackDetailView inside a modal dialog if needed."""

    open_history_session_requested = Signal(str)
    status_message = Signal(str)
    track_changed = Signal(int, dict)

    def __init__(
        self,
        engine: Engine,
        track_data: Dict[str, Any],
        db_path: Optional[str] = None,
        tracks_list: Optional[List[Dict[str, Any]]] = None,
        current_index: int = 0,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        title = track_data.get("title") or "Track Details"
        artist = track_data.get("artist") or ""
        window_title = f"{artist} - {title}" if artist and artist != "Unknown Artist" else title
        self.setWindowTitle(f"Track Details — {window_title}")
        self.resize(920, 720)
        self.setMinimumSize(780, 540)
        self.setStyleSheet(
            f"QDialog {{ background-color: {COLOR_BG_DARK}; color: {COLOR_TEXT_PRIMARY}; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.detail_view = TrackDetailView(
            parent=self,
            engine=engine,
            track_data=track_data,
            db_path=db_path,
            tracks_list=tracks_list,
            current_index=current_index,
            back_button_text="✕ Close",
        )
        self.detail_view.back_clicked.connect(self.reject)
        self.detail_view.open_history_session_requested.connect(self._on_open_history_session)
        self.detail_view.status_message.connect(self.status_message.emit)
        self.detail_view.track_changed.connect(self.track_changed.emit)
        layout.addWidget(self.detail_view)

    def __getattr__(self, name: str):
        if "detail_view" in self.__dict__ and hasattr(self.detail_view, name):
            return getattr(self.detail_view, name)
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    def _on_open_history_session(self, history_id: str) -> None:
        self.open_history_session_requested.emit(history_id)
        self.accept()

    def closeEvent(self, event) -> None:
        if hasattr(self, "detail_view"):
            self.detail_view._terminate_worker()
        super().closeEvent(event)

    def reject(self) -> None:
        if hasattr(self, "detail_view"):
            self.detail_view._terminate_worker()
        super().reject()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            event.accept()
            return
        super().keyPressEvent(event)
