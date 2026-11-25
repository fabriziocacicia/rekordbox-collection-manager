"""Transition & Combos Network Graph Widget - Pan & Zoom Interactive Network.

Interactive directed network graph visualizing habitual track sequences (A -> B combos).
Highlights rigid mixing habits, fixed pairs, and transitions executed repeatedly in History.
Supports smooth mouse-wheel zooming, click-and-drag panning, and individual node dragging.
"""

import math
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional, Tuple
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QToolTip, QWidget

from rekordbox_collection_manager.analytics import TransitionEdgeData
from rekordbox_collection_manager.gui.theme import (
    COLOR_AMBER,
    COLOR_BG_CARD,
    COLOR_BG_DARK,
    COLOR_BLUE,
    COLOR_BORDER,
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
        self.setMinimumSize(460, 240)

        self.edges: List[TransitionEdgeData] = []
        self.nodes: Dict[str, Dict[str, Any]] = {}

        self.node_positions: Dict[str, QPointF] = {}
        self.hovered_node_id: Optional[str] = None
        self.hovered_edge: Optional[Tuple[str, str]] = None
        self.selected_node_id: Optional[str] = None
        self.selected_edge: Optional[Tuple[str, str]] = None

        # Pan & Zoom State
        self._zoom_factor: float = 1.0
        self._default_zoom: float = 1.0
        self._pan_offset: QPointF = QPointF(0.0, 0.0)
        self._is_panning: bool = False
        self._pan_start_pos: Optional[QPointF] = None

        # Node & Edge Press / Dragging State
        self._dragging_node_id: Optional[str] = None
        self._drag_start_pos: Optional[QPointF] = None
        self._node_moved: bool = False
        self._pressing_edge: Optional[Tuple[str, str]] = None
        self._edge_press_pos: Optional[QPointF] = None
        self._has_custom_layout: bool = False

        # Overlay Button Rects (cached in screen coordinates)
        self._btn_zoom_in = QRectF()
        self._btn_zoom_out = QRectF()
        self._btn_zoom_reset = QRectF()
        self._hovered_tool_btn: Optional[str] = None

    def set_data(self, edges: List[TransitionEdgeData], nodes: Dict[str, Dict[str, Any]]) -> None:
        self.edges = edges or []
        self.nodes = nodes or {}
        self.selected_node_id = None
        self.selected_edge = None
        self.hovered_node_id = None
        self.hovered_edge = None
        self._has_custom_layout = False
        self._layout_nodes()
        self.update()

    def clear_selection(self) -> None:
        if self.selected_node_id is not None or self.selected_edge is not None:
            self.selected_node_id = None
            self.selected_edge = None
            self.update()

    def _get_node_radius(self, nid: str) -> float:
        """Calculate visual node radius based on total play count."""
        nd = self.nodes.get(nid, {})
        plays = nd.get("plays", 0)
        max_plays = max((n.get("plays", 0) for n in self.nodes.values()), default=1)
        play_ratio = min(1.0, plays / float(max_plays)) if max_plays > 0 else 0
        return max(13.0, min(24.0, 13.0 + play_ratio * 11.0))

    def _layout_component_local(
        self,
        comp: List[str],
        out_edges: Dict[str, set],
        in_edges: Dict[str, set],
        comp_edges: List[TransitionEdgeData],
    ) -> Dict[str, List[float]]:
        """Compute local coordinates centered around (0, 0) for a single connected component."""
        positions: Dict[str, List[float]] = {}
        k = len(comp)
        if k == 1:
            positions[comp[0]] = [0.0, 0.0]
            return positions

        if k == 2:
            u, v = comp[0], comp[1]
            src, tgt = (u, v) if v in out_edges[u] else (v, u)
            positions[src] = [-72.0, 0.0]
            positions[tgt] = [72.0, 0.0]
            return positions

        # Try clean horizontal DAG layering for acyclic components
        comp_set = set(comp)
        in_deg = {nid: len(in_edges[nid] & comp_set) for nid in comp}
        q = deque([nid for nid in comp if in_deg[nid] == 0])
        topo_order: List[str] = []
        depth: Dict[str, int] = {nid: 0 for nid in comp}

        while q:
            u = q.popleft()
            topo_order.append(u)
            for v in out_edges[u] & comp_set:
                depth[v] = max(depth[v], depth[u] + 1)
                in_deg[v] -= 1
                if in_deg[v] == 0:
                    q.append(v)

        if len(topo_order) == k and k <= 10:
            # Pull source branches rightwards toward their immediate targets to eliminate long-edge crossings
            max_layer = max(depth.values(), default=0)
            for u in reversed(topo_order):
                children = out_edges[u] & comp_set
                if children:
                    depth[u] = min(depth[v] for v in children) - 1
            min_layer = min(depth.values(), default=0)
            layers: Dict[int, List[str]] = defaultdict(list)
            for nid in topo_order:
                layers[depth[nid] - min_layer].append(nid)

            x_step = 148.0
            y_step = 104.0
            n_layers = max(layers.keys(), default=0) + 1
            for l_idx, nodes_in_layer in layers.items():
                x = (l_idx - (n_layers - 1) / 2.0) * x_step
                cnt = len(nodes_in_layer)
                for row_idx, nid in enumerate(nodes_in_layer):
                    y = (row_idx - (cnt - 1) / 2.0) * y_step
                    positions[nid] = [x, y]
            return positions

        # Fallback for cyclic or large components: local circle + spring relaxation
        sub_r = max(75.0, k * 28.0)
        for s_idx, nid in enumerate(comp):
            s_angle = (2.0 * math.pi * s_idx) / k
            positions[nid] = [sub_r * math.cos(s_angle), sub_r * math.sin(s_angle)]

        TARGET_DIST = 130.0
        for iteration in range(40):
            forces = {nid: [0.0, 0.0] for nid in comp}
            for i in range(k):
                id1 = comp[i]
                p1 = positions[id1]
                for j in range(i + 1, k):
                    id2 = comp[j]
                    p2 = positions[id2]
                    dx = p1[0] - p2[0]
                    dy = p1[1] - p2[1]
                    dist = math.hypot(dx, dy)
                    if dist < 0.001:
                        dx, dy, dist = 0.01, 0.01, 0.014
                    if dist < TARGET_DIST:
                        rep = (TARGET_DIST - dist) / dist * 0.35
                        forces[id1][0] += dx * rep
                        forces[id1][1] += dy * rep
                        forces[id2][0] -= dx * rep
                        forces[id2][1] -= dy * rep
            for edge in comp_edges:
                p1 = positions[edge.source_id]
                p2 = positions[edge.target_id]
                dx = p2[0] - p1[0]
                dy = p2[1] - p1[1]
                dist = math.hypot(dx, dy)
                if dist > 125.0:
                    att = (dist - 125.0) * 0.12
                    nx, ny = dx / dist, dy / dist
                    forces[edge.source_id][0] += nx * att
                    forces[edge.source_id][1] += ny * att
                    forces[edge.target_id][0] -= nx * att
                    forces[edge.target_id][1] -= ny * att

            damping = 0.82 * (1.0 - iteration / 50.0)
            for nid in comp:
                positions[nid][0] += forces[nid][0] * damping
                positions[nid][1] += forces[nid][1] * damping

        xs = [p[0] for p in positions.values()]
        ys = [p[1] for p in positions.values()]
        cx = (min(xs) + max(xs)) / 2.0
        cy = (min(ys) + max(ys)) / 2.0
        for nid in comp:
            positions[nid][0] -= cx
            positions[nid][1] -= cy
        return positions

    def _layout_nodes(self) -> None:
        """Arrange connected combo components in a compact, readable left-to-right shelf grid."""
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
            self._default_zoom = 1.0
            self._pan_offset = QPointF(0.0, 0.0)
            return

        # 1. Detect connected components
        adj: Dict[str, set] = defaultdict(set)
        out_edges: Dict[str, set] = defaultdict(set)
        in_edges: Dict[str, set] = defaultdict(set)
        for edge in self.edges:
            u, v = edge.source_id, edge.target_id
            adj[u].add(v)
            adj[v].add(u)
            out_edges[u].add(v)
            in_edges[v].add(u)

        visited = set()
        components: List[List[str]] = []
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

        # Order multi-node hubs first, then by maximum combo frequency descending
        def comp_sort_key(c: List[str]) -> Tuple[int, int]:
            c_set = set(c)
            max_cnt = max(
                (e.count for e in self.edges if e.source_id in c_set and e.target_id in c_set),
                default=0,
            )
            return (len(c), max_cnt)

        components.sort(key=comp_sort_key, reverse=True)

        # 2. Compute local bounding box for each component
        comp_boxes: List[Tuple[Dict[str, List[float]], float, float, float, float]] = []
        pad_x = 68.0
        pad_y_top = 28.0
        pad_y_bot = 42.0

        for comp in components:
            c_set = set(comp)
            comp_edges = [e for e in self.edges if e.source_id in c_set and e.target_id in c_set]
            local_pos = self._layout_component_local(comp, out_edges, in_edges, comp_edges)
            xs = [p[0] for p in local_pos.values()]
            ys = [p[1] for p in local_pos.values()]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)
            box_w = (max_x - min_x) + pad_x * 2.0
            box_h = (max_y - min_y) + pad_y_top + pad_y_bot
            comp_boxes.append((local_pos, min_x - pad_x, min_y - pad_y_top, box_w, box_h))

        # 3. Choose shelf-packing row width that maximizes readable viewport zoom
        top_reserve = 56.0
        bot_reserve = 28.0
        avail_w = max(100.0, w - 32.0)
        avail_h = max(100.0, h - top_reserve - bot_reserve - 12.0)

        max_comp_w = max((b[3] for b in comp_boxes), default=300.0)
        sum_comp_w = sum(b[3] for b in comp_boxes) + 18.0 * max(0, len(comp_boxes) - 1)
        gap_x = 18.0
        gap_y = 14.0

        def pack_rows(row_limit: float):
            packed = []
            curr = []
            curr_w = 0.0
            for box in comp_boxes:
                bw = box[3]
                if curr and (curr_w + gap_x + bw) > row_limit:
                    packed.append(curr)
                    curr = [box]
                    curr_w = bw
                else:
                    if curr:
                        curr_w += gap_x
                    curr.append(box)
                    curr_w += bw
            if curr:
                packed.append(curr)
            total_w = max((sum(b[3] for b in r) + gap_x * max(0, len(r) - 1) for r in packed), default=max_comp_w)
            total_h = sum(max(b[4] for b in r) for r in packed) + gap_y * max(0, len(packed) - 1)
            zoom_score = min(avail_w / max(1.0, total_w), avail_h / max(1.0, total_h))
            return zoom_score, packed

        best_score = -1.0
        rows = []
        steps = 16
        for s_i in range(steps + 1):
            cand_w = max_comp_w + (sum_comp_w - max_comp_w) * (s_i / float(steps))
            score, cand_rows = pack_rows(cand_w)
            if score > best_score:
                best_score = score
                rows = cand_rows

        # Place packed rows centered in world coordinates
        positions: Dict[str, List[float]] = {}
        curr_y = 0.0
        for row in rows:
            row_w = sum(b[3] for b in row) + gap_x * max(0, len(row) - 1)
            row_h = max(b[4] for b in row)
            curr_x = -row_w / 2.0
            for local_pos, bx, by, bw, bh in row:
                y_offset = curr_y + (row_h - bh) / 2.0 - by
                x_offset = curr_x - bx
                for nid, (lx, ly) in local_pos.items():
                    positions[nid] = [lx + x_offset, ly + y_offset]
                curr_x += bw + gap_x
            curr_y += row_h + gap_y

        # 4. Center overall bounding box inside viewport (below top overlay, above bottom hint)
        xs = [p[0] for p in positions.values()]
        ys = [p[1] for p in positions.values()]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        box_cx = (min_x + max_x) / 2.0
        box_cy = (min_y + max_y) / 2.0

        view_cx = w / 2.0
        view_cy = top_reserve + (h - top_reserve - bot_reserve) / 2.0

        offset_x = view_cx - box_cx
        offset_y = view_cy - box_cy

        for nid in node_ids:
            self.node_positions[nid] = QPointF(positions[nid][0] + offset_x, positions[nid][1] + offset_y)

        # 5. Fit to Viewport cleanly
        graph_w = (max_x - min_x) + 144.0
        graph_h = (max_y - min_y) + 92.0
        fit_zoom = min(1.05, min(avail_w / graph_w, avail_h / graph_h))
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
                "No frequent track transitions detected with current threshold.\n"
                "(Transitions appear only when played in sequence across at least 2 distinct gigs)",
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

            edge_key = (edge.source_id, edge.target_id)
            is_edge_hovered = self.hovered_edge == edge_key
            is_edge_selected = self.selected_edge == edge_key
            is_node_connected = (
                self.hovered_node_id in edge_key or self.selected_node_id in edge_key
            )

            weight_ratio = edge.count / float(max_edge_cnt) if max_edge_cnt > 0 else 0
            thickness = max(1.5, min(6.0, 1.5 + weight_ratio * 4.5))

            if is_edge_hovered or is_edge_selected or is_node_connected:
                edge_color = QColor(COLOR_AMBER)
                pen_edge = QPen(edge_color, thickness + 1.2)
            else:
                edge_color = QColor(0, 162, 255, int(110 + 125 * weight_ratio))
                pen_edge = QPen(edge_color, thickness)

            r1 = self._get_node_radius(edge.source_id)
            r2 = self._get_node_radius(edge.target_id)
            painter.setPen(pen_edge)
            self._draw_arrow(painter, p1, p2, r1, r2, thickness)

            # Draw edge count badge at midpoint
            mid_pt = QPointF((p1.x() + p2.x()) / 2.0, (p1.y() + p2.y()) / 2.0)
            active_badge = is_edge_hovered or is_edge_selected
            painter.setBrush(QBrush(QColor("#382305" if active_badge else COLOR_BG_CARD)))
            painter.setPen(QPen(QColor(COLOR_AMBER if active_badge else COLOR_BORDER), 1.5 if active_badge else 1.0))
            badge_rect = QRectF(mid_pt.x() - 11, mid_pt.y() - 9, 22, 18)
            painter.drawRoundedRect(badge_rect, 4, 4)

            font_cnt = QFont()
            font_cnt.setPointSize(8)
            font_cnt.setBold(True)
            painter.setFont(font_cnt)
            painter.setPen(QColor(COLOR_AMBER if active_badge else COLOR_TEXT_PRIMARY))
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, str(edge.count))

        # 2. Draw Nodes
        font_node_title = QFont()
        font_node_title.setPointSize(8)
        font_node_title.setBold(True)

        font_sub = QFont()
        font_sub.setPointSize(8)
        fm_sub = QFontMetricsF(font_sub)

        for nid, nd in self.nodes.items():
            pos = self.node_positions.get(nid)
            if not pos:
                continue

            radius = self._get_node_radius(nid)
            is_node_hovered = self.hovered_node_id == nid
            is_node_selected = self.selected_node_id == nid

            # Color by Camelot ring (A = cyan/blue, B = emerald)
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
            painter.setPen(QPen(border_col, 2.2 if is_node_selected else 1.2))
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

            # Track title underneath node with crisp dark pill backdrop
            title_text = nd.get("title") or "Unknown"
            if len(title_text) > 18:
                title_text = title_text[:16] + "…"

            label_w = min(132.0, fm_sub.horizontalAdvance(title_text) + 12.0)
            label_rect = QRectF(pos.x() - label_w / 2.0, pos.y() + radius + 4, label_w, 17)
            painter.setBrush(QBrush(QColor(11, 15, 25, 220)))
            painter.setPen(QPen(QColor(COLOR_AMBER if is_node_selected else COLOR_BORDER), 0.8) if (is_node_hovered or is_node_selected) else Qt.PenStyle.NoPen)
            painter.drawRoundedRect(label_rect, 4, 4)

            painter.setFont(font_sub)
            painter.setPen(QColor("#ffffff" if (is_node_hovered or is_node_selected) else COLOR_TEXT_PRIMARY))
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, title_text)

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
        """Draw floating zoom/fit controls, stats criterion bar, and ring legend."""
        # Top-Right Floating Toolbar
        btn_w = 26
        fit_w = 34
        btn_h = 24
        spacing = 4
        total_bar_w = btn_w * 2 + 48 + fit_w + spacing * 3
        bar_x = w - total_bar_w - 14
        bar_y = 10

        self._btn_zoom_out = QRectF(bar_x, bar_y, btn_w, btn_h)
        rect_zoom_lbl = QRectF(bar_x + btn_w + spacing, bar_y, 48, btn_h)
        self._btn_zoom_in = QRectF(bar_x + btn_w + 48 + spacing * 2, bar_y, btn_w, btn_h)
        self._btn_zoom_reset = QRectF(bar_x + btn_w * 2 + 48 + spacing * 3, bar_y, fit_w, btn_h)

        # Top-Left Criteria & Count Summary
        font_crit = QFont()
        font_crit.setPointSize(8)
        painter.setFont(font_crit)
        painter.setPen(QColor(COLOR_TEXT_SECONDARY))
        painter.drawText(
            QRectF(14, 8, max(160.0, bar_x - 24), 16),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            f"🔗 {len(self.edges)} staple combos across {len(self.nodes)} tracks (repeated in ≥ 2 gigs)",
        )

        # Top-Left Compact Legend Row
        leg_y = 29.0
        leg_x = 14.0
        font_leg = QFont()
        font_leg.setPointSize(8)
        painter.setFont(font_leg)
        for label, col_hex in [("Minor (A)", "#0e7490"), ("Major (B)", "#10955a")]:
            painter.setBrush(QBrush(QColor(col_hex)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QRectF(leg_x, leg_y + 3, 8, 8))
            painter.setPen(QColor(COLOR_TEXT_MUTED))
            painter.drawText(QRectF(leg_x + 12, leg_y, 64, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
            leg_x += 78.0
        if bar_x - leg_x > 160:
            painter.setPen(QColor(COLOR_TEXT_MUTED))
            painter.drawText(
                QRectF(leg_x, leg_y, bar_x - leg_x - 10, 14),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                "• Size = Plays  • Badge = Combo Count",
            )

        # Helper to draw interactive tool button with hover state
        def draw_tool_btn(rect: QRectF, text: str, btn_key: str, font_pt: int = 9):
            is_hov = self._hovered_tool_btn == btn_key
            painter.setBrush(QBrush(QColor("#2b384e" if is_hov else "#1e293b")))
            painter.setPen(QPen(QColor(COLOR_BLUE if is_hov else COLOR_BORDER), 1))
            painter.drawRoundedRect(rect, 4, 4)
            f_b = QFont()
            f_b.setPointSize(font_pt)
            f_b.setBold(True)
            painter.setFont(f_b)
            painter.setPen(QColor("#ffffff" if is_hov else COLOR_TEXT_PRIMARY))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

        draw_tool_btn(self._btn_zoom_out, "−", "out", 10)
        draw_tool_btn(self._btn_zoom_in, "+", "in", 10)
        draw_tool_btn(self._btn_zoom_reset, "Fit", "reset", 8)

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
            "Drag background to pan • Wheel to zoom • Drag nodes to arrange • Click node or badge to filter • Double-click to fit",
        )

    # --------------------------------------------------------------------------
    # Interactive Mouse & Gesture Events (Pan, Zoom, Drag, Selection)
    # --------------------------------------------------------------------------

    def _hit_test_edge(self, world_pos: QPointF) -> Optional[Tuple[str, str]]:
        """Find edge whose midpoint badge/line center is near world_pos."""
        for edge in self.edges:
            p1 = self.node_positions.get(edge.source_id)
            p2 = self.node_positions.get(edge.target_id)
            if p1 and p2:
                mid_pt = QPointF((p1.x() + p2.x()) / 2.0, (p1.y() + p2.y()) / 2.0)
                if (world_pos.x() - mid_pt.x()) ** 2 + (world_pos.y() - mid_pt.y()) ** 2 <= 256:  # radius 16
                    return (edge.source_id, edge.target_id)
        return None

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

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        changed = (
            self.hovered_node_id is not None
            or self.hovered_edge is not None
            or self._hovered_tool_btn is not None
        )
        self.hovered_node_id = None
        self.hovered_edge = None
        self._hovered_tool_btn = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        QToolTip.hideText()
        if changed:
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

            # 1. Check node hit
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
                self.selected_edge = None
                self.update()
                return

            # 2. Check edge/badge hit before falling back to background pan
            clicked_edge = self._hit_test_edge(world_pos)
            if clicked_edge:
                self._pressing_edge = clicked_edge
                self._edge_press_pos = pos
                return

            # 3. Background pan
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

        # 2. If user pressed an edge badge and drags > 4px, seamlessly switch to panning
        if self._pressing_edge is not None and self._edge_press_pos is not None:
            if (pos.x() - self._edge_press_pos.x()) ** 2 + (pos.y() - self._edge_press_pos.y()) ** 2 > 16:
                self._pressing_edge = None
                self._edge_press_pos = None
                self._is_panning = True
                self._pan_start_pos = pos
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return

        # 3. Dragging a node
        if self._dragging_node_id is not None:
            world_pos = self._widget_to_world(pos)
            self.node_positions[self._dragging_node_id] = world_pos
            self._has_custom_layout = True
            if self._drag_start_pos and (pos.x() - self._drag_start_pos.x()) ** 2 + (pos.y() - self._drag_start_pos.y()) ** 2 > 16:
                self._node_moved = True
            self.update()
            return

        # 4. Floating Button Hover State
        old_btn = self._hovered_tool_btn
        if self._btn_zoom_in.contains(pos):
            self._hovered_tool_btn = "in"
        elif self._btn_zoom_out.contains(pos):
            self._hovered_tool_btn = "out"
        elif self._btn_zoom_reset.contains(pos):
            self._hovered_tool_btn = "reset"
        else:
            self._hovered_tool_btn = None

        if self._hovered_tool_btn is not None:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            if self._hovered_tool_btn != old_btn:
                self.update()
                tips = {"in": "Zoom in", "out": "Zoom out", "reset": "Fit graph to view"}
                QToolTip.showText(self.mapToGlobal(pos.toPoint()), tips[self._hovered_tool_btn], self)
            return
        elif old_btn is not None:
            self.update()
            QToolTip.hideText()

        # 5. Hovering elements in world coordinates
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
            self.hovered_edge = self._hit_test_edge(world_pos)

        if self.hovered_node_id or self.hovered_edge:
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
                    f"• Artist: <b>{nd.get('artist')}</b><br/>"
                    f"• Key: <b>{nd.get('key')}</b> | BPM: <b>{nd.get('bpm')}</b><br/>"
                    f"• Total live plays: <b>{nd.get('plays')}</b><br/><br/>"
                    f"<i>(Drag to move node • Click to view combo hub)</i>"
                )
                QToolTip.showText(self.mapToGlobal(pos_pt), tip, self)
            elif self.hovered_edge:
                src_id, tgt_id = self.hovered_edge
                edge = next((e for e in self.edges if e.source_id == src_id and e.target_id == tgt_id), None)
                if edge:
                    sessions_str = "<br/>• ".join(edge.session_names[:4])
                    if len(edge.session_names) > 4:
                        sessions_str += f"<br/>• ...and {len(edge.session_names) - 4} more sets"
                    tip = (
                        f"<b>Automatic Combo Detected:</b><br/>"
                        f"🎵 <b>{edge.source_title}</b><br/>"
                        f"&nbsp;&nbsp;&nbsp;&nbsp;➔ <b>{edge.target_title}</b><br/>"
                        f"• Played together: <b>{edge.count} times</b> in:<br/>• {sessions_str}<br/><br/>"
                        f"<i>Click to view pair in track table</i>"
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
            elif self._pressing_edge is not None:
                src_id, tgt_id = self._pressing_edge
                self._pressing_edge = None
                self._edge_press_pos = None
                edge = next((e for e in self.edges if e.source_id == src_id and e.target_id == tgt_id), None)
                if edge:
                    self.selected_edge = (src_id, tgt_id)
                    self.selected_node_id = None
                    self.update()
                    self.edge_clicked.emit(src_id, tgt_id, edge.count)
            elif self._is_panning:
                self._is_panning = False
                self.setCursor(Qt.CursorShape.ArrowCursor)

        if self._is_panning:
            self._is_panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseDoubleClickEvent(self, event) -> None:
        """Reset pan and zoom on double click outside floating toolbar buttons."""
        pos = event.position()
        if (
            self._btn_zoom_in.contains(pos)
            or self._btn_zoom_out.contains(pos)
            or self._btn_zoom_reset.contains(pos)
        ):
            return
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
