"""Set Turnover & DNA Widget - Interactive Multi-Dimensional Stacked Bar Chart.

Allows analyzing the composition of chronological live sets through 3 unified dimensions:
1. 🧬 Performance Clusters (Peak Bangers, Core Rotation, Untested, Deep Cuts, Burnout)
2. ⏳ Temporal Turnover (New Entries, In Rotation, Historical Deep Cuts, One-Shot)
3. 🔁 Direct Retention (Fresh vs Repeated from immediately preceding set)
"""

from typing import Any, Dict, List, Optional, Tuple
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from rekordbox_collection_manager.analytics import SessionTurnoverData
from rekordbox_collection_manager.gui.theme import (
    COLOR_AMBER,
    COLOR_BG_CARD,
    COLOR_BG_DARK,
    COLOR_BG_PANEL,
    COLOR_BLUE,
    COLOR_BORDER,
    COLOR_GREEN,
    COLOR_RED,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_SECONDARY,
)

# Visual Colors for Segments
COLOR_PEAK = "#10b981"         # Emerald
COLOR_CORE = "#00a2ff"         # Electric Blue
COLOR_UNTESTED = "#f59e0b"     # Amber
COLOR_DEEP_CUTS = "#8b5cf6"    # Purple / Indigo
COLOR_BURNOUT = "#ef4444"      # Coral / Red
COLOR_STANDARD = "#64748b"     # Slate
COLOR_ONE_SHOT = "#64748b"     # Slate


class _TurnoverCanvas(QWidget):
    """Internal canvas widget rendering the 100% stacked bar chart."""

    session_clicked = Signal(str, str, list)  # session_id, category_name, track_ids

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumSize(480, 240)

        self.series: List[SessionTurnoverData] = []
        self.active_mode: str = "cluster"  # "cluster", "turnover", "retention"

        self.hovered_session_idx: Optional[int] = None
        self.hovered_segment: Optional[str] = None
        self.selected_session_idx: Optional[int] = None

    def set_data(self, series: List[SessionTurnoverData]) -> None:
        self.series = series or []
        self.update()

    def set_mode(self, mode: str) -> None:
        if mode in ("cluster", "turnover", "retention"):
            self.active_mode = mode
            self.hovered_segment = None
            self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()

        top_margin = 52
        bottom_margin = 46
        left_margin = 45
        right_margin = 20

        grid_w = max(10, w - left_margin - right_margin)
        grid_h = max(10, h - top_margin - bottom_margin)

        # Background
        painter.fillRect(0, 0, w, h, QColor(COLOR_BG_DARK))

        # 1. Draw Active Legend at Top
        self._draw_legend(painter, left_margin, 10, grid_w)

        # 2. Draw Y-Axis Guideline Ticks (0%, 25%, 50%, 75%, 100%)
        font_axis = QFont()
        font_axis.setPointSize(8)
        painter.setFont(font_axis)
        painter.setPen(QColor(COLOR_TEXT_MUTED))

        for pct in (0, 25, 50, 75, 100):
            y_pos = top_margin + grid_h - (pct / 100.0) * grid_h
            painter.drawText(
                QRectF(0, y_pos - 7, left_margin - 8, 14),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"{pct}%",
            )
            # Grid guideline
            painter.setPen(QPen(QColor(COLOR_BORDER), 1, Qt.PenStyle.DotLine))
            painter.drawLine(int(left_margin), int(y_pos), int(left_margin + grid_w), int(y_pos))
            painter.setPen(QColor(COLOR_TEXT_MUTED))

        n_sessions = len(self.series)
        if n_sessions == 0:
            painter.setPen(QColor(COLOR_TEXT_MUTED))
            font_empty = QFont()
            font_empty.setPointSize(11)
            painter.setFont(font_empty)
            painter.drawText(
                QRectF(left_margin, top_margin, grid_w, grid_h),
                Qt.AlignmentFlag.AlignCenter,
                "Nessuna sessione valida registrata per l'analisi del turnover.",
            )
            return

        # Bar widths
        col_w = max(12.0, min(50.0, (grid_w / float(n_sessions)) * 0.78))
        step_w = grid_w / float(n_sessions)

        for idx, s in enumerate(self.series):
            x_center = left_margin + (idx + 0.5) * step_w
            x_left = x_center - (col_w / 2.0)

            is_session_hovered = self.hovered_session_idx == idx
            is_session_selected = self.selected_session_idx == idx

            # Draw Archetype Icon Badge above bar
            badge_w = min(col_w + 6, 26.0)
            badge_rect = QRectF(x_center - (badge_w / 2.0), top_margin - 23, badge_w, 18)
            badge_bg = QColor("#0d2b45") if is_session_hovered else QColor("#1e293b")
            border_col = QColor(COLOR_BLUE if is_session_hovered else COLOR_BORDER)
            painter.setPen(QPen(border_col, 1))
            painter.setBrush(QBrush(badge_bg))
            painter.drawRoundedRect(badge_rect, 4, 4)

            font_icon = QFont()
            font_icon.setPointSize(9)
            painter.setFont(font_icon)
            painter.setPen(QColor(COLOR_TEXT_PRIMARY))
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, s.archetype_icon)

            # Draw Bars according to active_mode
            if self.active_mode == "cluster":
                self._draw_cluster_bars(painter, s, idx, x_left, col_w, top_margin, grid_h, is_session_hovered)
            elif self.active_mode == "turnover":
                self._draw_turnover_bars(painter, s, idx, x_left, col_w, top_margin, grid_h, is_session_hovered)
            else:
                self._draw_retention_bars(painter, s, idx, x_left, col_w, top_margin, grid_h, is_session_hovered)

            # Selection border
            if is_session_selected:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(COLOR_TEXT_PRIMARY), 2))
                painter.drawRoundedRect(QRectF(x_left - 2, top_margin, col_w + 4, grid_h), 4, 4)

            # Draw X-Axis Label below bar
            font_label = QFont()
            font_label.setPointSize(8)
            painter.setFont(font_label)
            painter.setPen(QColor(COLOR_TEXT_SECONDARY))
            label_text = f"S{s.session_index}" if n_sessions > 12 else s.name[:8]
            painter.drawText(
                QRectF(x_center - 25, top_margin + grid_h + 5, 50, 16),
                Qt.AlignmentFlag.AlignCenter,
                label_text,
            )

            # Date subtext
            if n_sessions <= 16:
                painter.setPen(QColor(COLOR_TEXT_MUTED))
                d_short = s.date_str[5:] if len(s.date_str) >= 10 else s.date_str
                painter.drawText(
                    QRectF(x_center - 25, top_margin + grid_h + 20, 50, 14),
                    Qt.AlignmentFlag.AlignCenter,
                    d_short,
                )

    def _draw_cluster_bars(
        self, painter: QPainter, s: SessionTurnoverData, idx: int,
        x_left: float, col_w: float, top_margin: float, grid_h: float, is_hovered: bool
    ) -> None:
        """Render stacked bar for Performance Clusters mode."""
        segments = [
            ("peak", s.peak_bangers_pct, QColor(COLOR_PEAK)),
            ("core", s.core_rotation_pct, QColor(COLOR_CORE)),
            ("untested", s.untested_pct, QColor(COLOR_UNTESTED)),
            ("deep_cluster", s.deep_cuts_cluster_pct, QColor(COLOR_DEEP_CUTS)),
            ("burnout", s.burnout_pct, QColor(COLOR_BURNOUT)),
            ("standard", s.standard_pct, QColor(COLOR_STANDARD)),
        ]
        y_curr = top_margin + grid_h
        for seg_name, pct, col in segments:
            h_seg = (pct / 100.0) * grid_h
            if h_seg > 0:
                y_next = y_curr - h_seg
                rect = QRectF(x_left, y_next, col_w, h_seg)
                is_seg_active = is_hovered and (self.hovered_segment == seg_name)
                draw_col = col.lighter(130) if is_seg_active else col
                painter.fillRect(rect, draw_col)
                y_curr = y_next

    def _draw_turnover_bars(
        self, painter: QPainter, s: SessionTurnoverData, idx: int,
        x_left: float, col_w: float, top_margin: float, grid_h: float, is_hovered: bool
    ) -> None:
        """Render stacked bar for Temporal Turnover mode."""
        segments = [
            ("new", s.new_entries_pct, QColor(COLOR_GREEN)),
            ("rotation", s.in_rotation_pct, QColor(COLOR_BLUE)),
            ("deep", s.deep_cuts_pct, QColor(COLOR_DEEP_CUTS)),
            ("one_shot", s.one_shot_pct, QColor(COLOR_ONE_SHOT)),
        ]
        y_curr = top_margin + grid_h
        for seg_name, pct, col in segments:
            h_seg = (pct / 100.0) * grid_h
            if h_seg > 0:
                y_next = y_curr - h_seg
                rect = QRectF(x_left, y_next, col_w, h_seg)
                is_seg_active = is_hovered and (self.hovered_segment == seg_name)
                draw_col = col.lighter(130) if is_seg_active else col
                painter.fillRect(rect, draw_col)
                y_curr = y_next

    def _draw_retention_bars(
        self, painter: QPainter, s: SessionTurnoverData, idx: int,
        x_left: float, col_w: float, top_margin: float, grid_h: float, is_hovered: bool
    ) -> None:
        """Render stacked bar for Previous Set Retention mode."""
        segments = [
            ("fresh", s.fresh_tracks_pct, QColor(COLOR_GREEN)),
            ("repeated", s.repeated_prev_pct, QColor(COLOR_AMBER)),
        ]
        y_curr = top_margin + grid_h
        for seg_name, pct, col in segments:
            h_seg = (pct / 100.0) * grid_h
            if h_seg > 0:
                y_next = y_curr - h_seg
                rect = QRectF(x_left, y_next, col_w, h_seg)
                is_seg_active = is_hovered and (self.hovered_segment == seg_name)
                draw_col = col.lighter(130) if is_seg_active else col
                painter.fillRect(rect, draw_col)
                y_curr = y_next

    def _draw_legend(self, painter: QPainter, start_x: float, y: float, total_w: float) -> None:
        if self.active_mode == "cluster":
            legend_items = [
                ("⚡ Peak Bangers", COLOR_PEAK),
                ("🔄 Core Rotation", COLOR_CORE),
                ("🌱 Novità / Debutto", COLOR_UNTESTED),
                ("🔍 Deep Cuts", COLOR_DEEP_CUTS),
                ("⏳ Sovraesposte / Burnout", COLOR_BURNOUT),
            ]
        elif self.active_mode == "turnover":
            legend_items = [
                ("🌱 New Entries", COLOR_GREEN),
                ("🔄 In Rotazione (ultime 3)", COLOR_BLUE),
                ("✨ Deep Cuts / Storiche", COLOR_DEEP_CUTS),
                ("🎯 One-Shot", COLOR_ONE_SHOT),
            ]
        else:
            legend_items = [
                ("🆕 Tracce Nuove / Fresche", COLOR_GREEN),
                ("🔁 Ripetute dal Set Precedente", COLOR_AMBER),
            ]

        font_leg = QFont()
        font_leg.setPointSize(8)
        font_leg.setBold(True)
        painter.setFont(font_leg)

        curr_x = start_x
        step_leg = min(150.0, total_w / float(len(legend_items)))
        for label, col_str in legend_items:
            painter.fillRect(int(curr_x), int(y + 2), 10, 10, QColor(col_str))
            painter.setPen(QColor(COLOR_TEXT_SECONDARY))
            painter.drawText(int(curr_x + 14), int(y + 11), label)
            curr_x += step_leg

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        w = self.width()
        h = self.height()

        top_margin = 38
        bottom_margin = 46
        left_margin = 45
        right_margin = 20
        grid_w = max(10, w - left_margin - right_margin)
        grid_h = max(10, h - top_margin - bottom_margin)

        n_sessions = len(self.series)
        if n_sessions == 0:
            return

        step_w = grid_w / float(n_sessions)
        col_w = max(12.0, min(50.0, (grid_w / float(n_sessions)) * 0.78))

        old_idx = self.hovered_session_idx
        old_seg = self.hovered_segment
        self.hovered_session_idx = None
        self.hovered_segment = None

        if left_margin <= pos.x() <= (left_margin + grid_w) and (top_margin - 24) <= pos.y() <= (top_margin + grid_h):
            idx = int((pos.x() - left_margin) // step_w)
            if 0 <= idx < n_sessions:
                s = self.series[idx]
                x_center = left_margin + (idx + 0.5) * step_w
                x_left = x_center - (col_w / 2.0)
                if (x_left - 4) <= pos.x() <= (x_left + col_w + 4):
                    self.hovered_session_idx = idx
                    y_rel = (top_margin + grid_h) - pos.y()
                    pct_pos = (y_rel / grid_h) * 100.0

                    if pct_pos <= 100.0:
                        # Identify hovered segment based on active mode
                        if self.active_mode == "cluster":
                            acc = 0.0
                            if pct_pos <= (acc := acc + s.peak_bangers_pct):
                                self.hovered_segment = "peak"
                            elif pct_pos <= (acc := acc + s.core_rotation_pct):
                                self.hovered_segment = "core"
                            elif pct_pos <= (acc := acc + s.untested_pct):
                                self.hovered_segment = "untested"
                            elif pct_pos <= (acc := acc + s.deep_cuts_cluster_pct):
                                self.hovered_segment = "deep_cluster"
                            elif pct_pos <= (acc := acc + s.burnout_pct):
                                self.hovered_segment = "burnout"
                            else:
                                self.hovered_segment = "standard"
                        elif self.active_mode == "turnover":
                            if pct_pos <= s.new_entries_pct:
                                self.hovered_segment = "new"
                            elif pct_pos <= (s.new_entries_pct + s.in_rotation_pct):
                                self.hovered_segment = "rotation"
                            elif pct_pos <= (s.new_entries_pct + s.in_rotation_pct + s.deep_cuts_pct):
                                self.hovered_segment = "deep"
                            else:
                                self.hovered_segment = "one_shot"
                        else:
                            if pct_pos <= s.fresh_tracks_pct:
                                self.hovered_segment = "fresh"
                            else:
                                self.hovered_segment = "repeated"
                    else:
                        self.hovered_segment = None

        if self.hovered_session_idx != old_idx or self.hovered_segment != old_seg:
            self.update()
            if self.hovered_session_idx is not None:
                s = self.series[self.hovered_session_idx]
                stagnation_tip = (
                    f"<br/><font color='{COLOR_RED}'>⚠️ New entries < 15%: rischio stagnazione!</font>"
                    if s.new_entries_pct < 15.0
                    else ""
                )
                retention_tip = (
                    f"• <b>Riciclo dal set prima:</b> {s.repeated_prev_count} brani ({s.repeated_prev_pct}%)<br/>"
                    if s.session_index > 1
                    else ""
                )
                tags_html = ""
                if s.archetype_tags:
                    tags_pills = " ".join([f"<span style='background:#334155; color:#cbd5e1; padding:1px 5px; border-radius:3px; font-size:10px;'>{t}</span>" for t in s.archetype_tags])
                    tags_html = f"<br/>{tags_pills}"

                tip = (
                    f"<b>Set #{s.session_index}: {s.name}</b> ({s.date_str})<br/>"
                    f"🏷️ <b>Profilo DNA:</b> {s.archetype_icon} <b>{s.archetype}</b>{tags_html}<br/>"
                    f"<i>«{s.archetype_desc}»</i><br/><br/>"
                    f"Totale brani: <b>{s.total_tracks}</b><br/><br/>"
                    f"<b>🧬 CLUSTER DI PERFORMANCE (a quel tempo):</b><br/>"
                    f"• <b>Peak Bangers:</b> {s.peak_bangers_count} ({s.peak_bangers_pct}%)<br/>"
                    f"• <b>Core Rotation:</b> {s.core_rotation_count} ({s.core_rotation_pct}%)<br/>"
                    f"• <b>Novità / Debutto:</b> {s.untested_count} ({s.untested_pct}%)<br/>"
                    f"• <b>Deep Cuts / Ripescaggi:</b> {s.deep_cuts_cluster_count} ({s.deep_cuts_cluster_pct}%)<br/>"
                    f"• <b>Sovraesposte / Burnout:</b> {s.burnout_count} ({s.burnout_pct}%)<br/><br/>"
                    f"<b>⏳ TURNOVER & RICAMBIO:</b><br/>"
                    f"• <b>New Entries:</b> {s.new_entries_count} ({s.new_entries_pct}%)<br/>"
                    f"• <b>In Rotazione (ultime 3):</b> {s.in_rotation_count} ({s.in_rotation_pct}%)<br/>"
                    f"{retention_tip}"
                    f"{stagnation_tip}<br/>"
                    f"<i>Clicca per filtrare i brani del set nella tabella</i>"
                )
                pos_pt = event.position().toPoint()
                QToolTip.showText(self.mapToGlobal(pos_pt), tip, self)
            else:
                QToolTip.hideText()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.hovered_session_idx is not None:
            idx = self.hovered_session_idx
            self.selected_session_idx = idx
            self.update()
            s = self.series[idx]

            seg = self.hovered_segment or "all"
            t_ids: List[str] = []
            arch_prefix = f"{s.archetype_icon} {s.archetype}"

            if self.active_mode == "cluster":
                if seg == "peak":
                    t_ids = s.peak_bangers_ids
                    c_name = f"Set #{s.session_index} [{arch_prefix}]: Peak Bangers"
                elif seg == "core":
                    t_ids = s.core_rotation_ids
                    c_name = f"Set #{s.session_index} [{arch_prefix}]: Core Rotation"
                elif seg == "untested":
                    t_ids = s.untested_ids
                    c_name = f"Set #{s.session_index} [{arch_prefix}]: Novità / Debutto"
                elif seg == "deep_cluster":
                    t_ids = s.deep_cuts_cluster_ids
                    c_name = f"Set #{s.session_index} [{arch_prefix}]: Deep Cuts / Ripescaggi"
                elif seg == "burnout":
                    t_ids = s.burnout_ids
                    c_name = f"Set #{s.session_index} [{arch_prefix}]: Sovraesposte / Burnout"
                elif seg == "standard":
                    t_ids = s.standard_ids
                    c_name = f"Set #{s.session_index} [{arch_prefix}]: Standard"
                else:
                    t_ids = s.new_entries_ids + s.in_rotation_ids + s.deep_cuts_ids + s.one_shot_ids
                    c_name = f"Set #{s.session_index} [{arch_prefix}]: Tutte le tracce"
            elif self.active_mode == "turnover":
                if seg == "new":
                    t_ids = s.new_entries_ids
                    c_name = f"Set #{s.session_index} [{arch_prefix}]: New Entries"
                elif seg == "rotation":
                    t_ids = s.in_rotation_ids
                    c_name = f"Set #{s.session_index} [{arch_prefix}]: In Rotazione"
                elif seg == "deep":
                    t_ids = s.deep_cuts_ids
                    c_name = f"Set #{s.session_index} [{arch_prefix}]: Deep Cuts Storici"
                elif seg == "one_shot":
                    t_ids = s.one_shot_ids
                    c_name = f"Set #{s.session_index} [{arch_prefix}]: One-Shot"
                else:
                    t_ids = s.new_entries_ids + s.in_rotation_ids + s.deep_cuts_ids + s.one_shot_ids
                    c_name = f"Set #{s.session_index} [{arch_prefix}]: Tutte le tracce"
            else:
                if seg == "fresh":
                    t_ids = s.fresh_tracks_ids
                    c_name = f"Set #{s.session_index} [{arch_prefix}]: Tracce Fresche"
                elif seg == "repeated":
                    t_ids = s.repeated_prev_ids
                    c_name = f"Set #{s.session_index} [{arch_prefix}]: Tracce Ripetute dal Set Precedente"
                else:
                    t_ids = s.fresh_tracks_ids + s.repeated_prev_ids
                    c_name = f"Set #{s.session_index} [{arch_prefix}]: Tutte le tracce"

            self.session_clicked.emit(s.session_id, c_name, t_ids)


class SetTurnoverWidget(QWidget):
    """Wrapper container with mode selection controls and responsive canvas."""

    session_clicked = Signal(str, str, list)  # session_id, category_name, track_ids

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumSize(480, 280)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Mode Selector Header Row
        header_row = QHBoxLayout()
        header_row.setContentsMargins(8, 4, 8, 0)
        header_row.setSpacing(8)

        lbl_mode = QLabel("SCOMPOSIZIONE DNA:")
        lbl_mode.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 10px; font-weight: 700; letter-spacing: 0.5px;")
        header_row.addWidget(lbl_mode)

        self.btn_group = QButtonGroup(self)
        self.btn_group.setExclusive(True)

        self.btn_mode_cluster = QPushButton("🧬 Cluster di Performance")
        self.btn_mode_turnover = QPushButton("⏳ Turnover Temporale")
        self.btn_mode_retention = QPushButton("🔁 Riciclo dal Set Precedente")

        for idx, (btn, mode_key) in enumerate([
            (self.btn_mode_cluster, "cluster"),
            (self.btn_mode_turnover, "turnover"),
            (self.btn_mode_retention, "retention"),
        ]):
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR_BG_CARD};
                    border: 1px solid {COLOR_BORDER};
                    border-radius: 5px;
                    color: {COLOR_TEXT_SECONDARY};
                    padding: 4px 11px;
                    font-size: 11px;
                    font-weight: 600;
                }}
                QPushButton:hover {{
                    background-color: #2b2b36;
                    color: {COLOR_TEXT_PRIMARY};
                }}
                QPushButton:checked {{
                    background-color: #0d2b45;
                    border-color: {COLOR_BLUE};
                    color: #ffffff;
                }}
            """)
            if idx == 0:
                btn.setChecked(True)
            self.btn_group.addButton(btn, idx)
            header_row.addWidget(btn)

        header_row.addStretch(1)

        lbl_arch_hint = QLabel("🏷️ Archetipi: 🚀 Prime-Time | 🧪 Testing | 🔍 Digger | 🔁 Fotocopia | ⚖️ Equilibrato")
        lbl_arch_hint.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px;")
        lbl_arch_hint.setToolTip(
            "Classificazione descrittiva automatica del set:\n"
            "• 🚀 Prime-Time / Festival: >= 65% hit collaudate e rotazione fissa\n"
            "• 🧪 Testing Lab / Rodaggio: >= 35% musica nuova mai testata prima\n"
            "• 🔍 Digger's Set / Ricerca: >= 25% ripescaggi dal catalogo storico\n"
            "• 🔁 Twin / Fotocopia: >= 55% tracce riproposte dal set precedente\n"
            "• ⚖️ Signature Flow / Equilibrato: flusso armonioso ed equilibrato\n"
            "• 🌱 Genesis / Battesimo: debutto dello storico o rinnovo quasi totale"
        )
        header_row.addWidget(lbl_arch_hint)
        layout.addLayout(header_row)

        # Main Canvas
        self.canvas = _TurnoverCanvas(self)
        self.canvas.session_clicked.connect(self.session_clicked.emit)
        layout.addWidget(self.canvas, stretch=1)

        # Button signals
        self.btn_mode_cluster.clicked.connect(lambda: self.canvas.set_mode("cluster"))
        self.btn_mode_turnover.clicked.connect(lambda: self.canvas.set_mode("turnover"))
        self.btn_mode_retention.clicked.connect(lambda: self.canvas.set_mode("retention"))

    @property
    def series(self) -> List[SessionTurnoverData]:
        return self.canvas.series

    @property
    def active_mode(self) -> str:
        return self.canvas.active_mode

    def set_data(self, series: List[SessionTurnoverData]) -> None:
        self.canvas.set_data(series)

    def select_session(self, session_idx: int) -> None:
        self.canvas.select_session(session_idx)

    def clear_selection(self) -> None:
        self.canvas.clear_selection()
