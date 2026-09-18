"""Tests for GUI models, backend helpers, and view components."""

import os
import unittest
from unittest.mock import patch

# Set offscreen platform for headless Qt testing
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from sqlalchemy import create_engine

from rekordbox_collection_fixer.edit_play_count import (
    search_tracks,
    set_track_play_count,
    update_track_play_count,
)
from rekordbox_collection_fixer.gui.history_view import HistoryView
from rekordbox_collection_fixer.gui.play_count_view import PlayCountView
from rekordbox_collection_fixer.rename_history import (
    batch_update_history_records,
    fetch_history_records,
)

# Shared QApplication instance for Qt unit tests
app = QApplication.instance()
if app is None:
    app = QApplication([])


class TestDatabaseHelpers(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        with self.engine.begin() as conn:
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
                CREATE TABLE djmdHistory (
                    ID VARCHAR(255) PRIMARY KEY,
                    Name VARCHAR(255),
                    ParentID VARCHAR(255),
                    Attribute INTEGER,
                    DateCreated VARCHAR(255),
                    Seq INTEGER,
                    UUID VARCHAR(255),
                    rb_data_status INTEGER DEFAULT 0,
                    rb_local_data_status INTEGER DEFAULT 0,
                    rb_local_deleted INTEGER DEFAULT 0,
                    rb_local_synced INTEGER DEFAULT 1,
                    created_at VARCHAR(255),
                    updated_at VARCHAR(255)
                );
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdContent (ID, Title, SrcArtistName, SearchStr, DJPlayCount, rb_local_deleted)
                VALUES 
                    ('1', 'Titanium', 'David Guetta', 'titanium', 5, 0),
                    ('2', 'One More Time', 'Daft Punk', 'one more time', 2, 0);
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdHistory (ID, Name, ParentID, Attribute, DateCreated, Seq, rb_local_deleted)
                VALUES 
                    ('root', 'root', NULL, 1, '2026-01-01', 0, 0),
                    ('2026', '2026', 'root', 1, '2026-01-01', 1, 0),
                    ('202608', '8', '2026', 1, '2026-08-01', 8, 0),
                    ('h1', 'HISTORY 2026-08-10', '202608', 0, '2026-08-10', 1, 0),
                    ('h2', 'HISTORY 2026-08-20', '202608', 0, '2026-08-20', 2, 0);
            """)

    def test_set_track_play_count(self):
        old_val, new_val = set_track_play_count(self.engine, "1", 42)
        self.assertEqual(old_val, 5)
        self.assertEqual(new_val, 42)

        # Floor at zero test
        old_val, new_val = set_track_play_count(self.engine, "1", -10)
        self.assertEqual(old_val, 42)
        self.assertEqual(new_val, 0)

    def test_batch_update_history_records(self):
        records = fetch_history_records(self.engine, "djmdHistory")
        self.assertEqual(len(records), 2)

        updates = [
            {
                "record": records[0],
                "new_name": "HISTORY 2026-08-12 @ Fabric",
                "new_date": "2026-08-12",
            },
            {
                "record": records[1],
                "new_name": "HISTORY 2026-09-05 @ Amnesia",
                "new_date": "2026-09-05",
            },
        ]

        stats = batch_update_history_records(self.engine, updates, auto_realign=True)
        self.assertEqual(stats["updated_count"], 2)

        updated_records = fetch_history_records(self.engine, "djmdHistory")
        names = {r["name"] for r in updated_records}
        self.assertIn("HISTORY 2026-08-12 @ Fabric", names)
        self.assertIn("HISTORY 2026-09-05 @ Amnesia", names)


class TestHistoryView(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        with self.engine.begin() as conn:
            conn.exec_driver_sql("""
                CREATE TABLE djmdHistory (
                    ID VARCHAR(255) PRIMARY KEY,
                    Name VARCHAR(255),
                    ParentID VARCHAR(255),
                    Attribute INTEGER,
                    DateCreated VARCHAR(255),
                    Seq INTEGER,
                    UUID VARCHAR(255),
                    rb_data_status INTEGER DEFAULT 0,
                    rb_local_data_status INTEGER DEFAULT 0,
                    rb_local_deleted INTEGER DEFAULT 0,
                    rb_local_synced INTEGER DEFAULT 1,
                    created_at VARCHAR(255),
                    updated_at VARCHAR(255)
                );
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdHistory (ID, Name, ParentID, Attribute, DateCreated, Seq, rb_local_deleted)
                VALUES 
                    ('root', 'root', NULL, 1, '2026-01-01', 0, 0),
                    ('2026', '2026', 'root', 1, '2026-01-01', 1, 0),
                    ('202608', '8', '2026', 1, '2026-08-01', 8, 0),
                    ('h1', 'HISTORY 2026-08-10', '202608', 0, '2026-08-10', 1, 0),
                    ('h2', 'HISTORY 2026-08-20 @ Space', '202608', 0, '2026-08-20', 2, 0);
            """)
        self.records = fetch_history_records(self.engine, "djmdHistory")
        self.view = HistoryView()
        self.view.set_data(self.engine, self.records)

    def test_initial_table_population(self):
        self.assertEqual(self.view.table.rowCount(), 2)
        self.assertEqual(len(self.view.staged_edits), 0)
        self.assertFalse(self.view.save_btn.isEnabled())

    def test_inline_editing_stages_changes(self):
        # Edit name of row 0
        name_item = self.view.table.item(0, 2)
        name_item.setText("HISTORY 2026-08-10 @ Fabric VIP")
        self.view._on_cell_changed(0, 2)

        self.assertEqual(len(self.view.staged_edits), 1)
        self.assertTrue(self.view.save_btn.isEnabled())
        self.assertTrue(self.view.discard_btn.isEnabled())
        status_item = self.view.table.item(0, 0)
        self.assertEqual(status_item.text(), "● Staged")

    def test_auto_extract_dates(self):
        # Set a session with date in name but no date field
        self.view.original_records[0]["date"] = ""
        self.view.original_records[0]["name"] = "HISTORY 2026-08-15 Club Night"
        self.view._refresh_table()

        self.view._on_auto_extract_dates()
        rec_id = self.view.original_records[0]["id"]
        self.assertIn(rec_id, self.view.staged_edits)
        self.assertEqual(self.view.staged_edits[rec_id]["date"], "2026-08-15")

    def test_search_filter(self):
        self.view.search_input.setText("Space")
        self.assertEqual(self.view.table.rowCount(), 1)
        self.assertIn("Space", self.view.table.item(0, 2).text())


class TestPlayCountView(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        with self.engine.begin() as conn:
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
                INSERT INTO djmdContent (ID, Title, SrcArtistName, SearchStr, DJPlayCount, rb_local_deleted)
                VALUES 
                    ('1', 'Titanium', 'David Guetta', 'titanium', 5, 0),
                    ('2', 'One More Time', 'Daft Punk', 'one more time', 0, 0);
            """)
        self.view = PlayCountView()
        self.view.engine = self.engine
        # Synchronously populate records for test
        self.view.records = search_tracks(self.engine, "")
        self.view._populate_table()

    def test_table_populated(self):
        self.assertEqual(self.view.table.rowCount(), 2)

    def _find_row_by_track_id(self, track_id: str) -> int:
        for r in range(self.view.table.rowCount()):
            if self.view.table.item(r, 0).data(Qt.ItemDataRole.UserRole) == track_id:
                return r
        raise ValueError(f"Track ID {track_id} not found in table")

    def test_stepper_and_delta(self):
        with patch("rekordbox_collection_fixer.gui.play_count_view.is_rekordbox_running", return_value=False):
            self.view._apply_track_delta("1", 1)
            row_idx = self._find_row_by_track_id("1")
            count_item = self.view.table.item(row_idx, 2)
            self.assertEqual(count_item.text(), "6")

            self.view._apply_track_delta("1", -2)
            count_item = self.view.table.item(row_idx, 2)
            self.assertEqual(count_item.text(), "4")

    def test_direct_cell_edit(self):
        with patch("rekordbox_collection_fixer.gui.play_count_view.is_rekordbox_running", return_value=False):
            row_idx = self._find_row_by_track_id("1")
            count_item = self.view.table.item(row_idx, 2)
            count_item.setText("25")
            self.view._on_cell_changed(row_idx, 2)
            rec = next(r for r in self.view.records if r["id"] == "1")
            self.assertEqual(rec["play_count"], 25)


class TestAssets(unittest.TestCase):
    def test_asset_path_resolution(self):
        from rekordbox_collection_fixer.gui.assets import get_app_icon, get_asset_path

        icon_path = get_asset_path("icon.png")
        self.assertIsNotNone(icon_path)
        self.assertTrue(os.path.exists(icon_path))

        icon = get_app_icon()
        self.assertFalse(icon.isNull())

    def test_macos_metadata(self):
        from rekordbox_collection_fixer.gui.assets import get_asset_path
        from rekordbox_collection_fixer.gui.macos import (
            setup_macos_app_metadata,
            update_macos_menu_title,
        )

        # Should execute safely without throwing exceptions
        setup_macos_app_metadata(
            app_name="Rekordbox Collection Manager",
            icon_path=get_asset_path("icon.png"),
        )
        update_macos_menu_title("Rekordbox Collection Manager")


if __name__ == "__main__":
    unittest.main()
