"""Tests for Analytics GUI, AnalyticsView, and customized visualization widgets."""

import unittest
from unittest.mock import MagicMock
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QApplication
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

        # 4. Survival Decay Widget
        survival_widget = SurvivalDecayWidget()
        survival_widget.resize(500, 300)
        survival_widget.set_data(snapshot.survival_curve, snapshot.point_of_no_return_set)
        self.assertEqual(survival_widget.point_of_no_return, snapshot.point_of_no_return_set)

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

    def test_analytics_view_integration(self):
        """Test AnalyticsView loaded with snapshot and interactive filtering."""
        view = AnalyticsView()
        view.resize(900, 700)

        snapshot = compute_analytics(self.engine, min_session_duration_sec=0, min_session_tracks=1)
        view._on_analytics_loaded(snapshot)

        # Verify KPI values are populated
        self.assertEqual(view.card_sessions.lbl_value.text(), "1")

        # Tab switching and section descriptions
        self.assertEqual(len(view.tab_buttons), 5)
        for btn in view.tab_buttons:
            self.assertTrue(len(btn.toolTip()) > 20)

        view._on_tab_switched(3)  # Survival Decay
        self.assertEqual(view.chart_stack.currentIndex(), 3)
        self.assertEqual(view.lbl_sec_icon.text(), "📉")
        self.assertIn("Curva di Decadimento", view.lbl_sec_desc.text())
        self.assertTrue(len(view.lbl_sec_help.toolTip()) > 50)

        view._on_tab_switched(1)
        self.assertEqual(view.chart_stack.currentIndex(), 1)
        view._on_tab_switched(0)
        self.assertEqual(view.chart_stack.currentIndex(), 0)

        # Filter by cluster
        view._filter_by_cluster("Tutti")
        self.assertEqual(len(view.current_filtered_tracks), 2)

        # Table search filter
        view._apply_table_search("Track 1")
        self.assertEqual(len(view.table.records), 1)
        view._apply_table_search("")
        self.assertEqual(len(view.table.records), 2)


if __name__ == "__main__":
    unittest.main()

