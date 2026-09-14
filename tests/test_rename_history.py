"""Tests for rekordbox_collection_fixer.rename_history."""

import sys
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine

from rekordbox_collection_fixer.rename_history import (
    ensure_djmd_history_folders,
    fetch_history_records,
    is_rekordbox_running,
    main,
    normalize_date_input,
    parse_args,
    parse_year_month,
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

        update_history_record(engine, records[0], "HISTORY 2026-09-13", "2026-09-13")

        updated_records = fetch_history_records(engine, "djmdHistory")
        self.assertEqual(updated_records[0]["name"], "HISTORY 2026-09-13")
        self.assertEqual(updated_records[0]["date"], "2026-09-13")

    def test_parse_year_month(self):
        self.assertEqual(parse_year_month("2026-08-31 23:00:00"), (2026, 8))
        self.assertEqual(parse_year_month("2026-08-31"), (2026, 8))
        self.assertEqual(parse_year_month("2025/12/25"), (2025, 12))
        self.assertEqual(parse_year_month("2024.01.05"), (2024, 1))
        self.assertEqual(parse_year_month(""), (None, None))
        self.assertEqual(parse_year_month(None), (None, None))
        self.assertEqual(parse_year_month("invalid-date"), (None, None))

    def test_normalize_date_input(self):
        self.assertEqual(
            normalize_date_input("2026-08-31", "2026-09-14 16:50:22"),
            "2026-08-31 16:50:22",
        )
        self.assertEqual(
            normalize_date_input("2026-08-31 22:00:00", "2026-09-14 16:50:22"),
            "2026-08-31 22:00:00",
        )
        self.assertEqual(
            normalize_date_input("2026-08-31", "2026-09-14"),
            "2026-08-31",
        )

    def test_ensure_djmd_history_folders_and_hierarchy_update(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as conn:
            conn.exec_driver_sql("""
                CREATE TABLE djmdHistory (
                    ID VARCHAR(255) PRIMARY KEY,
                    Seq INTEGER,
                    Name VARCHAR(255),
                    Attribute INTEGER,
                    ParentID VARCHAR(255),
                    DateCreated VARCHAR(255),
                    UUID VARCHAR(255),
                    updated_at VARCHAR(255)
                );
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdHistory (ID, Name, DateCreated, Attribute, ParentID) 
                VALUES ('100', 'HISTORY 2026-09-14', '2026-09-14 16:00:00', 0, '202609');
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdHistory (ID, Name, Attribute, ParentID) 
                VALUES ('2026', '2026', 1, 'root');
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdHistory (ID, Name, Attribute, ParentID) 
                VALUES ('202609', '9', 1, '2026');
            """)

        records = fetch_history_records(engine, "djmdHistory")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["parent_id"], "202609")

        new_parent = update_history_record(
            engine,
            records[0],
            "HISTORY 2026-08-31",
            "2026-08-31 23:00:00",
        )
        self.assertEqual(new_parent, "202608")

        with engine.connect() as conn:
            session = conn.exec_driver_sql("SELECT Name, DateCreated, ParentID, updated_at FROM djmdHistory WHERE ID = '100'").mappings().first()
            self.assertEqual(session["Name"], "HISTORY 2026-08-31")
            self.assertEqual(session["DateCreated"], "2026-08-31 23:00:00")
            self.assertEqual(session["ParentID"], "202608")
            self.assertIsNotNone(session["updated_at"])

            aug_folder = conn.exec_driver_sql("SELECT ID, Name, ParentID, Attribute FROM djmdHistory WHERE ID = '202608'").mappings().first()
            self.assertIsNotNone(aug_folder)
            self.assertEqual(aug_folder["Name"], "8")
            self.assertEqual(aug_folder["ParentID"], "2026")
            self.assertEqual(aug_folder["Attribute"], 1)

    def test_update_realigns_folder_when_date_already_set(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as conn:
            conn.exec_driver_sql("""
                CREATE TABLE djmdHistory (
                    ID VARCHAR(255) PRIMARY KEY,
                    Name VARCHAR(255),
                    Attribute INTEGER,
                    ParentID VARCHAR(255),
                    DateCreated VARCHAR(255)
                );
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdHistory (ID, Name, DateCreated, Attribute, ParentID) 
                VALUES ('100', 'HISTORY 2026-08-31', '2026-08-31 15:00:00', 0, '202609');
            """)

        records = fetch_history_records(engine, "djmdHistory")
        new_parent = update_history_record(engine, records[0], "SET IN AUGUST", None)
        self.assertEqual(new_parent, "202608")

        with engine.connect() as conn:
            session = conn.exec_driver_sql("SELECT ParentID, Name FROM djmdHistory WHERE ID = '100'").mappings().first()
            self.assertEqual(session["ParentID"], "202608")
            self.assertEqual(session["Name"], "SET IN AUGUST")

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
            mock_text.side_effect = [
                MagicMock(ask=MagicMock(return_value="HISTORY 2026-09-13")),
                MagicMock(ask=MagicMock(return_value="2026-09-13")),
            ]
            mock_confirm.return_value.ask.return_value = False

            run_interactive_session(engine, "djmdHistory", "dummy.db")

            self.assertEqual(mock_text.call_args_list[0][1]["default"], "HISTORY 2026-09-14")
            self.assertEqual(mock_text.call_args_list[1][1]["default"], "2026-09-14")

        records = fetch_history_records(engine, "djmdHistory")
        self.assertEqual(records[0]["name"], "HISTORY 2026-09-13")
        self.assertEqual(records[0]["date"], "2026-09-13")

    def test_interactive_session_detects_mismatch_and_updates_parent(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as conn:
            conn.exec_driver_sql("""
                CREATE TABLE djmdHistory (
                    ID VARCHAR(255) PRIMARY KEY,
                    Name VARCHAR(255),
                    DateCreated VARCHAR(255),
                    Attribute INTEGER,
                    ParentID VARCHAR(255)
                );
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdHistory (ID, Name, DateCreated, Attribute, ParentID) 
                VALUES ('42', 'HISTORY 2026-08-31', '2026-08-31 22:00:00', 0, '202609');
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdHistory (ID, Name, Attribute, ParentID) 
                VALUES ('2026', '2026', 1, 'root');
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdHistory (ID, Name, Attribute, ParentID) 
                VALUES ('202609', '9', 1, '2026');
            """)

        with patch("questionary.select") as mock_select, \
             patch("questionary.text") as mock_text, \
             patch("questionary.confirm") as mock_confirm:

            mock_select.return_value.ask.return_value = "42"
            # User simply confirms the existing name and date
            mock_text.side_effect = [
                MagicMock(ask=MagicMock(return_value="HISTORY 2026-08-31")),
                MagicMock(ask=MagicMock(return_value="2026-08-31 22:00:00")),
            ]
            mock_confirm.return_value.ask.return_value = False

            run_interactive_session(engine, "djmdHistory", "dummy.db")

            # Verify choice had mismatch warning indicator
            choices = mock_select.call_args[1]["choices"]
            first_choice = choices[0]
            self.assertIn("⚠️", first_choice.title)
            self.assertIn("Folder: 202609 ➔ 202608", first_choice.title)

        with engine.connect() as conn:
            session = conn.exec_driver_sql("SELECT ParentID FROM djmdHistory WHERE ID = '42'").mappings().first()
            self.assertEqual(session["ParentID"], "202608")

    def test_main_aborts_when_rekordbox_running(self):
        with patch("rekordbox_collection_fixer.rename_history.ensure_rekordbox_not_running", side_effect=SystemExit(1)):
            with self.assertRaises(SystemExit) as cm:
                main(["dummy.db"])
            self.assertEqual(cm.exception.code, 1)

    def test_main_force_passes_flag_to_ensure_check(self):
        with patch("rekordbox_collection_fixer.rename_history.ensure_rekordbox_not_running") as mock_ensure, \
             patch("rekordbox_collection_fixer.rename_history.resolve_db_path", return_value="dummy.db"), \
             patch("rekordbox_collection_fixer.rename_history.open_database", return_value=(MagicMock(), "djmdHistory")), \
             patch("rekordbox_collection_fixer.rename_history.run_interactive_session") as mock_session:
            main(["--force", "dummy.db"])
            mock_ensure.assert_called_once_with(force=True)
            mock_session.assert_called_once()


if __name__ == "__main__":
    unittest.main()

