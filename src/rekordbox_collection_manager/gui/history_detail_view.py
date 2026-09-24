"""History Session Detail View Screen.

Presents complete metadata for a Rekordbox history session at the top
and a rich, scrollable, searchable, and sortable tracklist of all played tracks at the bottom.
Utilizes the shared customizable TracksTableWidget for rich audio format badges,
tag & playlist chips, Finder link reveals, and context menus.
"""

from typing import Any, Dict, List, Optional
from PySide6.QtCore import QEvent, QStringListModel, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QCompleter,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.engine import Engine

from rekordbox_collection_manager.gui.theme import (
    COLOR_AMBER,
    COLOR_AMBER_BG,
    COLOR_AMBER_BORDER,
    COLOR_BG_CARD,
    COLOR_BG_HOVER,
    COLOR_BG_INPUT,
    COLOR_BG_PANEL,
    COLOR_BLUE,
    COLOR_BLUE_BG,
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
)
from rekordbox_collection_manager.gui.track_detail_dialog import TrackDetailDialog
from rekordbox_collection_manager.gui.track_table import (
    DEFAULT_HISTORY_COLUMNS,
    NumericTableWidgetItem,
    TrackTableConfig,
    TracksTableWidget,
)
from rekordbox_collection_manager.gui.chip import BadgeChip, DJChip, DJChipsContainer
from rekordbox_collection_manager.gui.workers import HistorySessionDetailWorker
from rekordbox_collection_manager.utils import (
    MONTH_INT_TO_NAME,
    compose_history_name,
    compose_played_with,
    extract_year_month_from_name_or_date,
    parse_history_name,
    parse_played_with,
)

# Standard DJ badge chip for collaborator DJs in HistoryDetailView
DetailDJChip = DJChip


class LocationChip(QFrame):
    """Refined interactive location chip matching Pioneer DJ dark theme."""

    def __init__(self, parent_view: Any):
        super().__init__()
        self.parent_view = parent_view
        self.setObjectName("locChip")
        self.setFixedHeight(30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(f"""
            QFrame#locChip {{
                background-color: {COLOR_BG_PANEL};
                border: 1px solid {COLOR_BORDER};
                border-radius: 6px;
            }}
            QFrame#locChip:hover {{
                border-color: #38bdf8;
            }}
            QFrame#locChip QLabel {{
                background: transparent;
            }}
        """)

    def enterEvent(self, event: Any) -> None:
        super().enterEvent(event)
        if (
            self.parent_view.current_location
            and not self.parent_view.is_deleted
            and hasattr(self.parent_view, "location_clear_btn")
            and hasattr(self.parent_view, "location_edit_widget")
            and not self.parent_view.location_edit_widget.isVisible()
        ):
            self.parent_view.location_clear_btn.setVisible(True)

    def leaveEvent(self, event: Any) -> None:
        super().leaveEvent(event)
        if hasattr(self.parent_view, "location_clear_btn"):
            self.parent_view.location_clear_btn.setVisible(False)

    def mousePressEvent(self, event: Any) -> None:
        if (
            not self.parent_view.is_deleted
            and hasattr(self.parent_view, "location_edit_widget")
            and not self.parent_view.location_edit_widget.isVisible()
        ):
            self.parent_view._start_location_edit()
        super().mousePressEvent(event)


class DJsChip(QFrame):
    """Refined interactive collaborator DJs chip matching Pioneer DJ dark theme."""

    def __init__(self, parent_view: Any):
        super().__init__()
        self.parent_view = parent_view
        self.setObjectName("djsChip")
        self.setFixedHeight(30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(f"""
            QFrame#djsChip {{
                background-color: {COLOR_BG_PANEL};
                border: 1px solid {COLOR_BORDER};
                border-radius: 6px;
            }}
            QFrame#djsChip:hover {{
                border-color: #f59e0b;
            }}
            QFrame#djsChip QLabel {{
                background: transparent;
            }}
        """)

    def mousePressEvent(self, event: Any) -> None:
        if (
            not self.parent_view.is_deleted
            and hasattr(self.parent_view, "dj_input_widget")
            and not self.parent_view.dj_input_widget.isVisible()
            and not parse_played_with(self.parent_view.current_artist)
        ):
            self.parent_view._start_dj_add()
        super().mousePressEvent(event)


class DetailDJsWrapper:
    """Compatibility wrapper exposing get_djs and set_djs for HistoryDetailView."""

    def __init__(self, view: Any):
        self.view = view

    def get_djs(self) -> List[str]:
        return parse_played_with(self.view.current_artist)

    def set_djs(self, djs: List[str]) -> None:
        self.view._set_djs_list(djs)

    def set_available_djs(self, djs: List[str]) -> None:
        self.view.set_available_djs(djs)


class HistoryDetailView(QWidget):
    """Detail view displaying history session overview and tracklist."""

    back_clicked = Signal()
    prev_session_requested = Signal()
    next_session_requested = Signal()
    notify_status = Signal(str)
    open_history_session_requested = Signal(str)  # Emits history_id
    open_playlist_requested = Signal(str)  # Emits playlist_name_or_id
    open_tag_requested = Signal(str)  # Emits tag_name_or_id
    track_detail_requested = Signal(dict, int)  # Emits (track_dict, track_idx)
    delete_session_requested = Signal(str)  # Emits history_id
    restore_session_requested = Signal(str)  # Emits history_id
    session_metadata_updated = Signal(str, str, str, str)  # Emits (history_id, base_name, location, artist)
    save_session_requested = Signal(str)  # Emits history_id
    discard_session_requested = Signal(str)  # Emits history_id
    location_added = Signal(str)  # Emits new location name
    dj_added = Signal(str)  # Emits new DJ name

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.engine: Optional[Engine] = None
        self.db_path: Optional[str] = None
        self.history_id: Optional[str] = None
        self.session_data: Dict[str, Any] = {}
        self.all_tracks: List[Dict[str, Any]] = []
        self.displayed_tracks: List[Dict[str, Any]] = []
        self.filter_text: str = ""
        self.active_worker: Optional[HistorySessionDetailWorker] = None

        self.original_base_name: str = ""
        self.base_name: str = ""
        self.original_location: str = ""
        self.current_location: str = ""
        self.original_artist: str = ""
        self.current_artist: str = ""
        self.available_locations: List[str] = []
        self.available_djs: List[str] = []
        self.is_deleted: bool = False
        self.is_staged: bool = False

        self.djs_chips: List[DetailDJChip] = []
        self.djs_container = DetailDJsWrapper(self)

        self._init_ui()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 14, 16, 14)
        main_layout.setSpacing(12)

        # 1. Top Navigation & Breadcrumb Header
        nav_bar = QHBoxLayout()
        nav_bar.setSpacing(10)

        self.back_btn = QPushButton("← Back to Sessions")
        self.back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_btn.setToolTip("Return to history sessions list (Esc)")
        self.back_btn.clicked.connect(self._on_back_clicked)
        nav_bar.addWidget(self.back_btn)

        self.breadcrumb_label = QLabel("HISTORY SESSIONS")
        self.breadcrumb_label.setStyleSheet(
            f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 0.5px;"
        )
        nav_bar.addWidget(self.breadcrumb_label, stretch=1)

        self.delete_btn = QPushButton("🗑️ Delete Session")
        self.delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.delete_btn.setToolTip("Soft-delete this history session from Rekordbox")
        self.delete_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_RED_BG};
                color: {COLOR_RED};
                border: 1px solid {COLOR_RED_BORDER};
                border-radius: 6px;
                padding: 4px 10px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {COLOR_RED};
                color: #ffffff;
            }}
        """)
        self.delete_btn.setVisible(False)
        self.delete_btn.clicked.connect(self._on_delete_clicked)
        nav_bar.addWidget(self.delete_btn)

        self.restore_btn = QPushButton("♻️ Restore Session")
        self.restore_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.restore_btn.setToolTip("Restore this deleted session back to active collection")
        self.restore_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_GREEN_BG};
                color: {COLOR_GREEN};
                border: 1px solid {COLOR_GREEN_BORDER};
                border-radius: 6px;
                padding: 4px 10px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {COLOR_GREEN};
                color: #ffffff;
            }}
        """)
        self.restore_btn.setVisible(False)
        self.restore_btn.clicked.connect(self._on_restore_clicked)
        nav_bar.addWidget(self.restore_btn)

        self.prev_btn = QPushButton("◀ Prev")
        self.prev_btn.setToolTip("View previous chronological session")
        self.prev_btn.clicked.connect(self.prev_session_requested.emit)
        nav_bar.addWidget(self.prev_btn)

        self.next_btn = QPushButton("Next ▶")
        self.next_btn.setToolTip("View next chronological session")
        self.next_btn.clicked.connect(self.next_session_requested.emit)
        nav_bar.addWidget(self.next_btn)

        main_layout.addLayout(nav_bar)

        # 2. Session Header & Overview KPI Cards
        self.header_panel = QFrame()
        self.header_panel.setObjectName("cardFrame")
        self.header_panel.setStyleSheet(
            f"QFrame#cardFrame {{ background-color: {COLOR_BG_PANEL}; border: 1px solid {COLOR_BORDER}; "
            f"border-radius: 8px; padding: 12px; }}"
        )
        header_layout = QVBoxLayout(self.header_panel)
        header_layout.setContentsMargins(14, 12, 14, 12)
        header_layout.setSpacing(10)

        # Title Card (Refined container surrounding History Name & metadata chips)
        self.title_frame = QFrame()
        self.title_frame.setObjectName("sessionTitleCard")
        self.title_frame.setFixedHeight(48)
        self.title_frame.setStyleSheet(
            f"QFrame#sessionTitleCard {{ "
            f"  background-color: {COLOR_BG_CARD}; "
            f"  border: 1px solid {COLOR_BORDER}; "
            f"  border-radius: 6px; "
            f"}} "
            f"QFrame#sessionTitleCard QLabel {{ "
            f"  background: transparent; "
            f"}}"
        )
        title_layout = QHBoxLayout(self.title_frame)
        title_layout.setContentsMargins(12, 6, 12, 6)
        title_layout.setSpacing(10)
        title_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        # Left: Session Icon & Parsed History/Date Name
        session_icon = QLabel("🎧")
        session_icon.setStyleSheet("font-size: 16px; background: transparent;")
        title_layout.addWidget(session_icon)

        self.session_title_label = QLabel("Loading session...")
        self.session_title_label.setObjectName("sessionTitleLabel")
        self.session_title_label.setStyleSheet(
            f"QLabel#sessionTitleLabel {{ "
            f"  color: {COLOR_TEXT_PRIMARY}; "
            f"  font-size: 16px; "
            f"  font-weight: 700; "
            f"  letter-spacing: -0.2px; "
            f"  background: transparent; "
            f"  padding: 2px 6px; "
            f"  border-radius: 4px; "
            f"}} "
            f"QLabel#sessionTitleLabel:hover {{ "
            f"  background-color: {COLOR_BG_HOVER}; "
            f"  color: #f8fafc; "
            f"}}"
        )
        self.session_title_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.session_title_label.setToolTip("Double-click to rename session")
        self.session_title_label.mouseDoubleClickEvent = self._on_title_double_clicked
        title_layout.addWidget(self.session_title_label)

        # Title Edit Widget (Inline QLineEdit + confirm ✓ + cancel ✕)
        self.title_edit_widget = QWidget()
        self.title_edit_widget.setStyleSheet("background: transparent; border: none;")
        title_edit_layout = QHBoxLayout(self.title_edit_widget)
        title_edit_layout.setContentsMargins(0, 0, 0, 0)
        title_edit_layout.setSpacing(4)
        title_edit_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("Enter session name...")
        self.title_input.setFixedHeight(24)
        self.title_input.setMinimumWidth(180)
        self.title_input.setMaximumWidth(280)
        self.title_input.setStyleSheet(
            f"QLineEdit {{ "
            f"  background-color: {COLOR_BG_INPUT}; "
            f"  color: {COLOR_TEXT_PRIMARY}; "
            f"  border: 1px solid #38bdf8; "
            f"  border-radius: 4px; "
            f"  padding: 1px 8px; "
            f"  font-size: 13px; "
            f"  font-weight: 700; "
            f"}}"
        )
        self.title_input.returnPressed.connect(self._commit_title_edit)
        self.title_input.installEventFilter(self)
        title_edit_layout.addWidget(self.title_input)

        self.title_confirm_btn = QPushButton("✓")
        self.title_confirm_btn.setFixedSize(16, 16)
        self.title_confirm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.title_confirm_btn.setToolTip("Confirm rename (Enter)")
        self.title_confirm_btn.setStyleSheet("""
            QPushButton {
                color: #22c55e;
                font-size: 12px;
                font-weight: 700;
                background: transparent;
                border: none;
                padding: 0;
            }
            QPushButton:hover {
                color: #4ade80;
            }
        """)
        self.title_confirm_btn.clicked.connect(self._commit_title_edit)
        title_edit_layout.addWidget(self.title_confirm_btn)

        self.title_cancel_btn = QPushButton("×")
        self.title_cancel_btn.setFixedSize(16, 16)
        self.title_cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.title_cancel_btn.setToolTip("Cancel edit (Esc)")
        self.title_cancel_btn.setStyleSheet("""
            QPushButton {
                color: #64748b;
                font-size: 14px;
                font-weight: 700;
                background: transparent;
                border: none;
                padding: 0;
            }
            QPushButton:hover {
                color: #ef4444;
            }
        """)
        self.title_cancel_btn.clicked.connect(self._cancel_title_edit)
        title_edit_layout.addWidget(self.title_cancel_btn)

        self.title_edit_widget.setVisible(False)
        title_layout.addWidget(self.title_edit_widget)

        self.deleted_badge = QLabel("🗑️ DELETED")
        self.deleted_badge.setStyleSheet(
            f"background-color: {COLOR_RED_BG}; color: {COLOR_RED}; border: 1px solid {COLOR_RED_BORDER}; "
            f"border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: 700;"
        )
        self.deleted_badge.setVisible(False)
        title_layout.addWidget(self.deleted_badge)

        title_layout.addStretch(1)

        # Right: Refined Location & Played With Chips
        self.location_chip = LocationChip(self)
        loc_layout = QHBoxLayout(self.location_chip)
        loc_layout.setContentsMargins(10, 0, 10, 0)
        loc_layout.setSpacing(6)
        loc_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        loc_icon = QLabel("📍")
        loc_icon.setStyleSheet("font-size: 11px; background: transparent; border: none;")
        loc_layout.addWidget(loc_icon)

        loc_tag = QLabel("LOCATION")
        loc_tag.setStyleSheet(
            f"color: {COLOR_TEXT_MUTED}; font-size: 9px; font-weight: 700; letter-spacing: 0.8px; background: transparent; border: none;"
        )
        loc_layout.addWidget(loc_tag)

        # Location Display Widget (Value, Clear button, or Add button)
        self.location_display_widget = QWidget()
        self.location_display_widget.setStyleSheet("background: transparent; border: none;")
        loc_disp_layout = QHBoxLayout(self.location_display_widget)
        loc_disp_layout.setContentsMargins(0, 0, 0, 0)
        loc_disp_layout.setSpacing(4)
        loc_disp_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self.location_val_label = QLabel("")
        self.location_val_label.setStyleSheet(
            f"color: {COLOR_TEXT_PRIMARY}; font-size: 12px; font-weight: 600; background: transparent; border: none;"
        )
        self.location_val_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.location_val_label.setToolTip("Click to edit location")
        self.location_val_label.mousePressEvent = lambda event: self._start_location_edit() if not self.is_deleted else None
        loc_disp_layout.addWidget(self.location_val_label)

        self.location_clear_btn = QPushButton("×")
        self.location_clear_btn.setFixedSize(14, 14)
        self.location_clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.location_clear_btn.setToolTip("Clear location")
        self.location_clear_btn.setStyleSheet("""
            QPushButton {
                color: #64748b;
                font-size: 13px;
                font-weight: 700;
                background: transparent;
                border: none;
                padding: 0;
                margin: 0;
            }
            QPushButton:hover {
                color: #ef4444;
            }
        """)
        self.location_clear_btn.clicked.connect(self._clear_location)
        self.location_clear_btn.setVisible(False)
        loc_disp_layout.addWidget(self.location_clear_btn)

        self.location_add_btn = QPushButton("+ Add Location")
        self.location_add_btn.setFixedHeight(22)
        self.location_add_btn.setMinimumWidth(95)
        self.location_add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.location_add_btn.setToolTip("Add location to this session")
        self.location_add_btn.setStyleSheet("""
            QPushButton {
                color: #64748b;
                font-size: 11px;
                font-weight: 500;
                background: transparent;
                border: none;
                padding: 0px 2px;
            }
            QPushButton:hover {
                color: #38bdf8;
            }
        """)
        self.location_add_btn.clicked.connect(self._start_location_edit)
        loc_disp_layout.addWidget(self.location_add_btn)

        loc_layout.addWidget(self.location_display_widget)

        # Location Edit Widget (Inline QLineEdit + completer + confirm/cancel)
        self.location_edit_widget = QWidget()
        self.location_edit_widget.setStyleSheet("background: transparent; border: none;")
        loc_edit_layout = QHBoxLayout(self.location_edit_widget)
        loc_edit_layout.setContentsMargins(0, 0, 0, 0)
        loc_edit_layout.setSpacing(4)
        loc_edit_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self.location_input = QLineEdit()
        self.location_input.setPlaceholderText("Enter location...")
        self.location_input.setFixedHeight(22)
        self.location_input.setMinimumWidth(110)
        self.location_input.setMaximumWidth(160)
        self.location_input.setStyleSheet(
            f"QLineEdit {{ "
            f"  background-color: {COLOR_BG_INPUT}; "
            f"  color: {COLOR_TEXT_PRIMARY}; "
            f"  border: 1px solid #38bdf8; "
            f"  border-radius: 4px; "
            f"  padding: 1px 6px; "
            f"  font-size: 11px; "
            f"  font-weight: 600; "
            f"}}"
        )
        self.location_completer_model = QStringListModel(self)
        self.location_completer = QCompleter(self.location_completer_model, self.location_input)
        self.location_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.location_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.location_completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        popup = self.location_completer.popup()
        if popup:
            popup.setMinimumWidth(240)
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
        self.location_completer.activated.connect(self._on_location_completer_activated)
        self.location_input.setCompleter(self.location_completer)
        self.location_input.returnPressed.connect(self._commit_location_edit)
        self.location_input.installEventFilter(self)
        loc_edit_layout.addWidget(self.location_input)

        self.location_confirm_btn = QPushButton("✓")
        self.location_confirm_btn.setFixedSize(16, 16)
        self.location_confirm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.location_confirm_btn.setToolTip("Confirm location (Enter)")
        self.location_confirm_btn.setStyleSheet("""
            QPushButton {
                color: #22c55e;
                font-size: 12px;
                font-weight: 700;
                background: transparent;
                border: none;
                padding: 0;
            }
            QPushButton:hover {
                color: #4ade80;
            }
        """)
        self.location_confirm_btn.clicked.connect(self._commit_location_edit)
        loc_edit_layout.addWidget(self.location_confirm_btn)

        self.location_cancel_btn = QPushButton("×")
        self.location_cancel_btn.setFixedSize(16, 16)
        self.location_cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.location_cancel_btn.setToolTip("Cancel edit (Esc)")
        self.location_cancel_btn.setStyleSheet("""
            QPushButton {
                color: #64748b;
                font-size: 14px;
                font-weight: 700;
                background: transparent;
                border: none;
                padding: 0;
            }
            QPushButton:hover {
                color: #ef4444;
            }
        """)
        self.location_cancel_btn.clicked.connect(self._cancel_location_edit)
        loc_edit_layout.addWidget(self.location_cancel_btn)

        self.location_edit_widget.setVisible(False)
        loc_layout.addWidget(self.location_edit_widget)

        title_layout.addWidget(self.location_chip)

        self.djs_chip = DJsChip(self)
        djs_layout = QHBoxLayout(self.djs_chip)
        djs_layout.setContentsMargins(10, 0, 10, 0)
        djs_layout.setSpacing(6)
        djs_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        djs_icon = QLabel("👥")
        djs_icon.setStyleSheet("font-size: 11px; background: transparent; border: none;")
        djs_layout.addWidget(djs_icon)

        djs_tag = QLabel("PLAYED WITH")
        djs_tag.setStyleSheet(
            f"color: {COLOR_TEXT_MUTED}; font-size: 9px; font-weight: 700; letter-spacing: 0.8px; background: transparent; border: none;"
        )
        djs_layout.addWidget(djs_tag)

        self.djs_chips_container = QWidget()
        self.djs_chips_container.setStyleSheet("background: transparent; border: none;")
        self.djs_chips_layout = QHBoxLayout(self.djs_chips_container)
        self.djs_chips_layout.setContentsMargins(0, 0, 0, 0)
        self.djs_chips_layout.setSpacing(4)
        self.djs_chips_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        djs_layout.addWidget(self.djs_chips_container)

        self.dj_add_btn = QPushButton("+ Add DJ")
        self.dj_add_btn.setFixedHeight(22)
        self.dj_add_btn.setMinimumWidth(65)
        self.dj_add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dj_add_btn.setToolTip("Add collaborator DJ to this session")
        self.dj_add_btn.setStyleSheet("""
            QPushButton {
                color: #64748b;
                font-size: 11px;
                font-weight: 500;
                background: transparent;
                border: none;
                padding: 0px 2px;
            }
            QPushButton:hover {
                color: #f59e0b;
            }
        """)
        self.dj_add_btn.clicked.connect(self._start_dj_add)
        djs_layout.addWidget(self.dj_add_btn)

        # Inline DJ input editor
        self.dj_input_widget = QWidget()
        self.dj_input_widget.setStyleSheet("background: transparent; border: none;")
        dj_in_layout = QHBoxLayout(self.dj_input_widget)
        dj_in_layout.setContentsMargins(0, 0, 0, 0)
        dj_in_layout.setSpacing(4)
        dj_in_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self.dj_input = QLineEdit()
        self.dj_input.setPlaceholderText("Enter DJ name...")
        self.dj_input.setFixedHeight(22)
        self.dj_input.setMinimumWidth(90)
        self.dj_input.setMaximumWidth(150)
        self.dj_input.setStyleSheet(
            f"QLineEdit {{ "
            f"  background-color: {COLOR_BG_INPUT}; "
            f"  color: {COLOR_TEXT_PRIMARY}; "
            f"  border: 1px solid #f59e0b; "
            f"  border-radius: 4px; "
            f"  padding: 1px 6px; "
            f"  font-size: 11px; "
            f"  font-weight: 600; "
            f"}}"
        )
        self.dj_completer_model = QStringListModel(self)
        self.dj_completer = QCompleter(self.dj_completer_model, self.dj_input)
        self.dj_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.dj_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.dj_completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        popup = self.dj_completer.popup()
        if popup:
            popup.setMinimumWidth(240)
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
        self.dj_completer.activated.connect(self._on_dj_completer_activated)
        self.dj_input.setCompleter(self.dj_completer)
        self.dj_input.returnPressed.connect(self._commit_dj_add)
        self.dj_input.installEventFilter(self)
        dj_in_layout.addWidget(self.dj_input)

        dj_confirm = QPushButton("✓")
        dj_confirm.setFixedSize(16, 16)
        dj_confirm.setCursor(Qt.CursorShape.PointingHandCursor)
        dj_confirm.setToolTip("Add DJ (Enter)")
        dj_confirm.setStyleSheet("""
            QPushButton {
                color: #f59e0b;
                font-size: 12px;
                font-weight: 700;
                background: transparent;
                border: none;
                padding: 0;
            }
            QPushButton:hover {
                color: #fbbf24;
            }
        """)
        dj_confirm.clicked.connect(self._commit_dj_add)
        dj_in_layout.addWidget(dj_confirm)

        dj_cancel = QPushButton("×")
        dj_cancel.setFixedSize(16, 16)
        dj_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        dj_cancel.setToolTip("Cancel (Esc)")
        dj_cancel.setStyleSheet("""
            QPushButton {
                color: #64748b;
                font-size: 14px;
                font-weight: 700;
                background: transparent;
                border: none;
                padding: 0;
            }
            QPushButton:hover {
                color: #ef4444;
            }
        """)
        dj_cancel.clicked.connect(self._cancel_dj_add)
        dj_in_layout.addWidget(dj_cancel)

        self.dj_input_widget.setVisible(False)
        djs_layout.addWidget(self.dj_input_widget)

        self.djs_val_label = QLabel("")
        self.djs_val_label.setVisible(False)
        djs_layout.addWidget(self.djs_val_label)

        title_layout.addWidget(self.djs_chip)

        header_layout.addWidget(self.title_frame)

        # KPI Metrics Cards Row
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(10)

        self.kpi_date = self._create_kpi_card("📅 DATE & TIME", "—")
        self.kpi_tracks = self._create_kpi_card("🎵 TOTAL TRACKS", "—")
        self.kpi_runtime = self._create_kpi_card("⏱️ TOTAL RUNTIME", "—")
        self.kpi_bpm = self._create_kpi_card("⚡ BPM PROFILE", "—")
        self.kpi_keys = self._create_kpi_card("🎹 TOP KEYS", "—")

        kpi_row.addWidget(self.kpi_date, stretch=1)
        kpi_row.addWidget(self.kpi_tracks, stretch=1)
        kpi_row.addWidget(self.kpi_runtime, stretch=1)
        kpi_row.addWidget(self.kpi_bpm, stretch=1)
        kpi_row.addWidget(self.kpi_keys, stretch=1)

        header_layout.addLayout(kpi_row)
        main_layout.addWidget(self.header_panel)

        # 3. Tracklist Toolbar (Filter, Stats, and Copy Action)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)

        self.tracklist_header_label = QLabel("TRACKLIST")
        self.tracklist_header_label.setStyleSheet(
            f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 0.8px;"
        )
        toolbar.addWidget(self.tracklist_header_label)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Filter tracks by title, artist, album, key, tags, or playlist...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._on_search_changed)
        toolbar.addWidget(self.search_input, stretch=2)

        self.copy_btn = QPushButton("📋 Copy Tracklist")
        self.copy_btn.setToolTip("Copy formatted tracklist to clipboard (e.g. 01. Artist - Title)")
        self.copy_btn.clicked.connect(self._on_copy_tracklist)
        toolbar.addWidget(self.copy_btn)

        main_layout.addLayout(toolbar)

        # 4. Scrollable Tracks Table (via shared customizable TracksTableWidget)
        table_config = TrackTableConfig(
            columns=list(DEFAULT_HISTORY_COLUMNS),
            index_mode="track_no",
            index_header_tooltip="Track play sequence in this DJ set",
            sorting_mode="client",
            enable_context_menu=True,
            enable_finder_clicks=True,
        )
        self.table = TracksTableWidget(config=table_config, parent=self)
        self.table.status_message.connect(self._on_table_status_message)
        self.table.track_detail_requested.connect(self._open_track_detail)
        self.table.open_playlist_requested.connect(self.open_playlist_requested.emit)
        self.table.open_tag_requested.connect(self.open_tag_requested.emit)
        main_layout.addWidget(self.table, stretch=1)

        # 5. Bottom Action Bar for Unsaved Changes
        self.bottom_bar = QFrame()
        self.bottom_bar.setObjectName("bottomActionBar")
        self.bottom_bar.setStyleSheet(
            f"QFrame#bottomActionBar {{ "
            f"  background-color: {COLOR_BG_PANEL}; "
            f"  border: 1px solid {COLOR_BORDER_FOCUS}; "
            f"  border-radius: 8px; "
            f"  padding: 6px 12px; "
            f"}}"
        )
        bottom_layout = QHBoxLayout(self.bottom_bar)
        bottom_layout.setContentsMargins(10, 6, 10, 6)
        bottom_layout.setSpacing(12)

        self.staged_status_label = QLabel("⚠️ Unsaved modifications — click Save Changes to commit.")
        self.staged_status_label.setStyleSheet(
            f"color: {COLOR_AMBER}; font-weight: 600; font-size: 12px;"
        )
        bottom_layout.addWidget(self.staged_status_label, stretch=1)

        self.discard_btn = QPushButton("Discard Changes")
        self.discard_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.discard_btn.clicked.connect(self._on_discard_clicked)
        bottom_layout.addWidget(self.discard_btn)

        self.save_btn = QPushButton("💾 Save Changes")
        self.save_btn.setObjectName("primaryButton")
        self.save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_btn.clicked.connect(self._on_save_clicked)
        bottom_layout.addWidget(self.save_btn)

        self.bottom_bar.setVisible(False)
        main_layout.addWidget(self.bottom_bar)

        # 6. Bottom Status Bar
        self.status_label = QLabel("0 tracks")
        self.status_label.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-size: 11px;")
        main_layout.addWidget(self.status_label)

    def _create_kpi_card(self, title: str, initial_value: str) -> QFrame:
        """Create a styled compact metric KPI card widget."""
        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ "
            f"  background-color: {COLOR_BG_CARD}; "
            f"  border: 1px solid {COLOR_BORDER}; "
            f"  border-radius: 6px; "
            f"  padding: 6px 10px; "
            f"}} "
            f"QFrame QLabel {{ "
            f"  background: transparent; "
            f"}}"
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(3)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(
            f"color: {COLOR_TEXT_MUTED}; font-size: 10px; font-weight: 700; letter-spacing: 0.5px; background: transparent;"
        )
        layout.addWidget(title_lbl)

        val_lbl = QLabel(initial_value)
        val_lbl.setObjectName("kpiValue")
        val_lbl.setStyleSheet(
            f"color: {COLOR_TEXT_PRIMARY}; font-size: 14px; font-weight: 700; background: transparent;"
        )
        layout.addWidget(val_lbl)

        # Store reference to value label on the card
        card.val_lbl = val_lbl  # type: ignore[attr-defined]
        return card

    def load_session(
        self,
        engine: Engine,
        history_id: str,
        session_title: str = "",
        session_date: str = "",
        has_prev: bool = False,
        has_next: bool = False,
        db_path: Optional[str] = None,
        available_locations: Optional[List[str]] = None,
        available_djs: Optional[List[str]] = None,
        is_staged: bool = False,
        original_name: Optional[str] = None,
        is_deleted: bool = False,
    ) -> None:
        """Load session details and tracklist asynchronously."""
        self.engine = engine
        self.db_path = db_path
        self.history_id = str(history_id)
        self.is_staged = is_staged
        self.is_deleted = is_deleted

        if available_locations is not None:
            self.set_available_locations(available_locations)
        if available_djs is not None:
            self.set_available_djs(available_djs)

        orig_str = original_name or session_title
        orig_base, orig_loc, orig_art = parse_history_name(orig_str)
        self.original_base_name = orig_base
        self.original_location = orig_loc
        self.original_artist = orig_art

        cur_base, cur_loc, cur_art = parse_history_name(session_title)
        self.base_name = cur_base
        self.current_location = cur_loc
        self.current_artist = cur_art

        self._render_title()
        self.prev_btn.setEnabled(has_prev)
        self.next_btn.setEnabled(has_next)
        self.status_label.setText("Loading tracks from database...")

        # Update Location & Played With (DJs) Chips
        self._render_location()
        self._update_detail_djs(self.current_artist)

        # Update Unsaved Modifications Bottom Bar
        has_changes = (
            (self.base_name != self.original_base_name)
            or (self.current_location != self.original_location)
            or (self.current_artist != self.original_artist)
            or is_staged
        )
        self._update_staged_bottom_bar(has_changes)

        # Breadcrumb
        y, m = extract_year_month_from_name_or_date(session_title, session_date)
        if y and m:
            m_name = MONTH_INT_TO_NAME.get(m, f"{m:02d}").upper()
            self.breadcrumb_label.setText(f"HISTORY  ›  {y}  ›  {m:02d} {m_name}")
        else:
            self.breadcrumb_label.setText("HISTORY  ›  SESSIONS")

        # Run background worker
        if self.active_worker and self.active_worker.isRunning():
            self.active_worker.terminate()

        self.active_worker = HistorySessionDetailWorker(engine, history_id, parent=self)
        self.active_worker.success.connect(self._on_data_loaded)
        self.active_worker.error.connect(self._on_data_error)
        self.active_worker.start()

    def _on_delete_clicked(self) -> None:
        session = self.session_data.get("session", {})
        hid = session.get("id") or getattr(self, "history_id", None)
        if hid:
            self.delete_session_requested.emit(str(hid))

    def _on_restore_clicked(self) -> None:
        session = self.session_data.get("session", {})
        hid = session.get("id") or getattr(self, "history_id", None)
        if hid:
            self.restore_session_requested.emit(str(hid))

    def _on_data_loaded(self, data: Dict[str, Any]) -> None:
        """Handle loaded session details and track records."""
        self.session_data = data
        self.all_tracks = data.get("tracks", [])
        stats = data.get("stats", {})
        session = data.get("session", {})

        s_name = session.get("name") or self.session_title_label.text()
        s_date = session.get("date") or "—"

        base_name, location, artist = parse_history_name(s_name)

        is_del = bool(session.get("is_deleted", False))
        self.is_deleted = is_del
        self.deleted_badge.setVisible(is_del)
        self.restore_btn.setVisible(is_del)
        self.delete_btn.setVisible(not is_del)

        if is_del:
            y, m = extract_year_month_from_name_or_date(s_name, s_date)
            if y and m:
                m_name = MONTH_INT_TO_NAME.get(m, f"{m:02d}").upper()
                self.breadcrumb_label.setText(f"HISTORY  ›  🗑️ DELETED SESSIONS  ›  {y}  ›  {m:02d} {m_name}")
            else:
                self.breadcrumb_label.setText("HISTORY  ›  🗑️ DELETED SESSIONS")

        # Synchronize location and artist from database if not staged
        if not self.is_staged and not self._has_staged_changes():
            self.original_base_name = base_name or s_name
            self.base_name = base_name or s_name
            self.original_location = location
            self.current_location = location
            self.original_artist = artist
            self.current_artist = artist
            self._render_title()
            self._render_location()
            self._update_detail_djs(artist)
        else:
            self._render_title()
            self._render_location()
            self._update_detail_djs(self.current_artist)

        # Update KPI Cards
        self.kpi_date.val_lbl.setText(s_date[:19] if s_date else "—")  # type: ignore[attr-defined]
        track_cnt = stats.get("track_count", 0)
        self.kpi_tracks.val_lbl.setText(f"{track_cnt} tracks")  # type: ignore[attr-defined]
        self.kpi_runtime.val_lbl.setText(stats.get("total_duration_str", "0m"))  # type: ignore[attr-defined]

        # BPM
        b_min = stats.get("bpm_min", 0.0)
        b_max = stats.get("bpm_max", 0.0)
        b_avg = stats.get("bpm_avg", 0.0)
        if b_min > 0 and b_max > 0:
            if b_min == b_max:
                bpm_str = f"{b_min} BPM"
            else:
                bpm_str = f"{b_min} – {b_max} (Avg {b_avg})"
        else:
            bpm_str = "—"
        self.kpi_bpm.val_lbl.setText(bpm_str)  # type: ignore[attr-defined]

        # Top Keys
        top_keys = stats.get("top_keys", [])
        if top_keys:
            keys_str = ", ".join(f"{k} ({c})" for k, c in top_keys[:3])
        else:
            keys_str = "—"
        self.kpi_keys.val_lbl.setText(keys_str)  # type: ignore[attr-defined]

        self.tracklist_header_label.setText(f"TRACKLIST ({track_cnt} TRACKS)")

        # Render Table
        self._filter_and_render_tracks()

    def _on_data_error(self, err_msg: str) -> None:
        self.status_label.setText(f"❌ Error loading session details: {err_msg}")
        self.notify_status.emit(f"Error loading session: {err_msg}")

    def _on_search_changed(self, text: str) -> None:
        self.filter_text = text.strip().lower()
        self._filter_and_render_tracks()

    def _on_table_status_message(self, msg: str) -> None:
        self.status_label.setText(msg)
        self.notify_status.emit(msg)

    def _filter_and_render_tracks(self) -> None:
        """Filter tracks and render rows into the table."""
        q = self.filter_text
        if q:
            self.displayed_tracks = [
                t for t in self.all_tracks
                if (
                    q in str(t.get("title", "")).lower()
                    or q in str(t.get("artist", "")).lower()
                    or q in str(t.get("album", "")).lower()
                    or q in str(t.get("genre", "")).lower()
                    or q in str(t.get("key", "")).lower()
                    or q in str(t.get("filename", "")).lower()
                    or q in str(t.get("file_type", "")).lower()
                    or any(q in str(tag).lower() for tag in t.get("tags", []))
                    or any(q in str(pl).lower() for pl in t.get("playlists", []))
                )
            ]
        else:
            self.displayed_tracks = list(self.all_tracks)

        self.table.set_tracks(self.displayed_tracks)

        # Update Footer Label
        disp_cnt = len(self.displayed_tracks)
        total_cnt = len(self.all_tracks)
        if self.filter_text:
            self.status_label.setText(f"Showing {disp_cnt} of {total_cnt} tracks matching '{self.filter_text}'")
        else:
            self.status_label.setText(f"Showing {total_cnt} tracks")

    def _on_copy_tracklist(self) -> None:
        """Copy formatted tracklist to system clipboard."""
        self.table.copy_tracklist_to_clipboard()

    def _on_back_clicked(self) -> None:
        self.back_clicked.emit()

    def _open_track_detail(self, track: Dict[str, Any]) -> None:
        """Request in-place track details view for a track in the history session."""
        if not track:
            return

        track_id = track.get("id") or track.get("content_id")
        curr_idx = 0
        for idx, r in enumerate(self.displayed_tracks):
            if (r.get("id") or r.get("content_id")) == track_id:
                curr_idx = idx
                break

        self.track_detail_requested.emit(track, curr_idx)

    def select_track_at_index(self, idx: int) -> None:
        """Select and scroll to track in table when detail view navigates."""
        if 0 <= idx < self.table.rowCount():
            self.table.selectRow(idx)
            self.table.scrollToRow(idx)

    def set_available_locations(self, locations: List[str]) -> None:
        """Update available locations for autocompleter."""
        self.available_locations = list(locations)
        self.location_completer_model.setStringList(self.available_locations)
        popup = self.location_completer.popup()
        if popup:
            fm = popup.fontMetrics()
            max_w = max((fm.horizontalAdvance(loc) for loc in self.available_locations), default=0)
            popup.setMinimumWidth(max(240, min(max_w + 48, 450)))

    def _has_staged_changes(self) -> bool:
        """Check whether local in-memory modifications differ from original saved database state."""
        return (
            (self.base_name != self.original_base_name)
            or (self.current_location != self.original_location)
            or (self.current_artist != self.original_artist)
        )

    def _render_title(self) -> None:
        """Render session title display state and cursor/tooltip based on deleted status."""
        self.title_edit_widget.setVisible(False)
        self.session_title_label.setVisible(True)
        self.session_title_label.setText(self.base_name or f"History Session {self.history_id}")
        if self.is_deleted:
            self.session_title_label.setCursor(Qt.CursorShape.ArrowCursor)
            self.session_title_label.setToolTip("")
        else:
            self.session_title_label.setCursor(Qt.CursorShape.PointingHandCursor)
            self.session_title_label.setToolTip("Double-click to rename session")

    def _on_title_double_clicked(self, event: Any) -> None:
        """Handle double-click on session title label to initiate inline renaming."""
        if not self.is_deleted:
            self._start_title_edit()

    def _start_title_edit(self) -> None:
        """Switch session title label into inline QLineEdit editor mode."""
        if self.is_deleted:
            return
        self.session_title_label.setVisible(False)
        self.title_edit_widget.setVisible(True)
        self.title_input.setText(self.base_name)
        self.title_input.setFocus()
        self.title_input.selectAll()

    def _commit_title_edit(self) -> None:
        """Commit inline edited session base name."""
        val = self.title_input.text().strip()
        self.title_edit_widget.setVisible(False)
        self.session_title_label.setVisible(True)

        if val and val != self.base_name:
            # If the user pasted a name that has @ or w, parse it gracefully
            parsed_b, parsed_l, parsed_a = parse_history_name(val)
            self.base_name = parsed_b or val
            if parsed_l and not self.current_location:
                self.current_location = parsed_l
                self._render_location()
            if parsed_a and not self.current_artist:
                self.current_artist = parsed_a
                self._render_djs()
            self._render_title()
            self._on_metadata_changed()
        else:
            self._render_title()

    def _cancel_title_edit(self) -> None:
        """Cancel inline title editing and revert back to label."""
        self.title_edit_widget.setVisible(False)
        self.session_title_label.setVisible(True)
        self._render_title()

    def _render_location(self) -> None:
        """Render location display state based on current location value."""
        self.location_edit_widget.setVisible(False)
        self.location_display_widget.setVisible(True)

        if self.current_location:
            self.location_val_label.setText(self.current_location)
            self.location_val_label.setVisible(True)
            self.location_clear_btn.setVisible(False)
            self.location_add_btn.setVisible(False)
            self.location_val_label.setCursor(
                Qt.CursorShape.PointingHandCursor if not self.is_deleted else Qt.CursorShape.ArrowCursor
            )
            self.location_val_label.setToolTip(
                "Click to edit location" if not self.is_deleted else ""
            )
        else:
            self.location_val_label.setText("")
            self.location_val_label.setVisible(False)
            self.location_clear_btn.setVisible(False)
            self.location_add_btn.setVisible(not self.is_deleted)

        self.location_chip.setVisible(True)

    def _start_location_edit(self) -> None:
        """Switch location chip to interactive inline editor mode."""
        if self.is_deleted:
            return
        self.location_display_widget.setVisible(False)
        self.location_edit_widget.setVisible(True)
        self.location_input.setText(self.current_location)
        self.location_input.setFocus()
        self.location_input.selectAll()

    def _commit_location_edit(self) -> None:
        """Commit inline edited location string."""
        val = self.location_input.text().strip()
        self.location_edit_widget.setVisible(False)
        self.location_display_widget.setVisible(True)

        if val != self.current_location:
            self.current_location = val
            if val:
                self.location_added.emit(val)
            self._render_location()
            self._on_metadata_changed()
        else:
            self._render_location()

    def _cancel_location_edit(self) -> None:
        """Cancel inline location editing and revert input display."""
        self.location_edit_widget.setVisible(False)
        self.location_display_widget.setVisible(True)
        self._render_location()

    def _clear_location(self) -> None:
        """Clear assigned location."""
        if self.is_deleted:
            return
        if self.current_location:
            self.current_location = ""
            self._render_location()
            self._on_metadata_changed()

    def _on_location_completer_activated(self, text: str) -> None:
        """Handle selection from location completer popup."""
        self.location_input.setText(text)
        self._commit_location_edit()

    def _on_djs_changed(self, djs: List[str]) -> None:
        """Handle DJ chips addition or removal in the Played With container."""
        new_art = compose_played_with(djs)
        if new_art != self.current_artist:
            self.current_artist = new_art
            self._on_metadata_changed()

    def _on_metadata_changed(self) -> None:
        """Emit metadata updates and toggle unsaved modifications bar."""
        has_changes = self._has_staged_changes()
        self._update_staged_bottom_bar(has_changes)
        if self.history_id:
            self.session_metadata_updated.emit(
                str(self.history_id),
                self.base_name,
                self.current_location,
                self.current_artist,
            )

    def _update_staged_bottom_bar(self, has_changes: bool) -> None:
        """Show or hide bottom action bar based on whether unsaved edits exist."""
        self.bottom_bar.setVisible(has_changes)
        if has_changes:
            parts = []
            if self.base_name != self.original_base_name:
                orig_disp = f"'{self.original_base_name}'" if self.original_base_name else "None"
                curr_disp = f"'{self.base_name}'" if self.base_name else "None"
                parts.append(f"Name: {orig_disp} → {curr_disp}")
            if self.current_location != self.original_location:
                orig_disp = f"'{self.original_location}'" if self.original_location else "None"
                curr_disp = f"'{self.current_location}'" if self.current_location else "None"
                parts.append(f"Location: {orig_disp} → {curr_disp}")
            if self.current_artist != self.original_artist:
                orig_disp = f"'{self.original_artist}'" if self.original_artist else "None"
                curr_disp = f"'{self.current_artist}'" if self.current_artist else "None"
                parts.append(f"Played with: {orig_disp} → {curr_disp}")
            summary = "; ".join(parts) if parts else "Session metadata modified"
            self.staged_status_label.setText(
                f"⚠️ Unsaved modifications ({summary}) — click Save Changes to commit."
            )

    def set_available_djs(self, djs: List[str]) -> None:
        """Update available DJs for autocompleter container."""
        self.available_djs = list(djs)
        if hasattr(self, "dj_completer_model"):
            self.dj_completer_model.setStringList(self.available_djs)
            popup = self.dj_completer.popup()
            if popup:
                fm = popup.fontMetrics()
                max_w = max((fm.horizontalAdvance(d) for d in self.available_djs), default=0)
                popup.setMinimumWidth(max(240, min(max_w + 48, 450)))

    def _start_dj_add(self) -> None:
        """Show inline DJ input."""
        if self.is_deleted:
            return
        self.dj_add_btn.setVisible(False)
        self.dj_input_widget.setVisible(True)
        self.dj_input.clear()
        self.dj_input.setFocus()

    def _commit_dj_add(self) -> None:
        """Commit inline entered DJ name."""
        name = self.dj_input.text().strip()
        self.dj_input_widget.setVisible(False)
        self.dj_add_btn.setVisible(not self.is_deleted)
        if name:
            cur_djs = parse_played_with(self.current_artist)
            if name not in cur_djs:
                cur_djs.append(name)
                self.current_artist = compose_played_with(cur_djs)
                self.dj_added.emit(name)
                self._render_djs()
                self._on_metadata_changed()

    def _cancel_dj_add(self) -> None:
        """Cancel inline DJ input."""
        self.dj_input_widget.setVisible(False)
        self.dj_add_btn.setVisible(not self.is_deleted)

    def _on_dj_completer_activated(self, text: str) -> None:
        """Handle DJ completer selection."""
        self.dj_input.setText(text)
        self._commit_dj_add()

    def _remove_dj(self, name: str) -> None:
        """Remove a DJ from played-with list."""
        if self.is_deleted:
            return
        cur_djs = [d for d in parse_played_with(self.current_artist) if d != name]
        self.current_artist = compose_played_with(cur_djs)
        self._render_djs()
        self._on_metadata_changed()

    def _set_djs_list(self, djs: List[str]) -> None:
        """Set list of DJs programmatically and render."""
        self.current_artist = compose_played_with(djs)
        self._render_djs()

    def _render_djs(self) -> None:
        """Render horizontal DJ chips and add button."""
        for chip in list(self.djs_chips):
            self.djs_chips_layout.removeWidget(chip)
            chip.deleteLater()
        self.djs_chips.clear()

        djs = parse_played_with(self.current_artist)
        for dj in djs:
            chip = DetailDJChip(dj, removable=not self.is_deleted, parent=self.djs_chips_container)
            chip.delete_requested.connect(lambda _, name=dj: self._remove_dj(name))
            self.djs_chips.append(chip)
            self.djs_chips_layout.addWidget(chip)

        if djs:
            self.dj_add_btn.setText("+")
            self.dj_add_btn.setToolTip("Add collaborator DJ")
            self.dj_add_btn.setFixedSize(18, 22)
            self.dj_add_btn.setStyleSheet("""
                QPushButton {
                    color: #f59e0b;
                    font-size: 14px;
                    font-weight: 700;
                    background: transparent;
                    border: none;
                    padding: 0;
                }
                QPushButton:hover {
                    color: #ffffff;
                }
            """)
        else:
            self.dj_add_btn.setText("+ Add DJ")
            self.dj_add_btn.setToolTip("Add collaborator DJ to this session")
            self.dj_add_btn.setFixedHeight(22)
            self.dj_add_btn.setMinimumWidth(65)
            self.dj_add_btn.setStyleSheet("""
                QPushButton {
                    color: #64748b;
                    font-size: 11px;
                    font-weight: 500;
                    background: transparent;
                    border: none;
                    padding: 0px 2px;
                }
                QPushButton:hover {
                    color: #f59e0b;
                }
            """)

        self.dj_add_btn.setVisible(not self.is_deleted and not self.dj_input_widget.isVisible())
        self.djs_val_label.setText(self.current_artist)
        self.djs_chip.setVisible(True)

    def _on_discard_clicked(self) -> None:
        """Revert base name, location and DJs to original saved state."""
        self.base_name = self.original_base_name
        self.current_location = self.original_location
        self.current_artist = self.original_artist
        self._render_title()
        self._render_location()
        self._render_djs()
        self.bottom_bar.setVisible(False)
        if self.history_id:
            self.discard_session_requested.emit(str(self.history_id))

    def _on_save_clicked(self) -> None:
        """Request commit of session modifications to database."""
        if self.history_id:
            self.save_session_requested.emit(str(self.history_id))

    def _update_detail_djs(self, artist_str: str) -> None:
        """Update parsed DJ chips in the detail header."""
        self.current_artist = artist_str
        self._render_djs()

    def eventFilter(self, watched: Any, event: Any) -> bool:
        """Handle Escape key on inline editors."""
        if watched in (getattr(self, "title_input", None), getattr(self, "location_input", None), getattr(self, "dj_input", None)):
            if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                if watched is getattr(self, "title_input", None):
                    self._cancel_title_edit()
                elif watched is self.location_input:
                    self._cancel_location_edit()
                elif watched is self.dj_input:
                    self._cancel_dj_add()
                return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Handle Escape key to go back."""
        if event.key() == Qt.Key.Key_Escape:
            self._on_back_clicked()
            event.accept()
            return
        super().keyPressEvent(event)


