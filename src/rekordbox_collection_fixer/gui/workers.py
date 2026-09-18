"""Background worker threads for non-blocking database operations."""

from typing import Any, Dict, List, Optional
from PySide6.QtCore import QThread, Signal
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rekordbox_collection_fixer.edit_play_count import (
    batch_update_track_play_counts,
    search_tracks,
    set_track_play_count,
    update_track_play_count,
)
from rekordbox_collection_fixer.rename_history import (
    batch_update_history_records,
    fetch_history_records,
    realign_history_order,
)
from rekordbox_collection_fixer.utils import (
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
            history_records = fetch_history_records(engine, history_table)

            # Count total tracks
            total_tracks = 0
            try:
                content_table = detect_content_table(engine)
                with engine.connect() as conn:
                    cols = {r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({content_table});").fetchall()}
                    del_filter = "WHERE (rb_local_deleted = 0 OR rb_local_deleted IS NULL)" if "rb_local_deleted" in cols else ""
                    row = conn.execute(text(f"SELECT count(*) FROM {content_table} {del_filter};")).fetchone()
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
        limit: int = 150,
        sort_by: str = "title",
        sort_order: str = "asc",
        parent: Optional[Any] = None,
    ):
        super().__init__(parent)
        self.engine = engine
        self.query = query
        self.limit = limit
        self.sort_by = sort_by
        self.sort_order = sort_order

    def run(self) -> None:
        try:
            records = search_tracks(
                self.engine,
                self.query,
                limit=self.limit,
                sort_by=self.sort_by,
                sort_order=self.sort_order,
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
                self.engine,
                self.updates,
                auto_realign=self.auto_realign,
            )
            self.success.emit(stats)
        except Exception as e:
            self.error.emit(str(e))


class HistoryRealignWorker(QThread):
    """Worker to realign sequence order of history sessions."""

    success = Signal(dict)  # stats
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


class TrackPlayCountWorker(QThread):
    """Worker to update or set play counts."""

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

