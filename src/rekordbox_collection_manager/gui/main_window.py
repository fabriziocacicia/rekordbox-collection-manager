"""Main Application Window for Rekordbox Collection Manager."""

import os
import shutil
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional
from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QSize, QTimer, Qt
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
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
from rekordbox_collection_manager.gui.assets import get_app_icon, get_asset_path, get_refresh_icon
from rekordbox_collection_manager.gui.history_view import HistoryView
from rekordbox_collection_manager.gui.play_count_view import PlayCountView
from rekordbox_collection_manager.gui.tracks_view import TracksView
from rekordbox_collection_manager.gui.theme import (
    COLOR_BLUE,
    COLOR_BORDER,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_SECONDARY,
)
from rekordbox_collection_manager.gui.workers import DatabaseLoaderWorker
from rekordbox_collection_manager.utils import (
    get_default_master_db_path,
    is_rekordbox_running,
)


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
                "icon": "🔢",
                "title": "Play Counts",
            },
            {
                "id": 3,
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

        # Active Database Label & Browse
        self.db_label = QLabel("Database: Resolving...")
        self.db_label.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-size: 12px;")
        header_layout.addWidget(self.db_label)

        browse_btn = QPushButton("📁 Switch DB...")
        browse_btn.setToolTip("Open custom master.db or USB exportLibrary.db")
        browse_btn.clicked.connect(self._on_browse_db)
        header_layout.addWidget(browse_btn)

        # Quick Backup Button
        backup_btn = QPushButton("📦 Quick Backup")
        backup_btn.setToolTip("Create instant timestamped backup of the database on Desktop")
        backup_btn.clicked.connect(self._on_quick_backup)
        header_layout.addWidget(backup_btn)

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
        self.btn_play_count = self.nav_buttons[2]
        self.btn_info = self.nav_buttons[3]

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
        self.stacked_widget.addWidget(self.history_view)

        self.tracks_view = TracksView(self)
        self.tracks_view.notify_status.connect(self._show_status_message)
        self.stacked_widget.addWidget(self.tracks_view)

        self.play_count_view = PlayCountView(self)
        self.play_count_view.notify_status.connect(self._show_status_message)
        self.stacked_widget.addWidget(self.play_count_view)

        self.info_view = InfoView(self)
        self.stacked_widget.addWidget(self.info_view)

        body_layout.addWidget(self.stacked_widget, stretch=1)
        root_layout.addWidget(body_widget, stretch=1)

        # 3. Status Bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Initializing Rekordbox Collection Manager...")

    def _on_nav_clicked(self, page_id: int) -> None:
        self.stacked_widget.setCurrentIndex(page_id)

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
        self.db_label.setText("Database: Connecting...")

        loader = DatabaseLoaderWorker(db_path, parent=self)
        loader.success.connect(self._on_db_loaded)
        loader.error.connect(self._on_db_error)
        loader.start()

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

        # Shorten path for display in header
        db_filename = os.path.basename(path)
        display_path = db_filename
        parent_dir = os.path.basename(os.path.dirname(path))
        if parent_dir:
            display_path = f"{parent_dir}/{db_filename}"
        self.db_label.setText(f"DB: {display_path}")
        self.db_label.setToolTip(path)

        # Update window title with active database
        self.setWindowTitle(f"Rekordbox Collection Manager — {db_filename}")
        if sys.platform == "darwin":
            try:
                self.setWindowFilePath(path)
            except Exception:
                pass

        # Populate Views
        self.history_view.set_data(engine, history_records)
        self.tracks_view.set_engine(engine, total_tracks=total_tracks)
        self.play_count_view.set_engine(engine)

        self.info_view.set_info({
            "Database File": path,
            "History Table Type": table_format,
            "History Sessions Loaded": len(history_records),
            "Total Collection Tracks": total_tracks,
            "Database File Size": f"{os.path.getsize(path) / (1024 * 1024):.2f} MB" if os.path.exists(path) else "N/A",
            "Last Loaded At": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

        self.status_bar.showMessage(
            f"Database loaded: {len(history_records)} history sessions, {total_tracks} tracks.",
            5000,
        )

    def _on_db_error(self, err_msg: str) -> None:
        self.setWindowTitle("Rekordbox Collection Manager")
        self.status_bar.showMessage(f"❌ Database load error: {err_msg}")
        self.db_label.setText("Database: Error")
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

    def _on_quick_backup(self) -> None:
        """Create a quick timestamped backup of the current database on the Desktop."""
        if not self.db_path or not os.path.exists(self.db_path):
            QMessageBox.warning(self, "Backup Warning", "No valid database is currently open to back up.")
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        desktop_dir = os.path.expanduser("~/Desktop")
        backup_folder = os.path.join(desktop_dir, f"rekordbox_backup_{timestamp}")

        try:
            os.makedirs(backup_folder, exist_ok=True)
            db_filename = os.path.basename(self.db_path)
            dest_file = os.path.join(backup_folder, db_filename)
            shutil.copy2(self.db_path, dest_file)

            # Also copy .edb or wal/shm if present
            for ext in ["-wal", "-shm"]:
                extra_file = self.db_path + ext
                if os.path.exists(extra_file):
                    shutil.copy2(extra_file, os.path.join(backup_folder, os.path.basename(extra_file)))

            self.status_bar.showMessage(f"✅ Backup created at: {backup_folder}", 7000)
            QMessageBox.information(
                self,
                "Backup Successful",
                f"Database snapshot successfully saved to:\n{backup_folder}\n\n"
                f"File: {db_filename}",
            )
        except Exception as e:
            QMessageBox.critical(self, "Backup Failed", f"Failed to create backup:\n{e}")

    def _show_status_message(self, msg: str) -> None:
        self.status_bar.showMessage(msg, 5000)
