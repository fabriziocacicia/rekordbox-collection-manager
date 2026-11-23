"""Dynamic Catch-All Playlist (πagged-Unbound) Reconciliation Engine.

Maintains a root-level playlist in Rekordbox containing all tracks that have
at least one MyTag but do not belong to any regular user-created playlist.
This ensures Rekordbox Sync Manager exports every tagged track to USB drives
for CDJ/XDJ MyTag filtering.

Decoupled and local-first: operates purely on local master.db without cloud
or network dependencies.
"""

import logging
import os
import uuid
from typing import Any, Dict, Optional, Set, Tuple
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from rekordbox_collection_manager.backup_restore import create_database_backup
from rekordbox_collection_manager.utils import (
    build_exclude_streaming_clause,
    get_default_master_db_path,
    get_rekordbox_timestamp,
    is_rekordbox_running,
    open_database,
)

logger = logging.getLogger(__name__)

# Root-level playlist name starting with Greek pi (U+03C0) to sort alphabetically last
UNBOUND_PLAYLIST_NAME = "πagged-Unbound"


def ensure_unbound_playlist_exists(conn: Connection) -> str:
    """Ensure the πagged-Unbound playlist exists at root level (ParentID='root').

    If it already exists and is active, returns its ID.
    If it exists but is soft-deleted, reactivates it with updated timestamps.
    If it does not exist, creates it with next available sequence number.

    Args:
        conn: Active SQLAlchemy connection inside a transaction.

    Returns:
        The playlist ID (str).
    """
    now_utc = get_rekordbox_timestamp()

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

    # Check if a playlist with this name already exists at root
    check_sql = f"""
        SELECT ID, Name, Attribute, ParentID, Seq {', rb_local_deleted' if has_rb_del else ''}
        FROM djmdPlaylist
        WHERE Name = :name
          AND ParentID = 'root'
        LIMIT 1;
    """
    existing = conn.execute(text(check_sql), {"name": UNBOUND_PLAYLIST_NAME}).fetchone()

    # Calculate next sequence number under root
    seq_sql = f"""
        SELECT COALESCE(MAX(Seq), 0) + 1
        FROM djmdPlaylist
        WHERE ParentID = 'root'
          {del_filter};
    """
    next_seq = int(conn.execute(text(seq_sql)).scalar() or 1)

    if existing:
        pl_id = str(existing[0])
        is_deleted = bool(existing[5]) if (has_rb_del and len(existing) > 5 and existing[5] is not None) else False

        if is_deleted:
            update_fields = ["Attribute = 0", "Seq = :seq"]
            params: Dict[str, Any] = {
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
            logger.info("Reactivated soft-deleted %s playlist (ID: %s)", UNBOUND_PLAYLIST_NAME, pl_id)
        return pl_id

    # Create new playlist
    new_id = str(uuid.uuid4())
    insert_cols = ["ID", "Name", "Attribute", "ParentID", "Seq"]
    insert_vals = [":id", ":name", "0", "'root'", ":seq"]
    params = {
        "id": new_id,
        "name": UNBOUND_PLAYLIST_NAME,
        "seq": next_seq,
    }

    if "created_at" in pl_cols:
        insert_cols.append("created_at")
        insert_vals.append(":now")
        params["now"] = now_utc
    if "updated_at" in pl_cols:
        insert_cols.append("updated_at")
        insert_vals.append(":now")
        params["now"] = now_utc
    if has_rb_del:
        insert_cols.append("rb_local_deleted")
        insert_vals.append("0")
    if "rb_local_synced" in pl_cols:
        insert_cols.append("rb_local_synced")
        insert_vals.append("1")
    if "rb_local_data_status" in pl_cols:
        insert_cols.append("rb_local_data_status")
        insert_vals.append("1")

    insert_sql = f"INSERT INTO djmdPlaylist ({', '.join(insert_cols)}) VALUES ({', '.join(insert_vals)});"
    conn.execute(text(insert_sql), params)
    logger.info("Created new %s playlist (ID: %s) under root", UNBOUND_PLAYLIST_NAME, new_id)
    return new_id


def get_unbound_target_track_ids(
    conn: Connection, unbound_playlist_id: Optional[str] = None
) -> Set[str]:
    """Resolve all active, non-streaming track IDs that have >= 1 MyTag and no regular user playlist.

    A regular user playlist is defined as djmdPlaylist with:
      - Attribute = 0 (or NULL)
      - rb_local_deleted = 0 (or NULL)
      - ID != unbound_playlist_id
      - Name != UNBOUND_PLAYLIST_NAME

    Smart playlists (Attribute = 2 or 4), folders (Attribute = 1), and history sessions
    (djmdHistory) do NOT disqualify a track from this set.

    Args:
        conn: Active SQLAlchemy connection.
        unbound_playlist_id: The ID of the πagged-Unbound playlist to exclude from user playlists.

    Returns:
        Set of matching ContentID strings.
    """
    existing_tables = {
        r[0]
        for r in conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table';"
        ).fetchall()
    }
    if "djmdContent" not in existing_tables or "djmdSongMyTag" not in existing_tables:
        return set()

    c_cols = {
        r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdContent);").fetchall()
    }
    smt_cols = {
        r[1]
        for r in conn.exec_driver_sql("PRAGMA table_info(djmdSongMyTag);").fetchall()
    }
    sp_cols = (
        {
            r[1]
            for r in conn.exec_driver_sql(
                "PRAGMA table_info(djmdSongPlaylist);"
            ).fetchall()
        }
        if "djmdSongPlaylist" in existing_tables
        else set()
    )
    p_cols = (
        {
            r[1]
            for r in conn.exec_driver_sql("PRAGMA table_info(djmdPlaylist);").fetchall()
        }
        if "djmdPlaylist" in existing_tables
        else set()
    )

    c_del = "AND (c.rb_local_deleted = 0 OR c.rb_local_deleted IS NULL)" if "rb_local_deleted" in c_cols else ""
    smt_del = "AND (smt.rb_local_deleted = 0 OR smt.rb_local_deleted IS NULL)" if "rb_local_deleted" in smt_cols else ""
    streaming_clause = build_exclude_streaming_clause(c_cols, table_alias="c")
    stock_type_clause = "AND (c.StockType = 0 OR c.StockType IS NULL)" if "StockType" in c_cols else ""

    # Exclude tracks already present in any active regular user playlist
    not_in_user_playlist_clause = ""
    params: Dict[str, Any] = {"unbound_name": UNBOUND_PLAYLIST_NAME}
    if "djmdSongPlaylist" in existing_tables and "djmdPlaylist" in existing_tables:
        sp_del = "AND (sp.rb_local_deleted = 0 OR sp.rb_local_deleted IS NULL)" if "rb_local_deleted" in sp_cols else ""
        p_del = "AND (p.rb_local_deleted = 0 OR p.rb_local_deleted IS NULL)" if "rb_local_deleted" in p_cols else ""
        attr_filter = "AND (p.Attribute = 0 OR p.Attribute IS NULL)" if "Attribute" in p_cols else ""

        exclude_pl_id = ""
        if unbound_playlist_id:
            exclude_pl_id = "AND p.ID != :unbound_id"
            params["unbound_id"] = str(unbound_playlist_id)

        not_in_user_playlist_clause = f"""
            AND c.ID NOT IN (
                SELECT sp.ContentID
                FROM djmdSongPlaylist sp
                JOIN djmdPlaylist p ON sp.PlaylistID = p.ID
                WHERE 1=1
                  {attr_filter}
                  AND p.Name != :unbound_name
                  {exclude_pl_id}
                  {p_del}
                  {sp_del}
            )
        """

    sql = f"""
        SELECT DISTINCT c.ID
        FROM djmdContent c
        JOIN djmdSongMyTag smt ON smt.ContentID = c.ID
        WHERE 1=1
          {c_del}
          {smt_del}
          {streaming_clause}
          {stock_type_clause}
          {not_in_user_playlist_clause};
    """

    rows = conn.execute(text(sql), params).fetchall()
    return {str(r[0]) for r in rows if r[0] is not None}


def reconcile_unbound_playlist(
    engine: Optional[Engine] = None,
    db_path: Optional[str] = None,
    force: bool = False,
    create_backup: bool = True,
) -> Dict[str, Any]:
    """Reconcile the membership of πagged-Unbound against local master.db.

    Tracks that have at least one MyTag and no regular user playlist are added.
    Tracks no longer qualifying (assigned to a regular playlist or untagged) are removed.

    Args:
        engine: Existing SQLAlchemy Engine. If None, open_database() is used.
        db_path: Path to master.db. Discovered automatically if None.
        force: Bypass Rekordbox running check if True.
        create_backup: Create pre-mutation backup of master.db if True.

    Returns:
        Dict with reconciliation statistics: {"added": int, "removed": int, "total": int, "playlist_id": str}.

    Raises:
        RuntimeError: If Pioneer Rekordbox is running and force=False.
    """
    if is_rekordbox_running() and not force:
        raise RuntimeError("Pioneer Rekordbox is currently running. Cannot mutate master.db.")

    resolved_path = None
    if engine is None:
        resolved_path = db_path or get_default_master_db_path()
        if not resolved_path or not os.path.exists(resolved_path):
            raise FileNotFoundError(f"Rekordbox master.db not found at: {resolved_path}")
        if create_backup:
            try:
                create_database_backup(resolved_path, force=True)
            except Exception as e:
                logger.warning("Safety backup warning (proceeding): %s", e)
        engine, _ = open_database(resolved_path)
    elif create_backup and db_path and os.path.exists(db_path):
        try:
            create_database_backup(db_path, force=True)
        except Exception as e:
            logger.warning("Safety backup warning (proceeding): %s", e)

    now_utc = get_rekordbox_timestamp()

    with engine.begin() as conn:
        pl_id = ensure_unbound_playlist_exists(conn)
        target_track_ids = get_unbound_target_track_ids(conn, unbound_playlist_id=pl_id)

        sp_cols = {
            r[1]
            for r in conn.exec_driver_sql("PRAGMA table_info(djmdSongPlaylist);").fetchall()
        }
        has_rb_del = "rb_local_deleted" in sp_cols

        # Fetch current entries in πagged-Unbound
        existing_sp_rows = conn.execute(
            text("""
                SELECT ID, ContentID, TrackNo, rb_local_deleted
                FROM djmdSongPlaylist
                WHERE PlaylistID = :pid;
            """),
            {"pid": pl_id},
        ).fetchall()

        # ContentID -> (ID, TrackNo, is_deleted)
        current_map: Dict[str, Tuple[str, int, bool]] = {}
        active_ids: Set[str] = set()
        max_track_no = 0

        for r in existing_sp_rows:
            sp_id, cid, t_no, del_val = str(r[0]), str(r[1]), int(r[2] or 0), bool(r[3]) if (has_rb_del and r[3] is not None) else False
            current_map[cid] = (sp_id, t_no, del_val)
            if not del_val:
                active_ids.add(cid)
                if t_no > max_track_no:
                    max_track_no = t_no

        to_remove = active_ids - target_track_ids
        to_add = target_track_ids - active_ids

        # 1. Soft-delete or remove disqualified tracks
        if to_remove:
            logger.info("Removing %d tracks from %s (now assigned to a user playlist or untagged)", len(to_remove), UNBOUND_PLAYLIST_NAME)
            if has_rb_del:
                del_updates = ["rb_local_deleted = 1"]
                if "updated_at" in sp_cols:
                    del_updates.append("updated_at = :now")
                if "rb_local_synced" in sp_cols:
                    del_updates.append("rb_local_synced = 1")
                if "rb_local_data_status" in sp_cols:
                    del_updates.append("rb_local_data_status = 1")

                conn.execute(
                    text(f"""
                        UPDATE djmdSongPlaylist
                        SET {', '.join(del_updates)}
                        WHERE PlaylistID = :pid
                          AND ContentID IN ({', '.join(f':r_{i}' for i in range(len(to_remove)))});
                    """),
                    {**{"pid": pl_id, "now": now_utc}, **{f"r_{i}": cid for i, cid in enumerate(to_remove)}},
                )
            else:
                conn.execute(
                    text(f"""
                        DELETE FROM djmdSongPlaylist
                        WHERE PlaylistID = :pid
                          AND ContentID IN ({', '.join(f':r_{i}' for i in range(len(to_remove)))});
                    """),
                    {**{"pid": pl_id}, **{f"r_{i}": cid for i, cid in enumerate(to_remove)}},
                )

        # 2. Add or reactivate newly qualifying tracks
        if to_add:
            logger.info("Adding %d tracks to %s", len(to_add), UNBOUND_PLAYLIST_NAME)
            for cid in sorted(to_add):
                max_track_no += 1
                if cid in current_map and has_rb_del and current_map[cid][2]:
                    # Reactivate existing soft-deleted row
                    sp_id = current_map[cid][0]
                    react_updates = ["rb_local_deleted = 0", "TrackNo = :tno"]
                    params_react: Dict[str, Any] = {"tno": max_track_no, "sp_id": sp_id}
                    if "updated_at" in sp_cols:
                        react_updates.append("updated_at = :now")
                        params_react["now"] = now_utc
                    if "rb_local_synced" in sp_cols:
                        react_updates.append("rb_local_synced = 1")
                    if "rb_local_data_status" in sp_cols:
                        react_updates.append("rb_local_data_status = 1")

                    conn.execute(
                        text(f"UPDATE djmdSongPlaylist SET {', '.join(react_updates)} WHERE ID = :sp_id;"),
                        params_react,
                    )
                else:
                    # Insert fresh row
                    new_sp_id = str(uuid.uuid4())
                    insert_cols = ["ID", "PlaylistID", "ContentID", "TrackNo"]
                    insert_vals = [":id", ":pid", ":cid", ":tno"]
                    params_ins: Dict[str, Any] = {
                        "id": new_sp_id,
                        "pid": pl_id,
                        "cid": cid,
                        "tno": max_track_no,
                    }
                    if "created_at" in sp_cols:
                        insert_cols.append("created_at")
                        insert_vals.append(":now")
                        params_ins["now"] = now_utc
                    if "updated_at" in sp_cols:
                        insert_cols.append("updated_at")
                        insert_vals.append(":now")
                        params_ins["now"] = now_utc
                    if has_rb_del:
                        insert_cols.append("rb_local_deleted")
                        insert_vals.append("0")
                    if "rb_local_synced" in sp_cols:
                        insert_cols.append("rb_local_synced")
                        insert_vals.append("1")
                    if "rb_local_data_status" in sp_cols:
                        insert_cols.append("rb_local_data_status")
                        insert_vals.append("1")

                    conn.execute(
                        text(f"INSERT INTO djmdSongPlaylist ({', '.join(insert_cols)}) VALUES ({', '.join(insert_vals)});"),
                        params_ins,
                    )

        # 3. Touch updated_at on the playlist if any mutations took place
        if to_remove or to_add:
            pl_cols = {
                r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdPlaylist);").fetchall()
            }
            pl_updates = []
            pl_params: Dict[str, Any] = {"pid": pl_id}
            if "updated_at" in pl_cols:
                pl_updates.append("updated_at = :now")
                pl_params["now"] = now_utc
            if "rb_local_synced" in pl_cols:
                pl_updates.append("rb_local_synced = 1")
            if "rb_local_data_status" in pl_cols:
                pl_updates.append("rb_local_data_status = 1")

            if pl_updates:
                conn.execute(
                    text(f"UPDATE djmdPlaylist SET {', '.join(pl_updates)} WHERE ID = :pid;"),
                    pl_params,
                )

        total_active = len(target_track_ids)
        logger.info(
            "Reconciliation complete for %s: added=%d, removed=%d, total=%d",
            UNBOUND_PLAYLIST_NAME,
            len(to_add),
            len(to_remove),
            total_active,
        )
        return {
            "added": len(to_add),
            "removed": len(to_remove),
            "total": total_active,
            "playlist_id": pl_id,
        }


def sync_unbound_cli() -> None:
    """CLI entrypoint for standalone reconciliation of πagged-Unbound playlist."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description=f"Reconcile the '{UNBOUND_PLAYLIST_NAME}' catch-all playlist for tagged tracks without a playlist."
    )
    parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Bypass the Pioneer Rekordbox running check",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="Path to Rekordbox master.db (default: auto-detected)",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip automatic pre-mutation safety backup",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview target tracks and changes without modifying the database",
    )

    args = parser.parse_args()

    print(f"=== {UNBOUND_PLAYLIST_NAME} Reconciliation ===")
    try:
        if args.dry_run:
            db_path = args.db or get_default_master_db_path()
            if not db_path or not os.path.exists(db_path):
                print(f"Error: master.db not found at {db_path}", file=sys.stderr)
                sys.exit(1)
            engine, _ = open_database(db_path)
            with engine.connect() as conn:
                existing = conn.execute(
                    text("SELECT ID FROM djmdPlaylist WHERE Name = :name AND ParentID = 'root' LIMIT 1;"),
                    {"name": UNBOUND_PLAYLIST_NAME},
                ).fetchone()
                pl_id = str(existing[0]) if existing else None
                targets = get_unbound_target_track_ids(conn, unbound_playlist_id=pl_id)
                current_active = set()
                if pl_id:
                    rows = conn.execute(
                        text("SELECT ContentID FROM djmdSongPlaylist WHERE PlaylistID = :pid AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL);"),
                        {"pid": pl_id},
                    ).fetchall()
                    current_active = {str(r[0]) for r in rows if r[0] is not None}
                to_add = targets - current_active
                to_remove = current_active - targets
                print(f"Playlist ID:    {pl_id or '(Will be created)'}")
                print(f"Current Tracks: {len(current_active)}")
                print(f"Target Tracks:  {len(targets)}")
                print(f"To Add:         {len(to_add)}")
                print(f"To Remove:      {len(to_remove)}")
                print("[Dry-run completed. No database changes were made.]")
                sys.exit(0)

        stats = reconcile_unbound_playlist(
            db_path=args.db,
            force=args.force,
            create_backup=not args.no_backup,
        )
        print(f"Successfully reconciled '{UNBOUND_PLAYLIST_NAME}':")
        print(f"  Tracks Added:   {stats['added']}")
        print(f"  Tracks Removed: {stats['removed']}")
        print(f"  Total Tracks:   {stats['total']}")
        sys.exit(0)
    except Exception as e:
        print(f"Error during reconciliation: {e}", file=sys.stderr)
        sys.exit(1)

