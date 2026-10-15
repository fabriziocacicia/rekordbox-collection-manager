"""Unit tests for HistoryDiffDialog and HistoryImportDialog."""

import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QApplication
from sqlalchemy import create_engine

from rekordbox_collection_manager.gui.history_diff_dialog import HistoryDiffDialog
from rekordbox_collection_manager.gui.history_import_dialog import HistoryImportDialog
from rekordbox_collection_manager.history_import import ExternalHistoryAnalysis, HistorySessionAnalysis

# Ensure QApplication exists for GUI tests
app = QApplication.instance() or QApplication([])


class TestHistoryGUI(unittest.TestCase):
    def setUp(self):
        self.cur_engine = create_engine("sqlite:///:memory:")

    def test_history_diff_dialog_init(self):
        current_tracks = [
            {
                "track_no": 1,
                "played_at": "2025-07-05 15:40:00",
                "played_time": "15:40:00",
                "content_id": "c1",
                "title": "Song A",
                "artist": "Artist A",
                "album": "Album A",
                "duration_sec": 300,
                "duration_str": "5:00",
                "file_path": "/Music/Song A.mp3",
                "file_name": "Song A.mp3",
                "bpm": 120.0,
            }
        ]
        external_tracks = [
            {
                "track_no": 1,
                "played_at": "2025-07-05 15:40:00",
                "played_time": "15:40:00",
                "content_id": "c1",
                "title": "Song A",
                "artist": "Artist A",
                "album": "Album A",
                "duration_sec": 300,
                "duration_str": "5:00",
                "file_path": "/Music/Song A.mp3",
                "file_name": "Song A.mp3",
                "bpm": 120.0,
            },
            {
                "track_no": 2,
                "played_at": "2025-07-05 15:45:00",
                "played_time": "15:45:00",
                "content_id": "c2",
                "title": "Song B",
                "artist": "Artist B",
                "album": "Album B",
                "duration_sec": 360,
                "duration_str": "6:00",
                "file_path": "/Music/Song B.mp3",
                "file_name": "Song B.mp3",
                "bpm": 122.0,
            },
        ]

        index = {
            "by_id": {"c1": "c1", "c2": "c2"},
            "by_filename": {"song a.mp3": "c1", "song b.mp3": "c2"},
            "by_title_artist": {("song a", "artist a"): "c1", ("song b", "artist b"): "c2"},
            "by_title_duration": {},
        }

        dlg = HistoryDiffDialog(
            session_name="HISTORY 2025-07-05",
            session_date="2025-07-05 15:36:36",
            current_tracks=current_tracks,
            external_tracks=external_tracks,
            current_track_index=index,
        )

        self.assertIsNotNone(dlg)
        self.assertEqual(dlg.cur_table.rowCount(), 1)
        self.assertEqual(dlg.ext_table.rowCount(), 2)
        self.assertEqual(dlg.merged_table.rowCount(), 2)

        # Test toggle selection
        dlg._set_all_new_included(False)
        active_count = sum(1 for it in dlg.diff_data["merged_column"] if it.included)
        self.assertEqual(active_count, 1)  # only current item remains

        dlg._set_all_new_included(True)
        active_count = sum(1 for it in dlg.diff_data["merged_column"] if it.included)
        self.assertEqual(active_count, 2)

    def test_history_import_dialog_init(self):
        dlg = HistoryImportDialog(
            current_engine=self.cur_engine,
            current_db_path=":memory:",
        )
        self.assertIsNotNone(dlg)
        self.assertFalse(dlg.overview_container.isVisible())
        self.assertEqual(dlg.current_category_filter, "to_merge")

    def test_history_import_dialog_populate_categories(self):
        dlg = HistoryImportDialog(
            current_engine=self.cur_engine,
            current_db_path=":memory:",
        )

        analysis = ExternalHistoryAnalysis(
            external_db_path="/path/old_master.db",
            total_external_sessions=3,
            sessions=[
                HistorySessionAnalysis(
                    external_id="e1",
                    name="Session 1",
                    date_created="2025-07-05",
                    year=2025,
                    month=7,
                    external_track_count=20,
                    current_id="c1",
                    current_track_count=10,
                    diff_count=10,
                    category="to_merge",
                ),
                HistorySessionAnalysis(
                    external_id="e2",
                    name="Session 2",
                    date_created="2025-08-04",
                    year=2025,
                    month=8,
                    external_track_count=15,
                    current_id="c2",
                    current_track_count=0,
                    diff_count=15,
                    category="in_trash",
                    is_current_deleted=True,
                ),
                HistorySessionAnalysis(
                    external_id="e3",
                    name="Session 3",
                    date_created="2026-01-01",
                    year=2026,
                    month=1,
                    external_track_count=30,
                    current_id="c3",
                    current_track_count=30,
                    diff_count=0,
                    category="identical",
                ),
            ],
            counts={"to_merge": 1, "in_trash": 1, "new": 0, "identical": 1},
        )

        with patch("rekordbox_collection_manager.gui.history_import_dialog.open_database") as mock_open:
            mock_open.return_value = (create_engine("sqlite:///:memory:"), "djmdHistory")
            dlg._on_analysis_success(analysis)

        self.assertFalse(dlg.overview_container.isHidden())
        self.assertEqual(dlg.table.rowCount(), 1)  # 1 in "to_merge"
        self.assertIn("1", dlg.btn_tab_merge.text())
        self.assertIn("1", dlg.btn_tab_trash.text())

        # Switch to trash tab
        dlg._set_category_filter("in_trash")
        self.assertEqual(dlg.table.rowCount(), 1)
        self.assertIn("Ripristina", dlg.btn_execute.text())

        # Select all visible
        dlg._select_all_visible(True)
        self.assertIn("e2", dlg.selected_session_ids)
        self.assertTrue(dlg.btn_execute.isEnabled())
        self.assertEqual(dlg.table.horizontalHeaderItem(0).text(), "✓")
        self.assertIn("Ripristina Selezionate (1)", dlg.btn_execute.text())

        # Deselect all visible
        dlg._select_all_visible(False)
        self.assertNotIn("e2", dlg.selected_session_ids)
        self.assertEqual(dlg.table.horizontalHeaderItem(0).text(), "✓")
        self.assertFalse(dlg.btn_execute.isEnabled())

    def test_history_import_dialog_cell_click_and_toggle(self):
        from PySide6.QtCore import Qt, QPoint
        from PySide6.QtTest import QTest
        from rekordbox_collection_manager.gui.history_diff_dialog import TableCheckBox, CheckBoxCellWidget

        dlg = HistoryImportDialog(
            current_engine=self.cur_engine,
            current_db_path=":memory:",
        )
        analysis = ExternalHistoryAnalysis(
            external_db_path="/path/old_master.db",
            total_external_sessions=2,
            sessions=[
                HistorySessionAnalysis(
                    external_id="e1",
                    name="Session 1",
                    date_created="2025-07-05",
                    year=2025,
                    month=7,
                    external_track_count=20,
                    current_id="c1",
                    current_track_count=10,
                    diff_count=10,
                    category="to_merge",
                ),
                HistorySessionAnalysis(
                    external_id="e2",
                    name="Session 2",
                    date_created="2025-08-04",
                    year=2025,
                    month=8,
                    external_track_count=15,
                    current_id="c2",
                    current_track_count=5,
                    diff_count=10,
                    category="to_merge",
                ),
            ],
            counts={"to_merge": 2, "in_trash": 0, "new": 0, "identical": 0},
        )
        with patch("rekordbox_collection_manager.gui.history_import_dialog.open_database") as mock_open:
            mock_open.return_value = (create_engine("sqlite:///:memory:"), "djmdHistory")
            dlg._on_analysis_success(analysis)

        dlg.show()
        app.processEvents()

        # Cell widget in row 0
        cell_widget = dlg.table.cellWidget(0, 0)
        self.assertIsInstance(cell_widget, CheckBoxCellWidget)
        cb = cell_widget.findChild(TableCheckBox)
        self.assertFalse(cb.isChecked())

        # Click anywhere in the cell (e.g. top-left corner)
        QTest.mouseClick(cell_widget, Qt.MouseButton.LeftButton, pos=QPoint(2, 2))
        self.assertTrue(cb.isChecked())
        self.assertIn("e1", dlg.selected_session_ids)
        self.assertIn("Merge a Selezionate (1)", dlg.btn_execute.text())

        # Click bottom select all button
        dlg.btn_select_all.click()
        self.assertIn("e1", dlg.selected_session_ids)
        self.assertIn("e2", dlg.selected_session_ids)
        self.assertIn("Merge a Selezionate (2)", dlg.btn_execute.text())

        # Click header section 0 to toggle all
        dlg.table.horizontalHeader().sectionClicked.emit(0)
        self.assertEqual(len(dlg.selected_session_ids), 0)
        self.assertFalse(dlg.btn_execute.isEnabled())

        dlg.close()

    def test_track_search_dialog(self):
        from rekordbox_collection_manager.gui.track_search_dialog import TrackSearchDialog
        from sqlalchemy import text

        # Populate in-memory collection
        with self.cur_engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE djmdContent (
                    ID VARCHAR(255) PRIMARY KEY,
                    Title VARCHAR(255),
                    ArtistID VARCHAR(255),
                    SrcArtistName VARCHAR(255),
                    FileNameL VARCHAR(255),
                    FolderPath VARCHAR(255),
                    Length INTEGER,
                    BPM INTEGER,
                    DJPlayCount INTEGER,
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """))
            conn.execute(text("""
                CREATE TABLE djmdArtist (
                    ID VARCHAR(255) PRIMARY KEY,
                    Name VARCHAR(255)
                );
            """))
            conn.execute(text("""
                INSERT INTO djmdArtist (ID, Name) VALUES ('art1', 'KeeQ');
            """))
            conn.execute(text("""
                INSERT INTO djmdContent (ID, Title, ArtistID, SrcArtistName, FileNameL, FolderPath, Length, BPM, DJPlayCount)
                VALUES ('c_ax', 'Axel (Original Mix)', 'art1', 'KeeQ', 'KeeQ - Axel.aiff', '/Music', 280, 12600, 3);
            """))

        dlg = TrackSearchDialog(
            current_engine=self.cur_engine,
            target_title="Axel",
            target_artist="KeeQ",
            target_duration_str="4:40",
            target_duration_sec=280,
        )
        self.assertIsNotNone(dlg)
        self.assertEqual(dlg.table.rowCount(), 1)
        self.assertTrue(dlg.btn_confirm.isEnabled())
        self.assertEqual(dlg.selected_track_data["id"], "c_ax")

        picked_events = []
        dlg.track_selected.connect(picked_events.append)
        dlg.btn_confirm.click()
        self.assertEqual(len(picked_events), 1)
        self.assertEqual(picked_events[0]["id"], "c_ax")
        dlg.close()

    def test_history_diff_manual_association(self):
        dlg = HistoryDiffDialog(
            session_name="HISTORY TEST",
            session_date="2025-07-05 16:00:00",
            current_tracks=[],
            external_tracks=[
                {
                    "track_no": 1,
                    "played_at": "2025-07-05 16:00:00",
                    "played_time": "16:00:00",
                    "content_id": "ext_99",
                    "title": "Axel",
                    "artist": "KeeQ",
                    "album": "",
                    "duration_sec": 280,
                    "duration_str": "4:40",
                    "file_path": "/old/Axel.mp3",
                    "file_name": "Axel.mp3",
                    "bpm": 126.0,
                }
            ],
            current_track_index={"by_id": {}, "by_filename": {}, "by_title_artist": {}, "by_title_duration": {}},
            current_engine=self.cur_engine,
        )

        # Before association: 1 missing file, 0 matches
        self.assertEqual(dlg.diff_data["merged_column"][0].status, "missing_file")
        self.assertIn("0 Match", dlg.chip_matched.text())
        self.assertIn("1 File Mancanti", dlg.chip_missing.text())

        # Simulate picking track from search
        target = dlg.diff_data["merged_column"][0]
        target.matched_content_id = "c_ax"
        target.status = "matched"
        setattr(target, "is_manual_match", True)
        dlg._populate_tables()
        dlg._update_stats_display()

        # After association: 1 match, 0 missing files
        self.assertEqual(dlg.diff_data["merged_column"][0].status, "matched")
        self.assertEqual(dlg.diff_data["merged_column"][0].matched_content_id, "c_ax")
        self.assertIn("1 Match", dlg.chip_matched.text())
        self.assertIn("0 File Mancanti", dlg.chip_missing.text())

        # Test unlinking
        dlg._unlink_track(target)
        self.assertEqual(dlg.diff_data["merged_column"][0].status, "missing_file")
        self.assertIsNone(dlg.diff_data["merged_column"][0].matched_content_id)
        self.assertIn("0 Match", dlg.chip_matched.text())
        self.assertIn("1 File Mancanti", dlg.chip_missing.text())

        dlg.close()


if __name__ == "__main__":
    unittest.main()

