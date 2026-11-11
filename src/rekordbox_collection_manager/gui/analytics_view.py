"""Main Analytics View for Rekordbox Collection Manager.

Integrates the KPI engine, 5 specialized interactive visual analytics views,
and a linked track table with Crate Pruning export capabilities (M3U8, XML, Rekordbox Playlist).
"""

import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional
from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
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
        if tooltip:
            self.setToolTip(tooltip)

        self.setStyleSheet(f"""
            QFrame#kpiCard {{
                background-color: {COLOR_BG_CARD};
                border: 1px solid {COLOR_BORDER};
                border-radius: 8px;
                padding: 10px 14px;
            }}
            QFrame#kpiCard:hover {{
                border-color: {accent_color};
                background-color: #262632;
            }}
        """)

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
            self.lbl_sub.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-size: 10px;")
            layout.addWidget(self.lbl_sub)

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
        self._active_filter_label: str = "Tutte le tracce"

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

        title_lbl = QLabel("📊 Collection Analytics & Set Flow Insights")
        title_lbl.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-size: 18px; font-weight: 700; letter-spacing: -0.3px;")
        title_col.addWidget(title_lbl)

        sub_lbl = QLabel("Analisi deterministica ad eventi: Crate Pruning, Distribuzione Armonica, DNA delle Serate e Sequenze")
        sub_lbl.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px;")
        title_col.addWidget(sub_lbl)
        header_row.addLayout(title_col, stretch=1)

        self.btn_refresh = QPushButton("🔄 Ricalcola Metriche")
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
            "Dead Weight", "0", COLOR_RED, "Zavorra candidata all'eliminazione",
            tooltip="Tracce mai suonate dopo 5+ set o non suonate da 6+ set consecutivi",
        )
        self.card_dead_weight.clicked.connect(lambda: self._filter_by_cluster("Dead Weight (Zavorra)"))
        self.cards_row.addWidget(self.card_dead_weight)

        self.card_peak = KPICard(
            "Peak Bangers", "0", COLOR_GREEN, "Cardini stabili dei set (Pick >= 60%)",
            tooltip="Tracce cardine ad altissima frequenza di selezione",
        )
        self.card_peak.clicked.connect(lambda: self._filter_by_cluster("Peak Bangers"))
        self.cards_row.addWidget(self.card_peak)

        self.card_core = KPICard(
            "Core Rotation", "0", COLOR_BLUE, "Repertorio affidabile (25-60%)",
            tooltip="Tracce stabili in rotazione costante",
        )
        self.card_core.clicked.connect(lambda: self._filter_by_cluster("Core Rotation"))
        self.cards_row.addWidget(self.card_core)

        self.card_untested = KPICard(
            "Untested", "0", COLOR_AMBER, "Novità in attesa di test (protette)",
            tooltip="Nuovi acquisti esposti a <= 2 serate: rigorosamente da proteggere",
        )
        self.card_untested.clicked.connect(lambda: self._filter_by_cluster("In Incubazione / Untested"))
        self.cards_row.addWidget(self.card_untested)

        self.card_burnout = KPICard(
            "Burnout", "0", "#f97316", "Tracce sature (non suonate da 8+ set)",
            tooltip="Tracce con alto play count storico ma trascurate negli ultimi set",
        )
        self.card_burnout.clicked.connect(lambda: self._filter_by_cluster("Burnout Tracks"))
        self.cards_row.addWidget(self.card_burnout)

        self.card_sessions = KPICard(
            "Live Sets", "0", "#a855f7", "Sessioni valide analizzate",
            tooltip="History con durata >= 20 min e >= 5 tracce",
        )
        self.cards_row.addWidget(self.card_sessions)

        main_layout.addLayout(self.cards_row)

        # ----------------------------------------------------------------------
        # 3. Main Splitter (Top: Charts & Analytics Vistas, Bottom: Tracks Table)
        # ----------------------------------------------------------------------
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(False)

        # Top Container: Tab Buttons + Stack of 5 Visualizations
        top_container = QWidget()
        top_layout = QVBoxLayout(top_container)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)

        # Tab Selector Bar
        tab_bar = QHBoxLayout()
        tab_bar.setSpacing(6)

        self.tab_group = QButtonGroup(self)
        self.tab_group.setExclusive(True)

        self._tabs_metadata = [
            {
                "id": 0,
                "label": "📦 Crate Health Matrix",
                "icon": "📦",
                "short_desc": "Matrice di Salute: incrocia l'esposizione alle serate col Pick Rate per isolare le zavorre (Death Zone) dai pezzi cardine.",
                "rich_tooltip": (
                    "<div style='max-width: 420px; font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, sans-serif; line-height: 1.4;'>"
                    "<b style='font-size: 13px; color: #38bdf8;'>📦 Crate Health Matrix (Matrice di Salute & Spurgo)</b><br/><br/>"
                    "<b>Cosa stai guardando:</b><br/>"
                    "Distribuzione di tutti i brani della libreria incrociando due dimensioni chiave:<br/>"
                    "• <b>Asse X (Esposizione):</b> A quante date/serate live il brano era presente in collezione.<br/>"
                    "• <b>Asse Y (Pick Rate %):</b> In quale percentuale di quelle serate lo hai effettivamente suonato.<br/><br/>"
                    "<b>Come interpretare le zone:</b><br/>"
                    "• <b style='color: #ef4444;'>💀 Death Zone (in basso a destra):</b> Brani portati a 5+ serate ma mai suonati, o ignorati da oltre 6 serate consecutive. Sono le vere <i>zavorre</i> da eliminare.<br/>"
                    "• <b style='color: #10b981;'>⭐ Top Performers (in alto a destra):</b> Cavalli di battaglia ad altissima rotazione e affidabilità.<br/>"
                    "• <b style='color: #f59e0b;'>🌱 In Incubazione (in basso a sinistra):</b> Nuovi acquisti esposti a &le; 2 serate, protetti e da testare.<br/><br/>"
                    "<i style='color: #94a3b8;'>💡 Clicca su una cella per caricare i brani ed esportare la Crate di Spurgo (M3U8, XML o Playlist Rekordbox).</i>"
                    "</div>"
                ),
            },
            {
                "id": 1,
                "label": "🎡 Camelot Wheel",
                "icon": "🎡",
                "short_desc": "Ruota Armonica: confronta la densità di tracce in archivio con l'effettivo spazio concesso nei tuoi live set.",
                "rich_tooltip": (
                    "<div style='max-width: 420px; font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, sans-serif; line-height: 1.4;'>"
                    "<b style='font-size: 13px; color: #38bdf8;'>🎡 Camelot Wheel (Bilanciamento Armonico)</b><br/><br/>"
                    "<b>Cosa stai guardando:</b><br/>"
                    "Ruota armonica polare a 24 spicchi: anello interno per le tonalità Minori (1A–12A), anello esterno per le Maggiori (1B–12B).<br/><br/>"
                    "<b>Come interpretare i dati:</b><br/>"
                    "• <b>Colori Termici:</b> Densità dei brani posseduti (dal blu scuro = poche tracce, all'oro fino al rosso corallo = le tonalità più ricche).<br/>"
                    "• <b>Numerino centrale:</b> Conteggio esatto dei brani presenti in collezione per quella tonalità.<br/>"
                    "• <b>Bordo azzurro esterno:</b> Quota reale di brani suonati nei tuoi live set in quella chiave.<br/>"
                    "• <b>Hub centrale:</b> Statistiche sul bilanciamento armonico globale e tonalità dominante.<br/><br/>"
                    "<i style='color: #94a3b8;'>💡 Clicca su uno spicchio per filtrare ed esaminare tutte le tracce di quella tonalità.</i>"
                    "</div>"
                ),
            },
            {
                "id": 2,
                "label": "🧬 Set Turnover & DNA",
                "icon": "🧬",
                "short_desc": "Turnover & DNA: ricambio di scaletta, freschezza e classificazione automatica degli archetipi di serata.",
                "rich_tooltip": (
                    "<div style='max-width: 420px; font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, sans-serif; line-height: 1.4;'>"
                    "<b style='font-size: 13px; color: #38bdf8;'>🧬 Set Turnover & DNA (Carta d'Identità dei Set)</b><br/><br/>"
                    "<b>Cosa stai guardando:</b><br/>"
                    "Grafico a barre cumulative al 100% che analizza la scaletta di ogni tua serata in ordine cronologico da sinistra a destra.<br/><br/>"
                    "<b>Come interpretare i dati:</b><br/>"
                    "• <b>🏷️ Archetipi Automatici (Badge sopra ogni colonna):</b> Classificazione istantanea del profilo del set (es. 🚀 Prime-Time, 🧪 Testing Lab, 🔍 Digger, 🔁 Fotocopia, ⚖️ Equilibrato).<br/>"
                    "• <b>3 Lenti di analisi:</b><br/>"
                    "  1. <i>🧬 Cluster di Performance:</i> Scompone il set tra Hit, Rotazione, Novità assolute, Deep Cuts e brani sovraesposti a quel tempo.<br/>"
                    "  2. <i>⏳ Turnover Temporale:</i> Misura l'ingresso di brani mai sentiti prima (New Entries), continuità recente e ripescaggi.<br/>"
                    "  3. <i>🔁 Riciclo dal Set Precedente:</i> Quante tracce hai replicato identiche dal set subito prima.<br/><br/>"
                    "<i style='color: #94a3b8;'>💡 Clicca su una barra o su un segmento per isolare esattamente quei brani nella tabella.</i>"
                    "</div>"
                ),
            },
            {
                "id": 3,
                "label": "📉 Survival Decay",
                "icon": "📉",
                "short_desc": "Curva di Decadimento: evidenzia la soglia di non ritorno oltre la quale i brani non suonati finiscono dimenticati.",
                "rich_tooltip": (
                    "<div style='max-width: 420px; font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, sans-serif; line-height: 1.4;'>"
                    "<b style='font-size: 13px; color: #38bdf8;'>📉 Survival Decay (Curva di Decadimento Kaplan-Meier)</b><br/><br/>"
                    "<b>Cosa stai guardando:</b><br/>"
                    "Traccia cosa succede ai brani acquistati man mano che passano le serate live: quale percentuale rimane ancora a ZERO play dopo 1, 2, 3, 5, 10 set dalla data di aggiunta.<br/><br/>"
                    "<b>Come interpretare la curva:</b><br/>"
                    "• <b>Discesa a gradini:</b> Parte dal 100% (tutti i nuovi acquisti partono a zero play) e scende man mano che i brani vengono 'battezzati' nei tuoi set.<br/>"
                    "• <b style='color: #f59e0b;'>⚠️ Soglia di Non Ritorno (Linea tratteggiata ambra):</b> È il punto matematico in cui la curva si appiattisce. Se un brano non viene suonato entro le prime 4 serate, la probabilità che venga mai suonato in futuro crolla vicino allo zero.<br/>"
                    "• <b>A cosa serve:</b> Identificare gli acquisti d'impulso finiti nel dimenticatoio prima che diventino zavorre intoccabili.<br/><br/>"
                    "<i style='color: #94a3b8;'>💡 Clicca su un gradino per visualizzare le tracce ancora a zero play a quel set.</i>"
                    "</div>"
                ),
            },
            {
                "id": 4,
                "label": "🕸️ Transitions & Combos",
                "icon": "🕸️",
                "short_desc": "Grafo delle Transizioni: sequenze suonate consecutivamente (A ➔ B) in almeno 2 serate distinte, rivelando le tue abitudini di mixaggio e combo fisse.",
                "rich_tooltip": (
                    "<div style='max-width: 440px; font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, sans-serif; line-height: 1.45;'>"
                    "<b style='font-size: 13px; color: #38bdf8;'>🕸️ Transitions & Combos (Grafo delle Sequenze Abituali)</b><br/><br/>"
                    "<b>Cosa stai guardando:</b><br/>"
                    "Mappa orientata delle transizioni e delle 'combo automatiche' tra brani emerse dalle tue serate storiche.<br/><br/>"
                    "<b>Criteri per cui una Combo compare nel grafo:</b><br/>"
                    "• <b>1. Successione Immediata (A ➔ B):</b> La traccia B deve essere stata suonata <i>subito dopo</i> la traccia A nella scaletta (transizione diretta, TrackNo consecutivo).<br/>"
                    "• <b>2. Soglia di Frequenza (&ge; 2 Serate distinte):</b> La stessa identica sequenza deve ripetersi in almeno <b>2 serate live diverse</b>. I passaggi estemporanei eseguiti una sola volta per caso vengono esclusi.<br/>"
                    "• <b>3. Sessioni Valide:</b> Vengono analizzate solo le playlist History con durata effettiva &ge; 20 minuti e almeno 5 tracce.<br/><br/>"
                    "<b>Come interpretare la vista:</b><br/>"
                    "• <b>I Nodi (Cerchi):</b> Singole tracce (il colore indica l'anello Camelot: azzurro = Minore, verde = Maggiore). Il testo interno mostra la chiave armonica (es. 8A).<br/>"
                    "• <b>Le Frecce &amp; Badge numerici:</b> Direzione del passaggio (da traccia entrante a uscente) con il numero esatto di volte in cui è stato replicato.<br/>"
                    "• <b>A cosa serve:</b> Identificare i tuoi automatismi e le 'safe zone' di mixaggio per decidere se consolidarle come punti di forza o diversificare le scalette.<br/><br/>"
                    "<i style='color: #94a3b8;'>💡 Clicca su un arco o nodo per caricare i dettagli e le date in tabella. Usa la rotella per lo zoom e trascina lo sfondo o i nodi.</i>"
                    "</div>"
                ),
            },
        ]

        self.tab_buttons: List[QPushButton] = []
        for meta in self._tabs_metadata:
            btn = QPushButton(meta["label"])
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
        top_layout.addLayout(tab_bar)

        # Section Info Banner with interactive tooltip and description
        self.section_info_bar = QWidget()
        self.section_info_bar.setStyleSheet(f"""
            QWidget {{
                background-color: {COLOR_BG_CARD};
                border: 1px solid {COLOR_BORDER};
                border-radius: 6px;
            }}
        """)
        sec_layout = QHBoxLayout(self.section_info_bar)
        sec_layout.setContentsMargins(10, 5, 10, 5)
        sec_layout.setSpacing(8)

        self.lbl_sec_icon = QLabel("📦")
        self.lbl_sec_icon.setStyleSheet("font-size: 13px;")
        sec_layout.addWidget(self.lbl_sec_icon)

        self.lbl_sec_desc = QLabel("")
        self.lbl_sec_desc.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-size: 11px;")
        sec_layout.addWidget(self.lbl_sec_desc, stretch=1)

        self.lbl_sec_help = QLabel("ℹ️ Come interpretare")
        self.lbl_sec_help.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lbl_sec_help.setStyleSheet(f"""
            QLabel {{
                color: {COLOR_BLUE};
                font-size: 11px;
                font-weight: 600;
                padding: 2px 7px;
                background-color: #0d2b45;
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

        top_layout.addWidget(self.section_info_bar)
        self._update_section_info(0)

        # Stacked Widget for the 5 Vistas
        self.chart_stack = QStackedWidget()

        # Vista 1: Crate Health Matrix Container
        v1_container = QWidget()
        v1_layout = QVBoxLayout(v1_container)
        v1_layout.setContentsMargins(0, 0, 0, 0)
        v1_layout.setSpacing(6)

        # Cluster filter chips above matrix
        cluster_chips_layout = QHBoxLayout()
        cluster_chips_layout.setSpacing(6)
        cluster_chips_layout.addWidget(QLabel("Filtra Cluster:"))
        for c_name, c_col in [
            ("Tutti", COLOR_TEXT_SECONDARY),
            ("Dead Weight (Zavorra)", COLOR_RED),
            ("Peak Bangers", COLOR_GREEN),
            ("Core Rotation", COLOR_BLUE),
            ("In Incubazione / Untested", COLOR_AMBER),
            ("Burnout Tracks", "#f97316"),
        ]:
            c_btn = QPushButton(c_name)
            c_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            c_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR_BG_PANEL};
                    border: 1px solid {COLOR_BORDER};
                    border-radius: 4px;
                    color: {c_col};
                    padding: 3px 10px;
                    font-size: 11px;
                    font-weight: 600;
                }}
                QPushButton:hover {{
                    background-color: {COLOR_BG_CARD};
                    border-color: {c_col};
                }}
            """)
            c_btn.clicked.connect(lambda checked=False, cn=c_name: self._filter_by_cluster(cn))
            cluster_chips_layout.addWidget(c_btn)
        cluster_chips_layout.addStretch(1)
        v1_layout.addLayout(cluster_chips_layout)

        self.widget_health_matrix = CrateHealthMatrixWidget(self)
        self.widget_health_matrix.cell_clicked.connect(self._on_matrix_cell_clicked)
        v1_layout.addWidget(self.widget_health_matrix)
        self.chart_stack.addWidget(v1_container)

        # Vista 2: Camelot Wheel
        self.widget_camelot = CamelotWheelWidget(self)
        self.widget_camelot.slice_clicked.connect(self._on_camelot_slice_clicked)
        self.chart_stack.addWidget(self.widget_camelot)

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

        top_layout.addWidget(self.chart_stack)
        self.splitter.addWidget(top_container)

        # ----------------------------------------------------------------------
        # 4. Bottom Container: Interactive Track Table with Crate Export
        # ----------------------------------------------------------------------
        bottom_container = QWidget()
        bottom_layout = QVBoxLayout(bottom_container)
        bottom_layout.setContentsMargins(0, 4, 0, 0)
        bottom_layout.setSpacing(6)

        # Table Control Bar
        table_bar = QHBoxLayout()
        table_bar.setSpacing(10)

        self.lbl_table_title = QLabel("Tutte le tracce")
        self.lbl_table_title.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-size: 13px; font-weight: 700;")
        table_bar.addWidget(self.lbl_table_title)

        self.lbl_table_count = QLabel("(0 brani)")
        self.lbl_table_count.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px;")
        table_bar.addWidget(self.lbl_table_count)

        table_bar.addStretch(1)

        # Search filter
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Filtra brani visualizzati...")
        self.search_input.setFixedWidth(210)
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
        self.btn_export_menu = QPushButton("📤 Esporta Crate di Spurgo ▼")
        self.btn_export_menu.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_export_menu.setStyleSheet(f"""
            QPushButton {{
                background-color: #381212;
                border: 1px solid {COLOR_RED};
                border-radius: 6px;
                color: #ff9999;
                padding: 5px 12px;
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

        bottom_layout.addLayout(table_bar)

        # Shared TracksTableWidget (client-side fast sorting)
        table_config = TrackTableConfig(
            columns=list(DEFAULT_COLLECTION_COLUMNS),
            index_mode="row_number",
            index_header_tooltip="Posizione",
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
        bottom_layout.addWidget(self.table, stretch=1)

        self.splitter.addWidget(bottom_container)
        self.splitter.setSizes([380, 280])

        main_layout.addWidget(self.splitter, stretch=1)

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

        act_m3u = QAction("📄 Esporta Playlist M3U8...", self)
        act_m3u.triggered.connect(self._export_m3u8)
        menu.addAction(act_m3u)

        act_xml = QAction("📑 Esporta XML Rekordbox...", self)
        act_xml.triggered.connect(self._export_xml)
        menu.addAction(act_xml)

        menu.addSeparator()

        act_rekordbox = QAction("➕ Crea Playlist 'Crate di Spurgo' in Rekordbox", self)
        act_rekordbox.triggered.connect(self._create_rekordbox_playlist)
        menu.addAction(act_rekordbox)

        self.btn_export_menu.setMenu(menu)

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
        self.btn_refresh.setText("⏳ Calcolo in corso...")
        self.notify_status.emit("Calcolo analytics e metriche avanzate...")

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
        self.btn_refresh.setText("🔄 Ricalcola Metriche")

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

        # Default filter: if dead weight tracks exist, display them to facilitate crate pruning
        if snapshot.dead_weight_count > 0:
            self._filter_by_cluster("Dead Weight (Zavorra)")
        else:
            all_records = [t.to_dict() for t in snapshot.tracks_by_id.values()]
            self._display_tracks(all_records, "Tutte le tracce a catalogo")

        self.notify_status.emit(
            f"Analytics aggiornate: {snapshot.total_valid_sessions} set analizzati, "
            f"{snapshot.dead_weight_count} tracce zavorra identificate."
        )

    def _on_analytics_error(self, err_msg: str) -> None:
        self.btn_refresh.setEnabled(True)
        self.btn_refresh.setText("🔄 Ricalcola Metriche")
        self.notify_status.emit(f"Errore calcolo analytics: {err_msg}")
        if self._interactive_refresh:
            QMessageBox.warning(self, "Errore Analytics", f"Impossibile calcolare le analytics:\n{err_msg}")

    def _on_tab_switched(self, tab_id: int) -> None:
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

    def _filter_by_cluster(self, cluster_name: str) -> None:
        if not self.snapshot:
            return
        if cluster_name == "Tutti":
            records = [t.to_dict() for t in self.snapshot.tracks_by_id.values()]
            self._display_tracks(records, "Tutte le tracce")
            return

        c_ids = self.snapshot.cluster_track_ids.get(cluster_name, [])
        records = [self.snapshot.tracks_by_id[cid].to_dict() for cid in c_ids if cid in self.snapshot.tracks_by_id]
        self._display_tracks(records, f"Cluster: {cluster_name}")

    def _on_matrix_cell_clicked(self, exposure: str, pick_rate: str, track_ids: List[str]) -> None:
        if not self.snapshot:
            return
        records = [self.snapshot.tracks_by_id[cid].to_dict() for cid in track_ids if cid in self.snapshot.tracks_by_id]
        is_death = (pick_rate == "0% (Mai suonata)") and (exposure in (">10 Set (Veterano)", "6-10 Set (Avanzato)"))
        hazard_prefix = "💀 [ZAVORRA] " if is_death else ""
        self._display_tracks(records, f"{hazard_prefix}Matrice: {exposure} / Pick Rate {pick_rate}")

    def _on_camelot_slice_clicked(self, camelot_code: str, track_ids: List[str]) -> None:
        if not self.snapshot:
            return
        records = [self.snapshot.tracks_by_id[cid].to_dict() for cid in track_ids if cid in self.snapshot.tracks_by_id]
        trad = self.snapshot.camelot_slices.get(camelot_code)
        trad_str = f" ({trad.traditional})" if trad and trad.traditional else ""
        self._display_tracks(records, f"Tonalità Camelot: {camelot_code}{trad_str}")

    def _on_turnover_session_clicked(self, session_id: str, category_name: str, track_ids: List[str]) -> None:
        if not self.snapshot:
            return
        records = [self.snapshot.tracks_by_id[cid].to_dict() for cid in track_ids if cid in self.snapshot.tracks_by_id]
        self._display_tracks(records, f"Set #{session_id}: {category_name}")

    def _on_survival_step_clicked(self, set_number: int, unplayed_pct: float) -> None:
        if not self.snapshot:
            return
        # Tracks exposed to set_number with 0 plays up to set_number
        unplayed = [
            t.to_dict() for t in self.snapshot.tracks_by_id.values()
            if t.exposure_gigs >= set_number and (t.adoption_latency is None or t.adoption_latency >= set_number)
        ]
        self._display_tracks(unplayed, f"Tracce a zero play dopo {set_number} set ({unplayed_pct}%)")

    def _on_transition_edge_clicked(self, src_id: str, tgt_id: str, count: int) -> None:
        if not self.snapshot:
            return
        records = []
        for cid in (src_id, tgt_id):
            if cid in self.snapshot.tracks_by_id:
                records.append(self.snapshot.tracks_by_id[cid].to_dict())
        self._display_tracks(records, f"Combo Automatica: {count} esecuzioni consecutive")

    def _on_transition_node_clicked(self, track_id: str) -> None:
        if not self.snapshot:
            return
        if track_id in self.snapshot.tracks_by_id:
            t_obj = self.snapshot.tracks_by_id[track_id]
            self._display_tracks([t_obj.to_dict()], f"Traccia: {t_obj.title} ({t_obj.artist})")

    def _display_tracks(self, records: List[Dict[str, Any]], title_desc: str) -> None:
        self.current_filtered_tracks = records
        self._active_filter_label = title_desc
        self.lbl_table_title.setText(title_desc)
        self.lbl_table_count.setText(f"({len(records)} brani)")
        self.search_input.clear()
        self.table.set_tracks(records)

    def _apply_table_search(self, query: str) -> None:
        if not query.strip():
            self.table.set_tracks(self.current_filtered_tracks)
            self.lbl_table_count.setText(f"({len(self.current_filtered_tracks)} brani)")
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
        self.lbl_table_count.setText(f"({len(filtered)} di {len(self.current_filtered_tracks)} brani)")

    def _on_track_detail_requested(self, track_record: Dict[str, Any]) -> None:
        # Open detailed track inspector if needed
        pass

    # --------------------------------------------------------------------------
    # Export Handlers
    # --------------------------------------------------------------------------

    def _export_m3u8(self) -> None:
        if not self.current_filtered_tracks:
            QMessageBox.information(self, "Esporta Crate", "Nessuna traccia attualmente selezionata da esportare.")
            return

        default_name = f"Crate_Spurgo_{datetime.now().strftime('%Y%m%d')}.m3u8"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Salva Crate di Spurgo (M3U8)",
            default_name,
            "M3U8 Playlist (*.m3u8);;M3U Playlist (*.m3u)",
        )
        if not file_path:
            return

        try:
            cnt = export_tracks_to_m3u8(file_path, self.current_filtered_tracks)
            QMessageBox.information(
                self,
                "Esportazione Completata",
                f"Esportati {cnt} brani con successo nel file:\n{file_path}\n\n"
                "Puoi trascinare questo file nella tua chiavetta USB o in Rekordbox per rimuovere le tracce zavorra.",
            )
            self.notify_status.emit(f"Esportate {cnt} tracce in {os.path.basename(file_path)}")
        except Exception as e:
            QMessageBox.critical(self, "Errore Esportazione", f"Impossibile esportare il file M3U8:\n{e}")

    def _export_xml(self) -> None:
        if not self.current_filtered_tracks:
            QMessageBox.information(self, "Esporta Crate", "Nessuna traccia attualmente selezionata da esportare.")
            return

        default_name = f"Crate_Spurgo_{datetime.now().strftime('%Y%m%d')}.xml"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Salva Crate di Spurgo (Rekordbox XML)",
            default_name,
            "Rekordbox XML (*.xml)",
        )
        if not file_path:
            return

        try:
            cnt = export_tracks_to_rekordbox_xml(file_path, "Crate di Spurgo", self.current_filtered_tracks)
            QMessageBox.information(
                self,
                "Esportazione Completata",
                f"Esportati {cnt} brani nel file Rekordbox XML:\n{file_path}",
            )
            self.notify_status.emit(f"Esportate {cnt} tracce in {os.path.basename(file_path)}")
        except Exception as e:
            QMessageBox.critical(self, "Errore Esportazione", f"Impossibile esportare il file XML:\n{e}")

    def _create_rekordbox_playlist(self) -> None:
        if not self.engine:
            return
        if not self.current_filtered_tracks:
            QMessageBox.information(self, "Crea Playlist", "Nessuna traccia attualmente selezionata da aggiungere alla playlist.")
            return

        if is_rekordbox_running():
            QMessageBox.warning(
                self,
                "Rekordbox Aperto",
                "Rekordbox è attualmente in esecuzione.\n"
                "Per creare la playlist in sicurezza ed evitare conflitti, chiudi Rekordbox (Cmd+Q) e riprova.",
            )
            return

        default_name = f"Crate di Spurgo ({len(self.current_filtered_tracks)})"
        track_ids = [str(t["id"]) for t in self.current_filtered_tracks if t.get("id")]

        reply = QMessageBox.question(
            self,
            "Crea Playlist in Rekordbox",
            f"Desideri creare la playlist '{default_name}' direttamente in Rekordbox con {len(track_ids)} tracce?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            res = create_purge_playlist_in_rekordbox(self.engine, default_name, track_ids)
            QMessageBox.information(
                self,
                "Playlist Creata",
                f"La playlist '{default_name}' con {len(track_ids)} brani è stata creata con successo in Rekordbox!",
            )
            self.db_saved.emit()
            self.notify_status.emit(f"Creata playlist '{default_name}' in Rekordbox.")
        except Exception as e:
            QMessageBox.critical(self, "Errore Creazione Playlist", f"Impossibile creare la playlist in Rekordbox:\n{e}")

