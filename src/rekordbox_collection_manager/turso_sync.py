"""Turso Cloud Sync Engine for Rekordbox.

Uses Turso HTTP Pipeline API for fast, synchronous, non-blocking sync.
Handles schema initialization, collection export (tracks, playlists, tags),
and ingesting changelog events from Turso back into master.db.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import requests
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rekordbox_collection_manager.backup_restore import create_database_backup
from rekordbox_collection_manager.utils import (
    get_default_master_db_path,
    get_rekordbox_timestamp,
    is_rekordbox_running,
    open_database,
)

logger = logging.getLogger(__name__)


def is_turso_configured() -> bool:
    """Check if Turso cloud database credentials are configured in env or .env."""
    try:
        url, token = get_turso_credentials()
        return bool(url and token)
    except Exception:
        return False


def get_turso_credentials() -> Tuple[str, str]:
    """Retrieve Turso database URL and Auth Token from env or .env file."""
    url = os.environ.get("TURSO_DATABASE_URL")
    token = os.environ.get("TURSO_AUTH_TOKEN")

    if not url or not token:
        # Check .env file in workspace root
        env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env")
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        if k == "TURSO_DATABASE_URL" and not url:
                            url = v
                        elif k == "TURSO_AUTH_TOKEN" and not token:
                            token = v

    if not url or not token:
        raise ValueError(
            "TURSO_DATABASE_URL and TURSO_AUTH_TOKEN must be set in environment or .env file."
        )

    if url.startswith("libsql://"):
        url = "https://" + url[len("libsql://") :]

    return url.rstrip("/"), token


def _to_turso_arg(val: Any) -> Dict[str, Any]:
    """Convert a Python value to Turso's JSON parameter format."""
    if val is None:
        return {"type": "null"}
    if isinstance(val, bool):
        return {"type": "integer", "value": "1" if val else "0"}
    if isinstance(val, int):
        return {"type": "integer", "value": str(val)}
    if isinstance(val, float):
        return {"type": "float", "value": val}
    return {"type": "text", "value": str(val)}


def _from_turso_row(row_data: List[Dict[str, Any]]) -> Tuple[Any, ...]:
    """Convert Turso row JSON format to a standard Python tuple."""
    res = []
    for item in row_data:
        t = item.get("type")
        v = item.get("value")
        if t == "null":
            res.append(None)
        elif t == "integer":
            res.append(int(v))
        elif t == "float":
            res.append(float(v))
        else:
            res.append(v)
    return tuple(res)


class TursoQueryResult:
    def __init__(self, cols: List[str], rows: List[Tuple[Any, ...]]):
        self.cols = cols
        self.rows = rows


class TursoHttpClient:
    """Synchronous HTTP client for Turso database using the /v2/pipeline API."""

    def __init__(self, url: str, token: str):
        self.endpoint = f"{url}/v2/pipeline"
        self.token = token
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        })

    def execute(self, sql: str, args: Optional[List[Any]] = None) -> TursoQueryResult:
        """Execute a single SQL statement."""
        stmt: Dict[str, Any] = {"sql": sql}
        if args:
            stmt["args"] = [_to_turso_arg(a) for a in args]

        body = {
            "requests": [
                {"type": "execute", "stmt": stmt},
                {"type": "close"},
            ]
        }
        resp = self.session.post(self.endpoint, json=body, timeout=15)
        if resp.status_code != 200:
            raise RuntimeError(f"Turso HTTP error ({resp.status_code}): {resp.text}")

        data = resp.json()
        results = data.get("results", [])
        if not results:
            return TursoQueryResult([], [])

        first = results[0]
        if first.get("type") == "error":
            raise RuntimeError(f"Turso query error: {first.get('error', {}).get('message')}")

        result_data = first.get("response", {}).get("result", {})
        cols = [c["name"] for c in result_data.get("cols", [])]
        raw_rows = result_data.get("rows", [])
        rows = [_from_turso_row(r) for r in raw_rows]
        return TursoQueryResult(cols, rows)

    def batch(self, statements: List[Tuple[str, Optional[List[Any]]]]) -> None:
        """Execute a list of SQL statements in a single HTTP request."""
        if not statements:
            return

        requests_list = []
        for sql, args in statements:
            stmt: Dict[str, Any] = {"sql": sql}
            if args:
                stmt["args"] = [_to_turso_arg(a) for a in args]
            requests_list.append({"type": "execute", "stmt": stmt})

        requests_list.append({"type": "close"})

        resp = self.session.post(self.endpoint, json={"requests": requests_list}, timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(f"Turso HTTP batch error ({resp.status_code}): {resp.text}")

        data = resp.json()
        for res in data.get("results", []):
            if res.get("type") == "error":
                raise RuntimeError(f"Turso batch execution error: {res.get('error', {}).get('message')}")


def create_turso_client() -> TursoHttpClient:
    """Create a synchronous Turso HTTP client."""
    url, token = get_turso_credentials()
    return TursoHttpClient(url=url, token=token)


def init_turso_schema(client: TursoHttpClient) -> None:
    """Initialize the schema in Turso for the mobile companion app."""
    statements = [
        # 1. Tracks table
        (
            """
            CREATE TABLE IF NOT EXISTS tracks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                artist TEXT,
                album TEXT,
                genre TEXT,
                bpm REAL,
                key_name TEXT,
                rating INTEGER DEFAULT 0,
                comment TEXT,
                color_id INTEGER DEFAULT 0,
                dj_play_count INTEGER DEFAULT 0,
                date_added TEXT,
                updated_at TEXT
            );
            """,
            None,
        ),
        ("CREATE INDEX IF NOT EXISTS idx_tracks_title ON tracks(title);", None),
        ("CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks(artist);", None),
        # 2. Playlists table
        (
            """
            CREATE TABLE IF NOT EXISTS playlists (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                parent_id TEXT,
                seq INTEGER DEFAULT 0,
                is_folder INTEGER DEFAULT 0,
                attribute INTEGER DEFAULT 0,
                updated_at TEXT
            );
            """,
            None,
        ),
        # 3. Playlist Tracks mapping
        (
            """
            CREATE TABLE IF NOT EXISTS playlist_tracks (
                id TEXT PRIMARY KEY,
                playlist_id TEXT NOT NULL,
                track_id TEXT NOT NULL,
                track_no INTEGER DEFAULT 0
            );
            """,
            None,
        ),
        ("CREATE INDEX IF NOT EXISTS idx_pt_playlist ON playlist_tracks(playlist_id);", None),
        ("CREATE INDEX IF NOT EXISTS idx_pt_track ON playlist_tracks(track_id);", None),
        # 4. Tags table
        (
            """
            CREATE TABLE IF NOT EXISTS tags (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                parent_id TEXT,
                seq INTEGER DEFAULT 0,
                updated_at TEXT
            );
            """,
            None,
        ),
        # 5. Track Tags mapping
        (
            """
            CREATE TABLE IF NOT EXISTS track_tags (
                id TEXT PRIMARY KEY,
                track_id TEXT NOT NULL,
                tag_id TEXT NOT NULL
            );
            """,
            None,
        ),
        ("CREATE INDEX IF NOT EXISTS idx_tt_track ON track_tags(track_id);", None),
        ("CREATE INDEX IF NOT EXISTS idx_tt_tag ON track_tags(tag_id);", None),
        # 6. Changelog Queue (Events written by iOS, ingested by Mac)
        (
            """
            CREATE TABLE IF NOT EXISTS changelog_events (
                id TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                created_at TEXT NOT NULL,
                processed_at TEXT,
                error_message TEXT
            );
            """,
            None,
        ),
        ("CREATE INDEX IF NOT EXISTS idx_events_status ON changelog_events(status, created_at);", None),
    ]
    client.batch(statements)


def export_collection_to_turso(
    engine: Engine,
    client: TursoHttpClient,
    progress_cb: Optional[Callable[[str, int, int], None]] = None,
) -> Dict[str, int]:
    """Export tracks, playlists, and tags from local Rekordbox master.db to Turso."""
    init_turso_schema(client)

    stats = {
        "tracks": 0,
        "playlists": 0,
        "playlist_tracks": 0,
        "tags": 0,
        "track_tags": 0,
    }

    with engine.connect() as conn:
        all_tables_res = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table';"
        ).fetchall()
        existing_tables = {r[0] for r in all_tables_res}

        # ----------------------------------------------------
        # 1. Export Tracks
        # ----------------------------------------------------
        if "djmdContent" in existing_tables:
            content_cols = {
                r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdContent);").fetchall()
            }
            del_clause = (
                "WHERE (c.rb_local_deleted = 0 OR c.rb_local_deleted IS NULL)"
                if "rb_local_deleted" in content_cols
                else ""
            )

            artist_join = (
                "LEFT JOIN djmdArtist a ON c.ArtistID = a.ID"
                if "djmdArtist" in existing_tables
                else ""
            )
            artist_col = (
                "COALESCE(a.Name, c.SrcArtistName, '')"
                if "djmdArtist" in existing_tables
                else "COALESCE(c.SrcArtistName, '')"
            )

            album_join = (
                "LEFT JOIN djmdAlbum al ON c.AlbumID = al.ID"
                if "djmdAlbum" in existing_tables
                else ""
            )
            album_col = "COALESCE(al.Name, '')" if "djmdAlbum" in existing_tables else "''"

            genre_join = (
                "LEFT JOIN djmdGenre g ON c.GenreID = g.ID"
                if "djmdGenre" in existing_tables
                else ""
            )
            genre_col = "COALESCE(g.Name, '')" if "djmdGenre" in existing_tables else "''"

            key_join = (
                "LEFT JOIN djmdKey k ON c.KeyID = k.ID" if "djmdKey" in existing_tables else ""
            )
            key_col = "COALESCE(k.ScaleName, '')" if "djmdKey" in existing_tables else "''"

            rating_col = "c.Rating" if "Rating" in content_cols else "0"
            comment_col = "COALESCE(c.Commnt, '')" if "Commnt" in content_cols else "''"
            color_col = "c.ColorID" if "ColorID" in content_cols else "0"
            bpm_col = "CAST(c.BPM AS REAL) / 100.0" if "BPM" in content_cols or "Bpm" in content_cols else "0.0"
            playcount_col = "c.DJPlayCount" if "DJPlayCount" in content_cols else "0"
            date_added_col = "COALESCE(c.created_at, '')" if "created_at" in content_cols else "''"
            updated_at_col = "COALESCE(c.updated_at, '')" if "updated_at" in content_cols else "''"

            sql = f"""
                SELECT 
                    c.ID, 
                    COALESCE(c.Title, '') AS Title,
                    {artist_col} AS Artist,
                    {album_col} AS Album,
                    {genre_col} AS Genre,
                    {bpm_col} AS Bpm,
                    {key_col} AS KeyName,
                    {rating_col} AS Rating,
                    {comment_col} AS Comment,
                    {color_col} AS ColorID,
                    {playcount_col} AS DJPlayCount,
                    {date_added_col} AS DateAdded,
                    {updated_at_col} AS UpdatedAt
                FROM djmdContent c
                {artist_join}
                {album_join}
                {genre_join}
                {key_join}
                {del_clause};
            """
            rows = conn.exec_driver_sql(sql).fetchall()
            total_tracks = len(rows)

            batch_size = 100
            for i in range(0, total_tracks, batch_size):
                chunk = rows[i : i + batch_size]
                stmts = []
                for r in chunk:
                    stmts.append((
                        """
                        INSERT OR REPLACE INTO tracks 
                        (id, title, artist, album, genre, bpm, key_name, rating, comment, color_id, dj_play_count, date_added, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        [
                            str(r[0]),
                            str(r[1]),
                            str(r[2]),
                            str(r[3]),
                            str(r[4]),
                            float(r[5] or 0.0),
                            str(r[6]),
                            int(r[7] or 0),
                            str(r[8]),
                            int(r[9] or 0),
                            int(r[10] or 0),
                            str(r[11]),
                            str(r[12]),
                        ],
                    ))
                client.batch(stmts)
                stats["tracks"] += len(chunk)
                if progress_cb:
                    progress_cb("tracks", stats["tracks"], total_tracks)

        # ----------------------------------------------------
        # 2. Export Playlists
        # ----------------------------------------------------
        if "djmdPlaylist" in existing_tables:
            pl_cols = {
                r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdPlaylist);").fetchall()
            }
            del_clause = (
                "WHERE (rb_local_deleted = 0 OR rb_local_deleted IS NULL)"
                if "rb_local_deleted" in pl_cols
                else ""
            )

            parent_col = "ParentID" if "ParentID" in pl_cols else "NULL"
            seq_col = "Seq" if "Seq" in pl_cols else "0"
            attr_col = "Attribute" if "Attribute" in pl_cols else "0"
            updated_col = "updated_at" if "updated_at" in pl_cols else "''"

            sql = f"""
                SELECT ID, Name, {parent_col}, {seq_col}, {attr_col}, {updated_col}
                FROM djmdPlaylist
                {del_clause};
            """
            pl_rows = conn.exec_driver_sql(sql).fetchall()
            pl_stmts = []
            for r in pl_rows:
                is_folder = 1 if r[4] == 1 else 0
                pl_stmts.append((
                    """
                    INSERT OR REPLACE INTO playlists (id, name, parent_id, seq, is_folder, attribute, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?);
                    """,
                    [
                        str(r[0]),
                        str(r[1]),
                        str(r[2]) if r[2] else None,
                        int(r[3] or 0),
                        is_folder,
                        int(r[4] or 0),
                        str(r[5] or ""),
                    ],
                ))
            if pl_stmts:
                for i in range(0, len(pl_stmts), 100):
                    client.batch(pl_stmts[i : i + 100])
                stats["playlists"] = len(pl_stmts)

        # ----------------------------------------------------
        # 3. Export Playlist Tracks
        # ----------------------------------------------------
        if "djmdSongPlaylist" in existing_tables:
            sp_cols = {
                r[1]
                for r in conn.exec_driver_sql("PRAGMA table_info(djmdSongPlaylist);").fetchall()
            }
            del_clause = (
                "WHERE (rb_local_deleted = 0 OR rb_local_deleted IS NULL)"
                if "rb_local_deleted" in sp_cols
                else ""
            )
            track_no_col = "TrackNo" if "TrackNo" in sp_cols else "0"

            sql = f"""
                SELECT ID, PlaylistID, ContentID, {track_no_col}
                FROM djmdSongPlaylist
                {del_clause};
            """
            sp_rows = conn.exec_driver_sql(sql).fetchall()
            sp_stmts = []
            for r in sp_rows:
                sp_stmts.append((
                    """
                    INSERT OR REPLACE INTO playlist_tracks (id, playlist_id, track_id, track_no)
                    VALUES (?, ?, ?, ?);
                    """,
                    [str(r[0]), str(r[1]), str(r[2]), int(r[3] or 0)],
                ))
            if sp_stmts:
                for i in range(0, len(sp_stmts), 100):
                    client.batch(sp_stmts[i : i + 100])
                stats["playlist_tracks"] = len(sp_stmts)

        # ----------------------------------------------------
        # 4. Export Tags & Track Tags
        # ----------------------------------------------------
        if "djmdMyTag" in existing_tables:
            tag_cols = {
                r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdMyTag);").fetchall()
            }
            del_clause = (
                "WHERE (rb_local_deleted = 0 OR rb_local_deleted IS NULL)"
                if "rb_local_deleted" in tag_cols
                else ""
            )
            parent_col = "ParentID" if "ParentID" in tag_cols else "NULL"
            seq_col = "Seq" if "Seq" in tag_cols else "0"
            updated_col = "updated_at" if "updated_at" in tag_cols else "''"

            sql = f"""
                SELECT ID, Name, {parent_col}, {seq_col}, {updated_col}
                FROM djmdMyTag
                {del_clause};
            """
            tag_rows = conn.exec_driver_sql(sql).fetchall()
            tag_stmts = []
            for r in tag_rows:
                tag_stmts.append((
                    """
                    INSERT OR REPLACE INTO tags (id, name, parent_id, seq, updated_at)
                    VALUES (?, ?, ?, ?, ?);
                    """,
                    [
                        str(r[0]),
                        str(r[1]),
                        str(r[2]) if r[2] else None,
                        int(r[3] or 0),
                        str(r[4] or ""),
                    ],
                ))
            if tag_stmts:
                for i in range(0, len(tag_stmts), 100):
                    client.batch(tag_stmts[i : i + 100])
                stats["tags"] = len(tag_stmts)

        if "djmdSongMyTag" in existing_tables:
            smt_cols = {
                r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdSongMyTag);").fetchall()
            }
            del_clause = (
                "WHERE (rb_local_deleted = 0 OR rb_local_deleted IS NULL)"
                if "rb_local_deleted" in smt_cols
                else ""
            )

            sql = f"""
                SELECT ID, ContentID, MyTagID
                FROM djmdSongMyTag
                {del_clause};
            """
            smt_rows = conn.exec_driver_sql(sql).fetchall()
            smt_stmts = []
            for r in smt_rows:
                smt_stmts.append((
                    """
                    INSERT OR REPLACE INTO track_tags (id, track_id, tag_id)
                    VALUES (?, ?, ?);
                    """,
                    [str(r[0]), str(r[1]), str(r[2])],
                ))
            if smt_stmts:
                for i in range(0, len(smt_stmts), 100):
                    client.batch(smt_stmts[i : i + 100])
                stats["track_tags"] = len(smt_stmts)

    return stats


def apply_changelog_events(engine: Engine, client: TursoHttpClient) -> int:
    """Read pending events from Turso changelog_events and apply them to local master.db."""
    try:
        res = client.execute(
            "SELECT id, action, payload, created_at FROM changelog_events WHERE status = 'PENDING' ORDER BY created_at ASC;"
        )
    except Exception as e:
        logger.debug("No changelog_events table or query error: %s", e)
        return 0

    events = res.rows
    if not events:
        return 0

    logger.info("Found %d pending changelog events to apply", len(events))
    processed_count = 0

    with engine.begin() as conn:
        for event in events:
            event_id = str(event[0])
            action = str(event[1]).upper()
            payload_str = str(event[2])

            try:
                payload = json.loads(payload_str)
            except Exception as e:
                _mark_event_status(client, event_id, "FAILED", f"Invalid JSON payload: {e}")
                continue

            try:
                now_str = get_rekordbox_timestamp()

                if action in ("RENAME_TRACK", "UPDATE_TRACK"):
                    track_id = payload.get("track_id")
                    if not track_id:
                        raise ValueError("Missing 'track_id' in payload")

                    updates = []
                    params = {"track_id": track_id, "updated_at": now_str}

                    if "title" in payload:
                        updates.append("Title = :title")
                        params["title"] = payload["title"]
                    if "comment" in payload:
                        updates.append("Commnt = :comment")
                        params["comment"] = payload["comment"]
                    if "rating" in payload:
                        updates.append("Rating = :rating")
                        params["rating"] = int(payload["rating"])
                    if "color_id" in payload:
                        updates.append("ColorID = :color_id")
                        params["color_id"] = int(payload["color_id"])

                    if updates:
                        updates.append("updated_at = :updated_at")
                        sql = f"UPDATE djmdContent SET {', '.join(updates)} WHERE ID = :track_id;"
                        conn.exec_driver_sql(sql, params)

                elif action == "ADD_TO_PLAYLIST":
                    playlist_id = payload.get("playlist_id")
                    track_id = payload.get("track_id")
                    if not playlist_id or not track_id:
                        raise ValueError("Missing 'playlist_id' or 'track_id'")

                    max_no = conn.exec_driver_sql(
                        "SELECT MAX(TrackNo) FROM djmdSongPlaylist WHERE PlaylistID = :pl;",
                        {"pl": playlist_id},
                    ).scalar() or 0
                    next_no = max_no + 1

                    max_id = conn.exec_driver_sql("SELECT MAX(CAST(ID AS INTEGER)) FROM djmdSongPlaylist;").scalar() or 0
                    new_sp_id = str(max_id + 1)

                    conn.exec_driver_sql(
                        """
                        INSERT INTO djmdSongPlaylist (ID, PlaylistID, ContentID, TrackNo, created_at, updated_at, rb_local_deleted)
                        VALUES (:id, :pl, :track, :track_no, :now, :now, 0);
                        """,
                        {
                            "id": new_sp_id,
                            "pl": playlist_id,
                            "track": track_id,
                            "track_no": next_no,
                            "now": now_str,
                        },
                    )

                elif action == "REMOVE_FROM_PLAYLIST":
                    playlist_id = payload.get("playlist_id")
                    track_id = payload.get("track_id")
                    if not playlist_id or not track_id:
                        raise ValueError("Missing 'playlist_id' or 'track_id'")

                    conn.exec_driver_sql(
                        """
                        UPDATE djmdSongPlaylist 
                        SET rb_local_deleted = 1, updated_at = :now
                        WHERE PlaylistID = :pl AND ContentID = :track;
                        """,
                        {"pl": playlist_id, "track": track_id, "now": now_str},
                    )

                elif action == "CREATE_PLAYLIST":
                    name = payload.get("name")
                    parent_id = payload.get("parent_id")
                    if not name:
                        raise ValueError("Missing playlist 'name'")

                    max_id = conn.exec_driver_sql("SELECT MAX(CAST(ID AS INTEGER)) FROM djmdPlaylist;").scalar() or 0
                    new_pl_id = str(max_id + 1)
                    max_seq = conn.exec_driver_sql("SELECT MAX(Seq) FROM djmdPlaylist;").scalar() or 0

                    conn.exec_driver_sql(
                        """
                        INSERT INTO djmdPlaylist (ID, Name, ParentID, Seq, Attribute, created_at, updated_at, rb_local_deleted)
                        VALUES (:id, :name, :parent, :seq, 0, :now, :now, 0);
                        """,
                        {
                            "id": new_pl_id,
                            "name": name,
                            "parent": parent_id,
                            "seq": max_seq + 1,
                            "now": now_str,
                        },
                    )

                elif action == "SET_TRACK_TAGS":
                    track_id = payload.get("track_id")
                    tag_ids = payload.get("tag_ids", [])
                    if not track_id:
                        raise ValueError("Missing 'track_id'")

                    conn.exec_driver_sql(
                        """
                        UPDATE djmdSongMyTag 
                        SET rb_local_deleted = 1, updated_at = :now 
                        WHERE ContentID = :track;
                        """,
                        {"track": track_id, "now": now_str},
                    )

                    for tid in tag_ids:
                        max_id = conn.exec_driver_sql("SELECT MAX(CAST(ID AS INTEGER)) FROM djmdSongMyTag;").scalar() or 0
                        conn.exec_driver_sql(
                            """
                            INSERT INTO djmdSongMyTag (ID, ContentID, MyTagID, created_at, updated_at, rb_local_deleted)
                            VALUES (:id, :track, :tag, :now, :now, 0);
                            """,
                            {"id": str(max_id + 1), "track": track_id, "tag": str(tid), "now": now_str},
                        )

                else:
                    raise ValueError(f"Unknown action: {action}")

                _mark_event_status(client, event_id, "PROCESSED")
                processed_count += 1

            except Exception as e:
                logger.error("Failed to apply event %s: %s", event_id, e)
                _mark_event_status(client, event_id, "FAILED", str(e))

    return processed_count


def _mark_event_status(client: TursoHttpClient, event_id: str, status: str, error_msg: Optional[str] = None) -> None:
    """Update event status in Turso."""
    now_str = datetime.now(timezone.utc).isoformat()
    client.execute(
        """
        UPDATE changelog_events 
        SET status = ?, processed_at = ?, error_message = ? 
        WHERE id = ?;
        """,
        [status, now_str, error_msg, event_id],
    )


class SyncResult:
    """Result of a Turso cloud sync cycle."""

    def __init__(
        self,
        success: bool,
        applied_events: int = 0,
        stats: Optional[Dict[str, int]] = None,
        message: str = "",
    ):
        self.success = success
        self.applied_events = applied_events
        self.stats = stats or {}
        self.message = message

    def __bool__(self) -> bool:
        return self.success

    def __repr__(self) -> str:
        return (
            f"<SyncResult success={self.success} applied_events={self.applied_events} "
            f"stats={self.stats} message='{self.message}'>"
        )


def run_sync_cycle(
    db_path: Optional[str] = None,
    engine: Optional[Engine] = None,
    force: bool = False,
    create_backup: bool = True,
    progress_cb: Optional[Callable[[str, int, int], None]] = None,
) -> SyncResult:
    """Execute a single sync cycle.

    1. Checks if Turso credentials are configured.
    2. If Rekordbox is running and force=False, skips to avoid database lock/conflict.
    3. Connects to Turso client.
    4. Creates a local safety backup if create_backup=True.
    5. Ingests and applies pending changelog events from Turso into master.db.
    6. Exports fresh collection snapshot (tracks, playlists, tags) to Turso.
    """
    if not is_turso_configured():
        logger.debug("Turso credentials not configured. Skipping cloud sync.")
        return SyncResult(success=False, message="Turso credentials not configured")

    if is_rekordbox_running() and not force:
        logger.info("Rekordbox is currently running. Sync deferred until Rekordbox closes.")
        return SyncResult(success=False, message="Rekordbox is currently running")

    resolved_db_path = db_path or get_default_master_db_path()
    if not resolved_db_path or not os.path.exists(resolved_db_path):
        logger.error("Could not locate Rekordbox master.db at: %s", resolved_db_path)
        return SyncResult(success=False, message=f"Could not locate Rekordbox master.db at {resolved_db_path}")

    logger.info("Starting Turso sync cycle for database: %s", resolved_db_path)

    # 1. Connect to Turso
    try:
        client = create_turso_client()
    except Exception as e:
        logger.error("Failed to connect to Turso: %s", e)
        return SyncResult(success=False, message=f"Failed to connect to Turso: {e}")

    # 2. Open database engine if not provided
    created_engine = False
    if engine is None:
        try:
            engine, _ = open_database(resolved_db_path)
            created_engine = True
        except Exception as e:
            logger.error("Failed to open master.db: %s", e)
            return SyncResult(success=False, message=f"Failed to open database: {e}")

    # 3. Create safety backup before modifying or exporting
    if create_backup:
        try:
            backup_res = create_database_backup(resolved_db_path, force=True)
            logger.info("Created safety backup at: %s", backup_res.get("backup_path") or backup_res.get("backup_folder"))
        except Exception as e:
            logger.warning("Backup warning (proceeding with caution): %s", e)

    # 4. Ingest pending changelog events from mobile (iOS)
    processed = 0
    try:
        processed = apply_changelog_events(engine, client)
        if processed > 0:
            logger.info("Successfully applied %d change events to master.db", processed)
        else:
            logger.info("No pending changelog events to apply.")
    except Exception as e:
        logger.error("Error applying changelog events: %s", e)

    # 5. Export fresh snapshot to Turso
    try:
        stats = export_collection_to_turso(engine, client, progress_cb=progress_cb)
        logger.info(
            "Export to Turso complete: %d tracks, %d playlists, %d tags synced.",
            stats.get("tracks", 0),
            stats.get("playlists", 0),
            stats.get("tags", 0),
        )
        return SyncResult(
            success=True,
            applied_events=processed,
            stats=stats,
            message="Cloud sync complete",
        )
    except Exception as e:
        logger.error("Error exporting to Turso: %s", e)
        return SyncResult(
            success=False,
            applied_events=processed,
            message=f"Error exporting to Turso: {e}",
        )
    finally:
        if created_engine and engine:
            try:
                engine.dispose()
            except Exception:
                pass
