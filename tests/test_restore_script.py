"""Tests for scripts/restore_rekordbox_mac.sh."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class TestRestoreScript(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo_root = Path(__file__).resolve().parent.parent
        cls.script_path = cls.repo_root / "scripts" / "restore_rekordbox_mac.sh"
        assert cls.script_path.exists(), f"Script not found: {cls.script_path}"

    def run_script(self, args=None, stdin_input=None, custom_env=None):
        cmd = [str(self.script_path)] + (args or [])
        env = os.environ.copy()
        if custom_env:
            env.update(custom_env)

        return subprocess.run(
            cmd,
            input=stdin_input,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    def test_help_flag(self):
        res = self.run_script(["--help"])
        self.assertEqual(res.returncode, 0)
        self.assertIn("Uso:", res.stdout)
        self.assertIn("restore_rekordbox_mac.sh", res.stdout)

    def test_invalid_path(self):
        res = self.run_script(["/tmp/non_existent_folder_abc123"])
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("La cartella di backup specificata non esiste", res.stdout)

    def test_backup_folder_missing_master_db(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            res = self.run_script([tmp_dir])
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("master.db", res.stdout)

    def test_no_backups_found_in_backups_folder(self):
        with tempfile.TemporaryDirectory() as fake_home:
            backups_dir = Path(fake_home) / "Music" / "rekordbox" / "Backups"
            backups_dir.mkdir(parents=True)
            res = self.run_script([], custom_env={"HOME": fake_home})
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("Nessun backup Rekordbox trovato", res.stdout)

    def test_successful_restore_explicit_path_with_yes_flag(self):
        with tempfile.TemporaryDirectory() as fake_home:
            home = Path(fake_home)
            backups_dir = home / "Music" / "rekordbox" / "Backups"
            backups_dir.mkdir(parents=True)

            # Create existing active Rekordbox library
            live_pioneer = home / "Library" / "Pioneer" / "rekordbox"
            live_pioneer.mkdir(parents=True)
            (live_pioneer / "master.db").write_text("ACTIVE_DB_CONTENT")
            (live_pioneer / "master.db-wal").write_text("OLD_ORPHAN_WAL")

            # Create backup folder
            backup_dir = backups_dir / "rekordbox_mac_backup_20260901_120000"
            backup_pioneer = backup_dir / "Pioneer_Library"
            backup_pioneer.mkdir(parents=True)
            (backup_pioneer / "master.db").write_text("BACKED_UP_DB_CONTENT")
            (backup_pioneer / "networkAnalyze6.db").write_text("ANALYSIS_DATA")

            # Run restore with -y
            res = self.run_script(["-y", str(backup_dir)], custom_env={"HOME": fake_home})
            self.assertEqual(res.returncode, 0, msg=f"STDOUT: {res.stdout}\nSTDERR: {res.stderr}")
            self.assertIn("Ripristino completato con successo", res.stdout)

            # Verify master.db was restored
            restored_db = (live_pioneer / "master.db").read_text()
            self.assertEqual(restored_db, "BACKED_UP_DB_CONTENT")
            self.assertEqual((live_pioneer / "networkAnalyze6.db").read_text(), "ANALYSIS_DATA")

            # Verify orphan master.db-wal was deleted
            self.assertFalse((live_pioneer / "master.db-wal").exists())

            # Verify safety pre-restore backup was created in Backups folder
            safety_backups = list(backups_dir.glob("rekordbox_pre_restore_safety_*"))
            self.assertEqual(len(safety_backups), 1)
            safety_db = safety_backups[0] / "Pioneer_Library" / "master.db"
            self.assertTrue(safety_db.exists())
            self.assertEqual(safety_db.read_text(), "ACTIVE_DB_CONTENT")

    def test_successful_restore_with_multiple_backups_interactive_choice(self):
        with tempfile.TemporaryDirectory() as fake_home:
            home = Path(fake_home)
            backups_dir = home / "Music" / "rekordbox" / "Backups"
            backups_dir.mkdir(parents=True)

            live_pioneer = home / "Library" / "Pioneer" / "rekordbox"
            live_pioneer.mkdir(parents=True)
            (live_pioneer / "master.db").write_text("CURRENT")

            # Create 2 backups: backup 1 (older), backup 2 (newer)
            b1 = backups_dir / "rekordbox_mac_backup_20260901_100000" / "Pioneer_Library"
            b1.mkdir(parents=True)
            (b1 / "master.db").write_text("OLDER_BACKUP")

            b2 = backups_dir / "rekordbox_mac_backup_20260902_200000" / "Pioneer_Library"
            b2.mkdir(parents=True)
            (b2 / "master.db").write_text("NEWER_BACKUP")

            # Choose 2 (older backup), confirm 's'
            res = self.run_script([], stdin_input="2\ns\n", custom_env={"HOME": fake_home})
            self.assertEqual(res.returncode, 0, msg=f"STDOUT: {res.stdout}\nSTDERR: {res.stderr}")
            self.assertIn("Ripristino completato con successo", res.stdout)

            self.assertEqual((live_pioneer / "master.db").read_text(), "OLDER_BACKUP")

    def test_restore_application_support_when_present(self):
        with tempfile.TemporaryDirectory() as fake_home:
            home = Path(fake_home)
            backups_dir = home / "Music" / "rekordbox" / "Backups"
            backups_dir.mkdir(parents=True)

            live_pioneer = home / "Library" / "Pioneer" / "rekordbox"
            live_pioneer.mkdir(parents=True)
            (live_pioneer / "master.db").write_text("CURRENT")

            live_appsupport = home / "Library" / "Application Support" / "Pioneer" / "rekordbox"
            live_appsupport.mkdir(parents=True)
            (live_appsupport / "config.json").write_text('{"ver": 1}')

            backup_dir = backups_dir / "rekordbox_mac_backup_20260903_150000"
            (backup_dir / "Pioneer_Library").mkdir(parents=True)
            (backup_dir / "Pioneer_Library" / "master.db").write_text("RESTORED_DB")

            (backup_dir / "Application_Support").mkdir(parents=True)
            (backup_dir / "Application_Support" / "config.json").write_text('{"ver": 2}')

            res = self.run_script(["-y", str(backup_dir)], custom_env={"HOME": fake_home})
            self.assertEqual(res.returncode, 0, msg=f"STDOUT: {res.stdout}\nSTDERR: {res.stderr}")

            self.assertEqual((live_appsupport / "config.json").read_text(), '{"ver": 2}')


if __name__ == "__main__":
    unittest.main()
