"""Unit tests for automatic view refresh and dirty view invalidation."""

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication
from sqlalchemy import create_engine

from rekordbox_collection_manager.gui.tracks_view import TracksView
from rekordbox_collection_manager.gui.play_count_view import PlayCountView
from rekordbox_collection_manager.gui.main_window import MainWindow

app = QApplication.instance() or QApplication([])


class TestViewRefresh(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")

    def test_tracks_view_refresh(self):
        view = TracksView()
        view.engine = self.engine
        view._execute_search = MagicMock()
        view.populate_filter_options = MagicMock()

        view.refresh(preserve_scroll=True)

        view.populate_filter_options.assert_called_once()
        view._execute_search.assert_called_once_with(preserve_scroll=True)

    def test_play_count_view_refresh(self):
        view = PlayCountView()
        view.engine = self.engine
        view._execute_search = MagicMock()

        view.refresh(preserve_scroll=True)

        view._execute_search.assert_called_once_with(preserve_scroll=True)

    @patch("rekordbox_collection_manager.gui.main_window.is_turso_configured", return_value=False)
    @patch("rekordbox_collection_manager.gui.main_window.is_rekordbox_running", return_value=False)
    def test_main_window_dirty_views_and_auto_refresh(self, mock_rb, mock_turso):
        with patch.object(MainWindow, "load_database"):
            win = MainWindow()
            win.tracks_view.refresh = MagicMock()
            win.playlists_view.refresh = MagicMock()
            win.tags_view.refresh = MagicMock()
            win.play_count_view.refresh = MagicMock()

            # Initially clean
            self.assertEqual(win._dirty_views, set())

            # Assume currently on Play Count view (index 4)
            win.stacked_widget.setCurrentIndex(4)

            # Changes saved on Play Count view
            win._on_db_saved()

            # TracksView should be pre-refreshed in background immediately
            win.tracks_view.refresh.assert_called_once_with(preserve_scroll=True)
            # Other views marked dirty (1: Playlists, 2: Tags)
            self.assertIn(1, win._dirty_views)
            self.assertIn(2, win._dirty_views)
            self.assertNotIn(0, win._dirty_views)  # Already pre-refreshed

            # Navigate to Playlists (1)
            win._on_nav_clicked(1)
            win.playlists_view.refresh.assert_called_once()
            self.assertNotIn(1, win._dirty_views)

            # Mark tracks dirty manually to test navigation refresh
            win._dirty_views.add(0)
            win.tracks_view.refresh.reset_mock()
            win._on_nav_clicked(0)
            win.tracks_view.refresh.assert_called_once_with(preserve_scroll=True)
            self.assertNotIn(0, win._dirty_views)

    def test_track_detail_view_has_staged_changes(self):
        from rekordbox_collection_manager.gui.track_detail_view import TrackDetailView

        detail = TrackDetailView()
        detail.current_playlists = ["A"]
        detail.original_playlists = ["A"]
        detail.current_tags = ["T1"]
        detail.original_tags = ["T1"]
        detail.current_comment = "hello"
        detail.original_comment = "hello"
        detail.current_store = "Beatport"
        detail.original_store = "Beatport"

        # Initially not staged
        self.assertFalse(detail.has_staged_changes())

        # Modify a tag
        detail.current_tags = ["T1", "T2"]
        self.assertTrue(detail.has_staged_changes())

    def test_tracks_view_detail_sync_on_search_success(self):
        view = TracksView()
        view.engine = self.engine
        view.stack.setCurrentIndex(1)  # On detail page
        view.detail_view.has_staged_changes = MagicMock(return_value=False)
        view.detail_view.track = {"id": "100"}
        view.detail_view.reload_track = MagicMock()

        records = [{"id": "100", "title": "Updated Song", "play_count": 5}]
        view._on_search_success("test", records)

        view.detail_view.reload_track.assert_called_once()

    @patch("rekordbox_collection_manager.edit_play_count.search_tracks")
    def test_track_detail_view_reload_track(self, mock_search):
        from rekordbox_collection_manager.gui.track_detail_view import TrackDetailView

        mock_search.return_value = [
            {"id": "100", "title": "New Title", "file_type": "AIFF", "bitrate": 1411}
        ]
        detail = TrackDetailView()
        detail.engine = self.engine
        detail.track = {"id": "100", "title": "Old Title", "file_type": "MP3"}
        detail.load_track = MagicMock()
        changed_signal = MagicMock()
        detail.track_changed.connect(changed_signal)

        detail.reload_track()

        mock_search.assert_called_once_with(self.engine, content_id="100")
        detail.load_track.assert_called_once()
        changed_signal.assert_called_once()

    def test_back_from_detail_refreshes_when_needed(self):
        view = TracksView()
        view.engine = self.engine
        view.stack.setCurrentIndex(1)
        view._needs_table_refresh = True
        view.refresh = MagicMock()

        view._on_back_from_detail()

        self.assertEqual(view.stack.currentIndex(), 0)
        self.assertFalse(view._needs_table_refresh)
        view.refresh.assert_called_once_with(preserve_scroll=True)

    def test_detail_track_changed_safe_matching(self):
        view = TracksView()
        view.engine = self.engine
        view.records = [
            {"id": "10", "title": "Track 10"},
            {"id": "20", "title": "Track 20"},
        ]
        view.table = MagicMock()
        view.table.rowCount.return_value = 2

        # Update track 20 (even if idx is passed as 0 or mismatched)
        view._on_detail_track_changed(0, {"id": "20", "title": "Track 20 Renamed"})

        # Record 0 should NOT be changed
        self.assertEqual(view.records[0]["title"], "Track 10")
        # Record 1 (id 20) SHOULD be updated
        self.assertEqual(view.records[1]["title"], "Track 20 Renamed")
        view.table.update_track_record.assert_called_once_with(1, {"id": "20", "title": "Track 20 Renamed"})


if __name__ == "__main__":
    unittest.main()
