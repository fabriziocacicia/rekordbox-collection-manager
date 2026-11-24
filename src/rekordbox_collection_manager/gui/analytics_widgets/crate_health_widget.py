"""Crate Health Matrix Widget - Interactive 2D Exposure x Pick Rate Heatmap.

Visualizes track distribution across real live exposure brackets (X-axis) and
selection pick rate brackets (Y-axis), isolating dead weight (Death Zone) from peak performers.
"""

from typing import Dict, List, Optional, Tuple
from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from rekordbox_collection_manager.analytics import EXPOSURE_BRACKETS, PICK_RATE_BRACKETS
from rekordbox_collection_manager.gui.theme import (
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


class CrateHealthMatrixWidget(QWidget):
    """Custom QPainter heatmap widget representing the Crate Health Matrix."""

    cell_clicked = Signal(str, str, list)  # exposure_bracket, pick_rate_bracket, track_ids

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumSize(460, 240)

        self.exposure_brackets = list(EXPOSURE_BRACKETS)
        self.pick_rate_brackets = list(PICK_RATE_BRACKETS)
        self.matrix_data: Dict[Tuple[str, str], List[str]] = {}
        self.max_cell_count: int = 1

        self.hovered_cell: Optional[Tuple[int, int]] = None
        self.selected_cell: Optional[Tuple[int, int]] = None

        # Cached layout geometry
        self._cell_rects: Dict[Tuple[int, int], QRectF] = {}

    def set_data(self, matrix: Dict[Tuple[str, str], List[str]]) -> None:
        """Update matrix dataset and redraw."""
        self.matrix_data = matrix or {}
        max_c = 1
        for track_ids in self.matrix_data.values():
            if len(track_ids) > max_c:
                max_c = len(track_ids)
        self.max_cell_count = max_c
        self.update()

    def select_cell_by_name(self, exposure: str, pick_rate: str) -> None:
        """Select cell programmatically."""
        try:
            c_idx = self.exposure_brackets.index(exposure)
            r_idx = self.pick_rate_brackets.index(pick_rate)
            self.selected_cell = (c_idx, r_idx)
            self.update()
        except ValueError:
            pass

    def clear_selection(self) -> None:
        self.selected_cell = None
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()

        # Margins for axes headers
        left_margin = 105
        bottom_margin = 56
        top_margin = 24
        right_margin = 20

        grid_w = max(10, w - left_margin - right_margin)
        grid_h = max(10, h - top_margin - bottom_margin)

        num_cols = len(self.exposure_brackets)
        num_rows = len(self.pick_rate_brackets)

        col_w = grid_w / num_cols
        row_h = grid_h / num_rows

        self._cell_rects.clear()

        # Background fill
        painter.fillRect(0, 0, w, h, QColor(COLOR_BG_DARK))

        # Y-Axis (Pick Rate) Header
        font_axis = QFont()
        font_axis.setPointSize(9)
        font_axis.setBold(True)
        painter.setFont(font_axis)
        painter.setPen(QColor(COLOR_TEXT_MUTED))
        painter.drawText(16, top_margin - 8, "PICK RATE")

        font_label = QFont()
        font_label.setPointSize(9)
        painter.setFont(font_label)

        # Draw Y-Axis Labels (Rows)
        for r_idx, p_bracket in enumerate(self.pick_rate_brackets):
            y_top = top_margin + r_idx * row_h
            r_rect = QRectF(10, y_top, left_margin - 16, row_h)
            painter.setPen(QColor(COLOR_TEXT_SECONDARY))
            painter.drawText(
                r_rect,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                p_bracket,
            )

        # Draw X-Axis Labels (Cols)
        for c_idx, e_bracket in enumerate(self.exposure_brackets):
            x_left = left_margin + c_idx * col_w
            c_rect = QRectF(x_left, top_margin + grid_h + 4, col_w, 18)
            painter.setPen(QColor(COLOR_TEXT_SECONDARY))
            painter.drawText(
                c_rect,
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                e_bracket,
            )

        # X-Axis (Exposure) Header - perfectly centered under the grid with clean padding
        painter.setFont(font_axis)
        painter.setPen(QColor(COLOR_TEXT_MUTED))
        painter.drawText(
            QRectF(left_margin, top_margin + grid_h + 26, grid_w, 18),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            "EXPOSURE (LIVE SETS)",
        )

        # Draw Grid Cells
        font_count = QFont()
        font_count.setPointSize(12)
        font_count.setBold(True)

        font_sub = QFont()
        font_sub.setPointSize(8)

        for r_idx, p_bracket in enumerate(self.pick_rate_brackets):
            for c_idx, e_bracket in enumerate(self.exposure_brackets):
                cell_key = (e_bracket, p_bracket)
                tracks = self.matrix_data.get(cell_key, [])
                count = len(tracks)

                cell_x = left_margin + c_idx * col_w
                cell_y = top_margin + r_idx * row_h
                cell_rect = QRectF(cell_x + 2, cell_y + 2, col_w - 4, row_h - 4)
                self._cell_rects[(c_idx, r_idx)] = cell_rect

                is_hovered = self.hovered_cell == (c_idx, r_idx)
                is_selected = self.selected_cell == (c_idx, r_idx)

                # Determine base cell color & hazard zones
                is_death_zone = (p_bracket == "0% (Never played)") and (
                    e_bracket in (">10 Sets (Veteran)", "6-10 Sets (Advanced)")
                )
                is_peak_zone = (p_bracket in ("76-100%", "51-75%")) and (
                    e_bracket in (">10 Sets (Veteran)", "6-10 Sets (Advanced)")
                )

                intensity = min(1.0, count / float(self.max_cell_count)) if self.max_cell_count > 0 else 0

                if count == 0:
                    bg_color = QColor(24, 24, 32, 190)
                    border_color = QColor(COLOR_BORDER)
                elif is_death_zone:
                    # Refined crimson/wine for dead weight
                    r_val = int(55 + 85 * intensity)
                    bg_color = QColor(r_val, 22, 28, int(170 + 75 * intensity))
                    border_color = QColor(COLOR_RED)
                elif is_peak_zone:
                    # Sophisticated emerald for peak bangers
                    g_val = int(45 + 75 * intensity)
                    bg_color = QColor(12, g_val, 45, int(170 + 75 * intensity))
                    border_color = QColor(COLOR_GREEN)
                else:
                    # Deep slate navy blue
                    b_val = int(35 + 85 * intensity)
                    bg_color = QColor(14, int(28 + 45 * intensity), b_val, int(150 + 80 * intensity))
                    border_color = QColor(42, 54, 72) if intensity > 0.4 else QColor(COLOR_BORDER)

                if is_hovered:
                    bg_color = bg_color.lighter(130)

                # Draw cell box
                painter.setBrush(QBrush(bg_color))
                if is_selected:
                    painter.setPen(QPen(QColor(COLOR_BLUE), 2.5))
                else:
                    painter.setPen(QPen(border_color, 1))

                painter.drawRoundedRect(cell_rect, 6, 6)

                # Draw track count inside cell
                if count > 0:
                    painter.setFont(font_count)
                    painter.setPen(QColor(COLOR_TEXT_PRIMARY))
                    painter.drawText(
                        QRectF(cell_rect.x(), cell_rect.y() + 4, cell_rect.width(), cell_rect.height() * 0.55),
                        Qt.AlignmentFlag.AlignCenter,
                        str(count),
                    )

                    # Subtext badge
                    painter.setFont(font_sub)
                    if is_death_zone:
                        painter.setPen(QColor("#ff9999"))
                        sub_text = "DEAD WEIGHT"
                    elif is_peak_zone:
                        painter.setPen(QColor("#99ffbb"))
                        sub_text = "PEAK"
                    else:
                        painter.setPen(QColor(COLOR_TEXT_MUTED))
                        sub_text = "tracks"

                    painter.drawText(
                        QRectF(cell_rect.x(), cell_rect.y() + cell_rect.height() * 0.52, cell_rect.width(), cell_rect.height() * 0.4),
                        Qt.AlignmentFlag.AlignCenter,
                        sub_text,
                    )
                else:
                    painter.setFont(font_sub)
                    painter.setPen(QColor(COLOR_TEXT_MUTED))
                    painter.drawText(cell_rect, Qt.AlignmentFlag.AlignCenter, "—")

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        old_hover = self.hovered_cell
        self.hovered_cell = None

        for (c_idx, r_idx), rect in self._cell_rects.items():
            if rect.contains(pos):
                self.hovered_cell = (c_idx, r_idx)
                break

        if self.hovered_cell != old_hover:
            self.update()
            if self.hovered_cell:
                c_idx, r_idx = self.hovered_cell
                e_b = self.exposure_brackets[c_idx]
                p_b = self.pick_rate_brackets[r_idx]
                tracks = self.matrix_data.get((e_b, p_b), [])
                tip = f"<b>{e_b}</b> | <b>Pick Rate: {p_b}</b><br/>{len(tracks)} tracks in this cell.<br/><i>Click to view tracklist</i>"
                pos_pt = event.position().toPoint()
                QToolTip.showText(self.mapToGlobal(pos_pt), tip, self)
            else:
                QToolTip.hideText()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position()
            for (c_idx, r_idx), rect in self._cell_rects.items():
                if rect.contains(pos):
                    self.selected_cell = (c_idx, r_idx)
                    self.update()
                    e_b = self.exposure_brackets[c_idx]
                    p_b = self.pick_rate_brackets[r_idx]
                    tracks = self.matrix_data.get((e_b, p_b), [])
                    self.cell_clicked.emit(e_b, p_b, tracks)
                    return

