"""Business logic for importing and merging external Rekordbox history sessions.

Supports:
- Analyzing external Rekordbox databases (e.g. from an old PC or backup) in comparison with the active database.
- Categorizing sessions into:
  - TO_MERGE: Active in current DB, but external DB has additional/different tracks.
  - IN_TRASH: Marked as soft-deleted in current DB (rb_local_deleted = 1), but active in external DB.
  - NEW: Present in external DB, completely missing from current DB.
  - IDENTICAL: Already active and synchronized between both databases.
- Multi-strategy track matching against current collection:
  - Exact ContentID match
  - Filename match
  - Normalized Title + Artist match
  - Normalized Title + Duration match
- Granular 3-column diff calculation (Current, External, Merged Result).
- Safe, transactional execution with automated pre-merge database backup.
"""

import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Engine

from rekordbox_collection_manager.backup_restore import create_database_backup
from rekordbox_collection_manager.rename_history import (
    ensure_djmd_history_folders,
    realign_history_order,
)
from rekordbox_collection_manager.utils import (
    extract_year_month_from_name_or_date,
    get_rekordbox_timestamp,
    open_database,
)

logger = logging.getLogger(__name__)


@dataclass
class TrackDiffItem:
    """Represents a single track within the 3-column history diff."""

    track_no: int
    played_at: str
    played_time: str
    title: str
    artist: str
    album: str
    duration_sec: int
    duration_str: str
    content_id: Optional[str]
    matched_content_id: Optional[str]
    file_path: str
    file_name: str
    status: str  # "matched", "new", "missing_file", "current_only"
    included: bool = True
    external_track_no: Optional[int] = None
    origin: str = "current"  # "current", "external", "merged"


@dataclass
class HistorySessionAnalysis:
    """Analysis result for a single history session from external DB."""

    external_id: str
    name: str
    date_created: str
    year: int
    month: int
    external_track_count: int
    current_id: Optional[str] = None
    current_name: Optional[str] = None
    current_track_count: int = 0
    diff_count: int = 0  # external_track_count - current_track_count
    category: str = "new"  # "to_merge", "in_trash", "new", "identical"
    is_current_deleted: bool = False


@dataclass
class ExternalHistoryAnalysis:
    """Comprehensive analysis of an external database against the current database."""

    external_db_path: str
    total_external_sessions: int
    sessions: List[HistorySessionAnalysis] = field(default_factory=list)
    counts: Dict[str, int] = field(default_factory=dict)


def normalize_string(s: Optional[str]) -> str:
    """Normalize a string for robust matching: lowercase, alphanumeric only, trimmed."""
    if not s:
        return ""
    # Remove featuring / remix noise for softer title matching if needed
    cleaned = re.sub(r"[^\w\s]", "", s.lower())
    return " ".join(cleaned.split())


def format_duration(seconds: int) -> str:
    """Format duration in seconds to MM:SS."""
    if not seconds or seconds < 0:
        return "--:--"
    m = seconds // 60
    s = seconds % 60
    return f"{m}:{s:02d}"


def build_current_track_index(current_engine: Engine) -> Dict[str, Any]:
    """Index current djmdContent tracks for rapid matching by ID, filename, and metadata."""
    index: Dict[str, Any] = {
        "by_id": {},
        "by_filename": {},
        "by_title_artist": {},
        "by_title_duration": {},
    }

    with current_engine.connect() as conn:
        tables_res = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table';"
        ).fetchall()
        existing_tables = {r[0] for r in tables_res}

        if "djmdContent" not in existing_tables:
            return index

        cols = {
            r[1]
            for r in conn.exec_driver_sql("PRAGMA table_info(djmdContent);").fetchall()
        }
        del_filter = (
            "WHERE (c.rb_local_deleted = 0 OR c.rb_local_deleted IS NULL)"
            if "rb_local_deleted" in cols
            else ""
        )

        join_artist = (
            "LEFT JOIN djmdArtist a ON c.ArtistID = a.ID"
            if "djmdArtist" in existing_tables
            else ""
        )
        artist_expr = (
            "COALESCE(a.Name, c.SrcArtistName, '')"
            if "djmdArtist" in existing_tables
            else "COALESCE(c.SrcArtistName, '')"
        )

        query = f"""
            SELECT c.ID, c.Title, {artist_expr} AS Artist, c.FileNameL, c.FolderPath, c.Length
            FROM djmdContent c
            {join_artist}
            {del_filter};
        """
        rows = conn.execute(text(query)).fetchall()

        for r in rows:
            cid = str(r[0])
            title = str(r[1] or "").strip()
            artist = str(r[2] or "").strip()
            fn = str(r[3] or "").strip()
            length = int(r[5] or 0)

            index["by_id"][cid] = cid

            if fn:
                base_fn = os.path.basename(fn).lower()
                if base_fn:
                    index["by_filename"][base_fn] = cid

            norm_t = normalize_string(title)
            norm_a = normalize_string(artist)

            if norm_t:
                key_ta = (norm_t, norm_a)
                index["by_title_artist"][key_ta] = cid

                # Also index with length tolerance (within 2s)
                if length > 0:
                    for delta in (-2, -1, 0, 1, 2):
                        index["by_title_duration"][(norm_t, length + delta)] = cid

    return index


def match_track_to_current(
    external_track: Dict[str, Any], current_track_index: Dict[str, Any]
) -> Optional[str]:
    """Match an external track to an existing ContentID in the current collection.

    Priority:
    1. Exact ID (if that ID exists in current DB)
    2. Exact Filename
    3. Normalized Title + Artist
    4. Normalized Title + Duration
    """
    ext_id = str(external_track.get("content_id") or "")
    if ext_id and ext_id in current_track_index["by_id"]:
        return ext_id

    fn = external_track.get("file_name") or ""
    if not fn and external_track.get("file_path"):
        fn = os.path.basename(external_track["file_path"])

    if fn:
        base_fn = fn.lower()
        if base_fn in current_track_index["by_filename"]:
            return current_track_index["by_filename"][base_fn]

    title = external_track.get("title") or ""
    artist = external_track.get("artist") or ""
    duration = int(external_track.get("duration_sec") or 0)

    norm_t = normalize_string(title)
    norm_a = normalize_string(artist)

    if norm_t:
        key_ta = (norm_t, norm_a)
        if key_ta in current_track_index["by_title_artist"]:
            return current_track_index["by_title_artist"][key_ta]

        if duration > 0:
            key_td = (norm_t, duration)
            if key_td in current_track_index["by_title_duration"]:
                return current_track_index["by_title_duration"][key_td]

    return None


def analyze_external_history_db(
    current_engine: Engine,
    external_db_path: str,
) -> ExternalHistoryAnalysis:
    """Analyze an external database and compare all history sessions against the current DB."""
    ext_engine, ext_fmt = open_database(external_db_path)

    analysis = ExternalHistoryAnalysis(
        external_db_path=external_db_path,
        total_external_sessions=0,
        counts={"to_merge": 0, "in_trash": 0, "new": 0, "identical": 0},
    )

    with ext_engine.connect() as ext_conn, current_engine.connect() as cur_conn:
        # 1. Fetch all external sessions (Attribute = 0)
        ext_sessions = ext_conn.exec_driver_sql("""
            SELECT ID, Name, DateCreated, ParentID
            FROM djmdHistory
            WHERE Attribute = 0
            ORDER BY DateCreated DESC;
        """).fetchall()

        analysis.total_external_sessions = len(ext_sessions)

        # 2. Fetch all current sessions (both active and deleted)
        cur_cols = {
            r[1]
            for r in cur_conn.exec_driver_sql("PRAGMA table_info(djmdHistory);").fetchall()
        }
        has_del = "rb_local_deleted" in cur_cols
        del_col = ", rb_local_deleted" if has_del else ", 0 AS rb_local_deleted"

        cur_sessions = cur_conn.exec_driver_sql(f"""
            SELECT ID, Name, DateCreated{del_col}
            FROM djmdHistory
            WHERE Attribute = 0;
        """).fetchall()

        # Build lookup tables for current sessions
        cur_by_id: Dict[str, Any] = {}
        cur_by_date: Dict[str, Any] = {}
        cur_by_name: Dict[str, Any] = {}

        for cs in cur_sessions:
            cid = str(cs[0])
            cname = str(cs[1] or "")
            cdate = str(cs[2] or "")
            cdel = bool(cs[3] == 1) if has_del else False
            record = {"id": cid, "name": cname, "date": cdate, "is_deleted": cdel}
            cur_by_id[cid] = record
            if cdate:
                cur_by_date[cdate] = record
            if cname:
                cur_by_name[cname] = record

        # 3. For each external session, count tracks and determine match
        for es in ext_sessions:
            eid = str(es[0])
            ename = str(es[1] or "(Untitled)")
            edate = str(es[2] or "")

            y, m = extract_year_month_from_name_or_date(ename, edate)

            # Count tracks in external session
            ext_t_count = ext_conn.exec_driver_sql(
                """
                SELECT COUNT(*) FROM djmdSongHistory
                WHERE HistoryID = :hid AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL);
            """,
                {"hid": eid},
            ).scalar() or 0

            # Match against current DB
            matched_cur: Optional[Dict[str, Any]] = None
            if eid in cur_by_id:
                matched_cur = cur_by_id[eid]
            elif edate and edate in cur_by_date:
                matched_cur = cur_by_date[edate]
            elif ename in cur_by_name:
                matched_cur = cur_by_name[ename]

            if not matched_cur:
                # Completely new session
                cat = "new"
                sess_analysis = HistorySessionAnalysis(
                    external_id=eid,
                    name=ename,
                    date_created=edate,
                    year=y,
                    month=m,
                    external_track_count=ext_t_count,
                    current_id=None,
                    current_name=None,
                    current_track_count=0,
                    diff_count=ext_t_count,
                    category=cat,
                    is_current_deleted=False,
                )
            else:
                cid = matched_cur["id"]
                cname = matched_cur["name"]
                is_del = matched_cur["is_deleted"]

                # Count active tracks in current session
                cur_t_count = cur_conn.exec_driver_sql(
                    """
                    SELECT COUNT(*) FROM djmdSongHistory
                    WHERE HistoryID = :hid AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL);
                """,
                    {"hid": cid},
                ).scalar() or 0

                diff_cnt = ext_t_count - cur_t_count

                if is_del:
                    cat = "in_trash"
                elif diff_cnt > 0 or ext_t_count != cur_t_count:
                    cat = "to_merge"
                else:
                    cat = "identical"

                sess_analysis = HistorySessionAnalysis(
                    external_id=eid,
                    name=ename,
                    date_created=edate,
                    year=y,
                    month=m,
                    external_track_count=ext_t_count,
                    current_id=cid,
                    current_name=cname,
                    current_track_count=cur_t_count,
                    diff_count=diff_cnt,
                    category=cat,
                    is_current_deleted=is_del,
                )

            analysis.sessions.append(sess_analysis)
            analysis.counts[cat] = analysis.counts.get(cat, 0) + 1

    return analysis


def fetch_external_session_tracks(
    external_engine: Engine, history_id: str
) -> List[Dict[str, Any]]:
    """Fetch complete list of tracks for a history session from the external DB."""
    tracks: List[Dict[str, Any]] = []

    with external_engine.connect() as conn:
        tables_res = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table';"
        ).fetchall()
        existing_tables = {r[0] for r in tables_res}

        if "djmdSongHistory" not in existing_tables or "djmdContent" not in existing_tables:
            return tracks

        sh_cols = {
            r[1]
            for r in conn.exec_driver_sql("PRAGMA table_info(djmdSongHistory);").fetchall()
        }
        sh_del = (
            "AND (sh.rb_local_deleted = 0 OR sh.rb_local_deleted IS NULL)"
            if "rb_local_deleted" in sh_cols
            else ""
        )

        join_artist = (
            "LEFT JOIN djmdArtist a ON c.ArtistID = a.ID"
            if "djmdArtist" in existing_tables
            else ""
        )
        artist_expr = (
            "COALESCE(a.Name, c.SrcArtistName, 'Unknown Artist')"
            if "djmdArtist" in existing_tables
            else "COALESCE(c.SrcArtistName, 'Unknown Artist')"
        )

        join_album = (
            "LEFT JOIN djmdAlbum al ON c.AlbumID = al.ID"
            if "djmdAlbum" in existing_tables
            else ""
        )
        album_expr = "al.Name" if "djmdAlbum" in existing_tables else "NULL"

        query = f"""
            SELECT 
                sh.TrackNo,
                sh.created_at AS PlayedAt,
                c.ID AS ContentID,
                c.Title,
                {artist_expr} AS ArtistName,
                {album_expr} AS AlbumName,
                c.Length,
                c.FolderPath,
                c.FileNameL,
                c.BPM,
                c.Rating,
                c.ReleaseYear,
                c.Commnt
            FROM djmdSongHistory sh
            JOIN djmdContent c ON sh.ContentID = c.ID
            {join_artist}
            {join_album}
            WHERE sh.HistoryID = :hid
              {sh_del}
            ORDER BY sh.TrackNo ASC;
        """
        rows = conn.execute(text(query), {"hid": history_id}).mappings().fetchall()

        for r in rows:
            dur = int(r.get("Length") or 0)
            played_at = str(r.get("PlayedAt") or "")
            played_time = ""
            if played_at:
                parts = played_at.split()
                if len(parts) >= 2:
                    played_time = parts[1].split(".")[0]
                else:
                    played_time = played_at

            fn = str(r.get("FileNameL") or "")
            fp = str(r.get("FolderPath") or "")

            tracks.append({
                "track_no": int(r.get("TrackNo") or 0),
                "played_at": played_at,
                "played_time": played_time,
                "content_id": str(r.get("ContentID") or ""),
                "title": str(r.get("Title") or "(Untitled Track)"),
                "artist": str(r.get("ArtistName") or "Unknown Artist"),
                "album": str(r.get("AlbumName") or ""),
                "duration_sec": dur,
                "duration_str": format_duration(dur),
                "file_path": fp,
                "file_name": fn,
                "bpm": float(r.get("BPM") or 0) / 100.0 if r.get("BPM") else 0.0,
                "rating": int(r.get("Rating") or 0),
                "release_year": int(r.get("ReleaseYear") or 0),
                "comment": str(r.get("Commnt") or ""),
            })

    return tracks


def fetch_current_session_tracks(
    current_engine: Engine, history_id: str
) -> List[Dict[str, Any]]:
    """Fetch complete list of tracks for a history session from the current DB."""
    from rekordbox_collection_manager.rename_history import fetch_session_tracks

    session_data = fetch_session_tracks(current_engine, history_id)
    raw_tracks = session_data.get("tracks", [])

    clean_tracks: List[Dict[str, Any]] = []
    for t in raw_tracks:
        played_at = str(t.get("played_at") or "")
        played_time = ""
        if played_at:
            parts = played_at.split()
            if len(parts) >= 2:
                played_time = parts[1].split(".")[0]
            else:
                played_time = played_at

        clean_tracks.append({
            "track_no": int(t.get("track_no") or 0),
            "played_at": played_at,
            "played_time": played_time,
            "content_id": str(t.get("id") or ""),
            "title": str(t.get("title") or ""),
            "artist": str(t.get("artist") or ""),
            "album": str(t.get("album") or ""),
            "duration_sec": int(t.get("duration_sec") or 0),
            "duration_str": t.get("duration") or format_duration(int(t.get("duration_sec") or 0)),
            "file_path": str(t.get("file_path") or ""),
            "file_name": str(t.get("file_name") or ""),
            "bpm": float(t.get("bpm") or 0.0),
        })

    return clean_tracks


def calculate_history_session_diff(
    current_tracks: List[Dict[str, Any]],
    external_tracks: List[Dict[str, Any]],
    current_track_index: Dict[str, Any],
) -> Dict[str, Any]:
    """Calculate the 3-column diff between current and external history sessions.

    Returns:
    {
        "current_column": List[TrackDiffItem],
        "external_column": List[TrackDiffItem],
        "merged_column": List[TrackDiffItem],
        "stats": {
            "matched_count": int,
            "new_count": int,
            "missing_file_count": int,
            "total_result_count": int,
        }
    }
    """
    current_items: List[TrackDiffItem] = []
    external_items: List[TrackDiffItem] = []

    # Build lookup of current session tracks (by content_id, title+artist, and played_at)
    cur_session_cids: Set[str] = set()
    cur_session_ta: Set[Tuple[str, str]] = set()

    for ct in current_tracks:
        cid = ct["content_id"]
        if cid:
            cur_session_cids.add(cid)
        norm_t = normalize_string(ct["title"])
        norm_a = normalize_string(ct["artist"])
        if norm_t:
            cur_session_ta.add((norm_t, norm_a))

        current_items.append(
            TrackDiffItem(
                track_no=ct["track_no"],
                played_at=ct["played_at"],
                played_time=ct["played_time"],
                title=ct["title"],
                artist=ct["artist"],
                album=ct["album"],
                duration_sec=ct["duration_sec"],
                duration_str=ct["duration_str"],
                content_id=ct["content_id"],
                matched_content_id=ct["content_id"],
                file_path=ct["file_path"],
                file_name=ct["file_name"],
                status="current_only",
                included=True,
                origin="current",
            )
        )

    matched_count = 0
    new_count = 0
    missing_file_count = 0

    # Process external tracks
    for et in external_tracks:
        matched_cid = match_track_to_current(et, current_track_index)
        norm_t = normalize_string(et["title"])
        norm_a = normalize_string(et["artist"])

        # Check if already present in the current session
        is_already_in_session = False
        if matched_cid and matched_cid in cur_session_cids:
            is_already_in_session = True
        elif (norm_t, norm_a) in cur_session_ta:
            is_already_in_session = True

        if is_already_in_session:
            status = "matched"
            matched_count += 1
            # Mark corresponding current track as matched
            for ci in current_items:
                if (ci.content_id and ci.content_id == matched_cid) or (
                    normalize_string(ci.title) == norm_t
                    and normalize_string(ci.artist) == norm_a
                ):
                    ci.status = "matched"
                    break
        elif matched_cid:
            status = "new"
            new_count += 1
        else:
            status = "missing_file"
            missing_file_count += 1

        external_items.append(
            TrackDiffItem(
                track_no=et["track_no"],
                played_at=et["played_at"],
                played_time=et["played_time"],
                title=et["title"],
                artist=et["artist"],
                album=et["album"],
                duration_sec=et["duration_sec"],
                duration_str=et["duration_str"],
                content_id=et["content_id"],
                matched_content_id=matched_cid,
                file_path=et["file_path"],
                file_name=et["file_name"],
                status=status,
                included=(status != "matched"),  # new items included by default
                external_track_no=et["track_no"],
                origin="external",
            )
        )

    # 3. Construct initial Merged column
    # Starts with all current items + new external items, sorted chronologically
    merged_items: List[TrackDiffItem] = []

    # First add all current items
    for ci in current_items:
        merged_items.append(
            TrackDiffItem(
                track_no=ci.track_no,
                played_at=ci.played_at,
                played_time=ci.played_time,
                title=ci.title,
                artist=ci.artist,
                album=ci.album,
                duration_sec=ci.duration_sec,
                duration_str=ci.duration_str,
                content_id=ci.content_id,
                matched_content_id=ci.matched_content_id,
                file_path=ci.file_path,
                file_name=ci.file_name,
                status=ci.status,
                included=True,
                origin="current",
            )
        )

    # Add external items that are new or missing_file
    for ei in external_items:
        if ei.status in ("new", "missing_file"):
            merged_items.append(
                TrackDiffItem(
                    track_no=ei.track_no,
                    played_at=ei.played_at,
                    played_time=ei.played_time,
                    title=ei.title,
                    artist=ei.artist,
                    album=ei.album,
                    duration_sec=ei.duration_sec,
                    duration_str=ei.duration_str,
                    content_id=ei.content_id,
                    matched_content_id=ei.matched_content_id,
                    file_path=ei.file_path,
                    file_name=ei.file_name,
                    status=ei.status,
                    included=True,
                    external_track_no=ei.external_track_no,
                    origin="external",
                )
            )

    # Sort merged items by played_at timestamp if present, otherwise by track_no
    def sort_key(item: TrackDiffItem) -> Tuple:
        pa = item.played_at or ""
        return (pa if pa else "9999", item.track_no)

    merged_items.sort(key=sort_key)

    # Re-index TrackNo 1..N
    for idx, item in enumerate(merged_items, start=1):
        item.track_no = idx

    return {
        "current_column": current_items,
        "external_column": external_items,
        "merged_column": merged_items,
        "stats": {
            "matched_count": matched_count,
            "new_count": new_count,
            "missing_file_count": missing_file_count,
            "total_result_count": len(merged_items),
        },
    }


def execute_history_merge_plan(
    current_engine: Engine,
    external_engine: Engine,
    plan: List[Dict[str, Any]],
    db_path: str,
    create_placeholders: bool = True,
    backup: bool = True,
) -> Dict[str, Any]:
    """Execute the import/merge plan safely in the current database.

    Each item in `plan` is a dict:
    {
        "external_history_id": str,
        "current_history_id": Optional[str],
        "action": "merge" | "restore_and_merge" | "restore" | "create_new",
        "tracks_to_insert": List[TrackDiffItem],
        "realign_existing_tracks": Optional[List[Dict[str, Any]]],
    }

    Returns execution summary statistics.
    """
    stats = {
        "sessions_merged": 0,
        "sessions_restored": 0,
        "sessions_created": 0,
        "tracks_added": 0,
        "placeholders_created": 0,
        "backup_path": "",
    }

    # 1. Automatic pre-execution safety backup
    if backup:
        backup_res = create_database_backup(db_path=db_path)
        stats["backup_path"] = str(backup_res.get("backup_folder") or "")

    now_utc = get_rekordbox_timestamp()

    with current_engine.begin() as conn, external_engine.connect() as ext_conn:
        h_cols = {
            r[1]
            for r in conn.exec_driver_sql("PRAGMA table_info(djmdHistory);").fetchall()
        }
        sh_cols = {
            r[1]
            for r in conn.exec_driver_sql("PRAGMA table_info(djmdSongHistory);").fetchall()
        }
        c_cols = {
            r[1]
            for r in conn.exec_driver_sql("PRAGMA table_info(djmdContent);").fetchall()
        }

        for item in plan:
            ext_hid = item["external_history_id"]
            cur_hid = item.get("current_history_id")
            action = item["action"]
            tracks_to_insert: List[TrackDiffItem] = item.get("tracks_to_insert", [])

            # Fetch external session metadata
            ext_h_row = ext_conn.execute(
                text("SELECT ID, Name, DateCreated, ParentID FROM djmdHistory WHERE ID = :hid;"),
                {"hid": ext_hid},
            ).mappings().fetchone()
            if not ext_h_row:
                continue

            session_name = ext_h_row["Name"] or "(Untitled Session)"
            session_date = ext_h_row["DateCreated"] or now_utc
            y, m = extract_year_month_from_name_or_date(session_name, session_date)

            # Ensure Year and Month folder hierarchy
            target_month_id = ensure_djmd_history_folders(
                conn, y, m, session_date, h_cols
            )

            target_hid = cur_hid

            # Handle session restoration or creation
            if action in ("restore", "restore_and_merge") and cur_hid:
                # Reactivate existing soft-deleted session
                set_parts = ["rb_local_deleted = 0"]
                if "rb_data_status" in h_cols:
                    set_parts.append("rb_data_status = 0")
                if "updated_at" in h_cols:
                    set_parts.append("updated_at = :now")
                if "ParentID" in h_cols:
                    set_parts.append("ParentID = :month_id")

                conn.execute(
                    text(f"UPDATE djmdHistory SET {', '.join(set_parts)} WHERE ID = :hid;"),
                    {"hid": cur_hid, "now": now_utc, "month_id": target_month_id},
                )
                stats["sessions_restored"] += 1

            elif action == "create_new" or not target_hid:
                # Create brand new session row
                new_hid = cur_hid or ext_hid
                # If ID exists, generate a new one
                exists = conn.exec_driver_sql(
                    "SELECT 1 FROM djmdHistory WHERE ID = :id", {"id": new_hid}
                ).fetchone()
                if exists:
                    new_hid = str(uuid.uuid4())

                sess_data = {
                    "ID": new_hid,
                    "Name": session_name,
                    "ParentID": target_month_id,
                    "Attribute": 0,
                    "DateCreated": session_date,
                    "UUID": str(uuid.uuid4()),
                    "Seq": 1,
                    "rb_data_status": 0,
                    "rb_local_data_status": 0,
                    "rb_local_deleted": 0,
                    "rb_local_synced": 1,
                    "created_at": now_utc,
                    "updated_at": now_utc,
                }
                insert_cols = [k for k in sess_data if k in h_cols]
                col_names = ", ".join(insert_cols)
                placeholders = ", ".join(f":{k}" for k in insert_cols)
                conn.execute(
                    text(f"INSERT INTO djmdHistory ({col_names}) VALUES ({placeholders})"),
                    {k: sess_data[k] for k in insert_cols},
                )
                target_hid = new_hid
                stats["sessions_created"] += 1
            else:
                stats["sessions_merged"] += 1

            # Insert new tracks
            for t in tracks_to_insert:
                if not t.included:
                    continue

                content_id_to_link = t.matched_content_id

                # If track missing from current collection and placeholders enabled:
                if not content_id_to_link and create_placeholders and t.content_id:
                    ext_content = ext_conn.execute(
                        text("SELECT * FROM djmdContent WHERE ID = :cid;"),
                        {"cid": t.content_id},
                    ).mappings().fetchone()

                    if ext_content:
                        # Copy content row into current DB
                        new_cid = t.content_id
                        cid_exists = conn.exec_driver_sql(
                            "SELECT 1 FROM djmdContent WHERE ID = :cid",
                            {"cid": new_cid},
                        ).fetchone()
                        if cid_exists:
                            new_cid = str(uuid.uuid4())

                        content_dict = dict(ext_content)
                        content_dict["ID"] = new_cid
                        content_dict["UUID"] = str(uuid.uuid4())
                        content_dict["rb_data_status"] = 0
                        content_dict["rb_local_data_status"] = 0
                        content_dict["rb_local_deleted"] = 0
                        content_dict["created_at"] = now_utc
                        content_dict["updated_at"] = now_utc

                        insert_c_cols = [k for k in content_dict if k in c_cols]
                        c_col_names = ", ".join(insert_c_cols)
                        c_placeholders = ", ".join(f":{k}" for k in insert_c_cols)
                        conn.execute(
                            text(f"INSERT INTO djmdContent ({c_col_names}) VALUES ({c_placeholders})"),
                            {k: content_dict[k] for k in insert_c_cols},
                        )
                        content_id_to_link = new_cid
                        stats["placeholders_created"] += 1

                if not content_id_to_link:
                    # Skip if track cannot be linked
                    continue

                # Insert into djmdSongHistory
                sh_data = {
                    "ID": str(uuid.uuid4()),
                    "HistoryID": target_hid,
                    "ContentID": content_id_to_link,
                    "TrackNo": t.track_no,
                    "UUID": str(uuid.uuid4()),
                    "rb_data_status": 0,
                    "rb_local_data_status": 0,
                    "rb_local_deleted": 0,
                    "rb_local_synced": 1,
                    "created_at": t.played_at or now_utc,
                    "updated_at": now_utc,
                }
                insert_sh_cols = [k for k in sh_data if k in sh_cols]
                sh_col_names = ", ".join(insert_sh_cols)
                sh_placeholders = ", ".join(f":{k}" for k in insert_sh_cols)

                conn.execute(
                    text(f"INSERT INTO djmdSongHistory ({sh_col_names}) VALUES ({sh_placeholders})"),
                    {k: sh_data[k] for k in insert_sh_cols},
                )
                stats["tracks_added"] += 1

                # Increment DJPlayCount on djmdContent
                if "DJPlayCount" in c_cols:
                    conn.execute(
                        text("UPDATE djmdContent SET DJPlayCount = COALESCE(DJPlayCount, 0) + 1 WHERE ID = :cid;"),
                        {"cid": content_id_to_link},
                    )

    # 3. Realign chronological sequence order in djmdHistory
    realign_history_order(current_engine)

    return stats
