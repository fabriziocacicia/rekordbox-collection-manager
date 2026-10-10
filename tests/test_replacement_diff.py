"""Unit tests for the 3-column track replacement diff tool & metadata conflict resolution."""

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Headless Qt for unit testing
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from sqlalchemy import create_engine, text

from rekordbox_collection_manager.gui.replacement_diff_dialog import (
    FieldDiff,
    TrackReplacementDiffDialog,
    TrackReplacementDiffView,
    calculate_metadata_diff,
)
from rekordbox_collection_manager.replace_file import (
    _apply_resolved_metadata,
    fetch_track_full_info,
    merge_imported_tracks,
    replace_track_audio_file,
)

app = QApplication.instance()
if app is None:
    app = QApplication([])


class TestMetadataDiffCalculation(unittest.TestCase):
    """Test field-by-field metadata comparison and conflict detection."""

    def test_calculate_metadata_diff_conflicts_and_matches(self):
        old_data = {
            "title": "Solaris (Original Mix)",
            "artist": "Starlight",
            "album": "Galactic Ep",
            "genre": "Trance",
            "bpm": 138.0,
            "key": "8A",
            "rating": 4,
            "release_year": 2021,
            "track_no": 1,
            "disc_no": 1,
            "remixer": "",
            "label": "Armada",
            "mix_name": "Original Mix",
            "composer": "John Doe",
            "comment": "Club Anthem",
            "color_id": "1",
            "file_type": "MP3",
            "bitrate": 320,
            "sample_rate": 44100,
            "bit_depth": 16,
            "duration_sec": 420,
            "duration_str": "07:00",
            "file_size": 16000000,
            "file_path": "/Music/old.mp3",
            "play_count": 25,
            "date_added": "2021-05-10",
        }

        new_data = {
            "title": "Solaris (Club Mix)",  # Conflict
            "artist": "Starlight",  # Match
            "album": "Galactic Ep",  # Match
            "genre": "Uplifting Trance",  # Conflict
            "bpm": 138.0,  # Match
            "key": "8A",  # Match
            "rating": 5,  # Conflict
            "release_year": 2021,  # Match
            "track_no": 1,  # Match
            "disc_no": 1,  # Match
            "remixer": "Ferry",  # New data (was empty)
            "label": "Armada Music",  # Conflict
            "mix_name": "Club Mix",  # Conflict
            "composer": "",  # Kept old (new is empty)
            "comment": "Club Anthem",  # Match
            "color_id": "1",  # Match
            "format_name": "AIFF",
            "file_type": "AIFF",
            "bitrate": 1411,
            "sample_rate": 44100,
            "bit_depth": 16,
            "duration_sec": 420,
            "duration_str": "07:00",
            "file_size": 74000000,
            "file_path": "/Downloads/new.aiff",
        }

        diffs = calculate_metadata_diff(old_data, new_data, mode="file")
        self.assertTrue(len(diffs) > 0)

        diff_dict = {d.key: d for d in diffs}

        # Verify genre, bpm, key are excluded from conflict resolution view
        self.assertNotIn("genre", diff_dict)
        self.assertNotIn("bpm", diff_dict)
        self.assertNotIn("key", diff_dict)

        # Check title conflict
        title_diff = diff_dict["title"]
        self.assertEqual(title_diff.status, "conflict")
        self.assertEqual(title_diff.origin, "old")
        self.assertEqual(title_diff.result_val, "Solaris (Original Mix)")

        # Check artist match
        artist_diff = diff_dict["artist"]
        self.assertEqual(artist_diff.status, "match")
        self.assertTrue(artist_diff.is_match)

        # Check remixer new data
        remixer_diff = diff_dict["remixer"]
        self.assertEqual(remixer_diff.status, "new_data")
        self.assertEqual(remixer_diff.origin, "new")
        self.assertEqual(remixer_diff.result_val, "Ferry")

        # Check composer kept old
        composer_diff = diff_dict["composer"]
        self.assertEqual(composer_diff.status, "kept_old")
        self.assertEqual(composer_diff.origin, "old")
        self.assertEqual(composer_diff.result_val, "John Doe")

        # Check audio specs
        audio_diffs = [d for d in diffs if d.category == "audio"]
        self.assertTrue(len(audio_diffs) >= 5)
        fmt_diff = next(d for d in audio_diffs if d.key == "file_type")
        self.assertEqual(fmt_diff.old_val, "MP3")
        self.assertEqual(fmt_diff.new_val, "AIFF")
        self.assertEqual(fmt_diff.result_val, "AIFF")

        # Check preservation
        pres_diffs = [d for d in diffs if d.category == "preservation"]
        pc_diff = next(d for d in pres_diffs if d.key == "play_count")
        self.assertIn("25", str(pc_diff.result_val))

    def test_field_diff_editing(self):
        fd = FieldDiff(
            key="title",
            label="Track Title",
            category="info",
            old_val="Old Song",
            new_val="New Song",
            default_origin="old",
        )
        self.assertEqual(fd.status, "conflict")
        self.assertEqual(fd.result_val, "Old Song")

        # Simulate user choosing NEW
        fd.result_val = fd.new_val
        fd.origin = "new"
        self.assertEqual(fd.result_val, "New Song")

        # Simulate user custom typing
        fd.result_val = "Custom Song (Remastered)"
        fd.origin = "custom"
        self.assertEqual(fd.result_val, "Custom Song (Remastered)")


class TestApplyResolvedMetadata(unittest.TestCase):
    """Test backend database persistence of resolved conflict metadata."""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        with self.engine.begin() as conn:
            conn.exec_driver_sql("""
                CREATE TABLE djmdContent (
                    ID VARCHAR(255) PRIMARY KEY,
                    Title VARCHAR(255),
                    ArtistID VARCHAR(255),
                    AlbumID VARCHAR(255),
                    GenreID VARCHAR(255),
                    KeyID VARCHAR(255),
                    RemixerID VARCHAR(255),
                    ComposerID VARCHAR(255),
                    LabelID VARCHAR(255),
                    Subtitle VARCHAR(255),
                    Commnt VARCHAR(255),
                    BPM INTEGER,
                    Rating INTEGER,
                    ReleaseYear INTEGER,
                    TrackNo INTEGER,
                    DiscNo INTEGER,
                    ColorID VARCHAR(255),
                    rb_local_deleted SMALLINT DEFAULT 0,
                    created_at DATETIME,
                    updated_at DATETIME
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdArtist (
                    ID VARCHAR(255) PRIMARY KEY,
                    Name VARCHAR(255),
                    SearchStr VARCHAR(255),
                    UUID VARCHAR(255),
                    rb_local_deleted SMALLINT DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdAlbum (
                    ID VARCHAR(255) PRIMARY KEY,
                    Name VARCHAR(255),
                    SearchStr VARCHAR(255),
                    UUID VARCHAR(255),
                    rb_local_deleted SMALLINT DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdGenre (
                    ID VARCHAR(255) PRIMARY KEY,
                    Name VARCHAR(255),
                    UUID VARCHAR(255),
                    rb_local_deleted SMALLINT DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdKey (
                    ID VARCHAR(255) PRIMARY KEY,
                    ScaleName VARCHAR(255)
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdLabel (
                    ID VARCHAR(255) PRIMARY KEY,
                    Name VARCHAR(255),
                    UUID VARCHAR(255),
                    rb_local_deleted SMALLINT DEFAULT 0
                );
            """)
            # Populate initial key
            conn.exec_driver_sql("INSERT INTO djmdKey (ID, ScaleName) VALUES ('k1', '8A');")
            conn.exec_driver_sql("INSERT INTO djmdKey (ID, ScaleName) VALUES ('k2', '9B');")

            # Insert initial track
            conn.exec_driver_sql("""
                INSERT INTO djmdContent (ID, Title, BPM, Rating, ReleaseYear, TrackNo, DiscNo, Commnt)
                VALUES ('track-01', 'Initial Title', 12000, 3, 2020, 1, 1, 'Old Note');
            """)

    def test_apply_resolved_metadata_creates_relations(self):
        resolved = {
            "title": "Brand New Title",
            "artist": "Armin van Buuren",
            "album": "A State of Trance",
            "genre": "Trance",
            "bpm": 138.5,
            "key": "8A",
            "rating": 5,
            "release_year": 2024,
            "track_no": 2,
            "disc_no": 1,
            "remixer": "Jorn van Deynhoven",
            "label": "Armada Music",
            "mix_name": "Extended Club Mix",
            "comment": "Upgraded Lossless",
            "color_id": "3",
        }

        with self.engine.begin() as conn:
            cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdContent);").fetchall()}
            tables = {"djmdArtist", "djmdAlbum", "djmdGenre", "djmdKey", "djmdLabel"}
            _apply_resolved_metadata(conn, cols, tables, "track-01", resolved, "2026-10-09 12:00:00")

        # Verify Content row updated
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT Title, BPM, Rating, ReleaseYear, Subtitle, Commnt, ColorID FROM djmdContent WHERE ID = 'track-01';")).fetchone()
            self.assertEqual(row[0], "Brand New Title")
            self.assertEqual(row[1], 13850)  # BPM 138.5 * 100
            self.assertEqual(row[2], 5)
            self.assertEqual(row[3], 2024)
            self.assertEqual(row[4], "Extended Club Mix")
            self.assertEqual(row[5], "Upgraded Lossless")
            self.assertEqual(row[6], "3")

            # Verify Artist was created and linked
            artist_id_row = conn.execute(text("SELECT ArtistID, RemixerID, AlbumID, GenreID, KeyID, LabelID FROM djmdContent WHERE ID = 'track-01';")).fetchone()
            self.assertIsNotNone(artist_id_row[0])
            self.assertIsNotNone(artist_id_row[1])
            self.assertIsNotNone(artist_id_row[2])
            self.assertIsNotNone(artist_id_row[3])
            self.assertEqual(artist_id_row[4], "k1")
            self.assertIsNotNone(artist_id_row[5])

            artist_name = conn.execute(text("SELECT Name FROM djmdArtist WHERE ID = :id;"), {"id": artist_id_row[0]}).fetchone()[0]
            self.assertEqual(artist_name, "Armin van Buuren")


class TestTrackReplacementDiffDialog(unittest.TestCase):
    """Test GUI dialog view and preset actions."""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        with self.engine.begin() as conn:
            conn.exec_driver_sql("""
                CREATE TABLE djmdContent (
                    ID VARCHAR(255) PRIMARY KEY,
                    Title VARCHAR(255),
                    ArtistID VARCHAR(255),
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
                    DJPlayCount INTEGER,
                    StockDate VARCHAR(255),
                    FolderPath VARCHAR(255),
                    FileNameL VARCHAR(255),
                    rb_local_deleted SMALLINT DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdSongHistory (
                    ID VARCHAR(255) PRIMARY KEY,
                    HistoryID VARCHAR(255),
                    ContentID VARCHAR(255),
                    TrackNo INTEGER,
                    rb_local_deleted SMALLINT DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdPlaylist (
                    ID VARCHAR(255) PRIMARY KEY,
                    Name VARCHAR(255),
                    Attribute INTEGER DEFAULT 0,
                    rb_local_deleted SMALLINT DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdSongPlaylist (
                    ID VARCHAR(255) PRIMARY KEY,
                    PlaylistID VARCHAR(255),
                    ContentID VARCHAR(255),
                    TrackNo INTEGER,
                    rb_local_deleted SMALLINT DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdMyTag (
                    ID VARCHAR(255) PRIMARY KEY,
                    Name VARCHAR(255),
                    Attribute INTEGER DEFAULT 0,
                    rb_local_deleted SMALLINT DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdSongMyTag (
                    ID VARCHAR(255) PRIMARY KEY,
                    MyTagID VARCHAR(255),
                    ContentID VARCHAR(255),
                    rb_local_deleted SMALLINT DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdCue (
                    ID VARCHAR(255) PRIMARY KEY,
                    ContentID VARCHAR(255),
                    InMsec INTEGER,
                    Kind INTEGER,
                    rb_local_deleted SMALLINT DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdContent (ID, Title, BPM, Length, FileType, DJPlayCount, StockDate, FolderPath, FileNameL)
                VALUES ('t1', 'Track A', 12000, 300, 1, 10, '2023-01-01', '/music', 'track_a.mp3');
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdContent (ID, Title, BPM, Length, FileType, DJPlayCount, StockDate, FolderPath, FileNameL)
                VALUES ('t2', 'Track B Target', 12400, 320, 2, 2, '2024-02-01', '/music', 'track_b.aiff');
            """)

            # Add 3 history entries for t1
            conn.exec_driver_sql("INSERT INTO djmdSongHistory (ID, HistoryID, ContentID) VALUES ('sh1', 'h1', 't1');")
            conn.exec_driver_sql("INSERT INTO djmdSongHistory (ID, HistoryID, ContentID) VALUES ('sh2', 'h2', 't1');")
            conn.exec_driver_sql("INSERT INTO djmdSongHistory (ID, HistoryID, ContentID) VALUES ('sh3', 'h3', 't1');")

            # Add 2 playlists for t1, 1 for t2
            conn.exec_driver_sql("INSERT INTO djmdPlaylist (ID, Name, Attribute) VALUES ('p1', 'Summer 2024', 0);")
            conn.exec_driver_sql("INSERT INTO djmdPlaylist (ID, Name, Attribute) VALUES ('p2', 'Club Favorites', 0);")
            conn.exec_driver_sql("INSERT INTO djmdPlaylist (ID, Name, Attribute) VALUES ('p3', 'Lossless Upgrade', 0);")
            conn.exec_driver_sql("INSERT INTO djmdSongPlaylist (ID, PlaylistID, ContentID) VALUES ('sp1', 'p1', 't1');")
            conn.exec_driver_sql("INSERT INTO djmdSongPlaylist (ID, PlaylistID, ContentID) VALUES ('sp2', 'p2', 't1');")
            conn.exec_driver_sql("INSERT INTO djmdSongPlaylist (ID, PlaylistID, ContentID) VALUES ('sp3', 'p3', 't2');")

            # Add 1 tag for t1
            conn.exec_driver_sql("INSERT INTO djmdMyTag (ID, Name, Attribute) VALUES ('tag1', 'Peak Time', 0);")
            conn.exec_driver_sql("INSERT INTO djmdSongMyTag (ID, MyTagID, ContentID) VALUES ('smt1', 'tag1', 't1');")

            # Add 4 cues for t1, 2 for t2
            conn.exec_driver_sql("INSERT INTO djmdCue (ID, ContentID, InMsec) VALUES ('c1', 't1', 1000);")
            conn.exec_driver_sql("INSERT INTO djmdCue (ID, ContentID, InMsec) VALUES ('c2', 't1', 2000);")
            conn.exec_driver_sql("INSERT INTO djmdCue (ID, ContentID, InMsec) VALUES ('c3', 't1', 3000);")
            conn.exec_driver_sql("INSERT INTO djmdCue (ID, ContentID, InMsec) VALUES ('c4', 't1', 4000);")
            conn.exec_driver_sql("INSERT INTO djmdCue (ID, ContentID, InMsec) VALUES ('c5', 't2', 1500);")
            conn.exec_driver_sql("INSERT INTO djmdCue (ID, ContentID, InMsec) VALUES ('c6', 't2', 2500);")

    def test_fetch_track_full_info_relational_counts(self):
        info = fetch_track_full_info(self.engine, "t1")
        self.assertEqual(info["history_count"], 3)
        self.assertEqual(info["playlist_count"], 2)
        self.assertEqual(sorted(info["playlists"]), ["Club Favorites", "Summer 2024"])
        self.assertEqual(info["tag_count"], 1)
        self.assertEqual(info["tags"], ["Peak Time"])
        self.assertEqual(info["cue_count"], 4)

    def test_dialog_instantiation_mode_file(self):
        old_track = {
            "id": "t1",
            "title": "Track A",
            "artist": "Artist A",
            "bpm": 120.0,
            "file_type": "MP3",
            "play_count": 10,
        }
        new_item = {
            "title": "Track A (2024 Remaster)",
            "artist": "Artist A",
            "bpm": 120.0,
            "format_name": "AIFF",
            "file_type": "AIFF",
            "file_size": 50000000,
            "duration_sec": 300,
            "duration_str": "05:00",
            "bitrate": 1411,
            "bit_depth": 16,
            "sample_rate": 44100,
            "file_name": "track_a.aiff",
            "file_path": "/downloads/track_a.aiff",
        }

        dialog = TrackReplacementDiffDialog(
            engine=self.engine,
            mode="file",
            old_track=old_track,
            new_item=new_item,
            options={"old_file_action": "keep", "move_new_file_to_track_folder": False},
        )
        self.assertIsNotNone(dialog)
        self.assertEqual(len(dialog.diff_view.diffs), 26)

        diffs = {d.key: d for d in dialog.diff_view.diffs}
        self.assertNotIn("genre", diffs)
        self.assertNotIn("bpm", diffs)
        self.assertNotIn("key", diffs)
        self.assertEqual(diffs["history_sessions"].old_val, "3 sets")
        self.assertEqual(diffs["history_sessions"].result_val, "Preserved (3 sets)")
        self.assertEqual(diffs["playlists"].old_val, "2 playlists")
        self.assertEqual(diffs["playlists"].result_val, "Preserved (2 playlists)")
        self.assertEqual(diffs["tags"].old_val, "1 tag")
        self.assertEqual(diffs["tags"].result_val, "Preserved (1 tag)")
        self.assertEqual(diffs["cues"].old_val, "4 cues")
        self.assertEqual(diffs["cues"].result_val, "Preserved (4 cues)")

        # Check presets: Apply "new"
        dialog.diff_view._apply_global_preset("new")
        resolved = dialog.get_resolved_metadata()
        self.assertEqual(resolved["title"], "Track A (2024 Remaster)")
        self.assertNotIn("genre", resolved)
        self.assertNotIn("bpm", resolved)
        self.assertNotIn("key", resolved)

        # Check presets: Apply "old"
        dialog.diff_view._apply_global_preset("old")
        resolved = dialog.get_resolved_metadata()
        self.assertEqual(resolved["title"], "Track A")
        self.assertNotIn("genre", resolved)
        self.assertNotIn("bpm", resolved)
        self.assertNotIn("key", resolved)

        # Check options
        opts = dialog.get_options()
        self.assertEqual(opts["old_file_action"], "keep")
        self.assertIn("resolved_metadata", opts)
        self.assertNotIn("genre", opts["resolved_metadata"])
        self.assertNotIn("bpm", opts["resolved_metadata"])
        self.assertNotIn("key", opts["resolved_metadata"])

    @patch("rekordbox_collection_manager.gui.replacement_diff_dialog.is_rekordbox_running", return_value=False)
    def test_dialog_instantiation_mode_merge(self, mock_running):
        old_track = {
            "id": "t1",
            "title": "Old Track",
            "artist": "Artist X",
            "play_count": 55,
            "date_added": "2020-01-01",
        }
        target_track = {
            "id": "t2",
            "title": "Target Track AIFF",
            "artist": "Artist X",
            "play_count": 2,
            "date_added": "2024-02-01",
        }

        dialog = TrackReplacementDiffDialog(
            engine=self.engine,
            mode="merge",
            old_track=old_track,
            new_item=target_track,
            options={"old_file_action": "keep"},
        )
        self.assertIsNotNone(dialog)
        self.assertEqual(dialog.diff_view.mode, "merge")

        diffs = {d.key: d for d in dialog.diff_view.diffs}
        # Mode merge preserves old track history, tags, playlists
        self.assertEqual(diffs["history_sessions"].old_val, "3 sets")
        self.assertEqual(diffs["history_sessions"].new_val, "0 sets")
        self.assertEqual(diffs["playlists"].old_val, "2 playlists")
        self.assertEqual(diffs["playlists"].new_val, "1 playlist")
        self.assertEqual(diffs["tags"].old_val, "1 tag")
        self.assertEqual(diffs["cues"].old_val, "4 cues")
        self.assertEqual(diffs["cues"].new_val, "2 cues")

        # Simulate confirm
        dialog.diff_view._on_confirm_clicked()
        self.assertTrue(dialog.result() == 1 or dialog.resolved_metadata is not None)

    def test_genre_bpm_key_not_ported_during_merge(self):
        # Target t2 initially has BPM=12400
        options = {
            "old_file_action": "keep",
            "resolved_metadata": {
                "title": "Merged Title",
                "genre": "Rock",
                "bpm": 150.0,
                "key": "8A",
            },
        }
        res = merge_imported_tracks(self.engine, "t1", "t2", options=options)
        self.assertTrue(res["success"])

        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT Title, BPM, GenreID, KeyID FROM djmdContent WHERE ID = 't2';")).fetchone()
            # Title was updated via resolved_metadata
            self.assertEqual(row[0], "Merged Title")
            # BPM was NOT ported/overwritten (retains target track's original 12400)
            self.assertEqual(row[1], 12400)
            # GenreID and KeyID were NOT set/ported from resolved_metadata
            self.assertIsNone(row[2])
            self.assertIsNone(row[3])


if __name__ == "__main__":
    unittest.main()
