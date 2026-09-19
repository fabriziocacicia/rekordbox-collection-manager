"""Interactive CLI tool to increment and decrement track play counts in Rekordbox databases."""

import argparse
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import questionary
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rekordbox_collection_manager.utils import (
    detect_content_table,
    ensure_rekordbox_not_running,
    format_location_display,
    get_rekordbox_timestamp,
    is_rekordbox_running,
    open_database,
    resolve_artwork_path,
    resolve_db_path,
    resolve_file_and_folder_paths,
    resolve_file_type,
)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="edit-play-count",
        description="Interactively increment or decrement track play counts in Rekordbox.",
    )
    parser.add_argument(
        "query",
        nargs="?",
        default=None,
        help="Optional initial track title search query.",
    )
    parser.add_argument(
        "-d",
        "--db-path",
        dest="db_path",
        default=None,
        help="Path to Rekordbox database file (master.db or exportLibrary.db).",
    )
    parser.add_argument(
        "-i",
        "--inc",
        "--increment",
        dest="increment",
        action="store_true",
        help="Auto-apply increment (+1) to selected track.",
    )
    parser.add_argument(
        "-dec",
        "--dec",
        "--decrement",
        dest="decrement",
        action="store_true",
        help="Auto-apply decrement (-1) to selected track.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Proceed even if Rekordbox is currently running (not recommended).",
    )
    return parser.parse_args(argv)



def search_tracks(
    engine: Engine,
    query: str = "",
    limit: Optional[int] = 50,
    table_name: Optional[str] = None,
    sort_by: str = "title",
    sort_order: str = "asc",
    play_count_filter: Optional[str] = "all",
    content_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Search for tracks matching query string with rich metadata support.

    Dynamically queries djmdContent (or generic content table) and joins
    artist, album, genre, key, tags, and real playlists when present.
    Supports text searching, play count filtering ('all', 'played', 'unplayed'),
    and column sorting.
    """
    if table_name is None:
        table_name = detect_content_table(engine)

    query_str = query.strip()
    pattern = f"%{query_str}%"
    starts_with = f"{query_str}%"
    is_desc = str(sort_order).lower() == "desc"
    order_dir = "DESC" if is_desc else "ASC"

    records: List[Dict[str, Any]] = []
    with engine.connect() as conn:
        cols = {
            r[1]
            for r in conn.exec_driver_sql(f"PRAGMA table_info({table_name});").fetchall()
        }

        all_tables_res = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table';"
        ).fetchall()
        existing_tables = {r[0] for r in all_tables_res}

        if table_name == "djmdContent":
            joins = []
            if "djmdArtist" in existing_tables and "ArtistID" in cols:
                joins.append("LEFT JOIN djmdArtist a ON c.ArtistID = a.ID")
                artist_col_expr = "COALESCE(a.Name, c.SrcArtistName, 'Unknown Artist')"
            elif "SrcArtistName" in cols:
                artist_col_expr = "COALESCE(c.SrcArtistName, 'Unknown Artist')"
            else:
                artist_col_expr = "'Unknown Artist'"

            if "djmdAlbum" in existing_tables and "AlbumID" in cols:
                joins.append("LEFT JOIN djmdAlbum al ON c.AlbumID = al.ID")
                album_col_expr = "al.Name"
            else:
                album_col_expr = "NULL"

            if "djmdGenre" in existing_tables and "GenreID" in cols:
                joins.append("LEFT JOIN djmdGenre g ON c.GenreID = g.ID")
                genre_col_expr = "g.Name"
            else:
                genre_col_expr = "NULL"

            if "djmdKey" in existing_tables and "KeyID" in cols:
                joins.append("LEFT JOIN djmdKey k ON c.KeyID = k.ID")
                key_col_expr = "k.ScaleName"
            else:
                key_col_expr = "NULL"

            has_my_tag = "djmdSongMyTag" in existing_tables and "djmdMyTag" in existing_tables
            has_playlist = "djmdSongPlaylist" in existing_tables and "djmdPlaylist" in existing_tables

            if has_my_tag:
                tag_expr = """(
                    SELECT GROUP_CONCAT(t.Name, '|||')
                    FROM djmdSongMyTag smt
                    JOIN djmdMyTag t ON smt.MyTagID = t.ID
                    WHERE smt.ContentID = c.ID
                      AND (smt.rb_local_deleted = 0 OR smt.rb_local_deleted IS NULL)
                      AND (t.rb_local_deleted = 0 OR t.rb_local_deleted IS NULL)
                ) AS TagNames"""
            else:
                tag_expr = "NULL AS TagNames"

            if has_playlist:
                playlist_expr = """(
                    SELECT GROUP_CONCAT(p.Name, '|||')
                    FROM djmdSongPlaylist sp
                    JOIN djmdPlaylist p ON sp.PlaylistID = p.ID
                    WHERE sp.ContentID = c.ID
                      AND p.Attribute = 0
                      AND (p.rb_local_deleted = 0 OR p.rb_local_deleted IS NULL)
                      AND (sp.rb_local_deleted = 0 OR sp.rb_local_deleted IS NULL)
                ) AS PlaylistNames"""
            else:
                playlist_expr = "NULL AS PlaylistNames"

            join_clause = "\n".join(joins)

            # Columns
            bpm_expr = "c.BPM" if "BPM" in cols else "0 AS BPM"
            length_expr = "c.Length" if "Length" in cols else "0 AS Length"
            play_col = "c.DJPlayCount" if "DJPlayCount" in cols else ("c.PlayCount" if "PlayCount" in cols else "0")
            play_expr = f"{play_col} AS DJPlayCount"
            rating_expr = "c.Rating" if "Rating" in cols else "0 AS Rating"
            year_expr = "c.ReleaseYear" if "ReleaseYear" in cols else "0 AS ReleaseYear"
            bitrate_expr = "c.BitRate" if "BitRate" in cols else "0 AS BitRate"
            file_type_expr = "c.FileType" if "FileType" in cols else "NULL AS FileType"
            comment_expr = "c.Commnt" if "Commnt" in cols else "'' AS Commnt"
            folder_expr = "c.FolderPath" if "FolderPath" in cols else "'' AS FolderPath"
            filename_expr = "c.FileNameL" if "FileNameL" in cols else "'' AS FileNameL"
            image_path_expr = "c.ImagePath" if "ImagePath" in cols else "NULL AS ImagePath"
            stock_date_expr = "c.StockDate" if "StockDate" in cols else ("c.DateCreated" if "DateCreated" in cols else "NULL AS StockDate")
            sample_rate_expr = "c.SampleRate" if "SampleRate" in cols else "0 AS SampleRate"
            bit_depth_expr = "c.BitDepth" if "BitDepth" in cols else "0 AS BitDepth"
            file_size_expr = "c.FileSize" if "FileSize" in cols else "0 AS FileSize"
            color_expr = "c.ColorID" if "ColorID" in cols else "NULL AS ColorID"

            # Deleted filter
            deleted_filter = "AND (c.rb_local_deleted = 0 OR c.rb_local_deleted IS NULL)" if "rb_local_deleted" in cols else ""

            # Play count filter
            play_filter = ""
            if play_count_filter == "played":
                play_filter = f"AND ({play_col} > 0)"
            elif play_count_filter == "unplayed":
                play_filter = f"AND ({play_col} = 0 OR {play_col} IS NULL)"

            # Search condition
            search_condition = ""
            if content_id is not None:
                search_condition = "AND c.ID = :content_id"
            elif query_str:
                search_parts = ["c.Title LIKE :pattern"]
                if "SearchStr" in cols:
                    search_parts.append("c.SearchStr LIKE :pattern")
                if "djmdArtist" in existing_tables and "ArtistID" in cols:
                    search_parts.append("a.Name LIKE :pattern")
                if "SrcArtistName" in cols:
                    search_parts.append("c.SrcArtistName LIKE :pattern")
                if "FileNameL" in cols:
                    search_parts.append("c.FileNameL LIKE :pattern")
                if "djmdAlbum" in existing_tables and "AlbumID" in cols:
                    search_parts.append("al.Name LIKE :pattern")
                if "djmdGenre" in existing_tables and "GenreID" in cols:
                    search_parts.append("g.Name LIKE :pattern")
                if "djmdKey" in existing_tables and "KeyID" in cols:
                    search_parts.append("k.ScaleName LIKE :pattern")
                if has_my_tag:
                    search_parts.append("""c.ID IN (
                        SELECT smt.ContentID FROM djmdSongMyTag smt
                        JOIN djmdMyTag t ON smt.MyTagID = t.ID
                        WHERE t.Name LIKE :pattern
                          AND (smt.rb_local_deleted = 0 OR smt.rb_local_deleted IS NULL)
                          AND (t.rb_local_deleted = 0 OR t.rb_local_deleted IS NULL)
                    )""")
                if has_playlist:
                    search_parts.append("""c.ID IN (
                        SELECT sp.ContentID FROM djmdSongPlaylist sp
                        JOIN djmdPlaylist p ON sp.PlaylistID = p.ID
                        WHERE p.Attribute = 0 AND p.Name LIKE :pattern
                          AND (p.rb_local_deleted = 0 OR p.rb_local_deleted IS NULL)
                          AND (sp.rb_local_deleted = 0 OR sp.rb_local_deleted IS NULL)
                    )""")
                search_condition = "AND (" + " OR ".join(search_parts) + ")"

            # Order clause
            if sort_by == "play_count":
                order_clause = f"{play_col} {order_dir}, c.Title ASC"
            elif sort_by == "artist":
                order_clause = f"{artist_col_expr} {order_dir}, c.Title ASC"
            elif sort_by == "album":
                order_clause = f"{album_col_expr} {order_dir}, c.Title ASC"
            elif sort_by == "key":
                order_clause = f"{key_col_expr} {order_dir}, c.Title ASC"
            elif sort_by == "bpm":
                order_clause = f"c.BPM {order_dir}, c.Title ASC" if "BPM" in cols else "c.Title ASC"
            elif sort_by in ("duration", "time"):
                order_clause = f"c.Length {order_dir}, c.Title ASC" if "Length" in cols else "c.Title ASC"
            elif sort_by == "bitrate":
                order_clause = f"c.BitRate {order_dir}, c.Title ASC" if "BitRate" in cols else "c.Title ASC"
            elif sort_by in ("file_type", "type"):
                order_clause = f"c.FileType {order_dir}, c.Title ASC" if "FileType" in cols else "c.Title ASC"
            elif sort_by == "tags" and has_my_tag:
                order_clause = f"TagNames {order_dir}, c.Title ASC"
            elif sort_by in ("playlists", "playlist") and has_playlist:
                order_clause = f"PlaylistNames {order_dir}, c.Title ASC"
            elif sort_by in ("folder", "folder_name"):
                order_clause = f"c.FolderPath {order_dir}, c.Title ASC" if "FolderPath" in cols else "c.Title ASC"
            elif sort_by in ("filename", "file_name"):
                order_clause = f"c.FileNameL {order_dir}, c.Title ASC" if "FileNameL" in cols else "c.Title ASC"
            elif sort_by == "location":
                order_clause = f"c.FolderPath {order_dir}, c.FileNameL {order_dir}" if "FolderPath" in cols else ("c.FileNameL {order_dir}" if "FileNameL" in cols else "c.Title ASC")
            elif sort_by == "title" and is_desc:
                order_clause = "c.Title DESC"
            elif sort_by == "title" and not is_desc and query_str:
                order_clause = """CASE 
                    WHEN lower(c.Title) = lower(:exact) THEN 1
                    WHEN lower(c.Title) LIKE lower(:starts_with) THEN 2
                    ELSE 3
                END, c.Title ASC"""
            else:
                order_clause = "c.Title ASC"

            limit_clause = "LIMIT :limit" if (limit and limit > 0) else ""

            sql = f"""
                SELECT 
                    c.ID, 
                    c.Title, 
                    {artist_col_expr} AS ArtistName, 
                    {album_col_expr} AS AlbumName,
                    {genre_col_expr} AS GenreName,
                    {key_col_expr} AS KeyName,
                    {bpm_expr},
                    {length_expr},
                    {play_expr},
                    {rating_expr},
                    {year_expr},
                    {bitrate_expr},
                    {file_type_expr},
                    {comment_expr},
                    {folder_expr},
                    {filename_expr},
                    {tag_expr},
                    {playlist_expr},
                    {image_path_expr},
                    {stock_date_expr},
                    {sample_rate_expr},
                    {bit_depth_expr},
                    {file_size_expr},
                    {color_expr}
                FROM djmdContent c
                {join_clause}
                WHERE 1=1 {deleted_filter} {play_filter} {search_condition}
                ORDER BY {order_clause}
                {limit_clause};
            """
            params: Dict[str, Any] = {
                "pattern": pattern,
                "exact": query_str,
                "starts_with": starts_with,
            }
            if content_id is not None:
                params["content_id"] = str(content_id)
            if limit and limit > 0:
                params["limit"] = limit

            rows = conn.execute(text(sql), params).mappings().fetchall()

            for r in rows:
                raw_count = r.get("DJPlayCount")
                try:
                    count_int = int(raw_count) if raw_count is not None else 0
                except (ValueError, TypeError):
                    count_int = 0

                bpm_raw = r.get("BPM")
                bpm_val = round(float(bpm_raw) / 100.0, 1) if bpm_raw and bpm_raw > 0 else 0.0

                duration_sec = int(r.get("Length") or 0)
                if duration_sec > 0:
                    mins = duration_sec // 60
                    secs = duration_sec % 60
                    dur_str = f"{mins}:{secs:02d}"
                else:
                    dur_str = "--:--"

                raw_tags = r.get("TagNames")
                tags_list = [t.strip() for t in raw_tags.split("|||") if t.strip()] if raw_tags else []

                raw_playlists = r.get("PlaylistNames")
                playlists_list = [p.strip() for p in raw_playlists.split("|||") if p.strip()] if raw_playlists else []

                raw_fn = r.get("FileNameL") or ""
                raw_fp = r.get("FolderPath") or ""
                file_type_str = resolve_file_type(r.get("FileType"), raw_fn, raw_fp)
                loc_disp = format_location_display(raw_fn, raw_fp)
                f_name, f_dir, fn_disp, f_full = resolve_file_and_folder_paths(raw_fn, raw_fp)

                records.append({
                    "id": str(r.get("ID")),
                    "content_id": str(r.get("ID")),
                    "title": r.get("Title") or "(Untitled)",
                    "artist": r.get("ArtistName") or "Unknown Artist",
                    "album": r.get("AlbumName") or "",
                    "genre": r.get("GenreName") or "",
                    "key": r.get("KeyName") or "",
                    "bpm": bpm_val,
                    "duration_sec": duration_sec,
                    "duration_str": dur_str,
                    "play_count": count_int,
                    "rating": int(r.get("Rating") or 0),
                    "year": int(r.get("ReleaseYear") or 0),
                    "bitrate": int(r.get("BitRate") or 0),
                    "raw_file_type": r.get("FileType"),
                    "file_type": file_type_str,
                    "tags": tags_list,
                    "playlists": playlists_list,
                    "folder_name": f_name,
                    "folder_dir": f_dir,
                    "file_name": fn_disp,
                    "file_path": f_full,
                    "location_display": loc_disp,
                    "comment": r.get("Commnt") or "",
                    "folder_path": raw_fp,
                    "filename": raw_fn,
                    "image_path": r.get("ImagePath") or "",
                    "date_added": str(r.get("StockDate") or "")[:10],
                    "sample_rate": int(r.get("SampleRate") or 0),
                    "bit_depth": int(r.get("BitDepth") or 0),
                    "file_size": int(r.get("FileSize") or 0),
                    "color_id": str(r.get("ColorID") or ""),
                    "table": "djmdContent",
                })

        else:
            # Fallback for generic content table
            count_col = "DJPlayCount" if "DJPlayCount" in cols else ("PlayCount" if "PlayCount" in cols else None)
            count_expr = count_col if count_col else "0"

            play_filter = ""
            if play_count_filter == "played" and count_col:
                play_filter = f"AND ({count_col} > 0)"
            elif play_count_filter == "unplayed" and count_col:
                play_filter = f"AND ({count_col} = 0 OR {count_col} IS NULL)"

            if sort_by == "play_count":
                fallback_order = f"{count_expr} {order_dir}, Title ASC"
            elif sort_by == "title" and is_desc:
                fallback_order = "Title DESC"
            else:
                fallback_order = "Title ASC"

            limit_clause = "LIMIT :limit" if (limit and limit > 0) else ""

            if content_id is not None:
                where_clause = "WHERE ID = :content_id"
                params = {"content_id": str(content_id)}
            else:
                where_clause = f"WHERE Title LIKE :pattern {play_filter}"
                params = {"pattern": pattern}

            if limit and limit > 0:
                params["limit"] = limit

            sql = f"""
                SELECT ID, Title, {count_expr} AS DJPlayCount
                FROM {table_name}
                {where_clause}
                ORDER BY {fallback_order}
                {limit_clause};
            """

            rows = conn.execute(text(sql), params).mappings().fetchall()
            for r in rows:
                raw_count = r.get("DJPlayCount")
                try:
                    count_int = int(raw_count) if raw_count is not None else 0
                except (ValueError, TypeError):
                    count_int = 0
                records.append({
                    "id": str(r.get("ID")),
                    "content_id": str(r.get("ID")),
                    "title": r.get("Title") or "(Untitled)",
                    "artist": "Unknown Artist",
                    "album": "",
                    "genre": "",
                    "key": "",
                    "bpm": 0.0,
                    "duration_sec": 0,
                    "duration_str": "--:--",
                    "play_count": count_int,
                    "rating": 0,
                    "year": 0,
                    "bitrate": 0,
                    "raw_file_type": None,
                    "file_type": "—",
                    "tags": [],
                    "playlists": [],
                    "folder_name": "—",
                    "folder_dir": "",
                    "file_name": "—",
                    "file_path": "",
                    "location_display": "—",
                    "comment": "",
                    "folder_path": "",
                    "filename": "",
                    "image_path": "",
                    "date_added": "",
                    "sample_rate": 0,
                    "bit_depth": 0,
                    "file_size": 0,
                    "color_id": "",
                    "table": table_name,
                })

    return records


def fetch_track_full_details(
    engine: Engine,
    content_id: str,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch complete metadata and history session plays for a specific track.

    Returns a rich dictionary containing all track attributes (title, artist,
    album, key, bpm, audio specs, artwork path on disk, file location, tags, playlists)
    plus a 'history_plays' list of every history session where the track was dropped.
    """
    from rekordbox_collection_manager.rename_history import fetch_track_history_plays

    results = search_tracks(engine, query="", content_id=content_id, limit=1)
    if results:
        track = dict(results[0])
    else:
        track = {
            "id": str(content_id),
            "content_id": str(content_id),
            "title": "(Track Not Found)",
            "artist": "Unknown Artist",
            "album": "",
            "genre": "",
            "key": "",
            "bpm": 0.0,
            "duration_sec": 0,
            "duration_str": "--:--",
            "play_count": 0,
            "rating": 0,
            "year": 0,
            "bitrate": 0,
            "file_type": "—",
            "tags": [],
            "playlists": [],
            "folder_name": "—",
            "folder_dir": "",
            "file_name": "—",
            "file_path": "",
            "location_display": "—",
            "comment": "",
            "folder_path": "",
            "filename": "",
            "image_path": "",
            "date_added": "",
            "sample_rate": 0,
            "bit_depth": 0,
            "file_size": 0,
            "color_id": "",
            "table": "djmdContent",
        }

    raw_img = track.get("image_path")
    track["artwork_path"] = resolve_artwork_path(raw_img, db_path=db_path)
    track["history_plays"] = fetch_track_history_plays(engine, str(content_id))
    return track


def update_track_play_count(
    engine: Engine,
    track_id: str,
    delta: int,
    table_name: Optional[str] = None,
) -> Tuple[int, int]:
    """Increment or decrement play count of a track by delta.

    Returns (old_count, new_count).
    Ensures play count is never negative (minimum 0).
    Updates updated_at timestamp if column is present.
    """
    if table_name is None:
        table_name = detect_content_table(engine)

    now_utc = get_rekordbox_timestamp()

    with engine.begin() as conn:
        cols = {
            r[1]
            for r in conn.exec_driver_sql(f"PRAGMA table_info({table_name});").fetchall()
        }
        play_count_col = "DJPlayCount" if "DJPlayCount" in cols else "PlayCount"

        select_sql = f"SELECT {play_count_col} FROM {table_name} WHERE ID = :id;"
        row = conn.execute(text(select_sql), {"id": track_id}).fetchone()
        if not row:
            raise ValueError(f"Track ID '{track_id}' not found in {table_name}.")

        raw_count = row[0]
        try:
            current_count = int(raw_count) if raw_count is not None else 0
        except (ValueError, TypeError):
            current_count = 0

        new_count = max(0, current_count + delta)

        set_clauses = [f"{play_count_col} = :new_count"]
        params: Dict[str, Any] = {"new_count": new_count, "id": track_id}

        if "updated_at" in cols:
            set_clauses.append("updated_at = :updated_at")
            params["updated_at"] = now_utc

        update_sql = f"UPDATE {table_name} SET {', '.join(set_clauses)} WHERE ID = :id;"
        conn.execute(text(update_sql), params)

    return current_count, new_count


def set_track_play_count(
    engine: Engine,
    track_id: str,
    new_count: int,
    table_name: Optional[str] = None,
) -> Tuple[int, int]:
    """Set the play count of a track to an exact value (minimum 0).

    Returns (old_count, new_count).
    Updates updated_at timestamp if column is present.
    """
    if table_name is None:
        table_name = detect_content_table(engine)

    now_utc = get_rekordbox_timestamp()
    target_count = max(0, int(new_count))

    with engine.begin() as conn:
        cols = {
            r[1]
            for r in conn.exec_driver_sql(f"PRAGMA table_info({table_name});").fetchall()
        }
        play_count_col = "DJPlayCount" if "DJPlayCount" in cols else "PlayCount"

        select_sql = f"SELECT {play_count_col} FROM {table_name} WHERE ID = :id;"
        row = conn.execute(text(select_sql), {"id": track_id}).fetchone()
        if not row:
            raise ValueError(f"Track ID '{track_id}' not found in {table_name}.")

        raw_count = row[0]
        try:
            current_count = int(raw_count) if raw_count is not None else 0
        except (ValueError, TypeError):
            current_count = 0

        set_clauses = [f"{play_count_col} = :new_count"]
        params: Dict[str, Any] = {"new_count": target_count, "id": track_id}

        if "updated_at" in cols:
            set_clauses.append("updated_at = :updated_at")
            params["updated_at"] = now_utc

        update_sql = f"UPDATE {table_name} SET {', '.join(set_clauses)} WHERE ID = :id;"
        conn.execute(text(update_sql), params)

    return current_count, target_count


def batch_update_track_play_counts(
    engine: Engine,
    updates: List[Dict[str, Any]],
    table_name: Optional[str] = None,
) -> int:
    """Batch update the play counts of multiple tracks in a single atomic transaction.

    Each item in `updates` is a dict with:
      - 'id': track_id (str)
      - 'play_count': new_count (int)

    Returns the count of successfully updated records.
    """
    if not updates:
        return 0

    if table_name is None:
        table_name = detect_content_table(engine)

    now_utc = get_rekordbox_timestamp()

    with engine.begin() as conn:
        cols = {
            r[1]
            for r in conn.exec_driver_sql(f"PRAGMA table_info({table_name});").fetchall()
        }
        play_count_col = "DJPlayCount" if "DJPlayCount" in cols else "PlayCount"
        has_updated_at = "updated_at" in cols

        updated_count = 0
        for item in updates:
            track_id = str(item["id"])
            target_count = max(0, int(item["play_count"]))

            set_clauses = [f"{play_count_col} = :new_count"]
            params: Dict[str, Any] = {"new_count": target_count, "id": track_id}

            if has_updated_at:
                set_clauses.append("updated_at = :updated_at")
                params["updated_at"] = now_utc

            update_sql = f"UPDATE {table_name} SET {', '.join(set_clauses)} WHERE ID = :id;"
            res = conn.execute(text(update_sql), params)
            if res.rowcount and res.rowcount > 0:
                updated_count += res.rowcount

    return updated_count


def prompt_action_mode(cli_inc: bool, cli_dec: bool) -> str:
    """Determine initial action mode ('increment', 'decrement', or 'ask')."""
    if cli_inc and not cli_dec:
        return "increment"
    if cli_dec and not cli_inc:
        return "decrement"

    choice = questionary.select(
        "Select play count operation:",
        choices=[
            questionary.Choice("➕ Increment (+1)", value="increment"),
            questionary.Choice("➖ Decrement (-1)", value="decrement"),
            questionary.Choice("🔄 Choose per track", value="ask"),
        ],
        use_indicator=True,
    ).ask()

    return choice or "ask"


def run_interactive_session(
    engine: Engine,
    initial_query: Optional[str] = None,
    action_mode: str = "ask",
) -> None:
    """Run interactive track search, selection, and play count modification loop."""
    current_query = initial_query

    while True:
        if not current_query:
            print("\n")
            search_input = questionary.text(
                "Enter track title to search (or 'q' to quit):"
            ).ask()

            if search_input is None:
                print("Exiting.")
                return

            search_str = search_input.strip()
            if not search_str or search_str.lower() in ("q", "quit", "exit"):
                print("Exiting.")
                return
            current_query = search_str

        # Search matching tracks
        matches = search_tracks(engine, current_query)

        if not matches:
            print(f"⚠️  No tracks found matching '{current_query}'. Please try another search.")
            current_query = None
            continue

        # Prepare choices for selection
        choices: List[Any] = []
        for t in matches:
            artist_display = f" - {t['artist']}" if t.get("artist") else ""
            title = f"{t['title']}{artist_display}  [Play count: {t['play_count']}]"
            choices.append(questionary.Choice(title=title, value=t["id"]))

        choices.append(questionary.Separator())
        choices.append(questionary.Choice(title="🔍 Search for a different title", value="__SEARCH_AGAIN__"))
        choices.append(questionary.Choice(title="✕ Cancel / Exit", value="__CANCEL__"))

        print("\n")
        selected_id = questionary.select(
            f"Select track matching '{current_query}' (Use ↑/↓ arrow keys, Enter to select):",
            choices=choices,
            use_indicator=True,
        ).ask()

        if selected_id is None or selected_id == "__CANCEL__":
            print("Exiting.")
            return

        if selected_id == "__SEARCH_AGAIN__":
            current_query = None
            continue

        selected_track = next((t for t in matches if t["id"] == selected_id), None)
        if not selected_track:
            print("❌ Error: Selected track could not be found.")
            current_query = None
            continue

        # Determine delta (+1 or -1)
        cur_count = selected_track["play_count"]
        delta = 0

        if action_mode == "increment":
            delta = 1
        elif action_mode == "decrement":
            delta = -1
        else:
            action_choice = questionary.select(
                f"Action for '{selected_track['title']}' (Current play count: {cur_count}):",
                choices=[
                    questionary.Choice(f"➕ Increment (+1 ➔ {cur_count + 1})", value=1),
                    questionary.Choice(f"➖ Decrement (-1 ➔ {max(0, cur_count - 1)})", value=-1),
                    questionary.Choice("✕ Cancel", value=0),
                ],
                use_indicator=True,
            ).ask()

            if not action_choice:
                print("Operation cancelled for this track.")
                current_query = None
                continue
            delta = action_choice

        try:
            old_count, new_count = update_track_play_count(engine, selected_id, delta)
            action_symbol = "➕" if delta > 0 else "➖"
            print(
                f"\n✅ {action_symbol} Successfully updated '{selected_track['title']}' "
                f"by {selected_track.get('artist', 'Unknown Artist')}!"
            )
            print(f"   Play count: {old_count} ➔ {new_count}")
            if old_count == 0 and delta < 0:
                print("   (Play count was already 0, kept at minimum 0)")
        except Exception as e:
            print(f"\n❌ Failed to update play count: {e}")
            return

        # Prompt to edit another track
        print()
        again = questionary.confirm(
            "Would you like to edit another track?",
            default=True,
        ).ask()

        if not again:
            print("Done! All changes saved.")
            break

        # Reset query so next loop asks user for a new search query
        current_query = None


def main(argv: Optional[List[str]] = None) -> None:
    """Main CLI entrypoint for edit-play-count."""
    args = parse_args(argv)

    # 1. Always verify Rekordbox is not running before touching DB
    ensure_rekordbox_not_running(force=getattr(args, "force", False))

    # 2. Resolve database path
    db_path = resolve_db_path(args)

    print(f"Opening Rekordbox database: {db_path} ...")
    try:
        engine, _ = open_database(db_path)
    except Exception as e:
        print(f"❌ Error opening database: {e}")
        sys.exit(1)

    # 3. Determine operation mode (+1, -1, or ask)
    action_mode = prompt_action_mode(
        cli_inc=getattr(args, "increment", False),
        cli_dec=getattr(args, "decrement", False),
    )

    # 4. Start interactive session
    try:
        run_interactive_session(
            engine=engine,
            initial_query=args.query,
            action_mode=action_mode,
        )
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
    finally:
        try:
            engine.dispose()
        except Exception:
            pass


if __name__ == "__main__":
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    main()
