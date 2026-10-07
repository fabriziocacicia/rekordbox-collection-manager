"""Modal dialog for creating a new Rekordbox Playlist or Playlist Folder."""

from typing import Any, Dict, List, Optional
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.engine import Engine

from rekordbox_collection_manager.gui.theme import (
    COLOR_BG_CARD,
    COLOR_BG_DARK,
    COLOR_BG_INPUT,
    COLOR_BG_PANEL,
    COLOR_BLUE,
    COLOR_BLUE_HOVER,
    COLOR_BLUE_PRESSED,
    COLOR_BORDER,
    COLOR_BORDER_FOCUS,
    COLOR_BORDER_LIGHT,
    COLOR_RED,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_SECONDARY,
)
from rekordbox_collection_manager.playlists import create_playlist, fetch_playlist_folders
from rekordbox_collection_manager.utils import is_rekordbox_running


class CreatePlaylistDialog(QDialog):
    """Modal dialog allowing users to create a new Playlist or Playlist Folder in Rekordbox."""

    def __init__(
        self,
        engine: Engine,
        parent: Optional[QWidget] = None,
        initial_name: str = "",
        default_parent: Optional[str] = None,
        is_folder_default: bool = False,
    ):
        super().__init__(parent)
        self.engine = engine
        self.created_playlist: Optional[Dict[str, Any]] = None

        self.setWindowTitle("Create New Playlist")
        self.setFixedWidth(440)
        self.setStyleSheet(
            f"QDialog {{ background-color: {COLOR_BG_DARK}; color: {COLOR_TEXT_PRIMARY}; }}"
        )

        self._init_ui(initial_name, default_parent, is_folder_default)

    def _init_ui(
        self,
        initial_name: str,
        default_parent: Optional[str],
        is_folder_default: bool,
    ) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(20, 20, 20, 20)
        root_layout.setSpacing(16)

        # Header
        header_layout = QVBoxLayout()
        header_layout.setSpacing(4)
        self.title_lbl = QLabel("📑 Create New Playlist")
        self.title_lbl.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {COLOR_TEXT_PRIMARY};")
        header_layout.addWidget(self.title_lbl)

        self.desc_lbl = QLabel("Add a new playlist or playlist folder to your Rekordbox collection.")
        self.desc_lbl.setStyleSheet(f"font-size: 12px; color: {COLOR_TEXT_SECONDARY};")
        header_layout.addWidget(self.desc_lbl)
        root_layout.addLayout(header_layout)

        # Form fields container
        form_card = QWidget()
        form_card.setStyleSheet(
            f"QWidget {{ background-color: {COLOR_BG_PANEL}; border: 1px solid {COLOR_BORDER}; border-radius: 8px; }}"
        )
        form_layout = QVBoxLayout(form_card)
        form_layout.setContentsMargins(16, 14, 16, 16)
        form_layout.setSpacing(12)

        # 1. Type (Standard Playlist vs Folder)
        type_lbl = QLabel("ITEM TYPE")
        type_lbl.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 10px; font-weight: 700; border: none;")
        form_layout.addWidget(type_lbl)

        self.type_combo = QComboBox()
        self.type_combo.setStyleSheet(
            f"QComboBox {{ "
            f"  background-color: {COLOR_BG_INPUT}; "
            f"  color: {COLOR_TEXT_PRIMARY}; "
            f"  border: 1px solid {COLOR_BORDER_LIGHT}; "
            f"  border-radius: 6px; "
            f"  padding: 6px 10px; "
            f"  font-size: 13px; "
            f"}} "
            f"QComboBox::drop-down {{ "
            f"  border: none; "
            f"  width: 20px; "
            f"}} "
            f"QComboBox QAbstractItemView {{ "
            f"  background-color: {COLOR_BG_PANEL}; "
            f"  color: {COLOR_TEXT_PRIMARY}; "
            f"  selection-background-color: #0f3a63; "
            f"  border: 1px solid {COLOR_BORDER}; "
            f"}}"
        )
        self.type_combo.addItem("📑 Standard Playlist", False)
        self.type_combo.addItem("📁 Playlist Folder", True)
        if is_folder_default:
            self.type_combo.setCurrentIndex(1)
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        form_layout.addWidget(self.type_combo)

        # 2. Playlist / Folder Name
        self.name_title_lbl = QLabel("PLAYLIST NAME")
        self.name_title_lbl.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 10px; font-weight: 700; border: none;")
        form_layout.addWidget(self.name_title_lbl)

        self.name_input = QLineEdit(initial_name)
        self._update_name_placeholder()
        self.name_input.setStyleSheet(
            f"QLineEdit {{ "
            f"  background-color: {COLOR_BG_INPUT}; "
            f"  color: {COLOR_TEXT_PRIMARY}; "
            f"  border: 1px solid {COLOR_BORDER_LIGHT}; "
            f"  border-radius: 6px; "
            f"  padding: 6px 10px; "
            f"  font-size: 13px; "
            f"}} "
            f"QLineEdit:focus {{ "
            f"  border-color: {COLOR_BORDER_FOCUS}; "
            f"}}"
        )
        self.name_input.returnPressed.connect(self._on_submit)
        form_layout.addWidget(self.name_input)

        # 3. Parent Folder
        parent_lbl = QLabel("PARENT FOLDER")
        parent_lbl.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 10px; font-weight: 700; border: none;")
        form_layout.addWidget(parent_lbl)

        self.parent_combo = QComboBox()
        self.parent_combo.setStyleSheet(
            f"QComboBox {{ "
            f"  background-color: {COLOR_BG_INPUT}; "
            f"  color: {COLOR_TEXT_PRIMARY}; "
            f"  border: 1px solid {COLOR_BORDER_LIGHT}; "
            f"  border-radius: 6px; "
            f"  padding: 6px 10px; "
            f"  font-size: 13px; "
            f"}} "
            f"QComboBox::drop-down {{ "
            f"  border: none; "
            f"  width: 20px; "
            f"}} "
            f"QComboBox QAbstractItemView {{ "
            f"  background-color: {COLOR_BG_PANEL}; "
            f"  color: {COLOR_TEXT_PRIMARY}; "
            f"  selection-background-color: #0f3a63; "
            f"  border: 1px solid {COLOR_BORDER}; "
            f"}}"
        )

        # Populate parent folders
        folders = fetch_playlist_folders(self.engine)
        sel_idx = 0
        for idx, f in enumerate(folders):
            depth = f.get("depth", 0)
            prefix = "    " * depth
            icon = "🏠 " if depth == 0 else "📁 "
            label = f"{prefix}{icon}{f['name']}"
            self.parent_combo.addItem(label, f["id"])
            if default_parent and (
                f["name"].lower() == str(default_parent).lower()
                or f["id"] == str(default_parent)
            ):
                sel_idx = idx

        if folders:
            self.parent_combo.setCurrentIndex(sel_idx)

        form_layout.addWidget(self.parent_combo)
        root_layout.addWidget(form_card)

        # Rekordbox Safety Warning Label
        self.safety_lbl = QLabel("⚠️ Rekordbox is currently running. Please close it before creating playlists.")
        self.safety_lbl.setStyleSheet(f"color: {COLOR_RED}; font-size: 11px; font-weight: 600;")
        self.safety_lbl.setVisible(is_rekordbox_running())
        root_layout.addWidget(self.safety_lbl)

        # Action Buttons
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)
        button_layout.addStretch(1)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.setStyleSheet(
            f"QPushButton {{ "
            f"  background-color: transparent; "
            f"  border: 1px solid {COLOR_BORDER_LIGHT}; "
            f"  color: {COLOR_TEXT_SECONDARY}; "
            f"  padding: 6px 14px; "
            f"  border-radius: 6px; "
            f"  font-weight: 500; "
            f"}} "
            f"QPushButton:hover {{ "
            f"  background-color: {COLOR_BG_CARD}; "
            f"  color: {COLOR_TEXT_PRIMARY}; "
            f"}}"
        )
        self.btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(self.btn_cancel)

        self.btn_create = QPushButton("Create Playlist")
        self.btn_create.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_create.setStyleSheet(
            f"QPushButton {{ "
            f"  background-color: {COLOR_BLUE}; "
            f"  border: none; "
            f"  color: #ffffff; "
            f"  padding: 6px 16px; "
            f"  border-radius: 6px; "
            f"  font-weight: 600; "
            f"}} "
            f"QPushButton:hover {{ "
            f"  background-color: {COLOR_BLUE_HOVER}; "
            f"}} "
            f"QPushButton:pressed {{ "
            f"  background-color: {COLOR_BLUE_PRESSED}; "
            f"}}"
        )
        self.btn_create.clicked.connect(self._on_submit)
        button_layout.addWidget(self.btn_create)

        root_layout.addLayout(button_layout)

        # Update initial titles/text if folder
        self._on_type_changed(self.type_combo.currentIndex())

        # Focus line edit
        self.name_input.setFocus()
        if initial_name:
            self.name_input.selectAll()

    def _on_type_changed(self, index: int) -> None:
        is_folder = bool(self.type_combo.currentData())
        if is_folder:
            self.setWindowTitle("Create Playlist Folder")
            self.title_lbl.setText("📁 Create Playlist Folder")
            self.name_title_lbl.setText("FOLDER NAME")
            self.btn_create.setText("Create Folder")
        else:
            self.setWindowTitle("Create New Playlist")
            self.title_lbl.setText("📑 Create New Playlist")
            self.name_title_lbl.setText("PLAYLIST NAME")
            self.btn_create.setText("Create Playlist")
        self._update_name_placeholder()

    def _update_name_placeholder(self) -> None:
        is_folder = bool(self.type_combo.currentData())
        if is_folder:
            self.name_input.setPlaceholderText("e.g. Sets 2026, Venues, Genres...")
        else:
            self.name_input.setPlaceholderText("e.g. Melodic Techno, Peak Time, Ibiza...")

    def _on_submit(self) -> None:
        item_name = self.name_input.text().strip()
        is_folder = bool(self.type_combo.currentData())
        kind_str = "Folder" if is_folder else "Playlist"

        if not item_name:
            QMessageBox.warning(self, "Invalid Name", f"{kind_str} name cannot be empty.")
            self.name_input.setFocus()
            return

        if is_rekordbox_running():
            self.safety_lbl.setVisible(True)
            QMessageBox.warning(
                self,
                "Rekordbox is Running",
                f"Rekordbox is running. Please quit Rekordbox before creating {kind_str.lower()}s in the database.",
            )
            return

        parent_id = self.parent_combo.currentData() or "root"

        try:
            res = create_playlist(
                self.engine,
                name=item_name,
                parent_id_or_name=str(parent_id),
                is_folder=is_folder,
            )
            self.created_playlist = res
            self.accept()
        except Exception as e:
            QMessageBox.warning(self, f"Unable to Create {kind_str}", str(e))
            self.name_input.setFocus()
