"""Tests for package version and sidebar display alignment."""

import os
import unittest
from pathlib import Path
import tomllib

# Set offscreen platform for headless Qt testing
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication

import rekordbox_collection_manager
from rekordbox_collection_manager import __version__
from rekordbox_collection_manager.gui.main_window import MainWindow

app = QApplication.instance()
if app is None:
    app = QApplication([])


class TestVersionDisplay(unittest.TestCase):
    def test_version_matches_pyproject(self):
        pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
        self.assertTrue(pyproject_path.exists())
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        expected_version = data["project"]["version"]
        self.assertEqual(__version__, expected_version)

    def test_sidebar_version_label(self):
        window = MainWindow(initial_db_path="non_existent_db_for_test.db")
        self.assertEqual(window.app_ver.text(), f"v{__version__}")
        self.assertNotIn("Python", window.app_ver.text())
        window.close()


if __name__ == "__main__":
    unittest.main()
