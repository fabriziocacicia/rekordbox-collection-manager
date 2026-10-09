"""Pytest configuration and global fixtures."""

import pytest
from rekordbox_collection_manager.gui.workers import wait_for_retired_workers


@pytest.fixture(autouse=True)
def cleanup_gui_workers():
    """Ensure all background worker threads are drained between tests."""
    yield
    wait_for_retired_workers(timeout_ms=1000)
