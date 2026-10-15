"""Tests for history_import module."""

import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, text

from rekordbox_collection_manager.history_import import (
    TrackDiffItem,
    analyze_external_history_db,
    build_current_track_index,
    calculate_history_session_diff,
    execute_history_merge_plan,
    format_duration,
    match_track_to_current,
    normalize_string,
)


class TestHistoryImport(unittest.TestCase):
    def setUp(self):
        # Create an in-memory SQLite database simulating current Rekordbox DB
        self.cur_engine = create_engine("sqlite:///:memory:")
        with self.cur_engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE djmdContent (
                    ID VARCHAR(255) PRIMARY KEY,
                    Title VARCHAR(255),
                    ArtistID VARCHAR(255),
                    SrcArtistName VARCHAR(255),
                    AlbumID VARCHAR(255),
                    FileNameL VARCHAR(255),
                    FolderPath VARCHAR(255),
                    Length INTEGER,
                    BPM INTEGER,
                    Rating INTEGER,
                    ReleaseYear INTEGER,
                    Commnt VARCHAR(255),
                    DJPlayCount INTEGER DEFAULT 0,
                    rb_local_deleted INTEGER DEFAULT 0,
                    created_at DATETIME,
                    updated_at DATETIME
                );
            """))
            conn.execute(text("""
                CREATE TABLE djmdArtist (
                    ID VARCHAR(255) PRIMARY KEY,
                    Name VARCHAR(255)
                );
            """))
            conn.execute(text("""
                CREATE TABLE djmdHistory (
                    ID VARCHAR(255) PRIMARY KEY,
                    Name VARCHAR(255),
                    ParentID VARCHAR(255),
                    Attribute INTEGER,
                    DateCreated VARCHAR(255),
                    Seq INTEGER,
                    rb_local_deleted INTEGER DEFAULT 0,
                    created_at DATETIME,
                    updated_at DATETIME
                );
            """))
            conn.execute(text("""
                CREATE TABLE djmdSongHistory (
                    ID VARCHAR(255) PRIMARY KEY,
                    HistoryID VARCHAR(255),
                    ContentID VARCHAR(255),
                    TrackNo INTEGER,
                    rb_local_deleted INTEGER DEFAULT 0,
                    created_at DATETIME,
                    updated_at DATETIME
                );
            """))

            # Populate current DB
            conn.execute(text("INSERT INTO djmdArtist (ID, Name) VALUES ('art1', 'Rampa');"))
            conn.execute(text("INSERT INTO djmdArtist (ID, Name) VALUES ('art2', 'Adam Port');"))
            conn.execute(text("""
                INSERT INTO djmdContent (ID, Title, ArtistID, FileNameL, Length, DJPlayCount, created_at, updated_at)
                VALUES ('c1', 'Say What', 'art1', 'Say What.mp3', 360, 5, '2025-01-01', '2025-01-01');
            """))
            conn.execute(text("""
                INSERT INTO djmdContent (ID, Title, ArtistID, FileNameL, Length, DJPlayCount, created_at, updated_at)
                VALUES ('c2', 'Move', 'art2', 'Move.aiff', 320, 2, '2025-01-01', '2025-01-01');
            """))

            # Active history session with 1 track
            conn.execute(text("""
                INSERT INTO djmdHistory (ID, Name, DateCreated, Attribute, ParentID, rb_local_deleted)
                VALUES ('h_active', 'HISTORY 2025-07-05', '2025-07-05 15:36:36', 0, '202507', 0);
            """))
            conn.execute(text("""
                INSERT INTO djmdSongHistory (ID, HistoryID, ContentID, TrackNo, created_at)
                VALUES ('sh1', 'h_active', 'c1', 1, '2025-07-05 15:40:00');
            """))

            # Deleted history session (in trash)
            conn.execute(text("""
                INSERT INTO djmdHistory (ID, Name, DateCreated, Attribute, ParentID, rb_local_deleted)
                VALUES ('h_deleted', 'HISTORY 2025-08-04', '2025-08-04 21:38:11', 0, '202508', 1);
            """))

    def test_normalize_string(self):
        self.assertEqual(normalize_string("Say What (Original Mix)"), "say what original mix")
        self.assertEqual(normalize_string("  Adam   Port feat. Stryv  "), "adam port feat stryv")
        self.assertEqual(normalize_string(None), "")

    def test_format_duration(self):
        self.assertEqual(format_duration(360), "6:00")
        self.assertEqual(format_duration(65), "1:05")
        self.assertEqual(format_duration(0), "--:--")

    def test_track_index_and_matching(self):
        index = build_current_track_index(self.cur_engine)
        self.assertIn("c1", index["by_id"])
        self.assertIn("say what.mp3", index["by_filename"])

        # Match by ID
        m1 = match_track_to_current({"content_id": "c1"}, index)
        self.assertEqual(m1, "c1")

        # Match by filename
        m2 = match_track_to_current({"content_id": "other", "file_name": "Move.aiff"}, index)
        self.assertEqual(m2, "c2")

        # Match by Title + Artist
        m3 = match_track_to_current({
            "content_id": "999",
            "title": "Say What",
            "artist": "Rampa",
            "duration_sec": 360,
        }, index)
        self.assertEqual(m3, "c1")

        # Unmatched track
        m4 = match_track_to_current({
            "content_id": "999",
            "title": "Unknown Song",
            "artist": "Nobody",
        }, index)
        self.assertIsNone(m4)

    def test_calculate_history_session_diff(self):
        index = build_current_track_index(self.cur_engine)

        current_tracks = [
            {
                "track_no": 1,
                "played_at": "2025-07-05 15:40:00",
                "played_time": "15:40:00",
                "content_id": "c1",
                "title": "Say What",
                "artist": "Rampa",
                "album": "Keinemusik",
                "duration_sec": 360,
                "duration_str": "6:00",
                "file_path": "/Music/Say What.mp3",
                "file_name": "Say What.mp3",
                "bpm": 122.0,
            }
        ]

        external_tracks = [
            # Track 1: Match with current
            {
                "track_no": 1,
                "played_at": "2025-07-05 15:40:00",
                "played_time": "15:40:00",
                "content_id": "c1",
                "title": "Say What",
                "artist": "Rampa",
                "album": "Keinemusik",
                "duration_sec": 360,
                "duration_str": "6:00",
                "file_path": "/Music/Say What.mp3",
                "file_name": "Say What.mp3",
                "bpm": 122.0,
            },
            # Track 2: New track, available in current collection
            {
                "track_no": 2,
                "played_at": "2025-07-05 15:46:00",
                "played_time": "15:46:00",
                "content_id": "ext_c2",
                "title": "Move",
                "artist": "Adam Port",
                "album": "Keinemusik",
                "duration_sec": 320,
                "duration_str": "5:20",
                "file_path": "/Old/Move.aiff",
                "file_name": "Move.aiff",
                "bpm": 124.0,
            },
            # Track 3: New track, missing from collection
            {
                "track_no": 3,
                "played_at": "2025-07-05 15:52:00",
                "played_time": "15:52:00",
                "content_id": "ext_c3",
                "title": "Missing Track",
                "artist": "Old Artist",
                "album": "Old Album",
                "duration_sec": 300,
                "duration_str": "5:00",
                "file_path": "/Old/Missing.mp3",
                "file_name": "Missing.mp3",
                "bpm": 120.0,
            },
        ]

        diff_res = calculate_history_session_diff(current_tracks, external_tracks, index)

        self.assertEqual(diff_res["stats"]["matched_count"], 1)
        self.assertEqual(diff_res["stats"]["new_count"], 1)
        self.assertEqual(diff_res["stats"]["missing_file_count"], 1)
        self.assertEqual(diff_res["stats"]["total_result_count"], 3)

        merged = diff_res["merged_column"]
        self.assertEqual(len(merged), 3)
        self.assertEqual(merged[0].title, "Say What")
        self.assertEqual(merged[0].track_no, 1)
        self.assertEqual(merged[1].title, "Move")
        self.assertEqual(merged[1].track_no, 2)
        self.assertEqual(merged[1].matched_content_id, "c2")
        self.assertEqual(merged[2].title, "Missing Track")
        self.assertEqual(merged[2].track_no, 3)

    @patch("rekordbox_collection_manager.history_import.open_database")
    def test_analyze_external_history_db(self, mock_open_db):
        ext_engine = create_engine("sqlite:///:memory:")
        with ext_engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE djmdHistory (
                    ID VARCHAR(255) PRIMARY KEY,
                    Name VARCHAR(255),
                    ParentID VARCHAR(255),
                    Attribute INTEGER,
                    DateCreated VARCHAR(255),
                    Seq INTEGER,
                    rb_local_deleted INTEGER DEFAULT 0,
                    created_at DATETIME,
                    updated_at DATETIME
                );
            """))
            conn.execute(text("""
                CREATE TABLE djmdSongHistory (
                    ID VARCHAR(255) PRIMARY KEY,
                    HistoryID VARCHAR(255),
                    ContentID VARCHAR(255),
                    TrackNo INTEGER,
                    rb_local_deleted INTEGER DEFAULT 0,
                    created_at DATETIME,
                    updated_at DATETIME
                );
            """))
            # Session 1: matches h_active (to_merge, has 2 tracks vs 1 in cur)
            conn.execute(text("""
                INSERT INTO djmdHistory (ID, Name, DateCreated, Attribute, ParentID)
                VALUES ('h_ext_1', 'HISTORY 2025-07-05', '2025-07-05 15:36:36', 0, '202507');
            """))
            conn.execute(text("""
                INSERT INTO djmdSongHistory (ID, HistoryID, ContentID, TrackNo)
                VALUES ('s1', 'h_ext_1', 'c1', 1);
            """))
            conn.execute(text("""
                INSERT INTO djmdSongHistory (ID, HistoryID, ContentID, TrackNo)
                VALUES ('s2', 'h_ext_1', 'c2', 2);
            """))

            # Session 2: matches h_deleted (in_trash)
            conn.execute(text("""
                INSERT INTO djmdHistory (ID, Name, DateCreated, Attribute, ParentID)
                VALUES ('h_ext_2', 'HISTORY 2025-08-04', '2025-08-04 21:38:11', 0, '202508');
            """))
            conn.execute(text("""
                INSERT INTO djmdSongHistory (ID, HistoryID, ContentID, TrackNo)
                VALUES ('s3', 'h_ext_2', 'c1', 1);
            """))

            # Session 3: completely new session
            conn.execute(text("""
                INSERT INTO djmdHistory (ID, Name, DateCreated, Attribute, ParentID)
                VALUES ('h_ext_3', 'HISTORY 2026-05-01', '2026-05-01 20:00:00', 0, '202605');
            """))

        mock_open_db.return_value = (ext_engine, "djmdHistory")

        analysis = analyze_external_history_db(self.cur_engine, "/fake/path/old_master.db")

        self.assertEqual(analysis.total_external_sessions, 3)
        self.assertEqual(analysis.counts["to_merge"], 1)
        self.assertEqual(analysis.counts["in_trash"], 1)
        self.assertEqual(analysis.counts["new"], 1)
        self.assertEqual(analysis.counts["identical"], 0)

    def test_execute_history_merge_plan(self):
        # Create external engine
        ext_engine = create_engine("sqlite:///:memory:")
        with ext_engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE djmdContent (
                    ID VARCHAR(255) PRIMARY KEY,
                    Title VARCHAR(255),
                    ArtistID VARCHAR(255),
                    SrcArtistName VARCHAR(255),
                    AlbumID VARCHAR(255),
                    FileNameL VARCHAR(255),
                    FolderPath VARCHAR(255),
                    Length INTEGER,
                    BPM INTEGER,
                    Rating INTEGER,
                    ReleaseYear INTEGER,
                    Commnt VARCHAR(255),
                    DJPlayCount INTEGER DEFAULT 0,
                    rb_local_deleted INTEGER DEFAULT 0,
                    created_at DATETIME,
                    updated_at DATETIME
                );
            """))
            conn.execute(text("""
                CREATE TABLE djmdHistory (
                    ID VARCHAR(255) PRIMARY KEY,
                    Name VARCHAR(255),
                    ParentID VARCHAR(255),
                    Attribute INTEGER,
                    DateCreated VARCHAR(255),
                    Seq INTEGER,
                    rb_local_deleted INTEGER DEFAULT 0,
                    created_at DATETIME,
                    updated_at DATETIME
                );
            """))
            conn.execute(text("""
                CREATE TABLE djmdSongHistory (
                    ID VARCHAR(255) PRIMARY KEY,
                    HistoryID VARCHAR(255),
                    ContentID VARCHAR(255),
                    TrackNo INTEGER,
                    rb_local_deleted INTEGER DEFAULT 0,
                    created_at DATETIME,
                    updated_at DATETIME
                );
            """))

            # Populate external
            conn.execute(text("""
                INSERT INTO djmdHistory (ID, Name, DateCreated, Attribute, ParentID)
                VALUES ('h_ext_active', 'HISTORY 2025-07-05', '2025-07-05 15:36:36', 0, '202507');
            """))
            conn.execute(text("""
                INSERT INTO djmdContent (ID, Title, Length, created_at, updated_at)
                VALUES ('ext_c_missing', 'Rare Gem', 310, '2025-01-01', '2025-01-01');
            """))

        plan = [
            {
                "external_history_id": "h_ext_active",
                "current_history_id": "h_active",
                "action": "merge",
                "tracks_to_insert": [
                    TrackDiffItem(
                        track_no=2,
                        played_at="2025-07-05 15:46:00",
                        played_time="15:46:00",
                        title="Move",
                        artist="Adam Port",
                        album="",
                        duration_sec=320,
                        duration_str="5:20",
                        content_id="c2",
                        matched_content_id="c2",
                        file_path="",
                        file_name="Move.aiff",
                        status="new",
                        included=True,
                    ),
                    TrackDiffItem(
                        track_no=3,
                        played_at="2025-07-05 15:52:00",
                        played_time="15:52:00",
                        title="Rare Gem",
                        artist="Rare Artist",
                        album="",
                        duration_sec=310,
                        duration_str="5:10",
                        content_id="ext_c_missing",
                        matched_content_id=None,
                        file_path="/Old/Rare.mp3",
                        file_name="Rare.mp3",
                        status="missing_file",
                        included=True,
                    ),
                ],
            }
        ]

        stats = execute_history_merge_plan(
            current_engine=self.cur_engine,
            external_engine=ext_engine,
            plan=plan,
            db_path=":memory:",
            create_placeholders=True,
            backup=False,
        )

        self.assertEqual(stats["sessions_merged"], 1)
        self.assertEqual(stats["tracks_added"], 2)
        self.assertEqual(stats["placeholders_created"], 1)

        # Verify that current DB now has 3 tracks in h_active
        with self.cur_engine.connect() as conn:
            cnt = conn.exec_driver_sql(
                "SELECT COUNT(*) FROM djmdSongHistory WHERE HistoryID = 'h_active';"
            ).scalar()
            self.assertEqual(cnt, 3)

            # Verify placeholder was created in djmdContent
            pl = conn.exec_driver_sql(
                "SELECT Title FROM djmdContent WHERE Title = 'Rare Gem';"
            ).fetchone()
            self.assertIsNotNone(pl)


if __name__ == "__main__":
    unittest.main()
