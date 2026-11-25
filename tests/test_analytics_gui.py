"""Tests for Analytics GUI, AnalyticsView, and customized visualization widgets."""

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from rekordbox_collection_manager.analytics import compute_analytics
from rekordbox_collection_manager.gui.analytics_view import AnalyticsView
from rekordbox_collection_manager.gui.analytics_widgets import (
    CamelotWheelWidget,
    CrateHealthMatrixWidget,
    SetTurnoverWidget,
    SurvivalDecayWidget,
    TransitionGraphWidget,
)

app = QApplication.instance()
if not app:
    app = QApplication([])


class TestAnalyticsGUI(unittest.TestCase):
    """Test suite for Analytics widgets and integrated AnalyticsView."""

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        with self.engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE djmdKey (ID VARCHAR(255) PRIMARY KEY, ScaleName VARCHAR(255));")
            conn.exec_driver_sql("CREATE TABLE djmdArtist (ID VARCHAR(255) PRIMARY KEY, Name VARCHAR(255));")
            conn.exec_driver_sql("CREATE TABLE djmdAlbum (ID VARCHAR(255) PRIMARY KEY, Name VARCHAR(255));")
            conn.exec_driver_sql("""
                CREATE TABLE djmdPlaylist (
                    ID VARCHAR(255) PRIMARY KEY,
                    Seq INTEGER,
                    Name VARCHAR(255),
                    Attribute INTEGER DEFAULT 0,
                    ParentID VARCHAR(255),
                    SmartList TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdSongPlaylist (
                    ID VARCHAR(255) PRIMARY KEY,
                    PlaylistID VARCHAR(255),
                    ContentID VARCHAR(255),
                    TrackNo INTEGER,
                    created_at TEXT,
                    updated_at TEXT,
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdContent (
                    ID VARCHAR(255) PRIMARY KEY,
                    Title VARCHAR(255),
                    ArtistID VARCHAR(255),
                    SrcArtistName VARCHAR(255),
                    AlbumID VARCHAR(255),
                    KeyID VARCHAR(255),
                    BPM INTEGER,
                    Length INTEGER,
                    TotalTime INTEGER,
                    Rating INTEGER DEFAULT 0,
                    DJPlayCount INTEGER DEFAULT 0,
                    created_at TEXT,
                    FolderPath VARCHAR(1024),
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdHistory (
                    ID VARCHAR(255) PRIMARY KEY,
                    Name VARCHAR(255),
                    DateCreated TEXT,
                    ParentID VARCHAR(255),
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdSongHistory (
                    ID VARCHAR(255) PRIMARY KEY,
                    HistoryID VARCHAR(255),
                    ContentID VARCHAR(255),
                    HistoryOrder INTEGER,
                    created_at TEXT,
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)

            conn.exec_driver_sql("INSERT INTO djmdKey VALUES ('k1', '8A');")
            conn.exec_driver_sql("INSERT INTO djmdKey VALUES ('k2', '8B');")
            conn.exec_driver_sql("INSERT INTO djmdContent (ID, Title, KeyID, BPM, Length, TotalTime, DJPlayCount, created_at, FolderPath) VALUES ('t1', 'Track 1', 'k1', 12800, 300, 300, 15, '2025-01-01', '/t1.mp3');")
            conn.exec_driver_sql("INSERT INTO djmdContent (ID, Title, KeyID, BPM, Length, TotalTime, DJPlayCount, created_at, FolderPath) VALUES ('t2', 'Track 2', 'k2', 12600, 240, 240, 0, '2025-01-01', '/t2.mp3');")
            conn.exec_driver_sql("INSERT INTO djmdHistory VALUES ('h1', 'Gig 1', '2025-01-10', 'root', 0);")
            for i in range(1, 6):
                conn.exec_driver_sql(f"INSERT INTO djmdSongHistory VALUES ('sh_{i}', 'h1', 't1', {i}, '2025-01-10', 0);")

    def test_analytics_widgets_direct(self):
        """Test individual widget data assignment and hit testing."""
        snapshot = compute_analytics(self.engine, min_session_duration_sec=0, min_session_tracks=1)

        # 1. Crate Health Matrix Widget
        matrix_widget = CrateHealthMatrixWidget()
        matrix_widget.resize(500, 300)
        matrix_widget.set_data(snapshot.health_matrix)
        self.assertEqual(len(matrix_widget.exposure_brackets), 4)

        # 2. Camelot Wheel Widget
        wheel_widget = CamelotWheelWidget()
        wheel_widget.resize(400, 400)
        wheel_widget.set_data(snapshot.camelot_slices)
        wheel_widget.select_slice("8A")
        self.assertEqual(wheel_widget.selected_code, "8A")
        self.assertGreaterEqual(wheel_widget.max_catalog_count, 1)
        col_empty = wheel_widget._get_slice_color(0, is_minor=True)
        col_active = wheel_widget._get_slice_color(10, is_minor=False)
        self.assertNotEqual(col_empty, col_active)

        # 3. Turnover Widget
        turnover_widget = SetTurnoverWidget()
        turnover_widget.resize(500, 300)
        turnover_widget.set_data(snapshot.turnover_series)
        self.assertEqual(len(turnover_widget.series), 1)

        # Test dense series (55 sessions) to verify subsampling and legend tooltips
        from rekordbox_collection_manager.analytics import SessionTurnoverData
        mock_series = [
            SessionTurnoverData(
                session_id=f"sess_{i}",
                session_index=i + 1,
                name=f"Set #{i + 1}",
                date_str=f"2024-{(i % 12) + 1:02d}-01",
                total_tracks=20,
                peak_bangers_count=4,
                peak_bangers_pct=20.0,
                core_rotation_count=6,
                core_rotation_pct=30.0,
                untested_count=6,
                untested_pct=30.0,
                deep_cuts_cluster_count=4,
                deep_cuts_cluster_pct=20.0,
                burnout_count=0,
                burnout_pct=0.0,
                standard_count=0,
                standard_pct=0.0,
                new_entries_count=5,
                new_entries_pct=25.0,
                in_rotation_count=10,
                in_rotation_pct=50.0,
                deep_cuts_count=5,
                deep_cuts_pct=25.0,
                one_shot_count=0,
                one_shot_pct=0.0,
                fresh_tracks_count=15,
                fresh_tracks_pct=75.0,
                repeated_prev_count=5,
                repeated_prev_pct=25.0,
                archetype="Balanced",
                archetype_desc="Balanced set",
                archetype_icon="⚖️",
                archetype_tags=["balanced"],
            )
            for i in range(55)
        ]
        turnover_widget.set_data(mock_series)
        self.assertEqual(len(turnover_widget.series), 55)
        pix = QPixmap(800, 400)
        turnover_widget.canvas.render(pix)
        self.assertEqual(len(turnover_widget.canvas._legend_items), 5)
        self.assertIn("Peak Bangers", turnover_widget.canvas._legend_items[0][1])
        self.assertIn("Set Staples", turnover_widget.canvas._legend_items[0][3])

        # Test hover on legend item
        first_legend_rect = turnover_widget.canvas._legend_items[0][0]
        ev_move = QMouseEvent(
            QEvent.Type.MouseMove,
            first_legend_rect.center(),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        turnover_widget.canvas.mouseMoveEvent(ev_move)
        ev_leave = QEvent(QEvent.Type.Leave)
        turnover_widget.canvas.leaveEvent(ev_leave)
        self.assertIsNone(turnover_widget.canvas.hovered_session_idx)

        # 4. Survival Decay Widget
        survival_widget = SurvivalDecayWidget()
        survival_widget.resize(700, 380)
        survival_widget.set_data(snapshot.survival_curve, snapshot.point_of_no_return_set)
        self.assertEqual(survival_widget.point_of_no_return, snapshot.point_of_no_return_set)

        from rekordbox_collection_manager.analytics import SurvivalStepData
        mock_steps = [
            SurvivalStepData(
                set_number=k,
                total_exposed=100,
                unplayed_count=max(50, 100 - k * 5 if k <= 6 else 70 - (k - 6)),
                unplayed_pct=float(max(50, 100 - k * 5 if k <= 6 else 70 - (k - 6))),
            )
            for k in range(21)
        ]
        survival_widget.set_data(mock_steps, point_of_no_return=6)
        pix_surv = QPixmap(700, 380)
        survival_widget.render(pix_surv)

        clicked_steps = []
        survival_widget.step_clicked.connect(lambda sn, pct: clicked_steps.append((sn, pct)))
        left_m, top_m, _, _, g_w, g_h = survival_widget._chart_geometry()
        x_set6 = left_m + (6.0 / 20.0) * g_w
        ev_surv_move = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(x_set6, top_m + g_h * 0.8),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        survival_widget.mouseMoveEvent(ev_surv_move)
        self.assertEqual(survival_widget.hovered_step_idx, 6)

        ev_surv_click = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(x_set6, top_m + g_h * 0.8),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        survival_widget.mousePressEvent(ev_surv_click)
        self.assertEqual(survival_widget.selected_step_idx, 6)
        self.assertEqual(clicked_steps, [(6, 70.0)])

        survival_widget.leaveEvent(QEvent(QEvent.Type.Leave))
        self.assertIsNone(survival_widget.hovered_step_idx)
        survival_widget.clear_selection()
        self.assertIsNone(survival_widget.selected_step_idx)

        # 5. Transition Graph Widget
        graph_widget = TransitionGraphWidget()
        graph_widget.resize(800, 600)
        graph_widget.set_data(snapshot.transition_edges, snapshot.transition_nodes)

        # Test airy layout with multiple transition pairs
        from rekordbox_collection_manager.analytics import TransitionEdgeData
        mock_edges = [
            TransitionEdgeData(
                source_id=f"s_{i}",
                source_title=f"Src {i}",
                source_artist="Art",
                target_id=f"t_{i}",
                target_title=f"Tgt {i}",
                target_artist="Art",
                count=2,
                session_names=["Set 1", "Set 2"],
            )
            for i in range(10)
        ]
        mock_nodes = {}
        for i in range(10):
            mock_nodes[f"s_{i}"] = {"title": f"Src {i}", "artist": "Art", "key": "8A", "bpm": 124, "plays": 3, "ring": "A"}
            mock_nodes[f"t_{i}"] = {"title": f"Tgt {i}", "artist": "Art", "key": "9A", "bpm": 125, "plays": 2, "ring": "A"}

        graph_widget.set_data(mock_edges, mock_nodes)
        self.assertEqual(len(graph_widget.node_positions), 20)

        # Verify airy spacing between nodes (> 80px minimum distance)
        node_pts = list(graph_widget.node_positions.values())
        min_distance = float("inf")
        import math
        for i in range(len(node_pts)):
            for j in range(i + 1, len(node_pts)):
                d = math.hypot(node_pts[i].x() - node_pts[j].x(), node_pts[i].y() - node_pts[j].y())
                if d < min_distance:
                    min_distance = d
        self.assertGreater(min_distance, 80.0)

        # Test zoom in, zoom out and reset
        prev_zoom = graph_widget._zoom_factor
        graph_widget.zoom_in()
        self.assertGreater(graph_widget._zoom_factor, prev_zoom)
        graph_widget.zoom_out()
        graph_widget.reset_view()
        self.assertAlmostEqual(graph_widget._pan_offset.x(), 0.0)
        self.assertAlmostEqual(graph_widget._pan_offset.y(), 0.0)

        # Render to populate overlay button rects and verify edge-click & leaveEvent
        pix_graph = QPixmap(800, 600)
        graph_widget.render(pix_graph)
        zoomed_val = graph_widget._zoom_factor
        graph_widget.zoom_in()
        self.assertGreater(graph_widget._zoom_factor, zoomed_val)
        after_zoom = graph_widget._zoom_factor
        # Double-clicking zoom-in button must not reset view
        ev_dbl_btn = QMouseEvent(
            QEvent.Type.MouseButtonDblClick,
            graph_widget._btn_zoom_in.center(),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        graph_widget.mouseDoubleClickEvent(ev_dbl_btn)
        self.assertAlmostEqual(graph_widget._zoom_factor, after_zoom)
        graph_widget.reset_view()

        clicked_edges = []
        graph_widget.edge_clicked.connect(lambda u, v, c: clicked_edges.append((u, v, c)))
        p1 = graph_widget.node_positions["s_0"]
        p2 = graph_widget.node_positions["t_0"]
        mid_screen = graph_widget._world_to_widget(QPointF((p1.x() + p2.x()) / 2.0, (p1.y() + p2.y()) / 2.0))
        graph_widget.mousePressEvent(
            QMouseEvent(
                QEvent.Type.MouseButtonPress,
                mid_screen,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )
        self.assertFalse(graph_widget._is_panning)
        graph_widget.mouseReleaseEvent(
            QMouseEvent(
                QEvent.Type.MouseButtonRelease,
                mid_screen,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )
        self.assertEqual(clicked_edges, [("s_0", "t_0", 2)])
        self.assertEqual(graph_widget.selected_edge, ("s_0", "t_0"))

        graph_widget.leaveEvent(QEvent(QEvent.Type.Leave))
        self.assertIsNone(graph_widget.hovered_node_id)
        self.assertIsNone(graph_widget.hovered_edge)
        graph_widget.clear_selection()
        self.assertIsNone(graph_widget.selected_edge)

    def test_analytics_view_integration(self):
        """Test AnalyticsView loaded with snapshot and interactive filtering."""
        view = AnalyticsView()
        view.show()
        view.resize(900, 700)

        snapshot = compute_analytics(self.engine, min_session_duration_sec=0, min_session_tracks=1)
        view._on_analytics_loaded(snapshot)

        # Verify KPI values are populated
        self.assertEqual(view.card_sessions.lbl_value.text(), "1")

        # Verify responsive orientation adaptation
        # At 900px (< 1120px breakpoint), layout adapts to Vertical (Stacked)
        self.assertEqual(view.splitter.orientation(), Qt.Orientation.Vertical)
        self.assertIn("Stacked", view.btn_layout_toggle.text())

        # At 1400px (>= 1120px breakpoint), layout adapts to Horizontal (Lateral) with pushed-right compact table
        view.resize(1400, 800)
        QApplication.processEvents()
        self.assertEqual(view.splitter.orientation(), Qt.Orientation.Horizontal)
        self.assertIn("Lateral", view.btn_layout_toggle.text())
        sizes = view.splitter.sizes()
        self.assertGreater(sizes[0], sizes[1] + 250)

        # Test manual toggle override
        view._toggle_layout_mode()
        self.assertEqual(view.splitter.orientation(), Qt.Orientation.Vertical)
        self.assertTrue(view._user_pinned_layout)
        view._toggle_layout_mode()
        self.assertEqual(view.splitter.orientation(), Qt.Orientation.Horizontal)

        # Verify horizontal scrollbar policy, export button, and Expand hidden in Lateral mode
        self.assertEqual(view.table.horizontalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.assertIn("Export", view.btn_export_menu.text())
        self.assertFalse(view.btn_expand_table.isVisible())

        # Double-click on splitter handle is a no-op in Lateral (horizontal split) mode
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QMouseEvent
        handle = view.splitter.handle(1)
        dbl_click = QMouseEvent(
            QEvent.Type.MouseButtonDblClick,
            QPointF(5, 5),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(handle, dbl_click)
        self.assertFalse(getattr(view, "_table_expanded", False))
        self.assertTrue(view.chart_container.isVisible())

        # Switch to Vertical (Stacked) mode: Expand button becomes visible and TITLE stretches
        view._toggle_layout_mode()
        self.assertEqual(view.splitter.orientation(), Qt.Orientation.Vertical)
        self.assertTrue(view.btn_expand_table.isVisible())
        self.assertIn("Expand", view.btn_expand_table.text())

        # Expand table via button in Stacked mode
        view.btn_expand_table.click()
        self.assertTrue(view._table_expanded)
        self.assertFalse(view.chart_container.isVisible())
        self.assertIn("Restore", view.btn_expand_table.text())

        # Auto-restore on tab switch
        view._on_tab_switched(2)
        self.assertFalse(view._table_expanded)
        self.assertTrue(view.chart_container.isVisible())
        self.assertIn("Expand", view.btn_expand_table.text())

        # Expand via double-click on splitter handle in Stacked mode
        QApplication.sendEvent(handle, dbl_click)
        self.assertTrue(view._table_expanded)
        self.assertFalse(view.chart_container.isVisible())

        # Auto-restore on KPI card click
        view._on_kpi_card_clicked(view.card_dead_weight, "Dead Weight (Zavorra)")
        self.assertFalse(view._table_expanded)
        self.assertTrue(view.chart_container.isVisible())

        # Auto-restore if switching back to Horizontal while expanded
        view.btn_expand_table.click()
        self.assertTrue(view._table_expanded)
        view._toggle_layout_mode()
        self.assertEqual(view.splitter.orientation(), Qt.Orientation.Horizontal)
        self.assertFalse(view._table_expanded)
        self.assertTrue(view.chart_container.isVisible())
        self.assertFalse(view.btn_expand_table.isVisible())

        # Tab switching and section descriptions
        self.assertEqual(len(view.tab_buttons), 5)
        for btn in view.tab_buttons:
            self.assertTrue(len(btn.toolTip()) > 20)

        view._on_tab_switched(3)  # Survival Decay
        self.assertEqual(view.chart_stack.currentIndex(), 3)
        self.assertEqual(view.lbl_sec_icon.text(), "📉")
        self.assertIn("Decay Curve", view.lbl_sec_desc.text())
        self.assertTrue(len(view.lbl_sec_help.toolTip()) > 50)

        view._on_tab_switched(1)
        self.assertEqual(view.chart_stack.currentIndex(), 1)
        # Verify harmonic profile card populated
        self.assertIn("Minor", view.lbl_mode_ratio.text())

        # Click Camelot slice 8A
        view._on_camelot_slice_clicked("8A", ["t1"])
        self.assertIn("8A", view.lbl_table_title.text())
        self.assertEqual(len(view.table.records), 1)

        # Test jumping via Camelot key helper
        view._jump_to_key("8B")
        self.assertEqual(view.widget_camelot.selected_code, "8B")
        self.assertIn("8B", view.lbl_table_title.text())

        view._on_tab_switched(0)
        self.assertEqual(view.chart_stack.currentIndex(), 0)

        # Filter by cluster
        view._filter_by_cluster("Tutti")
        self.assertEqual(len(view.current_filtered_tracks), 2)

        # KPI card active toggle
        self.assertFalse(view.card_dead_weight.is_active())
        view._on_kpi_card_clicked(view.card_dead_weight, "Dead Weight (Zavorra)")
        self.assertTrue(view.card_dead_weight.is_active())
        # Clicking again deselects and resets to all tracks
        view._on_kpi_card_clicked(view.card_dead_weight, "Dead Weight (Zavorra)")
        self.assertFalse(view.card_dead_weight.is_active())
        self.assertEqual(len(view.current_filtered_tracks), 2)

        # Table search filter
        view._apply_table_search("Track 1")
        self.assertEqual(len(view.table.records), 1)
        view._apply_table_search("")
        self.assertEqual(len(view.table.records), 2)

    def test_analytics_view_error_modal_only_on_interactive_refresh(self):
        """Passive DB-load analytics errors emit status without modal QMessageBox popups."""
        view = AnalyticsView()
        with patch.object(QMessageBox, "warning") as mock_warn:
            view._interactive_refresh = False
            view._on_analytics_error("no such table: djmdContent")
            mock_warn.assert_not_called()

            view._interactive_refresh = True
            view._on_analytics_error("no such table: djmdContent")
            mock_warn.assert_called_once()

    def test_analytics_view_cleanup_stops_worker(self):
        """AnalyticsView.cleanup() stops and clears any active background worker."""
        view = AnalyticsView()
        mock_worker = MagicMock()
        view._worker = mock_worker
        with patch("rekordbox_collection_manager.gui.analytics_view.stop_worker") as mock_stop:
            view.cleanup()
            mock_stop.assert_called_once_with(mock_worker, wait_ms=1000)
            self.assertIsNone(view._worker)


if __name__ == "__main__":
    unittest.main()

