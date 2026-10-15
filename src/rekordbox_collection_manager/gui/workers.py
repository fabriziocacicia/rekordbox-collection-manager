"""Background worker threads for non-blocking database operations."""

import threading
from typing import Any, Callable, Dict, List, Optional, Sequence, Union
from PySide6.QtCore import QObject, QThread, Signal
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rekordbox_collection_manager.edit_play_count import (
    batch_update_track_play_counts,
    fetch_all_playlists,
    fetch_all_tags,
    fetch_distinct_file_types,
    fetch_distinct_keys,
    fetch_track_full_details,
    search_tracks,
    set_track_play_count,
    update_track_play_count,
)
from rekordbox_collection_manager.playlists import (
    fetch_playlist_tracks,
    fetch_playlists_tree,
)
from rekordbox_collection_manager.tags import (
    fetch_tag_tracks,
    fetch_tags_tree,
    sort_tags_in_folder,
)
from rekordbox_collection_manager.rename_history import (
    batch_update_history_records,
    fetch_history_records,
    fetch_session_tracks,
    realign_history_order,
    restore_history_session,
    soft_delete_history_session,
)
from rekordbox_collection_manager.utils import (
    build_exclude_streaming_clause,
    detect_content_table,
    detect_history_table,
    open_database,
    resolve_db_path,
)


_RETIRED_WORKERS: set = set()


def stop_worker(worker: Optional[Any], wait_ms: int = 50) -> None:
    """Safely stop a background worker without terminating it mid-operation.

    Disconnects common completion signals so subsequent emissions are ignored,
    requests thread exit/interruption, and waits briefly for clean termination.
    Keeps a strong reference to any worker still executing until its 'finished'
    signal fires, preventing Python GC from destroying active C++ QThread objects.
    """
    if not worker:
        return
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for sig_name in ("success", "error", "progress"):
            sig = getattr(worker, sig_name, None)
            if sig is not None:
                try:
                    sig.disconnect()
                except Exception:
                    pass

    # Support daemon QObject workers with custom stop() method (e.g. SyncWorker, BackupWorker)
    if hasattr(worker, "stop"):
        try:
            worker.stop()
        except Exception:
            pass

    is_running = False
    if hasattr(worker, "isRunning"):
        try:
            is_running = worker.isRunning()
        except Exception:
            is_running = False

    if is_running:
        _RETIRED_WORKERS.add(worker)
        try:
            worker.setParent(None)
        except Exception:
            pass
        try:
            worker.finished.connect(lambda: _RETIRED_WORKERS.discard(worker))
        except Exception:
            pass
        if hasattr(worker, "requestInterruption"):
            worker.requestInterruption()
        if hasattr(worker, "quit"):
            worker.quit()
        if hasattr(worker, "wait"):
            worker.wait(wait_ms)


def wait_for_retired_workers(timeout_ms: int = 1000) -> None:
    """Wait for any active background workers to finish before process termination."""
    remaining = list(_RETIRED_WORKERS)
    if not remaining:
        return
    per_worker = max(20, timeout_ms // len(remaining))
    for worker in remaining:
        try:
            if hasattr(worker, "stop"):
                worker.stop()
            is_running = False
            if hasattr(worker, "isRunning"):
                try:
                    is_running = worker.isRunning()
                except Exception:
                    pass
            if is_running:
                if hasattr(worker, "requestInterruption"):
                    worker.requestInterruption()
                if hasattr(worker, "quit"):
                    worker.quit()
                if hasattr(worker, "wait"):
                    worker.wait(per_worker)
        except Exception:
            pass
    _RETIRED_WORKERS.clear()


class DatabaseLoaderWorker(QThread):
    """Worker to resolve, open, and load initial database records."""

    success = Signal(object, str, list, int, str)  # engine, path, history_records, total_tracks, table_format
    error = Signal(str)

    def __init__(self, db_path: Optional[str] = None, parent: Optional[Any] = None):
        super().__init__(parent)
        self.db_path = db_path

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                return
            resolved_path = resolve_db_path(self.db_path, allow_prompt=False)
            if self.isInterruptionRequested():
                return
            engine, _ = open_database(resolved_path)
            if self.isInterruptionRequested():
                return

            # Clean up duplicate tag associations if Rekordbox is not running
            try:
                from rekordbox_collection_manager.utils import is_rekordbox_running
                if not is_rekordbox_running():
                    from rekordbox_collection_manager.tags import cleanup_duplicate_song_tags
                    cleanup_duplicate_song_tags(engine)
            except Exception:
                pass

            history_table = detect_history_table(engine)
            history_records = fetch_history_records(engine, history_table, include_deleted=True)
            if self.isInterruptionRequested():
                return

            # Count total tracks (excluding deleted and streaming tracks)
            total_tracks = 0
            try:
                content_table = detect_content_table(engine)
                with engine.connect() as conn:
                    cols = {r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({content_table});").fetchall()}
                    del_filter = "WHERE (rb_local_deleted = 0 OR rb_local_deleted IS NULL)" if "rb_local_deleted" in cols else "WHERE 1=1"
                    streaming_filter = build_exclude_streaming_clause(cols, table_alias=None)
                    row = conn.execute(text(f"SELECT count(*) FROM {content_table} {del_filter} {streaming_filter};")).fetchone()
                    total_tracks = int(row[0]) if row else 0
            except Exception:
                total_tracks = 0

            if self.isInterruptionRequested():
                return
            self.success.emit(engine, resolved_path, history_records, total_tracks, history_table)
        except Exception as e:
            if not self.isInterruptionRequested():
                self.error.emit(str(e))


class TrackSearchWorker(QThread):
    """Worker to perform track searches without blocking the UI."""

    success = Signal(str, list)  # query, records
    error = Signal(str)

    def __init__(
        self,
        engine: Engine,
        query: str,
        limit: Optional[int] = 150,
        sort_by: str = "title",
        sort_order: str = "asc",
        play_count_filter: Optional[str] = "all",
        exclude_streaming: bool = True,
        purchased_filter: Optional[str] = "all",
        store_filter: Optional[Union[str, Sequence[str]]] = None,
        key_filter: Optional[Union[str, Sequence[str]]] = None,
        bpm_min: Optional[float] = None,
        bpm_max: Optional[float] = None,
        file_type_filter: Optional[Union[str, Sequence[str]]] = None,
        tag_filter: Optional[Union[str, Sequence[str]]] = None,
        playlist_filter: Optional[Union[str, Sequence[str]]] = None,
        tag_match_mode: str = "any",
        playlist_match_mode: str = "any",
        parent: Optional[Any] = None,
    ):
        super().__init__(parent)
        self.engine = engine
        self.query = query
        self.limit = limit
        self.sort_by = sort_by
        self.sort_order = sort_order
        self.play_count_filter = play_count_filter
        self.exclude_streaming = exclude_streaming
        self.purchased_filter = purchased_filter
        self.store_filter = store_filter
        self.key_filter = key_filter
        self.bpm_min = bpm_min
        self.bpm_max = bpm_max
        self.file_type_filter = file_type_filter
        self.tag_filter = tag_filter
        self.playlist_filter = playlist_filter
        self.tag_match_mode = tag_match_mode
        self.playlist_match_mode = playlist_match_mode

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                return
            records = search_tracks(
                self.engine,
                self.query,
                limit=self.limit,
                sort_by=self.sort_by,
                sort_order=self.sort_order,
                play_count_filter=self.play_count_filter,
                exclude_streaming=self.exclude_streaming,
                purchased_filter=self.purchased_filter,
                store_filter=self.store_filter,
                key_filter=self.key_filter,
                bpm_min=self.bpm_min,
                bpm_max=self.bpm_max,
                file_type_filter=self.file_type_filter,
                tag_filter=self.tag_filter,
                playlist_filter=self.playlist_filter,
                tag_match_mode=self.tag_match_mode,
                playlist_match_mode=self.playlist_match_mode,
            )
            if self.isInterruptionRequested():
                return
            self.success.emit(self.query, records)
        except Exception as e:
            if not self.isInterruptionRequested():
                self.error.emit(str(e))


class FilterOptionsWorker(QThread):
    """Worker to fetch distinct formats, keys, tags, and playlists asynchronously without GUI freeze."""

    success = Signal(list, list, list, list)  # formats, keys, tag_names, playlist_names
    error = Signal(str)

    def __init__(self, engine: Engine, parent: Optional[Any] = None):
        super().__init__(parent)
        self.engine = engine

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                return
            formats = fetch_distinct_file_types(self.engine)
            if self.isInterruptionRequested():
                return
            keys = fetch_distinct_keys(self.engine)
            if self.isInterruptionRequested():
                return
            tags = fetch_all_tags(self.engine)
            tag_names = [t.get("name") if isinstance(t, dict) else str(t) for t in tags if t]
            tag_names = [n for n in tag_names if n]
            if self.isInterruptionRequested():
                return
            playlists = fetch_all_playlists(self.engine)
            playlist_names = [p.get("name") if isinstance(p, dict) else str(p) for p in playlists if p]
            playlist_names = [n for n in playlist_names if n]
            if self.isInterruptionRequested():
                return
            self.success.emit(formats, keys, tag_names, playlist_names)
        except Exception as e:
            if not self.isInterruptionRequested():
                self.error.emit(str(e))


class HistoryBatchSaveWorker(QThread):
    """Worker to save batch history renames and realign the tree."""

    success = Signal(dict)  # stats
    error = Signal(str)

    def __init__(
        self,
        engine: Engine,
        updates: List[Dict[str, Any]],
        auto_realign: bool = True,
        parent: Optional[Any] = None,
    ):
        super().__init__(parent)
        self.engine = engine
        self.updates = updates
        self.auto_realign = auto_realign

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                return
            stats = batch_update_history_records(
                self.engine, self.updates, auto_realign=self.auto_realign
            )
            if self.isInterruptionRequested():
                return
            self.success.emit(stats)
        except Exception as e:
            if not self.isInterruptionRequested():
                self.error.emit(str(e))


class HistoryRealignWorker(QThread):
    """Worker to run history realignment without UI freeze."""

    success = Signal(dict)
    error = Signal(str)

    def __init__(self, engine: Engine, parent: Optional[Any] = None):
        super().__init__(parent)
        self.engine = engine

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                return
            stats = realign_history_order(self.engine)
            if self.isInterruptionRequested():
                return
            self.success.emit(stats)
        except Exception as e:
            if not self.isInterruptionRequested():
                self.error.emit(str(e))


class PlayCountUpdateWorker(QThread):
    """Worker to update a single track's play count in background."""

    success = Signal(str, int, int)  # track_id, old_count, new_count
    error = Signal(str, str)  # track_id, error_message

    def __init__(
        self,
        engine: Engine,
        track_id: str,
        delta: Optional[int] = None,
        absolute: Optional[int] = None,
        parent: Optional[Any] = None,
    ):
        super().__init__(parent)
        self.engine = engine
        self.track_id = track_id
        self.delta = delta
        self.absolute = absolute

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                return
            if self.absolute is not None:
                old_count, new_count = set_track_play_count(
                    self.engine, self.track_id, self.absolute
                )
            elif self.delta is not None:
                old_count, new_count = update_track_play_count(
                    self.engine, self.track_id, self.delta
                )
            else:
                return
            if self.isInterruptionRequested():
                return
            self.success.emit(self.track_id, old_count, new_count)
        except Exception as e:
            if not self.isInterruptionRequested():
                self.error.emit(self.track_id, str(e))


class PlayCountBatchSaveWorker(QThread):
    """Worker to save batch track play count updates in an atomic transaction."""

    success = Signal(int)  # updated_count
    error = Signal(str)

    def __init__(
        self,
        engine: Engine,
        updates: List[Dict[str, Any]],
        parent: Optional[Any] = None,
    ):
        super().__init__(parent)
        self.engine = engine
        self.updates = updates

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                return
            updated_count = batch_update_track_play_counts(self.engine, self.updates)
            if self.isInterruptionRequested():
                return
            self.success.emit(updated_count)
        except Exception as e:
            if not self.isInterruptionRequested():
                self.error.emit(str(e))


class HistorySessionDetailWorker(QThread):
    """Worker to fetch session tracks and aggregate metrics without blocking the UI."""

    success = Signal(dict)  # session details & tracks dictionary
    error = Signal(str)

    def __init__(
        self,
        engine: Engine,
        history_id: str,
        parent: Optional[Any] = None,
    ):
        super().__init__(parent)
        self.engine = engine
        self.history_id = history_id

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                return
            data = fetch_session_tracks(self.engine, self.history_id)
            if self.isInterruptionRequested():
                return
            self.success.emit(data)
        except Exception as e:
            if not self.isInterruptionRequested():
                self.error.emit(str(e))


class TrackDetailWorker(QThread):
    """Worker to fetch full track metadata and history plays without blocking the UI."""

    success = Signal(dict)  # full track details & history plays
    error = Signal(str)

    def __init__(
        self,
        engine: Engine,
        content_id: str,
        db_path: Optional[str] = None,
        parent: Optional[Any] = None,
    ):
        super().__init__(parent)
        self.engine = engine
        self.content_id = content_id
        self.db_path = db_path

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                return
            data = fetch_track_full_details(
                self.engine,
                self.content_id,
                db_path=self.db_path,
            )
            if self.isInterruptionRequested():
                return
            self.success.emit(data)
        except Exception as e:
            if not self.isInterruptionRequested():
                self.error.emit(str(e))


class AudioInspectWorker(QThread):
    """Worker to extract audio file specs without freezing UI."""

    success = Signal(dict)  # audio specs dict
    error = Signal(str)

    def __init__(self, file_path: str, parent: Optional[Any] = None):
        super().__init__(parent)
        self.file_path = file_path

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                return
            from rekordbox_collection_manager.replace_file import inspect_audio_file

            info = inspect_audio_file(self.file_path)
            if self.isInterruptionRequested():
                return
            self.success.emit(info)
        except Exception as e:
            if not self.isInterruptionRequested():
                self.error.emit(str(e))


class TrackReplaceWorker(QThread):
    """Worker to perform track audio file replacement in background."""

    success = Signal(dict)  # result dict
    error = Signal(str)

    def __init__(
        self,
        engine: Engine,
        content_id: str,
        new_file_path: str,
        options: Optional[Dict[str, Any]] = None,
        parent: Optional[Any] = None,
    ):
        super().__init__(parent)
        self.engine = engine
        self.content_id = content_id
        self.new_file_path = new_file_path
        self.options = options or {}

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                return
            from rekordbox_collection_manager.replace_file import replace_track_audio_file

            res = replace_track_audio_file(
                self.engine,
                self.content_id,
                self.new_file_path,
                options=self.options,
            )
            if self.isInterruptionRequested():
                return
            self.success.emit(res)
        except Exception as e:
            if not self.isInterruptionRequested():
                self.error.emit(str(e))


class TrackMergeWorker(QThread):
    """Worker to merge old collection track into a newly imported track in background."""

    success = Signal(dict)  # result dict
    error = Signal(str)

    def __init__(
        self,
        engine: Engine,
        source_content_id: str,
        target_content_id: str,
        options: Optional[Dict[str, Any]] = None,
        parent: Optional[Any] = None,
    ):
        super().__init__(parent)
        self.engine = engine
        self.source_content_id = source_content_id
        self.target_content_id = target_content_id
        self.options = options or {}

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                return
            from rekordbox_collection_manager.replace_file import merge_imported_tracks

            res = merge_imported_tracks(
                self.engine,
                self.source_content_id,
                self.target_content_id,
                options=self.options,
            )
            if self.isInterruptionRequested():
                return
            self.success.emit(res)
        except Exception as e:
            if not self.isInterruptionRequested():
                self.error.emit(str(e))


class BackupWorker(QObject):
    """Worker to create database backups in background without blocking UI."""

    success = Signal(dict)
    error = Signal(str)
    finished = Signal()

    def __init__(
        self,
        db_path: Optional[str] = None,
        backup_dir: Optional[Any] = None,
        parent: Optional[Any] = None,
    ):
        super().__init__(parent)
        self.db_path = db_path
        self.backup_dir = backup_dir
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def run(self) -> None:
        """Execute directly in the caller's thread (useful for testing)."""
        self._run()

    def isRunning(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def isInterruptionRequested(self) -> bool:
        return self._stop_event.is_set()

    def requestInterruption(self) -> None:
        self._stop_event.set()

    def quit(self) -> None:
        self.requestInterruption()

    def wait(self, timeout_ms: int = 0) -> None:
        if self._thread and self._thread.is_alive():
            timeout_sec = timeout_ms / 1000.0 if timeout_ms > 0 else None
            self._thread.join(timeout=timeout_sec)

    def _run(self) -> None:
        try:
            from rekordbox_collection_manager.backup_restore import create_database_backup

            res = create_database_backup(db_path=self.db_path, backup_dir=self.backup_dir)
            self.success.emit(res)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self.finished.emit()


class RestoreWorker(QThread):
    """Worker to perform database restoration in background without blocking UI."""

    success = Signal(dict)
    error = Signal(str)

    def __init__(
        self,
        backup_path: Any,
        backup_root: Optional[Any] = None,
        parent: Optional[Any] = None,
    ):
        super().__init__(parent)
        self.backup_path = backup_path
        self.backup_root = backup_root

    def run(self) -> None:
        try:
            from rekordbox_collection_manager.backup_restore import perform_restore

            res = perform_restore(backup_path=self.backup_path, backup_root=self.backup_root)
            self.success.emit(res)
        except Exception as e:
            self.error.emit(str(e))


class HistoryDeleteWorker(QThread):
    """Worker to soft-delete a history session in background."""

    success = Signal(str)  # history_id
    error = Signal(str, str)  # history_id, error_message

    def __init__(self, engine: Engine, history_id: str, parent: Optional[Any] = None):
        super().__init__(parent)
        self.engine = engine
        self.history_id = history_id

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                return
            ok = soft_delete_history_session(self.engine, self.history_id)
            if self.isInterruptionRequested():
                return
            if ok:
                self.success.emit(self.history_id)
            else:
                self.error.emit(self.history_id, f"Session {self.history_id} could not be deleted.")
        except Exception as e:
            if not self.isInterruptionRequested():
                self.error.emit(self.history_id, str(e))


class HistoryRestoreWorker(QThread):
    """Worker to restore a soft-deleted history session in background."""

    success = Signal(str)  # history_id
    error = Signal(str, str)  # history_id, error_message

    def __init__(self, engine: Engine, history_id: str, parent: Optional[Any] = None):
        super().__init__(parent)
        self.engine = engine
        self.history_id = history_id

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                return
            ok = restore_history_session(self.engine, self.history_id)
            if self.isInterruptionRequested():
                return
            if ok:
                self.success.emit(self.history_id)
            else:
                self.error.emit(self.history_id, f"Session {self.history_id} could not be restored.")
        except Exception as e:
            if not self.isInterruptionRequested():
                self.error.emit(self.history_id, str(e))


class PlaylistLoaderWorker(QThread):
    """Worker to fetch playlist tree and track counts asynchronously."""

    success = Signal(list)  # tree
    error = Signal(str)

    def __init__(
        self,
        engine: Engine,
        include_deleted: bool = False,
        parent: Optional[Any] = None,
    ):
        super().__init__(parent)
        self.engine = engine
        self.include_deleted = include_deleted

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                return
            tree = fetch_playlists_tree(self.engine, include_deleted=self.include_deleted)
            if self.isInterruptionRequested():
                return
            self.success.emit(tree)
        except Exception as e:
            if not self.isInterruptionRequested():
                self.error.emit(str(e))

class CategoryTracksWorker(QThread):
    """Base worker to query and populate category tracks (playlists, tags, etc.) asynchronously."""

    success = Signal(str, list)  # category_id, records
    error = Signal(str)

    def __init__(
        self,
        engine: Engine,
        category_id: str,
        fetch_fn: Callable[..., List[Dict[str, Any]]],
        query: str = "",
        limit: Optional[int] = None,
        sort_by: Optional[str] = None,
        sort_order: str = "asc",
        exclude_streaming: bool = True,
        parent: Optional[Any] = None,
    ):
        super().__init__(parent)
        self.engine = engine
        self.category_id = category_id
        self.fetch_fn = fetch_fn
        self.query = query
        self.limit = limit
        self.sort_by = sort_by
        self.sort_order = sort_order
        self.exclude_streaming = exclude_streaming

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                return
            tracks = self.fetch_fn(
                self.engine,
                self.category_id,
                query=self.query,
                limit=self.limit,
                sort_by=self.sort_by,
                sort_order=self.sort_order,
                exclude_streaming=self.exclude_streaming,
            )
            if self.isInterruptionRequested():
                return
            self.success.emit(self.category_id, tracks)
        except Exception as e:
            if not self.isInterruptionRequested():
                self.error.emit(str(e))


class PlaylistTracksWorker(CategoryTracksWorker):
    """Worker to query and populate playlist tracks without UI freeze."""

    def __init__(
        self,
        engine: Engine,
        playlist_id: str,
        query: str = "",
        limit: Optional[int] = None,
        sort_by: Optional[str] = None,
        sort_order: str = "asc",
        exclude_streaming: bool = True,
        parent: Optional[Any] = None,
    ):
        super().__init__(
            engine=engine,
            category_id=playlist_id,
            fetch_fn=fetch_playlist_tracks,
            query=query,
            limit=limit,
            sort_by=sort_by,
            sort_order=sort_order,
            exclude_streaming=exclude_streaming,
            parent=parent,
        )
        self.playlist_id = playlist_id


class TagsTreeLoaderWorker(QThread):
    """Worker to fetch tags tree and track counts asynchronously."""

    success = Signal(list)  # tree
    error = Signal(str)

    def __init__(
        self,
        engine: Engine,
        include_deleted: bool = False,
        category_names: Any = ("Genre", "Style"),
        parent: Optional[Any] = None,
    ):
        super().__init__(parent)
        self.engine = engine
        self.include_deleted = include_deleted
        self.category_names = category_names

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                return
            tree = fetch_tags_tree(
                self.engine,
                include_deleted=self.include_deleted,
                category_names=self.category_names,
            )
            if self.isInterruptionRequested():
                return
            self.success.emit(tree)
        except Exception as e:
            if not self.isInterruptionRequested():
                self.error.emit(str(e))


class TagSortWorker(QThread):
    """Worker to sort child tags inside a folder asynchronously without UI freeze."""

    success = Signal(int)  # number of sorted tags
    error = Signal(str)

    def __init__(
        self,
        engine: Engine,
        folder_id: str,
        descending: bool = False,
        parent: Optional[Any] = None,
    ):
        super().__init__(parent)
        self.engine = engine
        self.folder_id = folder_id
        self.descending = descending

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                return
            count = sort_tags_in_folder(
                self.engine, self.folder_id, descending=self.descending
            )
            if self.isInterruptionRequested():
                return
            self.success.emit(count)
        except Exception as e:
            if not self.isInterruptionRequested():
                self.error.emit(str(e))


class TagTracksWorker(CategoryTracksWorker):
    """Worker to query and populate tag tracks without UI freeze."""

    def __init__(
        self,
        engine: Engine,
        tag_id: str,
        query: str = "",
        limit: Optional[int] = None,
        sort_by: Optional[str] = None,
        sort_order: str = "asc",
        exclude_streaming: bool = True,
        parent: Optional[Any] = None,
    ):
        super().__init__(
            engine=engine,
            category_id=tag_id,
            fetch_fn=fetch_tag_tracks,
            query=query,
            limit=limit,
            sort_by=sort_by,
            sort_order=sort_order,
            exclude_streaming=exclude_streaming,
            parent=parent,
        )
        self.tag_id = tag_id


class SyncWorker(QObject):
    """Background worker to run Turso cloud sync without freezing the GUI.

    Executes via a daemon thread with Qt Signal bridges, avoiding C++ QThread
    destruction assertions on application exit while HTTP network calls are in flight.
    """

    success = Signal(object)  # SyncResult
    error = Signal(str)
    progress = Signal(str, int, int)  # (stage, current, total)
    finished = Signal()

    def __init__(
        self,
        db_path: Optional[str] = None,
        engine: Optional[Engine] = None,
        force: bool = False,
        create_backup: bool = True,
        parent: Optional[Any] = None,
    ):
        super().__init__(parent)
        self.db_path = db_path
        self.engine = engine
        self.force = force
        self.create_backup = create_backup
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def run(self) -> None:
        """Execute directly in the caller's thread (useful for testing)."""
        self._run()

    def isRunning(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def isInterruptionRequested(self) -> bool:
        return self._stop_event.is_set()

    def requestInterruption(self) -> None:
        self._stop_event.set()

    def quit(self) -> None:
        self.requestInterruption()

    def wait(self, timeout_ms: int = 0) -> None:
        if self._thread and self._thread.is_alive():
            timeout_sec = timeout_ms / 1000.0 if timeout_ms > 0 else None
            self._thread.join(timeout=timeout_sec)

    def _run(self) -> None:
        try:
            from rekordbox_collection_manager.turso_sync import run_sync_cycle

            def _on_progress(stage: str, current: int, total: int) -> None:
                if not self.isInterruptionRequested():
                    self.progress.emit(stage, current, total)

            res = run_sync_cycle(
                db_path=self.db_path,
                engine=self.engine,
                force=self.force,
                create_backup=self.create_backup,
                progress_cb=_on_progress,
                cancel_fn=self.isInterruptionRequested,
            )
            if self.isInterruptionRequested():
                return
            if res.success:
                self.success.emit(res)
            else:
                self.error.emit(res.message)
        except Exception as e:
            if not self.isInterruptionRequested():
                self.error.emit(str(e))
        finally:
            self.finished.emit()

