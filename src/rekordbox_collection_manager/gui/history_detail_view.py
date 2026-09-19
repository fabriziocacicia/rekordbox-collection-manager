"""History Session Detail View Screen.

Presents complete metadata for a Rekordbox history session at the top
and a rich, scrollable, searchable, and sortable tracklist of all played tracks at the bottom.
Utilizes the shared customizable TracksTableWidget for rich audio format badges,
tag & playlist chips, Finder link reveals, and context menus.
"""

from typing import Any, Dict, List, Optional
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.engine import Engine

from rekordbox_collection_manager.gui.theme import (
    COLOR_BG_CARD,
    COLOR_BG_PANEL,
    COLOR_BLUE,
    COLOR_BORDER,
    COLOR_BORDER_LIGHT,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_SECONDARY,
)
from rekordbox_collection_manager.gui.track_table import (
    DEFAULT_HISTORY_COLUMNS,
    NumericTableWidgetItem,
    TrackTableConfig,
    TracksTableWidget,
)
from rekordbox_collection_manager.gui.workers import HistorySessionDetailWorker
from rekordbox_collection_manager.utils import (
    MONTH_INT_TO_NAME,
    extract_year_month_from_name_or_date,
    parse_history_name,
)


class HistoryDetailView(QWidget):
    """Detail view displaying history session overview and tracklist."""

    back_clicked = Signal()
    prev_session_requested = Signal()
    next_session_requested = Signal()
    notify_status = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.engine: Optional[Engine] = None
        self.session_data: Dict[str, Any] = {}
        self.all_tracks: List[Dict[str, Any]] = []
        self.displayed_tracks: List[Dict[str, Any]] = []
        self.filter_text: str = ""
        self.active_worker: Optional[HistorySessionDetailWorker] = None

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
        title_layout.setContentsMargins(12, 8, 12, 8)
        title_layout.setSpacing(10)
        title_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        # Left: Session Icon & Parsed History/Date Name
        session_icon = QLabel("🎧")
        session_icon.setStyleSheet("font-size: 16px; background: transparent;")
        title_layout.addWidget(session_icon)

        self.session_title_label = QLabel("Loading session...")
        self.session_title_label.setStyleSheet(
            f"color: {COLOR_TEXT_PRIMARY}; font-size: 16px; font-weight: 700; letter-spacing: -0.2px; background: transparent;"
        )
        title_layout.addWidget(self.session_title_label)

        title_layout.addStretch(1)

        # Right: Refined Location & Played With Chips
        self.location_chip = QFrame()
        self.location_chip.setObjectName("locChip")
        self.location_chip.setStyleSheet(
            f"QFrame#locChip {{ "
            f"  background-color: {COLOR_BG_PANEL}; "
            f"  border: 1px solid {COLOR_BORDER}; "
            f"  border-radius: 6px; "
            f"}} "
            f"QFrame#locChip QLabel {{ "
            f"  background: transparent; "
            f"}}"
        )
        loc_layout = QHBoxLayout(self.location_chip)
        loc_layout.setContentsMargins(8, 4, 10, 4)
        loc_layout.setSpacing(6)

        loc_icon = QLabel("📍")
        loc_icon.setStyleSheet("font-size: 11px; background: transparent;")
        loc_layout.addWidget(loc_icon)

        loc_tag = QLabel("LOCATION")
        loc_tag.setStyleSheet(
            f"color: {COLOR_TEXT_MUTED}; font-size: 9px; font-weight: 700; letter-spacing: 0.5px; background: transparent;"
        )
        loc_layout.addWidget(loc_tag)

        self.location_val_label = QLabel("")
        self.location_val_label.setStyleSheet(
            f"color: {COLOR_TEXT_PRIMARY}; font-size: 12px; font-weight: 600; background: transparent;"
        )
        loc_layout.addWidget(self.location_val_label)
        self.location_chip.setVisible(False)
        title_layout.addWidget(self.location_chip)

        self.djs_chip = QFrame()
        self.djs_chip.setObjectName("djsChip")
        self.djs_chip.setStyleSheet(
            f"QFrame#djsChip {{ "
            f"  background-color: {COLOR_BG_PANEL}; "
            f"  border: 1px solid {COLOR_BORDER}; "
            f"  border-radius: 6px; "
            f"}} "
            f"QFrame#djsChip QLabel {{ "
            f"  background: transparent; "
            f"}}"
        )
        djs_layout = QHBoxLayout(self.djs_chip)
        djs_layout.setContentsMargins(8, 4, 10, 4)
        djs_layout.setSpacing(6)

        djs_icon = QLabel("👥")
        djs_icon.setStyleSheet("font-size: 11px; background: transparent;")
        djs_layout.addWidget(djs_icon)

        djs_tag = QLabel("PLAYED WITH")
        djs_tag.setStyleSheet(
            f"color: {COLOR_TEXT_MUTED}; font-size: 9px; font-weight: 700; letter-spacing: 0.5px; background: transparent;"
        )
        djs_layout.addWidget(djs_tag)

        self.djs_val_label = QLabel("")
        self.djs_val_label.setStyleSheet(
            f"color: {COLOR_TEXT_PRIMARY}; font-size: 12px; font-weight: 600; background: transparent;"
        )
        djs_layout.addWidget(self.djs_val_label)
        self.djs_chip.setVisible(False)
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
        main_layout.addWidget(self.table, stretch=1)

        # 5. Bottom Status Bar
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
    ) -> None:
        """Load session details and tracklist asynchronously."""
        self.engine = engine

        base_name, location, artist = parse_history_name(session_title)
        self.session_title_label.setText(base_name or f"History Session {history_id}")
        self.prev_btn.setEnabled(has_prev)
        self.next_btn.setEnabled(has_next)
        self.status_label.setText("Loading tracks from database...")

        # Update Location & Played With (DJs) Chips
        if location:
            self.location_val_label.setText(location)
            self.location_chip.setVisible(True)
        else:
            self.location_chip.setVisible(False)

        if artist:
            self.djs_val_label.setText(artist)
            self.djs_chip.setVisible(True)
        else:
            self.djs_chip.setVisible(False)

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

    def _on_data_loaded(self, data: Dict[str, Any]) -> None:
        """Handle loaded session details and track records."""
        self.session_data = data
        self.all_tracks = data.get("tracks", [])
        stats = data.get("stats", {})
        session = data.get("session", {})

        s_name = session.get("name") or self.session_title_label.text()
        s_date = session.get("date") or "—"

        base_name, location, artist = parse_history_name(s_name)
        self.session_title_label.setText(base_name or s_name)

        if location:
            self.location_val_label.setText(location)
            self.location_chip.setVisible(True)
        else:
            self.location_chip.setVisible(False)

        if artist:
            self.djs_val_label.setText(artist)
            self.djs_chip.setVisible(True)
        else:
            self.djs_chip.setVisible(False)

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

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Handle Escape key to go back."""
        if event.key() == Qt.Key.Key_Escape:
            self._on_back_clicked()
            event.accept()
            return
        super().keyPressEvent(event)
