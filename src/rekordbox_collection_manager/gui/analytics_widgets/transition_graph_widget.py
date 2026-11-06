"""Transition & Combos Network Graph Widget - Pan & Zoom Interactive Network.

Interactive directed network graph visualizing habitual track sequences (A -> B combos).
Highlights rigid mixing habits, fixed pairs, and transitions executed repeatedly in History.
Supports smooth mouse-wheel zooming, click-and-drag panning, and individual node dragging.
"""

import math
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional, Tuple
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QToolTip, QWidget

from rekordbox_collection_manager.analytics import TransitionEdgeData
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


class TransitionGraphWidget(QWidget):
    """Custom interactive directed network graph for track combo sequences with Pan & Zoom."""

    edge_clicked = Signal(str, str, int)  # source_id, target_id, count
    node_clicked = Signal(str)            # track_id

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumSize(480, 320)

        self.edges: List[TransitionEdgeData] = []
        self.nodes: Dict[str, Dict[str, Any]] = {}

        self.node_positions: Dict[str, QPointF] = {}
        self.hovered_node_id: Optional[str] = None
        self.hovered_edge: Optional[Tuple[str, str]] = None
        self.selected_node_id: Optional[str] = None

        # Pan & Zoom State
        self._zoom_factor: float = 1.0
        self._default_zoom: float = 1.0
        self._pan_offset: QPointF = QPointF(0.0, 0.0)
        self._is_panning: bool = False
        self._pan_start_pos: Optional[QPointF] = None

        # Node Dragging State
        self._dragging_node_id: Optional[str] = None
        self._drag_start_pos: Optional[QPointF] = None
        self._node_moved: bool = False
        self._has_custom_layout: bool = False

        # Overlay Button Rects (cached in screen coordinates)
        self._btn_zoom_in = QRectF()
        self._btn_zoom_out = QRectF()
        self._btn_zoom_reset = QRectF()

    def set_data(self, edges: List[TransitionEdgeData], nodes: Dict[str, Dict[str, Any]]) -> None:
        self.edges = edges or []
        self.nodes = nodes or {}
        self._has_custom_layout = False
        self._layout_nodes()
        self.update()

    def _get_node_radius(self, nid: str) -> float:
        """Calculate visual node radius based on total play count."""
        nd = self.nodes.get(nid, {})
        plays = nd.get("plays", 0)
        max_plays = max((n.get("plays", 0) for n in self.nodes.values()), default=1)
        play_ratio = min(1.0, plays / float(max_plays)) if max_plays > 0 else 0
        return max(13.0, min(24.0, 13.0 + play_ratio * 11.0))

    def _layout_nodes(self) -> None:
        """Arrange nodes in an airy, combo-aware layout with spring relaxation."""
        if self._has_custom_layout:
            return

        self.node_positions.clear()
        node_ids = list(self.nodes.keys())
        n = len(node_ids)
        if n == 0:
            return

        w = float(self.width())
        h = float(self.height())

        if n == 1:
            self.node_positions[node_ids[0]] = QPointF(w / 2.0, h / 2.0)
            self._zoom_factor = 1.0
            self._pan_offset = QPointF(0.0, 0.0)
            return

        # 1. Adjacency & Connected Component Detection
        adj = defaultdict(set)
        out_edges = defaultdict(set)
        for edge in self.edges:
            u, v = edge.source_id, edge.target_id
            adj[u].add(v)
            adj[v].add(u)
            out_edges[u].add(v)

        visited = set()
        components = []
        for nid in node_ids:
            if nid not in visited:
                comp = []
                q = deque([nid])
                visited.add(nid)
                while q:
                    curr = q.popleft()
                    comp.append(curr)
                    for neighbor in adj[curr]:
                        if neighbor not in visited:
                            visited.add(neighbor)
                            q.append(neighbor)
                components.append(comp)

        # Sort components by size descending
        components.sort(key=lambda c: len(c), reverse=True)
        n_comp = len(components)

        # 2. Initial Airy Component Placement
        positions: Dict[str, List[float]] = {}
        base_r = max(160.0, min(320.0, 50.0 + n_comp * 19.0))
        stagger_delta = 80.0 if n_comp > 4 else 0.0

        for i, comp in enumerate(components):
            angle = (2.0 * math.pi * i) / n_comp - (math.pi / 2.0)
            r_center = base_r + (stagger_delta if (i % 2 == 1) else 0.0)
            cx = r_center * math.cos(angle)
            cy = r_center * math.sin(angle)

            if len(comp) == 1:
                positions[comp[0]] = [cx, cy]
            elif len(comp) == 2:
                u, v = comp[0], comp[1]
                # Order source -> target along radial ray (pointing outwards)
                if v in out_edges[u]:
                    src, tgt = u, v
                else:
                    src, tgt = v, u
                dx = math.cos(angle) * 60.0
                dy = math.sin(angle) * 60.0
                positions[src] = [cx - dx, cy - dy]
                positions[tgt] = [cx + dx, cy + dy]
            elif len(comp) == 3:
                # Chain flow or linear sequence
                ordered = comp[:]
                for cand in comp:
                    in_deg = sum(1 for other in comp if cand in out_edges[other])
                    if in_deg == 0:
                        cand_next = [nxt for nxt in comp if nxt in out_edges[cand]]
                        if cand_next:
                            nxt1 = cand_next[0]
                            rem = [x for x in comp if x not in (cand, nxt1)]
                            if rem:
                                ordered = [cand, nxt1, rem[0]]
                        break
                dx = math.cos(angle) * 65.0
                dy = math.sin(angle) * 65.0
                positions[ordered[0]] = [cx - dx, cy - dy]
                positions[ordered[1]] = [cx, cy]
                positions[ordered[2]] = [cx + dx, cy + dy]
            else:
                # Sub-circle for larger cluster
                sub_r = max(60.0, len(comp) * 24.0)
                for s_idx, nid in enumerate(comp):
                    s_angle = (2.0 * math.pi * s_idx) / len(comp)
                    positions[nid] = [cx + sub_r * math.cos(s_angle), cy + sub_r * math.sin(s_angle)]

        # 3. Spring Relaxation Pass (Anti-collision + Edge Cohesion)
        TARGET_DIST = 125.0
        for iteration in range(40):
            forces = {nid: [0.0, 0.0] for nid in node_ids}
            # Repulsion between all nodes
            for i in range(n):
                id1 = node_ids[i]
                p1 = positions[id1]
                for j in range(i + 1, n):
                    id2 = node_ids[j]
                    p2 = positions[id2]
                    dx = p1[0] - p2[0]
                    dy = p1[1] - p2[1]
                    dist = math.hypot(dx, dy)
                    if dist < 0.001:
                        dx = 0.01
                        dy = 0.01
                        dist = 0.014
                    if dist < TARGET_DIST:
                        rep = (TARGET_DIST - dist) / dist * 0.35
                        forces[id1][0] += dx * rep
                        forces[id1][1] += dy * rep
                        forces[id2][0] -= dx * rep
                        forces[id2][1] -= dy * rep
            # Edge Cohesion (keep connected tracks comfortably near each other)
            for edge in self.edges:
                u, v = edge.source_id, edge.target_id
                if u not in positions or v not in positions:
                    continue
                p1 = positions[u]
                p2 = positions[v]
                dx = p2[0] - p1[0]
                dy = p2[1] - p1[1]
                dist = math.hypot(dx, dy)
                ideal_edge = 120.0
                if dist > ideal_edge:
                    att = (dist - ideal_edge) * 0.12
                    nx = dx / dist
                    ny = dy / dist
                    forces[u][0] += nx * att
                    forces[u][1] += ny * att
                    forces[v][0] -= nx * att
                    forces[v][1] -= ny * att

            damping = 0.82 * (1.0 - iteration / 50.0)
            for nid in node_ids:
                positions[nid][0] += forces[nid][0] * damping
                positions[nid][1] += forces[nid][1] * damping

        # 4. Center nodes & compute bounding box
        xs = [p[0] for p in positions.values()]
        ys = [p[1] for p in positions.values()]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        box_cx = (min_x + max_x) / 2.0
        box_cy = (min_y + max_y) / 2.0

        offset_x = (w / 2.0) - box_cx
        offset_y = (h / 2.0) - box_cy

        for nid in node_ids:
            self.node_positions[nid] = QPointF(positions[nid][0] + offset_x, positions[nid][1] + offset_y)

        # 5. Fit to Viewport
        graph_w = (max_x - min_x) + 160.0
        graph_h = (max_y - min_y) + 160.0
        avail_w = max(100.0, w - 80.0)
        avail_h = max(100.0, h - 80.0)
        fit_zoom = min(1.0, min(avail_w / graph_w, avail_h / graph_h))
        self._default_zoom = max(0.4, fit_zoom)

        if not self._has_custom_layout:
            self._zoom_factor = self._default_zoom
            self._pan_offset = QPointF(0.0, 0.0)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not self._has_custom_layout:
            self._layout_nodes()

    # --------------------------------------------------------------------------
    # Coordinate Transformations (Screen <-> World)
    # --------------------------------------------------------------------------

    def _widget_to_world(self, pt: QPointF) -> QPointF:
        """Convert widget screen coordinates to graph world coordinates."""
        cx = self.width() / 2.0
        cy = self.height() / 2.0
        wx = (pt.x() - self._pan_offset.x() - cx) / self._zoom_factor + cx
        wy = (pt.y() - self._pan_offset.y() - cy) / self._zoom_factor + cy
        return QPointF(wx, wy)

    def _world_to_widget(self, pt: QPointF) -> QPointF:
        """Convert graph world coordinates to widget screen coordinates."""
        cx = self.width() / 2.0
        cy = self.height() / 2.0
        sx = (pt.x() - cx) * self._zoom_factor + cx + self._pan_offset.x()
        sy = (pt.y() - cy) * self._zoom_factor + cy + self._pan_offset.y()
        return QPointF(sx, sy)

    # --------------------------------------------------------------------------
    # Rendering
    # --------------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()

        # Dark Background
        painter.fillRect(0, 0, w, h, QColor(COLOR_BG_DARK))

        if not self.nodes or not self.edges:
            painter.setPen(QColor(COLOR_TEXT_MUTED))
            font_empty = QFont()
            font_empty.setPointSize(11)
            painter.setFont(font_empty)
            painter.drawText(
                QRectF(0, 0, w, h),
                Qt.AlignmentFlag.AlignCenter,
                "Nessuna combinazione frequente di tracce rilevata con la soglia attuale.\n"
                "(Le transizioni compaiono solo se suonate in sequenza in almeno 2 serate)",
            )
            return

        # ----------------------------------------------------------------------
        # Apply Pan & Zoom Transformation
        # ----------------------------------------------------------------------
        painter.save()
        cx = w / 2.0
        cy = h / 2.0
        painter.translate(self._pan_offset.x() + cx, self._pan_offset.y() + cy)
        painter.scale(self._zoom_factor, self._zoom_factor)
        painter.translate(-cx, -cy)

        # 1. Draw Directed Edges (Arrows)
        max_edge_cnt = max((e.count for e in self.edges), default=1)

        for edge in self.edges:
            p1 = self.node_positions.get(edge.source_id)
            p2 = self.node_positions.get(edge.target_id)
            if not p1 or not p2:
                continue

            is_edge_hovered = self.hovered_edge == (edge.source_id, edge.target_id)
            is_node_connected = self.hovered_node_id in (edge.source_id, edge.target_id)

            # Weight and thickness
            weight_ratio = edge.count / float(max_edge_cnt) if max_edge_cnt > 0 else 0
            thickness = max(1.5, min(6.0, 1.5 + weight_ratio * 4.5))

            if is_edge_hovered or is_node_connected:
                edge_color = QColor(COLOR_AMBER)
                pen_edge = QPen(edge_color, thickness + 1.2)
            else:
                edge_color = QColor(0, 162, 255, int(90 + 130 * weight_ratio))
                pen_edge = QPen(edge_color, thickness)

            r1 = self._get_node_radius(edge.source_id)
            r2 = self._get_node_radius(edge.target_id)
            painter.setPen(pen_edge)
            self._draw_arrow(painter, p1, p2, r1, r2, thickness)

            # Draw edge count badge at midpoint
            mid_pt = QPointF((p1.x() + p2.x()) / 2.0, (p1.y() + p2.y()) / 2.0)
            painter.setBrush(QBrush(QColor(COLOR_BG_CARD)))
            painter.setPen(QPen(QColor(COLOR_BORDER if not is_edge_hovered else COLOR_AMBER), 1))
            badge_rect = QRectF(mid_pt.x() - 11, mid_pt.y() - 9, 22, 18)
            painter.drawRoundedRect(badge_rect, 4, 4)

            font_cnt = QFont()
            font_cnt.setPointSize(8)
            font_cnt.setBold(True)
            painter.setFont(font_cnt)
            painter.setPen(QColor(COLOR_TEXT_PRIMARY))
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, str(edge.count))

        # 2. Draw Nodes
        max_plays = max((nd.get("plays", 0) for nd in self.nodes.values()), default=1)

        font_node_title = QFont()
        font_node_title.setPointSize(8)
        font_node_title.setBold(True)

        for nid, nd in self.nodes.items():
            pos = self.node_positions.get(nid)
            if not pos:
                continue

            radius = self._get_node_radius(nid)

            is_node_hovered = self.hovered_node_id == nid
            is_node_selected = self.selected_node_id == nid

            # Color by Camelot ring (A = cyan/blue, B = emerald/amber)
            ring = nd.get("ring")
            if ring == "A":
                node_bg = QColor(14, 116, 144)  # Cyan
            elif ring == "B":
                node_bg = QColor(16, 149, 90)   # Emerald
            else:
                node_bg = QColor(COLOR_BLUE)

            if is_node_hovered:
                node_bg = node_bg.lighter(135)
                radius += 3.0

            painter.setBrush(QBrush(node_bg))
            border_col = QColor(COLOR_AMBER if is_node_selected else "#ffffff")
            painter.setPen(QPen(border_col, 2.0 if is_node_selected else 1.2))
            painter.drawEllipse(pos, radius, radius)

            # Node inner text: Key code (e.g. 8A)
            key_code = nd.get("key") or ""
            painter.setFont(font_node_title)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(
                QRectF(pos.x() - radius, pos.y() - radius, radius * 2, radius * 2),
                Qt.AlignmentFlag.AlignCenter,
                key_code[:3],
            )

            # Track title underneath node
            title_text = nd.get("title") or "Unknown"
            if len(title_text) > 18:
                title_text = title_text[:16] + "…"

            font_sub = QFont()
            font_sub.setPointSize(8)
            painter.setFont(font_sub)
            painter.setPen(QColor(COLOR_TEXT_PRIMARY))
            painter.drawText(
                QRectF(pos.x() - 65, pos.y() + radius + 3, 130, 16),
                Qt.AlignmentFlag.AlignCenter,
                title_text,
            )

        painter.restore()

        # ----------------------------------------------------------------------
        # Draw Floating Overlay Controls (Screen Coordinates)
        # ----------------------------------------------------------------------
        self._draw_overlay_controls(painter, w, h)

    def _draw_arrow(self, painter: QPainter, p1: QPointF, p2: QPointF, r1: float, r2: float, thickness: float) -> None:
        """Draw directed arrow connecting two nodes outside their radii."""
        dx = p2.x() - p1.x()
        dy = p2.y() - p1.y()
        dist = math.hypot(dx, dy)
        if dist < (r1 + r2 + 6.0):
            return

        target_offset = r2 + 4.0
        source_offset = r1 + 2.0

        p1_short = QPointF(p1.x() + (dx / dist) * source_offset, p1.y() + (dy / dist) * source_offset)
        p2_short = QPointF(p2.x() - (dx / dist) * target_offset, p2.y() - (dy / dist) * target_offset)

        painter.drawLine(p1_short, p2_short)

        arrow_size = 9.0 + thickness
        angle = math.atan2(dy, dx)

        arrow_p1 = QPointF(
            p2_short.x() - arrow_size * math.cos(angle - math.pi / 6.0),
            p2_short.y() - arrow_size * math.sin(angle - math.pi / 6.0),
        )
        arrow_p2 = QPointF(
            p2_short.x() - arrow_size * math.cos(angle + math.pi / 6.0),
            p2_short.y() - arrow_size * math.sin(angle + math.pi / 6.0),
        )

        arrow_head = QPolygonF([p2_short, arrow_p1, arrow_p2])
        painter.setBrush(painter.pen().brush())
        painter.drawPolygon(arrow_head)

    def _draw_overlay_controls(self, painter: QPainter, w: float, h: float) -> None:
        """Draw sleek interactive floating zoom and pan buttons."""
        # Top-Right Floating Toolbar
        btn_w = 26
        btn_h = 24
        spacing = 4
        total_bar_w = btn_w * 2 + 48 + 30 + spacing * 3
        bar_x = w - total_bar_w - 14
        bar_y = 12

        self._btn_zoom_out = QRectF(bar_x, bar_y, btn_w, btn_h)
        rect_zoom_lbl = QRectF(bar_x + btn_w + spacing, bar_y, 48, btn_h)
        self._btn_zoom_in = QRectF(bar_x + btn_w + 48 + spacing * 2, bar_y, btn_w, btn_h)
        self._btn_zoom_reset = QRectF(bar_x + btn_w * 2 + 48 + spacing * 3, bar_y, 30, btn_h)

        # Top-Left Criteria Pill / Label
        font_crit = QFont()
        font_crit.setPointSize(8)
        font_crit.setBold(False)
        painter.setFont(font_crit)
        painter.setPen(QColor(COLOR_TEXT_MUTED))
        painter.drawText(
            QRectF(14, 12, max(200.0, bar_x - 24), btn_h),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "🔗 Criterio Combo: passaggi diretti (A ➔ B) ripetuti in almeno 2 serate distinte",
        )

        font_btn = QFont()
        font_btn.setPointSize(9)
        font_btn.setBold(True)
        painter.setFont(font_btn)

        # Helper to draw a sleek rounded tool button
        def draw_tool_btn(rect: QRectF, text: str, tip: str = ""):
            painter.setBrush(QBrush(QColor("#1e293b")))
            painter.setPen(QPen(QColor(COLOR_BORDER), 1))
            painter.drawRoundedRect(rect, 4, 4)
            painter.setPen(QColor(COLOR_TEXT_PRIMARY))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

        draw_tool_btn(self._btn_zoom_out, "−")
        draw_tool_btn(self._btn_zoom_in, "+")
        draw_tool_btn(self._btn_zoom_reset, "⟲")

        # Zoom level indicator pill
        painter.setBrush(QBrush(QColor("#0f172a")))
        painter.setPen(QPen(QColor(COLOR_BORDER), 1))
        painter.drawRoundedRect(rect_zoom_lbl, 4, 4)
        painter.setPen(QColor(COLOR_TEXT_SECONDARY))
        font_zoom = QFont()
        font_zoom.setPointSize(8)
        painter.setFont(font_zoom)
        painter.drawText(rect_zoom_lbl, Qt.AlignmentFlag.AlignCenter, f"{int(round(self._zoom_factor * 100))}%")

        # Bottom-Left User Navigation Hint
        font_hint = QFont()
        font_hint.setPointSize(8)
        painter.setFont(font_hint)
        painter.setPen(QColor(COLOR_TEXT_MUTED))
        painter.drawText(
            QRectF(14, h - 22, w - 28, 16),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "🖱️ Trascina lo sfondo per spostarti • Rotella mouse per zoom • Trascina i nodi per disporli • Doppio clic per centrare",
        )

    # --------------------------------------------------------------------------
    # Interactive Mouse & Gesture Events (Pan, Zoom, Drag, Selection)
    # --------------------------------------------------------------------------

    def wheelEvent(self, event) -> None:
        """Zoom in/out centered around cursor position."""
        delta = event.angleDelta().y()
        if delta == 0:
            return

        factor = 1.15 if delta > 0 else (1.0 / 1.15)
        new_zoom = max(0.35, min(4.5, self._zoom_factor * factor))
        if new_zoom != self._zoom_factor:
            pos = event.position()
            cx = self.width() / 2.0
            cy = self.height() / 2.0
            scale_change = new_zoom / self._zoom_factor
            self._pan_offset = QPointF(
                (pos.x() - cx) * (1.0 - scale_change) + self._pan_offset.x() * scale_change,
                (pos.y() - cy) * (1.0 - scale_change) + self._pan_offset.y() * scale_change,
            )
            self._zoom_factor = new_zoom
            self.update()

    def mousePressEvent(self, event) -> None:
        pos = event.position()

        # Check Floating Toolbar Clicks
        if self._btn_zoom_in.contains(pos):
            self.zoom_in()
            return
        elif self._btn_zoom_out.contains(pos):
            self.zoom_out()
            return
        elif self._btn_zoom_reset.contains(pos):
            self.reset_view()
            return

        if event.button() == Qt.MouseButton.LeftButton:
            world_pos = self._widget_to_world(pos)

            # Check if clicked on a node to drag it
            clicked_node = None
            for nid, npos in self.node_positions.items():
                if (world_pos.x() - npos.x()) ** 2 + (world_pos.y() - npos.y()) ** 2 <= 484:  # radius ~22
                    clicked_node = nid
                    break

            if clicked_node:
                self._dragging_node_id = clicked_node
                self._drag_start_pos = pos
                self._node_moved = False
                self.selected_node_id = clicked_node
                self.update()
            else:
                # Clicked on background: start panning
                self._is_panning = True
                self._pan_start_pos = pos
                self.setCursor(Qt.CursorShape.ClosedHandCursor)

        elif event.button() in (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton):
            self._is_panning = True
            self._pan_start_pos = pos
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()

        # 1. Panning graph
        if self._is_panning and self._pan_start_pos is not None:
            delta = pos - self._pan_start_pos
            self._pan_offset += delta
            self._pan_start_pos = pos
            self.update()
            return

        # 2. Dragging a node
        if self._dragging_node_id is not None:
            world_pos = self._widget_to_world(pos)
            self.node_positions[self._dragging_node_id] = world_pos
            self._has_custom_layout = True
            if self._drag_start_pos and (pos.x() - self._drag_start_pos.x()) ** 2 + (pos.y() - self._drag_start_pos.y()) ** 2 > 16:
                self._node_moved = True
            self.update()
            return

        # 3. Floating Button Hover State
        if (
            self._btn_zoom_in.contains(pos)
            or self._btn_zoom_out.contains(pos)
            or self._btn_zoom_reset.contains(pos)
        ):
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            return

        # 4. Hovering elements in world coordinates
        world_pos = self._widget_to_world(pos)
        old_node = self.hovered_node_id
        old_edge = self.hovered_edge
        self.hovered_node_id = None
        self.hovered_edge = None

        # Check nodes
        for nid, npos in self.node_positions.items():
            if (world_pos.x() - npos.x()) ** 2 + (world_pos.y() - npos.y()) ** 2 <= 441:  # radius 21
                self.hovered_node_id = nid
                break

        # Check edges
        if not self.hovered_node_id:
            for edge in self.edges:
                p1 = self.node_positions.get(edge.source_id)
                p2 = self.node_positions.get(edge.target_id)
                if p1 and p2:
                    mid_pt = QPointF((p1.x() + p2.x()) / 2.0, (p1.y() + p2.y()) / 2.0)
                    if (world_pos.x() - mid_pt.x()) ** 2 + (world_pos.y() - mid_pt.y()) ** 2 <= 196:
                        self.hovered_edge = (edge.source_id, edge.target_id)
                        break

        if self.hovered_node_id:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        elif self.hovered_edge:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

        if self.hovered_node_id != old_node or self.hovered_edge != old_edge:
            self.update()
            pos_pt = pos.toPoint()
            if self.hovered_node_id:
                nd = self.nodes[self.hovered_node_id]
                tip = (
                    f"🎵 <b>{nd.get('title')}</b><br/>"
                    f"• Artista: <b>{nd.get('artist')}</b><br/>"
                    f"• Tonalità: <b>{nd.get('key')}</b> | BPM: <b>{nd.get('bpm')}</b><br/>"
                    f"• Esecuzioni live totali: <b>{nd.get('plays')}</b><br/><br/>"
                    f"<i>(Trascina per spostare il nodo • Clicca per filtrare)</i>"
                )
                QToolTip.showText(self.mapToGlobal(pos_pt), tip, self)
            elif self.hovered_edge:
                src_id, tgt_id = self.hovered_edge
                edge = next((e for e in self.edges if e.source_id == src_id and e.target_id == tgt_id), None)
                if edge:
                    sessions_str = "<br/>• ".join(edge.session_names[:4])
                    if len(edge.session_names) > 4:
                        sessions_str += f"<br/>• ...e altri {len(edge.session_names) - 4} set"
                    tip = (
                        f"<b>Combo Automatica Rilevata:</b><br/>"
                        f"🎵 <b>{edge.source_title}</b><br/>"
                        f"&nbsp;&nbsp;&nbsp;&nbsp;➔ <b>{edge.target_title}</b><br/>"
                        f"• Suonata insieme: <b>{edge.count} volte</b> in:<br/>• {sessions_str}<br/><br/>"
                        f"<i>Clicca per visualizzare la coppia</i>"
                    )
                    QToolTip.showText(self.mapToGlobal(pos_pt), tip, self)
            else:
                QToolTip.hideText()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._dragging_node_id is not None:
                nid = self._dragging_node_id
                self._dragging_node_id = None
                if not self._node_moved:
                    self.node_clicked.emit(nid)
            elif self._is_panning:
                self._is_panning = False
                self.setCursor(Qt.CursorShape.ArrowCursor)
            elif self.hovered_edge:
                src_id, tgt_id = self.hovered_edge
                edge = next((e for e in self.edges if e.source_id == src_id and e.target_id == tgt_id), None)
                if edge:
                    self.edge_clicked.emit(src_id, tgt_id, edge.count)

        if self._is_panning:
            self._is_panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseDoubleClickEvent(self, event) -> None:
        """Reset pan and zoom on double click."""
        self.reset_view()

    def reset_view(self) -> None:
        """Reset view zoom, pan and re-center nodes."""
        self._zoom_factor = getattr(self, "_default_zoom", 1.0)
        self._pan_offset = QPointF(0.0, 0.0)
        self._has_custom_layout = False
        self._layout_nodes()
        self.update()

    def zoom_in(self) -> None:
        """Zoom in toward center."""
        self._zoom_factor = min(4.5, self._zoom_factor * 1.25)
        self.update()

    def zoom_out(self) -> None:
        """Zoom out from center."""
        self._zoom_factor = max(0.35, self._zoom_factor / 1.25)
        self.update()
