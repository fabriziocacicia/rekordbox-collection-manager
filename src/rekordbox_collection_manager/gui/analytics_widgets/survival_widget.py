"""Survival Decay Curve Widget - Kaplan-Meier Step-Line Event Analysis.

Plots the percentage of tracks remaining at zero live plays over sets elapsed since introduction.
Reveals the mathematically proven point of no return beyond which unplayed tracks become dead weight.
"""

from typing import List, Optional, Tuple
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from rekordbox_collection_manager.analytics import SurvivalStepData
from rekordbox_collection_manager.gui.theme import (
    COLOR_AMBER,
    COLOR_BG_CARD,
    COLOR_BG_DARK,
    COLOR_BLUE,
    COLOR_BORDER,
    COLOR_RED,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_SECONDARY,
)


class SurvivalDecayWidget(QWidget):
    """Interactive step-line chart visualizing Kaplan-Meier survival decay of untested tracks."""

    step_clicked = Signal(int, float)  # set_number, unplayed_pct

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumSize(460, 220)

        self.steps: List[SurvivalStepData] = []
        self.point_of_no_return: int = 4
        self.hovered_step_idx: Optional[int] = None
        self.selected_step_idx: Optional[int] = None

    def set_data(self, steps: List[SurvivalStepData], point_of_no_return: int = 4) -> None:
        self.steps = steps or []
        self.point_of_no_return = point_of_no_return
        self.hovered_step_idx = None
        self.selected_step_idx = None
        self.update()

    def clear_selection(self) -> None:
        if self.selected_step_idx is not None:
            self.selected_step_idx = None
            self.update()

    def _chart_geometry(self) -> Tuple[int, int, int, int, int, int]:
        """Return (left_margin, top_margin, right_margin, bottom_margin, grid_w, grid_h)."""
        left_margin = 62
        top_margin = 56
        right_margin = 28
        bottom_margin = 46
        grid_w = max(10, self.width() - left_margin - right_margin)
        grid_h = max(10, self.height() - top_margin - bottom_margin)
        return left_margin, top_margin, right_margin, bottom_margin, grid_w, grid_h

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        left_margin, top_margin, _, _, grid_w, grid_h = self._chart_geometry()

        # Background
        painter.fillRect(0, 0, w, h, QColor(COLOR_BG_DARK))

        # Top Strip: Zone Legend (Left) & Summary Insights (Right)
        self._draw_top_strip(painter, left_margin, grid_w)

        # Y-Axis Title (Rotated Vertical Label)
        painter.save()
        font_y_title = QFont()
        font_y_title.setPointSize(7)
        font_y_title.setBold(True)
        painter.setFont(font_y_title)
        painter.setPen(QColor(COLOR_TEXT_MUTED))
        painter.translate(14, top_margin + grid_h / 2.0)
        painter.rotate(-90)
        painter.drawText(QRectF(-grid_h / 2.0, -10, grid_h, 16), Qt.AlignmentFlag.AlignCenter, "% STILL UNPLAYED")
        painter.restore()

        # Y-Axis Guideline Ticks (100%, 75%, 50%, 25%, 0%)
        font_axis = QFont()
        font_axis.setPointSize(8)
        painter.setFont(font_axis)

        for pct in (0, 25, 50, 75, 100):
            y_pos = top_margin + grid_h - (pct / 100.0) * grid_h
            painter.setPen(QColor(COLOR_TEXT_MUTED))
            painter.drawText(
                QRectF(18, y_pos - 7, left_margin - 26, 14),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"{pct}%",
            )
            painter.setPen(QPen(QColor(COLOR_BORDER), 1, Qt.PenStyle.DotLine))
            painter.drawLine(int(left_margin), int(y_pos), int(left_margin + grid_w), int(y_pos))

        if not self.steps:
            painter.setPen(QColor(COLOR_TEXT_MUTED))
            font_empty = QFont()
            font_empty.setPointSize(11)
            painter.setFont(font_empty)
            painter.drawText(
                QRectF(left_margin, top_margin, grid_w, grid_h),
                Qt.AlignmentFlag.AlignCenter,
                "Insufficient data to generate Kaplan-Meier decay curve.",
            )
            return

        max_set = max(s.set_number for s in self.steps) or 1

        def to_point(set_num: int, pct: float) -> QPointF:
            x = left_margin + (set_num / float(max_set)) * grid_w
            y = top_margin + grid_h - (pct / 100.0) * grid_h
            return QPointF(x, y)

        has_pnr = 0 < self.point_of_no_return <= max_set
        x_threshold = (
            left_margin + (self.point_of_no_return / float(max_set)) * grid_w
            if has_pnr
            else left_margin + grid_w
        )

        # Post-threshold subtle danger zone shading
        if has_pnr:
            danger_rect = QRectF(x_threshold, top_margin, (left_margin + grid_w) - x_threshold, grid_h)
            painter.fillRect(danger_rect, QColor(239, 68, 68, 12))

            # Subtle zone watermark labels near bottom axis
            font_zone = QFont()
            font_zone.setPointSize(7)
            font_zone.setBold(True)
            painter.setFont(font_zone)
            if (x_threshold - left_margin) >= 115:
                painter.setPen(QColor(0, 162, 255, 130))
                painter.drawText(
                    QRectF(left_margin + 8, top_margin + grid_h - 18, x_threshold - left_margin - 14, 14),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    "ACTIVE DISCOVERY WINDOW",
                )
            if ((left_margin + grid_w) - x_threshold) >= 130:
                painter.setPen(QColor(245, 158, 11, 140))
                painter.drawText(
                    QRectF(x_threshold + 8, top_margin + grid_h - 18, (left_margin + grid_w) - x_threshold - 14, 14),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    "DEAD WEIGHT PLATEAU",
                )

        # Build Step Line and Fill Area Path
        step_path = QPainterPath()
        fill_path = QPainterPath()

        first_pt = to_point(self.steps[0].set_number, self.steps[0].unplayed_pct)
        step_path.moveTo(first_pt)
        fill_path.moveTo(first_pt)

        current_pt = first_pt
        for i in range(1, len(self.steps)):
            next_pt = to_point(self.steps[i].set_number, self.steps[i].unplayed_pct)
            step_corner = QPointF(next_pt.x(), current_pt.y())
            step_path.lineTo(step_corner)
            step_path.lineTo(next_pt)

            fill_path.lineTo(step_corner)
            fill_path.lineTo(next_pt)
            current_pt = next_pt

        fill_path.lineTo(current_pt.x(), top_margin + grid_h)
        fill_path.lineTo(first_pt.x(), top_margin + grid_h)
        fill_path.closeSubpath()

        # Dual-Tone Fill & Stroke (Blue pre-threshold, Amber/Red post-threshold)
        painter.save()
        painter.setClipRect(QRectF(left_margin, top_margin, max(1.0, x_threshold - left_margin), grid_h + 2))
        grad_pre = QLinearGradient(0, top_margin, 0, top_margin + grid_h)
        grad_pre.setColorAt(0.0, QColor(0, 162, 255, 85))
        grad_pre.setColorAt(1.0, QColor(0, 162, 255, 8))
        painter.setBrush(QBrush(grad_pre))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(fill_path)
        painter.setPen(QPen(QColor(COLOR_BLUE), 2.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(step_path)
        painter.restore()

        if has_pnr and x_threshold < left_margin + grid_w:
            painter.save()
            painter.setClipRect(
                QRectF(x_threshold, top_margin, max(1.0, (left_margin + grid_w) - x_threshold + 2), grid_h + 2)
            )
            grad_post = QLinearGradient(0, top_margin, 0, top_margin + grid_h)
            grad_post.setColorAt(0.0, QColor(245, 158, 11, 60))
            grad_post.setColorAt(1.0, QColor(239, 68, 68, 8))
            painter.setBrush(QBrush(grad_post))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPath(fill_path)
            painter.setPen(QPen(QColor(COLOR_AMBER), 2.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(step_path)
            painter.restore()

        # Draw Point of No Return Line & Clamped Badge
        if has_pnr:
            pen_thresh = QPen(QColor(COLOR_AMBER), 1.5, Qt.PenStyle.DashLine)
            painter.setPen(pen_thresh)
            painter.drawLine(QPointF(x_threshold, top_margin), QPointF(x_threshold, top_margin + grid_h))

            badge_w = 144.0
            badge_x = max(float(left_margin), min(x_threshold - badge_w / 2.0, float(left_margin + grid_w) - badge_w))
            badge_rect = QRectF(badge_x, top_margin - 24, badge_w, 19)
            painter.setBrush(QBrush(QColor("#382305")))
            painter.setPen(QPen(QColor(COLOR_AMBER), 1))
            painter.drawRoundedRect(badge_rect, 4, 4)

            font_badge = QFont()
            font_badge.setPointSize(8)
            font_badge.setBold(True)
            painter.setFont(font_badge)
            painter.setPen(QColor(COLOR_AMBER))
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, f"Point of No Return (Set {self.point_of_no_return})")

        # Vertical Hover Crosshair
        if self.hovered_step_idx is not None and 0 <= self.hovered_step_idx < len(self.steps):
            h_step = self.steps[self.hovered_step_idx]
            h_pt = to_point(h_step.set_number, h_step.unplayed_pct)
            painter.setPen(QPen(QColor(255, 255, 255, 90), 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(h_pt.x(), top_margin), QPointF(h_pt.x(), top_margin + grid_h))

        # X-Axis Step-Skipping Interval
        step_w = grid_w / float(max_set)
        if step_w >= 24.0:
            label_interval = 1
        elif step_w >= 14.0:
            label_interval = 2
        else:
            label_interval = 5

        # Draw Data Points, Selected Halo, Milestone Callouts & X-Axis Labels
        font_pt = QFont()
        font_pt.setPointSize(8)
        painter.setFont(font_pt)

        for idx, s in enumerate(self.steps):
            pt = to_point(s.set_number, s.unplayed_pct)
            is_hover = self.hovered_step_idx == idx
            is_selected = self.selected_step_idx == idx
            is_pnr = s.set_number == self.point_of_no_return

            # X-Axis Tick Label (with dynamic thinning)
            should_label_x = (
                s.set_number == 0
                or s.set_number == max_set
                or is_pnr
                or is_hover
                or is_selected
                or (s.set_number % label_interval == 0)
            )
            if should_label_x:
                painter.setPen(QColor("#ffffff" if (is_hover or is_selected) else (COLOR_AMBER if is_pnr else COLOR_TEXT_SECONDARY)))
                font_tick = QFont()
                font_tick.setPointSize(8)
                font_tick.setBold(is_hover or is_selected or is_pnr)
                painter.setFont(font_tick)
                painter.drawText(
                    QRectF(pt.x() - 15, top_margin + grid_h + 7, 30, 16),
                    Qt.AlignmentFlag.AlignCenter,
                    str(s.set_number),
                )

            # Selection Ring
            if is_selected:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor("#ffffff"), 2.0))
                painter.drawEllipse(pt, 7.0, 7.0)

            # Node Circle
            pt_radius = 5.2 if (is_hover or is_selected) else (4.2 if is_pnr else 3.2)
            base_color = QColor(COLOR_AMBER if (is_pnr or s.set_number > self.point_of_no_return and has_pnr) else COLOR_BLUE)
            painter.setBrush(QBrush(base_color.lighter(130) if is_hover else base_color))
            painter.setPen(QPen(QColor("#ffffff"), 1.5 if (is_hover or is_selected) else 1.0))
            painter.drawEllipse(pt, pt_radius, pt_radius)

            # Milestone & Hover Callout Data Labels
            is_first_trial = (s.set_number == 1 and abs(self.point_of_no_return - 1) > 1)
            is_final = (s.set_number == max_set and max_set > 2 and abs(max_set - self.point_of_no_return) > 1)
            if is_hover or is_selected or is_pnr or is_first_trial or is_final:
                self._draw_point_callout(painter, pt, s, is_hover or is_selected, is_pnr, s.set_number == max_set)

        # X-Axis Title
        painter.setFont(font_axis)
        painter.setPen(QColor(COLOR_TEXT_MUTED))
        painter.drawText(
            QRectF(left_margin, top_margin + grid_h + 24, grid_w, 18),
            Qt.AlignmentFlag.AlignCenter,
            "NUMBER OF SETS ELAPSED SINCE PURCHASE / DATE ADDED",
        )

    def _draw_top_strip(self, painter: QPainter, left_margin: int, grid_w: int) -> None:
        """Render legend dots on the left and concise adoption insight stats on the right."""
        font_strip = QFont()
        font_strip.setPointSize(8)
        font_strip.setBold(True)
        painter.setFont(font_strip)

        # Left legend items
        x_curr = float(left_margin)
        legend = [
            ("Discovery Window", COLOR_BLUE),
            ("Dead Weight Plateau", COLOR_AMBER),
        ]
        for label, col_hex in legend:
            painter.setBrush(QBrush(QColor(col_hex)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QRectF(x_curr, 11, 8, 8))
            painter.setPen(QColor(COLOR_TEXT_SECONDARY))
            painter.drawText(QRectF(x_curr + 12, 7, 130, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
            x_curr += 135.0 if "Discovery" in label else 145.0

        # Right-aligned summary stats when space permits
        if grid_w >= 540 and len(self.steps) >= 2:
            step_by_num = {s.set_number: s for s in self.steps}
            first_trial = round(100.0 - step_by_num[1].unplayed_pct, 1) if 1 in step_by_num else None
            pnr_step = step_by_num.get(self.point_of_no_return)
            last_step = self.steps[-1]

            parts = []
            if first_trial is not None:
                parts.append(f"1st Set: {first_trial}% tried")
            if pnr_step is not None:
                adopted_pnr = round(100.0 - pnr_step.unplayed_pct, 1)
                parts.append(f"By Set {pnr_step.set_number}: {adopted_pnr}% adopted")
            parts.append(f"Plateau: {last_step.unplayed_pct}% unplayed")

            summary_text = "  •  ".join(parts)
            font_sum = QFont()
            font_sum.setPointSize(8)
            painter.setFont(font_sum)
            painter.setPen(QColor(COLOR_TEXT_MUTED))
            painter.drawText(
                QRectF(x_curr + 8, 7, max(10.0, left_margin + grid_w - x_curr - 8), 16),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                summary_text,
            )

    def _draw_point_callout(
        self, painter: QPainter, pt: QPointF, step: SurvivalStepData, emphasized: bool, is_pnr: bool, is_last: bool
    ) -> None:
        """Draw clean percentage label near key milestone or hovered curve points."""
        label = f"Set {step.set_number}: {step.unplayed_pct}%" if emphasized else f"{step.unplayed_pct}%"
        font_c = QFont()
        font_c.setPointSize(8)
        font_c.setBold(emphasized or is_pnr)
        painter.setFont(font_c)

        box_w = 88.0 if emphasized else 44.0
        box_h = 16.0
        if is_last and not emphasized:
            dx = -box_w - 6.0
        else:
            dx = 7.0 if not emphasized else (-box_w / 2.0)

        left_edge, top_edge, _, _, grid_w, _ = self._chart_geometry()
        x = max(float(left_edge + 2), min(pt.x() + dx, float(left_edge + grid_w) - box_w - 2))
        y = max(float(top_edge + 2), pt.y() - box_h - 6.0)

        rect = QRectF(x, y, box_w, box_h)
        if emphasized:
            painter.setBrush(QBrush(QColor(COLOR_BG_CARD)))
            painter.setPen(QPen(QColor(COLOR_AMBER if is_pnr else COLOR_BLUE), 1))
            painter.drawRoundedRect(rect, 4, 4)
            painter.setPen(QColor(COLOR_TEXT_PRIMARY))
        else:
            painter.setPen(QColor(COLOR_AMBER if is_pnr else COLOR_TEXT_SECONDARY))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        if self.hovered_step_idx is not None:
            self.hovered_step_idx = None
            self.unsetCursor()
            self.update()
        QToolTip.hideText()

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        left_margin, top_margin, _, _, grid_w, grid_h = self._chart_geometry()

        if not self.steps:
            return

        max_set = max(s.set_number for s in self.steps) or 1
        old_idx = self.hovered_step_idx
        self.hovered_step_idx = None

        if (left_margin - 10) <= pos.x() <= (left_margin + grid_w + 10) and (top_margin - 10) <= pos.y() <= (top_margin + grid_h + 12):
            best_idx = None
            best_dx = float("inf")
            for idx, s in enumerate(self.steps):
                x = left_margin + (s.set_number / float(max_set)) * grid_w
                dx = abs(pos.x() - x)
                if dx < best_dx:
                    best_dx = dx
                    best_idx = idx
            if best_idx is not None:
                self.hovered_step_idx = best_idx
                self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.unsetCursor()

        if self.hovered_step_idx != old_idx:
            self.update()
            if self.hovered_step_idx is not None:
                s = self.steps[self.hovered_step_idx]
                asymptote_note = (
                    f"<br/><font color='{COLOR_AMBER}'>★ Point of No Return: beyond this set, probability of playing the track drops to near zero!</font>"
                    if s.set_number == self.point_of_no_return
                    else ""
                )
                tip = (
                    f"<b>📉 After {s.set_number} live sets from track import:</b><br/>"
                    f"• Tracks still <b>NEVER played</b>: <font color='{COLOR_RED}'><b>{s.unplayed_pct}%</b></font> ({s.unplayed_count} of {s.total_exposed})<br/>"
                    f"• Tracks adopted and played in sets: <b>{round(100.0 - s.unplayed_pct, 1)}%</b>"
                    f"{asymptote_note}<br/><br/>"
                    f"<i>💡 Click to load the {s.unplayed_count} unplayed tracks into the table</i>"
                )
                pos_pt = event.position().toPoint()
                QToolTip.showText(self.mapToGlobal(pos_pt), tip, self)
            else:
                QToolTip.hideText()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.hovered_step_idx is not None:
            self.selected_step_idx = self.hovered_step_idx
            self.update()
            s = self.steps[self.hovered_step_idx]
            self.step_clicked.emit(s.set_number, s.unplayed_pct)
