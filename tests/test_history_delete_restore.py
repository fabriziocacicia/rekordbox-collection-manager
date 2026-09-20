"""Unit tests for Rekordbox history session soft-deletion and restoration."""

import unittest
from sqlalchemy import create_engine
from PySide6.QtWidgets import QApplication

from rekordbox_collection_manager.gui.workers import (
    HistoryDeleteWorker,
    HistoryRestoreWorker,
)
from rekordbox_collection_manager.rename_history import (
    fetch_history_records,
    fetch_session_tracks,
    restore_history_session,
    soft_delete_history_session,
)


class TestHistoryDeleteRestore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Ensure QApplication is initialized for QThread / Qt tests
        cls.app = QApplication.instance() or QApplication([])

    def _create_test_db(self):
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
                    rb_data_status INTEGER DEFAULT 0,
                    rb_local_data_status INTEGER DEFAULT 0,
                    rb_local_deleted TINYINT(1) DEFAULT 0,
                    rb_local_synced TINYINT(1) DEFAULT 0,
                    created_at DATETIME,
                    updated_at DATETIME
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdSongHistory (
                    ID VARCHAR(255) PRIMARY KEY,
                    HistoryID VARCHAR(255),
                    ContentID VARCHAR(255),
                    TrackNo INTEGER,
                    rb_data_status INTEGER DEFAULT 0,
                    rb_local_deleted TINYINT(1) DEFAULT 0,
                    created_at DATETIME,
                    updated_at DATETIME
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdContent (
                    ID VARCHAR(255) PRIMARY KEY,
                    Title VARCHAR(255),
                    SrcArtistName VARCHAR(255),
                    BPM INTEGER DEFAULT 12400,
                    Length INTEGER DEFAULT 300,
                    DJPlayCount INTEGER DEFAULT 1,
                    Rating INTEGER DEFAULT 0,
                    ReleaseYear INTEGER,
                    BitRate INTEGER,
                    Commnt TEXT,
                    FolderPath TEXT,
                    FileNameL TEXT,
                    rb_local_deleted TINYINT(1) DEFAULT 0
                );
            """)

            # Year folder 2026
            conn.exec_driver_sql("""
                INSERT INTO djmdHistory (ID, Seq, Name, Attribute, ParentID, DateCreated, rb_local_deleted, rb_data_status)
                VALUES ('2026', 1, '2026', 1, 'root', '2026-01-01 00:00:00', 0, 0);
            """)
            # Month folder 202603
            conn.exec_driver_sql("""
                INSERT INTO djmdHistory (ID, Seq, Name, Attribute, ParentID, DateCreated, rb_local_deleted, rb_data_status)
                VALUES ('202603', 3, '3', 1, '2026', '2026-03-01 00:00:00', 0, 0);
            """)
            # Session 1: HISTORY 2026-03-15
            conn.exec_driver_sql("""
                INSERT INTO djmdHistory (ID, Seq, Name, Attribute, ParentID, DateCreated, rb_local_deleted, rb_data_status)
                VALUES ('sess_1', 1, 'HISTORY 2026-03-15', 0, '202603', '2026-03-15 22:00:00', 0, 0);
            """)
            # Session 2: HISTORY 2026-03-20
            conn.exec_driver_sql("""
                INSERT INTO djmdHistory (ID, Seq, Name, Attribute, ParentID, DateCreated, rb_local_deleted, rb_data_status)
                VALUES ('sess_2', 2, 'HISTORY 2026-03-20', 0, '202603', '2026-03-20 23:00:00', 0, 0);
            """)

            # Tracks
            conn.exec_driver_sql("""
                INSERT INTO djmdContent (ID, Title, SrcArtistName) VALUES ('c1', 'Track 1', 'Artist 1');
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdContent (ID, Title, SrcArtistName) VALUES ('c2', 'Track 2', 'Artist 2');
            """)

            # Song history for sess_1
            conn.exec_driver_sql("""
                INSERT INTO djmdSongHistory (ID, HistoryID, ContentID, TrackNo, rb_local_deleted, rb_data_status)
                VALUES ('sh1', 'sess_1', 'c1', 1, 0, 0);
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdSongHistory (ID, HistoryID, ContentID, TrackNo, rb_local_deleted, rb_data_status)
                VALUES ('sh2', 'sess_1', 'c2', 2, 0, 0);
            """)
        return engine

    def test_soft_delete_history_session(self):
        engine = self._create_test_db()

        # Delete sess_1
        ok = soft_delete_history_session(engine, "sess_1")
        self.assertTrue(ok)

        with engine.connect() as conn:
            # sess_1 in djmdHistory
            row = conn.exec_driver_sql("SELECT rb_local_deleted, rb_data_status FROM djmdHistory WHERE ID = 'sess_1';").fetchone()
            self.assertEqual(row[0], 1)
            self.assertEqual(row[1], 2)

            # djmdSongHistory tracks for sess_1
            sh_rows = conn.exec_driver_sql("SELECT rb_local_deleted, rb_data_status FROM djmdSongHistory WHERE HistoryID = 'sess_1';").fetchall()
            self.assertEqual(len(sh_rows), 2)
            for r in sh_rows:
                self.assertEqual(r[0], 1)
                self.assertEqual(r[1], 2)

            # Month folder 202603 should still be active because sess_2 remains active
            m_row = conn.exec_driver_sql("SELECT rb_local_deleted FROM djmdHistory WHERE ID = '202603';").fetchone()
            self.assertEqual(m_row[0], 0)

            # Year folder 2026 should still be active
            y_row = conn.exec_driver_sql("SELECT rb_local_deleted FROM djmdHistory WHERE ID = '2026';").fetchone()
            self.assertEqual(y_row[0], 0)

    def test_soft_delete_cleans_empty_parent_folders(self):
        engine = self._create_test_db()

        # Delete both sessions under 202603
        soft_delete_history_session(engine, "sess_1")
        soft_delete_history_session(engine, "sess_2")

        with engine.connect() as conn:
            # Month folder 202603 should now be marked deleted
            m_row = conn.exec_driver_sql("SELECT rb_local_deleted, rb_data_status FROM djmdHistory WHERE ID = '202603';").fetchone()
            self.assertEqual(m_row[0], 1)
            self.assertEqual(m_row[1], 2)

            # Year folder 2026 should also be marked deleted because no active months remain
            y_row = conn.exec_driver_sql("SELECT rb_local_deleted, rb_data_status FROM djmdHistory WHERE ID = '2026';").fetchone()
            self.assertEqual(y_row[0], 1)
            self.assertEqual(y_row[1], 2)

    def test_restore_history_session(self):
        engine = self._create_test_db()

        # Delete sess_1 and sess_2 (folders become deleted)
        soft_delete_history_session(engine, "sess_1")
        soft_delete_history_session(engine, "sess_2")

        # Now restore sess_1
        ok = restore_history_session(engine, "sess_1")
        self.assertTrue(ok)

        with engine.connect() as conn:
            # sess_1 restored
            row = conn.exec_driver_sql("SELECT rb_local_deleted, rb_data_status FROM djmdHistory WHERE ID = 'sess_1';").fetchone()
            self.assertEqual(row[0], 0)
            self.assertEqual(row[1], 0)

            # Song history restored
            sh_rows = conn.exec_driver_sql("SELECT rb_local_deleted, rb_data_status FROM djmdSongHistory WHERE HistoryID = 'sess_1';").fetchall()
            for r in sh_rows:
                self.assertEqual(r[0], 0)
                self.assertEqual(r[1], 0)

            # Month folder 202603 restored
            m_row = conn.exec_driver_sql("SELECT rb_local_deleted, rb_data_status FROM djmdHistory WHERE ID = '202603';").fetchone()
            self.assertEqual(m_row[0], 0)
            self.assertEqual(m_row[1], 0)

            # Year folder 2026 restored
            y_row = conn.exec_driver_sql("SELECT rb_local_deleted, rb_data_status FROM djmdHistory WHERE ID = '2026';").fetchone()
            self.assertEqual(y_row[0], 0)
            self.assertEqual(y_row[1], 0)

    def test_fetch_history_records_include_deleted(self):
        engine = self._create_test_db()
        soft_delete_history_session(engine, "sess_1")

        # Default (include_deleted=False)
        active_records = fetch_history_records(engine, "djmdHistory", include_deleted=False)
        self.assertEqual(len(active_records), 1)
        self.assertEqual(active_records[0]["id"], "sess_2")
        self.assertFalse(active_records[0]["is_deleted"])

        # With include_deleted=True
        all_records = fetch_history_records(engine, "djmdHistory", include_deleted=True)
        self.assertEqual(len(all_records), 2)
        rec_ids = {r["id"]: r["is_deleted"] for r in all_records}
        self.assertTrue(rec_ids["sess_1"])
        self.assertFalse(rec_ids["sess_2"])

    def test_fetch_session_tracks_for_deleted_session(self):
        engine = self._create_test_db()
        soft_delete_history_session(engine, "sess_1")

        data = fetch_session_tracks(engine, "sess_1")
        self.assertEqual(data["session"]["id"], "sess_1")
        self.assertTrue(data["session"]["is_deleted"])
        self.assertEqual(len(data["tracks"]), 2)
        self.assertEqual(data["tracks"][0]["title"], "Track 1")

    def test_delete_and_restore_workers(self):
        engine = self._create_test_db()

        # Delete worker
        del_worker = HistoryDeleteWorker(engine, "sess_1")
        deleted_ids = []
        del_worker.success.connect(deleted_ids.append)
        del_worker.run()
        self.assertEqual(deleted_ids, ["sess_1"])

        with engine.connect() as conn:
            row = conn.exec_driver_sql("SELECT rb_local_deleted FROM djmdHistory WHERE ID = 'sess_1';").fetchone()
            self.assertEqual(row[0], 1)

        # Restore worker
        res_worker = HistoryRestoreWorker(engine, "sess_1")
        restored_ids = []
        res_worker.success.connect(restored_ids.append)
        res_worker.run()
        self.assertEqual(restored_ids, ["sess_1"])

        with engine.connect() as conn:
            row = conn.exec_driver_sql("SELECT rb_local_deleted FROM djmdHistory WHERE ID = 'sess_1';").fetchone()
            self.assertEqual(row[0], 0)


class TestHistoryViewDeletedUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

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
                    UUID VARCHAR(255),
                    rb_data_status INTEGER DEFAULT 0,
                    rb_local_data_status INTEGER DEFAULT 0,
                    rb_local_deleted TINYINT(1) DEFAULT 0,
                    rb_local_synced TINYINT(1) DEFAULT 0,
                    created_at DATETIME,
                    updated_at DATETIME
                );
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdHistory (ID, Seq, Name, Attribute, ParentID, DateCreated, rb_local_deleted, rb_data_status)
                VALUES 
                    ('2026', 1, '2026', 1, 'root', '2026-01-01 00:00:00', 0, 0),
                    ('202603', 3, '3', 1, '2026', '2026-03-01 00:00:00', 0, 0),
                    ('s_active', 1, 'HISTORY 2026-03-10', 0, '202603', '2026-03-10 20:00:00', 0, 0),
                    ('s_deleted', 2, 'HISTORY 2026-03-15', 0, '202603', '2026-03-15 21:00:00', 1, 2);
            """)

        from rekordbox_collection_manager.gui.history_view import HistoryView
        self.records = fetch_history_records(self.engine, "djmdHistory", include_deleted=True)
        self.view = HistoryView()
        self.view.set_data(self.engine, self.records)

    def test_show_deleted_toggle_and_tree(self):
        # 1. By default, only active sessions are shown
        self.assertEqual(self.view.table.rowCount(), 1)
        self.assertEqual(self.view.table.item(0, 0).text(), "Clean")
        self.assertIn("1", self.view.show_deleted_btn.text())
        self.assertIn("Show Deleted", self.view.show_deleted_btn.text())
        self.assertEqual(self.view.table.columnWidth(6), 160)

        # Folder tree should only have All Sessions and active year
        top_items = [self.view.folder_tree.topLevelItem(i).text(0) for i in range(self.view.folder_tree.topLevelItemCount())]
        self.assertFalse(any("Deleted Sessions" in t for t in top_items))

        # 2. Toggle show deleted ON
        self.view.show_deleted_btn.setChecked(True)
        self.assertIn("Hide Deleted", self.view.show_deleted_btn.text())
        top_items_on = [self.view.folder_tree.topLevelItem(i).text(0) for i in range(self.view.folder_tree.topLevelItemCount())]
        self.assertTrue(any("Deleted Sessions" in t for t in top_items_on))

        # Find deleted sessions item in tree and click it
        del_item = None
        for i in range(self.view.folder_tree.topLevelItemCount()):
            it = self.view.folder_tree.topLevelItem(i)
            if "Deleted Sessions" in it.text(0):
                del_item = it
                break
        self.assertIsNotNone(del_item)

        self.view._on_folder_item_clicked(del_item, 0)
        self.assertEqual(self.view.table.rowCount(), 1)
        self.assertEqual(self.view.table.item(0, 0).text(), "🗑️ Deleted")
        # Deleted item should not be editable
        from PySide6.QtCore import Qt
        flags = self.view.table.item(0, 1).flags()
        self.assertFalse(bool(flags & Qt.ItemFlag.ItemIsEditable))

        # 3. Toggle show deleted OFF
        self.view.show_deleted_btn.setChecked(False)
        self.assertIn("Show Deleted", self.view.show_deleted_btn.text())
        top_items_off = [self.view.folder_tree.topLevelItem(i).text(0) for i in range(self.view.folder_tree.topLevelItemCount())]
        self.assertFalse(any("Deleted Sessions" in t for t in top_items_off))
        # Active sessions restored
        self.assertEqual(self.view.table.rowCount(), 1)
        self.assertEqual(self.view.table.item(0, 0).text(), "Clean")

    def test_delete_and_restore_success_callbacks(self):
        # Soft delete the active session
        self.view._on_delete_success("s_active")
        self.assertEqual(self.view.table.rowCount(), 0)  # Active list is now empty

        # Check deleted button shows 2
        self.assertIn("2", self.view.show_deleted_btn.text())

        # Restore s_active
        self.view._on_restore_success("s_active")
        self.assertEqual(self.view.table.rowCount(), 1)
        self.assertEqual(self.view.table.item(0, 2).text(), "HISTORY 2026-03-10")

    def test_detail_view_deleted_state(self):
        detail = self.view.detail_view
        detail._on_data_loaded({
            "session": {"id": "s_deleted", "name": "HISTORY 2026-03-15", "date": "2026-03-15", "is_deleted": True},
            "tracks": [],
            "stats": {},
        })
        self.assertFalse(detail.deleted_badge.isHidden())
        self.assertFalse(detail.restore_btn.isHidden())
        self.assertIn("DELETED SESSIONS", detail.breadcrumb_label.text())

        # Active state
        detail._on_data_loaded({
            "session": {"id": "s_active", "name": "HISTORY 2026-03-10", "date": "2026-03-10", "is_deleted": False},
            "tracks": [],
            "stats": {},
        })
        self.assertTrue(detail.deleted_badge.isHidden())
        self.assertTrue(detail.restore_btn.isHidden())

    def test_action_buttons_equal_height(self):
        from PySide6.QtWidgets import QPushButton
        action_widget = self.view.table.cellWidget(0, 6)
        detail_btn = action_widget.findChild(QPushButton, "detailActionBtn")
        del_btn = action_widget.findChild(QPushButton, "deleteActionBtn")
        self.assertIsNotNone(detail_btn)
        self.assertIsNotNone(del_btn)
        self.assertEqual(detail_btn.maximumHeight(), 24)
        self.assertEqual(detail_btn.minimumHeight(), 24)
        self.assertEqual(del_btn.maximumHeight(), 24)
        self.assertEqual(del_btn.minimumHeight(), 24)
        self.assertEqual(detail_btn.maximumHeight(), del_btn.maximumHeight())

        # Switch to deleted
        self.view.show_deleted_btn.setChecked(True)
        del_item = None
        for i in range(self.view.folder_tree.topLevelItemCount()):
            it = self.view.folder_tree.topLevelItem(i)
            if "Deleted Sessions" in it.text(0):
                del_item = it
                break
        self.view._on_folder_item_clicked(del_item, 0)
        action_widget_del = self.view.table.cellWidget(0, 6)
        restore_btn = action_widget_del.findChild(QPushButton, "restoreActionBtn")
        self.assertIsNotNone(restore_btn)
        self.assertEqual(restore_btn.maximumHeight(), 24)
        self.assertEqual(restore_btn.minimumHeight(), 24)
        self.assertEqual(detail_btn.maximumHeight(), restore_btn.maximumHeight())

    def test_row_header_click_opens_menu(self):
        from unittest.mock import patch
        from PySide6.QtCore import QPoint

        with patch.object(self.view, "_show_session_context_menu") as mock_show:
            self.view._on_row_header_clicked(0)
            mock_show.assert_called_once()
            args, _ = mock_show.call_args
            self.assertEqual(args[0], 0)
            self.assertIn(0, [i.row() for i in self.view.table.selectedIndexes()])

        with patch.object(self.view, "_show_session_context_menu") as mock_show:
            self.view._on_row_header_context_menu(QPoint(10, 10))
            mock_show.assert_called_once()
            args, _ = mock_show.call_args
            self.assertEqual(args[0], 0)


if __name__ == "__main__":
    unittest.main()

