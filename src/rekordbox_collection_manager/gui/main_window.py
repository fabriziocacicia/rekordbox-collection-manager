"""Main Application Window for Rekordbox Collection Manager."""

import logging
import os
import shutil
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional
from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QSize, QTimer, Qt
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.engine import Engine

from rekordbox_collection_manager import __version__
from rekordbox_collection_manager.backup_restore import (
    create_database_backup,
    get_default_backup_dir,
)
from rekordbox_collection_manager.gui.assets import (
    get_app_icon,
    get_asset_path,
    get_database_pixmap,
    get_refresh_icon,
)
from rekordbox_collection_manager.gui.history_view import HistoryView
from rekordbox_collection_manager.gui.play_count_view import PlayCountView
from rekordbox_collection_manager.gui.playlists_view import PlaylistsView
from rekordbox_collection_manager.gui.replace_file_view import ReplaceFileView
from rekordbox_collection_manager.gui.restore_dialog import RestoreDialog
from rekordbox_collection_manager.gui.tags_view import TagsView
from rekordbox_collection_manager.gui.tracks_view import TracksView
from rekordbox_collection_manager.gui.theme import (
    COLOR_AMBER,
    COLOR_BLUE,
    COLOR_BORDER,
    COLOR_RED,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_SECONDARY,
)
from rekordbox_collection_manager.gui.workers import (
    BackupWorker,
    DatabaseLoaderWorker,
    SyncWorker,
)
from rekordbox_collection_manager.turso_sync import (
    SyncResult,
    is_turso_configured,
    run_sync_cycle,
)
from rekordbox_collection_manager.utils import (
    get_default_master_db_path,
    is_rekordbox_running,
    open_in_folder,
    reveal_in_file_manager,
)

logger = logging.getLogger(__name__)


class CollapsibleSidebarFrame(QFrame):
    """Custom sidebar QFrame supporting smooth width property animation."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("sidebarFrame")
        self.setFixedWidth(200)

    def _get_sidebar_width(self) -> int:
        return self.width()

    def _set_sidebar_width(self, width: int) -> None:
        self.setFixedWidth(width)

    sidebarWidth = Property(int, _get_sidebar_width, _set_sidebar_width)


class InfoView(QWidget):
    """View displaying database metadata, backup options, and system status."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("Database & Application Information")
        title.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        self.info_text = QTextEdit()
        self.info_text.setReadOnly(True)
        self.info_text.setStyleSheet(
            f"background-color: #1a1a20; border: 1px solid {COLOR_BORDER}; "
            f'border-radius: 6px; padding: 12px; font-family: "Menlo", "Consolas", "Courier New"; font-size: 12px;'
        )
        layout.addWidget(self.info_text, stretch=1)

    def set_info(self, info_dict: Dict[str, Any]) -> None:
        lines = []
        for k, v in info_dict.items():
            lines.append(f"{k:<25}: {v}")
        self.info_text.setPlainText("\n".join(lines))


class MainWindow(QMainWindow):
    """Main application window container."""

    def __init__(self, initial_db_path: Optional[str] = None):
        super().__init__()
        self.setWindowTitle("Rekordbox Collection Manager")
        self.resize(1180, 780)
        self.setMinimumSize(960, 600)

        # Set Window Icon
        app_icon = get_app_icon()
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)

        self.db_path: Optional[str] = initial_db_path
        self.engine: Optional[Engine] = None
        self.table_format: str = "djmdHistory"
        self.history_records: List[Dict[str, Any]] = []
        self.total_tracks: int = 0
        self.is_sidebar_collapsed: bool = False
        self._db_loader: Optional[DatabaseLoaderWorker] = None
        self._backup_worker: Optional[BackupWorker] = None
        self._sync_worker: Optional[SyncWorker] = None
        self._sync_pending: bool = False
        self._initial_sync_done: bool = False

        self.nav_items = [
            {
                "id": 0,
                "icon": "🕒",
                "title": "History Sessions",
            },
            {
                "id": 1,
                "icon": "🎵",
                "title": "Tracks",
            },
            {
                "id": 2,
                "icon": "📁",
                "title": "Playlists",
            },
            {
                "id": 3,
                "icon": "🏷️",
                "title": "Tags",
            },
            {
                "id": 4,
                "icon": "🔢",
                "title": "Play Counts",
            },
            {
                "id": 5,
                "icon": "🔄",
                "title": "Replace File",
            },
            {
                "id": 6,
                "icon": "ℹ️",
                "title": "Collection Info",
            },
        ]

        # Safety monitor timer (checks every 3 seconds)
        self.safety_timer = QTimer(self)
        self.safety_timer.setInterval(3000)
        self.safety_timer.timeout.connect(self._check_rekordbox_status)

        self._init_ui()
        self.safety_timer.start()
        self._check_rekordbox_status()

        # Load database
        self.load_database(self.db_path)

    def _init_ui(self) -> None:
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)

        root_layout = QVBoxLayout(central_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # 1. Header Frame (Logo, Safety Guard, DB Info, Backup)
        header_frame = QFrame()
        header_frame.setObjectName("headerFrame")
        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(16, 12, 16, 12)
        header_layout.setSpacing(14)

        # Brand Logo & Title
        icon_path = get_asset_path("icon.png")
        if icon_path and os.path.exists(icon_path):
            logo_label = QLabel()
            pix = QPixmap(icon_path).scaled(
                28,
                28,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            logo_label.setPixmap(pix)
            header_layout.addWidget(logo_label)

        brand_label = QLabel("Rekordbox Collection Manager")
        brand_label.setStyleSheet("font-size: 15px; font-weight: 700; color: #ffffff; letter-spacing: -0.2px;")
        header_layout.addWidget(brand_label)

        # Safety Guard Chip
        self.safety_chip = QLabel("Checking Rekordbox...")
        self.safety_chip.setObjectName("safeBadge")
        header_layout.addWidget(self.safety_chip)

        self.refresh_chip_btn = QPushButton()
        self.refresh_chip_btn.setObjectName("refreshChipBtn")
        self.refresh_chip_btn.setToolTip("Re-check Rekordbox running status")
        self.refresh_chip_btn.setIcon(get_refresh_icon())
        self.refresh_chip_btn.setIconSize(QSize(15, 15))
        self.refresh_chip_btn.setFixedSize(28, 28)
        self.refresh_chip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_chip_btn.clicked.connect(self._check_rekordbox_status)
        header_layout.addWidget(self.refresh_chip_btn)

        header_layout.addStretch(1)

        # Active Database Chip (read-only status)
        self.db_badge = QFrame()
        self.db_badge.setObjectName("dbBadge")
        self.db_badge.setFixedHeight(28)
        self.db_badge.setToolTip("Active Rekordbox Database")

        db_badge_layout = QHBoxLayout(self.db_badge)
        db_badge_layout.setContentsMargins(9, 0, 11, 0)
        db_badge_layout.setSpacing(7)
        db_badge_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self.db_icon_label = QLabel()
        self.db_icon_label.setFixedSize(14, 14)
        self.db_icon_label.setPixmap(get_database_pixmap(COLOR_AMBER, size=14))
        db_badge_layout.addWidget(self.db_icon_label)

        self.db_label = QLabel("Database: Resolving...")
        self.db_label.setTextFormat(Qt.TextFormat.RichText)
        self.db_label.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-size: 11px;")
        db_badge_layout.addWidget(self.db_label)

        header_layout.addWidget(self.db_badge)

        # Quick Backup Button
        self.backup_btn = QPushButton("📦 Quick Backup")
        self.backup_btn.setToolTip("Create instant timestamped backup in ~/Music/rekordbox/Backups")
        self.backup_btn.clicked.connect(self._on_quick_backup)
        header_layout.addWidget(self.backup_btn)

        root_layout.addWidget(header_frame)

        # 2. Main Body Split (Sidebar on left, Stacked screens on right)
        body_widget = QWidget()
        body_layout = QHBoxLayout(body_widget)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        # Sidebar Frame
        self.sidebar_frame = CollapsibleSidebarFrame()
        sidebar_layout = QVBoxLayout(self.sidebar_frame)
        sidebar_layout.setContentsMargins(0, 8, 0, 8)
        sidebar_layout.setSpacing(4)

        # Sidebar Header Row (Section label + collapse arrow)
        self.sidebar_header = QWidget()
        self.sidebar_header_layout = QHBoxLayout(self.sidebar_header)
        self.sidebar_header_layout.setContentsMargins(14, 2, 8, 6)
        self.sidebar_header_layout.setSpacing(4)

        self.sidebar_section_label = QLabel("NAVIGATION")
        self.sidebar_section_label.setStyleSheet(
            f"color: {COLOR_TEXT_MUTED}; font-size: 10px; font-weight: 700; letter-spacing: 0.8px;"
        )
        self.sidebar_header_layout.addWidget(self.sidebar_section_label, stretch=1)

        self.btn_collapse_sidebar = QPushButton("◀")
        self.btn_collapse_sidebar.setObjectName("sidebarCollapseBtn")
        self.btn_collapse_sidebar.setToolTip("Collapse sidebar (Cmd+B / Ctrl+B)")
        self.btn_collapse_sidebar.setFixedSize(26, 26)
        self.btn_collapse_sidebar.clicked.connect(self.toggle_sidebar)
        self.sidebar_header_layout.addWidget(self.btn_collapse_sidebar)

        sidebar_layout.addWidget(self.sidebar_header)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)

        self.nav_buttons: List[QPushButton] = []
        for item in self.nav_items:
            btn = QPushButton(f"{item['icon']}  {item['title']}")
            btn.setObjectName("navButton")
            btn.setCheckable(True)
            btn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            btn.setToolTip(item["title"])
            if item["id"] == 0:
                btn.setChecked(True)
            self.nav_group.addButton(btn, item["id"])
            sidebar_layout.addWidget(btn)
            self.nav_buttons.append(btn)

        self.btn_history = self.nav_buttons[0]
        self.btn_tracks = self.nav_buttons[1]
        self.btn_playlists = self.nav_buttons[2]
        self.btn_tags = self.nav_buttons[3]
        self.btn_play_count = self.nav_buttons[4]
        self.btn_replace_file = self.nav_buttons[5]
        self.btn_info = self.nav_buttons[6]

        sidebar_layout.addStretch(1)

        self.app_ver = QLabel(f"v{__version__}")
        self.app_ver.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px; padding: 12px;")
        sidebar_layout.addWidget(self.app_ver)

        self.nav_group.idClicked.connect(self._on_nav_clicked)
        body_layout.addWidget(self.sidebar_frame)

        # Global Shortcut for toggling sidebar
        self.sidebar_shortcut = QShortcut(QKeySequence("Ctrl+B"), self)
        self.sidebar_shortcut.activated.connect(self.toggle_sidebar)

        # Smooth sidebar width animation (drives both min and max width simultaneously)
        self.sidebar_anim = QPropertyAnimation(self.sidebar_frame, b"sidebarWidth", self)
        self.sidebar_anim.setDuration(150)
        self.sidebar_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self.sidebar_anim.finished.connect(self._on_sidebar_anim_finished)

        # Stacked Pages
        self.stacked_widget = QStackedWidget()

        self.history_view = HistoryView(self)
        self.history_view.notify_status.connect(self._show_status_message)
        self.history_view.request_reload.connect(lambda: self.load_database(self.db_path))
        self.history_view.open_playlist_requested.connect(self.open_playlist)
        self.history_view.open_tag_requested.connect(self.open_tag)
        self.stacked_widget.addWidget(self.history_view)

        self.tracks_view = TracksView(self)
        self.tracks_view.notify_status.connect(self._show_status_message)
        self.tracks_view.open_history_session_requested.connect(self.open_history_session)
        self.tracks_view.open_playlist_requested.connect(self.open_playlist)
        self.tracks_view.open_tag_requested.connect(self.open_tag)
        self.stacked_widget.addWidget(self.tracks_view)

        self.playlists_view = PlaylistsView(self)
        self.playlists_view.notify_status.connect(self._show_status_message)
        self.playlists_view.open_history_session_requested.connect(self.open_history_session)
        self.playlists_view.open_playlist_requested.connect(self.open_playlist)
        self.playlists_view.open_tag_requested.connect(self.open_tag)
        self.playlists_view.replace_file_requested.connect(self.open_replace_file_for_track)
        self.stacked_widget.addWidget(self.playlists_view)

        self.tags_view = TagsView(self)
        self.tags_view.notify_status.connect(self._show_status_message)
        self.tags_view.open_history_session_requested.connect(self.open_history_session)
        self.tags_view.open_playlist_requested.connect(self.open_playlist)
        self.tags_view.open_tag_requested.connect(self.open_tag)
        self.tags_view.replace_file_requested.connect(self.open_replace_file_for_track)
        self.stacked_widget.addWidget(self.tags_view)

        self.play_count_view = PlayCountView(self)
        self.play_count_view.notify_status.connect(self._show_status_message)
        self.play_count_view.open_history_session_requested.connect(self.open_history_session)
        self.play_count_view.open_playlist_requested.connect(self.open_playlist)
        self.play_count_view.open_tag_requested.connect(self.open_tag)
        self.stacked_widget.addWidget(self.play_count_view)

        self.replace_file_view = ReplaceFileView(self)
        self.replace_file_view.notify_status.connect(self._show_status_message)
        self.replace_file_view.open_history_session_requested.connect(self.open_history_session)
        self.stacked_widget.addWidget(self.replace_file_view)

        self.tracks_view.replace_file_requested.connect(self.open_replace_file_for_track)
        self.history_view.replace_file_requested.connect(self.open_replace_file_for_track)
        self.play_count_view.replace_file_requested.connect(self.open_replace_file_for_track)

        # Database save signals -> trigger cloud sync
        self.history_view.db_saved.connect(self._on_db_saved)
        self.tracks_view.db_saved.connect(self._on_db_saved)
        self.playlists_view.db_saved.connect(self._on_db_saved)
        self.tags_view.db_saved.connect(self._on_db_saved)
        self.play_count_view.db_saved.connect(self._on_db_saved)
        self.replace_file_view.db_saved.connect(self._on_db_saved)

        self.info_view = InfoView(self)
        self.stacked_widget.addWidget(self.info_view)

        body_layout.addWidget(self.stacked_widget, stretch=1)
        root_layout.addWidget(body_widget, stretch=1)

        # 3. Status Bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Initializing Rekordbox Collection Manager...")

        # 4. Native Application Menu Bar
        self._setup_menu_bar()

    def _on_nav_clicked(self, page_id: int) -> None:
        curr = self.stacked_widget.currentIndex()
        if page_id == curr:
            if page_id == 0 and hasattr(self.history_view, "reset_to_list"):
                self.history_view.reset_to_list()
            elif page_id == 1 and hasattr(self.tracks_view, "reset_to_list"):
                self.tracks_view.reset_to_list()
            elif page_id == 2 and hasattr(self.playlists_view, "reset_to_list"):
                self.playlists_view.reset_to_list()
            elif page_id == 3 and hasattr(self.tags_view, "reset_to_list"):
                self.tags_view.reset_to_list()
            elif page_id == 4 and hasattr(self.play_count_view, "reset_to_list"):
                self.play_count_view.reset_to_list()
        self.stacked_widget.setCurrentIndex(page_id)

    def open_history_session(self, history_id: str) -> None:
        """Switch navigation to History tab and open the requested history session."""
        if self.nav_buttons and len(self.nav_buttons) > 0:
            self.nav_buttons[0].setChecked(True)
        self.stacked_widget.setCurrentIndex(0)
        self.history_view.open_session_by_id(history_id)

    def open_playlist(self, playlist_name_or_id: str) -> None:
        """Switch navigation to Playlists tab and select the requested playlist."""
        if self.nav_buttons and len(self.nav_buttons) > 2:
            self.nav_buttons[2].setChecked(True)
        self.stacked_widget.setCurrentIndex(2)
        self.playlists_view.open_playlist_by_id_or_name(playlist_name_or_id)

    def open_tag(self, tag_name_or_id: str) -> None:
        """Switch navigation to Tags tab and select the requested tag."""
        if self.nav_buttons and len(self.nav_buttons) > 3:
            self.nav_buttons[3].setChecked(True)
        self.stacked_widget.setCurrentIndex(3)
        self.tags_view.open_tag_by_id_or_name(tag_name_or_id)

    def open_replace_file_for_track(self, track_data: Dict[str, Any]) -> None:
        """Switch navigation to Replace File tab and preselect track."""
        if self.nav_buttons and len(self.nav_buttons) > 5:
            self.nav_buttons[5].setChecked(True)
        self.stacked_widget.setCurrentIndex(5)
        self.replace_file_view.preselect_track(track_data)

    def toggle_sidebar(self) -> None:
        """Toggle sidebar between expanded and collapsed icon-only mode."""
        self.set_sidebar_collapsed(not self.is_sidebar_collapsed)

    def set_sidebar_collapsed(self, collapsed: bool, animate: bool = True) -> None:
        """Set whether sidebar is collapsed to icon-only mode with smooth animation."""
        self.is_sidebar_collapsed = collapsed
        target_width = 60 if collapsed else 200
        start_width = self.sidebar_frame.width()

        if animate and self.isVisible():
            self.sidebar_anim.stop()

            if collapsed:
                # Prepare UI for closing: hide labels and switch to icons immediately
                # so the width can shrink with zero text layout reflow or clipping.
                self._apply_collapsed_ui()
            else:
                # Prepare UI for opening: update buttons, expand with icons first
                self.btn_collapse_sidebar.setText("◀")
                self.btn_collapse_sidebar.setToolTip("Collapse sidebar (Cmd+B / Ctrl+B)")

            self.sidebar_anim.setStartValue(start_width)
            self.sidebar_anim.setEndValue(target_width)
            self.sidebar_anim.start()
        else:
            self.sidebar_frame.setFixedWidth(target_width)
            if collapsed:
                self._apply_collapsed_ui()
            else:
                self._apply_expanded_ui()

    def _on_sidebar_anim_finished(self) -> None:
        """Finalize UI once animation completes."""
        target_width = 60 if self.is_sidebar_collapsed else 200
        self.sidebar_frame.setFixedWidth(target_width)

        if self.is_sidebar_collapsed:
            self._apply_collapsed_ui()
        else:
            self._apply_expanded_ui()

    def _apply_collapsed_ui(self) -> None:
        """Switch navigation to compact icon-only mode."""
        self.btn_collapse_sidebar.setText("▶")
        self.btn_collapse_sidebar.setToolTip("Expand sidebar (Cmd+B / Ctrl+B)")
        self.sidebar_section_label.setVisible(False)
        self.sidebar_header_layout.setContentsMargins(0, 2, 0, 4)
        self.sidebar_header_layout.setAlignment(self.btn_collapse_sidebar, Qt.AlignmentFlag.AlignCenter)
        self.app_ver.setVisible(False)

        for btn, item in zip(self.nav_buttons, self.nav_items):
            btn.setText(item["icon"])
            btn.setToolTip(item["title"])
            btn.setStyleSheet("text-align: center; padding: 11px 0px; font-size: 15px;")

    def _apply_expanded_ui(self) -> None:
        """Switch navigation to full expanded mode."""
        self.btn_collapse_sidebar.setText("◀")
        self.btn_collapse_sidebar.setToolTip("Collapse sidebar (Cmd+B / Ctrl+B)")
        self.sidebar_section_label.setVisible(True)
        self.sidebar_header_layout.setContentsMargins(14, 2, 8, 6)
        self.sidebar_header_layout.setAlignment(self.btn_collapse_sidebar, Qt.AlignmentFlag.AlignRight)
        self.app_ver.setVisible(True)

        for btn, item in zip(self.nav_buttons, self.nav_items):
            btn.setText(f"{item['icon']}  {item['title']}")
            btn.setToolTip(item["title"])
            btn.setStyleSheet("")

    def _check_rekordbox_status(self) -> None:
        """Periodic safety check to verify if Rekordbox application is running."""
        is_running = is_rekordbox_running()
        if is_running:
            self.safety_chip.setText("🔴 Rekordbox Running (Database Locked)")
            self.safety_chip.setObjectName("dangerBadge")
            self.safety_chip.setToolTip(
                "Rekordbox is open. Write actions are locked to prevent database corruption. "
                "Close Rekordbox (Cmd + Q) to safely commit changes."
            )
        else:
            self.safety_chip.setText("🟢 Rekordbox Closed (Safe)")
            self.safety_chip.setObjectName("safeBadge")
            self.safety_chip.setToolTip("Rekordbox is closed. Database modifications are safe to perform.")
        self.safety_chip.style().unpolish(self.safety_chip)
        self.safety_chip.style().polish(self.safety_chip)

    def load_database(self, db_path: Optional[str] = None) -> None:
        """Start background loader worker to open database."""
        self.status_bar.showMessage("Opening Rekordbox database...")
        self.db_icon_label.setPixmap(get_database_pixmap(COLOR_AMBER, size=14))
        self.db_label.setText(
            f'<span style="color: {COLOR_AMBER}; font-size: 11px; font-weight: 500;">Connecting...</span>'
        )
        self.db_badge.setToolTip("Connecting to Rekordbox database...")
        self.db_label.setToolTip("Connecting to Rekordbox database...")

        if self._db_loader and self._db_loader.isRunning():
            self._db_loader.quit()
            self._db_loader.wait(500)

        loader = DatabaseLoaderWorker(db_path, parent=self)
        self._db_loader = loader
        loader.success.connect(self._on_db_loaded)
        loader.error.connect(self._on_db_error)
        loader.finished.connect(lambda: self._on_loader_finished(loader))
        loader.start()

    def _on_loader_finished(self, worker: DatabaseLoaderWorker) -> None:
        if self._db_loader is worker:
            self._db_loader = None

    def _on_db_loaded(
        self,
        engine: Engine,
        path: str,
        history_records: List[Dict[str, Any]],
        total_tracks: int,
        table_format: str,
    ) -> None:
        self.engine = engine
        self.db_path = path
        self.history_records = history_records
        self.total_tracks = total_tracks
        self.table_format = table_format

        # Shorten path for display in header badge
        db_filename = os.path.basename(path)
        parent_dir = os.path.basename(os.path.dirname(path))
        if parent_dir:
            display_html = (
                f'<span style="color: {COLOR_TEXT_MUTED}; font-size: 10px; font-weight: 700; letter-spacing: 0.5px;">DB</span>&nbsp;&nbsp;'
                f'<span style="color: {COLOR_TEXT_SECONDARY}; font-size: 11px;">{parent_dir}/</span>'
                f'<span style="color: {COLOR_TEXT_PRIMARY}; font-weight: 600; font-size: 11px;">{db_filename}</span>'
            )
        else:
            display_html = (
                f'<span style="color: {COLOR_TEXT_MUTED}; font-size: 10px; font-weight: 700; letter-spacing: 0.5px;">DB</span>&nbsp;&nbsp;'
                f'<span style="color: {COLOR_TEXT_PRIMARY}; font-weight: 600; font-size: 11px;">{db_filename}</span>'
            )

        self.db_icon_label.setPixmap(get_database_pixmap(COLOR_BLUE, size=14))
        self.db_label.setText(display_html)
        tip_text = f"Active Database:\n{path}"
        self.db_badge.setToolTip(tip_text)
        self.db_label.setToolTip(tip_text)

        # Update window title with active database
        self.setWindowTitle(f"Rekordbox Collection Manager — {db_filename}")
        if sys.platform == "darwin":
            try:
                self.setWindowFilePath(path)
            except Exception:
                pass

        # Populate Views
        self.history_view.set_data(engine, history_records, db_path=path)
        self.tracks_view.set_engine(engine, total_tracks=total_tracks, db_path=path)
        self.playlists_view.set_engine(engine, db_path=path)
        self.tags_view.set_engine(engine, db_path=path)
        self.play_count_view.set_engine(engine, db_path=path)
        self.replace_file_view.set_engine(engine, db_path=path)

        active_hist = sum(1 for r in history_records if not r.get("is_deleted"))
        del_hist = sum(1 for r in history_records if r.get("is_deleted"))
        del_hist_str = f" ({del_hist} deleted)" if del_hist > 0 else ""

        self.info_view.set_info({
            "Database File": path,
            "History Table Type": table_format,
            "History Sessions Loaded": f"{active_hist}{del_hist_str}",
            "Total Collection Tracks": total_tracks,
            "Database File Size": f"{os.path.getsize(path) / (1024 * 1024):.2f} MB" if os.path.exists(path) else "N/A",
            "Last Loaded At": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

        self.status_bar.showMessage(
            f"Database loaded: {active_hist} history sessions{del_hist_str}, {total_tracks} tracks.",
            5000,
        )

        # Trigger cloud sync on opening our custom manager
        if not self._initial_sync_done:
            self._initial_sync_done = True
            self.trigger_sync(background=True, create_backup=True)

    def _on_db_error(self, err_msg: str) -> None:
        self.setWindowTitle("Rekordbox Collection Manager")
        self.status_bar.showMessage(f"❌ Database load error: {err_msg}")
        self.db_icon_label.setPixmap(get_database_pixmap(COLOR_RED, size=14))
        self.db_label.setText(
            f'<span style="color: {COLOR_RED}; font-size: 11px; font-weight: 500;">DB Error</span>'
        )
        tip_error = f"Database Error:\n{err_msg}"
        self.db_badge.setToolTip(tip_error)
        self.db_label.setToolTip(tip_error)
        QMessageBox.critical(
            self,
            "Database Connection Error",
            f"Could not open Rekordbox database:\n\n{err_msg}\n\n"
            "Please check that Rekordbox is closed and the path is valid.",
        )

    def _on_browse_db(self) -> None:
        """Prompt user to choose a database file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Rekordbox Database",
            os.path.expanduser("~"),
            "Rekordbox Database (*.db *.edb);;All Files (*)",
        )
        if file_path:
            self.load_database(file_path)

    def _setup_menu_bar(self) -> None:
        """Set up native application menu bar (Database & View menus)."""
        menu_bar = self.menuBar()

        # --- 1. Database Menu ---
        db_menu = menu_bar.addMenu("&Database")

        act_switch_db = QAction("📁 Switch Database...", self)
        act_switch_db.setShortcut(QKeySequence.StandardKey.Open)
        act_switch_db.triggered.connect(self._on_browse_db)
        db_menu.addAction(act_switch_db)

        act_reload_db = QAction("🔄 Reload Database", self)
        act_reload_db.setShortcut(QKeySequence.StandardKey.Refresh)
        act_reload_db.triggered.connect(lambda: self.load_database(self.db_path))
        db_menu.addAction(act_reload_db)

        db_menu.addSeparator()

        act_backup = QAction("📦 Create Backup...", self)
        act_backup.setShortcut(QKeySequence("Ctrl+Shift+B"))
        act_backup.triggered.connect(self._on_quick_backup)
        db_menu.addAction(act_backup)

        act_restore = QAction("📥 Restore from Backup...", self)
        act_restore.triggered.connect(self._on_open_restore_dialog)
        db_menu.addAction(act_restore)

        db_menu.addSeparator()

        act_sync = QAction("☁️ Sync with Turso Cloud", self)
        act_sync.setShortcut(QKeySequence("Ctrl+Shift+S"))
        act_sync.triggered.connect(lambda: self.trigger_sync(background=True, create_backup=True))
        db_menu.addAction(act_sync)

        db_menu.addSeparator()

        act_reveal_db = QAction("📂 Reveal Database in Finder", self)
        act_reveal_db.triggered.connect(self._on_reveal_db_in_finder)
        db_menu.addAction(act_reveal_db)

        act_reveal_backups = QAction("🗂️ Reveal Backups in Finder", self)
        act_reveal_backups.triggered.connect(self._on_reveal_backups_in_finder)
        db_menu.addAction(act_reveal_backups)

    def _switch_to_tab(self, index: int) -> None:
        """Switch navigation to tab by index."""
        if 0 <= index < len(self.nav_buttons):
            self.nav_buttons[index].setChecked(True)
            self._on_nav_clicked(index)

    def _on_quick_backup(self) -> None:
        """Create a quick timestamped backup in ~/Music/rekordbox/Backups asynchronously."""
        if not self.db_path or not os.path.exists(self.db_path):
            QMessageBox.warning(self, "Backup Warning", "No valid database is currently open to back up.")
            return

        if self._backup_worker and self._backup_worker.isRunning():
            return

        self.backup_btn.setEnabled(False)
        self.backup_btn.setText("⏳ Backing up...")
        self.status_bar.showMessage("⏳ Creating backup snapshot in ~/Music/rekordbox/Backups...", 0)

        self._backup_worker = BackupWorker(db_path=self.db_path, parent=self)
        self._backup_worker.success.connect(self._on_backup_success)
        self._backup_worker.error.connect(self._on_backup_error)
        self._backup_worker.start()

    def _on_backup_success(self, res: Dict[str, Any]) -> None:
        self.backup_btn.setEnabled(True)
        self.backup_btn.setText("📦 Quick Backup")
        self.status_bar.showMessage(f"✅ Backup created in: {res['backup_folder']}", 7000)
        ans = QMessageBox.information(
            self,
            "Backup Successful",
            f"Collection snapshot successfully created!\n\n"
            f"Type: {res['type']}\n"
            f"Folder: {res['backup_folder']}\n"
            f"Size: {res['db_size_formatted']}",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Open,
            QMessageBox.StandardButton.Ok,
        )
        if ans == QMessageBox.StandardButton.Open:
            self._on_reveal_backups_in_finder()

    def _on_backup_error(self, err_msg: str) -> None:
        self.backup_btn.setEnabled(True)
        self.backup_btn.setText("📦 Quick Backup")
        self.status_bar.showMessage(f"❌ Backup failed: {err_msg}", 7000)
        QMessageBox.critical(self, "Backup Failed", f"Failed to create backup:\n{err_msg}")

    def _on_open_restore_dialog(self) -> None:
        """Open guided Restore dialog."""
        dlg = RestoreDialog(parent=self, current_db_path=self.db_path)
        dlg.restore_completed.connect(self._on_restore_completed)
        dlg.exec()

    def _on_restore_completed(self, restored_path: str) -> None:
        """Handle completion of restore operation and reload database."""
        self.status_bar.showMessage("✅ Collection restored. Reloading database...", 6000)
        self.load_database(self.db_path)
        self.trigger_sync(background=True, create_backup=False)

    def _on_reveal_db_in_finder(self) -> None:
        """Reveal active database file in system file manager (Finder / Explorer)."""
        if self.db_path and os.path.exists(self.db_path):
            reveal_in_file_manager(self.db_path)
        else:
            QMessageBox.warning(self, "Reveal in Finder", "Database file not found on disk.")

    def _on_reveal_backups_in_finder(self) -> None:
        """Open default Backups folder in system file manager (Finder / Explorer)."""
        backup_dir = get_default_backup_dir()
        backup_dir.mkdir(parents=True, exist_ok=True)
        open_in_folder(str(backup_dir))

    def _show_status_message(self, msg: str) -> None:
        self.status_bar.showMessage(msg, 5000)

    def _on_db_saved(self) -> None:
        """Trigger background cloud sync after database modifications."""
        self.trigger_sync(background=True, create_backup=True)

    def trigger_sync(
        self,
        background: bool = True,
        create_backup: bool = True,
    ) -> None:
        """Trigger a Turso cloud sync cycle using the unified sync engine."""
        if not is_turso_configured():
            return

        if is_rekordbox_running():
            self.status_bar.showMessage("☁️ Cloud sync deferred: Rekordbox is currently running.", 5000)
            return

        if not self.db_path:
            return

        if background:
            if self._sync_worker and self._sync_worker.isRunning():
                self._sync_pending = True
                return

            self.status_bar.showMessage("☁️ Syncing collection with Turso cloud...", 0)
            worker = SyncWorker(db_path=self.db_path, create_backup=create_backup, parent=self)
            self._sync_worker = worker
            worker.success.connect(self._on_sync_success)
            worker.error.connect(self._on_sync_error)
            worker.finished.connect(lambda: self._on_sync_finished(worker))
            worker.start()
        else:
            try:
                run_sync_cycle(self.db_path, force=False, create_backup=create_backup)
            except Exception as e:
                logger.error("Error during cloud sync: %s", e)

    def _on_sync_success(self, res: SyncResult) -> None:
        msg = f"☁️ Turso cloud sync complete ({res.stats.get('tracks', 0)} tracks, {res.stats.get('playlists', 0)} playlists synced)."
        if res.applied_events > 0:
            msg += f" Ingested {res.applied_events} change(s) from mobile."
            self.load_database(self.db_path)
        self.status_bar.showMessage(msg, 6000)

    def _on_sync_error(self, err_msg: str) -> None:
        self.status_bar.showMessage(f"☁️ Cloud sync notice: {err_msg}", 5000)

    def _on_sync_finished(self, worker: SyncWorker) -> None:
        if self._sync_worker is worker:
            self._sync_worker = None
        if self._sync_pending:
            self._sync_pending = False
            self.trigger_sync(background=True, create_backup=False)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Safely terminate active background workers and perform final cloud sync before closing."""
        if self._db_loader and self._db_loader.isRunning():
            self._db_loader.quit()
            self._db_loader.wait(1000)
        if self._backup_worker and self._backup_worker.isRunning():
            self._backup_worker.wait(2000)

        # Trigger Turso cloud sync upon closing custom manager
        if is_turso_configured() and self.db_path and not is_rekordbox_running():
            if self._sync_worker and self._sync_worker.isRunning():
                self.status_bar.showMessage("Completing active cloud sync before exit...")
                self._sync_worker.wait(10000)
            else:
                self.status_bar.showMessage("Syncing collection with Turso cloud before closing...")
                try:
                    run_sync_cycle(self.db_path, force=False, create_backup=True)
                except Exception as e:
                    logger.error("Error during closing cloud sync: %s", e)

        super().closeEvent(event)
