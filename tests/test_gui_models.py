"""Tests for GUI models, backend helpers, and view components."""

import os
import unittest
from unittest.mock import patch

# Set offscreen platform for headless Qt testing
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from rekordbox_collection_manager.edit_play_count import (
    search_tracks,
    set_track_play_count,
    update_track_play_count,
)
from rekordbox_collection_manager.gui.chip import DJChipsContainer
from rekordbox_collection_manager.gui.history_detail_view import HistoryDetailView
from rekordbox_collection_manager.gui.history_view import (
    HistoryView,
    LocationDelegate,
    LocationLineEdit,
)
from rekordbox_collection_manager.gui.play_count_view import PlayCountView
from rekordbox_collection_manager.gui.tracks_view import TracksView
from rekordbox_collection_manager.rename_history import (
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

    def test_search_filter(self):
        self.view.search_input.setText("Space")
        self.assertEqual(self.view.table.rowCount(), 1)
        self.assertEqual(self.view.table.item(0, 2).text(), "HISTORY 2026-08-20")
        self.assertEqual(self.view.table.item(0, 3).text(), "Space")

    def test_inline_editing_location_and_djs(self):
        # Edit location of row 0
        loc_item = self.view.table.item(0, 3)
        rec_id = loc_item.data(Qt.ItemDataRole.UserRole)
        loc_item.setText("Amnesia")
        self.view._on_cell_changed(0, 3)
        self.assertEqual(len(self.view.staged_edits), 1)
        self.assertIn("Amnesia", self.view.staged_edits[rec_id]["name"])

        # Edit played with of row 0
        art_item = self.view.table.item(0, 4)
        art_item.setText("Carl Cox")
        self.view._on_cell_changed(0, 4)
        self.assertIn("Amnesia", self.view.staged_edits[rec_id]["name"])
        self.assertIn("Carl Cox", self.view.staged_edits[rec_id]["name"])

    def test_folder_tree_arrow_key_navigation_updates_table(self):
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent

        records = [
            {"id": "h1", "name": "HISTORY 2026-08-10", "date": "2026-08-10", "is_deleted": False},
            {"id": "h2", "name": "HISTORY 2026-09-15 @ Amnesia", "date": "2026-09-15", "is_deleted": False},
        ]
        self.view.set_data(self.engine, records)

        # Initially All Sessions is selected and current
        self.assertEqual(self.view.active_folder_filter, "active_all")
        self.assertEqual(self.view.table.rowCount(), 2)
        self.assertIsNotNone(self.view.folder_tree.currentItem())
        self.assertEqual(self.view.folder_tree.currentItem().data(0, Qt.ItemDataRole.UserRole), "active_all")

        # Press Down arrow to navigate to Year (2026)
        key_down = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier)
        self.view.folder_tree.keyPressEvent(key_down)
        self.assertEqual(self.view.active_folder_filter, "active_year_2026")
        self.assertEqual(self.view.table.rowCount(), 2)

        # Press Down arrow to navigate to September (202609)
        self.view.folder_tree.keyPressEvent(key_down)
        self.assertEqual(self.view.active_folder_filter, "active_month_202609")
        self.assertEqual(self.view.table.rowCount(), 1)
        self.assertEqual(self.view.table.item(0, 1).text(), "2026-09-15")

        # Press Down arrow to navigate to August (202608)
        self.view.folder_tree.keyPressEvent(key_down)
        self.assertEqual(self.view.active_folder_filter, "active_month_202608")
        self.assertEqual(self.view.table.rowCount(), 1)
        self.assertEqual(self.view.table.item(0, 1).text(), "2026-08-10")

        # Press Up arrow to navigate back to September (202609)
        key_up = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Up, Qt.KeyboardModifier.NoModifier)
        self.view.folder_tree.keyPressEvent(key_up)
        self.assertEqual(self.view.active_folder_filter, "active_month_202609")
        self.assertEqual(self.view.table.rowCount(), 1)
        self.assertEqual(self.view.table.item(0, 1).text(), "2026-09-15")


class TestPlayCountView(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
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
        self.assertEqual(self.view.table.columnCount(), 6)
        status_item = self.view.table.item(0, 0)
        self.assertEqual(status_item.text(), "Clean")

    def _find_row_by_track_id(self, track_id: str) -> int:
        for r in range(self.view.table.rowCount()):
            if self.view.table.item(r, 0).data(Qt.ItemDataRole.UserRole) == track_id:
                return r
        raise ValueError(f"Track ID {track_id} not found in table")

    def test_stepper_and_delta_stages_changes(self):
        self.view._apply_track_delta("1", 1)
        row_idx = self._find_row_by_track_id("1")
        count_item = self.view.table.item(row_idx, 3)
        status_item = self.view.table.item(row_idx, 0)

        # UI updated
        self.assertEqual(count_item.text(), "6")
        self.assertEqual(status_item.text(), "● Staged")
        self.assertIn("1", self.view.staged_edits)
        self.assertEqual(self.view.staged_edits["1"]["new_count"], 6)
        self.assertTrue(self.view.save_btn.isEnabled())
        self.assertTrue(self.view.discard_btn.isEnabled())
        self.assertIn("1 track modified", self.view.status_label.text())

        # DB is untouched in staging phase
        with self.engine.connect() as conn:
            val = conn.exec_driver_sql("SELECT DJPlayCount FROM djmdContent WHERE ID = '1'").fetchone()[0]
            self.assertEqual(val, 5)

        # Applying delta back to original un-stages it
        self.view._apply_track_delta("1", -1)
        self.assertEqual(count_item.text(), "5")
        self.assertEqual(status_item.text(), "Clean")
        self.assertNotIn("1", self.view.staged_edits)
        self.assertFalse(self.view.save_btn.isEnabled())
        self.assertFalse(self.view.discard_btn.isEnabled())

    def test_discard_changes(self):
        self.view._apply_track_delta("1", 2)
        self.assertIn("1", self.view.staged_edits)

        with patch("PySide6.QtWidgets.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            self.view._on_discard_changes()

        row_idx = self._find_row_by_track_id("1")
        self.assertEqual(self.view.table.item(row_idx, 3).text(), "5")
        self.assertEqual(self.view.table.item(row_idx, 0).text(), "Clean")
        self.assertEqual(len(self.view.staged_edits), 0)
        self.assertFalse(self.view.save_btn.isEnabled())

    def test_save_all_changes(self):
        self.view._apply_track_delta("1", 3)
        self.assertEqual(self.view.staged_edits["1"]["new_count"], 8)

        with patch("rekordbox_collection_manager.gui.play_count_view.is_rekordbox_running", return_value=False), \
             patch("PySide6.QtWidgets.QMessageBox.question", return_value=QMessageBox.StandardButton.Save), \
             patch("PySide6.QtWidgets.QMessageBox.information"):
            self.view._on_save_success(1)

        self.assertEqual(len(self.view.staged_edits), 0)
        row_idx = self._find_row_by_track_id("1")
        self.assertEqual(self.view.table.item(row_idx, 3).text(), "8")
        self.assertEqual(self.view.table.item(row_idx, 0).text(), "Clean")

    def test_row_highlighting_persists_across_repopulate(self):
        self.view._apply_track_delta("1", 1)
        self.view._populate_table()
        row_idx = self._find_row_by_track_id("1")
        self.assertEqual(self.view.table.item(row_idx, 0).text(), "● Staged")
        self.assertEqual(self.view.table.item(row_idx, 3).text(), "6")

    def test_play_count_column_read_only(self):
        row_idx = self._find_row_by_track_id("1")
        count_item = self.view.table.item(row_idx, 3)
        self.assertFalse(bool(count_item.flags() & Qt.ItemFlag.ItemIsEditable))

    def _click_header_and_wait(self, col: int) -> None:
        self.view._on_header_section_clicked(col)
        if self.view.active_search_worker:
            self.view.active_search_worker.wait(2000)
            app.processEvents()

    def test_header_sort_by_play_count(self):
        # Initial: track 1 has 5 plays, track 2 has 0 plays
        # First click on Play Count (col 3) -> Descending (highest plays first)
        self._click_header_and_wait(3)
        self.assertEqual(self.view.table.item(0, 1).text(), "Titanium")
        self.assertEqual(self.view.table.item(0, 3).text(), "5")
        self.assertEqual(self.view.table.item(1, 1).text(), "One More Time")
        self.assertEqual(self.view.table.item(1, 3).text(), "0")

        # Second click -> Ascending (lowest plays first)
        self._click_header_and_wait(3)
        self.assertEqual(self.view.table.item(0, 1).text(), "One More Time")
        self.assertEqual(self.view.table.item(0, 3).text(), "0")
        self.assertEqual(self.view.table.item(1, 1).text(), "Titanium")
        self.assertEqual(self.view.table.item(1, 3).text(), "5")

    def test_header_sort_by_title_and_artist(self):
        # Click Title (col 1) -> Ascending (One More Time before Titanium)
        self._click_header_and_wait(1)
        self.assertEqual(self.view.table.item(0, 1).text(), "One More Time")
        self.assertEqual(self.view.table.item(1, 1).text(), "Titanium")

        # Click Title again -> Descending
        self._click_header_and_wait(1)
        self.assertEqual(self.view.table.item(0, 1).text(), "Titanium")
        self.assertEqual(self.view.table.item(1, 1).text(), "One More Time")

        # Click Artist (col 2) -> Ascending (Daft Punk before David Guetta)
        self._click_header_and_wait(2)
        self.assertEqual(self.view.table.item(0, 2).text(), "Daft Punk")
        self.assertEqual(self.view.table.item(1, 2).text(), "David Guetta")

    def test_header_sort_by_status(self):
        # Stage track 2
        self.view._apply_track_delta("2", 1)
        # Click Status (col 0) -> Staged first
        self._click_header_and_wait(0)
        self.assertEqual(self.view.table.item(0, 0).text(), "● Staged")
        self.assertEqual(self.view.table.item(0, 1).text(), "One More Time")
        self.assertEqual(self.view.table.item(1, 0).text(), "Clean")
        self.assertEqual(self.view.table.item(1, 1).text(), "Titanium")



class TestAssets(unittest.TestCase):
    def test_asset_path_resolution(self):
        from rekordbox_collection_manager.gui.assets import get_app_icon, get_asset_path

        icon_path = get_asset_path("icon.png")
        self.assertIsNotNone(icon_path)
        self.assertTrue(os.path.exists(icon_path))

        icon = get_app_icon()
        self.assertFalse(icon.isNull())

    def test_macos_metadata(self):
        from rekordbox_collection_manager.gui.assets import get_asset_path
        from rekordbox_collection_manager.gui.macos import (
            setup_macos_app_metadata,
            update_macos_menu_title,
        )

        # Should execute safely without throwing exceptions
        setup_macos_app_metadata(
            app_name="Rekordbox Collection Manager",
            icon_path=get_asset_path("icon.png"),
        )
        update_macos_menu_title("Rekordbox Collection Manager")


class TestMainWindowSidebar(unittest.TestCase):
    def setUp(self):
        from rekordbox_collection_manager.gui.main_window import MainWindow

        with patch.object(MainWindow, "load_database"), patch.object(MainWindow, "_check_rekordbox_status"):
            self.window = MainWindow(initial_db_path=None)

    def tearDown(self):
        self.window.safety_timer.stop()
        self.window.close()

    def test_sidebar_initial_state(self):
        self.assertFalse(self.window.is_sidebar_collapsed)
        self.assertEqual(self.window.sidebar_frame.width(), 200)
        self.assertEqual(self.window.btn_history.text(), "🕒  History Sessions")
        self.assertEqual(self.window.btn_tracks.text(), "🎵  Tracks")
        self.assertEqual(self.window.btn_play_count.text(), "🔢  Play Counts")
        self.assertEqual(self.window.btn_replace_file.text(), "🔄  Replace File")
        self.assertEqual(self.window.btn_info.text(), "ℹ️  Collection Info")
        self.assertFalse(self.window.sidebar_section_label.isHidden())
        self.assertFalse(self.window.app_ver.isHidden())
        self.assertEqual(self.window.btn_collapse_sidebar.text(), "◀")

    def test_sidebar_collapse(self):
        self.window.set_sidebar_collapsed(True, animate=False)
        self.assertTrue(self.window.is_sidebar_collapsed)
        self.assertEqual(self.window.sidebar_frame.width(), 60)
        self.assertEqual(self.window.btn_history.text(), "🕒")
        self.assertEqual(self.window.btn_tracks.text(), "🎵")
        self.assertEqual(self.window.btn_play_count.text(), "🔢")
        self.assertEqual(self.window.btn_replace_file.text(), "🔄")
        self.assertEqual(self.window.btn_info.text(), "ℹ️")
        self.assertTrue(self.window.sidebar_section_label.isHidden())
        self.assertTrue(self.window.app_ver.isHidden())
        self.assertEqual(self.window.btn_collapse_sidebar.text(), "▶")
        self.assertEqual(self.window.btn_history.toolTip(), "History Sessions")
        self.assertEqual(self.window.btn_tracks.toolTip(), "Tracks")
        self.assertEqual(self.window.btn_play_count.toolTip(), "Play Counts")
        self.assertEqual(self.window.btn_replace_file.toolTip(), "Replace File")
        self.assertEqual(self.window.btn_info.toolTip(), "Collection Info")

    def test_sidebar_expand(self):
        self.window.set_sidebar_collapsed(True, animate=False)
        self.window.set_sidebar_collapsed(False, animate=False)
        self.assertFalse(self.window.is_sidebar_collapsed)
        self.assertEqual(self.window.sidebar_frame.width(), 200)
        self.assertEqual(self.window.btn_history.text(), "🕒  History Sessions")
        self.assertEqual(self.window.btn_tracks.text(), "🎵  Tracks")
        self.assertFalse(self.window.sidebar_section_label.isHidden())
        self.assertFalse(self.window.app_ver.isHidden())
        self.assertEqual(self.window.btn_collapse_sidebar.text(), "◀")

    def test_sidebar_toggle(self):
        self.window.toggle_sidebar()
        self.assertTrue(self.window.is_sidebar_collapsed)
        self.window.toggle_sidebar()
        self.assertFalse(self.window.is_sidebar_collapsed)

    def test_nav_page_switching_while_collapsed(self):
        self.window.set_sidebar_collapsed(True, animate=False)
        self.assertEqual(self.window.stacked_widget.currentIndex(), 0)
        self.window.btn_tracks.click()
        self.assertEqual(self.window.stacked_widget.currentIndex(), 1)
        self.window.btn_play_count.click()
        self.assertEqual(self.window.stacked_widget.currentIndex(), 2)
        self.window.btn_replace_file.click()
        self.assertEqual(self.window.stacked_widget.currentIndex(), 3)
        self.window.btn_info.click()
        self.assertEqual(self.window.stacked_widget.currentIndex(), 4)

    def test_refresh_button_icon_and_action(self):
        from rekordbox_collection_manager.gui.assets import get_refresh_icon

        icon = get_refresh_icon()
        self.assertFalse(icon.isNull())
        self.assertIsNotNone(self.window.refresh_chip_btn)
        self.assertEqual(self.window.refresh_chip_btn.objectName(), "refreshChipBtn")
        self.assertFalse(self.window.refresh_chip_btn.icon().isNull())
        self.assertEqual(self.window.refresh_chip_btn.toolTip(), "Re-check Rekordbox running status")
        self.assertEqual(self.window.refresh_chip_btn.width(), 28)
        self.assertEqual(self.window.refresh_chip_btn.height(), 28)



class TestHistoryDetailView(unittest.TestCase):
    """Tests for the HistoryDetailView component."""

    def setUp(self):
        self.detail_view = HistoryDetailView()
        self.mock_data = {
            "history_id": "sess_101",
            "session": {
                "id": "sess_101",
                "name": "HISTORY 2026-08-14 @ Space Terrace",
                "date": "2026-08-14 18:30:00",
                "parent_id": "202608",
            },
            "tracks": [
                {
                    "track_no": 1,
                    "content_id": "c1",
                    "title": "Glue",
                    "artist": "Bicep",
                    "album": "Bicep",
                    "genre": "Breakbeat",
                    "key": "Am",
                    "bpm": 130.0,
                    "duration_sec": 269,
                    "duration_str": "4:29",
                    "play_count": 12,
                    "rating": 5,
                    "year": 2017,
                    "bitrate": 320,
                    "comment": "",
                    "folder_path": "/Music/bicep-glue.mp3",
                    "filename": "bicep-glue.mp3",
                    "played_at": "2026-08-14 18:35:00",
                },
                {
                    "track_no": 2,
                    "content_id": "c2",
                    "title": "Opal (Four Tet Remix)",
                    "artist": "Bicep",
                    "album": "Opal",
                    "genre": "Electronic",
                    "key": "Fm",
                    "bpm": 127.0,
                    "duration_sec": 494,
                    "duration_str": "8:14",
                    "play_count": 7,
                    "rating": 4,
                    "year": 2018,
                    "bitrate": 320,
                    "comment": "",
                    "folder_path": "/Music/opal.mp3",
                    "filename": "opal.mp3",
                    "played_at": "2026-08-14 18:40:00",
                },
            ],
            "stats": {
                "track_count": 2,
                "total_duration_sec": 763,
                "total_duration_str": "12m 43s",
                "bpm_min": 127.0,
                "bpm_max": 130.0,
                "bpm_avg": 128.5,
                "top_genres": [("Breakbeat", 1), ("Electronic", 1)],
                "top_keys": [("Am", 1), ("Fm", 1)],
            },
        }

    def test_detail_view_rendering(self):
        self.detail_view._on_data_loaded(self.mock_data)

        self.assertEqual(self.detail_view.session_title_label.text(), "HISTORY 2026-08-14")
        self.assertEqual(self.detail_view.location_val_label.text(), "Space Terrace")
        self.assertFalse(self.detail_view.location_chip.isHidden())
        self.assertFalse(self.detail_view.djs_chip.isHidden())
        self.assertEqual(self.detail_view.djs_container.get_djs(), [])
        self.assertEqual(self.detail_view.kpi_tracks.val_lbl.text(), "2 tracks")
        self.assertEqual(self.detail_view.kpi_runtime.val_lbl.text(), "12m 43s")
        self.assertIn("127.0", self.detail_view.kpi_bpm.val_lbl.text())
        self.assertIn("130.0", self.detail_view.kpi_bpm.val_lbl.text())
        self.assertEqual(self.detail_view.table.rowCount(), 2)
        self.assertEqual(self.detail_view.table.columnCount(), 14)

        # Check column 1 (Title)
        self.assertEqual(self.detail_view.table.item(0, 1).text(), "Glue")
        self.assertEqual(self.detail_view.table.item(1, 1).text(), "Opal (Four Tet Remix)")

        # Check column 2 (Artist)
        self.assertEqual(self.detail_view.table.item(0, 2).text(), "Bicep")

        # Check column 4 (Key)
        self.assertEqual(self.detail_view.table.item(0, 4).text(), "Am")
        self.assertEqual(self.detail_view.table.item(1, 4).text(), "Fm")

        # Check column 8 (Bitrate)
        self.assertEqual(self.detail_view.table.item(0, 8).text(), "320 kbps")

    def test_detail_view_filtering(self):
        self.detail_view._on_data_loaded(self.mock_data)
        self.assertEqual(self.detail_view.table.rowCount(), 2)

        # Filter for "Glue"
        self.detail_view.search_input.setText("Glue")
        self.assertEqual(self.detail_view.table.rowCount(), 1)
        self.assertEqual(self.detail_view.table.item(0, 1).text(), "Glue")

        # Clear filter
        self.detail_view.search_input.setText("")
        self.assertEqual(self.detail_view.table.rowCount(), 2)

    def test_detail_view_copy_tracklist(self):
        self.detail_view._on_data_loaded(self.mock_data)

        # Call copy tracklist
        self.detail_view._on_copy_tracklist()
        clipboard = QApplication.clipboard()
        if clipboard:
            text = clipboard.text()
            self.assertIn("01. Bicep - Glue", text)
            self.assertIn("02. Bicep - Opal (Four Tet Remix)", text)

    def test_detail_view_back_button(self):
        back_called = False

        def on_back():
            nonlocal back_called
            back_called = True

        self.detail_view.back_clicked.connect(on_back)
        self.detail_view.back_btn.click()
        self.assertTrue(back_called)

    def test_detail_view_djs_chips_rendering(self):
        data = dict(self.mock_data)
        data["session"] = {
            "id": "sess_102",
            "name": "HISTORY 2026-08-14 @ Space Terrace w Carl Cox, Marco Carola",
            "date": "2026-08-14 18:30:00",
            "parent_id": "202608",
        }
        self.detail_view._on_data_loaded(data)

        self.assertFalse(self.detail_view.djs_chip.isHidden())
        self.assertEqual(len(self.detail_view.djs_chips), 2)
        chip_names = [c.name for c in self.detail_view.djs_chips]
        self.assertEqual(chip_names, ["Carl Cox", "Marco Carola"])
        self.assertEqual(self.detail_view.djs_val_label.text(), "Carl Cox, Marco Carola")

    def test_detail_view_inline_location_editing(self):
        """Test adding, editing, and clearing location in HistoryDetailView."""
        self.detail_view.history_id = "sess_101"
        self.detail_view._on_data_loaded(self.mock_data)

        # Initially location is "Space Terrace"
        self.assertEqual(self.detail_view.current_location, "Space Terrace")
        self.assertTrue(self.detail_view.bottom_bar.isHidden())

        # Start inline edit
        self.detail_view._start_location_edit()
        self.assertFalse(self.detail_view.location_edit_widget.isHidden())
        self.assertEqual(self.detail_view.location_input.text(), "Space Terrace")

        # Change location to "Amnesia"
        self.detail_view.location_input.setText("Amnesia")
        self.detail_view._commit_location_edit()

        self.assertEqual(self.detail_view.current_location, "Amnesia")
        self.assertEqual(self.detail_view.location_val_label.text(), "Amnesia")
        self.assertFalse(self.detail_view.bottom_bar.isHidden())

        # Discard changes reverts back to original "Space Terrace"
        self.detail_view._on_discard_clicked()
        self.assertEqual(self.detail_view.current_location, "Space Terrace")
        self.assertEqual(self.detail_view.location_val_label.text(), "Space Terrace")
        self.assertTrue(self.detail_view.bottom_bar.isHidden())

        # Clear location
        self.detail_view._clear_location()
        self.assertEqual(self.detail_view.current_location, "")
        self.assertFalse(self.detail_view.location_add_btn.isHidden())
        self.assertFalse(self.detail_view.bottom_bar.isHidden())

    def test_detail_view_djs_addition_and_removal(self):
        """Test adding and removing DJ chips directly in HistoryDetailView."""
        self.detail_view.history_id = "sess_101"
        self.detail_view._on_data_loaded(self.mock_data)

        # Initially no DJs
        self.assertEqual(self.detail_view.djs_container.get_djs(), [])
        self.assertTrue(self.detail_view.bottom_bar.isHidden())

        # Add a DJ
        self.detail_view._on_djs_changed(["Carl Cox"])
        self.assertEqual(self.detail_view.current_artist, "Carl Cox")
        self.assertFalse(self.detail_view.bottom_bar.isHidden())

        # Add second DJ
        self.detail_view.djs_container.set_djs(["Carl Cox", "Solomun"])
        self.detail_view._on_djs_changed(["Carl Cox", "Solomun"])
        self.assertEqual(self.detail_view.current_artist, "Carl Cox, Solomun")
        self.assertEqual(len(self.detail_view.djs_chips), 2)

        # Discard reverts back to empty
        self.detail_view._on_discard_clicked()
        self.assertEqual(self.detail_view.current_artist, "")
        self.assertEqual(self.detail_view.djs_container.get_djs(), [])
        self.assertTrue(self.detail_view.bottom_bar.isHidden())

    def test_detail_view_inline_title_editing(self):
        """Test double-click renaming of session title in HistoryDetailView."""
        self.detail_view.history_id = "sess_101"
        self.detail_view._on_data_loaded(self.mock_data)

        # Verify initial display, cursor, and tooltip
        self.assertEqual(self.detail_view.session_title_label.text(), "HISTORY 2026-08-14")
        self.assertEqual(self.detail_view.session_title_label.cursor().shape(), Qt.CursorShape.PointingHandCursor)
        self.assertEqual(self.detail_view.session_title_label.toolTip(), "Double-click to rename session")
        self.assertTrue(self.detail_view.title_edit_widget.isHidden())

        # Double-click triggers inline editing
        self.detail_view._on_title_double_clicked(None)
        self.assertFalse(self.detail_view.title_edit_widget.isHidden())
        self.assertTrue(self.detail_view.session_title_label.isHidden())
        self.assertEqual(self.detail_view.title_input.text(), "HISTORY 2026-08-14")

        # Cancel reverts without changing
        self.detail_view._cancel_title_edit()
        self.assertTrue(self.detail_view.title_edit_widget.isHidden())
        self.assertFalse(self.detail_view.session_title_label.isHidden())
        self.assertEqual(self.detail_view.session_title_label.text(), "HISTORY 2026-08-14")
        self.assertTrue(self.detail_view.bottom_bar.isHidden())

        # Edit and commit a new name
        self.detail_view._start_title_edit()
        self.detail_view.title_input.setText("Summer Closing Party")
        self.detail_view._commit_title_edit()

        self.assertTrue(self.detail_view.title_edit_widget.isHidden())
        self.assertFalse(self.detail_view.session_title_label.isHidden())
        self.assertEqual(self.detail_view.base_name, "Summer Closing Party")
        self.assertEqual(self.detail_view.session_title_label.text(), "Summer Closing Party")
        self.assertFalse(self.detail_view.bottom_bar.isHidden())
        self.assertIn("Summer Closing Party", self.detail_view.staged_status_label.text())

        # Discard changes reverts back to original
        self.detail_view._on_discard_clicked()
        self.assertEqual(self.detail_view.base_name, "HISTORY 2026-08-14")
        self.assertEqual(self.detail_view.session_title_label.text(), "HISTORY 2026-08-14")
        self.assertTrue(self.detail_view.bottom_bar.isHidden())

        # Empty name submission does not overwrite
        self.detail_view._start_title_edit()
        self.detail_view.title_input.setText("   ")
        self.detail_view._commit_title_edit()
        self.assertEqual(self.detail_view.base_name, "HISTORY 2026-08-14")
        self.assertEqual(self.detail_view.session_title_label.text(), "HISTORY 2026-08-14")

        # When session is deleted, double-click does not activate edit mode
        self.detail_view.is_deleted = True
        self.detail_view._render_title()
        self.assertEqual(self.detail_view.session_title_label.cursor().shape(), Qt.CursorShape.ArrowCursor)
        self.assertEqual(self.detail_view.session_title_label.toolTip(), "")
        self.detail_view._on_title_double_clicked(None)
        self.assertTrue(self.detail_view.title_edit_widget.isHidden())


class TestHistoryViewDetailIntegration(unittest.TestCase):
    """Tests for HistoryView table actions and detail view integration."""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        with self.engine.begin() as conn:
            conn.exec_driver_sql("""
                CREATE TABLE djmdHistory (
                    ID VARCHAR(255) PRIMARY KEY,
                    Seq INTEGER,
                    Name VARCHAR(255),
                    Attribute INTEGER,
                    ParentID VARCHAR(255),
                    DateCreated VARCHAR(255),
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdHistory VALUES 
                    ('h1', 1, 'HISTORY 2026-08-10', 0, '202608', '2026-08-10 12:00:00', 0),
                    ('h2', 2, 'HISTORY 2026-08-20', 0, '202608', '2026-08-20 12:00:00', 0);
            """)

        self.records = [
            {"id": "h1", "name": "HISTORY 2026-08-10", "date": "2026-08-10 12:00:00", "parent_id": "202608", "table": "djmdHistory"},
            {"id": "h2", "name": "HISTORY 2026-08-20", "date": "2026-08-20 12:00:00", "parent_id": "202608", "table": "djmdHistory"},
        ]
        self.history_view = HistoryView()
        self.patcher = patch.object(self.history_view.detail_view, "load_session")
        self.patcher.start()
        self.history_view.set_data(self.engine, self.records)

    def tearDown(self):
        self.patcher.stop()

    def test_table_has_seven_columns_including_location_and_played_with(self):
        self.assertEqual(self.history_view.table.columnCount(), 7)
        self.assertEqual(self.history_view.table.horizontalHeaderItem(2).text(), "Session Name")
        self.assertEqual(self.history_view.table.horizontalHeaderItem(3).text(), "Location")
        self.assertEqual(self.history_view.table.horizontalHeaderItem(4).text(), "Played With")
        self.assertEqual(self.history_view.table.horizontalHeaderItem(6).text(), "Actions")
        self.assertEqual(self.history_view.table.rowCount(), 2)

    def test_open_session_detail_and_back(self):
        # Initial page is 0 (Sessions list)
        self.assertEqual(self.history_view.stack.currentIndex(), 0)

        # Open detail for h1
        self.history_view._open_session_detail("h1")
        self.assertEqual(self.history_view.stack.currentIndex(), 1)
        self.assertEqual(self.history_view.current_detail_id, "h1")

        # Go back to sessions
        self.history_view._on_back_from_detail()
        self.assertEqual(self.history_view.stack.currentIndex(), 0)
        self.assertIsNone(self.history_view.current_detail_id)

    def test_double_click_row_opens_detail(self):
        # Double clicking column 0 (Status) should open detail view
        self.assertEqual(self.history_view.stack.currentIndex(), 0)
        self.history_view._on_cell_double_clicked(0, 0)
        self.assertEqual(self.history_view.stack.currentIndex(), 1)
        self.assertEqual(self.history_view.current_detail_id, "h1")

    def test_prev_next_session_navigation(self):
        self.history_view._open_session_detail("h1")
        self.assertEqual(self.history_view.current_detail_id, "h1")

        # Next session
        self.history_view._on_next_session()
        self.assertEqual(self.history_view.current_detail_id, "h2")

        # Prev session
        self.history_view._on_prev_session()
        self.assertEqual(self.history_view.current_detail_id, "h1")

    def test_history_view_chips_displayed_and_staged(self):
        records = [
            {
                "id": "h_test",
                "name": "HISTORY 2026-08-10 @ Fabric w Carl Cox, Marco Carola",
                "date": "2026-08-10 12:00:00",
                "parent_id": "202608",
                "table": "djmdHistory",
            },
        ]
        self.history_view.set_data(self.engine, records)
        self.assertEqual(self.history_view.table.rowCount(), 1)

        container = self.history_view.table.cellWidget(0, 4)
        self.assertIsInstance(container, DJChipsContainer)
        self.assertEqual(container.get_djs(), ["Carl Cox", "Marco Carola"])
        self.assertEqual(len(container.chips), 2)

        # Remove "Carl Cox" chip
        container._on_chip_delete("Carl Cox")
        self.assertEqual(container.get_djs(), ["Marco Carola"])
        self.assertIn("h_test", self.history_view.staged_edits)
        self.assertEqual(
            self.history_view.staged_edits["h_test"]["name"],
            "HISTORY 2026-08-10 @ Fabric w Marco Carola",
        )
        self.assertTrue(self.history_view.save_btn.isEnabled())

        # Add new custom DJ "Seth Troxler"
        container.input_edit.setText("Seth Troxler")
        container._on_input_return_pressed()
        self.assertEqual(container.get_djs(), ["Marco Carola", "Seth Troxler"])
        self.assertEqual(
            self.history_view.staged_edits["h_test"]["name"],
            "HISTORY 2026-08-10 @ Fabric w Marco Carola, Seth Troxler",
        )
        self.assertIn("Seth Troxler", self.history_view.all_available_djs)

    def test_dj_chips_container_custom_and_autocomplete(self):
        container = DJChipsContainer()
        container.set_available_djs(["Carl Cox", "Marco Carola"])
        container.set_djs(["Carl Cox"])
        self.assertEqual(container.get_djs(), ["Carl Cox"])

        # Autocomplete candidates exclude "Carl Cox"
        container.show_add_input()
        candidates = container.completer_model.stringList()
        self.assertIn("Marco Carola", candidates)
        self.assertNotIn("Carl Cox", candidates)

        # Type custom DJ
        container.input_edit.setText("The Martinez Brothers")
        container._on_input_return_pressed()
        self.assertEqual(container.get_djs(), ["Carl Cox", "The Martinez Brothers"])
        self.assertEqual(container.chips[1].scheme_name, "amber")

    def test_location_pool_and_case_normalization(self):
        records = [
            {
                "id": "h_loc1",
                "name": "HISTORY 2026-08-10 @ Fabric w Carl Cox",
                "date": "2026-08-10 12:00:00",
                "parent_id": "202608",
                "table": "djmdHistory",
            },
            {
                "id": "h_loc2",
                "name": "HISTORY 2026-08-15 @ Amnesia Ibiza",
                "date": "2026-08-15 12:00:00",
                "parent_id": "202608",
                "table": "djmdHistory",
            },
        ]
        self.history_view.set_data(self.engine, records)
        self.assertEqual(
            self.history_view.get_all_available_locations(),
            ["Amnesia Ibiza", "Fabric"],
        )
        self.assertIn("Fabric", self.history_view.all_available_locations)
        self.assertIn("Amnesia Ibiza", self.history_view.all_available_locations)

        # Delegate is installed on column 3
        delegate = self.history_view.table.itemDelegateForColumn(3)
        self.assertIsInstance(delegate, LocationDelegate)

        # Show view and open editor via table.edit
        self.history_view.show()
        self.history_view.table.edit(self.history_view.table.model().index(0, 3))
        app.processEvents()

        editor = next(
            (c for c in self.history_view.table.findChildren(LocationLineEdit) if c.isVisible()),
            None,
        )
        self.assertIsNotNone(editor)
        self.assertIsNotNone(editor.completer())
        candidates = editor.completer().model().stringList()
        self.assertEqual(candidates, ["Amnesia Ibiza", "Fabric"])

        # Test completer activation on editor commits location
        editor.completer().activated.emit("Amnesia Ibiza")
        app.processEvents()
        self.assertEqual(self.history_view.table.item(0, 3).text(), "Amnesia Ibiza")
        self.assertIn("h_loc1", self.history_view.staged_edits)
        self.assertEqual(
            self.history_view.staged_edits["h_loc1"]["name"],
            "HISTORY 2026-08-10 @ Amnesia Ibiza w Carl Cox",
        )

    def test_location_custom_entry_and_registration(self):
        records = [
            {
                "id": "h_loc1",
                "name": "HISTORY 2026-08-10 @ Fabric",
                "date": "2026-08-10 12:00:00",
                "parent_id": "202608",
                "table": "djmdHistory",
            },
        ]
        self.history_view.set_data(self.engine, records)
        delegate = self.history_view.table.itemDelegateForColumn(3)

        editor = delegate.createEditor(
            self.history_view.table,
            None,
            self.history_view.table.model().index(0, 3),
        )
        # Type custom new location and setModelData
        editor.setText("Printworks London")
        delegate.setModelData(
            editor,
            self.history_view.table.model(),
            self.history_view.table.model().index(0, 3),
        )
        self.assertEqual(self.history_view.table.item(0, 3).text(), "Printworks London")
        self.assertIn("Printworks London", self.history_view.all_available_locations)
        self.assertIn("Printworks London", self.history_view.get_all_available_locations())
        self.assertEqual(
            self.history_view.staged_edits["h_loc1"]["name"],
            "HISTORY 2026-08-10 @ Printworks London",
        )

        # Test case normalization: typing lowercase "fabric" normalizes to existing "Fabric"
        editor.setText("fabric")
        delegate.setModelData(
            editor,
            self.history_view.table.model(),
            self.history_view.table.model().index(0, 3),
        )
        self.assertEqual(self.history_view.table.item(0, 3).text(), "Fabric")

        # Discarding changes rebuilds location pool back to original records
        self.history_view.staged_edits["dummy"] = {"name": "HISTORY @ BrandNewLoc"}
        self.history_view._rebuild_all_available_locations()
        self.assertIn("BrandNewLoc", self.history_view.all_available_locations)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.history_view._on_discard_changes()
        self.assertNotIn("BrandNewLoc", self.history_view.all_available_locations)

    def test_detail_view_metadata_updates_stage_in_history_view(self):
        """Test that edits made in HistoryDetailView stage and synchronize with HistoryView."""
        self.history_view._open_session_detail("h1")
        self.assertEqual(self.history_view.current_detail_id, "h1")
        # In this test suite setUp, load_session was mocked with patcher, so set history_id manually
        self.history_view.detail_view.history_id = "h1"
        self.history_view.detail_view.original_location = ""
        self.history_view.detail_view.original_artist = ""
        self.assertFalse(self.history_view.detail_view.location_chip.isHidden())
        self.assertFalse(self.history_view.detail_view.djs_chip.isHidden())

        # Initially h1 is not staged
        self.assertNotIn("h1", self.history_view.staged_edits)

        # In detail view, set location to "DC10" and DJ to "Seth Troxler"
        self.history_view.detail_view.location_input.setText("DC10")
        self.history_view.detail_view._commit_location_edit()
        self.history_view.detail_view._on_djs_changed(["Seth Troxler"])

        # Staged edits should be updated in HistoryView
        self.assertIn("h1", self.history_view.staged_edits)
        self.assertEqual(
            self.history_view.staged_edits["h1"]["name"],
            "HISTORY 2026-08-10 @ DC10 w Seth Troxler",
        )

        # Table row for h1 should be updated and marked as Staged
        self.assertEqual(self.history_view.table.item(0, 3).text(), "DC10")
        self.assertEqual(self.history_view.table.item(0, 4).text(), "Seth Troxler")
        self.assertEqual(self.history_view.table.item(0, 0).text(), "● Staged")

        # In detail view, also rename session base name
        self.history_view.detail_view._start_title_edit()
        self.history_view.detail_view.title_input.setText("Opening Festival Set")
        self.history_view.detail_view._commit_title_edit()

        # Staged edits should be updated in HistoryView with new base name
        self.assertIn("h1", self.history_view.staged_edits)
        self.assertEqual(
            self.history_view.staged_edits["h1"]["name"],
            "Opening Festival Set @ DC10 w Seth Troxler",
        )
        self.assertEqual(self.history_view.table.item(0, 2).text(), "Opening Festival Set")

        # Discard from detail view
        self.history_view.detail_view._on_discard_clicked()
        self.assertNotIn("h1", self.history_view.staged_edits)
        self.assertEqual(self.history_view.table.item(0, 0).text(), "Clean")
        self.assertEqual(self.history_view.table.item(0, 2).text(), "HISTORY 2026-08-10")


class TestTracksView(unittest.TestCase):
    """Unit tests for the TracksView component."""

    def setUp(self):
        self.view = TracksView()
        self.mock_records = [
            {
                "id": "1",
                "content_id": "1",
                "title": "Titanium",
                "artist": "David Guetta",
                "album": "Nothing But the Beat",
                "genre": "Dance",
                "key": "8A",
                "bpm": 126.0,
                "duration_sec": 245,
                "duration_str": "4:05",
                "play_count": 5,
                "rating": 5,
                "year": 2011,
                "bitrate": 320,
                "file_type": "MP3",
                "tags": ["Electro", "Club"],
                "playlists": ["Mainstage - BP", "Festival"],
                "folder_name": "Music",
                "folder_dir": "/Music",
                "file_name": "Titanium.mp3",
                "file_path": "/Music/Titanium.mp3",
                "folder_path": "/Music/Titanium.mp3",
                "filename": "Titanium.mp3",
                "location_display": "Music/Titanium.mp3",
            },
            {
                "id": "2",
                "content_id": "2",
                "title": "One More Time",
                "artist": "Daft Punk",
                "album": "Discovery",
                "genre": "French House",
                "key": "11B",
                "bpm": 123.0,
                "duration_sec": 320,
                "duration_str": "5:20",
                "play_count": 0,
                "rating": 4,
                "year": 2001,
                "bitrate": 320,
                "file_type": "AIFF",
                "tags": ["French Touch"],
                "playlists": [],
                "folder_name": "Music",
                "folder_dir": "/Music",
                "file_name": "OneMoreTime.aiff",
                "file_path": "/Music/OneMoreTime.aiff",
                "comment": "",
                "folder_path": "/Music/OneMoreTime.aiff",
                "filename": "OneMoreTime.aiff",
                "location_display": "Music/OneMoreTime.aiff",
            },
        ]

    def test_tracks_view_initial_ui(self):
        self.assertEqual(self.view.tracks_header_label.text(), "TRACKS")
        self.assertEqual(self.view.table.columnCount(), 13)
        expected_headers = [
            "#",
            "Title",
            "Artist",
            "Key",
            "BPM",
            "Time",
            "Plays",
            "Bitrate",
            "Type",
            "Tags",
            "Playlists",
            "Folder",
            "File Name",
        ]
        for idx, text in enumerate(expected_headers):
            self.assertEqual(self.view.table.horizontalHeaderItem(idx).text(), text)

        self.assertIsNotNone(self.view.table.itemDelegateForColumn(8))
        self.assertIsNotNone(self.view.table.itemDelegateForColumn(9))
        self.assertIsNotNone(self.view.table.itemDelegateForColumn(10))
        self.assertIsNotNone(self.view.table.itemDelegateForColumn(11))
        self.assertIsNotNone(self.view.table.itemDelegateForColumn(12))

        self.assertEqual(self.view.filter_combo.count(), 3)
        self.assertEqual(self.view.filter_combo.itemText(0), "All Tracks")
        self.assertEqual(self.view.filter_combo.itemText(1), "Played Tracks (> 0)")
        self.assertEqual(self.view.filter_combo.itemText(2), "Unplayed (0)")
        self.assertEqual(self.view.limit_combo.currentIndex(), 1)  # Default: Limit 100

    def test_tracks_view_population_and_read_only(self):
        self.view.records = list(self.mock_records)
        self.view._populate_table()

        self.assertEqual(self.view.table.rowCount(), 2)

        # Check Row 0
        self.assertEqual(self.view.table.item(0, 0).text(), "1")
        self.assertEqual(self.view.table.item(0, 1).text(), "Titanium")
        self.assertEqual(self.view.table.item(0, 2).text(), "David Guetta")
        self.assertEqual(self.view.table.item(0, 3).text(), "8A")
        self.assertEqual(self.view.table.item(0, 4).text(), "126.0")
        self.assertEqual(self.view.table.item(0, 5).text(), "4:05")
        self.assertEqual(self.view.table.item(0, 6).text(), "5")
        self.assertEqual(self.view.table.item(0, 7).text(), "320 kbps")
        self.assertEqual(self.view.table.item(0, 8).text(), "MP3")
        self.assertEqual(self.view.table.item(0, 9).text(), "Electro, Club")
        self.assertEqual(self.view.table.item(0, 9).data(Qt.ItemDataRole.UserRole), ["Electro", "Club"])
        self.assertEqual(self.view.table.item(0, 10).text(), "Mainstage - BP, Festival")
        self.assertEqual(self.view.table.item(0, 10).data(Qt.ItemDataRole.UserRole), ["Mainstage - BP", "Festival"])
        self.assertEqual(self.view.table.item(0, 11).text(), "Music")
        self.assertEqual(self.view.table.item(0, 11).data(Qt.ItemDataRole.UserRole), "/Music")
        self.assertEqual(self.view.table.item(0, 12).text(), "Titanium.mp3")
        self.assertEqual(self.view.table.item(0, 12).data(Qt.ItemDataRole.UserRole), "/Music/Titanium.mp3")

        # Verify all items in Row 0 are read-only (not editable)
        for col in range(13):
            item = self.view.table.item(0, col)
            self.assertIsNotNone(item)
            self.assertFalse(item.flags() & Qt.ItemFlag.ItemIsEditable)

        # Check Row 1
        self.assertEqual(self.view.table.item(1, 0).text(), "2")
        self.assertEqual(self.view.table.item(1, 1).text(), "One More Time")
        self.assertEqual(self.view.table.item(1, 3).text(), "11B")
        self.assertEqual(self.view.table.item(1, 4).text(), "123.0")
        self.assertEqual(self.view.table.item(1, 6).text(), "0")
        self.assertEqual(self.view.table.item(1, 8).text(), "AIFF")
        self.assertEqual(self.view.table.item(1, 9).text(), "French Touch")
        self.assertEqual(self.view.table.item(1, 10).text(), "—")
        self.assertEqual(self.view.table.item(1, 11).text(), "Music")
        self.assertEqual(self.view.table.item(1, 12).text(), "OneMoreTime.aiff")

    def test_tracks_view_search_success_updates_header_and_table(self):
        self.view._on_search_success("test", self.mock_records)

        self.assertEqual(self.view.tracks_header_label.text(), "TRACKS (2)")
        self.assertEqual(self.view.table.rowCount(), 2)
        self.assertIn("Showing 2 tracks matching 'test'", self.view.status_label.text())

    def test_tracks_view_copy_tracklist(self):
        self.view.records = list(self.mock_records)
        self.view._on_copy_tracklist()

        clipboard = QApplication.clipboard()
        self.assertIsNotNone(clipboard)
        text = clipboard.text()
        self.assertIn("01. David Guetta - Titanium", text)
        self.assertIn("02. Daft Punk - One More Time", text)

    def test_tracks_view_folder_open_and_file_reveal_handlers(self):
        with patch("rekordbox_collection_manager.gui.tracks_view.open_in_folder", return_value=True) as mock_open:
            self.view._on_open_folder("/Music")
            mock_open.assert_called_once_with("/Music")
            self.assertIn("Opened folder", self.view.status_label.text())

        with patch("rekordbox_collection_manager.gui.tracks_view.reveal_in_file_manager", return_value=True) as mock_reveal:
            self.view._on_reveal_file("/Music/Titanium.mp3")
            mock_reveal.assert_called_once_with("/Music/Titanium.mp3")
            self.assertIn("Revealed in Finder", self.view.status_label.text())



class TestMainWindowDbBadge(unittest.TestCase):
    def setUp(self):
        from rekordbox_collection_manager.gui.main_window import MainWindow

        with patch.object(MainWindow, "load_database"), patch.object(MainWindow, "_check_rekordbox_status"):
            self.window = MainWindow(initial_db_path=None)

    def tearDown(self):
        self.window.safety_timer.stop()
        self.window.close()

    def test_db_badge_initialization(self):
        self.assertIsNotNone(self.window.db_badge)
        self.assertEqual(self.window.db_badge.objectName(), "dbBadge")
        self.assertEqual(self.window.db_badge.height(), 28)
        self.assertEqual(self.window.db_badge.cursor().shape(), Qt.CursorShape.ArrowCursor)
        self.assertIsNotNone(self.window.db_icon_label.pixmap())
        self.assertIn("Resolving", self.window.db_label.text())

    def test_db_badge_loaded_state(self):
        engine = create_engine("sqlite:///:memory:")
        self.window._on_db_loaded(
            engine=engine,
            path="/path/to/rekordbox/master.db",
            history_records=[],
            total_tracks=0,
            table_format="djmdHistory",
        )
        self.assertIn("master.db", self.window.db_label.text())
        self.assertIn("rekordbox/", self.window.db_label.text())
        self.assertIn("/path/to/rekordbox/master.db", self.window.db_badge.toolTip())

    def test_db_badge_error_state(self):
        with patch.object(QMessageBox, "critical"):
            self.window._on_db_error("Connection failed")
        self.assertIn("DB Error", self.window.db_label.text())
        self.assertIn("Connection failed", self.window.db_badge.toolTip())

    def test_database_pixmap_and_icon_generators(self):
        from rekordbox_collection_manager.gui.assets import get_database_icon, get_database_pixmap

        pix = get_database_pixmap("#00a2ff", 14)
        self.assertFalse(pix.isNull())
        icon = get_database_icon("#00a2ff")
        self.assertFalse(icon.isNull())


class TestTracksTableWidget(unittest.TestCase):
    """Unit tests for the shared customizable TracksTableWidget."""

    def setUp(self):
        from rekordbox_collection_manager.gui.track_table import (
            DEFAULT_COLLECTION_COLUMNS,
            DEFAULT_HISTORY_COLUMNS,
            TrackColumn,
            TrackTableConfig,
            TracksTableWidget,
        )

        self.mock_tracks = [
            {
                "track_no": 10,
                "content_id": "c1",
                "title": "Breathe",
                "artist": "CamelPhat",
                "album": "Dark Matter",
                "genre": "Melodic House",
                "key": "4A",
                "bpm": 125.0,
                "duration_sec": 195,
                "duration_str": "3:15",
                "play_count": 8,
                "bitrate": 320,
                "file_type": "FLAC",
                "tags": ["Melodic", "Peak"],
                "playlists": ["Festival 2024"],
                "folder_name": "Melodic",
                "folder_dir": "/Music/Melodic",
                "file_name": "Breathe.flac",
                "file_path": "/Music/Melodic/Breathe.flac",
            },
            {
                "track_no": 11,
                "content_id": "c2",
                "title": "Glue",
                "artist": "Bicep",
                "album": "Bicep",
                "genre": "Breakbeat",
                "key": "Am",
                "bpm": 130.0,
                "duration_sec": 269,
                "duration_str": "4:29",
                "play_count": 3,
                "bitrate": 320,
                "file_type": "MP3",
                "tags": [],
                "playlists": [],
                "folder_name": "Breaks",
                "folder_dir": "/Music/Breaks",
                "file_name": "Glue.mp3",
                "file_path": "/Music/Breaks/Glue.mp3",
            },
        ]

    def test_table_collection_mode(self):
        from rekordbox_collection_manager.gui.track_table import (
            DEFAULT_COLLECTION_COLUMNS,
            TrackTableConfig,
            TracksTableWidget,
        )

        cfg = TrackTableConfig(
            columns=list(DEFAULT_COLLECTION_COLUMNS),
            index_mode="row_number",
            sorting_mode="server",
        )
        table = TracksTableWidget(config=cfg)
        self.assertEqual(table.columnCount(), 13)
        self.assertFalse(table.isSortingEnabled())

        table.set_tracks(self.mock_tracks)
        self.assertEqual(table.rowCount(), 2)
        # Row index mode: 1 and 2
        self.assertEqual(table.item(0, 0).text(), "1")
        self.assertEqual(table.item(1, 0).text(), "2")
        self.assertEqual(table.item(0, 1).text(), "Breathe")
        self.assertEqual(table.item(0, 2).text(), "CamelPhat")

    def test_table_history_mode(self):
        from rekordbox_collection_manager.gui.track_table import (
            DEFAULT_HISTORY_COLUMNS,
            TrackTableConfig,
            TracksTableWidget,
        )

        cfg = TrackTableConfig(
            columns=list(DEFAULT_HISTORY_COLUMNS),
            index_mode="track_no",
            sorting_mode="client",
        )
        table = TracksTableWidget(config=cfg)
        self.assertEqual(table.columnCount(), 14)
        self.assertTrue(table.isSortingEnabled())

        table.set_tracks(self.mock_tracks)
        self.assertEqual(table.rowCount(), 2)
        # Track sequence mode: 10 and 11
        self.assertEqual(table.item(0, 0).text(), "10")
        self.assertEqual(table.item(1, 0).text(), "11")
        # Column 3 is Album in history preset
        self.assertEqual(table.item(0, 3).text(), "Dark Matter")
        self.assertEqual(table.item(1, 3).text(), "Bicep")

    def test_table_sorting_signal_emission_in_server_mode(self):
        from rekordbox_collection_manager.gui.track_table import (
            DEFAULT_COLLECTION_COLUMNS,
            TrackTableConfig,
            TracksTableWidget,
        )

        cfg = TrackTableConfig(
            columns=list(DEFAULT_COLLECTION_COLUMNS),
            sorting_mode="server",
        )
        table = TracksTableWidget(config=cfg)

        received_sorts = []
        table.sort_requested.connect(lambda field, order: received_sorts.append((field, order)))

        # Simulate clicking BPM column (index 4 in collection columns)
        table._on_header_section_clicked(4)
        self.assertEqual(len(received_sorts), 1)
        self.assertEqual(received_sorts[0][0], "bpm")
        self.assertEqual(received_sorts[0][1], Qt.SortOrder.DescendingOrder)

    def test_table_copy_tracklist_formatting(self):
        from rekordbox_collection_manager.gui.track_table import (
            DEFAULT_COLLECTION_COLUMNS,
            TrackTableConfig,
            TracksTableWidget,
        )

        table = TracksTableWidget(config=TrackTableConfig(columns=list(DEFAULT_COLLECTION_COLUMNS)))
        table.set_tracks(self.mock_tracks)

        count = table.copy_tracklist_to_clipboard()
        self.assertEqual(count, 2)

        clipboard = QApplication.clipboard()
        self.assertIsNotNone(clipboard)
        text = clipboard.text()
        self.assertIn("01. CamelPhat - Breathe", text)
        self.assertIn("02. Bicep - Glue", text)

    def test_chip_badge_delegate_and_column_widths(self):
        from PySide6.QtGui import QColor, QImage, QPainter
        from PySide6.QtWidgets import QStyleOptionViewItem
        from rekordbox_collection_manager.gui.track_table import (
            DEFAULT_COLLECTION_COLUMNS,
            ChipBadgeDelegate,
            TrackColumn,
            TrackTableConfig,
            TracksTableWidget,
        )

        table = TracksTableWidget(config=TrackTableConfig(columns=list(DEFAULT_COLLECTION_COLUMNS)))
        tags_idx = list(DEFAULT_COLLECTION_COLUMNS).index(TrackColumn.TAGS)
        playlists_idx = list(DEFAULT_COLLECTION_COLUMNS).index(TrackColumn.PLAYLISTS)

        self.assertEqual(table.columnWidth(tags_idx), 220)
        self.assertEqual(table.columnWidth(playlists_idx), 270)

        delegate = ChipBadgeDelegate(
            bg_color=QColor(50, 50, 50),
            border_color=QColor(80, 80, 80),
            text_color=QColor(255, 255, 255),
        )

        # Test paint on QImage
        img = QImage(300, 40, QImage.Format.Format_ARGB32)
        img.fill(0)
        painter = QPainter(img)
        opt = QStyleOptionViewItem()
        opt.rect = table.rect()

        table.set_tracks(self.mock_tracks)
        model_idx = table.model().index(0, tags_idx)

        # Ensure delegate paint executes cleanly
        delegate.paint(painter, opt, model_idx)
        painter.end()

        # Test sizeHint
        hint = delegate.sizeHint(opt, model_idx)
        self.assertGreater(hint.width(), 0)
        self.assertEqual(hint.height(), 36)


if __name__ == "__main__":
    unittest.main()



