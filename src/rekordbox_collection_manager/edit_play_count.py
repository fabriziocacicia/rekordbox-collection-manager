"""Interactive CLI tool to increment and decrement track play counts in Rekordbox databases."""

import argparse
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import questionary
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rekordbox_collection_manager.utils import (
    EXTENSION_TO_FILE_TYPE,
    _resolve_tag_matching_ids,
    build_exclude_streaming_clause,
    build_track_record,
    detect_content_table,
    ensure_rekordbox_not_running,
    format_location_display,
    get_rekordbox_timestamp,
    is_rekordbox_running,
    open_database,
    resolve_artwork_path,
    resolve_db_path,
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
) -> List[Dict[str, Any]]:
    """Search for tracks matching query string with rich metadata and filter support.

    Dynamically queries djmdContent (or generic content table) and joins
    artist, album, genre, key, tags, and real playlists when present.
    Supports text searching, purchased filtering ('all', 'purchased', 'downloaded'),
    play count filtering ('all', 'played', 'unplayed'), musical key, tempo/BPM range,
    file format, multi-tag filtering with match mode ('any'/'all'), multi-playlist
    filtering with match mode ('any'/'all'), streaming exclusion, and column sorting.
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
                tag_cols = {
                    r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdMyTag);").fetchall()
                }
                smt_cols = {
                    r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdSongMyTag);").fetchall()
                }
                tag_del = (
                    "AND (t.rb_local_deleted = 0 OR t.rb_local_deleted IS NULL)"
                    if "rb_local_deleted" in tag_cols
                    else ""
                )
                smt_del = (
                    "AND (smt.rb_local_deleted = 0 OR smt.rb_local_deleted IS NULL)"
                    if "rb_local_deleted" in smt_cols
                    else ""
                )
                tag_seq = "t.Seq ASC, " if "Seq" in tag_cols else ""
                tag_expr = f"""(
                    SELECT GROUP_CONCAT(Name, '|||')
                    FROM (
                        SELECT DISTINCT t.Name
                        FROM djmdSongMyTag smt
                        JOIN djmdMyTag t ON smt.MyTagID = t.ID
                        WHERE smt.ContentID = c.ID
                          {smt_del}
                          {tag_del}
                        ORDER BY {tag_seq}lower(t.Name) ASC
                    )
                ) AS TagNames"""
            else:
                tag_expr = "NULL AS TagNames"

            if has_playlist:
                p_cols = {
                    r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdPlaylist);").fetchall()
                }
                sp_cols = {
                    r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdSongPlaylist);").fetchall()
                }
                p_del = (
                    "AND (p.rb_local_deleted = 0 OR p.rb_local_deleted IS NULL)"
                    if "rb_local_deleted" in p_cols
                    else ""
                )
                sp_del = (
                    "AND (sp.rb_local_deleted = 0 OR sp.rb_local_deleted IS NULL)"
                    if "rb_local_deleted" in sp_cols
                    else ""
                )
                p_attr = (
                    "AND p.Attribute = 0"
                    if "Attribute" in p_cols
                    else ""
                )
                playlist_expr = f"""(
                    SELECT GROUP_CONCAT(Name, '|||')
                    FROM (
                        SELECT DISTINCT p.Name
                        FROM djmdSongPlaylist sp
                        JOIN djmdPlaylist p ON sp.PlaylistID = p.ID
                        WHERE sp.ContentID = c.ID
                          {p_attr}
                          {p_del}
                          {sp_del}
                        ORDER BY lower(p.Name) ASC
                    )
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
            created_at_expr = "c.created_at" if "created_at" in cols else "NULL AS created_at"
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

            # Purchased filter (inside folder named Purchased)
            purchased_sql = """(
                c.FolderPath LIKE '%/Purchased/%' 
                OR c.FolderPath LIKE '%/Purchased' 
                OR c.FolderPath LIKE '%\\Purchased\\%' 
                OR c.FolderPath LIKE '%\\Purchased'
            )"""
            purchased_clause = ""
            p_mode = str(purchased_filter or "all").strip().lower()
            if p_mode == "purchased":
                purchased_clause = f"AND {purchased_sql}"
            elif p_mode in ("downloaded", "unpurchased", "non_purchased", "exclude_purchased"):
                purchased_clause = f"AND NOT {purchased_sql}"

            # Store filter (supports multi-store selection e.g. ["Beatport", "Bandcamp"])
            store_clause = ""
            store_params: Dict[str, Any] = {}
            if store_filter and "Commnt" in cols:
                st_list: List[str] = []
                if isinstance(store_filter, (list, tuple, set)):
                    st_list = [
                        str(s).strip()
                        for s in store_filter
                        if str(s).strip() and str(s).strip().lower() not in ("all", "all stores", "")
                    ]
                else:
                    s_str = str(store_filter).strip()
                    if s_str and s_str.lower() not in ("all", "all stores", ""):
                        st_list = [s_str]

                if st_list:
                    store_conds = []
                    for idx, st_name in enumerate(st_list):
                        st_lower = st_name.lower()
                        p_name = f"st_cond_{idx}"
                        if st_lower == "beatport":
                            store_conds.append(f"lower(c.Commnt) LIKE :{p_name}")
                            store_params[p_name] = "%purchased at beatport.com%"
                        elif st_lower == "qobuz":
                            store_conds.append(f"lower(c.Commnt) LIKE :{p_name}")
                            store_params[p_name] = "%purchased at qobuz.com%"
                        elif st_lower == "traxsource":
                            store_conds.append(f"lower(c.Commnt) LIKE :{p_name}")
                            store_params[p_name] = "%purchased at traxsource.com%"
                        elif st_lower == "bandcamp":
                            store_conds.append(f"(lower(c.Commnt) LIKE :{p_name}_1 OR lower(c.Commnt) LIKE :{p_name}_2)")
                            store_params[f"{p_name}_1"] = "%bandcamp.com%"
                            store_params[f"{p_name}_2"] = "visit %bandcamp.com%"
                    if store_conds:
                        store_clause = f"AND ({' OR '.join(store_conds)})"

            # Key filter (supports single or multi-key selection e.g. ["8A", "8B"] or "8A")
            key_clause = ""
            key_params: Dict[str, Any] = {}
            if key_filter:
                k_list: List[str] = []
                if isinstance(key_filter, (list, tuple, set)):
                    k_list = [
                        str(k).strip()
                        for k in key_filter
                        if str(k).strip() and str(k).strip().lower() not in ("all", "all keys", "")
                    ]
                else:
                    k_str = str(key_filter).strip()
                    if k_str and k_str.lower() not in ("all", "all keys", ""):
                        k_list = [k_str]

                if k_list and "djmdKey" in existing_tables and "KeyID" in cols:
                    k_placeholders = [f":key_f_{i}" for i in range(len(k_list))]
                    for i, k_val in enumerate(k_list):
                        key_params[f"key_f_{i}"] = k_val
                    key_clause = f"AND k.ScaleName IN ({', '.join(k_placeholders)})"

            # BPM filter
            bpm_clause = ""
            if bpm_min is not None and bpm_min > 0 and "BPM" in cols:
                bpm_clause += " AND c.BPM >= :bpm_min_int"
            if bpm_max is not None and bpm_max > 0 and "BPM" in cols:
                bpm_clause += " AND c.BPM <= :bpm_max_int"

            # File type filter (supports single or multi-format selection e.g. ["AIFF", "MP3"])
            file_type_clause = ""
            file_type_params: Dict[str, Any] = {}
            if file_type_filter:
                ft_list: List[str] = []
                if isinstance(file_type_filter, (list, tuple, set)):
                    ft_list = [
                        str(f).strip().lower().lstrip(".")
                        for f in file_type_filter
                        if str(f).strip() and str(f).strip().lower() not in ("all", "all formats", "")
                    ]
                else:
                    f_str = str(file_type_filter).strip().lower().lstrip(".")
                    if f_str and f_str not in ("all", "all formats", ""):
                        ft_list = [f_str]

                if ft_list:
                    ft_conds = []
                    for idx, ext in enumerate(ft_list):
                        ext_variants = [ext]
                        if ext == "aiff":
                            ext_variants = ["aiff", "aif"]
                        elif ext == "aif":
                            ext_variants = ["aif", "aiff"]
                        elif ext == "wav":
                            ext_variants = ["wav", "wave"]

                        sub_conds = []
                        for v_idx, variant in enumerate(ext_variants):
                            param_name = f"ft_ext_{idx}_{v_idx}"
                            sub_conds.append(
                                f"(lower(c.FileNameL) LIKE :{param_name} OR lower(c.FolderPath) LIKE :{param_name})"
                            )
                            file_type_params[param_name] = f"%.{variant}"

                        fmt_upper = ext.upper()
                        ft_int = EXTENSION_TO_FILE_TYPE.get(fmt_upper)
                        if ft_int is not None and "FileType" in cols:
                            sub_conds.append(f"c.FileType = {ft_int}")

                        ft_conds.append(f"({' OR '.join(sub_conds)})")
                    file_type_clause = f"AND ({' OR '.join(ft_conds)})"

            # Tag filter (supports multi-tag filtering and match mode 'any' / 'all')
            tag_clause = ""
            tag_params: Dict[str, Any] = {}
            tag_names: List[str] = []
            if tag_filter:
                if isinstance(tag_filter, (list, tuple, set)):
                    tag_names = [
                        str(t).strip()
                        for t in tag_filter
                        if str(t).strip() and str(t).strip().lower() not in ("all", "all tags")
                    ]
                else:
                    t_str = str(tag_filter).strip()
                    if t_str and t_str.lower() not in ("all", "all tags"):
                        tag_names = [t_str]

            if tag_names and has_my_tag:
                tag_placeholders = [f":tag_f_{i}" for i in range(len(tag_names))]
                for i, name in enumerate(tag_names):
                    tag_params[f"tag_f_{i}"] = name

                if str(tag_match_mode).strip().lower() == "all" and len(tag_names) > 1:
                    tag_clause = f"""AND c.ID IN (
                        SELECT smt.ContentID FROM djmdSongMyTag smt
                        JOIN djmdMyTag t ON smt.MyTagID = t.ID
                        WHERE t.Name IN ({", ".join(tag_placeholders)})
                          AND (smt.rb_local_deleted = 0 OR smt.rb_local_deleted IS NULL)
                          AND (t.rb_local_deleted = 0 OR t.rb_local_deleted IS NULL)
                        GROUP BY smt.ContentID
                        HAVING COUNT(DISTINCT t.Name) >= {len(tag_names)}
                    )"""
                else:
                    tag_clause = f"""AND c.ID IN (
                        SELECT smt.ContentID FROM djmdSongMyTag smt
                        JOIN djmdMyTag t ON smt.MyTagID = t.ID
                        WHERE t.Name IN ({", ".join(tag_placeholders)})
                          AND (smt.rb_local_deleted = 0 OR smt.rb_local_deleted IS NULL)
                          AND (t.rb_local_deleted = 0 OR t.rb_local_deleted IS NULL)
                    )"""

            # Playlist filter (supports multi-playlist filtering and match mode 'any' / 'all')
            playlist_clause = ""
            playlist_params: Dict[str, Any] = {}
            playlist_names: List[str] = []
            if playlist_filter:
                if isinstance(playlist_filter, (list, tuple, set)):
                    playlist_names = [
                        str(p).strip()
                        for p in playlist_filter
                        if str(p).strip() and str(p).strip().lower() not in ("all", "all playlists")
                    ]
                else:
                    p_str = str(playlist_filter).strip()
                    if p_str and p_str.lower() not in ("all", "all playlists"):
                        playlist_names = [p_str]

            if playlist_names and has_playlist:
                pl_placeholders = [f":pl_f_{i}" for i in range(len(playlist_names))]
                for i, name in enumerate(playlist_names):
                    playlist_params[f"pl_f_{i}"] = name

                if str(playlist_match_mode).strip().lower() == "all" and len(playlist_names) > 1:
                    playlist_clause = f"""AND c.ID IN (
                        SELECT sp.ContentID FROM djmdSongPlaylist sp
                        JOIN djmdPlaylist p ON sp.PlaylistID = p.ID
                        WHERE p.Attribute = 0 AND p.Name IN ({", ".join(pl_placeholders)})
                          AND (p.rb_local_deleted = 0 OR p.rb_local_deleted IS NULL)
                          AND (sp.rb_local_deleted = 0 OR sp.rb_local_deleted IS NULL)
                        GROUP BY sp.ContentID
                        HAVING COUNT(DISTINCT p.Name) >= {len(playlist_names)}
                    )"""
                else:
                    playlist_clause = f"""AND c.ID IN (
                        SELECT sp.ContentID FROM djmdSongPlaylist sp
                        JOIN djmdPlaylist p ON sp.PlaylistID = p.ID
                        WHERE p.Attribute = 0 AND p.Name IN ({", ".join(pl_placeholders)})
                          AND (p.rb_local_deleted = 0 OR p.rb_local_deleted IS NULL)
                          AND (sp.rb_local_deleted = 0 OR sp.rb_local_deleted IS NULL)
                    )"""

            # Streaming tracks filter (exclude Spotify, Apple Music, and other streaming tracks)
            streaming_filter = ""
            if exclude_streaming and content_id is None:
                streaming_filter = build_exclude_streaming_clause(cols, table_alias="c")

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
            elif sort_by in ("purchased", "is_purchased"):
                order_clause = f"{purchased_sql} {order_dir}, c.Title ASC"
            elif sort_by in ("purchased_at", "store"):
                if "Commnt" in cols:
                    store_expr = """CASE
                        WHEN lower(c.Commnt) LIKE '%bandcamp%' THEN 'Bandcamp'
                        WHEN lower(c.Commnt) LIKE '%beatport%' THEN 'Beatport'
                        WHEN lower(c.Commnt) LIKE '%qobuz%' AND lower(c.Commnt) LIKE '%purchased%' THEN 'Qobuz'
                        WHEN lower(c.Commnt) LIKE '%traxsource%' THEN 'Traxsource'
                        ELSE ''
                    END"""
                    order_clause = f"{store_expr} {order_dir}, c.Title ASC"
                else:
                    order_clause = "c.Title ASC"
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
                    {created_at_expr},
                    {sample_rate_expr},
                    {bit_depth_expr},
                    {file_size_expr},
                    {color_expr}
                FROM djmdContent c
                {join_clause}
                WHERE 1=1 {deleted_filter} {play_filter} {purchased_clause} {store_clause} {key_clause} {bpm_clause} {file_type_clause} {tag_clause} {playlist_clause} {streaming_filter} {search_condition}
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
            if store_clause:
                params.update(store_params)
            if key_clause:
                params.update(key_params)
            if bpm_min is not None and bpm_min > 0:
                params["bpm_min_int"] = int(bpm_min * 100)
            if bpm_max is not None and bpm_max > 0:
                params["bpm_max_int"] = int(bpm_max * 100)
            if file_type_clause:
                params.update(file_type_params)
            if tag_clause:
                params.update(tag_params)
            if playlist_clause:
                params.update(playlist_params)
            if limit and limit > 0:
                params["limit"] = limit

            rows = conn.execute(text(sql), params).mappings().fetchall()

            for r in rows:
                records.append(build_track_record(r, extra={"table": "djmdContent"}))

        else:
            # Fallback for generic content table
            count_col = "DJPlayCount" if "DJPlayCount" in cols else ("PlayCount" if "PlayCount" in cols else None)
            count_expr = count_col if count_col else "0"

            play_filter = ""
            if play_count_filter == "played" and count_col:
                play_filter = f"AND ({count_col} > 0)"
            elif play_count_filter == "unplayed" and count_col:
                play_filter = f"AND ({count_col} = 0 OR {count_col} IS NULL)"

            fallback_purchased_clause = ""
            if "FolderPath" in cols:
                fb_purchased_sql = """(
                    FolderPath LIKE '%/Purchased/%' 
                    OR FolderPath LIKE '%/Purchased' 
                    OR FolderPath LIKE '%\\Purchased\\%' 
                    OR FolderPath LIKE '%\\Purchased'
                )"""
                p_mode = str(purchased_filter or "all").strip().lower()
                if p_mode == "purchased":
                    fallback_purchased_clause = f"AND {fb_purchased_sql}"
                elif p_mode in ("downloaded", "unpurchased", "non_purchased", "exclude_purchased"):
                    fallback_purchased_clause = f"AND NOT {fb_purchased_sql}"

            if sort_by == "play_count":
                fallback_order = f"{count_expr} {order_dir}, Title ASC"
            elif sort_by == "title" and is_desc:
                fallback_order = "Title DESC"
            else:
                fallback_order = "Title ASC"

            limit_clause = "LIMIT :limit" if (limit and limit > 0) else ""

            fallback_streaming_filter = ""
            if exclude_streaming and content_id is None:
                fallback_streaming_filter = build_exclude_streaming_clause(cols, table_alias=None)

            if content_id is not None:
                where_clause = "WHERE ID = :content_id"
                params = {"content_id": str(content_id)}
            else:
                where_clause = f"WHERE Title LIKE :pattern {play_filter} {fallback_purchased_clause} {fallback_streaming_filter}"
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
                records.append(build_track_record(r, extra={"table": table_name}))

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
        track = build_track_record({"ID": content_id, "Title": "(Track Not Found)"})

    raw_img = track.get("image_path")
    track["artwork_path"] = resolve_artwork_path(raw_img, db_path=db_path)
    track["history_plays"] = fetch_track_history_plays(engine, str(content_id))
    return track


def fetch_all_playlists(engine: Engine) -> List[Dict[str, Any]]:
    """Fetch all standard user playlists from Rekordbox database.

    Filters for standard playlists (Attribute = 0) and non-deleted entries.
    Returns a list of dicts: [{'id': str, 'name': str, 'parent_id': Optional[str]}],
    sorted alphabetically by Name.
    """
    with engine.connect() as conn:
        cols = {
            r[1]
            for r in conn.exec_driver_sql("PRAGMA table_info(djmdPlaylist);").fetchall()
        }
        if not cols:
            return []

        attr_clause = "AND Attribute = 0" if "Attribute" in cols else ""
        del_clause = (
            "AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL)"
            if "rb_local_deleted" in cols
            else ""
        )
        parent_col = "ParentID" if "ParentID" in cols else "NULL AS ParentID"

        sql = f"""
            SELECT ID, Name, {parent_col}
            FROM djmdPlaylist
            WHERE 1=1 {attr_clause} {del_clause}
              AND Name IS NOT NULL AND TRIM(Name) != ''
            ORDER BY lower(Name) ASC;
        """
        rows = conn.execute(text(sql)).fetchall()
        return [
            {
                "id": str(r[0]),
                "name": str(r[1]).strip(),
                "parent_id": str(r[2]) if r[2] else None,
            }
            for r in rows
            if r[1] and str(r[1]).strip()
        ]


def fetch_distinct_keys(engine: Engine) -> List[str]:
    """Fetch distinct musical keys present in the Rekordbox library."""
    with engine.connect() as conn:
        tables = {
            r[0]
            for r in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table';"
            ).fetchall()
        }
        if "djmdKey" not in tables:
            return []

        table_name = next((t for t in ("djmdContent", "content") if t in tables), None)
        if table_name:
            cols = {
                r[1]
                for r in conn.exec_driver_sql(f"PRAGMA table_info({table_name});").fetchall()
            }
            if "KeyID" in cols:
                del_clause = (
                    "AND (c.rb_local_deleted = 0 OR c.rb_local_deleted IS NULL)"
                    if "rb_local_deleted" in cols
                    else ""
                )
                streaming_clause = build_exclude_streaming_clause(cols, table_alias="c")

                sql = f"""
                    SELECT DISTINCT k.ScaleName
                    FROM {table_name} c
                    JOIN djmdKey k ON c.KeyID = k.ID
                    WHERE k.ScaleName IS NOT NULL AND TRIM(k.ScaleName) != ''
                      {del_clause} {streaming_clause}
                    ORDER BY k.ScaleName;
                """
                rows = conn.exec_driver_sql(sql).fetchall()
                return [str(r[0]).strip() for r in rows if r[0] and str(r[0]).strip()]

        # Fallback if KeyID column is not present in collection table
        rows = conn.exec_driver_sql(
            "SELECT DISTINCT ScaleName FROM djmdKey WHERE ScaleName IS NOT NULL AND TRIM(ScaleName) != '' ORDER BY ScaleName;"
        ).fetchall()
        return [str(r[0]).strip() for r in rows if r[0] and str(r[0]).strip()]


def fetch_distinct_file_types(engine: Engine) -> List[str]:
    """Fetch distinct audio file formats (e.g. MP3, AIFF, FLAC, WAV, M4A) present in the database.

    Dynamically queries djmdContent / content (ignoring soft-deleted and streaming tracks),
    mapping Rekordbox FileType IDs and inspecting file extensions.
    Returns an alphabetically sorted list of uppercase format strings (e.g. ['AIFF', 'M4A', 'MP3', 'WAV']).
    """
    with engine.connect() as conn:
        tables = {
            r[0]
            for r in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table';"
            ).fetchall()
        }
        table_name = next((t for t in ("djmdContent", "content") if t in tables), None)
        if not table_name:
            return []

        cols = {
            r[1]
            for r in conn.exec_driver_sql(f"PRAGMA table_info({table_name});").fetchall()
        }
        del_filter = (
            "AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL)"
            if "rb_local_deleted" in cols
            else ""
        )
        streaming_filter = build_exclude_streaming_clause(cols, table_alias=None)

        formats: Set[str] = set()
        known_ft_ids: Set[Any] = set()

        # 1. Inspect FileType integer column if present
        if "FileType" in cols:
            ft_rows = conn.exec_driver_sql(
                f"SELECT DISTINCT FileType FROM {table_name} WHERE FileType IS NOT NULL {del_filter} {streaming_filter};"
            ).fetchall()
            for r in ft_rows:
                raw_ft = r[0]
                fmt_name = resolve_file_type(raw_ft)
                if fmt_name and fmt_name not in ("—", "Spotify", "Apple Music"):
                    formats.add(fmt_name)
                    known_ft_ids.add(raw_ft)

        # 2. Inspect FileNameL or FolderPath for tracks where FileType is NULL or not mapped
        target_col = "FileNameL" if "FileNameL" in cols else ("FolderPath" if "FolderPath" in cols else None)
        if target_col:
            ft_cond = ""
            if known_ft_ids:
                ids_str = ", ".join(str(i) for i in known_ft_ids)
                ft_cond = f"AND (FileType IS NULL OR FileType NOT IN ({ids_str}))"
            elif "FileType" in cols:
                ft_cond = "AND FileType IS NULL"

            fn_rows = conn.exec_driver_sql(
                f"SELECT DISTINCT {target_col} FROM {table_name} WHERE {target_col} IS NOT NULL AND TRIM({target_col}) != '' {ft_cond} {del_filter} {streaming_filter};"
            ).fetchall()
            for r in fn_rows:
                fmt_name = resolve_file_type(None, filename=r[0])
                if fmt_name and fmt_name not in ("—", "Spotify", "Apple Music"):
                    formats.add(fmt_name)

        return sorted(list(formats))


def add_track_to_playlist(
    engine: Engine,
    content_id: str,
    playlist_name_or_id: str,
) -> bool:
    """Add a track to a playlist in Rekordbox database.

    If association was previously soft-deleted, reactivates it (rb_local_deleted = 0).
    Otherwise creates a new djmdSongPlaylist record with auto-incremented TrackNo.
    Returns True if successfully added or already present.
    """
    now_utc = get_rekordbox_timestamp()

    with engine.begin() as conn:
        pl_cols = {
            r[1]
            for r in conn.exec_driver_sql("PRAGMA table_info(djmdPlaylist);").fetchall()
        }
        sp_cols = {
            r[1]
            for r in conn.exec_driver_sql("PRAGMA table_info(djmdSongPlaylist);").fetchall()
        }

        if not pl_cols or not sp_cols:
            raise ValueError("Required playlist tables (djmdPlaylist, djmdSongPlaylist) not found.")

        # Find playlist by ID or Name
        pl_del = "AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL)" if "rb_local_deleted" in pl_cols else ""
        pl_attr = "AND Attribute = 0" if "Attribute" in pl_cols else ""

        # Try ID match first
        find_sql = f"SELECT ID, Name FROM djmdPlaylist WHERE ID = :query {pl_del} LIMIT 1;"
        pl_row = conn.execute(text(find_sql), {"query": playlist_name_or_id}).fetchone()

        if not pl_row:
            # Try Name match (case-insensitive)
            find_sql = f"SELECT ID, Name FROM djmdPlaylist WHERE lower(Name) = lower(:query) {pl_attr} {pl_del} LIMIT 1;"
            pl_row = conn.execute(text(find_sql), {"query": playlist_name_or_id.strip()}).fetchone()

        if not pl_row:
            raise ValueError(f"Playlist '{playlist_name_or_id}' not found.")

        pl_id = str(pl_row[0])
        content_id_str = str(content_id)

        # Check existing membership in djmdSongPlaylist
        has_rb_del = "rb_local_deleted" in sp_cols
        check_sql = (
            "SELECT ID"
            + (", rb_local_deleted" if has_rb_del else "")
            + " FROM djmdSongPlaylist WHERE PlaylistID = :pl_id AND ContentID = :c_id LIMIT 1;"
        )
        existing = conn.execute(text(check_sql), {"pl_id": pl_id, "c_id": content_id_str}).fetchone()

        if existing:
            entry_id = str(existing[0])
            if has_rb_del:
                is_del = existing[1]
                if is_del == 0 or is_del is None:
                    # Already active
                    return True
                # Reactivate soft-deleted row
                update_parts = ["rb_local_deleted = 0"]
                params: Dict[str, Any] = {"id": entry_id}
                if "updated_at" in sp_cols:
                    update_parts.append("updated_at = :now")
                    params["now"] = now_utc
                if "rb_local_synced" in sp_cols:
                    update_parts.append("rb_local_synced = 1")
                update_sql = f"UPDATE djmdSongPlaylist SET {', '.join(update_parts)} WHERE ID = :id;"
                conn.execute(text(update_sql), params)
                return True
            else:
                return True

        # Calculate next TrackNo
        track_no_sql = "SELECT COALESCE(MAX(TrackNo), 0) + 1 FROM djmdSongPlaylist WHERE PlaylistID = :pl_id;"
        next_track_no = conn.execute(text(track_no_sql), {"pl_id": pl_id}).scalar() or 1

        new_id = str(uuid.uuid4())
        record_data = {
            "ID": new_id,
            "PlaylistID": pl_id,
            "ContentID": content_id_str,
            "TrackNo": int(next_track_no),
            "UUID": new_id,
            "rb_data_status": 0,
            "rb_local_data_status": 0,
            "rb_local_deleted": 0,
            "rb_local_synced": 1,
            "created_at": now_utc,
            "updated_at": now_utc,
        }
        insert_cols = [k for k in record_data if k in sp_cols]
        col_names = ", ".join(insert_cols)
        placeholders = ", ".join(f":{k}" for k in insert_cols)
        insert_sql = f"INSERT INTO djmdSongPlaylist ({col_names}) VALUES ({placeholders});"
        conn.execute(text(insert_sql), {k: record_data[k] for k in insert_cols})
        return True


def remove_track_from_playlist(
    engine: Engine,
    content_id: str,
    playlist_name_or_id: str,
) -> bool:
    """Remove a track from a playlist in Rekordbox database.

    If rb_local_deleted column exists, performs a soft delete (rb_local_deleted = 1).
    Otherwise performs a standard DELETE.
    Returns True if removed.
    """
    now_utc = get_rekordbox_timestamp()

    with engine.begin() as conn:
        pl_cols = {
            r[1]
            for r in conn.exec_driver_sql("PRAGMA table_info(djmdPlaylist);").fetchall()
        }
        sp_cols = {
            r[1]
            for r in conn.exec_driver_sql("PRAGMA table_info(djmdSongPlaylist);").fetchall()
        }

        if not pl_cols or not sp_cols:
            return False

        # Find playlist ID
        find_sql = "SELECT ID FROM djmdPlaylist WHERE ID = :query LIMIT 1;"
        pl_row = conn.execute(text(find_sql), {"query": playlist_name_or_id}).fetchone()

        if not pl_row:
            find_sql = "SELECT ID FROM djmdPlaylist WHERE lower(Name) = lower(:query) LIMIT 1;"
            pl_row = conn.execute(text(find_sql), {"query": playlist_name_or_id.strip()}).fetchone()

        if not pl_row:
            return False

        pl_id = str(pl_row[0])
        content_id_str = str(content_id)

        if "rb_local_deleted" in sp_cols:
            update_parts = ["rb_local_deleted = 1"]
            params: Dict[str, Any] = {"pl_id": pl_id, "c_id": content_id_str}
            if "updated_at" in sp_cols:
                update_parts.append("updated_at = :now")
                params["now"] = now_utc
            sql = (
                f"UPDATE djmdSongPlaylist SET {', '.join(update_parts)} "
                "WHERE PlaylistID = :pl_id AND ContentID = :c_id AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL);"
            )
            res = conn.execute(text(sql), params)
            return (res.rowcount or 0) > 0
        else:
            del_sql = "DELETE FROM djmdSongPlaylist WHERE PlaylistID = :pl_id AND ContentID = :c_id;"
            res = conn.execute(text(del_sql), {"pl_id": pl_id, "c_id": content_id_str})
            return (res.rowcount or 0) > 0


def fetch_all_tags(engine: Engine) -> List[Dict[str, Any]]:
    """Fetch all user My Tags from Rekordbox database.

    Filters for standard selectable tags (Attribute = 0) and non-deleted entries.
    Returns a list of dicts: [{'id': str, 'name': str, 'parent_id': Optional[str]}],
    sorted alphabetically by Name.
    """
    with engine.connect() as conn:
        cols = {
            r[1]
            for r in conn.exec_driver_sql("PRAGMA table_info(djmdMyTag);").fetchall()
        }
        if not cols:
            return []

        attr_clause = "AND Attribute = 0" if "Attribute" in cols else ""
        del_clause = (
            "AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL)"
            if "rb_local_deleted" in cols
            else ""
        )
        parent_col = "ParentID" if "ParentID" in cols else "NULL AS ParentID"

        sql = f"""
            SELECT ID, Name, {parent_col}
            FROM djmdMyTag
            WHERE 1=1 {attr_clause} {del_clause}
              AND Name IS NOT NULL AND TRIM(Name) != ''
            ORDER BY lower(Name) ASC;
        """
        rows = conn.execute(text(sql)).fetchall()
        return [
            {
                "id": str(r[0]),
                "name": str(r[1]).strip(),
                "parent_id": str(r[2]) if r[2] else None,
            }
            for r in rows
            if r[1] and str(r[1]).strip()
        ]


def add_track_to_tag(
    engine: Engine,
    content_id: str,
    tag_name_or_id: str,
) -> bool:
    """Add a track to a My Tag in Rekordbox database.

    If association was previously soft-deleted, reactivates it (rb_local_deleted = 0).
    Otherwise creates a new djmdSongMyTag record.
    Returns True if successfully added or already present.
    """
    now_utc = get_rekordbox_timestamp()

    with engine.begin() as conn:
        tag_cols = {
            r[1]
            for r in conn.exec_driver_sql("PRAGMA table_info(djmdMyTag);").fetchall()
        }
        smt_cols = {
            r[1]
            for r in conn.exec_driver_sql("PRAGMA table_info(djmdSongMyTag);").fetchall()
        }

        if not tag_cols or not smt_cols:
            raise ValueError("Required My Tag tables (djmdMyTag, djmdSongMyTag) not found.")

        # Find tag by ID or Name
        tag_del = "AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL)" if "rb_local_deleted" in tag_cols else ""
        tag_attr = "AND Attribute = 0" if "Attribute" in tag_cols else ""

        # Try ID match first
        find_sql = f"SELECT ID, Name FROM djmdMyTag WHERE ID = :query {tag_del} LIMIT 1;"
        tag_row = conn.execute(text(find_sql), {"query": tag_name_or_id}).fetchone()

        if not tag_row:
            # Try Name match (case-insensitive)
            find_sql = f"SELECT ID, Name FROM djmdMyTag WHERE lower(Name) = lower(:query) {tag_attr} {tag_del} LIMIT 1;"
            tag_row = conn.execute(text(find_sql), {"query": tag_name_or_id.strip()}).fetchone()

        if not tag_row:
            raise ValueError(f"My Tag '{tag_name_or_id}' not found.")

        tag_id = str(tag_row[0])
        content_id_str = str(content_id)

        # Check existing association in djmdSongMyTag (matching signed/unsigned representations)
        has_rb_del = "rb_local_deleted" in smt_cols
        matching_tids = _resolve_tag_matching_ids(tag_id)
        placeholders = ", ".join(f":tid_{i}" for i in range(len(matching_tids)))
        params: Dict[str, Any] = {f"tid_{i}": tid for i, tid in enumerate(matching_tids)}
        params["c_id"] = content_id_str

        check_sql = (
            "SELECT ID"
            + (", rb_local_deleted" if has_rb_del else "")
            + f" FROM djmdSongMyTag WHERE MyTagID IN ({placeholders}) AND ContentID = :c_id"
            + " ORDER BY (CASE WHEN rb_local_deleted = 0 OR rb_local_deleted IS NULL THEN 0 ELSE 1 END) ASC LIMIT 1;"
        )
        existing = conn.execute(text(check_sql), params).fetchone()

        if existing:
            entry_id = str(existing[0])
            if has_rb_del:
                is_del = existing[1]
                if is_del == 0 or is_del is None:
                    # Already active
                    return True
                # Reactivate soft-deleted row
                update_parts = ["rb_local_deleted = 0"]
                params = {"id": entry_id}
                if "updated_at" in smt_cols:
                    update_parts.append("updated_at = :now")
                    params["now"] = now_utc
                if "rb_local_synced" in smt_cols:
                    update_parts.append("rb_local_synced = 1")
                update_sql = f"UPDATE djmdSongMyTag SET {', '.join(update_parts)} WHERE ID = :id;"
                conn.execute(text(update_sql), params)
                return True
            else:
                return True

        # Calculate next TrackNo if column exists
        track_no = None
        if "TrackNo" in smt_cols:
            track_no_sql = "SELECT COALESCE(MAX(TrackNo), 0) + 1 FROM djmdSongMyTag WHERE MyTagID = :tag_id;"
            track_no = conn.execute(text(track_no_sql), {"tag_id": tag_id}).scalar() or 1

        new_id = str(uuid.uuid4())
        record_data = {
            "ID": new_id,
            "MyTagID": tag_id,
            "ContentID": content_id_str,
            "TrackNo": int(track_no) if track_no is not None else None,
            "UUID": new_id,
            "rb_data_status": 0,
            "rb_local_data_status": 0,
            "rb_local_deleted": 0,
            "rb_local_synced": 1,
            "created_at": now_utc,
            "updated_at": now_utc,
        }
        insert_cols = [k for k in record_data if k in smt_cols]
        col_names = ", ".join(insert_cols)
        placeholders = ", ".join(f":{k}" for k in insert_cols)
        insert_sql = f"INSERT INTO djmdSongMyTag ({col_names}) VALUES ({placeholders});"
        conn.execute(text(insert_sql), {k: record_data[k] for k in insert_cols})
        return True


def remove_track_from_tag(
    engine: Engine,
    content_id: str,
    tag_name_or_id: str,
) -> bool:
    """Remove a track from a My Tag in Rekordbox database.

    If rb_local_deleted column exists, performs a soft delete (rb_local_deleted = 1).
    Otherwise performs a standard DELETE.
    Returns True if removed.
    """
    now_utc = get_rekordbox_timestamp()

    with engine.begin() as conn:
        tag_cols = {
            r[1]
            for r in conn.exec_driver_sql("PRAGMA table_info(djmdMyTag);").fetchall()
        }
        smt_cols = {
            r[1]
            for r in conn.exec_driver_sql("PRAGMA table_info(djmdSongMyTag);").fetchall()
        }

        if not tag_cols or not smt_cols:
            return False

        # Find tag ID
        find_sql = "SELECT ID FROM djmdMyTag WHERE ID = :query LIMIT 1;"
        tag_row = conn.execute(text(find_sql), {"query": tag_name_or_id}).fetchone()

        if not tag_row:
            find_sql = "SELECT ID FROM djmdMyTag WHERE lower(Name) = lower(:query) LIMIT 1;"
            tag_row = conn.execute(text(find_sql), {"query": tag_name_or_id.strip()}).fetchone()

        if not tag_row:
            return False

        tag_id = str(tag_row[0])
        content_id_str = str(content_id)

        matching_tids = _resolve_tag_matching_ids(tag_id)
        placeholders = ", ".join(f":tid_{i}" for i in range(len(matching_tids)))
        params: Dict[str, Any] = {f"tid_{i}": tid for i, tid in enumerate(matching_tids)}
        params["c_id"] = content_id_str

        if "rb_local_deleted" in smt_cols:
            update_parts = ["rb_local_deleted = 1"]
            if "updated_at" in smt_cols:
                update_parts.append("updated_at = :now")
                params["now"] = now_utc
            sql = (
                f"UPDATE djmdSongMyTag SET {', '.join(update_parts)} "
                f"WHERE MyTagID IN ({placeholders}) AND ContentID = :c_id AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL);"
            )
            res = conn.execute(text(sql), params)
            return (res.rowcount or 0) > 0
        else:
            del_sql = f"DELETE FROM djmdSongMyTag WHERE MyTagID IN ({placeholders}) AND ContentID = :c_id;"
            res = conn.execute(text(del_sql), params)
            return (res.rowcount or 0) > 0


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
