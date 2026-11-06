"""Camelot Wheel Radial Heatmap Widget.

Visualizes 24 harmonic sectors (1A-12A Minor inner ring, 1B-12B Major outer ring).
Weights sector background colors dynamically according to catalog track density (cool blue to radiant coral),
displays track count badges directly inside each sector, and overlays live set execution frequencies.
"""

import math
from typing import Dict, List, Optional, Tuple
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from rekordbox_collection_manager.analytics import CamelotSliceData
from rekordbox_collection_manager.gui.theme import (
    COLOR_AMBER,
    COLOR_BG_CARD,
    COLOR_BG_DARK,
    COLOR_BLUE,
    COLOR_BORDER,
    COLOR_GREEN,
    COLOR_RED,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_SECONDARY,
)


class CamelotWheelWidget(QWidget):
    """Interactive polar radial chart implementing the 24-sector Camelot Wheel."""

    slice_clicked = Signal(str, list)  # camelot_code, track_ids

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumSize(420, 420)

        self.slices: Dict[str, CamelotSliceData] = {}
        self.hovered_code: Optional[str] = None
        self.selected_code: Optional[str] = None

        self.max_catalog_count: int = 1
        self.total_catalog_tracks: int = 0
        self.max_catalog_share: float = 1.0
        self.max_live_share: float = 1.0

    def set_data(self, slices: Dict[str, CamelotSliceData]) -> None:
        """Update Camelot slices dataset and compute scale maxima."""
        self.slices = slices or {}
        max_cnt = 1
        total_cnt = 0
        max_cat = 1.0
        max_live = 1.0

        for s in self.slices.values():
            if s.catalog_count > max_cnt:
                max_cnt = s.catalog_count
            total_cnt += s.catalog_count
            if s.catalog_share_pct > max_cat:
                max_cat = s.catalog_share_pct
            if s.live_share_pct > max_live:
                max_live = s.live_share_pct

        self.max_catalog_count = max_cnt
        self.total_catalog_tracks = total_cnt
        self.max_catalog_share = max_cat
        self.max_live_share = max_live
        self.update()

    def select_slice(self, camelot_code: str) -> None:
        self.selected_code = camelot_code
        self.update()

    def clear_selection(self) -> None:
        self.selected_code = None
        self.update()

    def _get_sector_angles(self, camelot_num: int) -> Tuple[float, float]:
        """Convert Camelot hour (1..12) to start and span angles in degrees.

        12 is at 12 o'clock (90 deg), 1 at 1 o'clock (60 deg), 2 at 2 o'clock (30 deg)...
        Each sector spans 30 degrees.
        """
        center_deg = 90.0 - (camelot_num % 12) * 30.0
        start_deg = center_deg + 15.0
        span_deg = -30.0
        return start_deg, span_deg

    def _make_arc_path(
        self,
        center: QPointF,
        r_inner: float,
        r_outer: float,
        start_deg: float,
        span_deg: float,
    ) -> QPainterPath:
        """Create a donut sector path between inner and outer radii."""
        path = QPainterPath()
        outer_rect = QRectF(center.x() - r_outer, center.y() - r_outer, r_outer * 2, r_outer * 2)
        inner_rect = QRectF(center.x() - r_inner, center.y() - r_inner, r_inner * 2, r_inner * 2)

        path.arcMoveTo(outer_rect, start_deg)
        path.arcTo(outer_rect, start_deg, span_deg)

        end_deg = start_deg + span_deg
        path.arcTo(inner_rect, end_deg, -span_deg)
        path.closeSubpath()
        return path

    def _get_slice_color(self, track_count: int, is_minor: bool) -> QColor:
        """Compute high-contrast thermal density color weighted by track count."""
        if track_count <= 0 or self.max_catalog_count <= 0:
            # Dark slate for empty keys / catalog gaps
            return QColor(24, 25, 33, 230)

        # Normalized weight between 0.0 and 1.0
        w = min(1.0, max(0.0, track_count / float(self.max_catalog_count)))

        # Chromatic density gradient stops:
        # 0.00: Deep Navy/Ocean  (29, 64, 160)
        # 0.25: Vivid Cyan/Blue   (6, 160, 215)
        # 0.50: Emerald Green     (16, 175, 120)
        # 0.75: Warm Amber/Gold   (245, 158, 11)
        # 1.00: Radiant Coral/Red (239, 60, 68)
        stops = [
            (0.00, (29, 64, 160)),
            (0.25, (6, 160, 215)),
            (0.50, (16, 175, 120)),
            (0.75, (245, 158, 11)),
            (1.00, (239, 60, 68)),
        ]

        r, g, b = stops[0][1]
        for i in range(len(stops) - 1):
            s1, c1 = stops[i]
            s2, c2 = stops[i + 1]
            if s1 <= w <= s2:
                t = (w - s1) / (s2 - s1)
                r = int(c1[0] + (c2[0] - c1[0]) * t)
                g = int(c1[1] + (c2[1] - c1[1]) * t)
                b = int(c1[2] + (c2[2] - c1[2]) * t)
                break

        # Subtle shade for inner ring (Minors) for concentric depth
        if is_minor:
            r = int(r * 0.88)
            g = int(g * 0.88)
            b = int(b * 0.88)

        return QColor(r, g, b, 235)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()

        legend_space = 36.0
        wheel_h = h - legend_space
        center = QPointF(w / 2.0, wheel_h / 2.0)

        # Background
        painter.fillRect(0, 0, w, h, QColor(COLOR_BG_DARK))

        min_dim = min(w, wheel_h)
        if min_dim < 100:
            return

        r_outer = (min_dim / 2.0) - 16.0
        r_mid = r_outer * 0.68
        r_inner = r_outer * 0.38
        r_center_hub = r_inner * 0.84

        font_label = QFont()
        font_label.setPointSize(9)
        font_label.setBold(True)

        font_sub = QFont()
        font_sub.setPointSize(7)

        # 1. Draw Sectors
        for n in range(1, 13):
            start_deg, span_deg = self._get_sector_angles(n)

            # Outer ring: Major (B)
            code_b = f"{n}B"
            slice_b = self.slices.get(code_b)
            self._draw_slice(
                painter, center, r_mid, r_outer, start_deg, span_deg,
                slice_b, code_b, font_label, font_sub, is_minor=False,
            )

            # Inner ring: Minor (A)
            code_a = f"{n}A"
            slice_a = self.slices.get(code_a)
            self._draw_slice(
                painter, center, r_inner, r_mid, start_deg, span_deg,
                slice_a, code_a, font_label, font_sub, is_minor=True,
            )

        # 2. Draw Center Hub
        painter.setBrush(QBrush(QColor(COLOR_BG_CARD)))
        painter.setPen(QPen(QColor(COLOR_BORDER), 1.5))
        painter.drawEllipse(center, r_center_hub, r_center_hub)

        # Center Hub Content
        painter.setPen(QColor(COLOR_TEXT_PRIMARY))
        font_hub = QFont()
        font_hub.setPointSize(11)
        font_hub.setBold(True)
        painter.setFont(font_hub)

        if self.hovered_code and self.hovered_code in self.slices:
            s_hover = self.slices[self.hovered_code]
            painter.drawText(
                QRectF(center.x() - r_center_hub, center.y() - 20, r_center_hub * 2, 16),
                Qt.AlignmentFlag.AlignCenter,
                s_hover.code,
            )
            font_hub_sub = QFont()
            font_hub_sub.setPointSize(8)
            painter.setFont(font_hub_sub)
            painter.setPen(QColor(COLOR_TEXT_SECONDARY))
            painter.drawText(
                QRectF(center.x() - r_center_hub, center.y() - 4, r_center_hub * 2, 14),
                Qt.AlignmentFlag.AlignCenter,
                s_hover.traditional,
            )
            # Highlighted track count
            font_hub_cnt = QFont()
            font_hub_cnt.setPointSize(10)
            font_hub_cnt.setBold(True)
            painter.setFont(font_hub_cnt)
            painter.setPen(QColor(COLOR_BLUE))
            painter.drawText(
                QRectF(center.x() - r_center_hub, center.y() + 10, r_center_hub * 2, 15),
                Qt.AlignmentFlag.AlignCenter,
                f"{s_hover.catalog_count} brani",
            )
        else:
            painter.drawText(
                QRectF(center.x() - r_center_hub, center.y() - 16, r_center_hub * 2, 16),
                Qt.AlignmentFlag.AlignCenter,
                "CAMELOT",
            )
            font_hub_sub = QFont()
            font_hub_sub.setPointSize(8)
            painter.setFont(font_hub_sub)
            painter.setPen(QColor(COLOR_TEXT_SECONDARY))
            painter.drawText(
                QRectF(center.x() - r_center_hub, center.y() + 1, r_center_hub * 2, 14),
                Qt.AlignmentFlag.AlignCenter,
                f"{self.total_catalog_tracks} brani tot.",
            )

        # 3. Draw Density Scale Legend Bar at Bottom
        self._draw_legend_bar(painter, w, h)

    def _draw_slice(
        self,
        painter: QPainter,
        center: QPointF,
        r_in: float,
        r_out: float,
        start_deg: float,
        span_deg: float,
        slice_data: Optional[CamelotSliceData],
        code: str,
        font_main: QFont,
        font_sub: QFont,
        is_minor: bool,
    ) -> None:
        path = self._make_arc_path(center, r_in, r_out, start_deg, span_deg)

        is_hovered = self.hovered_code == code
        is_selected = self.selected_code == code

        track_count = slice_data.catalog_count if slice_data else 0
        live_share = slice_data.live_share_pct if slice_data else 0.0

        # Weighted background color based on track count
        bg_color = self._get_slice_color(track_count, is_minor)
        if is_hovered:
            bg_color = bg_color.lighter(135)

        painter.setBrush(QBrush(bg_color))
        painter.setPen(QPen(QColor(COLOR_BORDER), 1))
        painter.drawPath(path)

        # Draw Live Frequency Overlay (Glowing perimeter rim arc)
        if live_share > 0:
            live_ratio = min(1.0, live_share / self.max_live_share) if self.max_live_share > 0 else 0
            live_thickness = max(2.5, (r_out - r_in) * 0.22 * live_ratio)
            overlay_path = self._make_arc_path(
                center, r_out - live_thickness, r_out, start_deg, span_deg
            )
            overlay_color = QColor(255, 255, 255, 175)
            painter.setBrush(QBrush(overlay_color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPath(overlay_path)

        # Draw Selection Border
        if is_selected:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#ffffff"), 2.5))
            painter.drawPath(path)

        # Draw Text Label at sector centroid
        mid_angle_deg = start_deg + (span_deg / 2.0)
        mid_angle_rad = math.radians(mid_angle_deg)
        r_mid = (r_in + r_out) / 2.0

        label_x = center.x() + r_mid * math.cos(mid_angle_rad)
        label_y = center.y() - r_mid * math.sin(mid_angle_rad)

        # 1. Key Code (e.g. 8A)
        painter.setFont(font_main)
        painter.setPen(QColor("#ffffff" if track_count > 0 else COLOR_TEXT_MUTED))
        text_rect = QRectF(label_x - 22, label_y - 15, 44, 14)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, code)

        # 2. Traditional Name (e.g. Am)
        trad_text = slice_data.traditional if slice_data else ""
        if trad_text:
            painter.setFont(font_sub)
            painter.setPen(QColor("#e2e8f0" if track_count > 0 else COLOR_TEXT_MUTED))
            sub_rect = QRectF(label_x - 22, label_y - 1, 44, 11)
            painter.drawText(sub_rect, Qt.AlignmentFlag.AlignCenter, trad_text)

        # 3. Track Count Badge ("numerino")
        count_str = str(track_count)
        pill_w = max(24.0, 14.0 + len(count_str) * 6.5)
        pill_h = 14.0
        pill_rect = QRectF(label_x - pill_w / 2.0, label_y + 11, pill_w, pill_h)

        if track_count > 0:
            badge_bg = QColor(0, 0, 0, 165)
            badge_border = QColor(255, 255, 255, 80)
            badge_text_col = QColor("#ffffff")
        else:
            badge_bg = QColor(0, 0, 0, 90)
            badge_border = QColor(255, 255, 255, 25)
            badge_text_col = QColor(COLOR_TEXT_MUTED)

        painter.setBrush(QBrush(badge_bg))
        painter.setPen(QPen(badge_border, 1))
        painter.drawRoundedRect(pill_rect, 4, 4)

        font_count = QFont()
        font_count.setPointSize(8)
        font_count.setBold(True)
        painter.setFont(font_count)
        painter.setPen(badge_text_col)
        painter.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, count_str)

    def _draw_legend_bar(self, painter: QPainter, w: float, h: float) -> None:
        """Draw density color scale legend at bottom of widget."""
        leg_w = min(280.0, max(180.0, w - 80.0))
        leg_h = 9.0
        leg_x = (w - leg_w) / 2.0
        leg_y = h - 18.0

        grad = QLinearGradient(leg_x, 0, leg_x + leg_w, 0)
        grad.setColorAt(0.00, QColor(29, 64, 160))
        grad.setColorAt(0.25, QColor(6, 160, 215))
        grad.setColorAt(0.50, QColor(16, 175, 120))
        grad.setColorAt(0.75, QColor(245, 158, 11))
        grad.setColorAt(1.00, QColor(239, 60, 68))

        painter.setBrush(QBrush(grad))
        painter.setPen(QPen(QColor(COLOR_BORDER), 1))
        painter.drawRoundedRect(QRectF(leg_x, leg_y, leg_w, leg_h), 4, 4)

        font_leg = QFont()
        font_leg.setPointSize(8)
        painter.setFont(font_leg)
        painter.setPen(QColor(COLOR_TEXT_MUTED))
        painter.drawText(QRectF(leg_x - 70, leg_y - 2, 62, 14), Qt.AlignmentFlag.AlignRight, "0 brani")
        painter.drawText(QRectF(leg_x + leg_w + 8, leg_y - 2, 85, 14), Qt.AlignmentFlag.AlignLeft, f"Max ({self.max_catalog_count})")
        painter.drawText(QRectF(leg_x, leg_y - 14, leg_w, 12), Qt.AlignmentFlag.AlignCenter, "Densità Catalogo (Peso brani)")

    def _get_slice_at_pos(self, pos: QPointF) -> Optional[str]:
        """Hit test polar position to identify Camelot sector."""
        w = self.width()
        h = self.height()
        legend_space = 36.0
        wheel_h = h - legend_space
        center = QPointF(w / 2.0, wheel_h / 2.0)

        dx = pos.x() - center.x()
        dy = -(pos.y() - center.y())  # Screen Y is inverted
        dist = math.hypot(dx, dy)

        min_dim = min(w, wheel_h)
        r_outer = (min_dim / 2.0) - 16.0
        r_mid = r_outer * 0.68
        r_inner = r_outer * 0.38

        if dist < r_inner or dist > r_outer:
            return None

        # Calculate angle in degrees [0, 360)
        angle_rad = math.atan2(dy, dx)
        angle_deg = math.degrees(angle_rad)
        if angle_deg < 0:
            angle_deg += 360.0

        cw_deg = (90.0 + 15.0 - angle_deg) % 360.0
        num = int(cw_deg // 30.0)
        num = 12 if num == 0 else num

        ring = "B" if dist >= r_mid else "A"
        return f"{num}{ring}"

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        found_code = self._get_slice_at_pos(pos)

        if found_code != self.hovered_code:
            self.hovered_code = found_code
            self.update()

            if self.hovered_code and self.hovered_code in self.slices:
                s = self.slices[self.hovered_code]
                imbalance_str = (
                    f"<font color='{COLOR_AMBER}'>+{s.imbalance_pct}% (Sovra-selezionata)</font>"
                    if s.imbalance_pct > 1.5
                    else (
                        f"<font color='{COLOR_RED}'>{s.imbalance_pct}% (Buco di catalogo)</font>"
                        if s.imbalance_pct < -1.5
                        else "<font color='#88ffaa'>Bilanciata</font>"
                    )
                )
                tip = (
                    f"<b>{s.code} ({s.traditional})</b> — Tonalità {'Minore' if s.ring == 'minor' else 'Maggiore'}<br/>"
                    f"• <b>Catalogo:</b> <b>{s.catalog_count} brani</b> ({s.catalog_share_pct}%)<br/>"
                    f"• <b>Esecuzioni Live:</b> {s.live_plays_count} play ({s.live_share_pct}%)<br/>"
                    f"• <b>Bilanciamento:</b> {imbalance_str}<br/>"
                    f"<i>Clicca per filtrare i brani in questa tonalità</i>"
                )
                pos_pt = event.position().toPoint()
                QToolTip.showText(self.mapToGlobal(pos_pt), tip, self)
            else:
                QToolTip.hideText()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position()
            code = self._get_slice_at_pos(pos)
            if code and code in self.slices:
                self.selected_code = code
                self.update()
                s = self.slices[code]
                self.slice_clicked.emit(code, s.track_ids)
