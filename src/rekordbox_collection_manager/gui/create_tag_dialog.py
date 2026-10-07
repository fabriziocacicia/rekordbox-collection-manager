"""Modal dialog for creating a new Rekordbox My Tag under a chosen category folder."""

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
from rekordbox_collection_manager.tags import create_tag, fetch_tag_categories
from rekordbox_collection_manager.utils import is_rekordbox_running


class CreateTagDialog(QDialog):
    """Modal dialog allowing users to create a new My Tag in Rekordbox."""

    def __init__(
        self,
        engine: Engine,
        parent: Optional[QWidget] = None,
        initial_name: str = "",
        default_category: Optional[str] = None,
    ):
        super().__init__(parent)
        self.engine = engine
        self.created_tag: Optional[Dict[str, Any]] = None

        self.setWindowTitle("Create New Tag")
        self.setFixedWidth(420)
        self.setStyleSheet(
            f"QDialog {{ background-color: {COLOR_BG_DARK}; color: {COLOR_TEXT_PRIMARY}; }}"
        )

        self._init_ui(initial_name, default_category)

    def _init_ui(self, initial_name: str, default_category: Optional[str]) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(20, 20, 20, 20)
        root_layout.setSpacing(16)

        # Header
        header_layout = QVBoxLayout()
        header_layout.setSpacing(4)
        title_lbl = QLabel("🏷️ Create New Tag")
        title_lbl.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {COLOR_TEXT_PRIMARY};")
        header_layout.addWidget(title_lbl)

        desc_lbl = QLabel("Add a new My Tag to your Rekordbox collection.")
        desc_lbl.setStyleSheet(f"font-size: 12px; color: {COLOR_TEXT_SECONDARY};")
        header_layout.addWidget(desc_lbl)
        root_layout.addLayout(header_layout)

        # Form fields container
        form_card = QWidget()
        form_card.setStyleSheet(
            f"QWidget {{ background-color: {COLOR_BG_PANEL}; border: 1px solid {COLOR_BORDER}; border-radius: 8px; }}"
        )
        form_layout = QVBoxLayout(form_card)
        form_layout.setContentsMargins(16, 14, 16, 16)
        form_layout.setSpacing(12)

        # 1. Tag Name
        name_lbl = QLabel("TAG NAME")
        name_lbl.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 10px; font-weight: 700; border: none;")
        form_layout.addWidget(name_lbl)

        self.name_input = QLineEdit(initial_name)
        self.name_input.setPlaceholderText("e.g. Minimal Deep Tech, Afro, Tulum...")
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

        # 2. Category Folder
        cat_lbl = QLabel("CATEGORY FOLDER")
        cat_lbl.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 10px; font-weight: 700; border: none;")
        form_layout.addWidget(cat_lbl)

        self.category_combo = QComboBox()
        self.category_combo.setStyleSheet(
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

        # Populate categories
        categories = fetch_tag_categories(self.engine)
        sel_idx = 0
        for idx, cat in enumerate(categories):
            self.category_combo.addItem(f"📁 {cat['name']}", cat["id"])
            if default_category and (
                cat["name"].lower() == default_category.lower() or cat["id"] == str(default_category)
            ):
                sel_idx = idx

        if categories:
            self.category_combo.setCurrentIndex(sel_idx)

        form_layout.addWidget(self.category_combo)
        root_layout.addWidget(form_card)

        # Rekordbox Safety Warning Label
        self.safety_lbl = QLabel("⚠️ Rekordbox is currently running. Please close it before creating tags.")
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

        self.btn_create = QPushButton("Create Tag")
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

        # Focus line edit
        self.name_input.setFocus()
        if initial_name:
            self.name_input.selectAll()

    def _on_submit(self) -> None:
        tag_name = self.name_input.text().strip()
        if not tag_name:
            QMessageBox.warning(self, "Invalid Name", "Tag name cannot be empty.")
            self.name_input.setFocus()
            return

        if is_rekordbox_running():
            self.safety_lbl.setVisible(True)
            QMessageBox.warning(
                self,
                "Rekordbox is Running",
                "Rekordbox is running. Please quit Rekordbox before creating tags in the database.",
            )
            return

        cat_id = self.category_combo.currentData()
        if not cat_id:
            cat_id = self.category_combo.currentText().replace("📁 ", "").strip()

        try:
            res = create_tag(self.engine, name=tag_name, parent_id_or_name=str(cat_id))
            self.created_tag = res
            self.accept()
        except Exception as e:
            QMessageBox.warning(self, "Unable to Create Tag", str(e))
            self.name_input.setFocus()
