"""Unit tests for Turso Cloud Sync Engine, Daemon, and Custom Manager GUI integration."""

import os
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, text

from rekordbox_collection_manager.turso_sync import (
    SyncResult,
    is_turso_configured,
    run_sync_cycle,
)
from rekordbox_collection_manager.gui.workers import SyncWorker


def test_sync_result_truthiness_and_attributes():
    res_ok = SyncResult(success=True, applied_events=3, stats={"tracks": 10}, message="OK")
    assert bool(res_ok) is True
    assert res_ok.success is True
    assert res_ok.applied_events == 3
    assert res_ok.stats == {"tracks": 10}
    assert res_ok.message == "OK"
    assert "success=True" in repr(res_ok)

    res_fail = SyncResult(success=False, applied_events=0, message="Failed")
    assert bool(res_fail) is False
    assert res_fail.success is False
    assert res_fail.applied_events == 0


def test_is_turso_configured(monkeypatch):
    monkeypatch.setenv("TURSO_DATABASE_URL", "https://my-test-db.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "secret-token")
    assert is_turso_configured() is True

    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
    # Patch get_turso_credentials to simulate missing credentials
    with patch("rekordbox_collection_manager.turso_sync.get_turso_credentials", side_effect=ValueError("No credentials")):
        assert is_turso_configured() is False


def test_run_sync_cycle_unconfigured(monkeypatch):
    with patch("rekordbox_collection_manager.turso_sync.is_turso_configured", return_value=False):
        res = run_sync_cycle()
        assert bool(res) is False
        assert "not configured" in res.message


def test_run_sync_cycle_rekordbox_running():
    with patch("rekordbox_collection_manager.turso_sync.is_turso_configured", return_value=True), \
         patch("rekordbox_collection_manager.turso_sync.is_rekordbox_running", return_value=True):
        res = run_sync_cycle(force=False)
        assert bool(res) is False
        assert "Rekordbox is currently running" in res.message

        # With force=True, it proceeds beyond the running check
        with patch("rekordbox_collection_manager.turso_sync.create_turso_client") as mock_client:
            mock_client.side_effect = RuntimeError("Stop execution here")
            res_forced = run_sync_cycle(force=True, db_path="/nonexistent/path/db.db")
            # Fails on db path or connection, not on rekordbox running check
            assert "Rekordbox is currently running" not in res_forced.message


def test_run_sync_cycle_full_success(tmp_path):
    fake_db = tmp_path / "master.db"
    fake_db.touch()

    mock_client = MagicMock()
    mock_stats = {"tracks": 42, "playlists": 5, "tags": 12}

    engine = create_engine(f"sqlite:///{fake_db}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE djmdContent (ID TEXT PRIMARY KEY, Title TEXT);"))
        conn.execute(text("INSERT INTO djmdContent (ID, Title) VALUES ('1', 'Test Track');"))

    with patch("rekordbox_collection_manager.turso_sync.is_turso_configured", return_value=True), \
         patch("rekordbox_collection_manager.turso_sync.is_rekordbox_running", return_value=False), \
         patch("rekordbox_collection_manager.turso_sync.create_turso_client", return_value=mock_client), \
         patch("rekordbox_collection_manager.turso_sync.create_database_backup", return_value={"backup_folder": str(tmp_path / "backups")}) as mock_backup, \
         patch("rekordbox_collection_manager.turso_sync.apply_changelog_events", return_value=2) as mock_apply, \
         patch("rekordbox_collection_manager.turso_sync.export_collection_to_turso", return_value=mock_stats) as mock_export:

        res = run_sync_cycle(db_path=str(fake_db), engine=engine, create_backup=True)

        assert res.success is True
        assert res.applied_events == 2
        assert res.stats == mock_stats
        mock_backup.assert_called_once()
        mock_apply.assert_called_once()
        mock_export.assert_called_once()


os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication

def get_qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_sync_worker_thread(tmp_path):
    get_qapp()
    fake_db = tmp_path / "master.db"
    fake_db.touch()

    worker = SyncWorker(db_path=str(fake_db), force=False, create_backup=False)

    received_results = []
    received_errors = []

    worker.success.connect(lambda res: received_results.append(res))
    worker.error.connect(lambda err: received_errors.append(err))

    fake_result = SyncResult(success=True, applied_events=1, stats={"tracks": 10}, message="OK")

    with patch("rekordbox_collection_manager.turso_sync.run_sync_cycle", return_value=fake_result):
        worker.run()

    assert len(received_results) == 1
    assert received_results[0].success is True
    assert received_results[0].applied_events == 1
    assert len(received_errors) == 0


def test_views_have_db_saved_signals():
    get_qapp()
    from rekordbox_collection_manager.gui.history_view import HistoryView
    from rekordbox_collection_manager.gui.play_count_view import PlayCountView
    from rekordbox_collection_manager.gui.playlists_view import PlaylistsView
    from rekordbox_collection_manager.gui.replace_file_view import ReplaceFileView
    from rekordbox_collection_manager.gui.tags_view import TagsView
    from rekordbox_collection_manager.gui.track_detail_view import TrackDetailView
    from rekordbox_collection_manager.gui.tracks_view import TracksView

    hv = HistoryView()
    pv = PlaylistsView()
    tv = TagsView()
    trv = TracksView()
    pcv = PlayCountView()
    rfv = ReplaceFileView()
    tdv = TrackDetailView()

    assert hasattr(hv, "db_saved")
    assert hasattr(pv, "db_saved")
    assert hasattr(tv, "db_saved")
    assert hasattr(trv, "db_saved")
    assert hasattr(pcv, "db_saved")
    assert hasattr(rfv, "db_saved")
    assert hasattr(tdv, "db_saved")


def test_main_window_sync_triggers(tmp_path):
    get_qapp()
    from rekordbox_collection_manager.gui.main_window import MainWindow

    fake_db = tmp_path / "master.db"
    fake_db.touch()

    with patch.object(MainWindow, "load_database"), \
         patch("rekordbox_collection_manager.gui.main_window.is_turso_configured", return_value=True), \
         patch("rekordbox_collection_manager.gui.main_window.is_rekordbox_running", return_value=False):

        window = MainWindow(initial_db_path=str(fake_db))

        with patch.object(window, "trigger_sync") as mock_trigger:
            window._on_db_saved()
            mock_trigger.assert_called_once_with(background=True, create_backup=True)

        # Test initial load triggers sync
        window._initial_sync_done = False
        with patch.object(window, "trigger_sync") as mock_trigger:
            window._on_db_loaded(
                engine=MagicMock(),
                path=str(fake_db),
                history_records=[],
                total_tracks=10,
                table_format="djmdSongHistory",
            )
            assert window._initial_sync_done is True
            mock_trigger.assert_called_once_with(background=True, create_backup=True)

        # Test closeEvent runs sync cycle
        from PySide6.QtGui import QCloseEvent
        with patch("rekordbox_collection_manager.gui.main_window.run_sync_cycle") as mock_run_sync:
            window.closeEvent(QCloseEvent())
            mock_run_sync.assert_called_once_with(str(fake_db), force=False, create_backup=True)
