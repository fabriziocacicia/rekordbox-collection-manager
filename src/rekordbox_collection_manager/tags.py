"""Tags database queries and management for Rekordbox MyTags.

Provides methods to query tag categories (filtering by Genre and Style),
hierarchies, track counts, and retrieve track lists for individual tags with full metadata.
"""

from typing import Any, Dict, List, Optional, Set, Tuple
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rekordbox_collection_manager.utils import (
    _resolve_tag_matching_ids,
    build_exclude_streaming_clause,
    build_track_record,
    get_rekordbox_timestamp,
    resolve_tag_matching_ids,
)


def fetch_tags_tree(
    engine: Engine,
    include_deleted: bool = False,
    category_names: Tuple[str, ...] = ("Genre", "Style"),
) -> List[Dict[str, Any]]:
    """Retrieve filtered MyTag hierarchy (Genre and Style category folders).

    Returns a hierarchical list of category folder items ('Genre', 'Style'),
    each containing their respective child tags with real-time track counts from djmdSongMyTag.
    """
    with engine.connect() as conn:
        existing_tables = {
            r[0]
            for r in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table';"
            ).fetchall()
        }
        if "djmdMyTag" not in existing_tables:
            return []

        tag_cols = {
            r[1]
            for r in conn.exec_driver_sql("PRAGMA table_info(djmdMyTag);").fetchall()
        }

        # 1. Compute track count for each tag from djmdSongMyTag
        track_counts: Dict[str, int] = {}
        if "djmdSongMyTag" in existing_tables:
            smt_cols = {
                r[1]
                for r in conn.exec_driver_sql(
                    "PRAGMA table_info(djmdSongMyTag);"
                ).fetchall()
            }
            c_cols = {
                r[1]
                for r in conn.exec_driver_sql(
                    "PRAGMA table_info(djmdContent);"
                ).fetchall()
            } if "djmdContent" in existing_tables else set()

            smt_del = (
                "AND (smt.rb_local_deleted = 0 OR smt.rb_local_deleted IS NULL)"
                if "rb_local_deleted" in smt_cols
                else ""
            )
            c_del = (
                "AND (c.rb_local_deleted = 0 OR c.rb_local_deleted IS NULL)"
                if "rb_local_deleted" in c_cols
                else ""
            )
            streaming_filter = build_exclude_streaming_clause(c_cols, table_alias="c")

            if "djmdContent" in existing_tables:
                tag_rows_sql = f"""
                    SELECT smt.MyTagID, smt.ContentID 
                    FROM djmdSongMyTag smt
                    JOIN djmdContent c ON smt.ContentID = c.ID
                    WHERE 1=1
                      {smt_del}
                      {c_del}
                      {streaming_filter}
                    GROUP BY smt.MyTagID, smt.ContentID;
                """
            else:
                del_clause = (
                    "WHERE (rb_local_deleted = 0 OR rb_local_deleted IS NULL)"
                    if "rb_local_deleted" in smt_cols
                    else ""
                )
                tag_rows_sql = f"""
                    SELECT MyTagID, ContentID 
                    FROM djmdSongMyTag 
                    {del_clause}
                    GROUP BY MyTagID, ContentID;
                """
            tag_tracks_map: Dict[str, Set[str]] = {}
            for row in conn.execute(text(tag_rows_sql)).fetchall():
                if row[0] is not None and row[1] is not None:
                    raw_id = str(row[0])
                    cid = str(row[1])
                    for tid in _resolve_tag_matching_ids(raw_id):
                        if tid not in tag_tracks_map:
                            tag_tracks_map[tid] = set()
                        tag_tracks_map[tid].add(cid)
            track_counts = {tid: len(cids) for tid, cids in tag_tracks_map.items()}

        # 2. Query category roots matching category_names
        del_filter = (
            ""
            if include_deleted
            else "AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL)"
            if "rb_local_deleted" in tag_cols
            else ""
        )
        seq_col = "Seq" if "Seq" in tag_cols else "0 AS Seq"
        attr_col = "Attribute" if "Attribute" in tag_cols else "1 AS Attribute"
        rb_del_col = (
            "rb_local_deleted" if "rb_local_deleted" in tag_cols else "0 AS rb_local_deleted"
        )

        cat_placeholders = ", ".join(f":c_{i}" for i in range(len(category_names)))
        cat_params = {f"c_{i}": name for i, name in enumerate(category_names)}

        cat_query = f"""
            SELECT ID, Name, {seq_col}, {attr_col}, {rb_del_col}
            FROM djmdMyTag
            WHERE (ParentID = 'root' OR Attribute = 1)
              AND Name IN ({cat_placeholders})
              {del_filter}
            ORDER BY Seq ASC, lower(Name) ASC;
        """
        cat_rows = conn.execute(text(cat_query), cat_params).fetchall()

        tree: List[Dict[str, Any]] = []

        for cr in cat_rows:
            cat_id = str(cr[0])
            cat_name = str(cr[1]).strip()
            cat_seq = int(cr[2]) if cr[2] is not None else 0
            cat_attr = int(cr[3]) if cr[3] is not None else 1
            cat_del = bool(cr[4]) if cr[4] is not None else False

            # Query child tags belonging to this category
            child_query = f"""
                SELECT ID, Name, {seq_col}, {attr_col}, {rb_del_col}
                FROM djmdMyTag
                WHERE ParentID = :cat_id
                  {del_filter}
                ORDER BY Seq ASC, lower(Name) ASC;
            """
            child_rows = conn.execute(text(child_query), {"cat_id": cat_id}).fetchall()

            children: List[Dict[str, Any]] = []
            total_cat_tracks = 0

            for chr in child_rows:
                tag_id = str(chr[0])
                tag_name = str(chr[1]).strip()
                tag_seq = int(chr[2]) if chr[2] is not None else 0
                tag_del = bool(chr[4]) if chr[4] is not None else False

                # Sum track count across signed/unsigned representations
                t_count = 0
                for match_id in _resolve_tag_matching_ids(tag_id):
                    if match_id in track_counts:
                        t_count = track_counts[match_id]
                        break

                total_cat_tracks += t_count

                children.append({
                    "id": tag_id,
                    "name": tag_name,
                    "seq": tag_seq,
                    "attribute": 0,  # tag item
                    "parent_id": cat_id,
                    "category_name": cat_name,
                    "is_deleted": tag_del,
                    "track_count": t_count,
                    "children": [],
                })

            category_node = {
                "id": cat_id,
                "name": cat_name,
                "seq": cat_seq,
                "attribute": 1,  # folder item
                "parent_id": "root",
                "is_deleted": cat_del,
                "track_count": total_cat_tracks,
                "children": children,
            }
            tree.append(category_node)

        return tree


def fetch_tag_tracks(
    engine: Engine,
    tag_id: str,
    query: str = "",
    limit: Optional[int] = None,
    sort_by: Optional[str] = None,
    sort_order: str = "asc",
    exclude_streaming: bool = True,
) -> List[Dict[str, Any]]:
    """Query tracks assigned to a specific MyTag.

    Includes full track metadata, file format badges, folder/file paths,
    tags, and playlist memberships. Orders by track title ASC by default.
    """
    with engine.connect() as conn:
        existing_tables = {
            r[0]
            for r in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table';"
            ).fetchall()
        }
        if "djmdSongMyTag" not in existing_tables or "djmdContent" not in existing_tables:
            return []

        smt_cols = {
            r[1]
            for r in conn.exec_driver_sql("PRAGMA table_info(djmdSongMyTag);").fetchall()
        }
        c_cols = {
            r[1]
            for r in conn.exec_driver_sql("PRAGMA table_info(djmdContent);").fetchall()
        }

        smt_del = (
            "AND (smt.rb_local_deleted = 0 OR smt.rb_local_deleted IS NULL)"
            if "rb_local_deleted" in smt_cols
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

        has_my_tag = (
            "djmdSongMyTag" in existing_tables and "djmdMyTag" in existing_tables
        )
        has_playlist = (
            "djmdSongPlaylist" in existing_tables and "djmdPlaylist" in existing_tables
        )

        if has_my_tag:
            tag_cols = {
                r[1]
                for r in conn.exec_driver_sql("PRAGMA table_info(djmdMyTag);").fetchall()
            }
            tag_del = (
                "AND (t.rb_local_deleted = 0 OR t.rb_local_deleted IS NULL)"
                if "rb_local_deleted" in tag_cols
                else ""
            )
            osmt_del = (
                "AND (osmt.rb_local_deleted = 0 OR osmt.rb_local_deleted IS NULL)"
                if "rb_local_deleted" in smt_cols
                else ""
            )
            tag_seq = "t.Seq ASC, " if "Seq" in tag_cols else ""
            tag_expr = f"""(
                SELECT GROUP_CONCAT(Name, '|||')
                FROM (
                    SELECT DISTINCT t.Name
                    FROM djmdSongMyTag osmt
                    JOIN djmdMyTag t ON osmt.MyTagID = t.ID
                    WHERE osmt.ContentID = c.ID
                      {osmt_del}
                      {tag_del}
                    ORDER BY {tag_seq}lower(t.Name) ASC
                )
            ) AS TagNames"""
        else:
            tag_expr = "NULL AS TagNames"

        if has_playlist:
            p_cols = {
                r[1]
                for r in conn.exec_driver_sql("PRAGMA table_info(djmdPlaylist);").fetchall()
            } if "djmdPlaylist" in existing_tables else set()
            osp_cols = {
                r[1]
                for r in conn.exec_driver_sql("PRAGMA table_info(djmdSongPlaylist);").fetchall()
            } if "djmdSongPlaylist" in existing_tables else set()
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

        matching_tag_ids = _resolve_tag_matching_ids(tag_id)
        tag_placeholders = ", ".join(f":tid_{i}" for i in range(len(matching_tag_ids)))
        params: Dict[str, Any] = {
            f"tid_{i}": tid for i, tid in enumerate(matching_tag_ids)
        }

        # Search query clause
        search_clause = ""
        if query and query.strip():
            album_search = (
                f"OR lower({album_expr}) LIKE :q" if album_expr != "NULL" else ""
            )
            key_search = (
                f"OR lower({key_expr}) LIKE :q" if key_expr != "NULL" else ""
            )
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
        default_order = f"lower(c.Title) {order_dir}"
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
            FROM djmdSongMyTag smt
            JOIN djmdContent c ON smt.ContentID = c.ID
            {join_artist}
            {join_album}
            {join_genre}
            {join_key}
            WHERE smt.MyTagID IN ({tag_placeholders})
              {smt_del}
              {c_del}
              {streaming_filter}
              {search_clause}
            GROUP BY c.ID
            ORDER BY {order_clause}
            {limit_clause};
        """
        rows = conn.execute(text(sql), params).mappings().fetchall()

        tracks: List[Dict[str, Any]] = []
        for idx, r in enumerate(rows, 1):
            tracks.append(build_track_record(r, extra={"track_no": idx}))

        return tracks


def sort_tags_in_folder(
    engine: Engine,
    folder_id: str,
    descending: bool = False,
) -> int:
    """Sort child tags inside a specified category/folder alphabetically.

    Updates the Seq column in djmdMyTag for all active tags under ParentID = folder_id.
    Returns the number of tags reordered.
    """
    now_utc = get_rekordbox_timestamp()

    with engine.begin() as conn:
        existing_tables = {
            r[0]
            for r in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table';"
            ).fetchall()
        }
        if "djmdMyTag" not in existing_tables:
            raise ValueError("Table djmdMyTag not found in database.")

        tag_cols = {
            r[1]
            for r in conn.exec_driver_sql("PRAGMA table_info(djmdMyTag);").fetchall()
        }

        del_filter = (
            "AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL)"
            if "rb_local_deleted" in tag_cols
            else ""
        )

        direction = "DESC" if descending else "ASC"
        matching_ids = _resolve_tag_matching_ids(folder_id) or [str(folder_id)]
        placeholders = ", ".join(f":fid_{i}" for i in range(len(matching_ids)))
        q_params: Dict[str, Any] = {f"fid_{i}": fid for i, fid in enumerate(matching_ids)}

        query = f"""
            SELECT ID, Name
            FROM djmdMyTag
            WHERE ParentID IN ({placeholders})
              AND Attribute = 0
              {del_filter}
            ORDER BY lower(Name) {direction}, Name {direction};
        """
        rows = conn.execute(text(query), q_params).fetchall()
        if not rows:
            return 0

        update_fields = ["Seq = :seq"]
        if "updated_at" in tag_cols:
            update_fields.append("updated_at = :now")
        if "rb_local_synced" in tag_cols:
            update_fields.append("rb_local_synced = 1")
        if "rb_local_data_status" in tag_cols:
            update_fields.append("rb_local_data_status = 1")

        update_sql = f"""
            UPDATE djmdMyTag
            SET {', '.join(update_fields)}
            WHERE ID = :tag_id;
        """

        for idx, row in enumerate(rows, start=1):
            tag_id = str(row[0])
            params: Dict[str, Any] = {
                "seq": idx,
                "tag_id": tag_id,
            }
            if "updated_at" in tag_cols:
                params["now"] = now_utc
            conn.execute(text(update_sql), params)

        return len(rows)


def cleanup_duplicate_song_tags(engine: Engine) -> int:
    """Find and clean up duplicate active associations in djmdSongMyTag.

    If multiple active rows exist for the same track and tag (including matching
    signed/unsigned 32-bit ID representations), keeps the earliest row and marks
    subsequent duplicate rows as soft-deleted (rb_local_deleted = 1), or deletes
    them if the rb_local_deleted column is not present.

    Returns the number of resolved duplicate rows.
    """
    now_utc = get_rekordbox_timestamp()
    with engine.begin() as conn:
        existing_tables = {
            r[0]
            for r in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table';"
            ).fetchall()
        }
        if "djmdSongMyTag" not in existing_tables:
            return 0

        smt_cols = {
            r[1]
            for r in conn.exec_driver_sql("PRAGMA table_info(djmdSongMyTag);").fetchall()
        }
        has_rb_del = "rb_local_deleted" in smt_cols
        has_updated_at = "updated_at" in smt_cols

        del_filter = (
            "WHERE (rb_local_deleted = 0 OR rb_local_deleted IS NULL)"
            if has_rb_del
            else ""
        )

        rows = conn.exec_driver_sql(
            f"SELECT ID, ContentID, MyTagID FROM djmdSongMyTag {del_filter} ORDER BY created_at ASC, ID ASC;"
        ).fetchall()

        seen_track_tags: Set[Tuple[str, str]] = set()
        duplicate_ids: List[str] = []

        for row_id, content_id, my_tag_id in rows:
            if not content_id or not my_tag_id:
                continue
            cid_str = str(content_id)
            matching_tids = _resolve_tag_matching_ids(my_tag_id)
            already_seen = any((cid_str, tid) in seen_track_tags for tid in matching_tids)
            if already_seen:
                duplicate_ids.append(str(row_id))
            else:
                for tid in matching_tids:
                    seen_track_tags.add((cid_str, tid))

        if not duplicate_ids:
            return 0

        # Mark duplicates as soft-deleted or DELETE them
        if has_rb_del:
            for d_id in duplicate_ids:
                if has_updated_at:
                    conn.exec_driver_sql(
                        "UPDATE djmdSongMyTag SET rb_local_deleted = 1, updated_at = :now WHERE ID = :id;",
                        {"now": now_utc, "id": d_id},
                    )
                else:
                    conn.exec_driver_sql(
                        "UPDATE djmdSongMyTag SET rb_local_deleted = 1 WHERE ID = :id;",
                        {"id": d_id},
                    )
        else:
            for d_id in duplicate_ids:
                conn.exec_driver_sql(
                    "DELETE FROM djmdSongMyTag WHERE ID = :id;",
                    {"id": d_id},
                )

        return len(duplicate_ids)
