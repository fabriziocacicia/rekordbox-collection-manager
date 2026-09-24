"""Tests for rekordbox_collection_manager.utils."""

import argparse
import os
import re
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine

from rekordbox_collection_manager.utils import (
    MONTH_NAME_TO_INT,
    build_exclude_streaming_clause,
    build_track_record,
    clean_session_name,
    compose_history_name,
    compose_played_with,
    detect_content_table,
    ensure_rekordbox_not_running,
    extract_date_from_name,
    extract_year_month_from_name_or_date,
    format_duration,
    format_rating_stars,
    get_rekordbox_timestamp,
    is_aiff_track,
    is_mp3_track,
    is_rekordbox_running,
    is_streaming_track,
    natural_sort_key,
    normalize_date_input,
    parse_history_name,
    parse_month_number,
    parse_played_with,
    parse_session_paren_number,
    parse_year_month,
    resolve_db_path,
)


class TestUtils(unittest.TestCase):
    # --- Process / Running Checks ---

    def test_is_rekordbox_running_pgrep(self):
        with patch("shutil.which", return_value="/usr/bin/pgrep"), \
             patch("subprocess.run") as mock_sub:
            mock_sub.return_value = MagicMock(returncode=0, stdout="8888 rekordbox\n")
            self.assertTrue(is_rekordbox_running())

    def test_is_rekordbox_running_pgrep_ignores_self_and_tool(self):
        with patch("shutil.which", return_value="/usr/bin/pgrep"), \
             patch("subprocess.run") as mock_sub, \
             patch("psutil.process_iter", return_value=[]), \
             patch("rekordbox_collection_manager.utils.get_rekordbox_pid", return_value=0):
            current_pid = os.getpid()
            mock_sub.return_value = MagicMock(
                returncode=0,
                stdout=f"{current_pid} rekordbox\n9999 rekordbox-collection-manager\n",
            )
            self.assertFalse(is_rekordbox_running())

    def test_is_rekordbox_running_psutil_fallback(self):
        proc_mock = MagicMock()
        proc_mock.info = {"pid": 55555, "name": "rekordbox.exe"}

        with patch("shutil.which", return_value=None), \
             patch("psutil.process_iter", return_value=[proc_mock]):
            self.assertTrue(is_rekordbox_running())

    def test_is_rekordbox_running_not_found(self):
        with patch("shutil.which", return_value="/usr/bin/pgrep"), \
             patch("subprocess.run") as mock_sub, \
             patch("psutil.process_iter", return_value=[]), \
             patch("rekordbox_collection_manager.utils.get_rekordbox_pid", return_value=0):
            mock_sub.return_value = MagicMock(returncode=1, stdout="")
            self.assertFalse(is_rekordbox_running())

    def test_ensure_rekordbox_not_running_exits(self):
        with patch("rekordbox_collection_manager.utils.is_rekordbox_running", return_value=True):
            with self.assertRaises(SystemExit) as cm:
                ensure_rekordbox_not_running(force=False, exit_on_running=True)
            self.assertEqual(cm.exception.code, 1)

    def test_ensure_rekordbox_not_running_force_allowed(self):
        with patch("rekordbox_collection_manager.utils.is_rekordbox_running", return_value=True):
            # When force=True, it should not exit
            running = ensure_rekordbox_not_running(force=True, exit_on_running=True)
            self.assertTrue(running)

    def test_ensure_rekordbox_not_running_when_not_running(self):
        with patch("rekordbox_collection_manager.utils.is_rekordbox_running", return_value=False):
            running = ensure_rekordbox_not_running(force=False, exit_on_running=True)
            self.assertFalse(running)

    # --- Database & Path Resolution ---

    def test_get_rekordbox_timestamp(self):
        ts = get_rekordbox_timestamp()
        # Rekordbox UTC format: YYYY-MM-DD HH:MM:SS.mmm +00:00
        self.assertRegex(ts, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} \+00:00$")

    def test_detect_content_table(self):
        engine_djmd = create_engine("sqlite:///:memory:")
        with engine_djmd.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE djmdContent (ID TEXT);")
        self.assertEqual(detect_content_table(engine_djmd), "djmdContent")

        engine_content = create_engine("sqlite:///:memory:")
        with engine_content.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE content (ID TEXT);")
        self.assertEqual(detect_content_table(engine_content), "content")

    def test_resolve_db_path_with_string_and_namespace(self):
        test_file = os.path.abspath("pyproject.toml")
        # Direct string
        self.assertEqual(resolve_db_path(test_file), test_file)

        # Namespace with db_path
        args_db = argparse.Namespace(db_path=test_file)
        self.assertEqual(resolve_db_path(args_db), test_file)

        # Namespace with db_path_flag
        args_flag = argparse.Namespace(db_path_flag=test_file, db_path=None)
        self.assertEqual(resolve_db_path(args_flag), test_file)

    # --- Date, Month & Text Utilities ---

    def test_month_name_to_int_coverage(self):
        self.assertEqual(MONTH_NAME_TO_INT["january"], 1)
        self.assertEqual(MONTH_NAME_TO_INT["august"], 8)
        self.assertEqual(MONTH_NAME_TO_INT["december"], 12)
        # Italian coverage
        self.assertEqual(MONTH_NAME_TO_INT["gennaio"], 1)
        self.assertEqual(MONTH_NAME_TO_INT["agosto"], 8)
        self.assertEqual(MONTH_NAME_TO_INT["dicembre"], 12)

    def test_parse_month_number(self):
        # 1. Check ID
        self.assertEqual(parse_month_number(None, id_val="202608"), 8)
        self.assertEqual(parse_month_number("Random", id_val="202612"), 12)

        # 2. Check Name if integer
        self.assertEqual(parse_month_number("8"), 8)
        self.assertEqual(parse_month_number("09"), 9)

        # 3. Check Name against English / Italian
        self.assertEqual(parse_month_number("August"), 8)
        self.assertEqual(parse_month_number("settembre"), 9)
        self.assertEqual(parse_month_number("nov"), 11)

        # 4. Check DateCreated
        self.assertEqual(parse_month_number("Folder", date_created="2026-05-10"), 5)

        # 5. Check Name with YYYY-MM
        self.assertEqual(parse_month_number("2026-07"), 7)

        # Fallback
        self.assertEqual(parse_month_number("Unknown"), 99)

    def test_parse_year_month(self):
        self.assertEqual(parse_year_month("2026-08-31 23:00:00"), (2026, 8))
        self.assertEqual(parse_year_month("2026/04/15"), (2026, 4))
        self.assertEqual(parse_year_month("2026.11"), (2026, 11))
        self.assertEqual(parse_year_month(None), (None, None))
        self.assertEqual(parse_year_month("invalid"), (None, None))

    def test_extract_date_from_name(self):
        self.assertEqual(
            extract_date_from_name("HISTORY 2026-07-06"),
            "2026-07-06",
        )
        self.assertEqual(
            extract_date_from_name("Set 2025/12/31 Session 1"),
            "2025-12-31",
        )
        self.assertIsNone(extract_date_from_name("Just a title without date"))
        self.assertIsNone(extract_date_from_name(None))

    def test_extract_year_month_from_name_or_date(self):
        # Prioritizes name
        self.assertEqual(
            extract_year_month_from_name_or_date("HISTORY 2026-08-01", "2026-09-01"),
            (2026, 8),
        )
        # Fallback to date_str
        self.assertEqual(
            extract_year_month_from_name_or_date("Untitled", "2026-10-15 12:00:00"),
            (2026, 10),
        )
        self.assertEqual(
            extract_year_month_from_name_or_date(None, None),
            (None, None),
        )

    def test_normalize_date_input(self):
        self.assertEqual(
            normalize_date_input("2026-08-31", "2026-09-14 16:50:22"),
            "2026-08-31 16:50:22",
        )
        self.assertEqual(
            normalize_date_input("2026-08-31 10:00:00", "2026-09-14 16:50:22"),
            "2026-08-31 10:00:00",
        )
        self.assertEqual(
            normalize_date_input("2026-08-31", "2026-09-14"),
            "2026-08-31",
        )

    def test_natural_sort_key(self):
        items = [
            "HISTORY 2026-08-01 (10)",
            "HISTORY 2026-08-01 (1)",
            "HISTORY 2026-08-01 (2)",
        ]
        sorted_items = sorted(items, key=natural_sort_key)
        self.assertEqual(
            sorted_items,
            [
                "HISTORY 2026-08-01 (1)",
                "HISTORY 2026-08-01 (2)",
                "HISTORY 2026-08-01 (10)",
            ],
        )
        self.assertEqual(natural_sort_key(None), [])

    def test_parse_session_paren_number(self):
        self.assertEqual(parse_session_paren_number("HISTORY 2026-07-08"), -1)
        self.assertEqual(parse_session_paren_number("HISTORY 2026-07-08 @ OZeta"), -1)
        self.assertEqual(parse_session_paren_number("HISTORY 2026-07-08 (0)"), 0)
        self.assertEqual(parse_session_paren_number("HISTORY 2026-07-08 (1)"), 1)
        self.assertEqual(parse_session_paren_number("HISTORY 2026-07-08 (2) @ OZeta"), 2)
        self.assertEqual(parse_session_paren_number("HISTORY 2026-07-08 ( 3 )"), 3)
        self.assertEqual(parse_session_paren_number("Session (Live)"), -1)
        self.assertEqual(parse_session_paren_number(None), -1)
        self.assertEqual(parse_session_paren_number(""), -1)

    def test_clean_session_name(self):
        self.assertEqual(clean_session_name("HISTORY 2026-07-08"), "HISTORY 2026-07-08")
        self.assertEqual(clean_session_name("HISTORY 2026-07-08 (1)"), "HISTORY 2026-07-08")
        self.assertEqual(clean_session_name("HISTORY 2026-07-08 (1) @ OZeta"), "HISTORY 2026-07-08 @ OZeta")
        self.assertEqual(clean_session_name("HISTORY 2026-07-08 @ OZeta"), "HISTORY 2026-07-08 @ OZeta")
        self.assertEqual(clean_session_name("Session (Live)"), "Session (Live)")
        self.assertEqual(clean_session_name(None), "")
        self.assertEqual(clean_session_name(""), "")

    def test_parse_history_name(self):
        # 1. Base names only
        self.assertEqual(parse_history_name("HISTORY 2026-07-24"), ("HISTORY 2026-07-24", "", ""))
        self.assertEqual(parse_history_name("HISTORY 2026-07-24 (1)"), ("HISTORY 2026-07-24 (1)", "", ""))
        self.assertEqual(parse_history_name("Sunset Live"), ("Sunset Live", "", ""))

        # 2. Location only (@)
        self.assertEqual(parse_history_name("HISTORY 2026-07-24 @ Fabric"), ("HISTORY 2026-07-24", "Fabric", ""))
        self.assertEqual(parse_history_name("HISTORY 2026-07-24 (1) @ Fabric"), ("HISTORY 2026-07-24 (1)", "Fabric", ""))
        self.assertEqual(parse_history_name("Club Night @ Space Ibiza"), ("Club Night", "Space Ibiza", ""))

        # 3. Artist only (w)
        self.assertEqual(parse_history_name("HISTORY 2026-07-24 w Carl Cox"), ("HISTORY 2026-07-24", "", "Carl Cox"))
        self.assertEqual(parse_history_name("HISTORY 2026-07-24 (1) w Carl Cox"), ("HISTORY 2026-07-24 (1)", "", "Carl Cox"))
        self.assertEqual(parse_history_name("HISTORY 2026-07-24 w/ Carl Cox"), ("HISTORY 2026-07-24", "", "Carl Cox"))

        # 4. Location AND Artist (@ and w)
        self.assertEqual(
            parse_history_name("HISTORY 2026-07-24 @ Fabric w Carl Cox"),
            ("HISTORY 2026-07-24", "Fabric", "Carl Cox"),
        )
        self.assertEqual(
            parse_history_name("HISTORY 2026-07-24 (1) @ Fabric w Carl Cox"),
            ("HISTORY 2026-07-24 (1)", "Fabric", "Carl Cox"),
        )
        self.assertEqual(
            parse_history_name("HISTORY 2026-07-24 @ Space w/ Carl Cox & Friends"),
            ("HISTORY 2026-07-24", "Space", "Carl Cox & Friends"),
        )
        # Location with word starting with W
        self.assertEqual(
            parse_history_name("HISTORY 2026-07-24 @ Warehouse Project w Bicep"),
            ("HISTORY 2026-07-24", "Warehouse Project", "Bicep"),
        )

        # 5. Empty and edge cases
        self.assertEqual(parse_history_name(""), ("", "", ""))
        self.assertEqual(parse_history_name(None), ("", "", ""))
        self.assertEqual(parse_history_name("HISTORY 2026-07-24 @"), ("HISTORY 2026-07-24", "", ""))
        self.assertEqual(parse_history_name("HISTORY 2026-07-24 @ Club w"), ("HISTORY 2026-07-24", "Club", ""))

    def test_compose_history_name(self):
        # 1. Base only
        self.assertEqual(compose_history_name("HISTORY 2026-07-24"), "HISTORY 2026-07-24")
        self.assertEqual(compose_history_name("HISTORY 2026-07-24 (1)", "", ""), "HISTORY 2026-07-24 (1)")

        # 2. Base + Location
        self.assertEqual(compose_history_name("HISTORY 2026-07-24", location="Fabric"), "HISTORY 2026-07-24 @ Fabric")
        self.assertEqual(compose_history_name("HISTORY 2026-07-24 (1)", location="@ Fabric"), "HISTORY 2026-07-24 (1) @ Fabric")

        # 3. Base + Artist
        self.assertEqual(compose_history_name("HISTORY 2026-07-24", artist="Carl Cox"), "HISTORY 2026-07-24 w Carl Cox")
        self.assertEqual(compose_history_name("HISTORY 2026-07-24", artist="w Carl Cox"), "HISTORY 2026-07-24 w Carl Cox")
        self.assertEqual(compose_history_name("HISTORY 2026-07-24", artist="w/ Carl Cox"), "HISTORY 2026-07-24 w Carl Cox")

        # 4. Base + Location + Artist
        self.assertEqual(
            compose_history_name("HISTORY 2026-07-24", location="Fabric", artist="Carl Cox"),
            "HISTORY 2026-07-24 @ Fabric w Carl Cox",
        )
        self.assertEqual(
            compose_history_name("HISTORY 2026-07-24 (1)", location="@ Amnesia", artist="w Marco Carola"),
            "HISTORY 2026-07-24 (1) @ Amnesia w Marco Carola",
        )

        # 5. Redundant base parsing cleanup
        self.assertEqual(
            compose_history_name("HISTORY 2026-07-24 @ OldClub", location="NewClub"),
            "HISTORY 2026-07-24 @ NewClub",
        )
        self.assertEqual(
            compose_history_name("HISTORY 2026-07-24 w OldDJ", artist="NewDJ"),
            "HISTORY 2026-07-24 w NewDJ",
        )

        # 6. List/Iterable artist
        self.assertEqual(
            compose_history_name("HISTORY 2026-07-24", location="Fabric", artist=["Carl Cox", "Marco Carola"]),
            "HISTORY 2026-07-24 @ Fabric w Carl Cox, Marco Carola",
        )

    def test_parse_played_with(self):
        self.assertEqual(parse_played_with("Carl Cox, Marco Carola"), ["Carl Cox", "Marco Carola"])
        self.assertEqual(parse_played_with("  Carl Cox  ,  Marco Carola  "), ["Carl Cox", "Marco Carola"])
        self.assertEqual(parse_played_with("Carl Cox"), ["Carl Cox"])
        self.assertEqual(parse_played_with(""), [])
        self.assertEqual(parse_played_with(None), [])
        self.assertEqual(parse_played_with(" , , "), [])
        self.assertEqual(parse_played_with("A, B, C, D"), ["A", "B", "C", "D"])

    def test_compose_played_with(self):
        self.assertEqual(compose_played_with(["Carl Cox", "Marco Carola"]), "Carl Cox, Marco Carola")
        self.assertEqual(compose_played_with(["Carl Cox"]), "Carl Cox")
        self.assertEqual(compose_played_with([]), "")
        self.assertEqual(compose_played_with(None), "")
        self.assertEqual(compose_played_with(["  Carl Cox  ", ""]), "Carl Cox")

    # --- Streaming Detection & Filtering ---

    def test_is_streaming_track(self):
        # 1. By FileType
        self.assertTrue(is_streaming_track(raw_file_type=25))
        self.assertTrue(is_streaming_track(raw_file_type=26))
        self.assertTrue(is_streaming_track(raw_file_type="25"))
        self.assertFalse(is_streaming_track(raw_file_type=1))
        self.assertFalse(is_streaming_track(raw_file_type=12))

        # 2. By URI Scheme in filename or folder_path
        self.assertTrue(is_streaming_track(filename="spotify:track:123"))
        self.assertTrue(is_streaming_track(folder_path="spotify:track:123"))
        self.assertTrue(is_streaming_track(filename="apple-music:tracks:456"))
        self.assertTrue(is_streaming_track(folder_path="beatport:track:789"))
        self.assertTrue(is_streaming_track(folder_path="tidal:track:999"))
        self.assertTrue(is_streaming_track(folder_path="soundcloud:track:888"))

        # 3. By ExtInfo StreamingInfo
        self.assertTrue(is_streaming_track(ext_info='{"StreamingInfo": {"AudioQuality": "4"}}'))

        # 4. Standard local files
        self.assertFalse(is_streaming_track(
            raw_file_type=1,
            filename="Track.mp3",
            folder_path="/Users/dj/Music/Track.mp3",
            ext_info="null",
        ))

    def test_build_exclude_streaming_clause(self):
        cols = {"FileType", "FileNameL", "FolderPath", "ExtInfo"}
        clause_c = build_exclude_streaming_clause(cols, table_alias="c")
        self.assertIn("c.FileType NOT IN (25, 26)", clause_c)
        self.assertIn("c.FileNameL NOT LIKE 'spotify:%'", clause_c)
        self.assertIn("c.FolderPath NOT LIKE 'apple-music:%'", clause_c)
        self.assertIn("c.ExtInfo NOT LIKE '%StreamingInfo%'", clause_c)

        # Generic table without alias
        clause_no_alias = build_exclude_streaming_clause(cols, table_alias=None)
        self.assertIn("FileType NOT IN (25, 26)", clause_no_alias)
        self.assertNotIn("c.FileType", clause_no_alias)

    def test_format_duration(self):
        self.assertEqual(format_duration(0), "--:--")
        self.assertEqual(format_duration(None), "--:--")
        self.assertEqual(format_duration(65), "1:05")
        self.assertEqual(format_duration(360), "6:00")
        self.assertEqual(format_duration(214.8), "3:35")

    def test_format_rating_stars(self):
        self.assertEqual(format_rating_stars(0), "☆☆☆☆☆")
        self.assertEqual(format_rating_stars(1), "★☆☆☆☆")
        self.assertEqual(format_rating_stars(3), "★★★☆☆")
        self.assertEqual(format_rating_stars(5), "★★★★★")
        # 0..255 scale
        self.assertEqual(format_rating_stars(255), "★★★★★")
        self.assertEqual(format_rating_stars(128), "★★★☆☆")

    def test_is_mp3_and_aiff_track(self):
        self.assertTrue(is_mp3_track("test.mp3"))
        self.assertTrue(is_mp3_track({"file_type": "MP3"}))
        self.assertTrue(is_mp3_track({"raw_file_type": 1}))
        self.assertTrue(is_mp3_track({"file_name": "song.mp3"}))
        self.assertFalse(is_mp3_track("test.aiff"))

        self.assertTrue(is_aiff_track("test.aiff"))
        self.assertTrue(is_aiff_track("test.aif"))
        self.assertTrue(is_aiff_track({"file_type": "AIFF"}))
        self.assertTrue(is_aiff_track({"raw_file_type": 12}))
        self.assertFalse(is_aiff_track("test.wav"))

    def test_build_track_record(self):
        row = {
            "ID": 101,
            "Title": "Sunrise Groove",
            "ArtistName": "DJ Tester",
            "AlbumName": "Test Album",
            "GenreName": "House",
            "KeyName": "8A",
            "BPM": 12400,
            "Length": 365,
            "DJPlayCount": 5,
            "Rating": 4,
            "ReleaseYear": 2024,
            "BitRate": 320,
            "FileType": 1,
            "TagNames": "Peak Time|||Vocal",
            "PlaylistNames": "Ibiza 2024|||Warm Up",
            "FolderPath": "/Music/Afro House",
            "FileNameL": "Sunrise.mp3",
        }
        record = build_track_record(row, extra={"track_no": 1})
        self.assertEqual(record["id"], "101")
        self.assertEqual(record["title"], "Sunrise Groove")
        self.assertEqual(record["artist"], "DJ Tester")
        self.assertEqual(record["bpm"], 124.0)
        self.assertEqual(record["duration_sec"], 365)
        self.assertEqual(record["duration_str"], "6:05")
        self.assertEqual(record["play_count"], 5)
        self.assertEqual(record["plays"], 5)
        self.assertEqual(record["tags"], ["Peak Time", "Vocal"])
        self.assertEqual(record["playlists"], ["Ibiza 2024", "Warm Up"])
        self.assertEqual(record["track_no"], 1)
        self.assertEqual(record["file_type"], "MP3")
        self.assertEqual(record["year"], 2024)
        self.assertEqual(record["release_year"], 2024)


if __name__ == "__main__":
    unittest.main()

