"""Interactive CLI tool to rename Rekordbox history sessions in master.db or exportLibrary.db."""

import argparse
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import questionary
from pyrekordbox.config import get_config
from pyrekordbox.db6.database import BLOB
from pyrekordbox.utils import deobfuscate
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlcipher3 import dbapi2 as sqlcipher_driver

from rekordbox_collection_manager.utils import (
    DEVICE_LIBRARY_PLUS_KEY,
    MONTH_NAME_TO_INT,
    build_track_record,
    clean_session_name,
    compose_history_name,
    ensure_rekordbox_not_running,
    extract_date_from_name,
    extract_year_month_from_name_or_date,
    get_default_master_db_path,
    get_rekordbox_timestamp,
    is_rekordbox_running,
    natural_sort_key,
    normalize_date_input,
    open_database,
    parse_history_name,
    parse_month_number,
    parse_session_paren_number,
    parse_year_month,
    resolve_db_path,
)


def history_session_sort_key(row_dict: Dict[str, Any]) -> Tuple:
    """Sort key for history sessions within a month folder.

    Orders chronologically: day 01 at top, day 31 at bottom.
    Extracts date from Name if formatted as YYYY-MM-DD (or YYYY/MM/DD),
    falling back to DateCreated.
    Within the same date, entries without () sort first (-1), followed by (0), (1), (2)...
    Then uses natural_sort_key on the cleaned name, DateCreated, and ID for tie-breaking.
    """
    name = row_dict.get("Name") or ""
    date_created = row_dict.get("DateCreated") or ""
    id_val = str(row_dict.get("ID") or "")

    # Look for YYYY-MM-DD or YYYY/MM/DD in name
    m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", name)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        extracted_date = f"{y:04d}-{mo:02d}-{d:02d}"
    elif date_created and len(date_created) >= 10:
        extracted_date = date_created[:10]
    else:
        extracted_date = "9999-99-99"

    paren_num = parse_session_paren_number(name)
    cleaned_name = clean_session_name(name)

    return (extracted_date, paren_num, natural_sort_key(cleaned_name), date_created, id_val)



def year_sort_key(row_dict: Dict[str, Any]) -> Tuple:
    """Sort key for year folders under root (chronological ascending: e.g. 2023 -> 2026)."""
    name = str(row_dict.get("Name") or "").strip()
    id_val = str(row_dict.get("ID") or "").strip()
    year_num = 9999
    if name.isdigit():
        year_num = int(name)
    elif id_val.isdigit():
        year_num = int(id_val)
    return (year_num, natural_sort_key(name), id_val)


def realign_history_order(engine: Engine) -> Dict[str, int]:
    """Realign Seq index in djmdHistory table so that:
    - Years under 'root' are ordered chronologically (e.g. 2023 -> 2026).
    - Month folders under Year folders are ordered by actual month (1..12).
    - History sessions under Month folders are ordered chronologically (day 01 top, day 31 bottom).

    Returns a dict with statistics: {"updated_rows": int, "parents_processed": int}.
    """
    stats = {"updated_rows": 0, "parents_processed": 0}
    now_utc = get_rekordbox_timestamp()

    with engine.begin() as conn:
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdHistory);").fetchall()}
        if "Seq" not in cols or "ParentID" not in cols:
            return stats

        has_updated_at = "updated_at" in cols
        del_filter = "AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL)" if "rb_local_deleted" in cols else ""

        # 1. Realign any history sessions whose ParentID does not match the date in their Name
        if "ParentID" in cols and "Name" in cols:
            session_rows = conn.execute(
                text(f"SELECT ID, Name, ParentID, DateCreated FROM djmdHistory WHERE (Attribute = 0 OR Attribute IS NULL) {del_filter};")
            ).mappings().fetchall()

            for s in session_rows:
                s_name = s.get("Name") or ""
                s_parent = str(s.get("ParentID") or "")
                s_date = s.get("DateCreated") or ""
                y, m = extract_year_month_from_name_or_date(s_name, s_date)
                if y and m:
                    expected_parent = f"{y}{m:02d}"
                    if s_parent != expected_parent:
                        date_str = s_date or f"{y:04d}-{m:02d}-01"
                        ensure_djmd_history_folders(conn, y, m, date_str, cols)

                        update_fields = ["ParentID = :parent_id"]
                        params: Dict[str, Any] = {"parent_id": expected_parent, "id": s["ID"]}
                        if has_updated_at:
                            update_fields.append("updated_at = :updated_at")
                            params["updated_at"] = now_utc

                        conn.execute(
                            text(f"UPDATE djmdHistory SET {', '.join(update_fields)} WHERE ID = :id"),
                            params,
                        )
                        stats["updated_rows"] += 1

        # 2. Fetch all distinct ParentIDs to sort their children
        parent_rows = conn.exec_driver_sql(
            f"SELECT DISTINCT ParentID FROM djmdHistory WHERE ParentID IS NOT NULL {del_filter};"
        ).fetchall()
        parent_ids = [r[0] for r in parent_rows if r[0] is not None]

        for parent_id in parent_ids:
            query = f"""
                SELECT ID, Name, Attribute, ParentID, DateCreated, Seq
                FROM djmdHistory
                WHERE ParentID = :parent_id {del_filter}
            """
            rows = conn.execute(text(query), {"parent_id": parent_id}).mappings().fetchall()
            if not rows:
                continue

            children = [dict(r) for r in rows]

            # Determine sorting key depending on parent and child types:
            # 1. If parent_id == 'root' -> children are Year folders
            if str(parent_id).lower() == "root":
                children.sort(key=year_sort_key)
            # 2. If children are Month folders (Attribute == 1)
            elif all(c.get("Attribute") == 1 for c in children):
                children.sort(
                    key=lambda c: (
                        parse_month_number(c.get("Name"), str(c.get("ID")), c.get("DateCreated")),
                        natural_sort_key(c.get("Name")),
                    )
                )
            # 3. Otherwise (history sessions or mixed)
            else:
                children.sort(key=history_session_sort_key)

            # Assign sequential Seq (1, 2, 3...)
            for seq_index, child in enumerate(children, start=1):
                child_id = child["ID"]
                current_seq = child.get("Seq")
                if current_seq != seq_index:
                    if has_updated_at:
                        conn.execute(
                            text("UPDATE djmdHistory SET Seq = :seq, updated_at = :updated_at WHERE ID = :id"),
                            {"seq": seq_index, "updated_at": now_utc, "id": child_id},
                        )
                    else:
                        conn.execute(
                            text("UPDATE djmdHistory SET Seq = :seq WHERE ID = :id"),
                            {"seq": seq_index, "id": child_id},
                        )
                    stats["updated_rows"] += 1

            stats["parents_processed"] += 1

    return stats


def ensure_djmd_history_folders(
    conn: Any,
    year: int,
    month: int,
    date_str: str,
    cols: set,
) -> str:
    """Ensure Year and Month folder records exist in djmdHistory table.

    Returns the target Month folder ID (e.g. '202608').
    """
    now_utc = get_rekordbox_timestamp()
    year_id = str(year)
    month_id = f"{year}{month:02d}"

    # 1. Check and create Year folder if missing
    has_year = conn.exec_driver_sql(
        "SELECT 1 FROM djmdHistory WHERE ID = :id", {"id": year_id}
    ).fetchone()

    if not has_year:
        year_data = {
            "ID": year_id,
            "Name": year_id,
            "ParentID": "root",
            "Attribute": 1,
            "DateCreated": date_str,
            "UUID": year_id,
            "Seq": 1,
            "rb_data_status": 0,
            "rb_local_data_status": 0,
            "rb_local_deleted": 0,
            "rb_local_synced": 1,
            "created_at": now_utc,
            "updated_at": now_utc,
        }
        insert_cols = [k for k in year_data if k in cols]
        col_names = ", ".join(insert_cols)
        placeholders = ", ".join(f":{k}" for k in insert_cols)
        conn.execute(
            text(f"INSERT INTO djmdHistory ({col_names}) VALUES ({placeholders})"),
            {k: year_data[k] for k in insert_cols},
        )
    elif "rb_local_deleted" in cols:
        set_parts = ["rb_local_deleted = 0"]
        if "rb_data_status" in cols:
            set_parts.append("rb_data_status = 0")
        if "updated_at" in cols:
            set_parts.append(f"updated_at = '{now_utc}'")
        conn.execute(
            text(f"UPDATE djmdHistory SET {', '.join(set_parts)} WHERE ID = :id AND rb_local_deleted != 0"),
            {"id": year_id},
        )

    # 2. Check and create Month folder if missing
    has_month = conn.exec_driver_sql(
        "SELECT 1 FROM djmdHistory WHERE ID = :id", {"id": month_id}
    ).fetchone()

    if not has_month:
        month_data = {
            "ID": month_id,
            "Name": str(month),
            "ParentID": year_id,
            "Attribute": 1,
            "DateCreated": date_str,
            "UUID": month_id,
            "Seq": month,
            "rb_data_status": 0,
            "rb_local_data_status": 0,
            "rb_local_deleted": 0,
            "rb_local_synced": 1,
            "created_at": now_utc,
            "updated_at": now_utc,
        }
        insert_cols = [k for k in month_data if k in cols]
        col_names = ", ".join(insert_cols)
        placeholders = ", ".join(f":{k}" for k in insert_cols)
        conn.execute(
            text(f"INSERT INTO djmdHistory ({col_names}) VALUES ({placeholders})"),
            {k: month_data[k] for k in insert_cols},
        )
    elif "rb_local_deleted" in cols:
        set_parts = ["rb_local_deleted = 0"]
        if "rb_data_status" in cols:
            set_parts.append("rb_data_status = 0")
        if "updated_at" in cols:
            set_parts.append(f"updated_at = '{now_utc}'")
        conn.execute(
            text(f"UPDATE djmdHistory SET {', '.join(set_parts)} WHERE ID = :id AND rb_local_deleted != 0"),
            {"id": month_id},
        )

    return month_id


def fetch_history_records(
    engine: Engine, table_format: str, include_deleted: bool = False
) -> List[Dict[str, Any]]:
    """Fetch history items from either djmdHistory or history table.

    If include_deleted is False (default), only active items are returned.
    If include_deleted is True, both active and soft-deleted items are returned,
    each tagged with 'is_deleted': bool.
    """
    records = []
    with engine.connect() as conn:
        if table_format == "djmdHistory":
            cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdHistory);").fetchall()}
            has_parent = "ParentID" in cols
            has_del = "rb_local_deleted" in cols
            del_filter = (
                "AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL)"
                if (has_del and not include_deleted)
                else ""
            )
            query = f"""
                SELECT ID, Name, DateCreated, Attribute,
                       {("ParentID" if has_parent else "NULL")} AS ParentID,
                       {("COALESCE(rb_local_deleted, 0)" if has_del else "0")} AS is_deleted
                FROM djmdHistory 
                WHERE (Attribute = 0 OR Attribute IS NULL) {del_filter}
                ORDER BY rowid DESC;
            """
            rows = conn.execute(text(query)).mappings().fetchall()
            for r in rows:
                records.append({
                    "id": str(r["ID"]),
                    "name": r["Name"] or "(Untitled)",
                    "date": r["DateCreated"] or "",
                    "parent_id": str(r["ParentID"]) if r.get("ParentID") is not None else None,
                    "table": "djmdHistory",
                    "is_deleted": bool(r["is_deleted"]),
                })
        elif table_format == "history":
            cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(history);").fetchall()}
            has_del = "rb_local_deleted" in cols
            del_filter = (
                "AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL)"
                if (has_del and not include_deleted)
                else ""
            )
            query = f"""
                SELECT history_id, name, attribute,
                       {("COALESCE(rb_local_deleted, 0)" if has_del else "0")} AS is_deleted
                FROM history 
                WHERE (attribute = 0 OR attribute IS NULL) {del_filter}
                ORDER BY history_id DESC;
            """
            rows = conn.execute(text(query)).mappings().fetchall()
            for r in rows:
                records.append({
                    "id": str(r["history_id"]),
                    "name": r["name"] or "(Untitled)",
                    "date": "",
                    "parent_id": None,
                    "table": "history",
                    "is_deleted": bool(r["is_deleted"]),
                })
    return records


def soft_delete_history_session(engine: Engine, history_id: str) -> bool:
    """Soft-delete a Rekordbox history session without data loss.

    Sets rb_local_deleted = 1 and rb_data_status = 2 on djmdHistory and all
    associated djmdSongHistory entries. Decreases the DJ play count of tracks
    played in this session by the number of times they were played.
    If the parent Month folder (and Year folder) has no remaining active sessions,
    marks them as deleted as well, matching Rekordbox's native cleanup behavior.
    """
    now_utc = get_rekordbox_timestamp()
    with engine.begin() as conn:
        tables_res = conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
        existing_tables = {r[0] for r in tables_res}

        if "djmdHistory" in existing_tables:
            cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdHistory);").fetchall()}
            if "rb_local_deleted" not in cols:
                return False

            row = conn.execute(
                text(f"SELECT ID, ParentID{', rb_local_deleted' if 'rb_local_deleted' in cols else ''} FROM djmdHistory WHERE ID = :hid;"),
                {"hid": history_id},
            ).mappings().fetchone()
            if not row:
                return False

            # If already deleted, do not re-process (prevents double-decrement)
            if "rb_local_deleted" in cols and row.get("rb_local_deleted") == 1:
                return True

            parent_id = row.get("ParentID")

            # 1. Collect active tracks played in this session to decrement their play count
            played_tracks: List[Tuple[str, int]] = []
            if "djmdSongHistory" in existing_tables:
                sh_cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdSongHistory);").fetchall()}
                sh_filter = "AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL)" if "rb_local_deleted" in sh_cols else ""
                pt_rows = conn.execute(
                    text(f"""
                        SELECT ContentID, COUNT(*) AS play_count
                        FROM djmdSongHistory
                        WHERE HistoryID = :hid
                          {sh_filter}
                          AND ContentID IS NOT NULL
                        GROUP BY ContentID;
                    """),
                    {"hid": history_id},
                ).fetchall()
                played_tracks = [(str(r[0]), int(r[1])) for r in pt_rows if r[0]]

            # 2. Soft delete the session row in djmdHistory
            set_parts = ["rb_local_deleted = 1"]
            if "rb_data_status" in cols:
                set_parts.append("rb_data_status = 2")
            if "updated_at" in cols:
                set_parts.append("updated_at = :now")

            conn.execute(
                text(f"UPDATE djmdHistory SET {', '.join(set_parts)} WHERE ID = :hid;"),
                {"hid": history_id, "now": now_utc},
            )

            # 3. Soft delete associated tracks in djmdSongHistory
            if "djmdSongHistory" in existing_tables:
                sh_cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdSongHistory);").fetchall()}
                if "rb_local_deleted" in sh_cols:
                    sh_set = ["rb_local_deleted = 1"]
                    if "rb_data_status" in sh_cols:
                        sh_set.append("rb_data_status = 2")
                    if "updated_at" in sh_cols:
                        sh_set.append("updated_at = :now")
                    conn.execute(
                        text(f"UPDATE djmdSongHistory SET {', '.join(sh_set)} WHERE HistoryID = :hid;"),
                        {"hid": history_id, "now": now_utc},
                    )

            # 4. Decrement play count in djmdContent by the number of times played
            if "djmdContent" in existing_tables and played_tracks:
                c_cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdContent);").fetchall()}
                play_col = "DJPlayCount" if "DJPlayCount" in c_cols else ("PlayCount" if "PlayCount" in c_cols else None)
                if play_col:
                    has_c_updated = "updated_at" in c_cols
                    c_updates = [f"{play_col} = MAX(0, COALESCE({play_col}, 0) - :play_cnt)"]
                    if has_c_updated:
                        c_updates.append("updated_at = :now")
                    update_c_sql = f"UPDATE djmdContent SET {', '.join(c_updates)} WHERE ID = :cid;"
                    for cid, cnt in played_tracks:
                        params: Dict[str, Any] = {"play_cnt": cnt, "cid": cid}
                        if has_c_updated:
                            params["now"] = now_utc
                        conn.execute(text(update_c_sql), params)

            # 5. Check if parent Month folder is now empty of active sessions
            if parent_id and str(parent_id).lower() != "root":
                active_in_month = conn.execute(
                    text("""
                        SELECT count(*) FROM djmdHistory
                        WHERE ParentID = :parent_id
                          AND (Attribute = 0 OR Attribute IS NULL)
                          AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL);
                    """),
                    {"parent_id": parent_id},
                ).scalar()

                if active_in_month == 0:
                    month_row = conn.execute(
                        text("SELECT ParentID FROM djmdHistory WHERE ID = :mid;"),
                        {"mid": parent_id},
                    ).mappings().fetchone()
                    year_id = month_row.get("ParentID") if month_row else None

                    conn.execute(
                        text(f"UPDATE djmdHistory SET {', '.join(set_parts)} WHERE ID = :mid;"),
                        {"mid": parent_id, "now": now_utc},
                    )

                    # Check if Year folder is now empty of active Month folders
                    if year_id and str(year_id).lower() != "root":
                        active_in_year = conn.execute(
                            text("""
                                SELECT count(*) FROM djmdHistory
                                WHERE ParentID = :year_id
                                  AND Attribute = 1
                                  AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL);
                            """),
                            {"year_id": year_id},
                        ).scalar()

                        if active_in_year == 0:
                            conn.execute(
                                text(f"UPDATE djmdHistory SET {', '.join(set_parts)} WHERE ID = :yid;"),
                                {"yid": year_id, "now": now_utc},
                            )

            return True

        elif "history" in existing_tables:
            cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(history);").fetchall()}
            if "rb_local_deleted" not in cols:
                return False

            h_row = conn.execute(
                text(f"SELECT history_id{', rb_local_deleted' if 'rb_local_deleted' in cols else ''} FROM history WHERE history_id = :hid;"),
                {"hid": history_id},
            ).mappings().fetchone()
            if not h_row:
                return False

            if "rb_local_deleted" in cols and h_row.get("rb_local_deleted") == 1:
                return True

            usb_played_tracks: List[Tuple[str, int]] = []
            if "songHistory" in existing_tables:
                sh_cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(songHistory);").fetchall()}
                sh_filter = "AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL)" if "rb_local_deleted" in sh_cols else ""
                pt_rows = conn.execute(
                    text(f"""
                        SELECT content_id, COUNT(*) AS play_count
                        FROM songHistory
                        WHERE history_id = :hid
                          {sh_filter}
                          AND content_id IS NOT NULL
                        GROUP BY content_id;
                    """),
                    {"hid": history_id},
                ).fetchall()
                usb_played_tracks = [(str(r[0]), int(r[1])) for r in pt_rows if r[0]]

            if "rb_local_deleted" in cols:
                conn.execute(
                    text("UPDATE history SET rb_local_deleted = 1 WHERE history_id = :hid;"),
                    {"hid": history_id},
                )
            if "songHistory" in existing_tables:
                sh_cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(songHistory);").fetchall()}
                if "rb_local_deleted" in sh_cols:
                    conn.execute(
                        text("UPDATE songHistory SET rb_local_deleted = 1 WHERE history_id = :hid;"),
                        {"hid": history_id},
                    )

            if "content" in existing_tables and usb_played_tracks:
                c_cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(content);").fetchall()}
                play_col = "DJPlayCount" if "DJPlayCount" in c_cols else ("PlayCount" if "PlayCount" in c_cols else None)
                id_col = "ID" if "ID" in c_cols else ("content_id" if "content_id" in c_cols else None)
                if play_col and id_col:
                    has_c_updated = "updated_at" in c_cols
                    c_updates = [f"{play_col} = MAX(0, COALESCE({play_col}, 0) - :play_cnt)"]
                    if has_c_updated:
                        c_updates.append("updated_at = :now")
                    update_c_sql = f"UPDATE content SET {', '.join(c_updates)} WHERE {id_col} = :cid;"
                    for cid, cnt in usb_played_tracks:
                        params = {"play_cnt": cnt, "cid": cid}
                        if has_c_updated:
                            params["now"] = now_utc
                        conn.execute(text(update_c_sql), params)

            return True

    return False


def restore_history_session(engine: Engine, history_id: str) -> bool:
    """Restore a previously soft-deleted Rekordbox history session and its tracks.

    Reactivates djmdHistory and djmdSongHistory records (rb_local_deleted = 0, rb_data_status = 0),
    re-increments the DJ play count of associated tracks by the number of times they were played,
    ensures the Month and Year folders are present and reactivated, and realigns the
    chronological sequence indices in the database.
    """
    now_utc = get_rekordbox_timestamp()
    restored = False

    with engine.begin() as conn:
        tables_res = conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
        existing_tables = {r[0] for r in tables_res}

        if "djmdHistory" in existing_tables:
            cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdHistory);").fetchall()}
            if "rb_local_deleted" not in cols:
                return False

            row = conn.execute(
                text(f"SELECT ID, Name, DateCreated, ParentID{', rb_local_deleted' if 'rb_local_deleted' in cols else ''} FROM djmdHistory WHERE ID = :hid;"),
                {"hid": history_id},
            ).mappings().fetchone()
            if not row:
                return False

            # If already active, do not re-process (prevents double-increment)
            if "rb_local_deleted" in cols and row.get("rb_local_deleted") == 0:
                return True

            name = row.get("Name") or ""
            date_val = row.get("DateCreated") or ""
            existing_parent = row.get("ParentID")

            # 1. Collect soft-deleted tracks in this session to restore their play count
            tracks_to_restore: List[Tuple[str, int]] = []
            if "djmdSongHistory" in existing_tables:
                sh_cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdSongHistory);").fetchall()}
                sh_filter = "AND rb_local_deleted = 1" if "rb_local_deleted" in sh_cols else ""
                pt_rows = conn.execute(
                    text(f"""
                        SELECT ContentID, COUNT(*) AS play_count
                        FROM djmdSongHistory
                        WHERE HistoryID = :hid
                          {sh_filter}
                          AND ContentID IS NOT NULL
                        GROUP BY ContentID;
                    """),
                    {"hid": history_id},
                ).fetchall()
                tracks_to_restore = [(str(r[0]), int(r[1])) for r in pt_rows if r[0]]

            # 2. Restore the session row in djmdHistory
            set_parts = ["rb_local_deleted = 0"]
            if "rb_data_status" in cols:
                set_parts.append("rb_data_status = 0")
            if "updated_at" in cols:
                set_parts.append("updated_at = :now")

            # 3. Ensure Year and Month folders exist and are active
            y, m = extract_year_month_from_name_or_date(name, date_val)
            if y and m:
                date_str = date_val or f"{y:04d}-{m:02d}-01"
                target_month_id = ensure_djmd_history_folders(conn, y, m, date_str, cols)
                if "ParentID" in cols:
                    set_parts.append("ParentID = :month_id")
                params: Dict[str, Any] = {"hid": history_id, "now": now_utc, "month_id": target_month_id}
            else:
                params = {"hid": history_id, "now": now_utc}
                if existing_parent:
                    reactivate_parts = ["rb_local_deleted = 0"]
                    if "rb_data_status" in cols:
                        reactivate_parts.append("rb_data_status = 0")
                    if "updated_at" in cols:
                        reactivate_parts.append("updated_at = :now")
                    conn.execute(
                        text(f"UPDATE djmdHistory SET {', '.join(reactivate_parts)} WHERE ID = :pid;"),
                        {"pid": existing_parent, "now": now_utc},
                    )

            conn.execute(
                text(f"UPDATE djmdHistory SET {', '.join(set_parts)} WHERE ID = :hid;"),
                params,
            )

            # 4. Restore associated tracks in djmdSongHistory
            if "djmdSongHistory" in existing_tables:
                sh_cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdSongHistory);").fetchall()}
                if "rb_local_deleted" in sh_cols:
                    sh_set = ["rb_local_deleted = 0"]
                    if "rb_data_status" in sh_cols:
                        sh_set.append("rb_data_status = 0")
                    if "updated_at" in sh_cols:
                        sh_set.append("updated_at = :now")
                    conn.execute(
                        text(f"UPDATE djmdSongHistory SET {', '.join(sh_set)} WHERE HistoryID = :hid;"),
                        {"hid": history_id, "now": now_utc},
                    )

            # 5. Re-increment play count in djmdContent by the number of times played
            if "djmdContent" in existing_tables and tracks_to_restore:
                c_cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdContent);").fetchall()}
                play_col = "DJPlayCount" if "DJPlayCount" in c_cols else ("PlayCount" if "PlayCount" in c_cols else None)
                if play_col:
                    has_c_updated = "updated_at" in c_cols
                    c_updates = [f"{play_col} = COALESCE({play_col}, 0) + :play_cnt"]
                    if has_c_updated:
                        c_updates.append("updated_at = :now")
                    update_c_sql = f"UPDATE djmdContent SET {', '.join(c_updates)} WHERE ID = :cid;"
                    for cid, cnt in tracks_to_restore:
                        params = {"play_cnt": cnt, "cid": cid}
                        if has_c_updated:
                            params["now"] = now_utc
                        conn.execute(text(update_c_sql), params)

            restored = True

        elif "history" in existing_tables:
            cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(history);").fetchall()}
            if "rb_local_deleted" not in cols:
                return False

            h_row = conn.execute(
                text(f"SELECT history_id{', rb_local_deleted' if 'rb_local_deleted' in cols else ''} FROM history WHERE history_id = :hid;"),
                {"hid": history_id},
            ).mappings().fetchone()
            if not h_row:
                return False

            if "rb_local_deleted" in cols and h_row.get("rb_local_deleted") == 0:
                return True

            usb_tracks_to_restore: List[Tuple[str, int]] = []
            if "songHistory" in existing_tables:
                sh_cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(songHistory);").fetchall()}
                sh_filter = "AND rb_local_deleted = 1" if "rb_local_deleted" in sh_cols else ""
                pt_rows = conn.execute(
                    text(f"""
                        SELECT content_id, COUNT(*) AS play_count
                        FROM songHistory
                        WHERE history_id = :hid
                          {sh_filter}
                          AND content_id IS NOT NULL
                        GROUP BY content_id;
                    """),
                    {"hid": history_id},
                ).fetchall()
                usb_tracks_to_restore = [(str(r[0]), int(r[1])) for r in pt_rows if r[0]]

            if "rb_local_deleted" in cols:
                conn.execute(
                    text("UPDATE history SET rb_local_deleted = 0 WHERE history_id = :hid;"),
                    {"hid": history_id},
                )
            if "songHistory" in existing_tables:
                sh_cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(songHistory);").fetchall()}
                if "rb_local_deleted" in sh_cols:
                    conn.execute(
                        text("UPDATE songHistory SET rb_local_deleted = 0 WHERE history_id = :hid;"),
                        {"hid": history_id},
                    )

            if "content" in existing_tables and usb_tracks_to_restore:
                c_cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(content);").fetchall()}
                play_col = "DJPlayCount" if "DJPlayCount" in c_cols else ("PlayCount" if "PlayCount" in c_cols else None)
                id_col = "ID" if "ID" in c_cols else ("content_id" if "content_id" in c_cols else None)
                if play_col and id_col:
                    has_c_updated = "updated_at" in c_cols
                    c_updates = [f"{play_col} = COALESCE({play_col}, 0) + :play_cnt"]
                    if has_c_updated:
                        c_updates.append("updated_at = :now")
                    update_c_sql = f"UPDATE content SET {', '.join(c_updates)} WHERE {id_col} = :cid;"
                    for cid, cnt in usb_tracks_to_restore:
                        params = {"play_cnt": cnt, "cid": cid}
                        if has_c_updated:
                            params["now"] = now_utc
                        conn.execute(text(update_c_sql), params)

            restored = True

    if restored:
        try:
            realign_history_order(engine)
        except Exception:
            pass

    return restored


def fetch_session_tracks(engine: Engine, history_id: str) -> Dict[str, Any]:
    """Fetch session metadata, track list, and aggregate statistics for a history session."""
    session_info: Dict[str, Any] = {
        "id": history_id,
        "name": "",
        "date": "",
        "parent_id": None,
        "is_deleted": False,
    }
    tracks: List[Dict[str, Any]] = []

    with engine.connect() as conn:
        tables_res = conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
        existing_tables = {r[0] for r in tables_res}

        # 1. Fetch Session Info
        is_session_deleted = False
        if "djmdHistory" in existing_tables:
            cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdHistory);").fetchall()}
            has_parent = "ParentID" in cols
            has_del = "rb_local_deleted" in cols
            select_parent = ", ParentID" if has_parent else ""
            select_del = ", rb_local_deleted" if has_del else ""
            h_row = conn.execute(
                text(f"SELECT ID, Name, DateCreated{select_parent}{select_del} FROM djmdHistory WHERE ID = :hid;"),
                {"hid": history_id},
            ).mappings().fetchone()
            if h_row:
                session_info["id"] = str(h_row["ID"])
                session_info["name"] = h_row["Name"] or "(Untitled)"
                session_info["date"] = h_row["DateCreated"] or ""
                if has_parent and h_row.get("ParentID") is not None:
                    session_info["parent_id"] = str(h_row["ParentID"])
                if has_del and h_row.get("rb_local_deleted") == 1:
                    is_session_deleted = True
                session_info["is_deleted"] = is_session_deleted
        elif "history" in existing_tables:
            h_cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(history);").fetchall()}
            has_del = "rb_local_deleted" in h_cols
            select_del = ", rb_local_deleted" if has_del else ""
            h_row = conn.execute(
                text(f"SELECT history_id, name{select_del} FROM history WHERE history_id = :hid;"),
                {"hid": history_id},
            ).mappings().fetchone()
            if h_row:
                session_info["id"] = str(h_row["history_id"])
                session_info["name"] = h_row["name"] or "(Untitled)"
                if has_del and h_row.get("rb_local_deleted") == 1:
                    is_session_deleted = True
                session_info["is_deleted"] = is_session_deleted

        # 2. Fetch Tracks
        if "djmdSongHistory" in existing_tables and "djmdContent" in existing_tables:
            sh_cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdSongHistory);").fetchall()}
            c_cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdContent);").fetchall()}

            # If the session itself is deleted, do not filter out soft-deleted songs
            if is_session_deleted:
                sh_del = ""
            else:
                sh_del = "AND (sh.rb_local_deleted = 0 OR sh.rb_local_deleted IS NULL)" if "rb_local_deleted" in sh_cols else ""
            c_del = "AND (c.rb_local_deleted = 0 OR c.rb_local_deleted IS NULL)" if "rb_local_deleted" in c_cols else ""

            # Check optional join tables
            join_artist = "LEFT JOIN djmdArtist a ON c.ArtistID = a.ID" if "djmdArtist" in existing_tables else ""
            artist_name_expr = "COALESCE(a.Name, c.SrcArtistName, 'Unknown Artist')" if "djmdArtist" in existing_tables else "COALESCE(c.SrcArtistName, 'Unknown Artist')"

            join_album = "LEFT JOIN djmdAlbum al ON c.AlbumID = al.ID" if "djmdAlbum" in existing_tables else ""
            album_expr = "al.Name" if "djmdAlbum" in existing_tables else "NULL"

            join_genre = "LEFT JOIN djmdGenre g ON c.GenreID = g.ID" if "djmdGenre" in existing_tables else ""
            genre_expr = "g.Name" if "djmdGenre" in existing_tables else "NULL"

            join_key = "LEFT JOIN djmdKey k ON c.KeyID = k.ID" if "djmdKey" in existing_tables else ""
            key_expr = "k.ScaleName" if "djmdKey" in existing_tables else "NULL"

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

            file_type_expr = "c.FileType" if "FileType" in c_cols else "NULL AS FileType"

            query = f"""
                SELECT 
                    sh.TrackNo,
                    sh.created_at AS PlayedAt,
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
                FROM djmdSongHistory sh
                JOIN djmdContent c ON sh.ContentID = c.ID
                {join_artist}
                {join_album}
                {join_genre}
                {join_key}
                WHERE sh.HistoryID = :hid
                  {sh_del}
                  {c_del}
                ORDER BY sh.TrackNo ASC;
            """
            rows = conn.execute(text(query), {"hid": history_id}).mappings().fetchall()
            for r in rows:
                tracks.append(
                    build_track_record(
                        r,
                        extra={
                            "track_no": int(r.get("TrackNo") or 0),
                            "played_at": str(r.get("PlayedAt") or ""),
                        },
                    )
                )

    # 3. Compute Aggregate Stats
    total_tracks = len(tracks)
    total_duration_sec = sum(t["duration_sec"] for t in tracks)
    if total_duration_sec >= 3600:
        hrs = total_duration_sec // 3600
        mins = (total_duration_sec % 3600) // 60
        total_duration_str = f"{hrs}h {mins:02d}m"
    elif total_duration_sec > 0:
        mins = total_duration_sec // 60
        secs = total_duration_sec % 60
        total_duration_str = f"{mins}m {secs:02d}s"
    else:
        total_duration_str = "0m"

    bpms = [t["bpm"] for t in tracks if t["bpm"] > 0]
    if bpms:
        bpm_min = round(min(bpms), 1)
        bpm_max = round(max(bpms), 1)
        bpm_avg = round(sum(bpms) / len(bpms), 1)
    else:
        bpm_min, bpm_max, bpm_avg = 0.0, 0.0, 0.0

    genre_counts: Dict[str, int] = {}
    key_counts: Dict[str, int] = {}
    for t in tracks:
        if t["genre"]:
            genre_counts[t["genre"]] = genre_counts.get(t["genre"], 0) + 1
        if t["key"]:
            key_counts[t["key"]] = key_counts.get(t["key"], 0) + 1

    top_genres = sorted(genre_counts.items(), key=lambda x: x[1], reverse=True)[:3]
    top_keys = sorted(key_counts.items(), key=lambda x: x[1], reverse=True)[:5]

    stats = {
        "track_count": total_tracks,
        "total_duration_sec": total_duration_sec,
        "total_duration_str": total_duration_str,
        "bpm_min": bpm_min,
        "bpm_max": bpm_max,
        "bpm_avg": bpm_avg,
        "top_genres": top_genres,
        "top_keys": top_keys,
    }

    return {
        "history_id": history_id,
        "session": session_info,
        "tracks": tracks,
        "stats": stats,
    }


def fetch_track_history_plays(engine: Engine, content_id: str) -> List[Dict[str, Any]]:
    """Fetch all history sessions in which a specific track was played.

    Queries djmdSongHistory joined with djmdHistory (or generic history table),
    returning chronological records with track set order (TrackNo), playback timestamp,
    session name, parsed date, location (@ Location), and co-DJs (w Artists).
    """
    history_plays: List[Dict[str, Any]] = []
    if not engine or not content_id:
        return history_plays

    with engine.connect() as conn:
        tables_res = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table';"
        ).fetchall()
        existing_tables = {r[0] for r in tables_res}

        if "djmdSongHistory" in existing_tables and "djmdHistory" in existing_tables:
            sh_cols = {
                r[1]
                for r in conn.exec_driver_sql("PRAGMA table_info(djmdSongHistory);").fetchall()
            }
            h_cols = {
                r[1]
                for r in conn.exec_driver_sql("PRAGMA table_info(djmdHistory);").fetchall()
            }

            sh_del = (
                "AND (sh.rb_local_deleted = 0 OR sh.rb_local_deleted IS NULL)"
                if "rb_local_deleted" in sh_cols
                else ""
            )
            h_del = (
                "AND (h.rb_local_deleted = 0 OR h.rb_local_deleted IS NULL)"
                if "rb_local_deleted" in h_cols
                else ""
            )

            query = f"""
                SELECT 
                    sh.ID AS song_history_id,
                    sh.HistoryID AS history_id,
                    sh.TrackNo AS track_no,
                    sh.created_at AS played_at,
                    h.Name AS history_name,
                    h.DateCreated AS session_date,
                    h.ParentID AS parent_id
                FROM djmdSongHistory sh
                LEFT JOIN djmdHistory h ON sh.HistoryID = h.ID
                WHERE sh.ContentID = :content_id
                  {sh_del}
                  {h_del}
                ORDER BY COALESCE(h.DateCreated, sh.created_at) DESC, sh.created_at DESC;
            """
            rows = (
                conn.execute(text(query), {"content_id": str(content_id)})
                .mappings()
                .fetchall()
            )
            for r in rows:
                raw_name = r.get("history_name") or "(Untitled Session)"
                session_title, location, played_with = parse_history_name(raw_name)
                session_date = r.get("session_date") or ""
                played_at = str(r.get("played_at") or "")

                time_display = ""
                if played_at:
                    parts = played_at.split()
                    if len(parts) >= 2:
                        time_display = parts[1].split(".")[0]
                    else:
                        time_display = played_at

                history_plays.append({
                    "song_history_id": str(r.get("song_history_id") or ""),
                    "history_id": str(r.get("history_id") or ""),
                    "track_no": int(r.get("track_no") or 0),
                    "played_at": played_at,
                    "played_time": time_display,
                    "history_name": raw_name,
                    "session_title": session_title,
                    "session_date": session_date,
                    "location": location,
                    "played_with": played_with,
                    "parent_id": (
                        str(r.get("parent_id") or "")
                        if r.get("parent_id") is not None
                        else None
                    ),
                })
        elif "history_songs" in existing_tables and "history" in existing_tables:
            try:
                query = """
                    SELECT hs.history_id, hs.track_no, h.name, h.date
                    FROM history_songs hs
                    JOIN history h ON hs.history_id = h.history_id
                    WHERE hs.content_id = :content_id
                    ORDER BY h.date DESC;
                """
                rows = (
                    conn.execute(text(query), {"content_id": str(content_id)})
                    .mappings()
                    .fetchall()
                )
                for r in rows:
                    raw_name = r.get("name") or "(Untitled Session)"
                    session_title, location, played_with = parse_history_name(raw_name)
                    history_plays.append({
                        "song_history_id": "",
                        "history_id": str(r.get("history_id") or ""),
                        "track_no": int(r.get("track_no") or 0),
                        "played_at": str(r.get("date") or ""),
                        "played_time": "",
                        "history_name": raw_name,
                        "session_title": session_title,
                        "session_date": str(r.get("date") or ""),
                        "location": location,
                        "played_with": played_with,
                        "parent_id": None,
                    })
            except Exception:
                pass

    return history_plays


def update_history_record(
    engine: Engine,
    record: Dict[str, Any],
    new_name: str,
    new_date: Optional[str] = None,
) -> Optional[str]:
    """Update a history entry in place in the database.

    For djmdHistory, ensures target Year/Month folders exist and automatically
    updates ParentID to the correct month folder.
    Returns the target ParentID if updated, or None.
    """
    target_parent_id: Optional[str] = None
    now_utc = get_rekordbox_timestamp()

    with engine.begin() as conn:
        if record["table"] == "djmdHistory":
            cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdHistory);").fetchall()}

            curr_date = new_date if new_date is not None else record.get("date")
            y, m = extract_year_month_from_name_or_date(new_name, curr_date)

            date_for_folder = curr_date or (f"{y:04d}-{m:02d}-01" if y and m else None)
            if y and m and "ParentID" in cols:
                target_parent_id = ensure_djmd_history_folders(conn, y, m, date_for_folder, cols)

            set_clauses = ["Name = :name"]
            params: Dict[str, Any] = {"name": new_name, "id": record["id"]}

            if new_date is not None:
                set_clauses.append("DateCreated = :date")
                params["date"] = new_date

            if target_parent_id and "ParentID" in cols:
                set_clauses.append("ParentID = :parent_id")
                params["parent_id"] = target_parent_id

            if "updated_at" in cols:
                set_clauses.append("updated_at = :updated_at")
                params["updated_at"] = now_utc

            sql = f"UPDATE djmdHistory SET {', '.join(set_clauses)} WHERE ID = :id"
            conn.execute(text(sql), params)

        elif record["table"] == "history":
            conn.execute(
                text("UPDATE history SET name = :name WHERE history_id = :id"),
                {"name": new_name, "id": int(record["id"])},
            )

    if record["table"] == "djmdHistory":
        realign_history_order(engine)

    return target_parent_id


def batch_update_history_records(
    engine: Engine,
    updates: List[Dict[str, Any]],
    auto_realign: bool = True,
) -> Dict[str, Any]:
    """Batch update multiple history entries in a single atomic transaction.

    Each item in `updates` is a dict with:
      - 'record': Dict with 'id', 'table', 'date', etc.
      - 'new_name': str
      - 'new_date': Optional[str] (optional)

    Returns dict with stats: {'updated_count': int, 'realign_stats': Dict[str, int]}.
    """
    stats = {"updated_count": 0, "realign_stats": {}}
    if not updates:
        return stats

    now_utc = get_rekordbox_timestamp()
    has_djmd = False

    with engine.begin() as conn:
        for item in updates:
            record = item["record"]
            new_name = item["new_name"]
            new_date = item.get("new_date")
            table = record.get("table", "djmdHistory")

            if table == "djmdHistory":
                has_djmd = True
                cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdHistory);").fetchall()}

                curr_date = new_date if new_date is not None else record.get("date")
                y, m = extract_year_month_from_name_or_date(new_name, curr_date)

                target_parent_id = None
                date_for_folder = curr_date or (f"{y:04d}-{m:02d}-01" if y and m else None)
                if y and m and "ParentID" in cols:
                    target_parent_id = ensure_djmd_history_folders(conn, y, m, date_for_folder, cols)

                set_clauses = ["Name = :name"]
                params: Dict[str, Any] = {"name": new_name, "id": record["id"]}

                if new_date is not None:
                    set_clauses.append("DateCreated = :date")
                    params["date"] = new_date

                if target_parent_id and "ParentID" in cols:
                    set_clauses.append("ParentID = :parent_id")
                    params["parent_id"] = target_parent_id

                if "updated_at" in cols:
                    set_clauses.append("updated_at = :updated_at")
                    params["updated_at"] = now_utc

                sql = f"UPDATE djmdHistory SET {', '.join(set_clauses)} WHERE ID = :id"
                conn.execute(text(sql), params)
                stats["updated_count"] += 1

            elif table == "history":
                conn.execute(
                    text("UPDATE history SET name = :name WHERE history_id = :id"),
                    {"name": new_name, "id": int(record["id"])},
                )
                stats["updated_count"] += 1

    if has_djmd and auto_realign:
        stats["realign_stats"] = realign_history_order(engine)

    return stats


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Interactive Rekordbox history renamer for master.db and USB exportLibrary.db.",
    )
    parser.add_argument(
        "db_path",
        nargs="?",
        default=None,
        help="Path to Rekordbox database (e.g. master.db or exportLibrary.db).",
    )
    parser.add_argument(
        "-d",
        "--db-path",
        dest="db_path_flag",
        default=None,
        help="Path to Rekordbox database file.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Proceed even if Rekordbox is currently running (not recommended).",
    )
    parser.add_argument(
        "--sort",
        "--realign",
        "--realign-order",
        action="store_true",
        dest="realign",
        help="Sort and realign chronological order of months and history sessions without opening interactive selector.",
    )
    return parser.parse_args(argv)






def run_interactive_session(engine: Engine, table_format: str, db_path: str) -> None:
    """Run interactive history selector and editor."""
    while True:
        records = fetch_history_records(engine, table_format)

        if not records:
            print(f"ℹ️  No history sessions found in '{os.path.basename(db_path)}'.")

            # If user opened an exportLibrary.db with 0 histories, offer master.db if available
            master_path = get_default_master_db_path()
            if master_path and os.path.abspath(master_path) != os.path.abspath(db_path):
                switch = questionary.confirm(
                    f"Would you like to open your local Rekordbox master.db instead?\n({master_path})",
                    default=True,
                ).ask()
                if switch:
                    try:
                        new_engine, new_format = open_database(master_path)
                        engine = new_engine
                        table_format = new_format
                        db_path = master_path
                        print(f"\nOpened master.db: {master_path}")
                        continue
                    except Exception as e:
                        print(f"❌ Could not open master.db: {e}")
            return

        choices = []
        for r in records:
            date_str = f" [{r['date']}]" if r["date"] else ""
            ey, em = extract_year_month_from_name_or_date(r["name"], r["date"])
            expected_folder = f"{ey}{em:02d}" if ey and em else None
            mismatch = (
                r.get("parent_id") is not None
                and expected_folder is not None
                and r["parent_id"] != expected_folder
            )
            if mismatch:
                title = f"⚠️  {r['name']}{date_str} (ID: {r['id']}) [Folder: {r['parent_id']} ➔ {expected_folder}]"
            else:
                title = f"{r['name']}{date_str} (ID: {r['id']})"
            choices.append(questionary.Choice(title=title, value=r["id"]))

        choices.append(questionary.Separator())
        if table_format == "djmdHistory":
            choices.append(
                questionary.Choice(
                    title="🔄 Realign & sort all history folders & sessions (months & days)",
                    value="__REALIGN__",
                )
            )
        choices.append(questionary.Choice(title="✕ Cancel / Exit", value="__CANCEL__"))

        print("\n\n")  # Spacing to ensure prompt clears IDE terminal hover/sticky header
        selected_id = questionary.select(
            "Select history to edit (use ↑/↓ arrow keys, Enter to confirm):",
            choices=choices,
            use_indicator=True,
        ).ask()

        if selected_id is None or selected_id == "__CANCEL__":
            print("Exiting.")
            return

        if selected_id == "__REALIGN__":
            print("\nRealigning history order in database...")
            stats = realign_history_order(engine)
            print(
                f"✅ Realignment complete! ({stats['updated_rows']} items updated across {stats['parents_processed']} folders)\n"
            )
            continue

        selected_rec = next((r for r in records if r["id"] == selected_id), None)
        if not selected_rec:
            print(f"❌ Record ID {selected_id} not found.")
            return

        current_name = selected_rec["name"]
        current_date = selected_rec["date"]

        # Parse current name into base, location (@), and artist (w)
        current_base, current_loc, current_art = parse_history_name(current_name)

        # 1. Base name prompt
        new_base = questionary.text(
            "Edit history Name (base):",
            default=current_base,
        ).ask()
        if new_base is None:
            print("Cancelled.")
            return

        # 2. Location prompt (@)
        new_loc = questionary.text(
            "Edit Location (@, optional):",
            default=current_loc,
        ).ask()
        if new_loc is None:
            print("Cancelled.")
            return

        # 3. Artist prompt (w)
        new_art = questionary.text(
            "Edit Artist(s) (w, optional):",
            default=current_art,
        ).ask()
        if new_art is None:
            print("Cancelled.")
            return

        # If user cleared base name but kept other parts, fallback safely
        if not new_base.strip() and not new_loc.strip() and not new_art.strip():
            print("❌ History name cannot be empty.")
            return
        if not new_base.strip():
            new_base = current_base or "(Untitled)"

        new_name = compose_history_name(new_base, new_loc, new_art)

        # Check if modified or if parent folder needs realignment
        changed = False
        if new_name != current_name:
            changed = True

        target_y, target_m = extract_year_month_from_name_or_date(new_name, current_date)
        expected_parent = f"{target_y}{target_m:02d}" if target_y and target_m else None
        if (
            selected_rec["table"] == "djmdHistory"
            and selected_rec.get("parent_id")
            and expected_parent
            and selected_rec["parent_id"] != expected_parent
        ):
            changed = True

        if not changed:
            print("ℹ️  No changes made.")
        else:
            try:
                new_parent_id = update_history_record(engine, selected_rec, new_name)
                print(f"\n✅ Successfully updated history (ID: {selected_id})!")
                if current_name != new_name:
                    print(f"   Name: '{current_name}' ➔ '{new_name}'")
                old_parent = selected_rec.get("parent_id")
                if new_parent_id and old_parent and new_parent_id != old_parent:
                    print(f"   Folder: '{old_parent}' ➔ '{new_parent_id}' (tree hierarchy updated)")
                elif new_parent_id and not old_parent:
                    print(f"   Folder: '{new_parent_id}'")
                print("   Tree & session chronological order aligned.")
            except Exception as e:
                print(f"\n❌ Failed to save changes: {e}")
                return

        print()
        again = questionary.confirm(
            "Would you like to edit another history session?",
            default=False,
        ).ask()

        if not again:
            print("Done! All changes saved.")
            break


def main(argv: Optional[List[str]] = None) -> None:
    """Main CLI entrypoint."""
    args = parse_args(argv)
    ensure_rekordbox_not_running(force=getattr(args, "force", False))
    db_path = resolve_db_path(args)

    print(f"Opening Rekordbox database: {db_path} ...")
    try:
        engine, table_format = open_database(db_path)
    except Exception as e:
        print(f"❌ Error opening database: {e}")
        sys.exit(1)

    if getattr(args, "realign", False):
        if table_format == "djmdHistory":
            print(f"Realigning history order in '{os.path.basename(db_path)}'...")
            stats = realign_history_order(engine)
            print(
                f"✅ Success! Aligned {stats['updated_rows']} entries across {stats['parents_processed']} folders."
            )
        else:
            print("ℹ️  Realigning Seq is only supported for databases with djmdHistory.")
        return

    try:
        run_interactive_session(engine, table_format, db_path)
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
    finally:
        try:
            engine.dispose()
        except Exception:
            pass


if __name__ == "__main__":
    # Ensure package can be imported when running script directly by path
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    main()
