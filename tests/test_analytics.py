"""Unit tests for Rekordbox Analytics and KPI Engine."""

import os
import tempfile
import unittest
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from rekordbox_collection_manager.analytics import (
    compute_analytics,
    create_purge_playlist_in_rekordbox,
    export_tracks_to_m3u8,
    export_tracks_to_rekordbox_xml,
    normalize_to_camelot,
)


class TestAnalyticsEngine(unittest.TestCase):
    """Test suite for Rekordbox Analytics Engine calculations and exports."""

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        with self.engine.begin() as conn:
            # Create Rekordbox schema
            conn.exec_driver_sql("""
                CREATE TABLE djmdKey (
                    ID VARCHAR(255) PRIMARY KEY,
                    ScaleName VARCHAR(255)
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdArtist (
                    ID VARCHAR(255) PRIMARY KEY,
                    Name VARCHAR(255)
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdAlbum (
                    ID VARCHAR(255) PRIMARY KEY,
                    Name VARCHAR(255)
                );
            """)
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

            # Seed Keys
            conn.exec_driver_sql("INSERT INTO djmdKey (ID, ScaleName) VALUES ('k1', '8A');")
            conn.exec_driver_sql("INSERT INTO djmdKey (ID, ScaleName) VALUES ('k2', 'Am');")
            conn.exec_driver_sql("INSERT INTO djmdKey (ID, ScaleName) VALUES ('k3', '8B');")
            conn.exec_driver_sql("INSERT INTO djmdKey (ID, ScaleName) VALUES ('k4', 'C');")

            # Seed Tracks
            # T1: Peak Banger (played in all 5 sets, added before set 1)
            conn.exec_driver_sql("""
                INSERT INTO djmdContent (ID, Title, KeyID, BPM, Length, TotalTime, DJPlayCount, created_at, FolderPath)
                VALUES ('t1', 'Peak Banger Track', 'k1', 12800, 300, 300, 25, '2025-01-01 10:00:00', '/Music/t1.mp3');
            """)
            # T2: Core Rotation (played in 2 of 5 sets, added before set 1)
            conn.exec_driver_sql("""
                INSERT INTO djmdContent (ID, Title, KeyID, BPM, Length, TotalTime, DJPlayCount, created_at, FolderPath)
                VALUES ('t2', 'Core Rotation Track', 'k2', 12600, 320, 320, 10, '2025-01-01 10:00:00', '/Music/t2.mp3');
            """)
            # T3: Dead Weight (added before set 1, 0 plays across 5 sets)
            conn.exec_driver_sql("""
                INSERT INTO djmdContent (ID, Title, KeyID, BPM, Length, TotalTime, DJPlayCount, created_at, FolderPath)
                VALUES ('t3', 'Zavorra Track', 'k3', 12400, 280, 280, 0, '2025-01-01 10:00:00', '/Music/t3.mp3');
            """)
            # T4: Untested / In Incubazione (added recently before set 5, 0 plays, only exposed to 1 set)
            conn.exec_driver_sql("""
                INSERT INTO djmdContent (ID, Title, KeyID, BPM, Length, TotalTime, DJPlayCount, created_at, FolderPath)
                VALUES ('t4', 'Brand New Track', 'k4', 13000, 240, 240, 0, '2025-05-15 10:00:00', '/Music/t4.mp3');
            """)
            # T5: Burnout track (high play count in past, but not played in any recent sets)
            conn.exec_driver_sql("""
                INSERT INTO djmdContent (ID, Title, KeyID, BPM, Length, TotalTime, DJPlayCount, created_at, FolderPath)
                VALUES ('t5', 'Old Classic Saturated', 'k1', 12800, 360, 360, 40, '2024-01-01 10:00:00', '/Music/t5.mp3');
            """)

            # Seed 5 valid sessions (gigs) each with >= 5 tracks and > 20 min (duration = 5 * 300 = 1500 sec)
            dates = [
                "2025-01-10 22:00:00",
                "2025-02-10 22:00:00",
                "2025-03-10 22:00:00",
                "2025-04-10 22:00:00",
                "2025-05-20 22:00:00",
            ]
            for i, d in enumerate(dates, 1):
                hid = f"h{i}"
                conn.exec_driver_sql(f"""
                    INSERT INTO djmdHistory (ID, Name, DateCreated)
                    VALUES ('{hid}', 'Live Gig {i}', '{d}');
                """)
                # In each set: t1 is played.
                # In set 1 and 2: t2 is played.
                # Combo: in sets 1, 2, 3: t1 -> t2 is played in sequence
                conn.exec_driver_sql(f"INSERT INTO djmdSongHistory (ID, HistoryID, ContentID, HistoryOrder) VALUES ('sh_{i}_1', '{hid}', 't1', 1);")
                conn.exec_driver_sql(f"INSERT INTO djmdSongHistory (ID, HistoryID, ContentID, HistoryOrder) VALUES ('sh_{i}_2', '{hid}', 't2', 2);")
                # Add dummy songs so set has 5 tracks (> 20 min)
                conn.exec_driver_sql(f"INSERT INTO djmdSongHistory (ID, HistoryID, ContentID, HistoryOrder) VALUES ('sh_{i}_3', '{hid}', 't1', 3);")
                conn.exec_driver_sql(f"INSERT INTO djmdSongHistory (ID, HistoryID, ContentID, HistoryOrder) VALUES ('sh_{i}_4', '{hid}', 't1', 4);")
                conn.exec_driver_sql(f"INSERT INTO djmdSongHistory (ID, HistoryID, ContentID, HistoryOrder) VALUES ('sh_{i}_5', '{hid}', 't1', 5);")

            # Seed 1 invalid session (< 5 tracks, soundcheck)
            conn.exec_driver_sql("""
                INSERT INTO djmdHistory (ID, Name, DateCreated)
                VALUES ('h_invalid', 'Soundcheck', '2025-01-05 18:00:00');
            """)
            conn.exec_driver_sql("INSERT INTO djmdSongHistory (ID, HistoryID, ContentID, HistoryOrder) VALUES ('sh_inv_1', 'h_invalid', 't1', 1);")

    def test_camelot_normalization(self):
        """Verify camelot normalization for classical keys and standard codes."""
        code, ring, num, trad = normalize_to_camelot("8A")
        self.assertEqual(code, "8A")
        self.assertEqual(ring, "A")
        self.assertEqual(num, 8)
        self.assertEqual(trad, "Am")

        code, ring, num, trad = normalize_to_camelot("Am")
        self.assertEqual(code, "8A")
        self.assertEqual(ring, "A")

        code, ring, num, trad = normalize_to_camelot("C major")
        self.assertEqual(code, "8B")
        self.assertEqual(ring, "B")

        code, ring, num, trad = normalize_to_camelot("F#m")
        self.assertEqual(code, "11A")
        self.assertEqual(num, 11)

    def test_compute_analytics_gig_filter_and_kpis(self):
        """Verify gig filter excludes soundchecks and computes correct KPIs."""
        snapshot = compute_analytics(self.engine, min_session_duration_sec=1200, min_session_tracks=5)

        # 5 valid sessions, soundcheck excluded
        self.assertEqual(snapshot.total_valid_sessions, 5)

        # T1 (Peak Banger): played in all 5 sets, exposure = 5, pick rate = 100%
        t1 = snapshot.tracks_by_id["t1"]
        self.assertEqual(t1.exposure_gigs, 5)
        self.assertEqual(t1.set_presence_gigs, 5)
        self.assertEqual(t1.pick_rate, 100.0)
        self.assertEqual(t1.cluster, "Peak Bangers")

        # T3 (Dead Weight): exposed to 5 sets, 0 plays
        t3 = snapshot.tracks_by_id["t3"]
        self.assertEqual(t3.exposure_gigs, 5)
        self.assertEqual(t3.set_presence_gigs, 0)
        self.assertEqual(t3.pick_rate, 0.0)
        self.assertEqual(t3.cluster, "Dead Weight (Zavorra)")

        # T4 (Untested): added just before set 5, exposure = 1 <= 2, 0 plays
        t4 = snapshot.tracks_by_id["t4"]
        self.assertEqual(t4.exposure_gigs, 1)
        self.assertEqual(t4.set_presence_gigs, 0)
        self.assertEqual(t4.cluster, "In Incubazione / Untested")

    def test_point_in_time_turnover_series(self):
        """Verify turnover and cluster composition is computed point-in-time without future leakage."""
        snapshot = compute_analytics(self.engine, min_session_duration_sec=1200, min_session_tracks=5)
        self.assertEqual(len(snapshot.turnover_series), 5)

        s1 = snapshot.turnover_series[0]
        # In set 1: 100% of tracks are first-time played (new entries / untested)
        self.assertEqual(s1.new_entries_count, s1.total_tracks)
        self.assertEqual(s1.untested_count, s1.total_tracks)
        self.assertEqual(s1.burnout_count, 0)
        self.assertEqual(s1.archetype, "Genesis / Battesimo")
        self.assertEqual(s1.archetype_icon, "🌱")

        # In set 4: t1 was played in sets 1, 2, 3 (3 out of 3 consecutive sets right before set 4)
        s4 = snapshot.turnover_series[3]
        self.assertIn("t1", s4.burnout_ids)
        self.assertGreaterEqual(s4.burnout_count, 1)
        self.assertTrue(len(s4.archetype) > 0)
        self.assertTrue(len(s4.archetype_icon) > 0)

    def test_transitions_network(self):
        """Verify transition graph identifies repeated track sequences."""
        snapshot = compute_analytics(self.engine, transition_min_frequency=2)
        # t1 -> t2 was played in all 5 sets
        edges = [e for e in snapshot.transition_edges if e.source_id == "t1" and e.target_id == "t2"]
        self.assertTrue(len(edges) >= 1)
        self.assertEqual(edges[0].count, 5)

    def test_m3u8_and_xml_export(self):
        """Verify export functions generate valid files."""
        tracks = [
            {"id": "t1", "title": "Song 1", "artist": "Artist 1", "folder_path": "/path/to/song1.mp3", "duration_sec": 300},
            {"id": "t2", "title": "Song 2", "artist": "Artist 2", "folder_path": "/path/to/song2.mp3", "duration_sec": 240},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            m3u_path = os.path.join(tmpdir, "test.m3u8")
            count_m3u = export_tracks_to_m3u8(m3u_path, tracks)
            self.assertEqual(count_m3u, 2)
            self.assertTrue(os.path.exists(m3u_path))

            xml_path = os.path.join(tmpdir, "test.xml")
            count_xml = export_tracks_to_rekordbox_xml(xml_path, "Purge Crate", tracks)
            self.assertEqual(count_xml, 2)
            self.assertTrue(os.path.exists(xml_path))

    def test_create_purge_playlist(self):
        """Verify creating purge playlist directly in Rekordbox sqlite tables."""
        res = create_purge_playlist_in_rekordbox(self.engine, "Crate di Spurgo", ["t3"])
        self.assertIsNotNone(res.get("id"))

        # Verify playlist was created
        with self.engine.connect() as conn:
            p_rows = conn.exec_driver_sql("SELECT Name FROM djmdPlaylist WHERE Name = 'Crate di Spurgo';").fetchall()
            self.assertEqual(len(p_rows), 1)

            sp_rows = conn.exec_driver_sql("SELECT ContentID FROM djmdSongPlaylist WHERE PlaylistID = :pid;", {"pid": res["id"]}).fetchall()
            self.assertEqual(len(sp_rows), 1)
            self.assertEqual(sp_rows[0][0], "t3")


if __name__ == "__main__":
    unittest.main()

