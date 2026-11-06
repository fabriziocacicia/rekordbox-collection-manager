"""Survival Decay Curve Widget - Kaplan-Meier Step-Line Event Analysis.

Plots the percentage of tracks remaining at zero live plays over sets elapsed since introduction.
Reveals the mathematically proven point of no return beyond which unplayed tracks become dead weight.
"""

from typing import List, Optional
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
        self.setMinimumSize(480, 260)

        self.steps: List[SurvivalStepData] = []
        self.point_of_no_return: int = 4
        self.hovered_step_idx: Optional[int] = None

    def set_data(self, steps: List[SurvivalStepData], point_of_no_return: int = 4) -> None:
        self.steps = steps or []
        self.point_of_no_return = point_of_no_return
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()

        top_margin = 35
        bottom_margin = 45
        left_margin = 55
        right_margin = 35

        grid_w = max(10, w - left_margin - right_margin)
        grid_h = max(10, h - top_margin - bottom_margin)

        # Background
        painter.fillRect(0, 0, w, h, QColor(COLOR_BG_DARK))

        # Y-Axis Guideline Ticks (100%, 75%, 50%, 25%, 0%)
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
            # Dotted guideline
            painter.setPen(QPen(QColor(COLOR_BORDER), 1, Qt.PenStyle.DotLine))
            painter.drawLine(int(left_margin), int(y_pos), int(left_margin + grid_w), int(y_pos))
            painter.setPen(QColor(COLOR_TEXT_MUTED))

        if not self.steps:
            painter.setPen(QColor(COLOR_TEXT_MUTED))
            font_empty = QFont()
            font_empty.setPointSize(11)
            painter.setFont(font_empty)
            painter.drawText(
                QRectF(left_margin, top_margin, grid_w, grid_h),
                Qt.AlignmentFlag.AlignCenter,
                "Dati insufficienti per generare la curva di decadimento Kaplan-Meier.",
            )
            return

        max_set = max(s.set_number for s in self.steps)
        if max_set == 0:
            max_set = 1

        def to_point(set_num: int, pct: float) -> QPointF:
            x = left_margin + (set_num / float(max_set)) * grid_w
            y = top_margin + grid_h - (pct / 100.0) * grid_h
            return QPointF(x, y)

        # Draw Point of No Return Line
        if 0 < self.point_of_no_return <= max_set:
            x_threshold = left_margin + (self.point_of_no_return / float(max_set)) * grid_w
            pen_thresh = QPen(QColor(COLOR_AMBER), 1.5, Qt.PenStyle.DashLine)
            painter.setPen(pen_thresh)
            painter.drawLine(QPointF(x_threshold, top_margin), QPointF(x_threshold, top_margin + grid_h))

            # Badge at top of line
            painter.setBrush(QBrush(QColor("#382305")))
            painter.setPen(QPen(QColor(COLOR_AMBER), 1))
            badge_rect = QRectF(x_threshold - 68, top_margin - 24, 136, 20)
            painter.drawRoundedRect(badge_rect, 4, 4)

            font_badge = QFont()
            font_badge.setPointSize(8)
            font_badge.setBold(True)
            painter.setFont(font_badge)
            painter.setPen(QColor(COLOR_AMBER))
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, f"Soglia Non Ritorno (Set {self.point_of_no_return})")

        # Build Step Line and Fill Area Path
        step_path = QPainterPath()
        fill_path = QPainterPath()

        first_pt = to_point(self.steps[0].set_number, self.steps[0].unplayed_pct)
        step_path.moveTo(first_pt)
        fill_path.moveTo(first_pt)

        current_pt = first_pt
        for i in range(1, len(self.steps)):
            next_pt = to_point(self.steps[i].set_number, self.steps[i].unplayed_pct)
            # Step line: horizontal to next X, then vertical down to next Y
            step_corner = QPointF(next_pt.x(), current_pt.y())
            step_path.lineTo(step_corner)
            step_path.lineTo(next_pt)

            fill_path.lineTo(step_corner)
            fill_path.lineTo(next_pt)
            current_pt = next_pt

        # Close fill path
        fill_path.lineTo(current_pt.x(), top_margin + grid_h)
        fill_path.lineTo(first_pt.x(), top_margin + grid_h)
        fill_path.closeSubpath()

        # Gradient fill
        grad = QLinearGradient(0, top_margin, 0, top_margin + grid_h)
        grad.setColorAt(0.0, QColor(0, 162, 255, 90))
        grad.setColorAt(1.0, QColor(0, 162, 255, 5))
        painter.setBrush(QBrush(grad))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(fill_path)

        # Draw Step Stroke
        pen_step = QPen(QColor(COLOR_BLUE), 2.5)
        painter.setPen(pen_step)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(step_path)

        # Draw Data Points & X-Axis Labels
        font_pt = QFont()
        font_pt.setPointSize(8)
        painter.setFont(font_pt)

        for idx, s in enumerate(self.steps):
            pt = to_point(s.set_number, s.unplayed_pct)

            # X-Axis Tick Label
            painter.setPen(QColor(COLOR_TEXT_SECONDARY))
            painter.drawText(
                QRectF(pt.x() - 15, top_margin + grid_h + 8, 30, 16),
                Qt.AlignmentFlag.AlignCenter,
                str(s.set_number),
            )

            # Node circle
            is_hover = self.hovered_step_idx == idx
            pt_radius = 5.0 if is_hover else 3.5
            pt_color = QColor(COLOR_AMBER if s.set_number == self.point_of_no_return else COLOR_BLUE)

            painter.setBrush(QBrush(pt_color.lighter(130) if is_hover else pt_color))
            painter.setPen(QPen(QColor("#ffffff"), 1.5 if is_hover else 1.0))
            painter.drawEllipse(pt, pt_radius, pt_radius)

        # X-Axis Title
        painter.setPen(QColor(COLOR_TEXT_MUTED))
        painter.drawText(
            QRectF(left_margin, top_margin + grid_h + 24, grid_w, 18),
            Qt.AlignmentFlag.AlignCenter,
            "NUMERO DI SET TRASCORSI DALL'ACQUISTO / DOWNLOAD",
        )

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        w = self.width()
        h = self.height()
        top_margin = 35
        bottom_margin = 45
        left_margin = 55
        right_margin = 35
        grid_w = max(10, w - left_margin - right_margin)
        grid_h = max(10, h - top_margin - bottom_margin)

        if not self.steps:
            return

        max_set = max(s.set_number for s in self.steps) or 1
        old_idx = self.hovered_step_idx
        self.hovered_step_idx = None

        for idx, s in enumerate(self.steps):
            x = left_margin + (s.set_number / float(max_set)) * grid_w
            y = top_margin + grid_h - (s.unplayed_pct / 100.0) * grid_h
            if (pos.x() - x) ** 2 + (pos.y() - y) ** 2 <= 144:  # radius 12
                self.hovered_step_idx = idx
                break

        if self.hovered_step_idx != old_idx:
            self.update()
            if self.hovered_step_idx is not None:
                s = self.steps[self.hovered_step_idx]
                asymptote_note = (
                    f"<br/><font color='{COLOR_AMBER}'>★ Soglia di Non Ritorno: oltre questo set la probabilità di suonare il brano scende drasticamente a zero!</font>"
                    if s.set_number == self.point_of_no_return
                    else ""
                )
                tip = (
                    f"<b>📉 Dopo {s.set_number} live set dall'importazione del brano:</b><br/>"
                    f"• Brani ancora <b>MAI suonati</b>: <font color='{COLOR_RED}'><b>{s.unplayed_pct}%</b></font> ({s.unplayed_count} su {s.total_exposed})<br/>"
                    f"• Brani adottati e suonati nei set: <b>{round(100.0 - s.unplayed_pct, 1)}%</b>"
                    f"{asymptote_note}<br/><br/>"
                    f"<i>💡 Clicca per caricare nella tabella le {s.unplayed_count} tracce rimaste a zero play</i>"
                )
                pos_pt = event.position().toPoint()
                QToolTip.showText(self.mapToGlobal(pos_pt), tip, self)
            else:
                QToolTip.hideText()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.hovered_step_idx is not None:
            s = self.steps[self.hovered_step_idx]
            self.step_clicked.emit(s.set_number, s.unplayed_pct)

