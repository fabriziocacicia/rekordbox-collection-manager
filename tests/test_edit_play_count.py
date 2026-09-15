"""Tests for rekordbox_collection_fixer.edit_play_count."""

import sys
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine

from rekordbox_collection_fixer.edit_play_count import (
    detect_content_table,
    main,
    parse_args,
    prompt_action_mode,
    resolve_db_path,
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
                    FolderPath, FileNameL, rb_local_deleted, updated_at
                )
                VALUES 
                    ('1', 'Titanium', 'art-1', 'David Guetta', 'titanium', 5, '/music/1.mp3', '1.mp3', 0, '2026-01-01'),
                    ('2', 'One More Time', 'art-2', 'Daft Punk', 'one more time', 2, '/music/2.mp3', '2.mp3', 0, '2026-01-01'),
                    ('3', 'Zero Plays Song', 'art-1', 'David Guetta', 'zero', 0, '/music/3.mp3', '3.mp3', 0, '2026-01-01'),
                    ('4', 'Deleted Track Titanium', 'art-1', 'David Guetta', 'deleted titanium', 10, '/music/4.mp3', '4.mp3', 1, '2026-01-01'),
                    ('5', 'Null Count Track', NULL, 'Solo Artist', 'null count', NULL, '/music/5.mp3', '5.mp3', 0, '2026-01-01'),
                    ('6', 'Titanium Remix', 'art-1', 'David Guetta', 'titanium remix', 1, '/music/6.mp3', '6.mp3', 0, '2026-01-01');
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

    def test_main_running_check(self):
        with patch("rekordbox_collection_fixer.edit_play_count.ensure_rekordbox_not_running") as mock_check, \
             patch("rekordbox_collection_fixer.edit_play_count.resolve_db_path", return_value="dummy.db"), \
             patch("rekordbox_collection_fixer.edit_play_count.open_database") as mock_open:
            mock_open.side_effect = SystemExit(0)
            with self.assertRaises(SystemExit):
                main(["--force"])
            mock_check.assert_called_once_with(force=True)


if __name__ == "__main__":
    unittest.main()
