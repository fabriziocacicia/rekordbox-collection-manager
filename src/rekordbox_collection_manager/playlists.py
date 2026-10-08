"""Playlists database queries and management for Rekordbox.

Provides methods to query playlist hierarchies, folders, smart playlists,
and retrieve track lists for individual playlists with full metadata.
"""

import random
import uuid
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rekordbox_collection_manager.utils import (
    build_exclude_streaming_clause,
    build_track_record,
    get_rekordbox_timestamp,
)


def parse_smartlist_to_sql(
    smartlist_xml: str,
    artist_expr: str = "c.SrcArtistName",
    album_expr: str = "NULL",
    genre_expr: str = "NULL",
    key_expr: str = "NULL",
    param_prefix: str = "sl_",
) -> Tuple[str, Dict[str, Any]]:
    """Parse Rekordbox SmartList XML criteria into a SQLite WHERE clause and parameter dictionary.

    Handles:
    - myTag (filtering via djmdSongMyTag with signed/unsigned 32-bit integer conversion)
    - bpm (scaled integer or float)
    - artist, album, genre, key, title/name, fileName, comments
    - rating, year, duration/length, counter/DJPlayCount, bitrate, grouping
    - LogicalOperator 1 (ALL/AND) and 2 (ANY/OR)
    """
    if not smartlist_xml or not smartlist_xml.strip():
        return "1=1", {}
    try:
        root = ET.fromstring(smartlist_xml)
    except Exception:
        return "1=1", {}

    log_op = root.attrib.get("LogicalOperator", "1")
    join_op = " OR " if log_op == "2" else " AND "

    cond_sqls: List[str] = []
    params: Dict[str, Any] = {}

    def handle_str_op(expr: str, op: int, val: str, p_name: str) -> None:
        if op == 1:  # EQUAL
            cond_sqls.append(f"{expr} = :{p_name}")
            params[p_name] = val.lower()
        elif op == 2:  # NOT_EQUAL
            cond_sqls.append(f"{expr} != :{p_name}")
            params[p_name] = val.lower()
        elif op == 8:  # CONTAINS
            cond_sqls.append(f"{expr} LIKE :{p_name}")
            params[p_name] = f"%{val.lower()}%"
        elif op == 9:  # NOT_CONTAINS
            cond_sqls.append(f"{expr} NOT LIKE :{p_name}")
            params[p_name] = f"%{val.lower()}%"
        elif op == 10:  # STARTS_WITH
            cond_sqls.append(f"{expr} LIKE :{p_name}")
            params[p_name] = f"{val.lower()}%"
        elif op == 11:  # ENDS_WITH
            cond_sqls.append(f"{expr} LIKE :{p_name}")
            params[p_name] = f"%{val.lower()}"

    def handle_num_op(
        col_expr: str,
        op: int,
        val_l_str: str,
        val_r_str: str,
        p_l: str,
        p_r: str,
        scale: float = 1.0,
    ) -> None:
        try:
            val_l_num = float(val_l_str) * scale
            val_l_int = int(val_l_num)
        except ValueError:
            return

        if op == 1:
            cond_sqls.append(f"{col_expr} = :{p_l}")
            params[p_l] = val_l_int
        elif op == 2:
            cond_sqls.append(f"{col_expr} != :{p_l}")
            params[p_l] = val_l_int
        elif op == 3:
            cond_sqls.append(f"{col_expr} > :{p_l}")
            params[p_l] = val_l_int
        elif op == 4:
            cond_sqls.append(f"{col_expr} < :{p_l}")
            params[p_l] = val_l_int
        elif op == 5 and val_r_str:
            try:
                val_r_num = float(val_r_str) * scale
                val_r_int = int(val_r_num)
                cond_sqls.append(f"{col_expr} BETWEEN :{p_l} AND :{p_r}")
                params[p_l] = val_l_int
                params[p_r] = val_r_int
            except ValueError:
                pass

    for idx, child in enumerate(root.findall("CONDITION")):
        prop = child.attrib.get("PropertyName", "")
        op = int(child.attrib.get("Operator", 1))
        val_l = child.attrib.get("ValueLeft", "")
        val_r = child.attrib.get("ValueRight", "")
        p_l = f"{param_prefix}{idx}_l"
        p_r = f"{param_prefix}{idx}_r"

        if prop == "myTag":
            try:
                v_int = int(val_l)
                uid = str(v_int + (1 << 32)) if v_int < 0 else str(v_int)
            except ValueError:
                uid = val_l
            if op == 9:  # NOT_CONTAINS
                cond_sqls.append(
                    f"c.ID NOT IN (SELECT smt.ContentID FROM djmdSongMyTag smt WHERE smt.MyTagID IN (:{p_l}_1, :{p_l}_2) AND (smt.rb_local_deleted = 0 OR smt.rb_local_deleted IS NULL))"
                )
            else:
                cond_sqls.append(
                    f"c.ID IN (SELECT smt.ContentID FROM djmdSongMyTag smt WHERE smt.MyTagID IN (:{p_l}_1, :{p_l}_2) AND (smt.rb_local_deleted = 0 OR smt.rb_local_deleted IS NULL))"
                )
            params[f"{p_l}_1"] = uid
            params[f"{p_l}_2"] = val_l

        elif prop in ("bpm", "BPM"):
            scale = 100.0 if (val_l and float(val_l) < 500) else 1.0
            handle_num_op("c.BPM", op, val_l, val_r, p_l, p_r, scale=scale)

        elif prop in ("artist", "artistName"):
            handle_str_op(f"lower({artist_expr})", op, val_l, p_l)

        elif prop in ("album", "albumName"):
            handle_str_op(f"lower({album_expr})", op, val_l, p_l)

        elif prop in ("genre", "genreName"):
            handle_str_op(f"lower({genre_expr})", op, val_l, p_l)

        elif prop in ("key", "Key"):
            handle_str_op(f"lower({key_expr})", op, val_l, p_l)

        elif prop in ("name", "Title"):
            handle_str_op("lower(c.Title)", op, val_l, p_l)

        elif prop in ("fileName", "filename"):
            handle_str_op("lower(COALESCE(c.FileNameL, ''))", op, val_l, p_l)

        elif prop in ("comments", "comment"):
            handle_str_op("lower(COALESCE(c.Commnt, ''))", op, val_l, p_l)

        elif prop in ("rating", "Rating"):
            handle_num_op("c.Rating", op, val_l, val_r, p_l, p_r)

        elif prop in ("year", "Year"):
            handle_num_op("c.ReleaseYear", op, val_l, val_r, p_l, p_r)

        elif prop in ("duration", "Length"):
            handle_num_op("c.Length", op, val_l, val_r, p_l, p_r)

        elif prop in ("counter", "DJPlayCount"):
            handle_num_op("c.DJPlayCount", op, val_l, val_r, p_l, p_r)

        elif prop in ("bitrate", "BitRate"):
            handle_num_op("c.BitRate", op, val_l, val_r, p_l, p_r)

        elif prop in ("grouping", "ColorID"):
            handle_num_op("c.ColorID", op, val_l, val_r, p_l, p_r)

        elif prop in ("stockDate", "StockDate"):
            handle_str_op("c.StockDate", op, val_l, p_l)

        elif prop in ("dateCreated", "created_at"):
            handle_str_op("c.created_at", op, val_l, p_l)

    if not cond_sqls:
        return "1=1", {}
    return f"({join_op.join(cond_sqls)})", params


def fetch_playlists_tree(
    engine: Engine, include_deleted: bool = False
) -> List[Dict[str, Any]]:
    """Retrieve full playlist hierarchy (folders, standard, and smart playlists).

    Returns a hierarchical list of root items, where folder items have a 'children' list.
    Also calculates track counts for standard playlists via djmdSongPlaylist.
    """
    with engine.connect() as conn:
        existing_tables = {
            r[0]
            for r in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table';"
            ).fetchall()
        }
        if "djmdPlaylist" not in existing_tables:
            return []

        pl_cols = {
            r[1]
            for r in conn.exec_driver_sql("PRAGMA table_info(djmdPlaylist);").fetchall()
        }

        c_cols = set()
        if "djmdContent" in existing_tables:
            c_cols = {
                r[1]
                for r in conn.exec_driver_sql("PRAGMA table_info(djmdContent);").fetchall()
            }
        c_del = "AND (c.rb_local_deleted = 0 OR c.rb_local_deleted IS NULL)" if "rb_local_deleted" in c_cols else ""
        streaming_filter = build_exclude_streaming_clause(c_cols, table_alias="c")

        # Check for djmdSongPlaylist to count tracks
        track_counts: Dict[str, int] = {}
        if "djmdSongPlaylist" in existing_tables:
            sp_cols = {
                r[1]
                for r in conn.exec_driver_sql(
                    "PRAGMA table_info(djmdSongPlaylist);"
                ).fetchall()
            }
            sp_del = (
                "AND (sp.rb_local_deleted = 0 OR sp.rb_local_deleted IS NULL)"
                if "rb_local_deleted" in sp_cols
                else ""
            )
            if "djmdContent" in existing_tables:
                count_sql = f"""
                    SELECT sp.PlaylistID, count(sp.ContentID) 
                    FROM djmdSongPlaylist sp
                    JOIN djmdContent c ON sp.ContentID = c.ID
                    WHERE 1=1
                      {sp_del}
                      {c_del}
                      {streaming_filter}
                    GROUP BY sp.PlaylistID;
                """
            else:
                sp_del_where = (
                    "WHERE (rb_local_deleted = 0 OR rb_local_deleted IS NULL)"
                    if "rb_local_deleted" in sp_cols
                    else ""
                )
                count_sql = f"""
                    SELECT PlaylistID, count(*) 
                    FROM djmdSongPlaylist 
                    {sp_del_where}
                    GROUP BY PlaylistID;
                """
            for row in conn.execute(text(count_sql)).fetchall():
                if row[0] is not None:
                    track_counts[str(row[0])] = int(row[1])

        # Build query for djmdPlaylist
        del_filter = (
            ""
            if include_deleted
            else "AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL)"
            if "rb_local_deleted" in pl_cols
            else ""
        )
        parent_col = "ParentID" if "ParentID" in pl_cols else "NULL AS ParentID"
        attr_col = "Attribute" if "Attribute" in pl_cols else "0 AS Attribute"
        seq_col = "Seq" if "Seq" in pl_cols else "0 AS Seq"
        smart_col = "SmartList" if "SmartList" in pl_cols else "NULL AS SmartList"
        rb_del_col = (
            "rb_local_deleted" if "rb_local_deleted" in pl_cols else "0 AS rb_local_deleted"
        )

        query = f"""
            SELECT 
                ID,
                Name,
                {seq_col},
                {attr_col},
                {parent_col},
                {smart_col},
                {rb_del_col}
            FROM djmdPlaylist
            WHERE 1=1 {del_filter}
              AND Name IS NOT NULL AND TRIM(Name) != ''
            ORDER BY Seq ASC, lower(Name) ASC;
        """
        rows = conn.execute(text(query)).fetchall()

        all_nodes: Dict[str, Dict[str, Any]] = {}
        items_list: List[Dict[str, Any]] = []

        artist_name_expr = (
            "COALESCE(a.Name, c.SrcArtistName, 'Unknown Artist')"
            if "djmdArtist" in existing_tables
            else "COALESCE(c.SrcArtistName, 'Unknown Artist')"
        )
        album_expr = "al.Name" if "djmdAlbum" in existing_tables else "NULL"
        genre_expr = "g.Name" if "djmdGenre" in existing_tables else "NULL"
        key_expr = "k.ScaleName" if "djmdKey" in existing_tables else "NULL"
        join_a = "LEFT JOIN djmdArtist a ON c.ArtistID = a.ID" if "djmdArtist" in existing_tables else ""
        join_al = "LEFT JOIN djmdAlbum al ON c.AlbumID = al.ID" if "djmdAlbum" in existing_tables else ""
        join_g = "LEFT JOIN djmdGenre g ON c.GenreID = g.ID" if "djmdGenre" in existing_tables else ""
        join_k = "LEFT JOIN djmdKey k ON c.KeyID = k.ID" if "djmdKey" in existing_tables else ""

        for r in rows:
            pl_id = str(r[0])
            name = str(r[1]).strip()
            seq = int(r[2]) if r[2] is not None else 0
            attr = int(r[3]) if r[3] is not None else 0
            parent_id = str(r[4]) if r[4] and str(r[4]).strip() not in ("", "root") else None
            smart_list = str(r[5]) if r[5] else None
            is_del = bool(r[6]) if r[6] is not None else False

            # Dynamically compute track count for smart playlists
            if attr == 4 and smart_list and "djmdContent" in existing_tables:
                clause, p_dict = parse_smartlist_to_sql(
                    smart_list,
                    artist_expr=artist_name_expr,
                    album_expr=album_expr,
                    genre_expr=genre_expr,
                    key_expr=key_expr,
                    param_prefix=f"sl_t_{len(all_nodes)}_",
                )
                try:
                    cnt_sql = f"""
                        SELECT count(c.ID)
                        FROM djmdContent c
                        {join_a}
                        {join_al}
                        {join_g}
                        {join_k}
                        WHERE {clause}
                          {c_del}
                          {streaming_filter};
                    """
                    cnt = conn.execute(text(cnt_sql), p_dict).scalar()
                    track_counts[pl_id] = int(cnt or 0)
                except Exception:
                    track_counts[pl_id] = 0

            node = {
                "id": pl_id,
                "name": name,
                "seq": seq,
                "attribute": attr,  # 0: standard, 1: folder, 4: smart
                "parent_id": parent_id,
                "smart_list": smart_list,
                "is_deleted": is_del,
                "track_count": track_counts.get(pl_id, 0),
                "children": [],
            }
            all_nodes[pl_id] = node
            items_list.append(node)

        # Assemble hierarchical tree
        root_nodes: List[Dict[str, Any]] = []
        for node in items_list:
            pid = node["parent_id"]
            if pid and pid in all_nodes:
                all_nodes[pid]["children"].append(node)
            else:
                root_nodes.append(node)

        return root_nodes


def fetch_playlist_tracks(
    engine: Engine,
    playlist_id: str,
    query: str = "",
    limit: Optional[int] = None,
    sort_by: Optional[str] = None,
    sort_order: str = "asc",
    exclude_streaming: bool = True,
) -> List[Dict[str, Any]]:
    """Query tracks belonging to a playlist from djmdSongPlaylist joined with djmdContent.

    Includes full track metadata, file format badges, folder/file paths,
    tags, and playlist memberships. Orders by TrackNo ASC by default.
    """
    with engine.connect() as conn:
        existing_tables = {
            r[0]
            for r in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table';"
            ).fetchall()
        }
        if "djmdContent" not in existing_tables:
            return []

        sp_cols = {
            r[1]
            for r in conn.exec_driver_sql("PRAGMA table_info(djmdSongPlaylist);").fetchall()
        } if "djmdSongPlaylist" in existing_tables else set()
        c_cols = {
            r[1]
            for r in conn.exec_driver_sql("PRAGMA table_info(djmdContent);").fetchall()
        }

        sp_del = (
            "AND (sp.rb_local_deleted = 0 OR sp.rb_local_deleted IS NULL)"
            if "rb_local_deleted" in sp_cols
            else ""
        )
        c_del = (
            "AND (c.rb_local_deleted = 0 OR c.rb_local_deleted IS NULL)"
            if "rb_local_deleted" in c_cols
            else ""
        )
        streaming_filter = (
            build_exclude_streaming_clause(c_cols, table_alias="c")
            if exclude_streaming
            else ""
        )

        join_artist = (
            "LEFT JOIN djmdArtist a ON c.ArtistID = a.ID"
            if "djmdArtist" in existing_tables
            else ""
        )
        artist_name_expr = (
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

        join_genre = (
            "LEFT JOIN djmdGenre g ON c.GenreID = g.ID"
            if "djmdGenre" in existing_tables
            else ""
        )
        genre_expr = "g.Name" if "djmdGenre" in existing_tables else "NULL"

        join_key = (
            "LEFT JOIN djmdKey k ON c.KeyID = k.ID"
            if "djmdKey" in existing_tables
            else ""
        )
        key_expr = "k.ScaleName" if "djmdKey" in existing_tables else "NULL"

        # Check if this playlist is a smart playlist
        is_smart = False
        smart_where_clause = ""
        smart_params: Dict[str, Any] = {}
        if "djmdPlaylist" in existing_tables:
            pl_info = conn.execute(
                text("SELECT Attribute, SmartList FROM djmdPlaylist WHERE ID = :pid"),
                {"pid": str(playlist_id)},
            ).fetchone()
            if pl_info and int(pl_info[0] or 0) == 4 and pl_info[1]:
                is_smart = True
                smart_where_clause, smart_params = parse_smartlist_to_sql(
                    str(pl_info[1]),
                    artist_expr=artist_name_expr,
                    album_expr=album_expr,
                    genre_expr=genre_expr,
                    key_expr=key_expr,
                    param_prefix="smart_",
                )

        if not is_smart and "djmdSongPlaylist" not in existing_tables:
            return []

        has_my_tag = (
            "djmdSongMyTag" in existing_tables and "djmdMyTag" in existing_tables
        )
        has_playlist = (
            "djmdSongPlaylist" in existing_tables and "djmdPlaylist" in existing_tables
        )

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
            osp_cols = {
                r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdSongPlaylist);").fetchall()
            }
            p_del = (
                "AND (p.rb_local_deleted = 0 OR p.rb_local_deleted IS NULL)"
                if "rb_local_deleted" in p_cols
                else ""
            )
            osp_del = (
                "AND (osp.rb_local_deleted = 0 OR osp.rb_local_deleted IS NULL)"
                if "rb_local_deleted" in osp_cols
                else ""
            )
            p_attr = "AND p.Attribute = 0" if "Attribute" in p_cols else ""
            playlist_expr = f"""(
                SELECT GROUP_CONCAT(Name, '|||')
                FROM (
                    SELECT DISTINCT p.Name
                    FROM djmdSongPlaylist osp
                    JOIN djmdPlaylist p ON osp.PlaylistID = p.ID
                    WHERE osp.ContentID = c.ID
                      {p_attr}
                      {p_del}
                      {osp_del}
                    ORDER BY lower(p.Name) ASC
                )
            ) AS PlaylistNames"""
        else:
            playlist_expr = "NULL AS PlaylistNames"

        file_type_expr = "c.FileType" if "FileType" in c_cols else "NULL AS FileType"
        track_no_col = "sp.TrackNo" if "TrackNo" in sp_cols else "1 AS TrackNo"

        # Search query clause
        search_clause = ""
        params: Dict[str, Any] = {"pid": str(playlist_id)}
        if query and query.strip():
            album_search = f"OR lower({album_expr}) LIKE :q" if album_expr != "NULL" else ""
            key_search = f"OR lower({key_expr}) LIKE :q" if key_expr != "NULL" else ""
            search_clause = f"""
                AND (
                    lower(c.Title) LIKE :q
                    OR lower({artist_name_expr}) LIKE :q
                    {album_search}
                    {key_search}
                    OR lower(COALESCE(c.FileNameL, '')) LIKE :q
                    OR lower(COALESCE(c.FolderPath, '')) LIKE :q
                )
            """
            params["q"] = f"%{query.strip().lower()}%"

        # Sorting
        order_dir = "DESC" if sort_order.lower() == "desc" else "ASC"
        default_order = (
            f"lower(c.Title) {order_dir}"
            if is_smart
            else f"{track_no_col} {order_dir}"
        )
        sort_map = {
            "track_no": default_order,
            "title": f"lower(c.Title) {order_dir}",
            "artist": f"lower({artist_name_expr}) {order_dir}",
            "album": f"lower(COALESCE(al.Name, '')) {order_dir}",
            "key": f"COALESCE(k.ScaleName, '') {order_dir}",
            "bpm": f"c.BPM {order_dir}",
            "time": f"c.Length {order_dir}",
            "plays": f"c.DJPlayCount {order_dir}",
            "bitrate": f"c.BitRate {order_dir}",
            "file_type": f"c.FileType {order_dir}",
            "file_name": f"lower(c.FileNameL) {order_dir}",
            "folder": f"lower(c.FolderPath) {order_dir}",
        }
        order_clause = sort_map.get(sort_by, default_order)

        limit_clause = ""
        if limit and limit > 0:
            limit_clause = f"LIMIT {int(limit)}"

        if is_smart:
            sql = f"""
                SELECT 
                    1 AS TrackNo,
                    c.ID AS ContentID,
                    c.Title,
                    {artist_name_expr} AS ArtistName,
                    {album_expr} AS AlbumName,
                    {genre_expr} AS GenreName,
                    {key_expr} AS KeyName,
                    c.BPM,
                    c.Length,
                    c.DJPlayCount,
                    c.Rating,
                    c.ReleaseYear,
                    c.BitRate,
                    {file_type_expr},
                    c.Commnt,
                    c.FolderPath,
                    c.FileNameL,
                    {tag_expr},
                    {playlist_expr}
                FROM djmdContent c
                {join_artist}
                {join_album}
                {join_genre}
                {join_key}
                WHERE {smart_where_clause}
                  {c_del}
                  {streaming_filter}
                  {search_clause}
                ORDER BY {order_clause}
                {limit_clause};
            """
            params.update(smart_params)
        else:
            sql = f"""
                SELECT 
                    {track_no_col} AS TrackNo,
                    c.ID AS ContentID,
                    c.Title,
                    {artist_name_expr} AS ArtistName,
                    {album_expr} AS AlbumName,
                    {genre_expr} AS GenreName,
                    {key_expr} AS KeyName,
                    c.BPM,
                    c.Length,
                    c.DJPlayCount,
                    c.Rating,
                    c.ReleaseYear,
                    c.BitRate,
                    {file_type_expr},
                    c.Commnt,
                    c.FolderPath,
                    c.FileNameL,
                    {tag_expr},
                    {playlist_expr}
                FROM djmdSongPlaylist sp
                JOIN djmdContent c ON sp.ContentID = c.ID
                {join_artist}
                {join_album}
                {join_genre}
                {join_key}
                WHERE sp.PlaylistID = :pid
                  {sp_del}
                  {c_del}
                  {streaming_filter}
                  {search_clause}
                ORDER BY {order_clause}
                {limit_clause};
            """
        rows = conn.execute(text(sql), params).mappings().fetchall()

        tracks: List[Dict[str, Any]] = []
        for idx, r in enumerate(rows, 1):
            track_num = idx if is_smart else int(r.get("TrackNo") or idx)
            tracks.append(build_track_record(r, extra={"track_no": track_num}))

        return tracks


def fetch_playlist_folders(engine: Engine) -> List[Dict[str, Any]]:
    """Fetch all active playlist folder categories (Attribute = 1) from Rekordbox database.

    Returns a hierarchical/flattened list of folder dictionaries, starting with the root container:
    [
        {"id": "root", "name": "Root (Top-level)", "attribute": 1, "parent_id": None, "depth": 0},
        {"id": "123", "name": "Sets", "attribute": 1, "parent_id": "root", "depth": 1},
        ...
    ]
    """
    root_node = {
        "id": "root",
        "name": "Root (Top-level)",
        "attribute": 1,
        "parent_id": None,
        "depth": 0,
    }

    with engine.connect() as conn:
        existing_tables = {
            r[0]
            for r in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table';"
            ).fetchall()
        }
        if "djmdPlaylist" not in existing_tables:
            return [root_node]

        pl_cols = {
            r[1]
            for r in conn.exec_driver_sql("PRAGMA table_info(djmdPlaylist);").fetchall()
        }

        del_filter = (
            "AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL)"
            if "rb_local_deleted" in pl_cols
            else ""
        )
        seq_col = "Seq" if "Seq" in pl_cols else "0 AS Seq"

        query = f"""
            SELECT ID, Name, ParentID, {seq_col}
            FROM djmdPlaylist
            WHERE Attribute = 1
              {del_filter}
            ORDER BY Seq ASC, lower(Name) ASC;
        """
        rows = conn.execute(text(query)).fetchall()
        folders_by_id = {}
        folders_by_parent: Dict[str, List[Dict[str, Any]]] = {}

        for r in rows:
            f_id = str(r[0])
            f_name = str(r[1]).strip() if r[1] else "Untitled Folder"
            p_id = str(r[2]).strip() if r[2] else "root"
            f_seq = int(r[3]) if r[3] is not None else 0
            node = {
                "id": f_id,
                "name": f_name,
                "attribute": 1,
                "parent_id": p_id,
                "seq": f_seq,
                "depth": 1,
            }
            folders_by_id[f_id] = node
            folders_by_parent.setdefault(p_id, []).append(node)

        result: List[Dict[str, Any]] = [root_node]

        def traverse(p_id: str, depth: int) -> None:
            children = folders_by_parent.get(p_id, [])
            for child in children:
                child_copy = dict(child)
                child_copy["depth"] = depth
                result.append(child_copy)
                traverse(child["id"], depth + 1)

        traverse("root", 1)
        return result


def create_playlist(
    engine: Engine,
    name: str,
    parent_id_or_name: str = "root",
    is_folder: bool = False,
) -> Dict[str, Any]:
    """Create a new playlist or playlist folder in Rekordbox database.

    If a playlist/folder with the exact same name and parent was previously soft-deleted,
    reactivates it and updates its timestamps.

    Args:
        engine: Active SQLAlchemy database engine.
        name: Desired playlist or folder name (non-empty string).
        parent_id_or_name: Parent folder ID or Name (e.g. 'root' or a folder ID).
        is_folder: True to create a playlist folder (Attribute = 1), False for standard playlist (Attribute = 0).

    Returns:
        Dict representing the created or reactivated playlist.

    Raises:
        ValueError: If name is empty, parent folder not found, or active duplicate exists.
    """
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("Playlist name cannot be empty.")

    now_utc = get_rekordbox_timestamp()

    with engine.begin() as conn:
        existing_tables = {
            r[0]
            for r in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table';"
            ).fetchall()
        }
        if "djmdPlaylist" not in existing_tables:
            raise ValueError("Table djmdPlaylist not found in database.")

        pl_cols = {
            r[1]
            for r in conn.exec_driver_sql("PRAGMA table_info(djmdPlaylist);").fetchall()
        }

        has_rb_del = "rb_local_deleted" in pl_cols
        del_filter = (
            "AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL)"
            if has_rb_del
            else ""
        )

        # 1. Resolve parent folder ID
        p_str = str(parent_id_or_name).strip()
        if p_str.lower() in ("root", "root (top-level)", ""):
            parent_id = "root"
            parent_name = "Root (Top-level)"
        else:
            parent_sql = f"""
                SELECT ID, Name
                FROM djmdPlaylist
                WHERE Attribute = 1
                  AND (ID = :p_query OR lower(Name) = lower(:p_query))
                  {del_filter}
                LIMIT 1;
            """
            parent_row = conn.execute(text(parent_sql), {"p_query": p_str}).fetchone()
            if not parent_row:
                raise ValueError(f"Parent folder '{parent_id_or_name}' not found.")
            parent_id = str(parent_row[0])
            parent_name = str(parent_row[1]).strip()

        # 2. Check if a playlist/folder with this name already exists under the same parent
        check_sql = """
            SELECT ID, Name, Attribute, ParentID, Seq, rb_local_deleted
            FROM djmdPlaylist
            WHERE lower(Name) = lower(:name)
              AND ParentID = :pid
            LIMIT 1;
        """
        existing = conn.execute(text(check_sql), {"name": clean_name, "pid": parent_id}).fetchone()

        # Calculate next sequence number in parent folder
        seq_sql = f"""
            SELECT COALESCE(MAX(Seq), 0) + 1
            FROM djmdPlaylist
            WHERE ParentID = :pid
              {del_filter};
        """
        next_seq = int(conn.execute(text(seq_sql), {"pid": parent_id}).scalar() or 1)

        attr_val = 1 if is_folder else 0
        kind_str = "folder" if is_folder else "playlist"

        if existing:
            pl_id = str(existing[0])
            existing_name = str(existing[1])
            is_deleted = bool(existing[5]) if (has_rb_del and existing[5] is not None) else False

            if is_deleted:
                # Reactivate soft-deleted playlist/folder
                update_fields = ["Attribute = :attr", "Seq = :seq"]
                params: Dict[str, Any] = {
                    "attr": attr_val,
                    "seq": next_seq,
                    "id": pl_id,
                }
                if has_rb_del:
                    update_fields.append("rb_local_deleted = 0")
                if "updated_at" in pl_cols:
                    update_fields.append("updated_at = :now")
                    params["now"] = now_utc
                if "rb_local_synced" in pl_cols:
                    update_fields.append("rb_local_synced = 1")
                if "rb_local_data_status" in pl_cols:
                    update_fields.append("rb_local_data_status = 1")

                update_sql = f"UPDATE djmdPlaylist SET {', '.join(update_fields)} WHERE ID = :id;"
                conn.execute(text(update_sql), params)

                return {
                    "id": pl_id,
                    "name": existing_name,
                    "parent_id": parent_id,
                    "parent_name": parent_name,
                    "attribute": attr_val,
                    "is_folder": is_folder,
                    "seq": next_seq,
                    "reactivated": True,
                }
            else:
                raise ValueError(f"A {kind_str} named '{existing_name}' already exists in '{parent_name}'.")

        # 3. Generate unique numeric 32-bit positive integer string ID
        while True:
            candidate_id = str(random.randint(100_000_000, 2_147_483_647))
            exists_id = conn.execute(
                text("SELECT 1 FROM djmdPlaylist WHERE ID = :cid LIMIT 1;"),
                {"cid": candidate_id},
            ).fetchone()
            if not exists_id:
                new_id = candidate_id
                break

        new_uuid = str(uuid.uuid4())
        record_data = {
            "ID": new_id,
            "Seq": next_seq,
            "Name": clean_name,
            "Attribute": attr_val,
            "ParentID": parent_id,
            "SmartList": None,
            "UUID": new_uuid,
            "rb_data_status": 0,
            "rb_local_data_status": 1,
            "rb_local_deleted": 0,
            "rb_local_synced": 1,
            "created_at": now_utc,
            "updated_at": now_utc,
        }

        insert_cols = [k for k in record_data if k in pl_cols]
        col_names = ", ".join(insert_cols)
        val_placeholders = ", ".join(f":{k}" for k in insert_cols)
        insert_sql = f"INSERT INTO djmdPlaylist ({col_names}) VALUES ({val_placeholders});"
        conn.execute(text(insert_sql), {k: record_data[k] for k in insert_cols})

        return {
            "id": new_id,
            "name": clean_name,
            "parent_id": parent_id,
            "parent_name": parent_name,
            "attribute": attr_val,
            "is_folder": is_folder,
            "seq": next_seq,
            "uuid": new_uuid,
            "reactivated": False,
        }


def delete_playlist(engine: Engine, playlist_id: str) -> bool:
    """Soft-delete an empty playlist or folder in the Rekordbox database.

    Only empty playlists (0 tracks) or empty folders (0 children) can be deleted.

    Args:
        engine: SQLAlchemy Engine connected to Rekordbox SQLite database.
        playlist_id: ID of the playlist or folder to delete.

    Returns:
        bool: True if deletion was successful.

    Raises:
        ValueError: If playlist is not found or is not empty.
    """
    now_utc = get_rekordbox_timestamp()
    with engine.begin() as conn:
        existing_tables = {
            r[0]
            for r in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table';"
            ).fetchall()
        }
        if "djmdPlaylist" not in existing_tables:
            raise ValueError("Table djmdPlaylist not found in database.")

        pl_cols = {
            r[1]
            for r in conn.exec_driver_sql("PRAGMA table_info(djmdPlaylist);").fetchall()
        }

        # 1. Fetch playlist record
        check_cols = ["ID", "Name", "Attribute"]
        if "ParentID" in pl_cols:
            check_cols.append("ParentID")
        if "SmartList" in pl_cols:
            check_cols.append("SmartList")
        if "rb_local_deleted" in pl_cols:
            check_cols.append("rb_local_deleted")

        pl_row = conn.execute(
            text(f"SELECT {', '.join(check_cols)} FROM djmdPlaylist WHERE ID = :pid;"),
            {"pid": str(playlist_id)},
        ).mappings().fetchone()

        if not pl_row:
            raise ValueError(f"Playlist with ID '{playlist_id}' not found.")

        # If already deleted, return True
        if "rb_local_deleted" in pl_cols and pl_row.get("rb_local_deleted") == 1:
            return True

        name = pl_row.get("Name") or "Untitled"
        attr = int(pl_row.get("Attribute") or 0)
        smart_list = pl_row.get("SmartList") if "SmartList" in pl_cols else None
        is_folder = (attr == 1)

        # 2. Check if empty
        if is_folder:
            child_del = (
                "AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL)"
                if "rb_local_deleted" in pl_cols
                else ""
            )
            child_cnt = conn.execute(
                text(f"SELECT COUNT(*) FROM djmdPlaylist WHERE ParentID = :pid {child_del};"),
                {"pid": str(playlist_id)},
            ).scalar() or 0
            if child_cnt > 0:
                raise ValueError(
                    f"Folder '{name}' is not empty ({child_cnt} item(s)). Only empty folders can be deleted."
                )
        else:
            # Check djmdSongPlaylist
            if "djmdSongPlaylist" in existing_tables:
                sp_cols = {
                    r[1]
                    for r in conn.exec_driver_sql("PRAGMA table_info(djmdSongPlaylist);").fetchall()
                }
                sp_del = (
                    "AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL)"
                    if "rb_local_deleted" in sp_cols
                    else ""
                )
                track_cnt = conn.execute(
                    text(f"SELECT COUNT(*) FROM djmdSongPlaylist WHERE PlaylistID = :pid {sp_del};"),
                    {"pid": str(playlist_id)},
                ).scalar() or 0
                if track_cnt > 0:
                    raise ValueError(
                        f"Playlist '{name}' is not empty ({track_cnt} track(s)). Only empty playlists can be deleted."
                    )

            # If Smart Playlist (Attribute == 4), also evaluate smart list criteria if available
            if attr == 4 and smart_list and "djmdContent" in existing_tables:
                c_cols = {
                    r[1]
                    for r in conn.exec_driver_sql("PRAGMA table_info(djmdContent);").fetchall()
                }
                c_del = "AND (c.rb_local_deleted = 0 OR c.rb_local_deleted IS NULL)" if "rb_local_deleted" in c_cols else ""
                streaming_filter = build_exclude_streaming_clause(c_cols, table_alias="c")

                artist_name_expr = (
                    "COALESCE(a.Name, c.SrcArtistName, 'Unknown Artist')"
                    if "djmdArtist" in existing_tables
                    else "COALESCE(c.SrcArtistName, 'Unknown Artist')"
                )
                album_expr = "al.Name" if "djmdAlbum" in existing_tables else "NULL"
                genre_expr = "g.Name" if "djmdGenre" in existing_tables else "NULL"
                key_expr = "k.ScaleName" if "djmdKey" in existing_tables else "NULL"
                join_a = "LEFT JOIN djmdArtist a ON c.ArtistID = a.ID" if "djmdArtist" in existing_tables else ""
                join_al = "LEFT JOIN djmdAlbum al ON c.AlbumID = al.ID" if "djmdAlbum" in existing_tables else ""
                join_g = "LEFT JOIN djmdGenre g ON c.GenreID = g.ID" if "djmdGenre" in existing_tables else ""
                join_k = "LEFT JOIN djmdKey k ON c.KeyID = k.ID" if "djmdKey" in existing_tables else ""

                try:
                    clause, p_dict = parse_smartlist_to_sql(
                        smart_list,
                        artist_expr=artist_name_expr,
                        album_expr=album_expr,
                        genre_expr=genre_expr,
                        key_expr=key_expr,
                        param_prefix="sl_del_",
                    )
                    cnt_sql = f"""
                        SELECT count(c.ID)
                        FROM djmdContent c
                        {join_a}
                        {join_al}
                        {join_g}
                        {join_k}
                        WHERE {clause}
                          {c_del}
                          {streaming_filter};
                    """
                    smart_cnt = int(conn.execute(text(cnt_sql), p_dict).scalar() or 0)
                    if smart_cnt > 0:
                        raise ValueError(
                            f"Smart playlist '{name}' is not empty ({smart_cnt} track(s)). Only empty playlists can be deleted."
                        )
                except ValueError:
                    raise
                except Exception:
                    pass

        # 3. Soft-delete or hard-delete
        if "rb_local_deleted" in pl_cols:
            update_fields = ["rb_local_deleted = 1"]
            params: Dict[str, Any] = {"pid": str(playlist_id)}
            if "updated_at" in pl_cols:
                update_fields.append("updated_at = :now")
                params["now"] = now_utc
            if "rb_local_data_status" in pl_cols:
                update_fields.append("rb_local_data_status = 1")
            if "rb_data_status" in pl_cols:
                update_fields.append("rb_data_status = 2")
            if "rb_local_synced" in pl_cols:
                update_fields.append("rb_local_synced = 1")

            conn.execute(
                text(f"UPDATE djmdPlaylist SET {', '.join(update_fields)} WHERE ID = :pid;"),
                params,
            )
            if "djmdSongPlaylist" in existing_tables:
                sp_cols = {
                    r[1]
                    for r in conn.exec_driver_sql("PRAGMA table_info(djmdSongPlaylist);").fetchall()
                }
                if "rb_local_deleted" in sp_cols:
                    sp_updates = ["rb_local_deleted = 1"]
                    if "updated_at" in sp_cols:
                        sp_updates.append("updated_at = :now")
                    if "rb_local_data_status" in sp_cols:
                        sp_updates.append("rb_local_data_status = 1")
                    if "rb_data_status" in sp_cols:
                        sp_updates.append("rb_data_status = 2")
                    conn.execute(
                        text(f"UPDATE djmdSongPlaylist SET {', '.join(sp_updates)} WHERE PlaylistID = :pid;"),
                        {"pid": str(playlist_id), "now": now_utc},
                    )
        else:
            conn.execute(text("DELETE FROM djmdPlaylist WHERE ID = :pid;"), {"pid": str(playlist_id)})
            if "djmdSongPlaylist" in existing_tables:
                conn.execute(text("DELETE FROM djmdSongPlaylist WHERE PlaylistID = :pid;"), {"pid": str(playlist_id)})

        return True
