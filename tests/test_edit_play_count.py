"""Tests for rekordbox_collection_manager.edit_play_count."""

import sys
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine

from rekordbox_collection_manager.edit_play_count import (
    batch_update_track_play_counts,
    detect_content_table,
    fetch_distinct_file_types,
    fetch_distinct_keys,
    format_location_display,
    main,
    parse_args,
    prompt_action_mode,
    resolve_db_path,
    resolve_file_type,
    run_interactive_session,
    search_tracks,
    update_track_play_count,
)


class TestEditPlayCount(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        with self.engine.begin() as conn:
            conn.exec_driver_sql("""
                CREATE TABLE djmdArtist (
                    ID VARCHAR(255) PRIMARY KEY,
                    Name VARCHAR(255),
                    SearchStr VARCHAR(255)
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdContent (
                    ID VARCHAR(255) PRIMARY KEY,
                    Title VARCHAR(255),
                    ArtistID VARCHAR(255),
                    SrcArtistName VARCHAR(255),
                    SearchStr VARCHAR(255),
                    DJPlayCount INTEGER,
                    FolderPath VARCHAR(255),
                    FileNameL VARCHAR(255),
                    FileType INTEGER DEFAULT 1,
                    rb_local_deleted INTEGER DEFAULT 0,
                    updated_at VARCHAR(255)
                );
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdArtist (ID, Name, SearchStr)
                VALUES 
                    ('art-1', 'David Guetta', 'david guetta'),
                    ('art-2', 'Daft Punk', 'daft punk');
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdContent (
                    ID, Title, ArtistID, SrcArtistName, SearchStr, DJPlayCount,
                    FolderPath, FileNameL, FileType, rb_local_deleted, updated_at
                )
                VALUES 
                    ('1', 'Titanium', 'art-1', 'David Guetta', 'titanium', 5, '/music/1.mp3', '1.mp3', 1, 0, '2026-01-01'),
                    ('2', 'One More Time', 'art-2', 'Daft Punk', 'one more time', 2, '/music/2.mp3', '2.mp3', 1, 0, '2026-01-01'),
                    ('3', 'Zero Plays Song', 'art-1', 'David Guetta', 'zero', 0, '/music/3.mp3', '3.mp3', 1, 0, '2026-01-01'),
                    ('4', 'Deleted Track Titanium', 'art-1', 'David Guetta', 'deleted titanium', 10, '/music/4.mp3', '4.mp3', 1, 1, '2026-01-01'),
                    ('5', 'Null Count Track', NULL, 'Solo Artist', 'null count', NULL, '/music/5.mp3', '5.mp3', 1, 0, '2026-01-01'),
                    ('6', 'Titanium Remix', 'art-1', 'David Guetta', 'titanium remix', 1, '/music/6.mp3', '6.mp3', 1, 0, '2026-01-01'),
                    ('7', '', NULL, NULL, 'spotify', 0, 'spotify:track:123', 'spotify:track:123', 25, 0, '2026-01-01'),
                    ('8', 'Streaming Song', NULL, 'Apple Artist', 'streaming', 3, 'apple-music:tracks:456', 'apple-music:tracks:456', 26, 0, '2026-01-01');
            """)

    def test_parse_args_defaults(self):
        args = parse_args([])
        self.assertIsNone(args.query)
        self.assertIsNone(args.db_path)
        self.assertFalse(args.increment)
        self.assertFalse(args.decrement)
        self.assertFalse(args.force)

    def test_parse_args_with_options(self):
        args = parse_args([
            "Titanium",
            "--inc",
            "--force",
            "-d",
            "custom_master.db",
        ])
        self.assertEqual(args.query, "Titanium")
        self.assertTrue(args.increment)
        self.assertFalse(args.decrement)
        self.assertTrue(args.force)
        self.assertEqual(args.db_path, "custom_master.db")

        args_dec = parse_args(["--decrement"])
        self.assertTrue(args_dec.decrement)

        args_dec_short = parse_args(["--dec"])
        self.assertTrue(args_dec_short.decrement)

    def test_detect_content_table(self):
        self.assertEqual(detect_content_table(self.engine), "djmdContent")

        generic_engine = create_engine("sqlite:///:memory:")
        with generic_engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE content (ID VARCHAR(255), Title VARCHAR(255), DJPlayCount INTEGER);")
        self.assertEqual(detect_content_table(generic_engine), "content")

    def test_search_tracks(self):
        # Search for 'Titanium'
        results = search_tracks(self.engine, "Titanium")
        # Should match 'Titanium' and 'Titanium Remix', but NOT the deleted 'Deleted Track Titanium'
        self.assertEqual(len(results), 2)
        # Exact title match should come first
        self.assertEqual(results[0]["id"], "1")
        self.assertEqual(results[0]["title"], "Titanium")
        self.assertEqual(results[0]["artist"], "David Guetta")
        self.assertEqual(results[0]["play_count"], 5)

        self.assertEqual(results[1]["id"], "6")
        self.assertEqual(results[1]["title"], "Titanium Remix")

    def test_search_tracks_by_artist(self):
        results = search_tracks(self.engine, "Daft Punk")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "2")
        self.assertEqual(results[0]["title"], "One More Time")
        self.assertEqual(results[0]["artist"], "Daft Punk")

    def test_search_tracks_null_count_defaults_to_zero(self):
        results = search_tracks(self.engine, "Null Count")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["play_count"], 0)
        self.assertEqual(results[0]["artist"], "Solo Artist")

    def test_search_tracks_no_matches(self):
        results = search_tracks(self.engine, "Nonexistent Song Name XYZ")
        self.assertEqual(len(results), 0)

    def test_search_tracks_sorting(self):
        # Sort by play count desc
        res = search_tracks(self.engine, "", sort_by="play_count", sort_order="desc")
        self.assertGreaterEqual(res[0]["play_count"], res[-1]["play_count"])
        self.assertEqual(res[0]["play_count"], 5)
        self.assertEqual(res[0]["title"], "Titanium")

        # Sort by title desc
        res_title = search_tracks(self.engine, "", sort_by="title", sort_order="desc")
        self.assertEqual(res_title[0]["title"], "Zero Plays Song")

    def test_search_tracks_play_count_filtering(self):
        # Played tracks only (> 0)
        played = search_tracks(self.engine, "", play_count_filter="played")
        for p in played:
            self.assertGreater(p["play_count"], 0)
        played_ids = {p["id"] for p in played}
        self.assertIn("1", played_ids)  # Titanium (5)
        self.assertIn("2", played_ids)  # One More Time (2)
        self.assertIn("6", played_ids)  # Titanium Remix (1)
        self.assertNotIn("3", played_ids)  # Zero Plays Song (0)
        self.assertNotIn("5", played_ids)  # Null Count Track (None -> 0)

        # Unplayed tracks only (0 or NULL)
        unplayed = search_tracks(self.engine, "", play_count_filter="unplayed")
        for u in unplayed:
            self.assertEqual(u["play_count"], 0)
        unplayed_ids = {u["id"] for u in unplayed}
        self.assertIn("3", unplayed_ids)
        self.assertIn("5", unplayed_ids)
        self.assertNotIn("1", unplayed_ids)

    def test_search_tracks_purchased_filter(self):
        # Insert a purchased track into the test database
        with self.engine.begin() as conn:
            conn.exec_driver_sql("""
                INSERT INTO djmdContent (
                    ID, Title, ArtistID, SrcArtistName, SearchStr, DJPlayCount,
                    FolderPath, FileNameL, FileType, rb_local_deleted, updated_at
                ) VALUES (
                    '10', 'Purchased Hit', 'art-1', 'David Guetta', 'purchased hit', 3,
                    '/Music/Purchased/bought.aiff', 'bought.aiff', 12, 0, '2026-01-01'
                );
            """)

        # 1. Filter: "purchased" only
        purchased = search_tracks(self.engine, "", purchased_filter="purchased")
        self.assertEqual(len(purchased), 1)
        self.assertEqual(purchased[0]["id"], "10")
        self.assertTrue(purchased[0]["is_purchased"])

        # 2. Filter: "downloaded" / "unpurchased" only
        downloaded = search_tracks(self.engine, "", purchased_filter="downloaded")
        self.assertTrue(len(downloaded) > 0)
        self.assertNotIn("10", {t["id"] for t in downloaded})
        for t in downloaded:
            self.assertFalse(t["is_purchased"])

        # 3. Filter: "all"
        all_tracks = search_tracks(self.engine, "", purchased_filter="all")
        all_ids = {t["id"] for t in all_tracks}
        self.assertIn("10", all_ids)
        self.assertIn("1", all_ids)

        # 3b. Filter: "purchasable" (downloaded + play count >= 3)
        purchasable = search_tracks(self.engine, "", purchased_filter="purchasable")
        self.assertTrue(len(purchasable) > 0)
        purchasable_ids = {t["id"] for t in purchasable}
        # Track 1: downloaded, play_count 5 -> purchasable
        self.assertIn("1", purchasable_ids)
        # Track 10: purchased folder, play_count 3 -> excluded (already purchased)
        self.assertNotIn("10", purchasable_ids)
        # Track 2: downloaded, play_count 2 -> excluded (< 3 plays)
        self.assertNotIn("2", purchasable_ids)
        # Track 3: downloaded, play_count 0 -> excluded
        self.assertNotIn("3", purchasable_ids)
        for t in purchasable:
            self.assertFalse(t["is_purchased"])
            self.assertGreaterEqual(t["play_count"], 3)

        # 4. Sorting by purchased descending (purchased track first)
        sorted_res = search_tracks(self.engine, "", sort_by="purchased", sort_order="desc")
        self.assertEqual(sorted_res[0]["id"], "10")

        # 5. File type filter: AIFF vs MP3
        aiff_tracks = search_tracks(self.engine, "", file_type_filter="AIFF")
        self.assertEqual(len(aiff_tracks), 1)
        self.assertEqual(aiff_tracks[0]["id"], "10")

        mp3_tracks = search_tracks(self.engine, "", file_type_filter="MP3")
        self.assertNotIn("10", {t["id"] for t in mp3_tracks})

        # Multi-format filter: ["AIFF", "MP3"]
        multi_tracks = search_tracks(self.engine, "", file_type_filter=["AIFF", "MP3"])
        multi_ids = {t["id"] for t in multi_tracks}
        self.assertIn("10", multi_ids)
        self.assertIn("1", multi_ids)

    def test_fetch_distinct_keys(self):
        # Without djmdKey table
        keys_empty = fetch_distinct_keys(self.engine)
        self.assertEqual(keys_empty, [])

        # Create djmdKey and populate
        with self.engine.begin() as conn:
            conn.exec_driver_sql("""
                CREATE TABLE djmdKey (
                    ID VARCHAR(255) PRIMARY KEY,
                    ScaleName VARCHAR(255)
                );
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdKey (ID, ScaleName)
                VALUES ('k1', '8A'), ('k2', '11B'), ('k3', '8A'), ('k4', '');
            """)

        keys = fetch_distinct_keys(self.engine)
        self.assertEqual(keys, ["11B", "8A"])

        # When KeyID is present in djmdContent
        engine_with_content = create_engine("sqlite:///:memory:")
        with engine_with_content.begin() as conn:
            conn.exec_driver_sql("""
                CREATE TABLE djmdKey (
                    ID VARCHAR(255) PRIMARY KEY,
                    ScaleName VARCHAR(255)
                );
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdKey (ID, ScaleName)
                VALUES ('k1', '8A'), ('k2', '11B'), ('k3', '4A');
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdContent (
                    ID VARCHAR(255) PRIMARY KEY,
                    Title VARCHAR(255),
                    KeyID VARCHAR(255),
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdContent (ID, Title, KeyID, rb_local_deleted)
                VALUES 
                    ('1', 'Track 1', 'k1', 0),
                    ('2', 'Track 2', 'k2', 1),
                    ('3', 'Track 3', NULL, 0);
            """)

        # Only k1 (8A) should be returned (k2 is deleted, k3 is NULL, 4A not in collection)
        filtered_keys = fetch_distinct_keys(engine_with_content)
        self.assertEqual(filtered_keys, ["8A"])

        # Test search_tracks with single key
        results_single = search_tracks(engine_with_content, "", key_filter="8A")
        self.assertEqual(len(results_single), 1)
        self.assertEqual(results_single[0]["id"], "1")

        # Test search_tracks with multi-key
        results_multi = search_tracks(engine_with_content, "", key_filter=["8A", "11B"])
        self.assertEqual(len(results_multi), 1)  # Track 2 is deleted

    def test_fetch_distinct_file_types(self):
        # 1. Base test db has MP3 tracks, 1 deleted MP3 track, and streaming tracks
        fmts = fetch_distinct_file_types(self.engine)
        self.assertEqual(fmts, ["MP3"])

        # 2. Insert an AIFF track
        with self.engine.begin() as conn:
            conn.exec_driver_sql("""
                INSERT INTO djmdContent (
                    ID, Title, SearchStr, DJPlayCount,
                    FolderPath, FileNameL, FileType, rb_local_deleted, updated_at
                ) VALUES (
                    '99', 'AIFF Song', 'aiff song', 0,
                    '/music/song.aiff', 'song.aiff', 12, 0, '2026-01-01'
                );
            """)
        fmts = fetch_distinct_file_types(self.engine)
        self.assertEqual(fmts, ["AIFF", "MP3"])

        # 3. Insert a track where FileType is NULL but FileNameL has .flac
        with self.engine.begin() as conn:
            conn.exec_driver_sql("""
                INSERT INTO djmdContent (
                    ID, Title, SearchStr, DJPlayCount,
                    FolderPath, FileNameL, FileType, rb_local_deleted, updated_at
                ) VALUES (
                    '100', 'Lossless FLAC', 'lossless flac', 0,
                    '/music/track.flac', 'track.flac', NULL, 0, '2026-01-01'
                );
            """)
        fmts = fetch_distinct_file_types(self.engine)
        self.assertEqual(fmts, ["AIFF", "FLAC", "MP3"])

        # 4. Empty engine or missing table returns []
        empty_engine = create_engine("sqlite:///:memory:")
        self.assertEqual(fetch_distinct_file_types(empty_engine), [])

    def test_search_tracks_excludes_streaming_by_default(self):
        # Default exclude_streaming=True
        results = search_tracks(self.engine, "")
        result_ids = {r["id"] for r in results}
        # Local tracks should be present
        self.assertIn("1", result_ids)
        self.assertIn("2", result_ids)
        self.assertIn("3", result_ids)
        self.assertIn("5", result_ids)
        self.assertIn("6", result_ids)
        # Streaming tracks (Spotify ID 7, Apple Music ID 8) must NOT be present
        self.assertNotIn("7", result_ids)
        self.assertNotIn("8", result_ids)

    def test_search_tracks_includes_streaming_when_flag_false(self):
        # Explicit exclude_streaming=False
        results = search_tracks(self.engine, "", exclude_streaming=False)
        result_ids = {r["id"] for r in results}
        self.assertIn("7", result_ids)
        self.assertIn("8", result_ids)

    def test_search_tracks_by_content_id_bypasses_streaming_filter(self):
        # Direct lookup by content_id still retrieves track details
        results = search_tracks(self.engine, "", content_id="7")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "7")
        self.assertEqual(results[0]["file_type"], "Spotify")

    def test_update_track_play_count_increment(self):
        old_count, new_count = update_track_play_count(self.engine, "1", delta=1)
        self.assertEqual(old_count, 5)
        self.assertEqual(new_count, 6)

        with self.engine.connect() as conn:
            row = conn.exec_driver_sql("SELECT DJPlayCount, updated_at FROM djmdContent WHERE ID = '1'").fetchone()
            self.assertEqual(row[0], 6)
            self.assertIn("+00:00", row[1])

    def test_update_track_play_count_decrement(self):
        old_count, new_count = update_track_play_count(self.engine, "1", delta=-1)
        self.assertEqual(old_count, 5)
        self.assertEqual(new_count, 4)

        with self.engine.connect() as conn:
            row = conn.exec_driver_sql("SELECT DJPlayCount FROM djmdContent WHERE ID = '1'").fetchone()
            self.assertEqual(row[0], 4)

    def test_update_track_play_count_floor_zero(self):
        old_count, new_count = update_track_play_count(self.engine, "3", delta=-1)
        self.assertEqual(old_count, 0)
        self.assertEqual(new_count, 0)

        with self.engine.connect() as conn:
            row = conn.exec_driver_sql("SELECT DJPlayCount FROM djmdContent WHERE ID = '3'").fetchone()
            self.assertEqual(row[0], 0)

    def test_update_track_play_count_from_null(self):
        old_count, new_count = update_track_play_count(self.engine, "5", delta=1)
        self.assertEqual(old_count, 0)
        self.assertEqual(new_count, 1)

    def test_update_track_play_count_not_found(self):
        with self.assertRaises(ValueError):
            update_track_play_count(self.engine, "99999", delta=1)

    def test_prompt_action_mode(self):
        self.assertEqual(prompt_action_mode(cli_inc=True, cli_dec=False), "increment")
        self.assertEqual(prompt_action_mode(cli_inc=False, cli_dec=True), "decrement")

        with patch("questionary.select") as mock_select:
            mock_select.return_value.ask.return_value = "increment"
            self.assertEqual(prompt_action_mode(cli_inc=False, cli_dec=False), "increment")

    def test_run_interactive_session_increment_flow(self):
        with patch("questionary.select") as mock_select, \
             patch("questionary.confirm") as mock_confirm:
            # 1. Select track '1' (Titanium)
            mock_select.return_value.ask.return_value = "1"
            # 2. Confirm edit another? -> False
            mock_confirm.return_value.ask.return_value = False

            run_interactive_session(
                self.engine,
                initial_query="Titanium",
                action_mode="increment",
            )

        with self.engine.connect() as conn:
            row = conn.exec_driver_sql("SELECT DJPlayCount FROM djmdContent WHERE ID = '1'").fetchone()
            self.assertEqual(row[0], 6)

    def test_run_interactive_session_ask_action_decrement(self):
        with patch("questionary.select") as mock_select, \
             patch("questionary.confirm") as mock_confirm:
            # 1. Select track '1'
            # 2. Select action delta -1
            mock_select.return_value.ask.side_effect = ["1", -1]
            mock_confirm.return_value.ask.return_value = False

            run_interactive_session(
                self.engine,
                initial_query="Titanium",
                action_mode="ask",
            )

        with self.engine.connect() as conn:
            row = conn.exec_driver_sql("SELECT DJPlayCount FROM djmdContent WHERE ID = '1'").fetchone()
            self.assertEqual(row[0], 4)

    def test_run_interactive_session_cancel_exits(self):
        with patch("questionary.select") as mock_select:
            mock_select.return_value.ask.return_value = "__CANCEL__"

            run_interactive_session(
                self.engine,
                initial_query="Titanium",
                action_mode="increment",
            )

        # Count shouldn't change
        with self.engine.connect() as conn:
            row = conn.exec_driver_sql("SELECT DJPlayCount FROM djmdContent WHERE ID = '1'").fetchone()
            self.assertEqual(row[0], 5)

    def test_batch_update_track_play_counts(self):
        updates = [
            {"id": "1", "play_count": 12},
            {"id": "2", "play_count": 0},
        ]
        count = batch_update_track_play_counts(self.engine, updates)
        self.assertEqual(count, 2)

        with self.engine.connect() as conn:
            c1 = conn.exec_driver_sql("SELECT DJPlayCount FROM djmdContent WHERE ID = '1'").fetchone()[0]
            c2 = conn.exec_driver_sql("SELECT DJPlayCount FROM djmdContent WHERE ID = '2'").fetchone()[0]
            self.assertEqual(c1, 12)
            self.assertEqual(c2, 0)

    def test_resolve_file_type(self):
        self.assertEqual(resolve_file_type(1), "MP3")
        self.assertEqual(resolve_file_type(4), "M4A")
        self.assertEqual(resolve_file_type(5), "FLAC")
        self.assertEqual(resolve_file_type(11), "WAV")
        self.assertEqual(resolve_file_type(12), "AIFF")
        self.assertEqual(resolve_file_type(25), "Spotify")
        self.assertEqual(resolve_file_type(26), "Apple Music")
        self.assertEqual(resolve_file_type(None, filename="track.flac"), "FLAC")
        self.assertEqual(resolve_file_type(None, filename="track.aiff"), "AIFF")
        self.assertEqual(resolve_file_type(None, folder_path="/Music/track.mp3"), "MP3")
        self.assertEqual(resolve_file_type(None, folder_path="apple-music:tracks:123"), "Apple Music")
        self.assertEqual(resolve_file_type(None, folder_path="spotify:track:456"), "Spotify")
        self.assertEqual(resolve_file_type(None, filename=""), "—")

    def test_format_location_display(self):
        self.assertEqual(
            format_location_display("song.mp3", "/Music/Afro House/song.mp3"),
            "Afro House/song.mp3",
        )
        self.assertEqual(
            format_location_display(None, "/Music/Afro House/song.mp3"),
            "Afro House/song.mp3",
        )
        self.assertEqual(
            format_location_display("song.mp3", None),
            "song.mp3",
        )
        self.assertEqual(
            format_location_display("", "spotify:track:123"),
            "spotify:track:123",
        )

    def test_search_tracks_tags_and_playlists(self):
        with self.engine.begin() as conn:
            conn.exec_driver_sql("""
                CREATE TABLE djmdMyTag (
                    ID VARCHAR(255) PRIMARY KEY,
                    Name VARCHAR(255),
                    Seq INTEGER,
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdSongMyTag (
                    ID VARCHAR(255) PRIMARY KEY,
                    MyTagID VARCHAR(255),
                    ContentID VARCHAR(255),
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdPlaylist (
                    ID VARCHAR(255) PRIMARY KEY,
                    Name VARCHAR(255),
                    Attribute INTEGER,
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdSongPlaylist (
                    ID VARCHAR(255) PRIMARY KEY,
                    PlaylistID VARCHAR(255),
                    ContentID VARCHAR(255),
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdMyTag (ID, Name, Seq) VALUES ('tag-1', 'Peak Time', 1), ('tag-2', 'Vocal', 2);
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdSongMyTag (ID, MyTagID, ContentID) VALUES ('smt-1', 'tag-1', '1'), ('smt-2', 'tag-2', '1');
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdPlaylist (ID, Name, Attribute) VALUES ('pl-1', 'Festival 2026', 0), ('pl-smart', 'Smart Playlist', 4);
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdSongPlaylist (ID, PlaylistID, ContentID) VALUES ('sp-1', 'pl-1', '1');
            """)

        results = search_tracks(self.engine, "Titanium")
        self.assertEqual(len(results), 2)
        titanium = results[0]
        self.assertEqual(titanium["tags"], ["Peak Time", "Vocal"])
        self.assertEqual(titanium["playlists"], ["Festival 2026"])
        self.assertEqual(titanium["location_display"], "music/1.mp3")

        # Test searching by tag name
        tag_match = search_tracks(self.engine, "Peak Time")
        self.assertEqual(len(tag_match), 1)
        self.assertEqual(tag_match[0]["id"], "1")

        # Test searching by playlist name
        pl_match = search_tracks(self.engine, "Festival")
        self.assertEqual(len(pl_match), 1)
        self.assertEqual(pl_match[0]["id"], "1")

        # --- Multi-tag & Multi-playlist filtering tests ---
        with self.engine.begin() as conn:
            conn.exec_driver_sql("INSERT INTO djmdMyTag (ID, Name, Seq) VALUES ('tag-3', 'Chill', 3);")
            conn.exec_driver_sql("INSERT INTO djmdSongMyTag (ID, MyTagID, ContentID) VALUES ('smt-3', 'tag-3', '2');")
            conn.exec_driver_sql("INSERT INTO djmdPlaylist (ID, Name, Attribute) VALUES ('pl-2', 'Club Night', 0);")
            conn.exec_driver_sql("INSERT INTO djmdSongPlaylist (ID, PlaylistID, ContentID) VALUES ('sp-2', 'pl-2', '2');")

        # Multi-tag ANY: Track 1 has Peak Time & Vocal, Track 2 has Chill
        any_tags = search_tracks(self.engine, "", tag_filter=["Peak Time", "Chill"], tag_match_mode="any")
        any_ids = {t["id"] for t in any_tags}
        self.assertIn("1", any_ids)
        self.assertIn("2", any_ids)

        # Multi-tag ALL: Track 1 has both Peak Time AND Vocal
        all_tags_1 = search_tracks(self.engine, "", tag_filter=["Peak Time", "Vocal"], tag_match_mode="all")
        self.assertEqual(len(all_tags_1), 1)
        self.assertEqual(all_tags_1[0]["id"], "1")

        # Multi-tag ALL: No track has both Peak Time AND Chill
        all_tags_none = search_tracks(self.engine, "", tag_filter=["Peak Time", "Chill"], tag_match_mode="all")
        self.assertEqual(len(all_tags_none), 0)

        # Multi-playlist ANY: Track 1 in Festival 2026, Track 2 in Club Night
        any_pls = search_tracks(self.engine, "", playlist_filter=["Festival 2026", "Club Night"], playlist_match_mode="any")
        any_pl_ids = {t["id"] for t in any_pls}
        self.assertIn("1", any_pl_ids)
        self.assertIn("2", any_pl_ids)

        # Multi-playlist ALL: No track in both Festival 2026 AND Club Night
        all_pls_none = search_tracks(self.engine, "", playlist_filter=["Festival 2026", "Club Night"], playlist_match_mode="all")
        self.assertEqual(len(all_pls_none), 0)

    def test_search_tracks_with_store_filter(self):
        with self.engine.begin() as conn:
            conn.exec_driver_sql("ALTER TABLE djmdContent ADD COLUMN Commnt TEXT;")
            conn.exec_driver_sql("UPDATE djmdContent SET Commnt = 'Purchased at Beatport.com' WHERE ID = '1';")
            conn.exec_driver_sql("UPDATE djmdContent SET Commnt = 'Purchased at qobuz.com' WHERE ID = '2';")
            conn.exec_driver_sql("UPDATE djmdContent SET Commnt = 'Visit https://artist.bandcamp.com' WHERE ID = '3';")
            conn.exec_driver_sql("UPDATE djmdContent SET Commnt = 'Purchased at Traxsource.com' WHERE ID = '5';")
            conn.exec_driver_sql("UPDATE djmdContent SET Commnt = 'downloaded from lucida.to: https://play.qobuz.com' WHERE ID = '6';")

        # Filter by Beatport
        bp_tracks = search_tracks(self.engine, "", store_filter=["Beatport"])
        self.assertEqual(len(bp_tracks), 1)
        self.assertEqual(bp_tracks[0]["id"], "1")
        self.assertEqual(bp_tracks[0]["store"], "Beatport")

        # Filter by Qobuz (Track 6 is lucida.to rip and must NOT match!)
        qobuz_tracks = search_tracks(self.engine, "", store_filter=["Qobuz"])
        self.assertEqual(len(qobuz_tracks), 1)
        self.assertEqual(qobuz_tracks[0]["id"], "2")
        self.assertEqual(qobuz_tracks[0]["store"], "Qobuz")

        # Multi-store filter: Bandcamp and Traxsource
        multi_tracks = search_tracks(self.engine, "", store_filter=["Bandcamp", "Traxsource"])
        multi_ids = {t["id"] for t in multi_tracks}
        self.assertEqual(multi_ids, {"3", "5"})

        # Sort by store / purchased_at
        sorted_tracks = search_tracks(
            self.engine, "", store_filter=["Beatport", "Qobuz", "Bandcamp", "Traxsource"],
            sort_by="purchased_at", sort_order="ASC"
        )
        # Bandcamp, Beatport, Qobuz, Traxsource alphabetically
        stores = [t["store"] for t in sorted_tracks]
        self.assertEqual(stores, ["Bandcamp", "Beatport", "Qobuz", "Traxsource"])

    def test_main_running_check(self):
        with patch("rekordbox_collection_manager.edit_play_count.ensure_rekordbox_not_running") as mock_check, \
             patch("rekordbox_collection_manager.edit_play_count.resolve_db_path", return_value="dummy.db"), \
             patch("rekordbox_collection_manager.edit_play_count.open_database") as mock_open:
            mock_open.side_effect = SystemExit(0)
            with self.assertRaises(SystemExit):
                main(["--force"])
            mock_check.assert_called_once_with(force=True)


if __name__ == "__main__":
    unittest.main()
