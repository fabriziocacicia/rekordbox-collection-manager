"""Background worker threads for non-blocking database operations."""

from typing import Any, Dict, List, Optional
from PySide6.QtCore import QThread, Signal
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rekordbox_collection_manager.edit_play_count import (
    batch_update_track_play_counts,
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


class DatabaseLoaderWorker(QThread):
    """Worker to resolve, open, and load initial database records."""

    success = Signal(object, str, list, int, str)  # engine, path, history_records, total_tracks, table_format
    error = Signal(str)

    def __init__(self, db_path: Optional[str] = None, parent: Optional[Any] = None):
        super().__init__(parent)
        self.db_path = db_path

    def run(self) -> None:
        try:
            resolved_path = resolve_db_path(self.db_path, allow_prompt=False)
            engine, _ = open_database(resolved_path)

            history_table = detect_history_table(engine)
            history_records = fetch_history_records(engine, history_table, include_deleted=True)

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

            self.success.emit(engine, resolved_path, history_records, total_tracks, history_table)
        except Exception as e:
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

    def run(self) -> None:
        try:
            records = search_tracks(
                self.engine,
                self.query,
                limit=self.limit,
                sort_by=self.sort_by,
                sort_order=self.sort_order,
                play_count_filter=self.play_count_filter,
                exclude_streaming=self.exclude_streaming,
            )
            self.success.emit(self.query, records)
        except Exception as e:
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
            stats = batch_update_history_records(
                self.engine, self.updates, auto_realign=self.auto_realign
            )
            self.success.emit(stats)
        except Exception as e:
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
            stats = realign_history_order(self.engine)
            self.success.emit(stats)
        except Exception as e:
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
            self.success.emit(self.track_id, old_count, new_count)
        except Exception as e:
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
            updated_count = batch_update_track_play_counts(self.engine, self.updates)
            self.success.emit(updated_count)
        except Exception as e:
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
            data = fetch_session_tracks(self.engine, self.history_id)
            self.success.emit(data)
        except Exception as e:
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
            data = fetch_track_full_details(
                self.engine,
                self.content_id,
                db_path=self.db_path,
            )
            self.success.emit(data)
        except Exception as e:
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
            from rekordbox_collection_manager.replace_file import inspect_audio_file

            info = inspect_audio_file(self.file_path)
            self.success.emit(info)
        except Exception as e:
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
            from rekordbox_collection_manager.replace_file import replace_track_audio_file

            res = replace_track_audio_file(
                self.engine,
                self.content_id,
                self.new_file_path,
                options=self.options,
            )
            self.success.emit(res)
        except Exception as e:
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
            from rekordbox_collection_manager.replace_file import merge_imported_tracks

            res = merge_imported_tracks(
                self.engine,
                self.source_content_id,
                self.target_content_id,
                options=self.options,
            )
            self.success.emit(res)
        except Exception as e:
            self.error.emit(str(e))


class BackupWorker(QThread):
    """Worker to create database backups in background without blocking UI."""

    success = Signal(dict)
    error = Signal(str)

    def __init__(
        self,
        db_path: Optional[str] = None,
        backup_dir: Optional[Any] = None,
        parent: Optional[Any] = None,
    ):
        super().__init__(parent)
        self.db_path = db_path
        self.backup_dir = backup_dir

    def run(self) -> None:
        try:
            from rekordbox_collection_manager.backup_restore import create_database_backup

            res = create_database_backup(db_path=self.db_path, backup_dir=self.backup_dir)
            self.success.emit(res)
        except Exception as e:
            self.error.emit(str(e))


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
            ok = soft_delete_history_session(self.engine, self.history_id)
            if ok:
                self.success.emit(self.history_id)
            else:
                self.error.emit(self.history_id, f"Session {self.history_id} could not be deleted.")
        except Exception as e:
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
            ok = restore_history_session(self.engine, self.history_id)
            if ok:
                self.success.emit(self.history_id)
            else:
                self.error.emit(self.history_id, f"Session {self.history_id} could not be restored.")
        except Exception as e:
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
            tree = fetch_playlists_tree(self.engine, include_deleted=self.include_deleted)
            self.success.emit(tree)
        except Exception as e:
            self.error.emit(str(e))


class PlaylistTracksWorker(QThread):
    """Worker to query and populate playlist tracks without UI freeze."""

    success = Signal(str, list)  # playlist_id, records
    error = Signal(str)

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
        super().__init__(parent)
        self.engine = engine
        self.playlist_id = playlist_id
        self.query = query
        self.limit = limit
        self.sort_by = sort_by
        self.sort_order = sort_order
        self.exclude_streaming = exclude_streaming

    def run(self) -> None:
        try:
            tracks = fetch_playlist_tracks(
                self.engine,
                self.playlist_id,
                query=self.query,
                limit=self.limit,
                sort_by=self.sort_by,
                sort_order=self.sort_order,
                exclude_streaming=self.exclude_streaming,
            )
            self.success.emit(self.playlist_id, tracks)
        except Exception as e:
            self.error.emit(str(e))


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
            tree = fetch_tags_tree(
                self.engine,
                include_deleted=self.include_deleted,
                category_names=self.category_names,
            )
            self.success.emit(tree)
        except Exception as e:
            self.error.emit(str(e))


class TagTracksWorker(QThread):
    """Worker to query and populate tag tracks without UI freeze."""

    success = Signal(str, list)  # tag_id, records
    error = Signal(str)

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
        super().__init__(parent)
        self.engine = engine
        self.tag_id = tag_id
        self.query = query
        self.limit = limit
        self.sort_by = sort_by
        self.sort_order = sort_order
        self.exclude_streaming = exclude_streaming

    def run(self) -> None:
        try:
            tracks = fetch_tag_tracks(
                self.engine,
                self.tag_id,
                query=self.query,
                limit=self.limit,
                sort_by=self.sort_by,
                sort_order=self.sort_order,
                exclude_streaming=self.exclude_streaming,
            )
            self.success.emit(self.tag_id, tracks)
        except Exception as e:
            self.error.emit(str(e))

