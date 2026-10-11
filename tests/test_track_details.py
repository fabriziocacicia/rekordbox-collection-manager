"""Unit tests for Track Details View dialog, queries, and worker."""

import os
import unittest
from unittest.mock import MagicMock, patch

# Set offscreen platform for headless Qt testing
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton
from sqlalchemy import create_engine

from rekordbox_collection_manager.edit_play_count import (
    add_track_to_playlist,
    add_track_to_tag,
    fetch_all_playlists,
    fetch_all_tags,
    fetch_track_full_details,
    remove_track_from_playlist,
    remove_track_from_tag,
    search_tracks,
)
from rekordbox_collection_manager.gui.history_view import HistoryView
from rekordbox_collection_manager.gui.play_count_view import PlayCountView
from rekordbox_collection_manager.gui.track_detail_dialog import (
    TrackDetailDialog,
    format_created_at_tooltip,
    format_file_size,
    format_rating_stars,
)
from rekordbox_collection_manager.gui.chip import (
    PurchasedChip,
    StoreChip,
)
from rekordbox_collection_manager.gui.track_detail_view import (
    PlaylistChip,
    PlaylistChipsContainer,
    TagChip,
    TagChipsContainer,
    TrackDetailView,
)
from rekordbox_collection_manager.gui.tracks_view import TracksView
from rekordbox_collection_manager.gui.workers import TrackDetailWorker, stop_worker
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
                    created_at VARCHAR(255),
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
                    FileSize, FileType, Rating, ReleaseYear, StockDate, created_at, FolderPath, FileNameL, DJPlayCount, rb_local_deleted
                ) VALUES 
                    ('101', 'NRFTW', 'Aaaron', 12200, 352, 320, 16, 44100, 14318435, 1, 204, 2024, '2025-07-25', '2025-07-25 15:30:00.123 +00:00', '/Music/Afro/NRFTW.mp3', 'NRFTW.mp3', 3, 0),
                    ('102', 'Titanium', 'David Guetta', 12800, 245, 320, 16, 44100, 9876543, 1, 255, 2011, '2024-01-10', '2024-01-10 10:00:00.000 +00:00', '/Music/Dance/Titanium.mp3', 'Titanium.mp3', 0, 0);
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

            conn.exec_driver_sql("""
                CREATE TABLE djmdPlaylist (
                    ID VARCHAR(255) PRIMARY KEY,
                    Name VARCHAR(255),
                    Attribute INTEGER DEFAULT 0,
                    ParentID VARCHAR(255),
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdSongPlaylist (
                    ID VARCHAR(255) PRIMARY KEY,
                    PlaylistID VARCHAR(255),
                    ContentID VARCHAR(255),
                    TrackNo INTEGER,
                    created_at VARCHAR(255),
                    updated_at VARCHAR(255),
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdPlaylist (ID, Name, Attribute, ParentID, rb_local_deleted)
                VALUES 
                    ('pl_1', 'Afro House', 0, 'root', 0),
                    ('pl_2', 'Deep Tech', 0, 'root', 0),
                    ('pl_3', 'Melodic Techno', 0, 'root', 0),
                    ('pl_smart', 'Smart Playlist', 4, 'root', 0),
                    ('pl_del', 'Deleted Playlist', 0, 'root', 1);
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdSongPlaylist (ID, PlaylistID, ContentID, TrackNo, rb_local_deleted)
                VALUES 
                    ('sp_1', 'pl_1', '101', 1, 0),
                    ('sp_2', 'pl_2', '101', 2, 0);
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdMyTag (
                    ID VARCHAR(255) PRIMARY KEY,
                    Name VARCHAR(255),
                    Attribute INTEGER DEFAULT 0,
                    ParentID VARCHAR(255),
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdSongMyTag (
                    ID VARCHAR(255) PRIMARY KEY,
                    MyTagID VARCHAR(255),
                    ContentID VARCHAR(255),
                    TrackNo INTEGER,
                    created_at VARCHAR(255),
                    updated_at VARCHAR(255),
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdMyTag (ID, Name, Attribute, ParentID, rb_local_deleted)
                VALUES 
                    ('tag_1', 'Acid House', 0, '1', 0),
                    ('tag_2', 'Deep House', 0, '1', 0),
                    ('tag_3', 'Techno', 0, '1', 0),
                    ('tag_folder', 'Category Folder', 1, 'root', 0),
                    ('tag_del', 'Deleted Tag', 0, '1', 1);
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdSongMyTag (ID, MyTagID, ContentID, rb_local_deleted)
                VALUES 
                    ('smt_1', 'tag_1', '101', 0),
                    ('smt_2', 'tag_2', '101', 0);
            """)

    def test_fetch_all_playlists(self):
        """Verify fetch_all_playlists retrieves active standard playlists sorted by name."""
        pls = fetch_all_playlists(self.engine)
        names = [p["name"] for p in pls]
        self.assertEqual(names, ["Afro House", "Deep Tech", "Melodic Techno"])
        self.assertNotIn("Smart Playlist", names)
        self.assertNotIn("Deleted Playlist", names)

    def test_add_track_to_playlist_new_and_reactivate(self):
        """Verify adding track to playlist creates row or reactivates soft-deleted row."""
        # Add to Melodic Techno
        added = add_track_to_playlist(self.engine, "101", "Melodic Techno")
        self.assertTrue(added)

        with self.engine.connect() as conn:
            row = conn.exec_driver_sql(
                "SELECT TrackNo, rb_local_deleted FROM djmdSongPlaylist WHERE PlaylistID = 'pl_3' AND ContentID = '101';"
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], 1)
            self.assertEqual(row[1], 0)

        # Soft delete it
        removed = remove_track_from_playlist(self.engine, "101", "Melodic Techno")
        self.assertTrue(removed)

        with self.engine.connect() as conn:
            is_del = conn.exec_driver_sql(
                "SELECT rb_local_deleted FROM djmdSongPlaylist WHERE PlaylistID = 'pl_3' AND ContentID = '101';"
            ).scalar()
            self.assertEqual(is_del, 1)

        # Re-add it (reactivation)
        readded = add_track_to_playlist(self.engine, "101", "Melodic Techno")
        self.assertTrue(readded)

        with self.engine.connect() as conn:
            is_active = conn.exec_driver_sql(
                "SELECT rb_local_deleted FROM djmdSongPlaylist WHERE PlaylistID = 'pl_3' AND ContentID = '101';"
            ).scalar()
            self.assertEqual(is_active, 0)

    def test_fetch_all_tags(self):
        """Verify fetch_all_tags retrieves active standard My Tags sorted by name."""
        tags = fetch_all_tags(self.engine)
        names = [t["name"] for t in tags]
        self.assertEqual(names, ["Acid House", "Deep House", "Techno"])
        self.assertNotIn("Category Folder", names)
        self.assertNotIn("Deleted Tag", names)

    def test_add_track_to_tag_new_and_reactivate(self):
        """Verify adding track to My Tag creates row or reactivates soft-deleted row."""
        # Add to Techno
        added = add_track_to_tag(self.engine, "101", "Techno")
        self.assertTrue(added)

        with self.engine.connect() as conn:
            row = conn.exec_driver_sql(
                "SELECT rb_local_deleted FROM djmdSongMyTag WHERE MyTagID = 'tag_3' AND ContentID = '101';"
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], 0)

        # Soft delete it
        removed = remove_track_from_tag(self.engine, "101", "Techno")
        self.assertTrue(removed)

        with self.engine.connect() as conn:
            is_del = conn.exec_driver_sql(
                "SELECT rb_local_deleted FROM djmdSongMyTag WHERE MyTagID = 'tag_3' AND ContentID = '101';"
            ).scalar()
            self.assertEqual(is_del, 1)

        # Re-add it (reactivation)
        readded = add_track_to_tag(self.engine, "101", "Techno")
        self.assertTrue(readded)

        with self.engine.connect() as conn:
            is_active = conn.exec_driver_sql(
                "SELECT rb_local_deleted FROM djmdSongMyTag WHERE MyTagID = 'tag_3' AND ContentID = '101';"
            ).scalar()
            self.assertEqual(is_active, 0)

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
        self.assertEqual(details["created_at"], "2025-07-25 15:30:00.123 +00:00")
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

        # Test Date Added tooltip formatter
        self.assertEqual(
            format_created_at_tooltip("2025-06-07 19:33:55.844 +00:00"),
            "2025-06-07 19:33:55",
        )
        self.assertEqual(
            format_created_at_tooltip("2025-06-07T19:33:55Z"),
            "2025-06-07 19:33:55",
        )
        self.assertEqual(format_created_at_tooltip(None, "2025-06-07"), "2025-06-07")
        self.assertEqual(format_created_at_tooltip("", ""), "")

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

    def test_stop_worker_graceful(self):
        """Verify stop_worker safely disconnects signals and handles edge cases."""
        worker = TrackDetailWorker(self.engine, "42")
        received = []
        worker.success.connect(lambda data: received.append(data))

        # Test safe disconnect
        stop_worker(worker)
        worker.run()
        self.assertEqual(len(received), 0)

        # None or non-running worker should not raise
        stop_worker(None)


class TestTrackDetailDialogUI(unittest.TestCase):
    """Test TrackDetailDialog UI components, table rendering, and user interactions."""

    def setUp(self):
        self._dialogs = []
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
                "date_added": "2025-06-07",
                "created_at": "2025-06-07 19:33:55.844 +00:00",
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

    def tearDown(self):
        for dlg in getattr(self, "_dialogs", []):
            try:
                if hasattr(dlg, "detail_view") and dlg.detail_view:
                    dlg.detail_view.cleanup()
                dlg.close()
            except Exception:
                pass
        self._dialogs.clear()

    def test_dialog_renders_track_metadata(self):
        """Verify dialog initializes with title, artist, key, bpm, and tags."""
        dlg = TrackDetailDialog(
            engine=self.engine,
            track_data=self.sample_tracks[0],
            tracks_list=self.sample_tracks,
            current_index=0,
        )
        self._dialogs.append(dlg)

        self.assertEqual(dlg.title_label.text(), "Track One")
        self.assertEqual(dlg.artist_label.text(), "Artist Alpha")
        self.assertEqual(dlg.bpm_kpi_val.text(), "124.0 BPM")
        self.assertEqual(dlg.duration_kpi_val.text(), "6:00")
        self.assertEqual(dlg.date_added_kpi_val.text(), "2025-06-07")
        self.assertEqual(dlg.date_added_kpi_val.toolTip(), "2025-06-07 19:33:55")
        self.assertEqual(dlg.date_added_kpi_val.parentWidget().toolTip(), "2025-06-07 19:33:55")
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
        self._dialogs.append(dlg)

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
        self._dialogs.append(dlg)

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
        self._views = []
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

    def tearDown(self):
        for v in getattr(self, "_views", []):
            try:
                if hasattr(v, "cleanup"):
                    v.cleanup()
                elif hasattr(v, "detail_view") and hasattr(v.detail_view, "cleanup"):
                    v.detail_view.cleanup()
                elif hasattr(v, "track_detail_view") and hasattr(v.track_detail_view, "cleanup"):
                    v.track_detail_view.cleanup()
            except Exception:
                pass
        self._views.clear()

    def test_view_renders_track_and_emits_back_button(self):
        """Verify TrackDetailView renders correctly and emits back_clicked signal on back button click."""
        view = TrackDetailView(
            engine=self.engine,
            track_data=self.sample_tracks[0],
            tracks_list=self.sample_tracks,
            current_index=0,
            back_button_text="← Back to Tracks",
        )
        self._views.append(view)
        self.assertEqual(view.title_label.text(), "Solaris")
        self.assertEqual(view.artist_label.text(), "Argy")
        self.assertEqual(view.back_btn.text(), "← Back to Tracks")

        back_emitted = []
        view.back_clicked.connect(lambda: back_emitted.append(True))
        view.back_btn.click()
        self.assertEqual(len(back_emitted), 1)

    def test_replace_btn_visibility_mp3_only(self):
        """Verify replace_btn is visible for MP3 and AIFF tracks and hidden for other file types."""
        flac_track = dict(self.sample_tracks[0])
        flac_track["file_type"] = "FLAC"
        flac_track["file_path"] = "/Music/solaris.flac"
        flac_track["file_name"] = "solaris.flac"

        mp3_track = dict(self.sample_tracks[0])
        mp3_track["file_type"] = "MP3"
        mp3_track["file_path"] = "/Music/solaris.mp3"
        mp3_track["file_name"] = "solaris.mp3"

        aiff_track = dict(self.sample_tracks[0])
        aiff_track["file_type"] = "AIFF"
        aiff_track["file_path"] = "/Music/solaris.aiff"
        aiff_track["file_name"] = "solaris.aiff"

        view = TrackDetailView(
            engine=self.engine,
            track_data=flac_track,
            tracks_list=[flac_track, mp3_track, aiff_track],
            current_index=0,
        )
        self._views.append(view)
        self.assertTrue(view.replace_btn.isHidden())

        # Advance to next track (MP3)
        view._on_next_track()
        self.assertFalse(view.replace_btn.isHidden())
        self.assertTrue(view.replace_btn.isEnabled())
        self.assertIn("MP3 ➔ AIFF", view.replace_btn.text())

        # Advance to next track (AIFF)
        view._on_next_track()
        self.assertFalse(view.replace_btn.isHidden())
        self.assertTrue(view.replace_btn.isEnabled())
        self.assertIn("AIFF ➔ AIFF", view.replace_btn.text())

        # Back to previous track (MP3)
        view._on_prev_track()
        self.assertFalse(view.replace_btn.isHidden())

        # Back to FLAC
        view._on_prev_track()
        self.assertTrue(view.replace_btn.isHidden())

    def test_view_esc_key_emits_back(self):
        """Verify pressing Escape key emits back_clicked."""
        view = TrackDetailView(
            engine=self.engine,
            track_data=self.sample_tracks[0],
            tracks_list=self.sample_tracks,
            current_index=0,
        )
        self._views.append(view)
        back_emitted = []
        view.back_clicked.connect(lambda: back_emitted.append(True))

        esc_event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        view.keyPressEvent(esc_event)
        self.assertEqual(len(back_emitted), 1)

    def test_tracks_view_stacked_navigation(self):
        """Verify TracksView switches to detail view at stack index 1 and returns to index 0 on back."""
        tracks_view = TracksView()
        self._views.append(tracks_view)
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
        self._views.append(history_view)
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
        self._views.append(pc_view)
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
        self._views.append(view)
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


class TestPlaylistChipsUI(unittest.TestCase):
    """Test interactive editable playlist chips, autocompletion, and staged changes."""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        with self.engine.begin() as conn:
            conn.exec_driver_sql("""
                CREATE TABLE djmdContent (
                    ID VARCHAR(255) PRIMARY KEY,
                    Title VARCHAR(255),
                    SrcArtistName VARCHAR(255),
                    Commnt VARCHAR(255),
                    updated_at VARCHAR(255),
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdPlaylist (
                    ID VARCHAR(255) PRIMARY KEY,
                    Name VARCHAR(255),
                    Attribute INTEGER DEFAULT 0,
                    ParentID VARCHAR(255),
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdSongPlaylist (
                    ID VARCHAR(255) PRIMARY KEY,
                    PlaylistID VARCHAR(255),
                    ContentID VARCHAR(255),
                    TrackNo INTEGER,
                    created_at VARCHAR(255),
                    updated_at VARCHAR(255),
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdPlaylist (ID, Name, Attribute) VALUES 
                    ('pl_1', 'Afro House', 0),
                    ('pl_2', 'Deep House', 0),
                    ('pl_3', 'Techno Peak', 0);
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdContent (ID, Title, SrcArtistName) VALUES ('10', 'Sunset Track', 'DJ Test');
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdSongPlaylist (ID, PlaylistID, ContentID, TrackNo) VALUES ('sp_1', 'pl_1', '10', 1);
            """)

            conn.exec_driver_sql("""
                CREATE TABLE djmdMyTag (
                    ID VARCHAR(255) PRIMARY KEY,
                    Name VARCHAR(255),
                    Attribute INTEGER DEFAULT 0,
                    ParentID VARCHAR(255),
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdSongMyTag (
                    ID VARCHAR(255) PRIMARY KEY,
                    MyTagID VARCHAR(255),
                    ContentID VARCHAR(255),
                    TrackNo INTEGER,
                    created_at VARCHAR(255),
                    updated_at VARCHAR(255),
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdMyTag (ID, Name, Attribute) VALUES 
                    ('tag_1', 'Melodic', 0),
                    ('tag_2', 'Peak Time', 0),
                    ('tag_3', 'Vocal', 0);
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdSongMyTag (ID, MyTagID, ContentID) VALUES ('smt_1', 'tag_1', '10');
            """)

        self._views = []
        self.track_data = {
            "id": "10",
            "content_id": "10",
            "title": "Sunset Track",
            "artist": "DJ Test",
            "playlists": ["Afro House"],
            "tags": ["Melodic"],
        }

    def tearDown(self):
        for v in getattr(self, "_views", []):
            try:
                if hasattr(v, "cleanup"):
                    v.cleanup()
            except Exception:
                pass
        self._views.clear()

    def test_playlist_chip_hover_and_delete(self):
        """Verify '✕' button becomes visible on hover and emits delete_requested on click."""
        chip = PlaylistChip("Afro House")
        chip.show()
        self.assertFalse(chip.x_btn.isVisible(), "'✕' should be hidden by default")

        # Simulate mouse enter
        enter_ev = QEvent(QEvent.Type.Enter)
        chip.enterEvent(enter_ev)
        self.assertTrue(chip.x_btn.isVisible(), "'✕' should be visible on hover")

        # Click '✕'
        deleted = []
        chip.delete_requested.connect(lambda name: deleted.append(name))
        chip.x_btn.click()
        self.assertEqual(deleted, ["Afro House"])

    def test_container_empty_space_click_opens_autocomplete(self):
        """Verify clicking empty space opens input field and autocompleter positioned to the right of chips."""
        container = PlaylistChipsContainer()
        container.resize(600, 100)
        container.show()
        container.set_available_playlists(["Afro House", "Deep House", "Techno Peak"])
        container.set_playlists(["Afro House"])

        self.assertFalse(container.input_edit.isVisible())
        self.assertEqual(len(container.chips), 1)

        # Trigger show_add_input
        container.show_add_input()
        QApplication.processEvents()
        self.assertTrue(container.input_edit.isVisible())
        self.assertTrue(container.is_input_active())

        # Ensure input is positioned to the right of the chip, NOT overlapping
        chip_geom = container.chips[0].geometry()
        input_geom = container.input_edit.geometry()
        self.assertGreater(input_geom.x(), chip_geom.x() + chip_geom.width() - 5, "Input must appear to the right of chips")
        self.assertFalse(chip_geom.intersects(input_geom), "Chip and search input must never overlap")

        # Check candidates in completer model (should exclude already assigned 'Afro House')
        candidates = container.completer_model.stringList()
        self.assertIn("Deep House", candidates)
        self.assertIn("Techno Peak", candidates)
        self.assertNotIn("Afro House", candidates)

        # Press Escape to hide
        container.hide_add_input()
        self.assertFalse(container.input_edit.isVisible())

    def test_container_select_autocomplete_attaches_chip(self):
        """Verify selecting a playlist from autocomplete attaches new chip and emits signal in order."""
        container = PlaylistChipsContainer()
        container.resize(600, 100)
        container.show()
        container.set_available_playlists(["Afro House", "Deep House", "Techno Peak"])
        container.set_playlists(["Afro House"])

        added = []
        container.playlist_added.connect(lambda name: added.append(name))

        # Select 'Deep House'
        container._on_playlist_chosen("Deep House")
        QApplication.processEvents()
        self.assertEqual(added, ["Deep House"])
        self.assertEqual(container.get_playlists(), ["Afro House", "Deep House"])
        self.assertEqual(len(container.chips), 2)
        self.assertEqual(container.chips[1].name, "Deep House")

        # Verify chips are positioned sequentially horizontally without overlap
        chip0_geom = container.chips[0].geometry()
        chip1_geom = container.chips[1].geometry()
        self.assertGreater(chip1_geom.x(), chip0_geom.x(), "Second chip must be to the right of first chip")
        self.assertFalse(chip0_geom.intersects(chip1_geom), "Chips must not overlap")

    def test_save_discard_buttons_visibility_and_workflow(self):
        """Verify bottom bar appears on staged edit and disappears on save or discard."""
        view = TrackDetailView(
            engine=self.engine,
            track_data=self.track_data,
            tracks_list=[self.track_data],
            current_index=0,
        )
        self._views.append(view)
        view.show()
        self.assertFalse(view.bottom_bar.isVisible(), "Bottom bar should be hidden initially")

        # Stage an addition
        view.playlists_container._on_playlist_chosen("Deep House")
        self.assertTrue(view.bottom_bar.isVisible(), "Bottom bar should appear when changes are staged")
        self.assertIn("Deep House", view.current_playlists)

        # Discard changes
        view.discard_btn.click()
        self.assertFalse(view.bottom_bar.isVisible(), "Bottom bar should hide after discarding")
        self.assertEqual(view.current_playlists, ["Afro House"])
        self.assertEqual(view.playlists_container.get_playlists(), ["Afro House"])

        # Stage a removal
        view.playlists_container._on_chip_delete("Afro House")
        self.assertTrue(view.bottom_bar.isVisible(), "Bottom bar should appear after deleting a chip")
        self.assertEqual(view.current_playlists, [])

        # Save changes
        with patch("rekordbox_collection_manager.gui.track_detail_view.is_rekordbox_running", return_value=False):
            view.save_btn.click()

        self.assertFalse(view.bottom_bar.isVisible(), "Bottom bar should hide after saving")
        self.assertEqual(view.original_playlists, [])

        # Verify in SQLite DB
        with self.engine.connect() as conn:
            is_del = conn.exec_driver_sql(
                "SELECT rb_local_deleted FROM djmdSongPlaylist WHERE PlaylistID = 'pl_1' AND ContentID = '10';"
            ).scalar()
            self.assertEqual(is_del, 1, "Track should be marked soft-deleted in database")

    def test_escape_key_handling(self):
        """Verify Escape dismisses search input when active, or navigates back when inactive."""
        view = TrackDetailView(
            engine=self.engine,
            track_data=self.track_data,
            tracks_list=[self.track_data],
            current_index=0,
        )
        self._views.append(view)
        view.show()
        view.playlists_container.show_add_input()
        self.assertTrue(view.playlists_container.is_input_active())

        back_called = []
        view.back_clicked.connect(lambda: back_called.append(True))

        # First Escape: should dismiss search input without navigating back
        esc_event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        view.keyPressEvent(esc_event)
        self.assertFalse(view.playlists_container.is_input_active())
        self.assertEqual(len(back_called), 0)

        # Second Escape: should trigger back navigation
        view.keyPressEvent(esc_event)
        self.assertEqual(len(back_called), 1)

    def test_tag_chip_hover_and_delete(self):
        """Verify '✕' button becomes visible on TagChip hover and emits delete_requested on click."""
        chip = TagChip("Melodic")
        chip.show()
        self.assertFalse(chip.x_btn.isVisible(), "'✕' should be hidden by default")

        # Simulate mouse enter
        enter_ev = QEvent(QEvent.Type.Enter)
        chip.enterEvent(enter_ev)
        self.assertTrue(chip.x_btn.isVisible(), "'✕' should be visible on hover")

        # Click '✕'
        deleted = []
        chip.delete_requested.connect(lambda name: deleted.append(name))
        chip.x_btn.click()
        self.assertEqual(deleted, ["Melodic"])

    def test_tag_container_empty_space_click_opens_autocomplete(self):
        """Verify clicking empty space opens input field and autocompleter in TagChipsContainer."""
        container = TagChipsContainer()
        container.resize(600, 100)
        container.show()
        container.set_available_tags(["Melodic", "Peak Time", "Vocal"])
        container.set_tags(["Melodic"])

        self.assertFalse(container.input_edit.isVisible())
        self.assertEqual(len(container.chips), 1)

        # Trigger show_add_input
        container.show_add_input()
        QApplication.processEvents()
        self.assertTrue(container.input_edit.isVisible())
        self.assertTrue(container.is_input_active())

        # Check candidates (should exclude already assigned 'Melodic')
        candidates = container.completer_model.stringList()
        self.assertIn("Peak Time", candidates)
        self.assertIn("Vocal", candidates)
        self.assertNotIn("Melodic", candidates)

        # Press Escape to hide
        container.hide_add_input()
        self.assertFalse(container.input_edit.isVisible())

    def test_tag_container_select_autocomplete_attaches_chip(self):
        """Verify selecting a tag from autocomplete attaches new TagChip and emits signal."""
        container = TagChipsContainer()
        container.resize(600, 100)
        container.show()
        container.set_available_tags(["Melodic", "Peak Time", "Vocal"])
        container.set_tags(["Melodic"])

        added = []
        container.tag_added.connect(lambda name: added.append(name))

        # Select 'Peak Time'
        container._on_tag_chosen("Peak Time")
        QApplication.processEvents()
        self.assertEqual(added, ["Peak Time"])
        self.assertEqual(container.get_tags(), ["Melodic", "Peak Time"])
        self.assertEqual(len(container.chips), 2)
        self.assertEqual(container.chips[1].name, "Peak Time")

    def test_save_discard_combined_playlists_and_tags(self):
        """Verify bottom bar tracks both playlists and tags and saves to database."""
        view = TrackDetailView(
            engine=self.engine,
            track_data=self.track_data,
            tracks_list=[self.track_data],
            current_index=0,
        )
        self._views.append(view)
        view.show()
        self.assertFalse(view.bottom_bar.isVisible(), "Bottom bar should be hidden initially")

        # Stage a tag addition
        view.tags_container._on_tag_chosen("Peak Time")
        self.assertTrue(view.bottom_bar.isVisible(), "Bottom bar should appear when tag is added")
        self.assertIn("Peak Time", view.current_tags)
        self.assertIn("Tags:", view.staged_status_label.text())

        # Stage a playlist addition
        view.playlists_container._on_playlist_chosen("Deep House")
        self.assertIn("Playlists:", view.staged_status_label.text())
        self.assertIn("Tags:", view.staged_status_label.text())

        # Discard changes
        view.discard_btn.click()
        self.assertFalse(view.bottom_bar.isVisible(), "Bottom bar should hide after discarding")
        self.assertEqual(view.current_playlists, ["Afro House"])
        self.assertEqual(view.current_tags, ["Melodic"])

        # Stage a tag deletion
        view.tags_container._on_chip_delete("Melodic")
        self.assertTrue(view.bottom_bar.isVisible())
        self.assertEqual(view.current_tags, [])

        # Save changes
        with patch("rekordbox_collection_manager.gui.track_detail_view.is_rekordbox_running", return_value=False):
            view.save_btn.click()

        self.assertFalse(view.bottom_bar.isVisible(), "Bottom bar should hide after saving")
        self.assertEqual(view.original_tags, [])

        # Verify in SQLite DB (djmdSongMyTag soft-deleted)
        with self.engine.connect() as conn:
            is_del = conn.exec_driver_sql(
                "SELECT rb_local_deleted FROM djmdSongMyTag WHERE MyTagID = 'tag_1' AND ContentID = '10';"
            ).scalar()
            self.assertEqual(is_del, 1, "Tag association should be marked soft-deleted in database")

    def test_tag_escape_key_handling(self):
        """Verify Escape dismisses tag search input first when active."""
        view = TrackDetailView(
            engine=self.engine,
            track_data=self.track_data,
            tracks_list=[self.track_data],
            current_index=0,
        )
        self._views.append(view)
        view.show()
        view.tags_container.show_add_input()
        self.assertTrue(view.tags_container.is_input_active())

        back_called = []
        view.back_clicked.connect(lambda: back_called.append(True))

        esc_event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        view.keyPressEvent(esc_event)
        self.assertFalse(view.tags_container.is_input_active())
        self.assertEqual(len(back_called), 0)

    def test_table_chip_item_deleted(self):
        """Verify deleting a chip from TracksTableWidget stages change until saved."""
        from unittest.mock import MagicMock
        from rekordbox_collection_manager.gui.track_table import TracksTableWidget, TrackTableConfig

        table = TracksTableWidget(config=TrackTableConfig())
        table.engine = self.engine
        table.records = [{"id": "track_1", "title": "Track 1", "tags": ["Melodic Techno"], "playlists": ["Favorites"]}]

        with patch("rekordbox_collection_manager.gui.track_table.is_rekordbox_running", return_value=False), \
             patch("rekordbox_collection_manager.gui.track_table.remove_track_from_tag") as mock_rm_tag, \
             patch("rekordbox_collection_manager.gui.track_table.remove_track_from_playlist") as mock_rm_pl:
            table._on_chip_item_deleted(0, "tags", "Melodic Techno")
            mock_rm_tag.assert_not_called()
            self.assertTrue(table.has_staged_changes())
            self.assertNotIn("Melodic Techno", table.records[0]["tags"])

            table._on_chip_item_deleted(0, "playlists", "Favorites")
            mock_rm_pl.assert_not_called()
            self.assertNotIn("Favorites", table.records[0]["playlists"])

            saved = table.save_changes()
            self.assertTrue(saved)
            mock_rm_tag.assert_called_once()
            mock_rm_pl.assert_called_once()
            self.assertFalse(table.has_staged_changes())

    def test_store_buttons_staging_and_saving(self):
        """Verify store badge button, popup selection, staging bar, discard, and save in TrackDetailView."""
        track = dict(self.track_data, id="track_store_1", comments="Purchased at Beatport.com", folder_path="/Music/Purchased/test.mp3")
        with self.engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO djmdContent (ID, Title, Commnt) VALUES ('track_store_1', 'Store Test', 'Purchased at Beatport.com')"
            )

        view = TrackDetailView(
            engine=self.engine,
            track_data=track,
            tracks_list=[track],
            current_index=0,
        )
        self._views.append(view)
        view.show()

        # Initial state: parsed Beatport store displayed as interactive pill button (no arrows, no emoji)
        self.assertEqual(view.current_store, "Beatport")
        self.assertTrue(view.store_badge_btn.isVisible())
        self.assertEqual(view.store_badge_btn.text(), "Beatport")
        self.assertEqual(view.purchased_badge.text(), "Purchased")
        self.assertFalse(view.bottom_bar.isVisible())

        # Select Qobuz -> stages Qobuz comment
        view._on_store_selected("Qobuz")
        self.assertEqual(view.current_store, "Qobuz")
        self.assertEqual(view.current_comment, "Purchased at qobuz.com")
        self.assertEqual(view.store_badge_btn.text(), "Qobuz")
        self.assertTrue(view.bottom_bar.isVisible())
        self.assertIn("Store: Qobuz", view.staged_status_label.text())

        # Discard changes -> reverts to Beatport
        view.discard_btn.click()
        self.assertEqual(view.current_store, "Beatport")
        self.assertEqual(view.current_comment, "Purchased at Beatport.com")
        self.assertEqual(view.store_badge_btn.text(), "Beatport")
        self.assertFalse(view.bottom_bar.isVisible())

        # Clicking badge when store is set prompts confirmation and clears if confirmed
        with patch("PySide6.QtWidgets.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            view.store_badge_btn.click()
        self.assertIsNone(view.current_store)
        self.assertEqual(view.current_comment, "")
        self.assertEqual(view.store_badge_btn.text(), "+ Store")
        self.assertTrue(view.bottom_bar.isVisible())

        # Click Save Changes -> commits to database
        with patch("rekordbox_collection_manager.gui.track_detail_view.is_rekordbox_running", return_value=False):
            view.save_btn.click()

        self.assertFalse(view.bottom_bar.isVisible())
        with self.engine.connect() as conn:
            row = conn.exec_driver_sql(
                "SELECT Commnt FROM djmdContent WHERE ID = 'track_store_1'"
            ).mappings().first()
            self.assertEqual(row["Commnt"], "")

    def test_store_filter_visibility_in_tracks_view(self):
        """Verify store filter and Purchased At column visibility in TracksView."""
        t_view = TracksView()
        t_view.engine = self.engine
        self._views.append(t_view)
        t_view.show()

        # By default, purchase_combo is 'All Statuses' (index 0)
        self.assertEqual(t_view.purchase_combo.currentData(), "all")
        self.assertTrue(t_view.store_filter_widget.isHidden())
        self.assertFalse(t_view.table.is_column_visible("Purchased At"))

        # Switch to '🛒 Purchased Only'
        idx_purchased = t_view.purchase_combo.findData("purchased")
        t_view.purchase_combo.setCurrentIndex(idx_purchased)

        self.assertFalse(t_view.store_filter_widget.isHidden())
        self.assertTrue(t_view.table.is_column_visible("Purchased At"))

        # Switch to '📥 Downloaded'
        idx_downloaded = t_view.purchase_combo.findData("downloaded")
        t_view.purchase_combo.setCurrentIndex(idx_downloaded)

        self.assertTrue(t_view.store_filter_widget.isHidden())
        self.assertFalse(t_view.table.is_column_visible("Purchased At"))
        self.assertFalse(t_view.play_filter_combo.isHidden())

        # Switch to '💳 Purchasable (≥3 plays)'
        idx_purchasable = t_view.purchase_combo.findData("purchasable")
        self.assertGreater(idx_purchasable, -1)
        # Select played filter first to check that it gets reset
        t_view.play_filter_combo.setCurrentIndex(1)
        t_view.purchase_combo.setCurrentIndex(idx_purchasable)

        # play_filter_combo should be hidden and reset to 'All'
        self.assertTrue(t_view.play_filter_combo.isHidden())
        self.assertEqual(t_view.play_filter_combo.currentIndex(), 0)
        self.assertTrue(t_view.store_filter_widget.isHidden())
        self.assertFalse(t_view.table.is_column_visible("Purchased At"))

        # Switch back to 'All Statuses' -> play_filter_combo reappears
        t_view.purchase_combo.setCurrentIndex(0)
        self.assertFalse(t_view.play_filter_combo.isHidden())

        # Test reset button restores visibility
        t_view.purchase_combo.setCurrentIndex(idx_purchasable)
        self.assertTrue(t_view.play_filter_combo.isHidden())
        t_view._on_reset_filters()
        self.assertFalse(t_view.play_filter_combo.isHidden())
        self.assertEqual(t_view.purchase_combo.currentIndex(), 0)

    def test_purchased_chip_and_store_chip_components(self):
        """Verify PurchasedChip and StoreChip styling, scheme changes, and click events."""
        # PurchasedChip states
        p_chip = PurchasedChip(is_purchased=True)
        self.assertEqual(p_chip.text(), "Purchased")
        self.assertEqual(p_chip.scheme_name, "green")
        self.assertFalse(p_chip.clickable)

        p_chip.set_purchased(False)
        self.assertEqual(p_chip.text(), "Downloaded")
        self.assertEqual(p_chip.scheme_name, "slate")

        # StoreChip unset state
        s_chip = StoreChip(store=None)
        self.assertEqual(s_chip.text(), "+ Store")
        self.assertEqual(s_chip.scheme_name, "slate")
        self.assertTrue(s_chip.clickable)

        # StoreChip set state
        clicked_events = []
        s_chip.clicked.connect(clicked_events.append)
        s_chip.set_store("Traxsource")
        self.assertEqual(s_chip.text(), "Traxsource")
        self.assertEqual(s_chip.scheme_name, "indigo")

        s_chip.click()
        self.assertEqual(clicked_events, ["Traxsource"])

        # StoreChip cleared state
        s_chip.set_store(None)
        self.assertEqual(s_chip.text(), "+ Store")
        self.assertEqual(s_chip.scheme_name, "slate")


if __name__ == "__main__":
    unittest.main()

