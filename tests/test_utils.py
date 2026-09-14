"""Tests for rekordbox_collection_fixer.utils."""

import os
import unittest
from unittest.mock import MagicMock, patch

from rekordbox_collection_fixer.utils import (
    ensure_rekordbox_not_running,
    is_rekordbox_running,
)


class TestUtils(unittest.TestCase):
    def test_is_rekordbox_running_pgrep(self):
        with patch("shutil.which", return_value="/usr/bin/pgrep"), \
             patch("subprocess.run") as mock_sub:
            mock_sub.return_value = MagicMock(returncode=0, stdout="8888 rekordbox\n")
            self.assertTrue(is_rekordbox_running())

    def test_is_rekordbox_running_pgrep_ignores_self_and_tool(self):
        with patch("shutil.which", return_value="/usr/bin/pgrep"), \
             patch("subprocess.run") as mock_sub, \
             patch("psutil.process_iter", return_value=[]), \
             patch("rekordbox_collection_fixer.utils.get_rekordbox_pid", return_value=0):
            current_pid = os.getpid()
            mock_sub.return_value = MagicMock(
                returncode=0,
                stdout=f"{current_pid} rekordbox\n9999 rekordbox-collection-fixer\n",
            )
            self.assertFalse(is_rekordbox_running())

    def test_is_rekordbox_running_psutil_fallback(self):
        proc_mock = MagicMock()
        proc_mock.info = {"pid": 55555, "name": "rekordbox.exe"}

        with patch("shutil.which", return_value=None), \
             patch("psutil.process_iter", return_value=[proc_mock]):
            self.assertTrue(is_rekordbox_running())

    def test_is_rekordbox_running_not_found(self):
        with patch("shutil.which", return_value="/usr/bin/pgrep"), \
             patch("subprocess.run") as mock_sub, \
             patch("psutil.process_iter", return_value=[]), \
             patch("rekordbox_collection_fixer.utils.get_rekordbox_pid", return_value=0):
            mock_sub.return_value = MagicMock(returncode=1, stdout="")
            self.assertFalse(is_rekordbox_running())

    def test_ensure_rekordbox_not_running_exits(self):
        with patch("rekordbox_collection_fixer.utils.is_rekordbox_running", return_value=True):
            with self.assertRaises(SystemExit) as cm:
                ensure_rekordbox_not_running(force=False, exit_on_running=True)
            self.assertEqual(cm.exception.code, 1)

    def test_ensure_rekordbox_not_running_force_allowed(self):
        with patch("rekordbox_collection_fixer.utils.is_rekordbox_running", return_value=True):
            # When force=True, it should not exit
            running = ensure_rekordbox_not_running(force=True, exit_on_running=True)
            self.assertTrue(running)

    def test_ensure_rekordbox_not_running_when_not_running(self):
        with patch("rekordbox_collection_fixer.utils.is_rekordbox_running", return_value=False):
            running = ensure_rekordbox_not_running(force=False, exit_on_running=True)
            self.assertFalse(running)


if __name__ == "__main__":
    unittest.main()
