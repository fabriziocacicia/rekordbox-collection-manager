"""Tests for rekordbox_collection_fixer.utils."""

import argparse
import os
import re
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine

from rekordbox_collection_fixer.utils import (
    MONTH_NAME_TO_INT,
    detect_content_table,
    ensure_rekordbox_not_running,
    extract_date_from_name,
    extract_year_month_from_name_or_date,
    get_rekordbox_timestamp,
    is_rekordbox_running,
    natural_sort_key,
    normalize_date_input,
    parse_month_number,
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
             patch("rekordbox_collection_fixer.utils.get_rekordbox_pid", return_value=0):
            current_pid = os.getpid()
            mock_sub.return_value = MagicMock(
                returncode=0,
                stdout=f"{current_pid} rekordbox\n9999 rekordbox-collection-fixer\n",
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
             patch("rekordbox_collection_fixer.utils.get_rekordbox_pid", return_value=0):
            mock_sub.return_value = MagicMock(returncode=1, stdout="")
            self.assertFalse(is_rekordbox_running())

    def test_ensure_rekordbox_not_running_exits(self):
        with patch("rekordbox_collection_fixer.utils.is_rekordbox_running", return_value=True):
            with self.assertRaises(SystemExit) as cm:
                ensure_rekordbox_not_running(force=False, exit_on_running=True)
            self.assertEqual(cm.exception.code, 1)

    def test_ensure_rekordbox_not_running_force_allowed(self):
        with patch("rekordbox_collection_fixer.utils.is_rekordbox_running", return_value=True):
            # When force=True, it should not exit
            running = ensure_rekordbox_not_running(force=True, exit_on_running=True)
            self.assertTrue(running)

    def test_ensure_rekordbox_not_running_when_not_running(self):
        with patch("rekordbox_collection_fixer.utils.is_rekordbox_running", return_value=False):
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


if __name__ == "__main__":
    unittest.main()
