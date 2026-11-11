"""Guided dialog for restoring Rekordbox backups."""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from rekordbox_collection_manager.backup_restore import (
    get_default_backup_dir,
    list_backups,
)
from rekordbox_collection_manager.gui.assets import get_refresh_icon
from rekordbox_collection_manager.gui.workers import RestoreWorker
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
    FONT_FAMILY,
)
from rekordbox_collection_manager.utils import is_rekordbox_running

logger = logging.getLogger(__name__)


class RestoreDialog(QDialog):
    """Modal dialog guiding users through safe Rekordbox library restoration."""

    restore_completed = Signal(str)

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        current_db_path: Optional[str] = None,
    ):
        super().__init__(parent)
        self.current_db_path = current_db_path
        self.backup_root = get_default_backup_dir()
        self.available_backups: List[Dict[str, Any]] = []
        self.selected_backup: Optional[Dict[str, Any]] = None
        self._restore_worker: Optional[RestoreWorker] = None

        self.setWindowTitle("Restore Rekordbox Collection")
        self.resize(840, 640)
        self.setMinimumSize(740, 520)
        self.setStyleSheet(
            f"QDialog {{ background-color: {COLOR_BG_DARK}; color: {COLOR_TEXT_PRIMARY}; }}"
        )

        self._init_ui()
        self.reload_backups_list()
        self._check_rekordbox_safety()

    def _init_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(24, 20, 24, 20)
        root_layout.setSpacing(16)

        # 1. Header Frame
        header_widget = QWidget()
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(12)

        title_col = QVBoxLayout()
        title_col.setSpacing(4)

        title_lbl = QLabel("Restore Rekordbox Collection")
        title_lbl.setStyleSheet(f"font-size: 18px; font-weight: 700; color: {COLOR_TEXT_PRIMARY};")
        title_col.addWidget(title_lbl)

        sub_lbl = QLabel("Restore your database, playlists, and settings from a previous backup snapshot.")
        sub_lbl.setStyleSheet(f"font-size: 12px; color: {COLOR_TEXT_SECONDARY};")
        title_col.addWidget(sub_lbl)
        header_layout.addLayout(title_col, stretch=1)

        # Safety Chip & Refresh
        self.safety_chip = QLabel("Checking Rekordbox...")
        self.safety_chip.setStyleSheet(
            f"background-color: {COLOR_BG_CARD}; border: 1px solid {COLOR_BORDER}; "
            f"border-radius: 12px; padding: 4px 12px; font-size: 11px; font-weight: 600;"
        )
        header_layout.addWidget(self.safety_chip)

        self.btn_refresh_safety = QPushButton()
        self.btn_refresh_safety.setIcon(get_refresh_icon())
        self.btn_refresh_safety.setIconSize(QSize(14, 14))
        self.btn_refresh_safety.setFixedSize(26, 26)
        self.btn_refresh_safety.setToolTip("Re-check Rekordbox status")
        self.btn_refresh_safety.clicked.connect(self._check_rekordbox_safety)
        header_layout.addWidget(self.btn_refresh_safety)

        root_layout.addWidget(header_widget)

        # 2. Backups Table Area
        table_header_layout = QHBoxLayout()
        section_lbl = QLabel("AVAILABLE BACKUPS (~/Music/rekordbox/Backups)")
        section_lbl.setStyleSheet(
            f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 0.5px;"
        )
        table_header_layout.addWidget(section_lbl, stretch=1)

        self.btn_browse = QPushButton("📁 Browse Other Folder...")
        self.btn_browse.setStyleSheet(
            f"QPushButton {{ background-color: {COLOR_BG_CARD}; border: 1px solid {COLOR_BORDER}; "
            f"border-radius: 4px; padding: 5px 12px; font-size: 11px; font-weight: 600; color: {COLOR_TEXT_PRIMARY}; }} "
            f"QPushButton:hover {{ background-color: {COLOR_BG_HOVER}; border-color: {COLOR_BLUE}; }}"
        )
        self.btn_browse.clicked.connect(self._on_browse_custom_backup)
        table_header_layout.addWidget(self.btn_browse)

        root_layout.addLayout(table_header_layout)

        # Backups Table
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Date & Time", "Snapshot Name", "Type", "Size"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setStyleSheet(
            f"QTableWidget {{ background-color: {COLOR_BG_PANEL}; border: 1px solid {COLOR_BORDER}; "
            f"border-radius: 6px; }} "
            f"QTableWidget::item {{ padding: 8px; border-bottom: 1px solid {COLOR_BORDER}; }} "
            f"QTableWidget::item:selected {{ background-color: {COLOR_BLUE_BG}; color: #ffffff; }} "
            f"QHeaderView::section {{ background-color: {COLOR_BG_CARD}; color: {COLOR_TEXT_MUTED}; "
            f"border: none; border-bottom: 1px solid {COLOR_BORDER}; padding: 6px 8px; font-size: 11px; font-weight: 700; }}"
        )
        self.table.itemSelectionChanged.connect(self._on_table_selection_changed)
        root_layout.addWidget(self.table, stretch=1)

        # 3. Selected Backup Preview Card
        self.preview_card = QFrame()
        self.preview_card.setStyleSheet(
            f"QFrame {{ background-color: {COLOR_BG_CARD}; border: 1px solid {COLOR_BORDER}; "
            f"border-radius: 6px; padding: 10px; }}"
        )
        preview_layout = QVBoxLayout(self.preview_card)
        preview_layout.setContentsMargins(12, 10, 12, 10)
        preview_layout.setSpacing(6)

        self.preview_title = QLabel("Selected: None")
        self.preview_title.setStyleSheet(f"font-weight: 700; font-size: 12px; color: {COLOR_TEXT_PRIMARY};")
        preview_layout.addWidget(self.preview_title)

        self.preview_path_lbl = QLabel("Location: —")
        self.preview_path_lbl.setStyleSheet(f"font-size: 11px; color: {COLOR_TEXT_SECONDARY};")
        preview_layout.addWidget(self.preview_path_lbl)

        self.preview_target_lbl = QLabel(
            "Destination: ~/Library/Pioneer/rekordbox (Standard Collection)"
        )
        self.preview_target_lbl.setStyleSheet(f"font-size: 11px; color: {COLOR_TEXT_MUTED};")
        preview_layout.addWidget(self.preview_target_lbl)

        root_layout.addWidget(self.preview_card)

        # 4. Safety Guarantee Box
        safety_box = QFrame()
        safety_box.setStyleSheet(
            f"QFrame {{ background-color: {COLOR_BLUE_BG}; border: 1px solid {COLOR_BORDER_LIGHT}; "
            f"border-radius: 6px; padding: 8px 12px; }}"
        )
        sb_layout = QHBoxLayout(safety_box)
        sb_layout.setContentsMargins(8, 6, 8, 6)
        sb_layout.setSpacing(10)

        shield_icon = QLabel("🛡️")
        shield_icon.setStyleSheet("font-size: 18px;")
        sb_layout.addWidget(shield_icon)

        safety_text = QLabel(
            "<b>Emergency Safety Net Active:</b> An instant snapshot of your current library "
            "(<code>rekordbox_pre_restore_safety_*</code>) will be saved in your Backups folder "
            "before any files are changed, so you can always roll back."
        )
        safety_text.setTextFormat(Qt.TextFormat.RichText)
        safety_text.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-size: 11px;")
        safety_text.setWordWrap(True)
        sb_layout.addWidget(safety_text, stretch=1)

        root_layout.addWidget(safety_box)

        # 5. Bottom Action Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 4, 0, 0)
        btn_layout.setSpacing(12)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setStyleSheet(
            f"QPushButton {{ background-color: {COLOR_BG_CARD}; border: 1px solid {COLOR_BORDER}; "
            f"border-radius: 6px; padding: 8px 20px; font-weight: 600; color: {COLOR_TEXT_PRIMARY}; }} "
            f"QPushButton:hover {{ background-color: {COLOR_BG_HOVER}; }}"
        )
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)

        btn_layout.addStretch(1)

        self.btn_restore = QPushButton("🔄 Restore Collection")
        self.btn_restore.setStyleSheet(
            f"QPushButton {{ background-color: {COLOR_AMBER}; border: none; "
            f"border-radius: 6px; padding: 8px 24px; font-weight: 700; color: #000000; font-size: 13px; }} "
            f"QPushButton:hover {{ background-color: #fbbf24; }} "
            f"QPushButton:disabled {{ background-color: #382e20; color: #7a6e5a; }}"
        )
        self.btn_restore.clicked.connect(self._on_restore_clicked)
        btn_layout.addWidget(self.btn_restore)

        root_layout.addLayout(btn_layout)

    def _check_rekordbox_safety(self) -> None:
        """Check if Rekordbox is running and update the safety banner and restore button."""
        running = is_rekordbox_running()
        if running:
            self.safety_chip.setText("⚠️ Rekordbox is running! Close it to restore.")
            self.safety_chip.setStyleSheet(
                f"background-color: {COLOR_RED_BG}; border: 1px solid {COLOR_RED_BORDER}; "
                f"border-radius: 12px; padding: 4px 12px; font-size: 11px; font-weight: 700; color: {COLOR_RED};"
            )
            self.btn_restore.setEnabled(False)
            self.btn_restore.setToolTip("Rekordbox must be completely closed before restoring.")
        else:
            self.safety_chip.setText("✅ Rekordbox is closed (Safe to restore)")
            self.safety_chip.setStyleSheet(
                f"background-color: {COLOR_GREEN_BG}; border: 1px solid {COLOR_GREEN_BORDER}; "
                f"border-radius: 12px; padding: 4px 12px; font-size: 11px; font-weight: 700; color: {COLOR_GREEN};"
            )
            self._update_restore_button_state()

    def _update_restore_button_state(self) -> None:
        if not is_rekordbox_running() and self.selected_backup is not None:
            self.btn_restore.setEnabled(True)
            self.btn_restore.setToolTip("Proceed with restoration of selected snapshot.")
        else:
            self.btn_restore.setEnabled(False)

    def reload_backups_list(self) -> None:
        """Scan Backups directory and populate table."""
        self.available_backups = list_backups(self.backup_root)
        self.table.setRowCount(0)

        for row_idx, b in enumerate(self.available_backups):
            self.table.insertRow(row_idx)

            date_str = b["created_at"].strftime("%Y-%m-%d  %H:%M:%S")
            item_date = QTableWidgetItem(date_str)
            item_name = QTableWidgetItem(b["name"])
            item_type = QTableWidgetItem(b["type"])
            item_size = QTableWidgetItem(b["db_size_formatted"])

            item_type.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_size.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            if b.get("is_safety"):
                item_name.setForeground(QColor(COLOR_AMBER))
                item_type.setText("Emergency Snapshot")
                item_type.setForeground(QColor(COLOR_AMBER))

            self.table.setItem(row_idx, 0, item_date)
            self.table.setItem(row_idx, 1, item_name)
            self.table.setItem(row_idx, 2, item_type)
            self.table.setItem(row_idx, 3, item_size)

        if self.available_backups:
            self.table.selectRow(0)
        else:
            self.preview_title.setText("No backups found in ~/Music/rekordbox/Backups")
            self.preview_path_lbl.setText("Use 'Browse Other Folder...' to select a backup manually.")
            self._update_restore_button_state()

    def _on_table_selection_changed(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            self.selected_backup = None
            self._update_restore_button_state()
            return

        idx = rows[0].row()
        if 0 <= idx < len(self.available_backups):
            b = self.available_backups[idx]
            self.selected_backup = b
            self.preview_title.setText(f"Selected: {b['name']} ({b['type']})")
            self.preview_path_lbl.setText(f"Location: {b['path']}")
            self._update_restore_button_state()

    def _on_browse_custom_backup(self) -> None:
        """Allow user to pick a custom backup folder outside the default directory."""
        dir_path = QFileDialog.getExistingDirectory(
            self,
            "Select Rekordbox Backup Folder",
            str(self.backup_root),
        )
        if not dir_path:
            return

        p = Path(dir_path)
        # Check if master.db is inside
        db_path = None
        b_type = "Custom Folder"
        if (p / "Pioneer_Library" / "master.db").is_file():
            db_path = p / "Pioneer_Library" / "master.db"
            b_type = "Full Library"
        elif (p / "master.db").is_file():
            db_path = p / "master.db"
            b_type = "DB Snapshot"
        elif (p / "exportLibrary.db").is_file():
            db_path = p / "exportLibrary.db"
            b_type = "USB Export DB"

        if not db_path:
            QMessageBox.warning(
                self,
                "Invalid Backup Folder",
                f"The selected folder does not contain a valid Rekordbox database (master.db or exportLibrary.db):\n{dir_path}",
            )
            return

        custom_backup = {
            "name": p.name,
            "path": p,
            "db_path": db_path,
            "db_size": db_path.stat().st_size,
            "db_size_formatted": f"{db_path.stat().st_size / (1024*1024):.1f} MB",
            "created_at": None,
            "is_safety": False,
            "type": b_type,
            "has_app_support": (p / "Application_Support").is_dir(),
        }

        # Add to available_backups and select
        self.available_backups.insert(0, custom_backup)
        self.table.insertRow(0)
        self.table.setItem(0, 0, QTableWidgetItem("Custom Selection"))
        self.table.setItem(0, 1, QTableWidgetItem(f"{p.name} (Custom)"))
        self.table.setItem(0, 2, QTableWidgetItem(b_type))
        self.table.setItem(0, 3, QTableWidgetItem(custom_backup["db_size_formatted"]))
        self.table.selectRow(0)

    def _on_restore_clicked(self) -> None:
        """Handle execution of the restore process."""
        if not self.selected_backup:
            return

        if is_rekordbox_running():
            self._check_rekordbox_safety()
            QMessageBox.warning(
                self,
                "Rekordbox is Running",
                "Please close Rekordbox (Cmd + Q) completely before restoring a collection backup.",
            )
            return

        b = self.selected_backup

        # Confirmation prompt
        ans = QMessageBox.warning(
            self,
            "Confirm Collection Restore",
            f"Are you sure you want to restore this backup?\n\n"
            f"Snapshot: {b['name']}\n"
            f"Type: {b['type']}\n\n"
            f"⚠️ This will overwrite your current live collection database.\n"
            f"An automatic emergency safety snapshot will be created beforehand.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        self.btn_restore.setEnabled(False)
        self.btn_restore.setText("⏳ Restoring...")
        self.btn_cancel.setEnabled(False)
        self.btn_browse.setEnabled(False)
        self.table.setEnabled(False)

        self._restore_worker = RestoreWorker(
            backup_path=b["path"],
            backup_root=self.backup_root,
            parent=self,
        )
        self._restore_worker.success.connect(self._on_restore_success)
        self._restore_worker.error.connect(self._on_restore_error)
        self._restore_worker.start()

    def _on_restore_success(self, res: Dict[str, Any]) -> None:
        QMessageBox.information(
            self,
            "Restore Complete",
            f"✅ Your Rekordbox collection has been restored successfully!\n\n"
            f"Database: {res['restored_db_path']}\n"
            f"Safety Snapshot: {res['safety_backup_path']}\n\n"
            f"The collection manager will now reload your restored database.",
        )
        self.restore_completed.emit(res["restored_db_path"])
        self.accept()

    def _on_restore_error(self, err_msg: str) -> None:
        logger.error(f"Restore failed: {err_msg}")
        QMessageBox.critical(
            self,
            "Restore Failed",
            f"Failed to restore the selected backup:\n{err_msg}",
        )
        self.btn_cancel.setEnabled(True)
        self.btn_browse.setEnabled(True)
        self.table.setEnabled(True)
        self.btn_restore.setText("🔄 Restore Collection")
        self._check_rekordbox_safety()
