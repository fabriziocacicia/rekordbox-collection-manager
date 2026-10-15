"""Turso Cloud Sync Engine for Rekordbox.

Uses Turso HTTP Pipeline API for fast, synchronous, non-blocking sync.
Handles schema initialization, collection export (tracks, playlists, tags),
and ingesting changelog events from Turso back into master.db.
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import requests
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rekordbox_collection_manager.backup_restore import create_database_backup
from rekordbox_collection_manager.utils import (
    build_exclude_streaming_clause,
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


def _get_sync_state_path() -> str:
    """Path to the local sync state cache file."""
    config_dir = os.path.expanduser("~/.config/rekordbox-collection-manager")
    os.makedirs(config_dir, exist_ok=True)
    return os.path.join(config_dir, "sync_state.json")


def get_last_sync_state() -> Dict[str, Any]:
    """Retrieve the cached database state from the last successful cloud sync."""
    path = _get_sync_state_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_sync_state(db_path: str, stats: Optional[Dict[str, int]] = None) -> None:
    """Save the database mtime, size, and sync timestamp upon successful sync."""
    path = _get_sync_state_path()
    try:
        abs_path = os.path.abspath(db_path)
        state = {
            "db_path": abs_path,
            "db_mtime": os.path.getmtime(abs_path) if os.path.exists(abs_path) else 0,
            "db_size": os.path.getsize(abs_path) if os.path.exists(abs_path) else 0,
            "last_sync_at": datetime.now(timezone.utc).isoformat(),
            "stats": stats or {},
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.warning("Could not save sync state: %s", e)


def _build_multi_row_insert(
    table: str, cols: List[str], rows: List[List[Any]]
) -> Tuple[str, List[Any]]:
    """Build a multi-row INSERT OR REPLACE statement for high-throughput batching."""
    if not rows:
        return "", []
    cols_str = ", ".join(cols)
    single_placeholder = "(" + ", ".join(["?"] * len(cols)) + ")"
    all_placeholders = ", ".join([single_placeholder] * len(rows))
    sql = f"INSERT OR REPLACE INTO {table} ({cols_str}) VALUES {all_placeholders};"
    params: List[Any] = []
    for r in rows:
        params.extend(r)
    return sql, params


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
        resp = self.session.post(self.endpoint, json=body, timeout=30)
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
        # 7. History sessions metadata (for mobile autocompletion & session records)
        (
            """
            CREATE TABLE IF NOT EXISTS history_sessions (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                date_created TEXT,
                location TEXT,
                collaborators TEXT,
                track_count INTEGER DEFAULT 0,
                updated_at TEXT
            );
            """,
            None,
        ),
        ("CREATE INDEX IF NOT EXISTS idx_hs_loc ON history_sessions(location);", None),
        ("CREATE INDEX IF NOT EXISTS idx_hs_collab ON history_sessions(collaborators);", None),
    ]
    client.batch(statements)


def export_collection_to_turso(
    engine: Engine,
    client: TursoHttpClient,
    progress_cb: Optional[Callable[[str, int, int], None]] = None,
    cancel_fn: Optional[Callable[[], bool]] = None,
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
        # 1. Fetch Tracks Data
        # ----------------------------------------------------
        rows = []
        if "djmdContent" in existing_tables:
            content_cols = {
                r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdContent);").fetchall()
            }
            where_parts = []
            if "rb_local_deleted" in content_cols:
                where_parts.append("(c.rb_local_deleted = 0 OR c.rb_local_deleted IS NULL)")
            streaming_clause = build_exclude_streaming_clause(content_cols, table_alias="c")
            if streaming_clause:
                where_parts.append(streaming_clause.removeprefix("AND ").strip())
            where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

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
                {where_sql};
            """
            rows = conn.exec_driver_sql(sql).fetchall()

        # ----------------------------------------------------
        # 2. Fetch Playlists Data
        # ----------------------------------------------------
        pl_rows = []
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

        # ----------------------------------------------------
        # 3. Fetch Playlist Tracks Data
        # ----------------------------------------------------
        all_pt_rows = []
        if "djmdSongPlaylist" in existing_tables:
            sp_cols = {
                r[1]
                for r in conn.exec_driver_sql("PRAGMA table_info(djmdSongPlaylist);").fetchall()
            }
            track_no_col = "sp.TrackNo" if "TrackNo" in sp_cols else "0"
            sp_streaming = (
                build_exclude_streaming_clause(content_cols, table_alias="c")
                if "djmdContent" in existing_tables
                else ""
            )

            sql = f"""
                SELECT sp.ID, sp.PlaylistID, sp.ContentID, {track_no_col}
                FROM djmdSongPlaylist sp
                JOIN djmdContent c ON sp.ContentID = c.ID
                WHERE (sp.rb_local_deleted = 0 OR sp.rb_local_deleted IS NULL)
                  AND (c.rb_local_deleted = 0 OR c.rb_local_deleted IS NULL)
                  {sp_streaming};
            """
            sp_rows = conn.exec_driver_sql(sql).fetchall()
            for r in sp_rows:
                all_pt_rows.append([str(r[0]), str(r[1]), str(r[2]), int(r[3] or 0)])

            # Also resolve and export tracks for dynamic Smart Playlists (Attribute == 4)
            if "djmdPlaylist" in existing_tables and "djmdContent" in existing_tables:
                from rekordbox_collection_manager.playlists import parse_smartlist_to_sql
                smart_pl_rows = conn.exec_driver_sql(
                    "SELECT ID, SmartList FROM djmdPlaylist WHERE Attribute = 4 AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL);"
                ).fetchall()
                for spl in smart_pl_rows:
                    pl_id, smart_xml = str(spl[0]), str(spl[1]) if spl[1] else None
                    if not smart_xml:
                        continue
                    clause, p_dict = parse_smartlist_to_sql(smart_xml)
                    if not clause:
                        continue
                    smart_sql = f"""
                        SELECT c.ID
                        FROM djmdContent c
                        WHERE {clause}
                          AND (c.rb_local_deleted = 0 OR c.rb_local_deleted IS NULL)
                          {sp_streaming}
                        ORDER BY c.Title COLLATE NOCASE;
                    """
                    try:
                        matching_tracks = conn.execute(text(smart_sql), p_dict).fetchall()
                        for idx, t_row in enumerate(matching_tracks, 1):
                            track_id = str(t_row[0])
                            synthetic_id = f"smart_{pl_id}_{track_id}"
                            all_pt_rows.append([synthetic_id, pl_id, track_id, idx])
                    except Exception as e:
                        logger.warning("Could not resolve tracks for smart playlist %s: %s", pl_id, e)

        # ----------------------------------------------------
        # 4. Fetch Tags & Track Tags Data
        # ----------------------------------------------------
        tag_rows = []
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

        smt_rows = []
        if "djmdSongMyTag" in existing_tables:
            smt_cols = {
                r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdSongMyTag);").fetchall()
            }
            smt_streaming = (
                build_exclude_streaming_clause(content_cols, table_alias="c")
                if "djmdContent" in existing_tables
                else ""
            )

            sql = f"""
                SELECT smt.ID, smt.ContentID, smt.MyTagID
                FROM djmdSongMyTag smt
                JOIN djmdContent c ON smt.ContentID = c.ID
                WHERE (smt.rb_local_deleted = 0 OR smt.rb_local_deleted IS NULL)
                  AND (c.rb_local_deleted = 0 OR c.rb_local_deleted IS NULL)
                  {smt_streaming}
                GROUP BY smt.ContentID, smt.MyTagID;
            """
            smt_rows = conn.exec_driver_sql(sql).fetchall()

        # ----------------------------------------------------
        # 5. Fetch History Sessions Data
        # ----------------------------------------------------
        history_batch = []
        if "djmdHistory" in existing_tables:
            hist_cols = {
                r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdHistory);").fetchall()
            }
            del_clause = (
                "AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL)"
                if "rb_local_deleted" in hist_cols
                else ""
            )
            updated_col = "updated_at" if "updated_at" in hist_cols else "''"
            date_col = "DateCreated" if "DateCreated" in hist_cols else "''"
            h_rows = conn.exec_driver_sql(
                f"SELECT ID, Name, {date_col}, {updated_col} FROM djmdHistory WHERE (Attribute = 0 OR Attribute IS NULL) {del_clause};"
            ).fetchall()

            from rekordbox_collection_manager.utils import parse_history_name
            for hr in h_rows:
                hid, hname, hdate, hupd = str(hr[0]), str(hr[1] or ""), str(hr[2] or ""), str(hr[3] or "")
                _, hloc, hcollab = parse_history_name(hname)
                t_count = 0
                if "djmdSongHistory" in existing_tables:
                    t_count = conn.exec_driver_sql(
                        "SELECT COUNT(*) FROM djmdSongHistory WHERE HistoryID = :hid;", {"hid": hid}
                    ).scalar() or 0
                history_batch.append([hid, hname, hdate, hloc or None, hcollab or None, t_count, hupd])

    # ==============================================================================
    # Execute Turso Cloud Uploads (SQLite connection is now CLOSED - zero db locks)
    # ==============================================================================

    # 1. Export Tracks
    if "djmdContent" in existing_tables and rows:
        total_tracks = len(rows)
        logger.info("Exporting %d tracks to Turso...", total_tracks)
        track_cols = [
            "id", "title", "artist", "album", "genre", "bpm",
            "key_name", "rating", "comment", "color_id",
            "dj_play_count", "date_added", "updated_at",
        ]
        batch_size = 200
        for i in range(0, total_tracks, batch_size):
            if cancel_fn and cancel_fn():
                logger.info("Turso export cancelled during tracks export.")
                return stats
            chunk = rows[i : i + batch_size]
            chunk_vals = [
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
                ]
                for r in chunk
            ]
            sql_insert, params = _build_multi_row_insert("tracks", track_cols, chunk_vals)
            client.execute(sql_insert, params)
            stats["tracks"] += len(chunk)
            if stats["tracks"] % 500 == 0 or stats["tracks"] == total_tracks:
                logger.info("Exported %d/%d tracks to Turso", stats["tracks"], total_tracks)
            if progress_cb:
                progress_cb("tracks", stats["tracks"], total_tracks)

        # Explicitly prune any streaming tracks that may have previously existed in Turso
        try:
            client.execute("DELETE FROM tracks WHERE title = '' OR title = '(untitled)' OR title LIKE '%spotify%' OR artist LIKE '%spotify%' OR id IN ('14939874', '218663293', '152484359', '61897480', '65048245');")
            client.execute("DELETE FROM playlist_tracks WHERE track_id IN ('14939874', '218663293', '152484359', '61897480', '65048245');")
            client.execute("DELETE FROM track_tags WHERE track_id IN ('14939874', '218663293', '152484359', '61897480', '65048245');")
        except Exception as e:
            logger.warning("Could not prune streaming tracks from Turso: %s", e)

    # 2. Export Playlists
    if "djmdPlaylist" in existing_tables and pl_rows:
        total_pl = len(pl_rows)
        logger.info("Exporting %d playlists to Turso...", total_pl)
        target_pl_cols = ["id", "name", "parent_id", "seq", "is_folder", "attribute", "updated_at"]
        batch_size = 100
        for i in range(0, total_pl, batch_size):
            if cancel_fn and cancel_fn():
                logger.info("Turso export cancelled during playlists export.")
                return stats
            chunk = pl_rows[i : i + batch_size]
            chunk_vals = [
                [
                    str(r[0]),
                    str(r[1]),
                    str(r[2]) if r[2] else None,
                    int(r[3] or 0),
                    1 if r[4] == 1 else 0,
                    int(r[4] or 0),
                    str(r[5] or ""),
                ]
                for r in chunk
            ]
            sql_insert, params = _build_multi_row_insert("playlists", target_pl_cols, chunk_vals)
            client.execute(sql_insert, params)
            stats["playlists"] += len(chunk)
            if progress_cb:
                progress_cb("playlists", stats["playlists"], total_pl)

    # 3. Export Playlist Tracks
    if all_pt_rows:
        total_pt = len(all_pt_rows)
        logger.info("Exporting %d playlist tracks (including smart playlists) to Turso...", total_pt)
        target_pt_cols = ["id", "playlist_id", "track_id", "track_no"]
        batch_size = 250
        for i in range(0, total_pt, batch_size):
            if cancel_fn and cancel_fn():
                logger.info("Turso export cancelled during playlist tracks export.")
                return stats
            chunk = all_pt_rows[i : i + batch_size]
            sql_insert, params = _build_multi_row_insert("playlist_tracks", target_pt_cols, chunk)
            client.execute(sql_insert, params)
            stats["playlist_tracks"] += len(chunk)
            if progress_cb:
                progress_cb("playlist_tracks", stats["playlist_tracks"], total_pt)

    # 4. Export Tags
    if tag_rows:
        total_tags = len(tag_rows)
        logger.info("Exporting %d tags to Turso...", total_tags)
        target_tag_cols = ["id", "name", "parent_id", "seq", "updated_at"]
        batch_size = 100
        for i in range(0, total_tags, batch_size):
            if cancel_fn and cancel_fn():
                logger.info("Turso export cancelled during tags export.")
                return stats
            chunk = tag_rows[i : i + batch_size]
            chunk_vals = [
                [
                    str(r[0]),
                    str(r[1]),
                    str(r[2]) if r[2] else None,
                    int(r[3] or 0),
                    str(r[4] or ""),
                ]
                for r in chunk
            ]
            sql_insert, params = _build_multi_row_insert("tags", target_tag_cols, chunk_vals)
            client.execute(sql_insert, params)
            stats["tags"] += len(chunk)
            if progress_cb:
                progress_cb("tags", stats["tags"], total_tags)

    # 5. Export Track Tags
    if smt_rows:
        total_tt = len(smt_rows)
        logger.info("Exporting %d track tags to Turso...", total_tt)
        target_tt_cols = ["id", "track_id", "tag_id"]
        batch_size = 300
        for i in range(0, total_tt, batch_size):
            if cancel_fn and cancel_fn():
                logger.info("Turso export cancelled during track tags export.")
                return stats
            chunk = smt_rows[i : i + batch_size]
            chunk_vals = [[str(r[0]), str(r[1]), str(r[2])] for r in chunk]
            sql_insert, params = _build_multi_row_insert("track_tags", target_tt_cols, chunk_vals)
            client.execute(sql_insert, params)
            stats["track_tags"] += len(chunk)
            if progress_cb:
                progress_cb("track_tags", stats["track_tags"], total_tt)

    # 6. Export History Sessions
    if history_batch:
        logger.info("Exporting %d history sessions to Turso...", len(history_batch))
        hist_target_cols = ["id", "name", "date_created", "location", "collaborators", "track_count", "updated_at"]
        batch_size = 100
        for i in range(0, len(history_batch), batch_size):
            if cancel_fn and cancel_fn():
                logger.info("Turso export cancelled during history export.")
                return stats
            chunk = history_batch[i : i + batch_size]
            sql_insert, params = _build_multi_row_insert("history_sessions", hist_target_cols, chunk)
            client.execute(sql_insert, params)
        stats["history_sessions"] = len(history_batch)

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
    events_to_mark: List[Tuple[str, str, Optional[str]]] = []

    with engine.begin() as conn:
        for event in events:
            event_id = str(event[0])
            action = str(event[1]).upper()
            payload_str = str(event[2])

            try:
                payload = json.loads(payload_str)
            except Exception as e:
                events_to_mark.append((event_id, "FAILED", f"Invalid JSON payload: {e}"))
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
                    if "dj_play_count" in payload:
                        updates.append("DJPlayCount = :dj_play_count")
                        params["dj_play_count"] = max(0, int(payload["dj_play_count"]))

                    if updates:
                        updates.append("updated_at = :updated_at")
                        sql = f"UPDATE djmdContent SET {', '.join(updates)} WHERE ID = :track_id;"
                        conn.exec_driver_sql(sql, params)

                elif action == "UPDATE_PLAY_COUNT":
                    track_id = payload.get("track_id")
                    count = payload.get("count", 0)
                    if not track_id:
                        raise ValueError("Missing 'track_id' in UPDATE_PLAY_COUNT payload")
                    conn.exec_driver_sql(
                        "UPDATE djmdContent SET DJPlayCount = :count, updated_at = :now WHERE ID = :track_id;",
                        {"count": max(0, int(count)), "now": now_str, "track_id": track_id},
                    )

                elif action in ("UPDATE_PLAYLIST_ORDER", "REORDER_PLAYLIST"):
                    playlist_id = payload.get("playlist_id")
                    track_ids = payload.get("track_ids", [])
                    if not playlist_id:
                        raise ValueError("Missing 'playlist_id'")
                    for idx, tid in enumerate(track_ids, 1):
                        conn.exec_driver_sql(
                            """
                            UPDATE djmdSongPlaylist 
                            SET TrackNo = :track_no, updated_at = :now
                            WHERE PlaylistID = :pl AND ContentID = :track;
                            """,
                            {"track_no": idx, "pl": playlist_id, "track": str(tid), "now": now_str},
                        )

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

                elif action == "DELETE_PLAYLIST":
                    playlist_id = payload.get("playlist_id")
                    if not playlist_id:
                        raise ValueError("Missing 'playlist_id'")
                    conn.exec_driver_sql(
                        """
                        UPDATE djmdPlaylist
                        SET rb_local_deleted = 1, updated_at = :now
                        WHERE ID = :id;
                        """,
                        {"id": str(playlist_id), "now": now_str},
                    )
                    if "djmdSongPlaylist" in existing_tables:
                        conn.exec_driver_sql(
                            """
                            UPDATE djmdSongPlaylist
                            SET rb_local_deleted = 1, updated_at = :now
                            WHERE PlaylistID = :id;
                            """,
                            {"id": str(playlist_id), "now": now_str},
                        )

                elif action == "DELETE_TAG":
                    tag_id = payload.get("tag_id")
                    if not tag_id:
                        raise ValueError("Missing 'tag_id'")
                    matching_ids = _resolve_tag_matching_ids(str(tag_id)) or [str(tag_id)]
                    for mid in matching_ids:
                        conn.exec_driver_sql(
                            """
                            UPDATE djmdMyTag
                            SET rb_local_deleted = 1, updated_at = :now
                            WHERE ID = :id;
                            """,
                            {"id": mid, "now": now_str},
                        )
                        if "djmdSongMyTag" in existing_tables:
                            conn.exec_driver_sql(
                                """
                                UPDATE djmdSongMyTag
                                SET rb_local_deleted = 1, updated_at = :now
                                WHERE MyTagID = :id;
                                """,
                                {"id": mid, "now": now_str},
                            )

                elif action == "SET_TRACK_TAGS":
                    track_id = payload.get("track_id")
                    tag_ids = list(dict.fromkeys(payload.get("tag_ids", [])))
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
                        existing_row = conn.exec_driver_sql(
                            "SELECT ID FROM djmdSongMyTag WHERE ContentID = :track AND MyTagID = :tag LIMIT 1;",
                            {"track": track_id, "tag": str(tid)},
                        ).fetchone()
                        if existing_row:
                            conn.exec_driver_sql(
                                "UPDATE djmdSongMyTag SET rb_local_deleted = 0, updated_at = :now WHERE ID = :id;",
                                {"id": str(existing_row[0]), "now": now_str},
                            )
                        else:
                            new_id = str(uuid.uuid4())
                            conn.exec_driver_sql(
                                """
                                INSERT INTO djmdSongMyTag (ID, ContentID, MyTagID, created_at, updated_at, rb_local_deleted)
                                VALUES (:id, :track, :tag, :now, :now, 0);
                                """,
                                {"id": new_id, "track": track_id, "tag": str(tid), "now": now_str},
                            )

                elif action == "CREATE_HISTORY_SESSION":
                    session_id = payload.get("session_id") or str(uuid.uuid4())
                    session_name = payload.get("session_name")
                    date_created = payload.get("date_created") or now_str
                    increment_play_counts = payload.get("increment_play_counts", True)
                    tracks = payload.get("tracks", [])

                    if not session_name:
                        session_name = f"HISTORY {date_created[:10]}"

                    from rekordbox_collection_manager.utils import extract_year_month_from_name_or_date
                    from rekordbox_collection_manager.rename_history import ensure_djmd_history_folders

                    y, m = extract_year_month_from_name_or_date(session_name, date_created)
                    if not y or not m:
                        try:
                            clean_dt = date_created.replace("Z", "+00:00").replace("T", " ")
                            if len(clean_dt) >= 10:
                                y = int(clean_dt[:4])
                                m = int(clean_dt[5:7])
                        except Exception:
                            now_dt = datetime.now(timezone.utc)
                            y, m = now_dt.year, now_dt.month

                    history_cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdHistory);").fetchall()}
                    month_id = ensure_djmd_history_folders(conn, y, m, date_created, history_cols)

                    existing = conn.exec_driver_sql("SELECT ID FROM djmdHistory WHERE ID = :id;", {"id": session_id}).fetchone()
                    if not existing:
                        target_id = session_id
                        session_data = {
                            "ID": target_id,
                            "Name": session_name,
                            "ParentID": month_id,
                            "Attribute": 0,
                            "DateCreated": date_created,
                            "Seq": 999,
                            "UUID": str(uuid.uuid4()) if "UUID" in history_cols else None,
                            "rb_data_status": 0,
                            "rb_local_data_status": 0,
                            "rb_local_deleted": 0,
                            "rb_local_synced": 1,
                            "created_at": now_str,
                            "updated_at": now_str,
                        }
                        ins_cols = [k for k in session_data if k in history_cols and session_data[k] is not None]
                        col_names = ", ".join(ins_cols)
                        placeholders = ", ".join(f":{k}" for k in ins_cols)
                        conn.execute(
                            text(f"INSERT INTO djmdHistory ({col_names}) VALUES ({placeholders})"),
                            {k: session_data[k] for k in ins_cols},
                        )
                    else:
                        target_id = session_id

                    song_history_cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdSongHistory);").fetchall()}
                    content_cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdContent);").fetchall()} if "djmdContent" in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table';").fetchall()[0] else set()

                    for t_item in tracks:
                        track_id = str(t_item.get("track_id"))
                        track_no = int(t_item.get("track_no", 1))
                        played_at = t_item.get("played_at") or now_str

                        max_sh_id = conn.exec_driver_sql("SELECT MAX(CAST(ID AS INTEGER)) FROM djmdSongHistory;").scalar() or 0
                        new_sh_id = str(max_sh_id + 1)

                        sh_data = {
                            "ID": new_sh_id,
                            "HistoryID": target_id,
                            "ContentID": track_id,
                            "TrackNo": track_no,
                            "rb_data_status": 0,
                            "rb_local_deleted": 0,
                            "created_at": played_at,
                            "updated_at": now_str,
                        }
                        sh_ins_cols = [k for k in sh_data if k in song_history_cols and sh_data[k] is not None]
                        sh_col_names = ", ".join(sh_ins_cols)
                        sh_placeholders = ", ".join(f":{k}" for k in sh_ins_cols)
                        conn.execute(
                            text(f"INSERT INTO djmdSongHistory ({sh_col_names}) VALUES ({sh_placeholders})"),
                            {k: sh_data[k] for k in sh_ins_cols},
                        )

                        if increment_play_counts:
                            conn.exec_driver_sql(
                                "UPDATE djmdContent SET DJPlayCount = COALESCE(DJPlayCount, 0) + 1, updated_at = :now WHERE ID = :track_id;",
                                {"now": now_str, "track_id": track_id},
                            )

                else:
                    raise ValueError(f"Unknown action: {action}")

                events_to_mark.append((event_id, "PROCESSED", None))

            except Exception as e:
                logger.error("Failed to apply event %s: %s", event_id, e)
                events_to_mark.append((event_id, "FAILED", str(e)))

    # Mark event statuses in Turso after releasing SQLite transaction lock
    for event_id, status, error_msg in events_to_mark:
        try:
            _mark_event_status(client, event_id, status, error_msg)
            if status == "PROCESSED":
                processed_count += 1
        except Exception as e:
            logger.error("Failed to mark event status %s: %s", event_id, e)

    try:
        from rekordbox_collection_manager.rename_history import realign_history_order
        realign_history_order(engine)
    except Exception as e:
        logger.debug("History realignment after changelog: %s", e)

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
    cancel_fn: Optional[Callable[[], bool]] = None,
) -> SyncResult:
    """Execute a single sync cycle.

    1. Checks if Turso credentials are configured.
    2. If Rekordbox is running and force=False, skips to avoid database lock/conflict.
    3. Connects to Turso client.
    4. Checks for pending mobile changelog events.
    5. If no pending events and master.db is unchanged since last sync, skips export (<0.3s).
    6. Creates a safety backup only if master.db will be modified or force=True.
    7. Ingests and applies pending changelog events from Turso into master.db.
    8. Exports fresh collection snapshot (tracks, playlists, tags) to Turso.
    9. Caches sync state for instant subsequent checks.
    """
    if cancel_fn and cancel_fn():
        return SyncResult(success=False, message="Cloud sync cancelled")

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

    if cancel_fn and cancel_fn():
        return SyncResult(success=False, message="Cloud sync cancelled")

    # 2. Check pending changelog events from mobile (iOS)
    pending_events_count = 0
    try:
        res = client.execute("SELECT count(*) FROM changelog_events WHERE status = 'PENDING';")
        if res.rows:
            pending_events_count = int(res.rows[0][0])
    except Exception as e:
        logger.debug("No changelog_events or error querying: %s", e)

    if cancel_fn and cancel_fn():
        return SyncResult(success=False, message="Cloud sync cancelled")

    # 3. Check if local database hasn't changed since last sync and no pending events
    abs_db_path = os.path.abspath(resolved_db_path)
    curr_mtime = os.path.getmtime(abs_db_path) if os.path.exists(abs_db_path) else 0
    curr_size = os.path.getsize(abs_db_path) if os.path.exists(abs_db_path) else 0
    last_state = get_last_sync_state()

    is_up_to_date = (
        not force
        and pending_events_count == 0
        and last_state.get("db_path") == abs_db_path
        and last_state.get("db_mtime") == curr_mtime
        and last_state.get("db_size") == curr_size
    )

    if is_up_to_date:
        logger.info("Turso cloud is already up to date with %s (no changes detected).", resolved_db_path)
        return SyncResult(
            success=True,
            applied_events=0,
            stats=last_state.get("stats", {}),
            message="Turso cloud is up to date (no changes detected)",
        )

    # 4. Create safety backup only if we will modify master.db or force=True
    if (pending_events_count > 0 or force) and create_backup:
        try:
            backup_res = create_database_backup(resolved_db_path, force=True)
            logger.info("Created safety backup at: %s", backup_res.get("backup_path") or backup_res.get("backup_folder"))
        except Exception as e:
            logger.warning("Backup warning (proceeding with caution): %s", e)

    if cancel_fn and cancel_fn():
        return SyncResult(success=False, message="Cloud sync cancelled")

    # 5. Open database engine if not provided
    created_engine = False
    if engine is None:
        try:
            engine, _ = open_database(resolved_db_path)
            created_engine = True
        except Exception as e:
            logger.error("Failed to open master.db: %s", e)
            return SyncResult(success=False, message=f"Failed to open database: {e}")

    if cancel_fn and cancel_fn():
        return SyncResult(success=False, message="Cloud sync cancelled")

    # 6. Ingest pending changelog events from mobile (iOS)
    processed = 0
    if pending_events_count > 0:
        try:
            processed = apply_changelog_events(engine, client)
            if processed > 0:
                logger.info("Successfully applied %d change events to master.db", processed)
            else:
                logger.info("No pending changelog events to apply.")
        except Exception as e:
            logger.error("Error applying changelog events: %s", e)

    if cancel_fn and cancel_fn():
        return SyncResult(success=False, message="Cloud sync cancelled")

    # 7. Export fresh snapshot to Turso
    try:
        stats = export_collection_to_turso(
            engine, client, progress_cb=progress_cb, cancel_fn=cancel_fn
        )
        if cancel_fn and cancel_fn():
            return SyncResult(success=False, message="Cloud sync cancelled")

        # Save sync state so subsequent checks are instant (< 0.3s)
        save_sync_state(resolved_db_path, stats)

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
