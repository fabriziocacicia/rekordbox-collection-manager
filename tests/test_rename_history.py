"""Tests for rekordbox_collection_fixer.rename_history."""

import sys
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine

from rekordbox_collection_fixer.rename_history import (
    ensure_djmd_history_folders,
    extract_date_from_name,
    extract_year_month_from_name_or_date,
    fetch_history_records,
    history_session_sort_key,
    is_rekordbox_running,
    main,
    natural_sort_key,
    normalize_date_input,
    parse_args,
    parse_month_number,
    parse_year_month,
    realign_history_order,
    resolve_db_path,
    run_interactive_session,
    update_history_record,
    year_sort_key,
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

    def test_fetch_and_realign_ignores_soft_deleted(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as conn:
            conn.exec_driver_sql("""
                CREATE TABLE djmdHistory (
                    ID VARCHAR(255) PRIMARY KEY,
                    Seq INTEGER,
                    Name VARCHAR(255),
                    DateCreated VARCHAR(255),
                    Attribute INTEGER,
                    ParentID VARCHAR(255),
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdHistory (ID, Seq, Name, DateCreated, Attribute, ParentID, rb_local_deleted) 
                VALUES ('1', 1, 'HISTORY 2026-07-05', '2026-07-05 11:00:00', 0, '202607', 0);
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdHistory (ID, Seq, Name, DateCreated, Attribute, ParentID, rb_local_deleted) 
                VALUES ('2', 2, 'HISTORY 2026-07-05 (1)', '2026-07-05 11:00:00', 0, '202607', 1);
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdHistory (ID, Seq, Name, DateCreated, Attribute, ParentID, rb_local_deleted) 
                VALUES ('3', 3, 'HISTORY 2026-07-06', '2026-07-06 11:00:00', 0, '202607', 0);
            """)

        records = fetch_history_records(engine, "djmdHistory")
        self.assertEqual(len(records), 2)
        record_ids = {r["id"] for r in records}
        self.assertIn("1", record_ids)
        self.assertIn("3", record_ids)
        self.assertNotIn("2", record_ids)

        # Test realign_history_order ignores ID 2 and sequences ID 1 as Seq=1, ID 3 as Seq=2
        realign_history_order(engine)
        with engine.connect() as conn:
            row1 = conn.exec_driver_sql("SELECT Seq FROM djmdHistory WHERE ID = '1';").fetchone()
            row3 = conn.exec_driver_sql("SELECT Seq FROM djmdHistory WHERE ID = '3';").fetchone()
            self.assertEqual(row1[0], 1)
            self.assertEqual(row3[0], 2)

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
                MagicMock(ask=MagicMock(return_value="HISTORY 2026-09-13")),  # Base
                MagicMock(ask=MagicMock(return_value="")),                    # Location
                MagicMock(ask=MagicMock(return_value="")),                    # Artist
                MagicMock(ask=MagicMock(return_value="2026-09-13")),          # Date
            ]
            mock_confirm.return_value.ask.return_value = False

            run_interactive_session(engine, "djmdHistory", "dummy.db")

            self.assertEqual(mock_text.call_args_list[0][1]["default"], "HISTORY 2026-09-14")
            self.assertEqual(mock_text.call_args_list[1][1]["default"], "")
            self.assertEqual(mock_text.call_args_list[2][1]["default"], "")
            self.assertEqual(mock_text.call_args_list[3][1]["default"], "2026-09-13")

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
                MagicMock(ask=MagicMock(return_value="HISTORY 2026-08-31")),  # Base
                MagicMock(ask=MagicMock(return_value="")),                    # Location
                MagicMock(ask=MagicMock(return_value="")),                    # Artist
                MagicMock(ask=MagicMock(return_value="2026-08-31 22:00:00")), # Date
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

    def test_interactive_session_custom_schema_location_and_artist(self):
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
                VALUES ('99', 'HISTORY 2026-07-24 (1) @ Space w Carl Cox', '2026-07-24 23:00:00', 0, '202607');
            """)

        with patch("questionary.select") as mock_select, \
             patch("questionary.text") as mock_text, \
             patch("questionary.confirm") as mock_confirm:

            mock_select.return_value.ask.return_value = "99"
            mock_text.side_effect = [
                MagicMock(ask=MagicMock(return_value="HISTORY 2026-07-25 (2)")),  # Base
                MagicMock(ask=MagicMock(return_value="Amnesia")),                 # Location
                MagicMock(ask=MagicMock(return_value="Marco Carola")),            # Artist
                MagicMock(ask=MagicMock(return_value="2026-07-25 23:00:00")),     # Date
            ]
            mock_confirm.return_value.ask.return_value = False

            run_interactive_session(engine, "djmdHistory", "dummy.db")

            # Check that defaults were pre-filled correctly by parsing existing name
            self.assertEqual(mock_text.call_args_list[0][1]["default"], "HISTORY 2026-07-24 (1)")
            self.assertEqual(mock_text.call_args_list[1][1]["default"], "Space")
            self.assertEqual(mock_text.call_args_list[2][1]["default"], "Carl Cox")

        records = fetch_history_records(engine, "djmdHistory")
        self.assertEqual(records[0]["name"], "HISTORY 2026-07-25 (2) @ Amnesia w Marco Carola")
        self.assertEqual(records[0]["date"], "2026-07-25 23:00:00")

    def test_interactive_session_clear_location_add_artist(self):
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
                VALUES ('101', 'HISTORY 2026-07-24 @ Fabric', '2026-07-24 23:00:00', 0, '202607');
            """)

        with patch("questionary.select") as mock_select, \
             patch("questionary.text") as mock_text, \
             patch("questionary.confirm") as mock_confirm:

            mock_select.return_value.ask.return_value = "101"
            # User leaves base unchanged, clears location, and fills artist
            mock_text.side_effect = [
                MagicMock(ask=MagicMock(return_value="HISTORY 2026-07-24")),  # Base
                MagicMock(ask=MagicMock(return_value="")),                    # Cleared location
                MagicMock(ask=MagicMock(return_value="Carl Cox")),            # Added artist
                MagicMock(ask=MagicMock(return_value="2026-07-24 23:00:00")), # Date
            ]
            mock_confirm.return_value.ask.return_value = False

            run_interactive_session(engine, "djmdHistory", "dummy.db")

            # Check prefilled defaults
            self.assertEqual(mock_text.call_args_list[0][1]["default"], "HISTORY 2026-07-24")
            self.assertEqual(mock_text.call_args_list[1][1]["default"], "Fabric")
            self.assertEqual(mock_text.call_args_list[2][1]["default"], "")

        records = fetch_history_records(engine, "djmdHistory")
        self.assertEqual(records[0]["name"], "HISTORY 2026-07-24 w Carl Cox")

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

    def test_parse_month_number(self):
        # 1. From 6-digit ID
        self.assertEqual(parse_month_number("anything", id_val="202601"), 1)
        self.assertEqual(parse_month_number("anything", id_val="202608"), 8)
        self.assertEqual(parse_month_number("anything", id_val="202609"), 9)
        self.assertEqual(parse_month_number("anything", id_val="202612"), 12)

        # 2. From integer name
        self.assertEqual(parse_month_number("1"), 1)
        self.assertEqual(parse_month_number("08"), 8)
        self.assertEqual(parse_month_number("9"), 9)
        self.assertEqual(parse_month_number("12"), 12)

        # 3. From English / Italian month names
        self.assertEqual(parse_month_number("January"), 1)
        self.assertEqual(parse_month_number("Gennaio"), 1)
        self.assertEqual(parse_month_number("September"), 9)
        self.assertEqual(parse_month_number("Settembre"), 9)
        self.assertEqual(parse_month_number("August"), 8)
        self.assertEqual(parse_month_number("Agosto"), 8)
        self.assertEqual(parse_month_number("dicembre"), 12)

        # 4. From DateCreated
        self.assertEqual(parse_month_number("other", date_created="2026-07-15 14:00:00"), 7)

        # 5. Fallback for unrecognized
        self.assertEqual(parse_month_number("unknown_month"), 99)

    def test_natural_sort_key(self):
        s1 = natural_sort_key("HISTORY 2026-08-01")
        s2 = natural_sort_key("HISTORY 2026-08-01 (1)")
        s3 = natural_sort_key("HISTORY 2026-08-01 (2)")
        s10 = natural_sort_key("HISTORY 2026-08-01 (10)")

        self.assertTrue(s1 < s2)
        self.assertTrue(s2 < s3)
        self.assertTrue(s3 < s10)

    def test_history_session_sort_key(self):
        rows = [
            {"ID": "1", "Name": "HISTORY 2026-08-30", "DateCreated": "2026-08-31 11:42:51"},
            {"ID": "2", "Name": "HISTORY 2026-08-01", "DateCreated": "2026-08-01 17:13:20"},
            {"ID": "3", "Name": "HISTORY 2026-08-01 (2)", "DateCreated": "2026-08-01 17:13:23"},
            {"ID": "4", "Name": "HISTORY 2026-08-01 (1)", "DateCreated": "2026-08-01 17:13:23"},
            {"ID": "5", "Name": "HISTORY 2026-08-28", "DateCreated": "2026-08-28 17:09:03"},
            {"ID": "6", "Name": "HISTORY 2026-08-15 (0)", "DateCreated": "2026-08-28 17:09:03"},
            {"ID": "7", "Name": "HISTORY 2026-08-23 (0)", "DateCreated": "2026-08-28 17:09:03"},
            {"ID": "8", "Name": "HISTORY 2026-08-07", "DateCreated": "2026-08-07 17:55:10"},
        ]

        sorted_rows = sorted(rows, key=history_session_sort_key)
        sorted_names = [r["Name"] for r in sorted_rows]

        expected_order = [
            "HISTORY 2026-08-01",
            "HISTORY 2026-08-01 (1)",
            "HISTORY 2026-08-01 (2)",
            "HISTORY 2026-08-07",
            "HISTORY 2026-08-15 (0)",
            "HISTORY 2026-08-23 (0)",
            "HISTORY 2026-08-28",
            "HISTORY 2026-08-30",
        ]
        self.assertEqual(sorted_names, expected_order)

    def test_history_session_sort_key_entries_without_parens_first(self):
        """Ensure that on the same date, entries without () sort first, then (0), (1), (2) etc."""
        rows = [
            {"ID": "1", "Name": "HISTORY 2026-07-08 (1)", "DateCreated": "2026-07-08 17:06:45"},
            {"ID": "2", "Name": "HISTORY 2026-07-08 (1) @ OZeta", "DateCreated": "2026-07-08 17:51:12"},
            {"ID": "3", "Name": "HISTORY 2026-07-08 @ OZeta", "DateCreated": "2026-07-08 17:51:12"},
            {"ID": "4", "Name": "HISTORY 2026-07-08 (2) @ OZeta", "DateCreated": "2026-07-08 17:55:00"},
        ]

        sorted_rows = sorted(rows, key=history_session_sort_key)
        sorted_names = [r["Name"] for r in sorted_rows]

        expected_order = [
            "HISTORY 2026-07-08 @ OZeta",
            "HISTORY 2026-07-08 (1)",
            "HISTORY 2026-07-08 (1) @ OZeta",
            "HISTORY 2026-07-08 (2) @ OZeta",
        ]
        self.assertEqual(sorted_names, expected_order)

    def test_year_sort_key(self):
        years = [
            {"ID": "2026", "Name": "2026"},
            {"ID": "2023", "Name": "2023"},
            {"ID": "2025", "Name": "2025"},
            {"ID": "2024", "Name": "2024"},
        ]
        sorted_years = sorted(years, key=year_sort_key)
        self.assertEqual([y["Name"] for y in sorted_years], ["2023", "2024", "2025", "2026"])

    def test_realign_history_order_complete_scenario(self):
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
                    updated_at VARCHAR(255)
                );
            """)

            # 1. Years under root (scrambled Seq)
            conn.exec_driver_sql("INSERT INTO djmdHistory VALUES ('2026', 1, '2026', 1, 'root', '2026-01-01', NULL);")
            conn.exec_driver_sql("INSERT INTO djmdHistory VALUES ('2023', 2, '2023', 1, 'root', '2023-01-01', NULL);")
            conn.exec_driver_sql("INSERT INTO djmdHistory VALUES ('2024', 3, '2024', 1, 'root', '2024-01-01', NULL);")
            conn.exec_driver_sql("INSERT INTO djmdHistory VALUES ('2025', 4, '2025', 1, 'root', '2025-01-01', NULL);")

            # 2. Months under 2026 (September on top with Seq 1, then Jan, Feb, Aug)
            conn.exec_driver_sql("INSERT INTO djmdHistory VALUES ('202609', 1, '9', 1, '2026', '2026-09-09', NULL);")
            conn.exec_driver_sql("INSERT INTO djmdHistory VALUES ('202601', 2, '1', 1, '2026', '2026-01-01', NULL);")
            conn.exec_driver_sql("INSERT INTO djmdHistory VALUES ('202602', 3, '2', 1, '2026', '2026-02-01', NULL);")
            conn.exec_driver_sql("INSERT INTO djmdHistory VALUES ('202608', 4, '8', 1, '2026', '2026-08-01', NULL);")

            # 3. Sessions under 202608 (August): out of chronological order
            conn.exec_driver_sql("INSERT INTO djmdHistory VALUES ('s1', 1, 'HISTORY 2026-08-01', 0, '202608', '2026-08-01 17:13:20', NULL);")
            conn.exec_driver_sql("INSERT INTO djmdHistory VALUES ('s2', 2, 'HISTORY 2026-08-30', 0, '202608', '2026-08-31 11:42:51', NULL);")
            conn.exec_driver_sql("INSERT INTO djmdHistory VALUES ('s3', 3, 'HISTORY 2026-08-01 (1)', 0, '202608', '2026-08-01 17:13:23', NULL);")
            conn.exec_driver_sql("INSERT INTO djmdHistory VALUES ('s4', 4, 'HISTORY 2026-08-07', 0, '202608', '2026-08-07 17:55:10', NULL);")
            conn.exec_driver_sql("INSERT INTO djmdHistory VALUES ('s5', 5, 'HISTORY 2026-08-28', 0, '202608', '2026-08-28 17:09:03', NULL);")
            conn.exec_driver_sql("INSERT INTO djmdHistory VALUES ('s6', 6, 'HISTORY 2026-08-23', 0, '202608', '2026-08-28 17:09:03', NULL);")

        stats = realign_history_order(engine)
        self.assertGreater(stats["updated_rows"], 0)

        with engine.connect() as conn:
            # Check root years order
            years = conn.exec_driver_sql("SELECT ID, Seq FROM djmdHistory WHERE ParentID = 'root' ORDER BY Seq ASC;").fetchall()
            self.assertEqual([y[0] for y in years], ["2023", "2024", "2025", "2026"])

            # Check 2026 months order (January to September)
            months = conn.exec_driver_sql("SELECT ID, Seq FROM djmdHistory WHERE ParentID = '2026' ORDER BY Seq ASC;").fetchall()
            self.assertEqual([m[0] for m in months], ["202601", "202602", "202608", "202609"])
            self.assertEqual([m[1] for m in months], [1, 2, 3, 4])

            # Check 202608 sessions order (08-01 top, 08-30 bottom)
            sessions = conn.exec_driver_sql("SELECT ID, Name, Seq FROM djmdHistory WHERE ParentID = '202608' ORDER BY Seq ASC;").fetchall()
            expected_sessions = [
                ("s1", "HISTORY 2026-08-01", 1),
                ("s3", "HISTORY 2026-08-01 (1)", 2),
                ("s4", "HISTORY 2026-08-07", 3),
                ("s6", "HISTORY 2026-08-23", 4),
                ("s5", "HISTORY 2026-08-28", 5),
                ("s2", "HISTORY 2026-08-30", 6),
            ]
            self.assertEqual(sessions, expected_sessions)

    def test_update_history_record_automatically_realigns(self):
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
                    updated_at VARCHAR(255)
                );
            """)
            # Month 9 is Seq 1, Month 1 is Seq 2
            conn.exec_driver_sql("INSERT INTO djmdHistory VALUES ('2026', 1, '2026', 1, 'root', '2026-01-01', NULL);")
            conn.exec_driver_sql("INSERT INTO djmdHistory VALUES ('202609', 1, '9', 1, '2026', '2026-09-09', NULL);")
            conn.exec_driver_sql("INSERT INTO djmdHistory VALUES ('202601', 2, '1', 1, '2026', '2026-01-01', NULL);")
            conn.exec_driver_sql("INSERT INTO djmdHistory VALUES ('s1', 1, 'OLD SESSION', 0, '202601', '2026-01-01 10:00:00', NULL);")

        records = fetch_history_records(engine, "djmdHistory")
        update_history_record(engine, records[0], "RENAMED SESSION", None)

        with engine.connect() as conn:
            months = conn.exec_driver_sql("SELECT ID, Seq FROM djmdHistory WHERE ParentID = '2026' ORDER BY Seq ASC;").fetchall()
            self.assertEqual(months, [("202601", 1), ("202609", 2)])

    def test_main_realign_flag(self):
        with patch("rekordbox_collection_fixer.rename_history.ensure_rekordbox_not_running"), \
             patch("rekordbox_collection_fixer.rename_history.resolve_db_path", return_value="dummy.db"), \
             patch("rekordbox_collection_fixer.rename_history.open_database", return_value=(MagicMock(), "djmdHistory")), \
             patch("rekordbox_collection_fixer.rename_history.realign_history_order", return_value={"updated_rows": 10, "parents_processed": 5}) as mock_realign, \
             patch("rekordbox_collection_fixer.rename_history.run_interactive_session") as mock_session:

            main(["--sort", "dummy.db"])
            mock_realign.assert_called_once()
            mock_session.assert_not_called()

    def test_interactive_session_realign_choice(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as conn:
            conn.exec_driver_sql("""
                CREATE TABLE djmdHistory (
                    ID VARCHAR(255) PRIMARY KEY,
                    Seq INTEGER,
                    Name VARCHAR(255),
                    DateCreated VARCHAR(255),
                    Attribute INTEGER,
                    ParentID VARCHAR(255)
                );
            """)
            conn.exec_driver_sql("INSERT INTO djmdHistory VALUES ('s1', 1, 'SESSION', '2026-01-01', 0, '202601');")

        with patch("questionary.select") as mock_select, \
             patch("rekordbox_collection_fixer.rename_history.realign_history_order", return_value={"updated_rows": 2, "parents_processed": 1}) as mock_realign:

            # First loop: user chooses __REALIGN__, second loop: user cancels
            mock_select.return_value.ask.side_effect = ["__REALIGN__", "__CANCEL__"]

            run_interactive_session(engine, "djmdHistory", "dummy.db")

            mock_realign.assert_called_once_with(engine)

    def test_extract_date_from_name(self):
        self.assertEqual(extract_date_from_name("HISTORY 2026-07-29"), "2026-07-29")
        self.assertEqual(extract_date_from_name("HISTORY 2026-07-29 (2)"), "2026-07-29")
        self.assertEqual(extract_date_from_name("2026/08/15 Club Set"), "2026-08-15")
        self.assertIsNone(extract_date_from_name("Club Night"))
        self.assertIsNone(extract_date_from_name(""))
        self.assertIsNone(extract_date_from_name(None))

    def test_extract_year_month_from_name_or_date(self):
        # Name has date, date_str has different date -> Name wins
        self.assertEqual(
            extract_year_month_from_name_or_date("HISTORY 2026-07-29", "2026-08-01 17:13:20"),
            (2026, 7),
        )
        # Name has no date -> date_str used
        self.assertEqual(
            extract_year_month_from_name_or_date("Club Night", "2026-08-01 17:13:20"),
            (2026, 8),
        )

    def test_update_history_record_uses_name_for_folder(self):
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
                    updated_at VARCHAR(255)
                );
            """)
            conn.exec_driver_sql("INSERT INTO djmdHistory VALUES ('2026', 1, '2026', 1, 'root', '2026-01-01', NULL);")
            conn.exec_driver_sql("INSERT INTO djmdHistory VALUES ('202608', 1, '8', 1, '2026', '2026-08-01', NULL);")
            conn.exec_driver_sql("INSERT INTO djmdHistory VALUES ('s1', 1, 'HISTORY 2026-08-01', 0, '202608', '2026-08-01 17:13:20', NULL);")

        records = fetch_history_records(engine, "djmdHistory")
        # User changes Name to July date, without changing new_date (or new_date=None)
        new_parent = update_history_record(engine, records[0], "HISTORY 2026-07-29", None)
        self.assertEqual(new_parent, "202607")

        with engine.connect() as conn:
            session = conn.exec_driver_sql("SELECT ParentID, Name, DateCreated FROM djmdHistory WHERE ID = 's1'").mappings().first()
            self.assertEqual(session["ParentID"], "202607")
            self.assertEqual(session["Name"], "HISTORY 2026-07-29")
            self.assertEqual(session["DateCreated"], "2026-07-29 17:13:20")

    def test_realign_history_order_moves_sessions_matching_name_date(self):
        # Reproducing user exact situation:
        # History renamed to July (2026-07-29) was still stuck in August folder (202608)
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
                    updated_at VARCHAR(255)
                );
            """)
            conn.exec_driver_sql("INSERT INTO djmdHistory VALUES ('2026', 1, '2026', 1, 'root', '2026-01-01', NULL);")
            conn.exec_driver_sql("INSERT INTO djmdHistory VALUES ('202607', 1, '7', 1, '2026', '2026-07-01', NULL);")
            conn.exec_driver_sql("INSERT INTO djmdHistory VALUES ('202608', 2, '8', 1, '2026', '2026-08-01', NULL);")

            # Existing July session
            conn.exec_driver_sql("INSERT INTO djmdHistory VALUES ('j1', 1, 'HISTORY 2026-07-10', 0, '202607', '2026-07-10 12:00:00', NULL);")
            # Session renamed to July 29 but stuck in August folder (202608) with August DateCreated
            conn.exec_driver_sql("INSERT INTO djmdHistory VALUES ('a1', 1, 'HISTORY 2026-07-29', 0, '202608', '2026-08-01 17:13:20', NULL);")
            # Normal August session
            conn.exec_driver_sql("INSERT INTO djmdHistory VALUES ('a2', 2, 'HISTORY 2026-08-10', 0, '202608', '2026-08-10 12:00:00', NULL);")

        realign_history_order(engine)

        with engine.connect() as conn:
            # a1 should have moved to 202607!
            a1 = conn.exec_driver_sql("SELECT ParentID, DateCreated, Seq FROM djmdHistory WHERE ID = 'a1'").mappings().first()
            self.assertEqual(a1["ParentID"], "202607")
            self.assertEqual(a1["DateCreated"], "2026-07-29 17:13:20")
            self.assertEqual(a1["Seq"], 2)  # after j1 (July 10)

            # August should only contain a2
            aug_sessions = conn.exec_driver_sql("SELECT ID, Seq FROM djmdHistory WHERE ParentID = '202608'").fetchall()
            self.assertEqual(aug_sessions, [("a2", 1)])


if __name__ == "__main__":
    unittest.main()


