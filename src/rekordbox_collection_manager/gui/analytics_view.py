"""Main Analytics View for Rekordbox Collection Manager.

Integrates the KPI engine, 5 specialized interactive visual analytics views,
and a linked track table with Crate Pruning export capabilities (M3U8, XML, Rekordbox Playlist).
"""

import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional
from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QToolTip,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.engine import Engine

from rekordbox_collection_manager.analytics import (
    AnalyticsSnapshot,
    CamelotSliceData,
    CAMELOT_TO_TRADITIONAL,
    create_purge_playlist_in_rekordbox,
    export_tracks_to_m3u8,
    export_tracks_to_rekordbox_xml,
)
from rekordbox_collection_manager.gui.analytics_widgets import (
    CamelotWheelWidget,
    CrateHealthMatrixWidget,
    SetTurnoverWidget,
    SurvivalDecayWidget,
    TransitionGraphWidget,
)
from rekordbox_collection_manager.gui.theme import (
    COLOR_AMBER,
    COLOR_BG_CARD,
    COLOR_BG_DARK,
    COLOR_BG_INPUT,
    COLOR_BG_PANEL,
    COLOR_BLUE,
    COLOR_BORDER,
    COLOR_GREEN,
    COLOR_RED,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_SECONDARY,
)
from rekordbox_collection_manager.gui.track_table import (
    DEFAULT_COLLECTION_COLUMNS,
    TrackColumn,
    TrackTableConfig,
    TracksTableWidget,
)
from rekordbox_collection_manager.gui.workers import AnalyticsLoaderWorker, stop_worker
from rekordbox_collection_manager.utils import is_rekordbox_running

logger = logging.getLogger(__name__)

RESPONSIVE_BREAKPOINT_WIDTH: int = 1120

ANALYTICS_TABLE_COLUMNS: List[str] = [
    TrackColumn.INDEX,
    TrackColumn.TITLE,
    TrackColumn.ARTIST,
    TrackColumn.KEY,
    TrackColumn.BPM,
    TrackColumn.TIME,
    TrackColumn.PLAYS,
    TrackColumn.FOLDER,
]


class KPICard(QFrame):
    """Compact status card displaying a single KPI metric."""

    clicked = Signal()

    def __init__(
        self,
        title: str,
        value: str,
        accent_color: str,
        subtitle: str = "",
        tooltip: str = "",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("kpiCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.accent_color = accent_color
        self._is_active = False
        if tooltip:
            self.setToolTip(tooltip)

        self._update_style()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self.lbl_title = QLabel(title.upper())
        self.lbl_title.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 10px; font-weight: 700; letter-spacing: 0.6px;")
        layout.addWidget(self.lbl_title)

        self.lbl_value = QLabel(value)
        self.lbl_value.setStyleSheet(f"color: {accent_color}; font-size: 20px; font-weight: 800; letter-spacing: -0.3px;")
        layout.addWidget(self.lbl_value)

        if subtitle:
            self.lbl_sub = QLabel(subtitle)
            self.lbl_sub.setWordWrap(True)
            self.lbl_sub.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-size: 10px;")
            layout.addWidget(self.lbl_sub)

    def set_active(self, active: bool) -> None:
        if self._is_active != active:
            self._is_active = active
            self._update_style()

    def is_active(self) -> bool:
        return self._is_active

    def _update_style(self) -> None:
        if self._is_active:
            self.setStyleSheet(f"""
                QFrame#kpiCard {{
                    background-color: #1c2233;
                    border: 2px solid {self.accent_color};
                    border-radius: 8px;
                    padding: 6px 10px;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                QFrame#kpiCard {{
                    background-color: {COLOR_BG_CARD};
                    border: 1px solid {COLOR_BORDER};
                    border-radius: 8px;
                    padding: 6px 10px;
                }}
                QFrame#kpiCard:hover {{
                    border-color: {self.accent_color};
                    background-color: #262632;
                }}
            """)

    def set_value(self, value: str, subtitle: Optional[str] = None) -> None:
        self.lbl_value.setText(value)
        if subtitle and hasattr(self, "lbl_sub"):
            self.lbl_sub.setText(subtitle)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


class AnalyticsView(QWidget):
    """Complete analytics dashboard view with 5 visual representations and track table."""

    notify_status = Signal(str)
    open_history_session_requested = Signal(str)
    open_playlist_requested = Signal(str)
    open_tag_requested = Signal(str)
    replace_file_requested = Signal(dict)
    db_saved = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.engine: Optional[Engine] = None
        self.db_path: str = ""
        self.snapshot: Optional[AnalyticsSnapshot] = None
        self._worker: Optional[AnalyticsLoaderWorker] = None
        self._interactive_refresh: bool = False

        self.current_filtered_tracks: List[Dict[str, Any]] = []
        self._active_filter_label: str = "All Tracks"
        self._user_pinned_layout: bool = False
        self._horizontal_sizes: List[int] = [880, 420]
        self._vertical_sizes: List[int] = [340, 260]

        self._init_ui()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 14, 16, 12)
        main_layout.setSpacing(12)

        # ----------------------------------------------------------------------
        # 1. Header Row (Title, Subtitle, Reload Button)
        # ----------------------------------------------------------------------
        header_row = QHBoxLayout()
        header_row.setSpacing(12)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)

        title_lbl = QLabel("Collection Analytics & Set Flow Insights")
        title_lbl.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-size: 18px; font-weight: 700; letter-spacing: -0.3px;")
        title_col.addWidget(title_lbl)

        sub_lbl = QLabel("Deterministic event analysis: Crate Pruning, Harmonic Distribution, Gig DNA & Transitions")
        sub_lbl.setWordWrap(True)
        sub_lbl.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px;")
        title_col.addWidget(sub_lbl)
        header_row.addLayout(title_col, stretch=1)

        self.btn_layout_toggle = QPushButton("⫤⫥ Lateral")
        self.btn_layout_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_layout_toggle.setToolTip("Switch between Lateral (side-by-side) and Stacked (vertical) layouts")
        self.btn_layout_toggle.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_BG_CARD};
                border: 1px solid {COLOR_BORDER};
                border-radius: 6px;
                color: {COLOR_TEXT_PRIMARY};
                padding: 6px 12px;
                font-weight: 600;
                font-size: 11px;
            }}
            QPushButton:hover {{
                background-color: #2b2b36;
                border-color: {COLOR_BLUE};
            }}
        """)
        self.btn_layout_toggle.clicked.connect(self._toggle_layout_mode)
        header_row.addWidget(self.btn_layout_toggle)

        self.btn_refresh = QPushButton("Recalculate Metrics")
        self.btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_refresh.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_BG_CARD};
                border: 1px solid {COLOR_BORDER};
                border-radius: 6px;
                color: {COLOR_TEXT_PRIMARY};
                padding: 6px 14px;
                font-weight: 600;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: #2b2b36;
                border-color: {COLOR_BLUE};
            }}
        """)
        self.btn_refresh.clicked.connect(lambda: self.refresh(interactive=True))
        header_row.addWidget(self.btn_refresh)

        main_layout.addLayout(header_row)

        # ----------------------------------------------------------------------
        # 2. KPI Cards Bar
        # ----------------------------------------------------------------------
        self.cards_row = QHBoxLayout()
        self.cards_row.setSpacing(10)

        self.card_dead_weight = KPICard(
            "Dead Weight", "0", COLOR_RED, "Prune candidates for removal",
            tooltip="Tracks never played after 5+ sets or unplayed for 6+ consecutive sets",
        )
        self.cards_row.addWidget(self.card_dead_weight)

        self.card_peak = KPICard(
            "Peak Bangers", "0", COLOR_GREEN, "Reliable set staples (Pick >= 60%)",
            tooltip="Core tracks with very high selection frequency",
        )
        self.cards_row.addWidget(self.card_peak)

        self.card_core = KPICard(
            "Core Rotation", "0", COLOR_BLUE, "Reliable rotation (25-60%)",
            tooltip="Consistent repertoire in steady rotation",
        )
        self.cards_row.addWidget(self.card_core)

        self.card_untested = KPICard(
            "Untested", "0", COLOR_AMBER, "New additions awaiting test (protected)",
            tooltip="Recent additions exposed to <= 2 gigs: strictly protected",
        )
        self.cards_row.addWidget(self.card_untested)

        self.card_burnout = KPICard(
            "Burnout", "0", "#f97316", "Saturated tracks (unplayed for 8+ sets)",
            tooltip="Tracks with high historical play count but neglected in recent sets",
        )
        self.cards_row.addWidget(self.card_burnout)

        self.card_sessions = KPICard(
            "Live Sets", "0", "#a855f7", "Valid sessions analyzed",
            tooltip="History sessions with duration >= 20 min and >= 5 tracks",
        )
        self.cards_row.addWidget(self.card_sessions)

        self.kpi_cluster_map = {
            self.card_dead_weight: "Dead Weight (Zavorra)",
            self.card_peak: "Peak Bangers",
            self.card_core: "Core Rotation",
            self.card_untested: "In Incubazione / Untested",
            self.card_burnout: "Burnout Tracks",
        }

        for card, cluster_key in self.kpi_cluster_map.items():
            card.clicked.connect(lambda ck=cluster_key, c=card: self._on_kpi_card_clicked(c, ck))

        main_layout.addLayout(self.cards_row)

        # ----------------------------------------------------------------------
        # 3. Main Splitter (Left: Charts & Analytics Vistas, Right: Tracks Table)
        # ----------------------------------------------------------------------
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)

        # Left Container: Tab Buttons + Stack of 5 Visualizations
        self.chart_container = QWidget()
        left_layout = QVBoxLayout(self.chart_container)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        # Tab Selector Bar
        tab_bar = QHBoxLayout()
        tab_bar.setSpacing(6)

        self.tab_group = QButtonGroup(self)
        self.tab_group.setExclusive(True)

        self._tabs_metadata = [
            {
                "id": 0,
                "label": "Crate Health",
                "icon": "📦",
                "short_desc": "Health Matrix: crosses gig exposure with Pick Rate to isolate dead weight (Death Zone) from set staples.",
                "rich_tooltip": (
                    "<div style='max-width: 420px; font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, sans-serif; line-height: 1.4;'>"
                    "<b style='font-size: 13px; color: #38bdf8;'>Crate Health Matrix (Health & Pruning Matrix)</b><br/><br/>"
                    "<b>What you are viewing:</b><br/>"
                    "Distribution of all library tracks crossing two key dimensions:<br/>"
                    "• <b>X Axis (Exposure):</b> How many live dates/gigs the track was present in your collection.<br/>"
                    "• <b>Y Axis (Pick Rate %):</b> In what percentage of those gigs you actually played it.<br/><br/>"
                    "<b>How to interpret the zones:</b><br/>"
                    "• <b style='color: #ef4444;'>Death Zone (bottom-right):</b> Tracks brought to 5+ gigs but never played, or ignored for over 6 consecutive gigs. These are prime <i>dead weight</i> candidates for pruning.<br/>"
                    "• <b style='color: #10b981;'>Top Performers (top-right):</b> Core workhorses with high rotation and reliability.<br/>"
                    "• <b style='color: #f59e0b;'>In Incubation (bottom-left):</b> New additions exposed to &le; 2 gigs, protected and awaiting test.<br/><br/>"
                    "<i style='color: #94a3b8;'>💡 Click a cell to load its tracks and export the Prune Crate (M3U8, XML, or Rekordbox Playlist).</i>"
                    "</div>"
                ),
            },
            {
                "id": 1,
                "label": "Camelot Wheel",
                "icon": "🎡",
                "short_desc": "Harmonic Wheel: compares catalog track density against actual airtime in your live sets.",
                "rich_tooltip": (
                    "<div style='max-width: 420px; font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, sans-serif; line-height: 1.4;'>"
                    "<b style='font-size: 13px; color: #38bdf8;'>Camelot Wheel (Harmonic Balance)</b><br/><br/>"
                    "<b>What you are viewing:</b><br/>"
                    "24-spoke polar harmonic wheel: inner ring for Minor keys (1A–12A), outer ring for Major keys (1B–12B).<br/><br/>"
                    "<b>How to interpret data:</b><br/>"
                    "• <b>Thermal Colors:</b> Owned track density (from dark blue = few tracks, through gold up to coral red = richest keys).<br/>"
                    "• <b>Center Number:</b> Exact count of tracks in collection for that key.<br/>"
                    "• <b>Cyan Outer Rim:</b> Actual share of tracks played in your live sets in that key.<br/>"
                    "• <b>Center Hub:</b> Statistics on global harmonic balance and dominant key.<br/><br/>"
                    "<i style='color: #94a3b8;'>💡 Click a slice to filter and inspect all tracks in that key.</i>"
                    "</div>"
                ),
            },
            {
                "id": 2,
                "label": "Turnover & DNA",
                "icon": "🧬",
                "short_desc": "Turnover & DNA: setlist rotation, freshness, and automatic gig archetype classification.",
                "rich_tooltip": (
                    "<div style='max-width: 420px; font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, sans-serif; line-height: 1.4;'>"
                    "<b style='font-size: 13px; color: #38bdf8;'>Set Turnover & DNA (Set Identity Card)</b><br/><br/>"
                    "<b>What you are viewing:</b><br/>"
                    "100% stacked bar chart analyzing the tracklist of each gig in chronological order from left to right.<br/><br/>"
                    "<b>How to interpret data:</b><br/>"
                    "• <b>Automatic Archetypes:</b> Instant classification of the set profile (e.g. Prime-Time, Testing Lab, Digger, Duplicate, Balanced).<br/>"
                    "• <b>3 Analysis Lenses:</b><br/>"
                    "  1. <i>Performance Clusters:</i> Deconstructs set across Hits, Rotation, Brand-new tracks, Deep Cuts, and overexposed tracks at that time.<br/>"
                    "  2. <i>Temporal Turnover:</i> Measures influx of brand-new tracks (New Entries), recent continuity, and catalog deep cuts.<br/>"
                    "  3. <i>Previous Set Retention:</i> How many tracks were repeated identically from the immediate prior gig.<br/><br/>"
                    "<i style='color: #94a3b8;'>💡 Click a bar or segment to isolate those exact tracks in the table.</i>"
                    "</div>"
                ),
            },
            {
                "id": 3,
                "label": "Survival Decay",
                "icon": "📉",
                "short_desc": "Decay Curve: highlights the point of no return beyond which unplayed tracks end up forgotten.",
                "rich_tooltip": (
                    "<div style='max-width: 420px; font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, sans-serif; line-height: 1.4;'>"
                    "<b style='font-size: 13px; color: #38bdf8;'>Survival Decay (Kaplan-Meier Decay Curve)</b><br/><br/>"
                    "<b>What you are viewing:</b><br/>"
                    "Tracks what happens to purchased tracks as live gigs pass: what percentage remains at ZERO plays after 1, 2, 3, 5, 10 sets from date added.<br/><br/>"
                    "<b>How to interpret the curve:</b><br/>"
                    "• <b>Stepwise descent:</b> Starts at 100% (all new additions start at zero plays) and descends as tracks get 'baptized' in your sets.<br/>"
                    "• <b style='color: #f59e0b;'>Point of No Return (dashed amber line):</b> Mathematical point where the curve flattens out. If a track is not played within the first 4 gigs, the likelihood of ever being played plunges close to zero.<br/>"
                    "• <b>Purpose:</b> Spot impulse purchases gathering dust before they turn into immovable dead weight.<br/><br/>"
                    "<i style='color: #94a3b8;'>💡 Click a step to view tracks still at zero plays by that gig.</i>"
                    "</div>"
                ),
            },
            {
                "id": 4,
                "label": "Transitions",
                "icon": "🕸️",
                "short_desc": "Transition Graph: sequences played consecutively (A ➔ B) across at least 2 distinct gigs, revealing mixing habits and staple combos.",
                "rich_tooltip": (
                    "<div style='max-width: 440px; font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, sans-serif; line-height: 1.45;'>"
                    "<b style='font-size: 13px; color: #38bdf8;'>Transitions & Combos (Habitual Sequence Graph)</b><br/><br/>"
                    "<b>What you are viewing:</b><br/>"
                    "Directed network map of transitions and 'automatic combos' between tracks emerging from your gig histories.<br/><br/>"
                    "<b>Criteria for a Combo to appear in graph:</b><br/>"
                    "• <b>1. Immediate Succession (A ➔ B):</b> Track B must have been played immediately after Track A in the setlist (direct transition, consecutive TrackNo).<br/>"
                    "• <b>2. Frequency Threshold (&ge; 2 Distinct Gigs):</b> The exact same transition must occur in at least <b>2 distinct live gigs</b>. One-off transitions are excluded.<br/>"
                    "• <b>3. Valid Sessions:</b> Only History playlists with duration &ge; 20 minutes and at least 5 tracks are analyzed.<br/><br/>"
                    "<b>How to interpret the view:</b><br/>"
                    "• <b>Nodes (Circles):</b> Individual tracks (color indicates Camelot ring: cyan = Minor, green = Major). Inner text shows harmonic key (e.g. 8A).<br/>"
                    "• <b>Arrows &amp; Numeric Badges:</b> Direction of transition (incoming to outgoing track) with exact number of times it was repeated.<br/>"
                    "• <b>Purpose:</b> Identify mixing habits and 'safe zones' to decide whether to lean into signature transitions or diversify sets.<br/><br/>"
                    "<i style='color: #94a3b8;'>💡 Click an edge or node to load details and dates in table. Use mouse wheel to zoom and drag background or nodes to reposition.</i>"
                    "</div>"
                ),
            },
        ]

        self.tab_buttons: List[QPushButton] = []
        for meta in self._tabs_metadata:
            btn = QPushButton(meta["label"].replace("&", "&&"))
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(meta["rich_tooltip"])
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR_BG_CARD};
                    border: 1px solid {COLOR_BORDER};
                    border-radius: 6px;
                    color: {COLOR_TEXT_SECONDARY};
                    padding: 6px 14px;
                    font-size: 11px;
                    font-weight: 600;
                }}
                QPushButton:hover {{
                    background-color: #2b2b36;
                    color: {COLOR_TEXT_PRIMARY};
                }}
                QPushButton:checked {{
                    background-color: #0f3a63;
                    border-color: {COLOR_BLUE};
                    color: #ffffff;
                }}
            """)
            if meta["id"] == 0:
                btn.setChecked(True)
            self.tab_group.addButton(btn, meta["id"])
            self.tab_buttons.append(btn)
            tab_bar.addWidget(btn)

        tab_bar.addStretch(1)
        self.tab_group.idClicked.connect(self._on_tab_switched)
        left_layout.addLayout(tab_bar)

        # Section Info Banner with interactive tooltip and description
        self.section_info_bar = QWidget()
        self.section_info_bar.setStyleSheet("background: transparent; border: none;")
        sec_layout = QHBoxLayout(self.section_info_bar)
        sec_layout.setContentsMargins(4, 2, 4, 4)
        sec_layout.setSpacing(8)

        self.lbl_sec_icon = QLabel("📦")
        self.lbl_sec_icon.setStyleSheet("font-size: 12px; background: transparent;")
        sec_layout.addWidget(self.lbl_sec_icon)

        self.lbl_sec_desc = QLabel("")
        self.lbl_sec_desc.setWordWrap(True)
        self.lbl_sec_desc.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-size: 11px; background: transparent;")
        sec_layout.addWidget(self.lbl_sec_desc, stretch=1)

        self.lbl_sec_help = QLabel("How to interpret")
        self.lbl_sec_help.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lbl_sec_help.setStyleSheet(f"""
            QLabel {{
                color: {COLOR_BLUE};
                font-size: 11px;
                font-weight: 600;
                padding: 2px 8px;
                background-color: #0e2740;
                border: 1px solid {COLOR_BLUE};
                border-radius: 4px;
            }}
            QLabel:hover {{
                background-color: #1a416e;
                color: #ffffff;
            }}
        """)
        self.lbl_sec_help.mousePressEvent = lambda e: QToolTip.showText(
            self.lbl_sec_help.mapToGlobal(QPoint(0, self.lbl_sec_help.height() + 2)),
            self.lbl_sec_help.toolTip(),
            self.lbl_sec_help,
        )
        sec_layout.addWidget(self.lbl_sec_help)

        left_layout.addWidget(self.section_info_bar)
        self._update_section_info(0)

        # Stacked Widget for the 5 Vistas
        self.chart_stack = QStackedWidget()

        # Vista 1: Crate Health Matrix Container
        v1_container = QWidget()
        v1_layout = QVBoxLayout(v1_container)
        v1_layout.setContentsMargins(0, 0, 0, 8)
        v1_layout.setSpacing(6)

        self.widget_health_matrix = CrateHealthMatrixWidget(self)
        self.widget_health_matrix.cell_clicked.connect(self._on_matrix_cell_clicked)
        v1_layout.addWidget(self.widget_health_matrix)
        self.chart_stack.addWidget(v1_container)

        # Vista 2: Camelot Wheel Container (Harmonic Profile + Radial Wheel)
        v2_container = QWidget()
        v2_layout = QHBoxLayout(v2_container)
        v2_layout.setContentsMargins(0, 0, 0, 4)
        v2_layout.setSpacing(12)

        self.card_harmonic_profile = self._create_harmonic_profile_card()
        v2_layout.addWidget(self.card_harmonic_profile)

        self.widget_camelot = CamelotWheelWidget(self)
        self.widget_camelot.slice_clicked.connect(self._on_camelot_slice_clicked)
        v2_layout.addWidget(self.widget_camelot, stretch=1)

        self.chart_stack.addWidget(v2_container)

        # Vista 3: Set Turnover & DNA
        self.widget_turnover = SetTurnoverWidget(self)
        self.widget_turnover.session_clicked.connect(self._on_turnover_session_clicked)
        self.chart_stack.addWidget(self.widget_turnover)

        # Vista 4: Survival Decay Curve
        self.widget_survival = SurvivalDecayWidget(self)
        self.widget_survival.step_clicked.connect(self._on_survival_step_clicked)
        self.chart_stack.addWidget(self.widget_survival)

        # Vista 5: Transition Network Graph
        self.widget_transitions = TransitionGraphWidget(self)
        self.widget_transitions.edge_clicked.connect(self._on_transition_edge_clicked)
        self.widget_transitions.node_clicked.connect(self._on_transition_node_clicked)
        self.chart_stack.addWidget(self.widget_transitions)

        left_layout.addWidget(self.chart_stack)
        self.splitter.addWidget(self.chart_container)

        # ----------------------------------------------------------------------
        # 4. Right Container: Interactive Track Table with Crate Export
        # ----------------------------------------------------------------------
        right_container = QWidget()
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(4, 0, 0, 0)
        right_layout.setSpacing(6)

        # Table Control Bar
        table_bar = QHBoxLayout()
        table_bar.setSpacing(8)

        self.lbl_table_title = QLabel("All Tracks")
        self.lbl_table_title.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-size: 13px; font-weight: 700;")
        table_bar.addWidget(self.lbl_table_title)

        self.lbl_table_count = QLabel("(0 tracks)")
        self.lbl_table_count.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px;")
        table_bar.addWidget(self.lbl_table_count)

        table_bar.addStretch(1)

        # Search filter
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter tracks...")
        self.search_input.setFixedWidth(130)
        self.search_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {COLOR_BG_INPUT};
                border: 1px solid {COLOR_BORDER};
                border-radius: 5px;
                padding: 4px 8px;
                color: {COLOR_TEXT_PRIMARY};
                font-size: 11px;
            }}
            QLineEdit:focus {{
                border-color: {COLOR_BLUE};
            }}
        """)
        self.search_input.textChanged.connect(self._apply_table_search)
        table_bar.addWidget(self.search_input)

        # Export Actions Button Menu
        self.btn_export_menu = QPushButton("Export ▼")
        self.btn_export_menu.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_export_menu.setToolTip("Export tracks to Rekordbox playlist, M3U8, or XML")
        self.btn_export_menu.setStyleSheet(f"""
            QPushButton {{
                background-color: #381212;
                border: 1px solid {COLOR_RED};
                border-radius: 6px;
                color: #ff9999;
                padding: 5px 10px;
                font-weight: 700;
                font-size: 11px;
            }}
            QPushButton:hover {{
                background-color: #4a1818;
                color: #ffffff;
            }}
        """)
        self._setup_export_menu()
        table_bar.addWidget(self.btn_export_menu)

        # Expand/Collapse Table Button
        self.btn_expand_table = QPushButton("⤢ Expand")
        self.btn_expand_table.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_expand_table.setToolTip("Expand track table to full height / Hide charts (or double-click splitter)")
        self.btn_expand_table.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_BG_CARD};
                border: 1px solid {COLOR_BORDER};
                border-radius: 6px;
                color: {COLOR_TEXT_SECONDARY};
                padding: 5px 10px;
                font-weight: 600;
                font-size: 11px;
            }}
            QPushButton:hover {{
                background-color: #2b2b36;
                color: {COLOR_TEXT_PRIMARY};
                border-color: {COLOR_BLUE};
            }}
        """)
        self.btn_expand_table.clicked.connect(lambda: self._toggle_table_expanded())
        table_bar.addWidget(self.btn_expand_table)

        right_layout.addLayout(table_bar)

        # Shared TracksTableWidget (client-side fast sorting)
        table_config = TrackTableConfig(
            columns=list(ANALYTICS_TABLE_COLUMNS),
            index_mode="row_number",
            index_header_tooltip="Position",
            sorting_mode="client",
            enable_context_menu=True,
            enable_finder_clicks=True,
        )
        self.table = TracksTableWidget(config=table_config, parent=self)
        self.table.track_detail_requested.connect(self._on_track_detail_requested)
        self.table.replace_file_requested.connect(self.replace_file_requested.emit)
        self.table.open_playlist_requested.connect(self.open_playlist_requested.emit)
        self.table.open_tag_requested.connect(self.open_tag_requested.emit)
        self.table.db_saved.connect(self.db_saved.emit)

        # Proportion table columns for lateral layout with horizontal scrolling
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        t_header = self.table.horizontalHeader()
        t_header.setStretchLastSection(False)
        for c_idx, c_name in enumerate(ANALYTICS_TABLE_COLUMNS):
            t_header.setSectionResizeMode(c_idx, QHeaderView.ResizeMode.Interactive)
            if c_name == TrackColumn.INDEX:
                self.table.setColumnWidth(c_idx, 34)
            elif c_name == TrackColumn.TITLE:
                self.table.setColumnWidth(c_idx, 180)
            elif c_name == TrackColumn.ARTIST:
                self.table.setColumnWidth(c_idx, 120)
            elif c_name == TrackColumn.KEY:
                self.table.setColumnWidth(c_idx, 46)
            elif c_name == TrackColumn.BPM:
                self.table.setColumnWidth(c_idx, 60)
            elif c_name == TrackColumn.TIME:
                self.table.setColumnWidth(c_idx, 54)
            elif c_name == TrackColumn.PLAYS:
                self.table.setColumnWidth(c_idx, 52)
            elif c_name == TrackColumn.FOLDER:
                self.table.setColumnWidth(c_idx, 90)

        right_layout.addWidget(self.table, stretch=1)

        self.splitter.addWidget(right_container)
        handle = self.splitter.handle(1)
        if handle:
            handle.setToolTip("Double-click to collapse/expand charts")
            handle.installEventFilter(self)
        self.splitter.setStretchFactor(0, 7)
        self.splitter.setStretchFactor(1, 3)
        self._apply_splitter_orientation(Qt.Orientation.Horizontal)

        main_layout.addWidget(self.splitter, stretch=1)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if hasattr(self, "splitter") and watched == self.splitter.handle(1):
            if event.type() == QEvent.Type.MouseButtonDblClick:
                self._toggle_table_expanded()
                return True
        return super().eventFilter(watched, event)

    def _toggle_table_expanded(self, expand: Optional[bool] = None) -> None:
        """Toggle or set full table expansion by collapsing/restoring the charts container."""
        if expand is None:
            self._table_expanded = not getattr(self, "_table_expanded", False)
        else:
            self._table_expanded = expand

        if self._table_expanded:
            self.chart_container.setVisible(False)
            self.btn_expand_table.setText("⤡ Restore")
            self.btn_expand_table.setToolTip("Restore charts view (or click any tab / KPI card)")
            self.btn_expand_table.setStyleSheet(f"""
                QPushButton {{
                    background-color: #0f3a63;
                    border: 1px solid {COLOR_BLUE};
                    border-radius: 6px;
                    color: #ffffff;
                    padding: 5px 10px;
                    font-weight: 700;
                    font-size: 11px;
                }}
                QPushButton:hover {{
                    background-color: #1a4d80;
                }}
            """)
        else:
            self.chart_container.setVisible(True)
            self.btn_expand_table.setText("⤢ Expand")
            self.btn_expand_table.setToolTip("Expand track table to full height / Hide charts (or double-click splitter)")
            self.btn_expand_table.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR_BG_CARD};
                    border: 1px solid {COLOR_BORDER};
                    border-radius: 6px;
                    color: {COLOR_TEXT_SECONDARY};
                    padding: 5px 10px;
                    font-weight: 600;
                    font-size: 11px;
                }}
                QPushButton:hover {{
                    background-color: #2b2b36;
                    color: {COLOR_TEXT_PRIMARY};
                    border-color: {COLOR_BLUE};
                }}
            """)

    def _toggle_layout_mode(self) -> None:
        """Manually toggle between Lateral (horizontal) and Stacked (vertical) splitter orientation."""
        if getattr(self, "_table_expanded", False):
            self._toggle_table_expanded(expand=False)
        self._user_pinned_layout = True
        new_orientation = (
            Qt.Orientation.Vertical
            if self.splitter.orientation() == Qt.Orientation.Horizontal
            else Qt.Orientation.Horizontal
        )
        self._apply_splitter_orientation(new_orientation)

    def _apply_splitter_orientation(self, orientation: Qt.Orientation) -> None:
        # Save previous sizes if valid and already configured
        if self.splitter.count() >= 2:
            current_sizes = self.splitter.sizes()
            if sum(current_sizes) > 0:
                if self.splitter.orientation() == Qt.Orientation.Horizontal:
                    self._horizontal_sizes = list(current_sizes)
                else:
                    self._vertical_sizes = list(current_sizes)

        self.splitter.setOrientation(orientation)

        if orientation == Qt.Orientation.Horizontal:
            self.splitter.setStyleSheet(f"""
                QSplitter::handle:horizontal {{
                    background-color: {COLOR_BORDER};
                    width: 1px;
                    margin: 0px 4px;
                }}
                QSplitter::handle:horizontal:hover {{
                    background-color: {COLOR_BLUE};
                }}
            """)
            self.splitter.setStretchFactor(0, 7)
            self.splitter.setStretchFactor(1, 3)
            if hasattr(self, "_horizontal_sizes") and sum(self._horizontal_sizes) > 0:
                self.splitter.setSizes(self._horizontal_sizes)
            else:
                w = self.width() or 1300
                chart_w = max(660, int(w * 0.68))
                table_w = max(380, w - chart_w)
                self.splitter.setSizes([chart_w, table_w])
            if hasattr(self, "btn_layout_toggle"):
                self.btn_layout_toggle.setText("⫤⫥ Lateral")
                self.btn_layout_toggle.setToolTip("Layout: Lateral (Side-by-Side). Click to switch to Stacked.")
        else:
            self.splitter.setStyleSheet(f"""
                QSplitter::handle:vertical {{
                    background-color: {COLOR_BORDER};
                    height: 1px;
                    margin: 4px 0px;
                }}
                QSplitter::handle:vertical:hover {{
                    background-color: {COLOR_BLUE};
                }}
            """)
            self.splitter.setStretchFactor(0, 6)
            self.splitter.setStretchFactor(1, 4)
            if hasattr(self, "_vertical_sizes") and sum(self._vertical_sizes) > 0:
                self.splitter.setSizes(self._vertical_sizes)
            else:
                total_h = self.splitter.height() or 500
                top_h = max(280, int(total_h * 0.58))
                self.splitter.setSizes([top_h, max(180, total_h - top_h)])
            if hasattr(self, "btn_layout_toggle"):
                self.btn_layout_toggle.setText("⬍ Stacked")
                self.btn_layout_toggle.setToolTip("Layout: Stacked (Vertical). Click to switch to Lateral.")

    def _handle_responsive_layout(self, width: Optional[int] = None) -> None:
        """Evaluate responsive breakpoint and adapt orientation if not pinned by user."""
        if getattr(self, "_user_pinned_layout", False):
            return
        w = width if width is not None else self.width()
        target = (
            Qt.Orientation.Horizontal
            if w >= RESPONSIVE_BREAKPOINT_WIDTH
            else Qt.Orientation.Vertical
        )
        if hasattr(self, "splitter") and self.splitter.orientation() != target:
            self._apply_splitter_orientation(target)

    def resize(self, *args) -> None:
        super().resize(*args)
        if len(args) == 2 and isinstance(args[0], (int, float)):
            self._handle_responsive_layout(int(args[0]))
        elif len(args) == 1 and hasattr(args[0], "width"):
            self._handle_responsive_layout(int(args[0].width()))
        else:
            self._handle_responsive_layout()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._handle_responsive_layout(event.size().width())

    def _setup_export_menu(self) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {COLOR_BG_CARD};
                border: 1px solid {COLOR_BORDER};
                color: {COLOR_TEXT_PRIMARY};
                font-size: 12px;
                padding: 4px;
            }}
            QMenu::item {{
                padding: 6px 14px;
                border-radius: 4px;
            }}
            QMenu::item:selected {{
                background-color: {COLOR_BLUE};
                color: #ffffff;
            }}
        """)

        act_m3u = QAction("Export M3U8 Playlist...", self)
        act_m3u.triggered.connect(self._export_m3u8)
        menu.addAction(act_m3u)

        act_xml = QAction("Export Rekordbox XML...", self)
        act_xml.triggered.connect(self._export_xml)
        menu.addAction(act_xml)

        menu.addSeparator()

        act_rekordbox = QAction("Create 'Prune Crate' Playlist in Rekordbox", self)
        act_rekordbox.triggered.connect(self._create_rekordbox_playlist)
        menu.addAction(act_rekordbox)

        self.btn_export_menu.setMenu(menu)

    def _create_harmonic_profile_card(self) -> QFrame:
        """Create companion card summarizing collection harmonic distribution."""
        card = QFrame()
        card.setFixedWidth(220)
        card.setStyleSheet(f"""
            QFrame {{
                background-color: {COLOR_BG_CARD};
                border: 1px solid {COLOR_BORDER};
                border-radius: 8px;
            }}
        """)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        lbl_header = QLabel("📊 HARMONIC PROFILE")
        lbl_header.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {COLOR_TEXT_PRIMARY}; border: none; background: transparent;")
        layout.addWidget(lbl_header)

        # Mode Distribution (Minor vs Major)
        lbl_mode_title = QLabel("Mode Balance")
        lbl_mode_title.setStyleSheet(f"font-size: 10px; font-weight: 600; color: {COLOR_TEXT_SECONDARY}; border: none; background: transparent;")
        layout.addWidget(lbl_mode_title)

        # Ratio Bar
        self.bar_mode_container = QFrame()
        self.bar_mode_container.setFixedHeight(7)
        self.bar_mode_container.setStyleSheet(f"background-color: {COLOR_BG_PANEL}; border-radius: 3px; border: none;")
        bar_layout = QHBoxLayout(self.bar_mode_container)
        bar_layout.setContentsMargins(0, 0, 0, 0)
        bar_layout.setSpacing(2)

        self.bar_minor = QFrame()
        self.bar_minor.setStyleSheet("background-color: #06b6d4; border-radius: 3px; border: none;")
        bar_layout.addWidget(self.bar_minor, stretch=80)

        self.bar_major = QFrame()
        self.bar_major.setStyleSheet("background-color: #10b981; border-radius: 3px; border: none;")
        bar_layout.addWidget(self.bar_major, stretch=20)
        layout.addWidget(self.bar_mode_container)

        self.lbl_mode_ratio = QLabel("Minor: —% | Major: —%")
        self.lbl_mode_ratio.setStyleSheet(f"font-size: 10px; color: {COLOR_TEXT_MUTED}; border: none; background: transparent;")
        layout.addWidget(self.lbl_mode_ratio)

        # Divider
        div1 = QFrame()
        div1.setFixedHeight(1)
        div1.setStyleSheet(f"background-color: {COLOR_BORDER}; border: none;")
        layout.addWidget(div1)

        # Dominant Keys
        lbl_dom_title = QLabel("Top Dominant Keys")
        lbl_dom_title.setStyleSheet(f"font-size: 10px; font-weight: 600; color: {COLOR_TEXT_SECONDARY}; border: none; background: transparent;")
        layout.addWidget(lbl_dom_title)

        self.lbl_dom_keys: List[QLabel] = []
        for i in range(3):
            lbl = QLabel(f"{i + 1}. —")
            lbl.setStyleSheet(f"font-size: 11px; color: {COLOR_TEXT_PRIMARY}; border: none; background: transparent;")
            layout.addWidget(lbl)
            self.lbl_dom_keys.append(lbl)

        # Divider
        div2 = QFrame()
        div2.setFixedHeight(1)
        div2.setStyleSheet(f"background-color: {COLOR_BORDER}; border: none;")
        layout.addWidget(div2)

        # Catalog Gap
        lbl_gap_title = QLabel("Collection Gap (Least Owned)")
        lbl_gap_title.setStyleSheet(f"font-size: 10px; font-weight: 600; color: {COLOR_TEXT_SECONDARY}; border: none; background: transparent;")
        layout.addWidget(lbl_gap_title)

        self.lbl_gap_key = QLabel("—")
        self.lbl_gap_key.setStyleSheet(f"font-size: 11px; color: {COLOR_TEXT_MUTED}; border: none; background: transparent;")
        layout.addWidget(self.lbl_gap_key)

        layout.addStretch(1)
        return card

    def _update_harmonic_profile(self, slices: Dict[str, CamelotSliceData]) -> None:
        """Update companion card with mode distribution and dominant keys."""
        if not slices:
            return

        minor_cnt = sum(s.catalog_count for code, s in slices.items() if code.endswith("A"))
        major_cnt = sum(s.catalog_count for code, s in slices.items() if code.endswith("B"))
        total = minor_cnt + major_cnt
        if total > 0:
            minor_pct = round((minor_cnt / total) * 100)
            major_pct = 100 - minor_pct
            self.lbl_mode_ratio.setText(f"Minor (A): {minor_pct}% ({minor_cnt}) | Major (B): {major_pct}% ({major_cnt})")
            bar_layout = self.bar_mode_container.layout()
            if bar_layout:
                bar_layout.setStretch(0, max(1, minor_pct))
                bar_layout.setStretch(1, max(1, major_pct))

        # Dominant Keys
        sorted_slices = sorted(slices.values(), key=lambda s: s.catalog_count, reverse=True)
        for idx in range(3):
            if idx < len(sorted_slices) and sorted_slices[idx].catalog_count > 0:
                s = sorted_slices[idx]
                pct = round(s.catalog_share_pct, 1)
                self.lbl_dom_keys[idx].setText(f"{idx + 1}. <b>{s.code}</b> ({s.traditional}) — {s.catalog_count} ({pct}%)")
            else:
                self.lbl_dom_keys[idx].setText(f"{idx + 1}. —")

        # Gap (least represented key > 0, or lowest)
        with_tracks = [s for s in sorted_slices if s.catalog_count > 0]
        if with_tracks:
            gap = with_tracks[-1]
            self.lbl_gap_key.setText(f"Lowest: <b>{gap.code}</b> ({gap.traditional}) — {gap.catalog_count} tracks")
        else:
            self.lbl_gap_key.setText("No tracks categorized")

    def _jump_to_key(self, key_code: str) -> None:
        """Programmatically select a Camelot key and display its tracks."""
        if not self.snapshot or key_code not in self.snapshot.camelot_slices:
            return
        self.widget_camelot.select_slice(key_code)
        s = self.snapshot.camelot_slices[key_code]
        self._on_camelot_slice_clicked(key_code, s.track_ids)

    def set_engine(self, engine: Engine, db_path: str = "") -> None:
        """Assign active database engine and compute analytics."""
        self.engine = engine
        self.db_path = db_path
        self.refresh(interactive=False)

    def refresh(self, interactive: bool = False) -> None:
        """Trigger background worker to compute analytics."""
        if not self.engine:
            return

        self._interactive_refresh = interactive
        self.btn_refresh.setEnabled(False)
        self.btn_refresh.setText("⏳ Calculating...")
        self.notify_status.emit("Calculating analytics and advanced metrics...")

        if self._worker:
            stop_worker(self._worker)

        worker = AnalyticsLoaderWorker(engine=self.engine)
        self._worker = worker
        worker.success.connect(self._on_analytics_loaded)
        worker.error.connect(self._on_analytics_error)
        worker.finished.connect(lambda: self._on_worker_finished(worker))
        worker.start()

    def _on_worker_finished(self, worker: AnalyticsLoaderWorker) -> None:
        if self._worker is worker:
            self._worker = None

    def cleanup(self) -> None:
        """Stop active analytics loader worker cleanly."""
        if getattr(self, "_worker", None):
            stop_worker(self._worker, wait_ms=1000)
            self._worker = None

    def _on_analytics_loaded(self, snapshot: AnalyticsSnapshot) -> None:
        self.snapshot = snapshot
        self.btn_refresh.setEnabled(True)
        self.btn_refresh.setText("Recalculate Metrics")

        # Update KPI Cards
        self.card_dead_weight.set_value(str(snapshot.dead_weight_count))
        self.card_peak.set_value(str(snapshot.peak_bangers_count))
        self.card_core.set_value(str(snapshot.core_rotation_count))
        self.card_untested.set_value(str(snapshot.untested_count))
        self.card_burnout.set_value(str(snapshot.burnout_count))
        self.card_sessions.set_value(str(snapshot.total_valid_sessions))

        # Update Child Widgets
        self.widget_health_matrix.set_data(snapshot.health_matrix)
        self.widget_camelot.set_data(snapshot.camelot_slices)
        self.widget_turnover.set_data(snapshot.turnover_series)
        self.widget_survival.set_data(snapshot.survival_curve, snapshot.point_of_no_return_set)
        self.widget_transitions.set_data(snapshot.transition_edges, snapshot.transition_nodes)

        # Update Harmonic Profile Card
        self._update_harmonic_profile(snapshot.camelot_slices)

        # Default filter: if dead weight tracks exist, display them to facilitate crate pruning
        if snapshot.dead_weight_count > 0:
            self._filter_by_cluster("Dead Weight (Zavorra)")
        else:
            self._set_active_kpi_card(None)
            all_records = [t.to_dict() for t in snapshot.tracks_by_id.values()]
            self._display_tracks(all_records, "All catalog tracks")

        self.notify_status.emit(
            f"Analytics updated: {snapshot.total_valid_sessions} sets analyzed, "
            f"{snapshot.dead_weight_count} dead weight tracks identified."
        )

    def _on_analytics_error(self, err_msg: str) -> None:
        self.btn_refresh.setEnabled(True)
        self.btn_refresh.setText("Recalculate Metrics")
        self.notify_status.emit(f"Analytics calculation error: {err_msg}")
        if self._interactive_refresh:
            QMessageBox.warning(self, "Analytics Error", f"Unable to calculate analytics:\n{err_msg}")

    def _on_tab_switched(self, tab_id: int) -> None:
        if getattr(self, "_table_expanded", False):
            self._toggle_table_expanded(expand=False)
        self.chart_stack.setCurrentIndex(tab_id)
        self._update_section_info(tab_id)

    def _update_section_info(self, tab_id: int) -> None:
        """Update section description banner and guide tooltip for active tab."""
        if hasattr(self, "_tabs_metadata") and 0 <= tab_id < len(self._tabs_metadata):
            meta = self._tabs_metadata[tab_id]
            self.lbl_sec_icon.setText(meta["icon"])
            self.lbl_sec_desc.setText(meta["short_desc"])
            self.lbl_sec_help.setToolTip(meta["rich_tooltip"])
            self.section_info_bar.setToolTip(meta["rich_tooltip"])

    # --------------------------------------------------------------------------
    # Interactive Filtering Callbacks
    # --------------------------------------------------------------------------

    def _on_kpi_card_clicked(self, card: KPICard, cluster_key: str) -> None:
        if getattr(self, "_table_expanded", False):
            self._toggle_table_expanded(expand=False)
        if card.is_active():
            self._set_active_kpi_card(None)
            if self.snapshot:
                records = [t.to_dict() for t in self.snapshot.tracks_by_id.values()]
                self._display_tracks(records, "All Tracks")
        else:
            self._filter_by_cluster(cluster_key)

    def _set_active_kpi_card(self, active_card: Optional[KPICard]) -> None:
        for card in self.kpi_cluster_map:
            card.set_active(card is active_card)

    def _filter_by_cluster(self, cluster_name: str) -> None:
        if not self.snapshot:
            return
        if cluster_name in ("All", "Tutti"):
            self._set_active_kpi_card(None)
            records = [t.to_dict() for t in self.snapshot.tracks_by_id.values()]
            self._display_tracks(records, "All Tracks")
            return

        cluster_map = {
            "Dead Weight": "Dead Weight (Zavorra)",
            "Untested": "In Incubazione / Untested",
        }
        target_key = cluster_map.get(cluster_name, cluster_name)

        matched_card = None
        for card, c_key in self.kpi_cluster_map.items():
            if c_key == target_key or cluster_name in (c_key, card.lbl_title.text()):
                matched_card = card
                break
        self._set_active_kpi_card(matched_card)

        c_ids = self.snapshot.cluster_track_ids.get(target_key, [])
        records = [self.snapshot.tracks_by_id[cid].to_dict() for cid in c_ids if cid in self.snapshot.tracks_by_id]
        clean_name = "Dead Weight" if "Dead Weight" in cluster_name else ("Untested" if "Untested" in cluster_name else cluster_name)
        self._display_tracks(records, f"Cluster: {clean_name}")

    def _on_matrix_cell_clicked(self, exposure: str, pick_rate: str, track_ids: List[str]) -> None:
        self._set_active_kpi_card(None)
        if not self.snapshot:
            return
        records = [self.snapshot.tracks_by_id[cid].to_dict() for cid in track_ids if cid in self.snapshot.tracks_by_id]
        is_death = (pick_rate == "0% (Never played)") and (exposure in (">10 Sets (Veteran)", "6-10 Sets (Advanced)"))
        hazard_prefix = "[DEAD WEIGHT] " if is_death else ""
        self._display_tracks(records, f"{hazard_prefix}Matrix: {exposure} / Pick Rate {pick_rate}")

    def _on_camelot_slice_clicked(self, camelot_code: str, track_ids: List[str]) -> None:
        self._set_active_kpi_card(None)
        if not self.snapshot:
            return
        records = [self.snapshot.tracks_by_id[cid].to_dict() for cid in track_ids if cid in self.snapshot.tracks_by_id]
        trad = self.snapshot.camelot_slices.get(camelot_code)
        trad_str = f" ({trad.traditional})" if trad and trad.traditional else ""
        self._display_tracks(records, f"Camelot Key: {camelot_code}{trad_str}")

    def _on_turnover_session_clicked(self, session_id: str, category_name: str, track_ids: List[str]) -> None:
        self._set_active_kpi_card(None)
        if not self.snapshot:
            return
        records = [self.snapshot.tracks_by_id[cid].to_dict() for cid in track_ids if cid in self.snapshot.tracks_by_id]
        self._display_tracks(records, f"Set #{session_id}: {category_name}")

    def _on_survival_step_clicked(self, set_number: int, unplayed_pct: float) -> None:
        self._set_active_kpi_card(None)
        if not self.snapshot:
            return
        # Tracks exposed to set_number with 0 plays up to set_number
        unplayed = [
            t.to_dict() for t in self.snapshot.tracks_by_id.values()
            if t.exposure_gigs >= set_number and (t.adoption_latency is None or t.adoption_latency >= set_number)
        ]
        self._display_tracks(unplayed, f"Zero-play tracks after {set_number} sets ({unplayed_pct}%)")

    def _on_transition_edge_clicked(self, src_id: str, tgt_id: str, count: int) -> None:
        self._set_active_kpi_card(None)
        if not self.snapshot:
            return
        records = []
        for cid in (src_id, tgt_id):
            if cid in self.snapshot.tracks_by_id:
                records.append(self.snapshot.tracks_by_id[cid].to_dict())
        self._display_tracks(records, f"Automatic Combo: {count} consecutive transitions")

    def _on_transition_node_clicked(self, track_id: str) -> None:
        self._set_active_kpi_card(None)
        if not self.snapshot:
            return
        if track_id in self.snapshot.tracks_by_id:
            t_obj = self.snapshot.tracks_by_id[track_id]
            self._display_tracks([t_obj.to_dict()], f"Track: {t_obj.title} ({t_obj.artist})")

    def _display_tracks(self, records: List[Dict[str, Any]], title_desc: str) -> None:
        self.current_filtered_tracks = records
        self._active_filter_label = title_desc
        self.lbl_table_title.setText(title_desc)
        self.lbl_table_count.setText(f"({len(records)} tracks)")
        self.search_input.clear()
        self.table.set_tracks(records)

    def _apply_table_search(self, query: str) -> None:
        if not query.strip():
            self.table.set_tracks(self.current_filtered_tracks)
            self.lbl_table_count.setText(f"({len(self.current_filtered_tracks)} tracks)")
            return

        q = query.strip().lower()
        filtered = [
            r for r in self.current_filtered_tracks
            if q in (r.get("title") or "").lower()
            or q in (r.get("artist") or "").lower()
            or q in (r.get("key") or "").lower()
            or q in (r.get("cluster") or "").lower()
        ]
        self.table.set_tracks(filtered)
        self.lbl_table_count.setText(f"({len(filtered)} of {len(self.current_filtered_tracks)} tracks)")

    def _on_track_detail_requested(self, track_record: Dict[str, Any]) -> None:
        # Open detailed track inspector if needed
        pass

    # --------------------------------------------------------------------------
    # Export Handlers
    # --------------------------------------------------------------------------

    def _export_m3u8(self) -> None:
        if not self.current_filtered_tracks:
            QMessageBox.information(self, "Export Prune Crate", "No tracks currently selected to export.")
            return

        default_name = f"Prune_Crate_{datetime.now().strftime('%Y%m%d')}.m3u8"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Prune Crate (M3U8)",
            default_name,
            "M3U8 Playlist (*.m3u8);;M3U Playlist (*.m3u)",
        )
        if not file_path:
            return

        try:
            cnt = export_tracks_to_m3u8(file_path, self.current_filtered_tracks)
            QMessageBox.information(
                self,
                "Export Complete",
                f"Successfully exported {cnt} tracks to file:\n{file_path}\n\n"
                "You can drag this file into Rekordbox or onto a USB drive to review and prune dead weight tracks.",
            )
            self.notify_status.emit(f"Exported {cnt} tracks to {os.path.basename(file_path)}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Unable to export M3U8 file:\n{e}")

    def _export_xml(self) -> None:
        if not self.current_filtered_tracks:
            QMessageBox.information(self, "Export Prune Crate", "No tracks currently selected to export.")
            return

        default_name = f"Prune_Crate_{datetime.now().strftime('%Y%m%d')}.xml"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Prune Crate (Rekordbox XML)",
            default_name,
            "Rekordbox XML (*.xml)",
        )
        if not file_path:
            return

        try:
            cnt = export_tracks_to_rekordbox_xml(file_path, "Prune Crate", self.current_filtered_tracks)
            QMessageBox.information(
                self,
                "Export Complete",
                f"Exported {cnt} tracks to Rekordbox XML file:\n{file_path}",
            )
            self.notify_status.emit(f"Exported {cnt} tracks to {os.path.basename(file_path)}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Unable to export XML file:\n{e}")

    def _create_rekordbox_playlist(self) -> None:
        if not self.engine:
            return
        if not self.current_filtered_tracks:
            QMessageBox.information(self, "Create Playlist", "No tracks currently selected to add to playlist.")
            return

        if is_rekordbox_running():
            QMessageBox.warning(
                self,
                "Rekordbox Running",
                "Rekordbox is currently running.\n"
                "To create the playlist safely and avoid database conflicts, please quit Rekordbox (Cmd+Q) and try again.",
            )
            return

        default_name = f"Prune Crate ({len(self.current_filtered_tracks)})"
        track_ids = [str(t["id"]) for t in self.current_filtered_tracks if t.get("id")]

        reply = QMessageBox.question(
            self,
            "Create Playlist in Rekordbox",
            f"Do you want to create playlist '{default_name}' directly in Rekordbox with {len(track_ids)} tracks?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            res = create_purge_playlist_in_rekordbox(self.engine, default_name, track_ids)
            QMessageBox.information(
                self,
                "Playlist Created",
                f"Playlist '{default_name}' with {len(track_ids)} tracks was successfully created in Rekordbox!",
            )
            self.db_saved.emit()
            self.notify_status.emit(f"Created playlist '{default_name}' in Rekordbox.")
        except Exception as e:
            QMessageBox.critical(self, "Playlist Creation Error", f"Unable to create playlist in Rekordbox:\n{e}")

