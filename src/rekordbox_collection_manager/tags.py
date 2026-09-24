"""Tags database queries and management for Rekordbox MyTags.

Provides methods to query tag categories (filtering by Genre and Style),
hierarchies, track counts, and retrieve track lists for individual tags with full metadata.
"""

from typing import Any, Dict, List, Optional, Set, Tuple
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rekordbox_collection_manager.utils import (
    build_exclude_streaming_clause,
    build_track_record,
)


def _resolve_tag_matching_ids(raw_tag_id: Any) -> List[str]:
    """Resolve signed and unsigned 32-bit representations of a tag ID."""
    tag_str = str(raw_tag_id).strip()
    try:
        v_int = int(tag_str)
        ids = [str(v_int)]
        if v_int < 0:
            ids.append(str(v_int + (1 << 32)))
        elif v_int >= (1 << 31):
            ids.append(str(v_int - (1 << 32)))
        return list(dict.fromkeys(ids))
    except (ValueError, TypeError):
        return [tag_str] if tag_str else []


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
            smt_del = (
                "WHERE (rb_local_deleted = 0 OR rb_local_deleted IS NULL)"
                if "rb_local_deleted" in smt_cols
                else ""
            )
            count_sql = f"""
                SELECT MyTagID, count(*) 
                FROM djmdSongMyTag 
                {smt_del}
                GROUP BY MyTagID;
            """
            for row in conn.execute(text(count_sql)).fetchall():
                if row[0] is not None:
                    raw_id = str(row[0])
                    cnt = int(row[1])
                    for tid in _resolve_tag_matching_ids(raw_id):
                        track_counts[tid] = track_counts.get(tid, 0) + cnt

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
            tag_expr = """(
                SELECT GROUP_CONCAT(t.Name, '|||')
                FROM djmdSongMyTag osmt
                JOIN djmdMyTag t ON osmt.MyTagID = t.ID
                WHERE osmt.ContentID = c.ID
                  AND (osmt.rb_local_deleted = 0 OR osmt.rb_local_deleted IS NULL)
                  AND (t.rb_local_deleted = 0 OR t.rb_local_deleted IS NULL)
            ) AS TagNames"""
        else:
            tag_expr = "NULL AS TagNames"

        if has_playlist:
            playlist_expr = """(
                SELECT GROUP_CONCAT(p.Name, '|||')
                FROM djmdSongPlaylist osp
                JOIN djmdPlaylist p ON osp.PlaylistID = p.ID
                WHERE osp.ContentID = c.ID
                  AND p.Attribute = 0
                  AND (p.rb_local_deleted = 0 OR p.rb_local_deleted IS NULL)
                  AND (osp.rb_local_deleted = 0 OR osp.rb_local_deleted IS NULL)
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
            ORDER BY {order_clause}
            {limit_clause};
        """
        rows = conn.execute(text(sql), params).mappings().fetchall()

        tracks: List[Dict[str, Any]] = []
        for idx, r in enumerate(rows, 1):
            tracks.append(build_track_record(r, extra={"track_no": idx}))

        return tracks
