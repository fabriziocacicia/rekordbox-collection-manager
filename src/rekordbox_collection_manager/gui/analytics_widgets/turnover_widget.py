"""Set Turnover & DNA Widget - Interactive Multi-Dimensional Stacked Bar Chart.

Allows analyzing the composition of chronological live sets through 3 unified dimensions:
1. 🧬 Performance Clusters (Peak Bangers, Core Rotation, Untested, Deep Cuts, Burnout)
2. ⏳ Temporal Turnover (New Entries, In Rotation, Historical Deep Cuts, One-Shot)
3. 🔁 Direct Retention (Fresh vs Repeated from immediately preceding set)
"""

from typing import Any, Dict, List, Optional, Tuple
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
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
        self._legend_items: List[Tuple[QRectF, str, str, str]] = []

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
                "No valid gig sessions recorded for set turnover analysis.",
            )
            return

        step_w = grid_w / float(n_sessions)

        # Determine step skipping for X-axis labels to eliminate text collision
        min_label_width = 30.0
        if step_w >= min_label_width:
            label_interval = 1
        elif step_w >= 16.0:
            label_interval = 5
        elif step_w >= 10.0:
            label_interval = 10
        else:
            label_interval = 20

        # Bar widths (guaranteeing a clean 2px gap between bars)
        col_w = max(4.0, min(48.0, step_w - 2.0))

        last_labeled_idx = -999
        for idx, s in enumerate(self.series):
            x_center = left_margin + (idx + 0.5) * step_w
            x_left = x_center - (col_w / 2.0)

            is_session_hovered = self.hovered_session_idx == idx
            is_session_selected = self.selected_session_idx == idx

            # Draw Archetype Icon Badge above bar (hover/select only if dense)
            show_badge = (n_sessions <= 20) or is_session_hovered or is_session_selected
            if show_badge:
                badge_w = min(max(col_w + 6, 20.0), 28.0)
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

            # Draw Bars according to active_mode with rounded 3px clip path
            bar_path = QPainterPath()
            bar_path.addRoundedRect(QRectF(x_left, top_margin, col_w, grid_h), 3, 3)
            painter.save()
            painter.setClipPath(bar_path)
            if self.active_mode == "cluster":
                self._draw_cluster_bars(painter, s, idx, x_left, col_w, top_margin, grid_h, is_session_hovered)
            elif self.active_mode == "turnover":
                self._draw_turnover_bars(painter, s, idx, x_left, col_w, top_margin, grid_h, is_session_hovered)
            else:
                self._draw_retention_bars(painter, s, idx, x_left, col_w, top_margin, grid_h, is_session_hovered)
            painter.restore()

            # Selection / Hover border
            if is_session_selected:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(COLOR_TEXT_PRIMARY), 2))
                painter.drawRoundedRect(QRectF(x_left - 1, top_margin - 1, col_w + 2, grid_h + 2), 4, 4)
            elif is_session_hovered:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(COLOR_BLUE), 1))
                painter.drawRoundedRect(QRectF(x_left - 0.5, top_margin - 0.5, col_w + 1, grid_h + 1), 3.5, 3.5)

            # Draw X-Axis Label below bar
            is_endpoint = (idx == 0) or (idx == n_sessions - 1)
            is_interval = (idx + 1) % label_interval == 0
            # Don't crowd the last endpoint label if interval just drew
            if idx == n_sessions - 1 and (idx - last_labeled_idx) < (label_interval // 2) and (idx != 0):
                is_endpoint = True
            should_draw_label = is_endpoint or is_interval or is_session_hovered or is_session_selected

            if should_draw_label:
                last_labeled_idx = idx
                # Tick line
                painter.setPen(QPen(QColor(COLOR_BLUE if is_session_hovered else COLOR_BORDER), 1))
                painter.drawLine(int(x_center), int(top_margin + grid_h), int(x_center), int(top_margin + grid_h + 3))

                font_label = QFont()
                font_label.setPointSize(8)
                font_label.setBold(is_session_hovered or is_session_selected)
                painter.setFont(font_label)
                painter.setPen(QColor("#ffffff" if is_session_hovered else COLOR_TEXT_SECONDARY))
                label_text = f"S{s.session_index}" if n_sessions > 12 else s.name[:8]
                painter.drawText(
                    QRectF(x_center - 18, top_margin + grid_h + 5, 36, 14),
                    Qt.AlignmentFlag.AlignCenter,
                    label_text,
                )

                # Date / Year timeline marker
                is_year_change = (
                    idx == 0
                    or (idx > 0 and len(s.date_str) >= 4 and s.date_str[:4] != self.series[idx - 1].date_str[:4])
                    or (idx == n_sessions - 1 and n_sessions > 15)
                )
                if is_year_change and len(s.date_str) >= 4:
                    font_year = QFont()
                    font_year.setPointSize(7)
                    painter.setFont(font_year)
                    painter.setPen(QColor(COLOR_TEXT_MUTED))
                    painter.drawText(
                        QRectF(x_center - 20, top_margin + grid_h + 19, 40, 12),
                        Qt.AlignmentFlag.AlignCenter,
                        s.date_str[:4],
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
                ("Peak Bangers", COLOR_PEAK, "Set Staples (Pick Rate >= 60%). High-frequency hits and peak-time anchors reliably played across sets."),
                ("Core Rotation", COLOR_CORE, "Active Rotation (Pick Rate 25–60%). Reliable backbone tracks regularly rotated into your sets."),
                ("New / Debut", COLOR_UNTESTED, "Untested / Fresh Debuts. Recent additions or tracks debuting in this live set."),
                ("Deep Cuts", COLOR_DEEP_CUTS, "Catalog Rediscoveries. Older catalog tracks pulled back into rotation after an absence."),
                ("Overexposed / Burnout", COLOR_BURNOUT, "Saturated Tracks. Tracks played in many prior gigs that may suffer from set fatigue."),
            ]
        elif self.active_mode == "turnover":
            legend_items = [
                ("New Entries", COLOR_GREEN, "Collection Debuts. Tracks played for the very first time in your performance history."),
                ("In Rotation (last 3)", COLOR_BLUE, "Active Rotation. Tracks that were also played in at least one of the 3 preceding live sets."),
                ("Deep Cuts / Catalog", COLOR_DEEP_CUTS, "Historical Repertoire. Older collection tracks not played in the 3 preceding gigs."),
                ("One-Shot", COLOR_ONE_SHOT, "Single Appearance. Tracks only played once in this specific set and never rotated again."),
            ]
        else:
            legend_items = [
                ("Fresh Tracks", COLOR_GREEN, "Set Turnover. Tracks in this set that were NOT played in the immediately preceding set."),
                ("Repeated from Prev", COLOR_AMBER, "Direct Retention. Tracks carried over directly from the immediately preceding gig."),
            ]

        self._legend_items = []
        font_leg = QFont()
        font_leg.setPointSize(8)
        font_leg.setBold(True)
        painter.setFont(font_leg)

        curr_x = start_x
        step_leg = min(170.0, total_w / float(len(legend_items)))
        for label, col_str, desc in legend_items:
            item_w = min(step_leg - 6, 160.0)
            item_rect = QRectF(curr_x, y, item_w, 18)
            self._legend_items.append((item_rect, label, col_str, desc))

            # Dot indicator
            painter.setBrush(QBrush(QColor(col_str)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QRectF(curr_x, y + 4, 8, 8))

            # Label text
            painter.setPen(QColor(COLOR_TEXT_SECONDARY))
            painter.drawText(
                QRectF(curr_x + 13, y, item_w - 13, 16),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                label,
            )
            curr_x += step_leg

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        if self.hovered_session_idx is not None or self.hovered_segment is not None:
            self.hovered_session_idx = None
            self.hovered_segment = None
            self.update()
        QToolTip.hideText()

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        w = self.width()
        h = self.height()

        top_margin = 52
        bottom_margin = 46
        left_margin = 45
        right_margin = 20
        grid_w = max(10, w - left_margin - right_margin)
        grid_h = max(10, h - top_margin - bottom_margin)

        n_sessions = len(self.series)
        if n_sessions == 0:
            return

        # Check legend item hover for category explanation tooltip
        if pos.y() < (top_margin - 12):
            hovered_legend = None
            for rect, label, col_str, desc in getattr(self, "_legend_items", []):
                if rect.contains(pos):
                    hovered_legend = (label, col_str, desc)
                    break

            if hovered_legend:
                if self.hovered_session_idx is not None or self.hovered_segment is not None:
                    self.hovered_session_idx = None
                    self.hovered_segment = None
                    self.update()
                label, col_str, desc = hovered_legend
                tip = (
                    f"<b><font color='{col_str}'>● {label}</font></b><br/>"
                    f"<span style='font-size:11px; color:#cbd5e1;'>{desc}</span>"
                )
                pos_pt = event.position().toPoint()
                QToolTip.showText(self.mapToGlobal(pos_pt), tip, self)
                return

        step_w = grid_w / float(n_sessions)
        col_w = max(4.0, min(48.0, step_w - 2.0))

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
                    f"<br/><font color='{COLOR_RED}'>⚠️ New entries < 15%: risk of set stagnation!</font>"
                    if s.new_entries_pct < 15.0
                    else ""
                )
                retention_tip = (
                    f"• <b>Repeated from prev set:</b> {s.repeated_prev_count} tracks ({s.repeated_prev_pct}%)<br/>"
                    if s.session_index > 1
                    else ""
                )
                tags_html = ""
                if s.archetype_tags:
                    tags_pills = " ".join([f"<span style='background:#334155; color:#cbd5e1; padding:1px 5px; border-radius:3px; font-size:10px;'>{t}</span>" for t in s.archetype_tags])
                    tags_html = f"<br/>{tags_pills}"

                tip = (
                    f"<b>Set #{s.session_index}: {s.name}</b> ({s.date_str})<br/>"
                    f"🏷️ <b>DNA Profile:</b> {s.archetype_icon} <b>{s.archetype}</b>{tags_html}<br/>"
                    f"<i>«{s.archetype_desc}»</i><br/><br/>"
                    f"Total tracks: <b>{s.total_tracks}</b><br/><br/>"
                    f"<b>🧬 PERFORMANCE CLUSTERS (at that time):</b><br/>"
                    f"• <b>Peak Bangers:</b> {s.peak_bangers_count} ({s.peak_bangers_pct}%)<br/>"
                    f"• <b>Core Rotation:</b> {s.core_rotation_count} ({s.core_rotation_pct}%)<br/>"
                    f"• <b>New / Debut:</b> {s.untested_count} ({s.untested_pct}%)<br/>"
                    f"• <b>Deep Cuts / Rediscoveries:</b> {s.deep_cuts_cluster_count} ({s.deep_cuts_cluster_pct}%)<br/>"
                    f"• <b>Overexposed / Burnout:</b> {s.burnout_count} ({s.burnout_pct}%)<br/><br/>"
                    f"<b>⏳ TURNOVER & ROTATION:</b><br/>"
                    f"• <b>New Entries:</b> {s.new_entries_count} ({s.new_entries_pct}%)<br/>"
                    f"• <b>In Rotation (last 3):</b> {s.in_rotation_count} ({s.in_rotation_pct}%)<br/>"
                    f"{retention_tip}"
                    f"{stagnation_tip}<br/>"
                    f"<i>Click to filter set tracks in table</i>"
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
                    c_name = f"Set #{s.session_index} [{arch_prefix}]: New / Debut"
                elif seg == "deep_cluster":
                    t_ids = s.deep_cuts_cluster_ids
                    c_name = f"Set #{s.session_index} [{arch_prefix}]: Deep Cuts / Rediscoveries"
                elif seg == "burnout":
                    t_ids = s.burnout_ids
                    c_name = f"Set #{s.session_index} [{arch_prefix}]: Overexposed / Burnout"
                elif seg == "standard":
                    t_ids = s.standard_ids
                    c_name = f"Set #{s.session_index} [{arch_prefix}]: Standard"
                else:
                    t_ids = s.new_entries_ids + s.in_rotation_ids + s.deep_cuts_ids + s.one_shot_ids
                    c_name = f"Set #{s.session_index} [{arch_prefix}]: All Tracks"
            elif self.active_mode == "turnover":
                if seg == "new":
                    t_ids = s.new_entries_ids
                    c_name = f"Set #{s.session_index} [{arch_prefix}]: New Entries"
                elif seg == "rotation":
                    t_ids = s.in_rotation_ids
                    c_name = f"Set #{s.session_index} [{arch_prefix}]: In Rotation"
                elif seg == "deep":
                    t_ids = s.deep_cuts_ids
                    c_name = f"Set #{s.session_index} [{arch_prefix}]: Catalog Deep Cuts"
                elif seg == "one_shot":
                    t_ids = s.one_shot_ids
                    c_name = f"Set #{s.session_index} [{arch_prefix}]: One-Shot"
                else:
                    t_ids = s.new_entries_ids + s.in_rotation_ids + s.deep_cuts_ids + s.one_shot_ids
                    c_name = f"Set #{s.session_index} [{arch_prefix}]: All Tracks"
            else:
                if seg == "fresh":
                    t_ids = s.fresh_tracks_ids
                    c_name = f"Set #{s.session_index} [{arch_prefix}]: Fresh Tracks"
                elif seg == "repeated":
                    t_ids = s.repeated_prev_ids
                    c_name = f"Set #{s.session_index} [{arch_prefix}]: Repeated from Prev Set"
                else:
                    t_ids = s.fresh_tracks_ids + s.repeated_prev_ids
                    c_name = f"Set #{s.session_index} [{arch_prefix}]: All Tracks"

            self.session_clicked.emit(s.session_id, c_name, t_ids)


class SetTurnoverWidget(QWidget):
    """Wrapper container with mode selection controls and responsive canvas."""

    session_clicked = Signal(str, str, list)  # session_id, category_name, track_ids

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumSize(460, 240)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Mode Selector Header Row
        header_row = QHBoxLayout()
        header_row.setContentsMargins(8, 4, 8, 0)
        header_row.setSpacing(8)

        lbl_mode = QLabel("DNA:")
        lbl_mode.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 10px; font-weight: 700; letter-spacing: 0.5px;")
        header_row.addWidget(lbl_mode)

        self.btn_group = QButtonGroup(self)
        self.btn_group.setExclusive(True)

        self.btn_mode_cluster = QPushButton("🧬 Clusters")
        self.btn_mode_turnover = QPushButton("⏳ Turnover")
        self.btn_mode_retention = QPushButton("🔁 Retention")

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

        lbl_arch_hint = QLabel("🏷️ Archetypes ℹ️")
        lbl_arch_hint.setCursor(Qt.CursorShape.PointingHandCursor)
        lbl_arch_hint.setStyleSheet(f"""
            QLabel {{
                color: {COLOR_TEXT_SECONDARY};
                background-color: {COLOR_BG_CARD};
                border: 1px solid {COLOR_BORDER};
                border-radius: 5px;
                padding: 4px 9px;
                font-size: 11px;
                font-weight: 600;
            }}
            QLabel:hover {{
                background-color: #2b2b36;
                border-color: {COLOR_BLUE};
                color: {COLOR_TEXT_PRIMARY};
            }}
        """)
        lbl_arch_hint.setToolTip(
            "Automatic descriptive set profile:\n"
            "• 🚀 Prime-Time / Festival: >= 65% proven hits and core rotation\n"
            "• 🧪 Testing Lab / Burn-in: >= 35% brand-new music never tested before\n"
            "• 🔍 Digger's Set / Deep Dive: >= 25% catalog rediscoveries\n"
            "• 🔁 Twin / Duplicate: >= 55% tracks repeated from previous set\n"
            "• ⚖️ Signature Flow / Balanced: smooth and balanced flow\n"
            "• 🌱 Genesis / Debut: collection debut or near-total overhaul"
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
