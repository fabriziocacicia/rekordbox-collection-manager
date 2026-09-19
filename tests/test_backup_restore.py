"""Tests for rekordbox_collection_manager.backup_restore."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rekordbox_collection_manager.backup_restore import (
    create_database_backup,
    get_default_backup_dir,
    list_backups,
    perform_restore,
)


class TestBackupRestore(unittest.TestCase):
    def test_get_default_backup_dir(self):
        d = get_default_backup_dir()
        self.assertIn("Music", str(d))
        self.assertIn("rekordbox", str(d))
        self.assertIn("Backups", str(d))

    def test_list_backups_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            backups = list_backups(tmp_dir)
            self.assertEqual(backups, [])

    def test_list_backups_with_items(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            # Create a full backup
            b1 = root / "rekordbox_mac_backup_20260901_120000"
            (b1 / "Pioneer_Library").mkdir(parents=True)
            (b1 / "Pioneer_Library" / "master.db").write_text("DUMMY_DB_1")

            # Create a newer quick snapshot
            b2 = root / "rekordbox_backup_20260902_180000"
            b2.mkdir(parents=True)
            (b2 / "master.db").write_text("DUMMY_DB_2_LONGER")

            # Create a non-backup folder
            (root / "random_folder").mkdir()

            backups = list_backups(root)
            self.assertEqual(len(backups), 2)
            # Sorted newest first
            self.assertEqual(backups[0]["name"], "rekordbox_backup_20260902_180000")
            self.assertEqual(backups[0]["type"], "DB Snapshot")
            self.assertEqual(backups[1]["name"], "rekordbox_mac_backup_20260901_120000")
            self.assertEqual(backups[1]["type"], "Full Library")

    def test_create_backup_standalone_db(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            db_file = root / "exportLibrary.db"
            db_file.write_text("EXPORT_DB_CONTENT")
            wal_file = root / "exportLibrary.db-wal"
            wal_file.write_text("WAL_DATA")

            backup_root = root / "Backups"

            with patch("rekordbox_collection_manager.backup_restore.is_rekordbox_running", return_value=False):
                res = create_database_backup(db_path=str(db_file), backup_dir=backup_root)

            self.assertTrue(res["success"])
            self.assertTrue(Path(res["backup_folder"]).is_dir())
            saved_db = Path(res["backup_folder"]) / "exportLibrary.db"
            self.assertTrue(saved_db.exists())
            self.assertEqual(saved_db.read_text(), "EXPORT_DB_CONTENT")
            saved_wal = Path(res["backup_folder"]) / "exportLibrary.db-wal"
            self.assertTrue(saved_wal.exists())

    def test_perform_restore_success(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(fake_home := tmp_dir)
            live_pioneer = root / "Library" / "Pioneer" / "rekordbox"
            live_pioneer.mkdir(parents=True)
            (live_pioneer / "master.db").write_text("LIVE_ACTIVE_DB")
            (live_pioneer / "master.db-wal").write_text("OLD_WAL")

            backups_dir = root / "Music" / "rekordbox" / "Backups"
            backup_folder = backups_dir / "rekordbox_mac_backup_20260901_100000"
            (backup_folder / "Pioneer_Library").mkdir(parents=True)
            (backup_folder / "Pioneer_Library" / "master.db").write_text("RESTORE_THIS_DB")
            (backup_folder / "Pioneer_Library" / "networkAnalyze6.db").write_text("ANALYSIS")

            with patch("rekordbox_collection_manager.backup_restore.is_rekordbox_running", return_value=False):
                result = perform_restore(
                    backup_path=backup_folder,
                    target_pioneer_dir=live_pioneer,
                    backup_root=backups_dir,
                )

            self.assertTrue(result["success"])
            self.assertEqual((live_pioneer / "master.db").read_text(), "RESTORE_THIS_DB")
            self.assertEqual((live_pioneer / "networkAnalyze6.db").read_text(), "ANALYSIS")
            # Old WAL file deleted
            self.assertFalse((live_pioneer / "master.db-wal").exists())

            # Emergency safety backup created
            safety_backups = list(backups_dir.glob("rekordbox_pre_restore_safety_*"))
            self.assertEqual(len(safety_backups), 1)
            self.assertEqual(
                (safety_backups[0] / "Pioneer_Library" / "master.db").read_text(),
                "LIVE_ACTIVE_DB",
            )

    def test_perform_restore_blocks_if_rekordbox_running(self):
        with patch("rekordbox_collection_manager.backup_restore.is_rekordbox_running", return_value=True):
            with self.assertRaises(RuntimeError) as ctx:
                perform_restore(backup_path="/some/path")
            self.assertIn("Rekordbox", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
