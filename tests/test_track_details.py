"""Unit tests for Track Details View dialog, queries, and worker."""

import os
import unittest
from unittest.mock import MagicMock, patch

# Set offscreen platform for headless Qt testing
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QPushButton
from sqlalchemy import create_engine

from rekordbox_collection_manager.edit_play_count import (
    fetch_track_full_details,
    search_tracks,
)
from rekordbox_collection_manager.gui.history_view import HistoryView
from rekordbox_collection_manager.gui.play_count_view import PlayCountView
from rekordbox_collection_manager.gui.track_detail_dialog import (
    TrackDetailDialog,
    format_file_size,
    format_rating_stars,
)
from rekordbox_collection_manager.gui.track_detail_view import TrackDetailView
from rekordbox_collection_manager.gui.tracks_view import TracksView
from rekordbox_collection_manager.gui.workers import TrackDetailWorker
from rekordbox_collection_manager.rename_history import fetch_track_history_plays
from rekordbox_collection_manager.utils import resolve_artwork_path

# Shared QApplication instance for Qt unit tests
app = QApplication.instance()
if app is None:
    app = QApplication([])


class TestTrackDetailsBackend(unittest.TestCase):
    """Test backend queries, artwork resolution, and data extraction."""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        with self.engine.begin() as conn:
            conn.exec_driver_sql("""
                CREATE TABLE djmdContent (
                    ID VARCHAR(255) PRIMARY KEY,
                    Title VARCHAR(255),
                    ArtistID VARCHAR(255),
                    SrcArtistName VARCHAR(255),
                    AlbumID VARCHAR(255),
                    GenreID VARCHAR(255),
                    KeyID VARCHAR(255),
                    BPM INTEGER,
                    Length INTEGER,
                    BitRate INTEGER,
                    BitDepth INTEGER,
                    SampleRate INTEGER,
                    FileSize INTEGER,
                    FileType INTEGER,
                    Commnt VARCHAR(255),
                    Rating INTEGER,
                    ReleaseYear INTEGER,
                    StockDate VARCHAR(255),
                    DateCreated VARCHAR(255),
                    ImagePath VARCHAR(255),
                    FolderPath VARCHAR(255),
                    FileNameL VARCHAR(255),
                    DJPlayCount INTEGER,
                    ColorID VARCHAR(255),
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdHistory (
                    ID VARCHAR(255) PRIMARY KEY,
                    Name VARCHAR(255),
                    ParentID VARCHAR(255),
                    Attribute INTEGER,
                    DateCreated VARCHAR(255),
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdSongHistory (
                    ID VARCHAR(255) PRIMARY KEY,
                    HistoryID VARCHAR(255),
                    ContentID VARCHAR(255),
                    TrackNo INTEGER,
                    created_at VARCHAR(255),
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)

            # Insert sample tracks
            conn.exec_driver_sql("""
                INSERT INTO djmdContent (
                    ID, Title, SrcArtistName, BPM, Length, BitRate, BitDepth, SampleRate,
                    FileSize, FileType, Rating, ReleaseYear, StockDate, FolderPath, FileNameL, DJPlayCount, rb_local_deleted
                ) VALUES 
                    ('101', 'NRFTW', 'Aaaron', 12200, 352, 320, 16, 44100, 14318435, 1, 204, 2024, '2025-07-25', '/Music/Afro/NRFTW.mp3', 'NRFTW.mp3', 3, 0),
                    ('102', 'Titanium', 'David Guetta', 12800, 245, 320, 16, 44100, 9876543, 1, 255, 2011, '2024-01-10', '/Music/Dance/Titanium.mp3', 'Titanium.mp3', 0, 0);
            """)

            # Insert sample history sessions
            conn.exec_driver_sql("""
                INSERT INTO djmdHistory (ID, Name, ParentID, DateCreated, rb_local_deleted)
                VALUES 
                    ('hist_1', 'HISTORY 2026-09-05 @ Chiringuito', '202609', '2026-09-05 16:33:59', 0),
                    ('hist_2', 'HISTORY 2026-08-14 (1) @ Chiringuito w Silvia, Secco', '202608', '2026-08-14 17:09:03', 0),
                    ('hist_del', 'HISTORY DELETED', '202608', '2026-08-01 12:00:00', 1);
            """)

            # Insert sample history plays for track 101
            conn.exec_driver_sql("""
                INSERT INTO djmdSongHistory (ID, HistoryID, ContentID, TrackNo, created_at, rb_local_deleted)
                VALUES 
                    ('sh_1', 'hist_1', '101', 19, '2026-09-10 14:33:59.576 +00:00', 0),
                    ('sh_2', 'hist_2', '101', 2, '2026-08-28 15:09:03.561 +00:00', 0),
                    ('sh_3', 'hist_del', '101', 5, '2026-08-01 12:05:00.000 +00:00', 0),
                    ('sh_del_row', 'hist_1', '101', 99, '2026-09-10 14:00:00.000 +00:00', 1);
            """)

    def test_fetch_track_history_plays(self):
        """Verify fetch_track_history_plays retrieves active sessions with parsed metadata."""
        plays = fetch_track_history_plays(self.engine, "101")
        self.assertEqual(len(plays), 2, "Should only return non-deleted history sessions")

        first = plays[0]
        self.assertEqual(first["history_id"], "hist_1")
        self.assertEqual(first["track_no"], 19)
        self.assertEqual(first["location"], "Chiringuito")
        self.assertEqual(first["played_with"], "")
        self.assertEqual(first["played_time"], "14:33:59")

        second = plays[1]
        self.assertEqual(second["history_id"], "hist_2")
        self.assertEqual(second["track_no"], 2)
        self.assertEqual(second["location"], "Chiringuito")
        self.assertEqual(second["played_with"], "Silvia, Secco")

    def test_fetch_track_history_plays_empty_for_unplayed_track(self):
        """Verify track without history returns empty list."""
        plays = fetch_track_history_plays(self.engine, "102")
        self.assertEqual(len(plays), 0)

    def test_fetch_track_full_details(self):
        """Verify fetch_track_full_details combines track metadata and history plays."""
        details = fetch_track_full_details(self.engine, "101")
        self.assertEqual(details["id"], "101")
        self.assertEqual(details["title"], "NRFTW")
        self.assertEqual(details["artist"], "Aaaron")
        self.assertEqual(details["bpm"], 122.0)
        self.assertEqual(details["duration_sec"], 352)
        self.assertEqual(details["date_added"], "2025-07-25")
        self.assertEqual(details["sample_rate"], 44100)
        self.assertEqual(len(details["history_plays"]), 2)

    def test_format_helpers(self):
        """Verify file size and star rating helpers."""
        self.assertEqual(format_file_size(14318435), "13.7 MB")
        self.assertEqual(format_file_size(512000), "500 KB")
        self.assertEqual(format_file_size(0), "—")

        self.assertEqual(format_rating_stars(0), "☆☆☆☆☆")
        self.assertEqual(format_rating_stars(255), "★★★★★")
        self.assertEqual(format_rating_stars(204), "★★★★☆")
        self.assertEqual(format_rating_stars(3), "★★★☆☆")

    def test_resolve_artwork_path(self):
        """Verify artwork path resolver returns None when file doesn't exist."""
        res = resolve_artwork_path("/PIONEER/Artwork/nonexistent/artwork.jpg")
        self.assertIsNone(res)
        self.assertIsNone(resolve_artwork_path(""))
        self.assertIsNone(resolve_artwork_path(None))


class TestTrackDetailWorker(unittest.TestCase):
    """Test asynchronous TrackDetailWorker execution."""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        with self.engine.begin() as conn:
            conn.exec_driver_sql("""
                CREATE TABLE djmdContent (
                    ID VARCHAR(255) PRIMARY KEY,
                    Title VARCHAR(255),
                    SrcArtistName VARCHAR(255),
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdContent (ID, Title, SrcArtistName)
                VALUES ('42', 'Solaris', 'Artbat');
            """)

    def test_worker_signals(self):
        """Verify worker fetches data and emits success signal."""
        worker = TrackDetailWorker(self.engine, "42")
        received = []
        worker.success.connect(lambda data: received.append(data))
        worker.run()

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["id"], "42")
        self.assertEqual(received[0]["title"], "Solaris")


class TestTrackDetailDialogUI(unittest.TestCase):
    """Test TrackDetailDialog UI components, table rendering, and user interactions."""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        with self.engine.begin() as conn:
            conn.exec_driver_sql("""
                CREATE TABLE djmdContent (
                    ID VARCHAR(255) PRIMARY KEY,
                    Title VARCHAR(255),
                    SrcArtistName VARCHAR(255),
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)

        self.sample_tracks = [
            {
                "id": "1",
                "content_id": "1",
                "title": "Track One",
                "artist": "Artist Alpha",
                "album": "Album A",
                "genre": "Melodic Techno",
                "key": "8A",
                "bpm": 124.0,
                "duration_sec": 360,
                "duration_str": "6:00",
                "play_count": 5,
                "rating": 204,
                "year": 2024,
                "bitrate": 320,
                "file_type": "MP3",
                "tags": ["Peak Time", "Vocal"],
                "playlists": ["Festival 2026", "Sunset Set"],
                "file_path": "/Music/track1.mp3",
                "folder_dir": "/Music",
                "comment": "Crowd favorite",
                "history_plays": [
                    {
                        "history_id": "session_100",
                        "track_no": 12,
                        "session_date": "2026-08-15",
                        "history_name": "HISTORY 2026-08-15 @ Amnesia",
                        "location": "Amnesia",
                        "played_with": "Solomun",
                        "played_time": "03:45:00",
                    }
                ],
            },
            {
                "id": "2",
                "content_id": "2",
                "title": "Track Two",
                "artist": "Artist Beta",
                "album": "Album B",
                "genre": "Deep House",
                "key": "5B",
                "bpm": 120.0,
                "duration_sec": 300,
                "duration_str": "5:00",
                "play_count": 0,
                "rating": 0,
                "year": 2025,
                "bitrate": 1411,
                "file_type": "AIFF",
                "tags": [],
                "playlists": [],
                "file_path": "/Music/track2.aiff",
                "folder_dir": "/Music",
                "history_plays": [],
            },
        ]

    def test_dialog_renders_track_metadata(self):
        """Verify dialog initializes with title, artist, key, bpm, and tags."""
        dlg = TrackDetailDialog(
            engine=self.engine,
            track_data=self.sample_tracks[0],
            tracks_list=self.sample_tracks,
            current_index=0,
        )

        self.assertEqual(dlg.title_label.text(), "Track One")
        self.assertEqual(dlg.artist_label.text(), "Artist Alpha")
        self.assertEqual(dlg.bpm_kpi_val.text(), "124.0 BPM")
        self.assertEqual(dlg.duration_kpi_val.text(), "6:00")
        self.assertIn("Key: 8A", dlg.key_badge.text())
        self.assertIn("5 plays", dlg.play_count_badge.text())
        self.assertEqual(dlg.pos_indicator.text(), "Track 1 of 2")
        self.assertFalse(dlg.prev_btn.isHidden())
        self.assertFalse(dlg.prev_btn.isEnabled(), "Prev should be disabled on first track")
        self.assertTrue(dlg.next_btn.isEnabled(), "Next should be enabled")

        # History table
        self.assertFalse(dlg.history_table.isHidden())
        self.assertEqual(dlg.history_table.rowCount(), 1)
        self.assertEqual(dlg.history_table.item(0, 0).text(), "#12")
        self.assertEqual(dlg.history_table.item(0, 1).text(), "2026-08-15")
        self.assertIn("Amnesia", dlg.history_table.item(0, 3).text())
        self.assertIn("Solomun", dlg.history_table.item(0, 4).text())

    def test_dialog_history_session_navigation_signal(self):
        """Verify clicking View Session or double clicking history row emits open_history_session_requested."""
        dlg = TrackDetailDialog(
            engine=self.engine,
            track_data=self.sample_tracks[0],
            tracks_list=self.sample_tracks,
            current_index=0,
        )

        emitted_session = []
        dlg.open_history_session_requested.connect(lambda hid: emitted_session.append(hid))

        # Double click the history row
        dlg._on_history_row_double_clicked(0, 2)
        self.assertEqual(len(emitted_session), 1)
        self.assertEqual(emitted_session[0], "session_100")

    def test_dialog_navigation_prev_next(self):
        """Verify next and prev track switching."""
        dlg = TrackDetailDialog(
            engine=self.engine,
            track_data=self.sample_tracks[0],
            tracks_list=self.sample_tracks,
            current_index=0,
        )

        dlg._on_next_track()
        self.assertEqual(dlg.current_index, 1)
        self.assertEqual(dlg.title_label.text(), "Track Two")
        self.assertEqual(dlg.pos_indicator.text(), "Track 2 of 2")
        self.assertTrue(dlg.prev_btn.isEnabled())
        self.assertFalse(dlg.next_btn.isEnabled())

        # Empty history state
        self.assertFalse(dlg.history_empty_card.isHidden())
        self.assertTrue(dlg.history_table.isHidden())

        dlg._on_prev_track()
        self.assertEqual(dlg.current_index, 0)
        self.assertEqual(dlg.title_label.text(), "Track One")


class TestTrackDetailViewIntegration(unittest.TestCase):
    """Test embedded TrackDetailView interactions, back signals, and stack transitions."""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        self.sample_tracks = [
            {
                "id": "10",
                "content_id": "10",
                "title": "Solaris",
                "artist": "Argy",
                "album": "New World",
                "bpm": 126.0,
                "duration_sec": 330,
                "duration_str": "5:30",
                "file_type": "FLAC",
                "key": "11A",
                "play_count": 7,
                "rating": 255,
                "tags": ["Afterlife", "Melodic"],
                "playlists": ["Peak Time Techno"],
                "file_path": "/Music/solaris.flac",
                "folder_dir": "/Music",
                "history_plays": [],
            }
        ]

    def test_view_renders_track_and_emits_back_button(self):
        """Verify TrackDetailView renders correctly and emits back_clicked signal on back button click."""
        view = TrackDetailView(
            engine=self.engine,
            track_data=self.sample_tracks[0],
            tracks_list=self.sample_tracks,
            current_index=0,
            back_button_text="← Back to Tracks",
        )
        self.assertEqual(view.title_label.text(), "Solaris")
        self.assertEqual(view.artist_label.text(), "Argy")
        self.assertEqual(view.back_btn.text(), "← Back to Tracks")

        back_emitted = []
        view.back_clicked.connect(lambda: back_emitted.append(True))
        view.back_btn.click()
        self.assertEqual(len(back_emitted), 1)

    def test_view_esc_key_emits_back(self):
        """Verify pressing Escape key emits back_clicked."""
        view = TrackDetailView(
            engine=self.engine,
            track_data=self.sample_tracks[0],
            tracks_list=self.sample_tracks,
            current_index=0,
        )
        back_emitted = []
        view.back_clicked.connect(lambda: back_emitted.append(True))

        esc_event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        view.keyPressEvent(esc_event)
        self.assertEqual(len(back_emitted), 1)

    def test_tracks_view_stacked_navigation(self):
        """Verify TracksView switches to detail view at stack index 1 and returns to index 0 on back."""
        tracks_view = TracksView()
        tracks_view.engine = self.engine
        tracks_view.records = list(self.sample_tracks)

        self.assertEqual(tracks_view.stack.currentIndex(), 0)

        # Open track detail
        tracks_view._open_track_detail(self.sample_tracks[0])
        self.assertEqual(tracks_view.stack.currentIndex(), 1)
        self.assertEqual(tracks_view.detail_view.title_label.text(), "Solaris")

        # Click back
        tracks_view._on_back_from_detail()
        self.assertEqual(tracks_view.stack.currentIndex(), 0)

        # Reset to list
        tracks_view.stack.setCurrentIndex(1)
        tracks_view.reset_to_list()
        self.assertEqual(tracks_view.stack.currentIndex(), 0)

    def test_history_view_stacked_navigation(self):
        """Verify HistoryView switches to detail view at stack index 2 and returns to index 1 on back."""
        history_view = HistoryView()
        history_view.engine = self.engine

        self.assertEqual(history_view.stack.currentIndex(), 0)

        # Simulate opening track detail from within a history session
        history_view.detail_view.displayed_tracks = list(self.sample_tracks)
        history_view._open_track_detail(self.sample_tracks[0], 0)
        self.assertEqual(history_view.stack.currentIndex(), 2)

        # Return back from track detail to history session
        history_view._on_back_from_track_detail()
        self.assertEqual(history_view.stack.currentIndex(), 1)

    def test_play_count_view_stacked_navigation(self):
        """Verify PlayCountView switches to detail view at stack index 1 and returns to index 0 on back."""
        pc_view = PlayCountView()
        pc_view.engine = self.engine
        pc_view.records = list(self.sample_tracks)

        self.assertEqual(pc_view.stack.currentIndex(), 0)

        pc_view._open_track_detail(self.sample_tracks[0])
        self.assertEqual(pc_view.stack.currentIndex(), 1)

    def test_details_loaded_populates_history_plays_and_kpi(self):
        """Verify _on_details_loaded accurately updates history KPI and populates the history table."""
        view = TrackDetailView(
            engine=self.engine,
            track_data={"id": "99", "content_id": "99", "title": "Test Track"},
            tracks_list=[],
            current_index=0,
        )
        self.assertEqual(view.history_kpi_val.text(), "0 sessions")
        self.assertEqual(view.history_table.rowCount(), 0)

        # Simulate details emitted by TrackDetailWorker (single dictionary with history_plays list)
        worker_payload = {
            "id": "99",
            "content_id": "99",
            "title": "Test Track Enriched",
            "bitrate": 320,
            "sample_rate": 44100,
            "bit_depth": 16,
            "file_size": 10485760,
            "date_added": "2026-09-01",
            "history_plays": [
                {
                    "history_id": "session_1",
                    "track_no": 3,
                    "session_date": "2026-09-10",
                    "history_name": "HISTORY 2026-09-10 @ DC-10",
                    "location": "DC-10",
                    "played_with": "Seth Troxler",
                    "played_time": "02:15:00",
                },
                {
                    "history_id": "session_2",
                    "track_no": 7,
                    "session_date": "2026-09-18",
                    "history_name": "HISTORY 2026-09-18 @ Pacha",
                    "location": "Pacha",
                    "played_with": "Marco Carola",
                    "played_time": "04:30:00",
                },
            ],
        }

        # Invoke _on_details_loaded with the worker payload
        view._on_details_loaded(worker_payload)

        self.assertEqual(view.history_kpi_val.text(), "2 sessions")
        self.assertEqual(view.history_table.rowCount(), 2)
        self.assertFalse(view.history_table.isHidden())
        self.assertTrue(view.history_empty_card.isHidden())

        # Check Row 0 values
        self.assertEqual(view.history_table.item(0, 0).text(), "#3")
        self.assertEqual(view.history_table.item(0, 1).text(), "2026-09-10")
        self.assertEqual(view.history_table.item(0, 2).text(), "HISTORY 2026-09-10")
        self.assertEqual(view.history_table.item(0, 2).toolTip(), "HISTORY 2026-09-10 @ DC-10")
        self.assertIn("DC-10", view.history_table.item(0, 3).text())
        self.assertIn("Seth Troxler", view.history_table.item(0, 4).text())
        self.assertEqual(view.history_table.item(0, 5).text(), "02:15:00")


if __name__ == "__main__":
    unittest.main()

