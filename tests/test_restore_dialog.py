"""Tests for rekordbox_collection_manager.gui.restore_dialog."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QApplication

from rekordbox_collection_manager.gui.restore_dialog import RestoreDialog

# Ensure QApplication exists for GUI tests
app = QApplication.instance() or QApplication([])


class TestRestoreDialog(unittest.TestCase):
    def test_restore_dialog_init_empty(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("rekordbox_collection_manager.gui.restore_dialog.get_default_backup_dir", return_value=Path(tmp_dir)), \
                 patch("rekordbox_collection_manager.gui.restore_dialog.is_rekordbox_running", return_value=False):
                dlg = RestoreDialog()
                self.assertIsNotNone(dlg)
                self.assertEqual(dlg.table.rowCount(), 0)
                self.assertFalse(dlg.btn_restore.isEnabled())

    def test_restore_dialog_with_backups_preselects_first(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            b1 = root / "rekordbox_mac_backup_20260901_100000"
            (b1 / "Pioneer_Library").mkdir(parents=True)
            (b1 / "Pioneer_Library" / "master.db").write_text("DB1")

            with patch("rekordbox_collection_manager.gui.restore_dialog.get_default_backup_dir", return_value=root), \
                 patch("rekordbox_collection_manager.gui.restore_dialog.is_rekordbox_running", return_value=False):
                dlg = RestoreDialog()
                self.assertEqual(dlg.table.rowCount(), 1)
                self.assertIsNotNone(dlg.selected_backup)
                self.assertEqual(dlg.selected_backup["name"], "rekordbox_mac_backup_20260901_100000")
                self.assertTrue(dlg.btn_restore.isEnabled())

    def test_restore_dialog_disables_restore_when_rekordbox_running(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            b1 = root / "rekordbox_mac_backup_20260901_100000"
            (b1 / "Pioneer_Library").mkdir(parents=True)
            (b1 / "Pioneer_Library" / "master.db").write_text("DB1")

            with patch("rekordbox_collection_manager.gui.restore_dialog.get_default_backup_dir", return_value=root), \
                 patch("rekordbox_collection_manager.gui.restore_dialog.is_rekordbox_running", return_value=True):
                dlg = RestoreDialog()
                self.assertFalse(dlg.btn_restore.isEnabled())
                self.assertIn("running", dlg.safety_chip.text().lower())

    def test_restore_clicked_aborts_if_rekordbox_running_at_click_time(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            b1 = root / "rekordbox_mac_backup_20260901_100000"
            (b1 / "Pioneer_Library").mkdir(parents=True)
            (b1 / "Pioneer_Library" / "master.db").write_text("DB1")

            with patch("rekordbox_collection_manager.gui.restore_dialog.get_default_backup_dir", return_value=root), \
                 patch("rekordbox_collection_manager.gui.restore_dialog.is_rekordbox_running", return_value=False):
                dlg = RestoreDialog()
                self.assertTrue(dlg.btn_restore.isEnabled())

            with patch("rekordbox_collection_manager.gui.restore_dialog.is_rekordbox_running", return_value=True), \
                 patch("PySide6.QtWidgets.QMessageBox.warning") as mock_warn, \
                 patch("rekordbox_collection_manager.gui.restore_dialog.RestoreWorker") as mock_worker:
                dlg._on_restore_clicked()
                mock_warn.assert_called_once()
                mock_worker.assert_not_called()
                self.assertFalse(dlg.btn_restore.isEnabled())

    def test_main_window_menu_bar(self):
        from rekordbox_collection_manager.gui.main_window import MainWindow

        with patch.object(MainWindow, "load_database"), patch.object(MainWindow, "_check_rekordbox_status"):
            win = MainWindow(initial_db_path=None)
            try:
                menu_bar = win.menuBar()
                self.assertIsNotNone(menu_bar)

                menus = [action.text() for action in menu_bar.actions()]
                self.assertIn("&Database", menus)
                self.assertNotIn("&View", menus)

                # Test tab switching helper
                win._switch_to_tab(1)
                self.assertEqual(win.stacked_widget.currentIndex(), 1)
                win._switch_to_tab(2)
                self.assertEqual(win.stacked_widget.currentIndex(), 2)
            finally:
                win.safety_timer.stop()
                win.close()


if __name__ == "__main__":
    unittest.main()
