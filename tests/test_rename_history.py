"""Tests for rekordbox_collection_fixer.rename_history."""

import sys
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine

from rekordbox_collection_fixer.rename_history import (
    fetch_history_records,
    parse_args,
    resolve_db_path,
    run_interactive_session,
    update_history_record,
)


class TestRenameHistory(unittest.TestCase):
    def test_parse_args(self):
        with patch.object(sys, "argv", ["rename-history"]):
            args = parse_args()
            self.assertIsNone(args.db_path)
            self.assertIsNone(args.db_path_flag)

        with patch.object(sys, "argv", ["rename-history", "my_collection.db"]):
            args = parse_args()
            self.assertEqual(args.db_path, "my_collection.db")

        with patch.object(sys, "argv", ["rename-history", "--db-path", "flag_lib.db"]):
            args = parse_args()
            self.assertEqual(args.db_path_flag, "flag_lib.db")

        with patch.object(sys, "argv", ["rename-history", "-d", "short_lib.db"]):
            args = parse_args()
            self.assertEqual(args.db_path_flag, "short_lib.db")

        # Test explicit argv parameter without touching sys.argv
        args = parse_args(["explicit.db", "-d", "flag.db"])
        self.assertEqual(args.db_path, "explicit.db")
        self.assertEqual(args.db_path_flag, "flag.db")

    def test_resolve_existing_path(self):
        with patch.object(sys, "argv", ["rename-history", "pyproject.toml"]):
            args = parse_args()
            resolved = resolve_db_path(args)
            self.assertTrue(resolved.endswith("pyproject.toml"))

    def test_fetch_and_update_djmd_history(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as conn:
            conn.exec_driver_sql("""
                CREATE TABLE djmdHistory (
                    ID VARCHAR(255) PRIMARY KEY,
                    Name VARCHAR(255),
                    DateCreated VARCHAR(255),
                    Attribute INTEGER
                );
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdHistory (ID, Name, DateCreated, Attribute) 
                VALUES ('100', 'HISTORY 2026-09-14', '2026-09-14', 0);
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdHistory (ID, Name, DateCreated, Attribute) 
                VALUES ('202609', '9', '2026-09-09', 1);
            """)

        records = fetch_history_records(engine, "djmdHistory")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["id"], "100")
        self.assertEqual(records[0]["name"], "HISTORY 2026-09-14")

        update_history_record(engine, records[0], "HISTORY 2026-09-13")

        updated_records = fetch_history_records(engine, "djmdHistory")
        self.assertEqual(updated_records[0]["name"], "HISTORY 2026-09-13")
        self.assertEqual(updated_records[0]["date"], "2026-09-14")

    def test_fetch_and_update_device_history(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as conn:
            conn.exec_driver_sql("""
                CREATE TABLE history (
                    history_id INTEGER PRIMARY KEY,
                    name VARCHAR(255),
                    attribute INTEGER
                );
            """)
            conn.exec_driver_sql("""
                INSERT INTO history (history_id, name, attribute) 
                VALUES (1, 'SESSION 1', 0);
            """)

        records = fetch_history_records(engine, "history")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["id"], "1")
        self.assertEqual(records[0]["name"], "SESSION 1")

        update_history_record(engine, records[0], "SET AT CLUB")

        updated_records = fetch_history_records(engine, "history")
        self.assertEqual(updated_records[0]["name"], "SET AT CLUB")

    def test_interactive_session_flow(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as conn:
            conn.exec_driver_sql("""
                CREATE TABLE djmdHistory (
                    ID VARCHAR(255) PRIMARY KEY,
                    Name VARCHAR(255),
                    DateCreated VARCHAR(255),
                    Attribute INTEGER
                );
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdHistory (ID, Name, DateCreated, Attribute) 
                VALUES ('42', 'HISTORY 2026-09-14', '2026-09-14', 0);
            """)

        with patch("questionary.select") as mock_select, \
             patch("questionary.text") as mock_text, \
             patch("questionary.confirm") as mock_confirm:

            mock_select.return_value.ask.return_value = "42"
            mock_text.return_value.ask.return_value = "HISTORY 2026-09-13"
            mock_confirm.return_value.ask.return_value = False

            run_interactive_session(engine, "djmdHistory", "dummy.db")

            self.assertEqual(mock_text.call_args[1]["default"], "HISTORY 2026-09-14")

        records = fetch_history_records(engine, "djmdHistory")
        self.assertEqual(records[0]["name"], "HISTORY 2026-09-13")
        self.assertEqual(records[0]["date"], "2026-09-14")


if __name__ == "__main__":
    unittest.main()
