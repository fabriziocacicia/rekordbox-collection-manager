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

from rekordbox_collection_fixer.utils import (
    DEVICE_LIBRARY_PLUS_KEY,
    ensure_rekordbox_not_running,
    get_default_master_db_path,
    is_rekordbox_running,
    open_database,
    resolve_db_path as utils_resolve_db_path,
)



def parse_year_month(date_str: Optional[str]) -> Tuple[Optional[int], Optional[int]]:
    """Extract (year, month) tuple from a date string (e.g. '2026-08-31 23:00:00')."""
    if not date_str:
        return None, None
    m = re.search(r"(\d{4})[-/.]?(\d{2})", date_str.strip())
    if m:
        try:
            y = int(m.group(1))
            mon = int(m.group(2))
            if 1900 <= y <= 2100 and 1 <= mon <= 12:
                return y, mon
        except ValueError:
            pass
    return None, None


def extract_date_from_name(name: Optional[str]) -> Optional[str]:
    """Extract YYYY-MM-DD date string from history name if present."""
    if not name:
        return None
    m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", name.strip())
    if m:
        try:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 1900 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31:
                return f"{y:04d}-{mo:02d}-{d:02d}"
        except ValueError:
            pass
    return None


def extract_year_month_from_name_or_date(
    name: Optional[str],
    date_str: Optional[str] = None,
) -> Tuple[Optional[int], Optional[int]]:
    """Extract (year, month) tuple prioritizing date in Name over DateCreated string."""
    date_in_name = extract_date_from_name(name)
    if date_in_name:
        y, m = parse_year_month(date_in_name)
        if y and m:
            return y, m
    if date_str:
        y, m = parse_year_month(date_str)
        if y and m:
            return y, m
    return None, None



def normalize_date_input(new_date: str, current_date: Optional[str] = None) -> str:
    """Normalize user date input: if user inputs only YYYY-MM-DD and current_date had a time part, preserve it."""
    cleaned = new_date.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", cleaned):
        if current_date and " " in current_date.strip():
            time_part = current_date.strip().split(" ", 1)[1]
            if time_part:
                return f"{cleaned} {time_part}"
    return cleaned


MONTH_NAME_TO_INT = {
    # English
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
    # Italian
    "gennaio": 1,
    "gen": 1,
    "febbraio": 2,
    "febbr": 2,
    "marzo": 3,
    "aprile": 4,
    "apr": 4,
    "maggio": 5,
    "mag": 5,
    "giugno": 6,
    "giu": 6,
    "luglio": 7,
    "lug": 7,
    "agosto": 8,
    "ago": 8,
    "settembre": 9,
    "set": 9,
    "ottobre": 10,
    "ott": 10,
    "novembre": 11,
    "dicembre": 12,
    "dic": 12,
}


def parse_month_number(
    name: Optional[str],
    id_val: Optional[str] = None,
    date_created: Optional[str] = None,
) -> int:
    """Extract chronological month number (1..12) from month folder name, ID, or DateCreated."""
    # 1. Check ID (e.g. '202608' -> 8, '202612' -> 12)
    if id_val and re.match(r"^\d{6}$", str(id_val).strip()):
        try:
            m = int(str(id_val).strip()[4:6])
            if 1 <= m <= 12:
                return m
        except ValueError:
            pass

    # 2. Check Name if integer (e.g. '8', '08', '12')
    cleaned_name = (name or "").strip().lower()
    if cleaned_name.isdigit():
        try:
            m = int(cleaned_name)
            if 1 <= m <= 12:
                return m
        except ValueError:
            pass

    # 3. Check Name against English / Italian month names
    if cleaned_name in MONTH_NAME_TO_INT:
        return MONTH_NAME_TO_INT[cleaned_name]

    # 4. Check DateCreated if available
    if date_created:
        _, m = parse_year_month(date_created)
        if m:
            return m

    # 5. Check if Name contains a 4-digit year + 2-digit month
    _, m = parse_year_month(cleaned_name)
    if m:
        return m

    return 99


def natural_sort_key(text: Optional[str]) -> List[Any]:
    """Sort strings containing numbers in natural human order.

    e.g. 'HISTORY 2026-08-01 (2)' -> ['history ', 2026, '-', 8, '-', 1, ' (', 2, ')']
    """
    if not text:
        return []
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", str(text))]


def history_session_sort_key(row_dict: Dict[str, Any]) -> Tuple:
    """Sort key for history sessions within a month folder.

    Orders chronologically: day 01 at top, day 31 at bottom.
    Extracts date from Name if formatted as YYYY-MM-DD (or YYYY/MM/DD),
    falling back to DateCreated, then uses natural_sort_key for tie-breaking.
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

    return (extracted_date, natural_sort_key(name), date_created, id_val)


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
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " +00:00"

    with engine.begin() as conn:
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdHistory);").fetchall()}
        if "Seq" not in cols or "ParentID" not in cols:
            return stats

        has_updated_at = "updated_at" in cols

        # 1. Realign any history sessions whose ParentID does not match the date in their Name
        if "ParentID" in cols and "Name" in cols:
            session_rows = conn.execute(
                text("SELECT ID, Name, ParentID, DateCreated FROM djmdHistory WHERE Attribute = 0 OR Attribute IS NULL;")
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

                        # Sync DateCreated date part if Name contains a specific date
                        name_date = extract_date_from_name(s_name)
                        time_part = s_date.strip().split(" ", 1)[1] if " " in s_date.strip() else ""
                        new_s_date = f"{name_date} {time_part}".strip() if name_date else s_date

                        update_fields = ["ParentID = :parent_id"]
                        params: Dict[str, Any] = {"parent_id": expected_parent, "id": s["ID"]}
                        if new_s_date and new_s_date != s_date:
                            update_fields.append("DateCreated = :date_created")
                            params["date_created"] = new_s_date
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
            "SELECT DISTINCT ParentID FROM djmdHistory WHERE ParentID IS NOT NULL;"
        ).fetchall()
        parent_ids = [r[0] for r in parent_rows if r[0] is not None]

        for parent_id in parent_ids:
            query = """
                SELECT ID, Name, Attribute, ParentID, DateCreated, Seq
                FROM djmdHistory
                WHERE ParentID = :parent_id
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
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " +00:00"
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

    return month_id


def fetch_history_records(engine: Engine, table_format: str) -> List[Dict[str, Any]]:
    """Fetch history items from either djmdHistory or history table."""
    records = []
    with engine.connect() as conn:
        if table_format == "djmdHistory":
            cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdHistory);").fetchall()}
            has_parent = "ParentID" in cols
            select_parent = ", ParentID" if has_parent else ""
            query = f"""
                SELECT ID, Name, DateCreated, Attribute{select_parent}
                FROM djmdHistory 
                WHERE Attribute = 0 OR Attribute IS NULL
                ORDER BY rowid DESC;
            """
            rows = conn.exec_driver_sql(query).fetchall()
            for r in rows:
                records.append({
                    "id": str(r[0]),
                    "name": r[1] or "(Untitled)",
                    "date": r[2] or "",
                    "parent_id": str(r[4]) if has_parent and len(r) > 4 and r[4] is not None else None,
                    "table": "djmdHistory",
                })
        elif table_format == "history":
            query = """
                SELECT history_id, name, attribute 
                FROM history 
                WHERE attribute = 0 OR attribute IS NULL
                ORDER BY history_id DESC;
            """
            rows = conn.exec_driver_sql(query).fetchall()
            for r in rows:
                records.append({
                    "id": str(r[0]),
                    "name": r[1] or "(Untitled)",
                    "date": "",
                    "parent_id": None,
                    "table": "history",
                })
    return records


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
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " +00:00"

    with engine.begin() as conn:
        if record["table"] == "djmdHistory":
            cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdHistory);").fetchall()}

            curr_date = new_date if new_date is not None else record.get("date")
            y, m = extract_year_month_from_name_or_date(new_name, curr_date)

            date_for_folder = curr_date or (f"{y:04d}-{m:02d}-01" if y and m else None)
            if y and m and "ParentID" in cols:
                target_parent_id = ensure_djmd_history_folders(conn, y, m, date_for_folder, cols)

            # Sync DateCreated date part with date in Name if user didn't explicitly override it
            final_date = new_date
            name_date = extract_date_from_name(new_name)
            if name_date:
                curr_d = new_date if new_date is not None else (record.get("date") or "")
                time_part = curr_d.strip().split(" ", 1)[1] if " " in curr_d.strip() else ""
                synced_date = f"{name_date} {time_part}".strip()
                old_record_date = record.get("date") or ""
                if new_date is None or (new_date == old_record_date and not new_date.startswith(name_date)):
                    final_date = synced_date

            if final_date is not None:
                set_clauses = ["Name = :name", "DateCreated = :date"]
                params: Dict[str, Any] = {"name": new_name, "date": final_date, "id": record["id"]}

                if target_parent_id and "ParentID" in cols:
                    set_clauses.append("ParentID = :parent_id")
                    params["parent_id"] = target_parent_id

                if "updated_at" in cols:
                    set_clauses.append("updated_at = :updated_at")
                    params["updated_at"] = now_utc

                sql = f"UPDATE djmdHistory SET {', '.join(set_clauses)} WHERE ID = :id"
                conn.execute(text(sql), params)
            else:
                set_clauses = ["Name = :name"]
                params = {"name": new_name, "id": record["id"]}

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


def resolve_db_path(args: argparse.Namespace) -> str:
    """Determine database path from args, environment, default master.db, or interactive input."""
    candidate = getattr(args, "db_path_flag", None) or getattr(args, "db_path", None)
    return utils_resolve_db_path(candidate)



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

        # Prefill name in terminal buffer for editing
        new_name = questionary.text(
            "Edit history Name:",
            default=current_name,
        ).ask()

        if new_name is None:
            print("Cancelled.")
            return

        # Prefill date if table supports date (djmdHistory)
        new_date = None
        if selected_rec["table"] == "djmdHistory":
            date_default = current_date
            name_date = extract_date_from_name(new_name)
            if name_date:
                time_part = current_date.strip().split(" ", 1)[1] if " " in current_date.strip() else ""
                date_default = f"{name_date} {time_part}".strip()

            user_date = questionary.text(
                "Edit history Date (DateCreated):",
                default=date_default,
            ).ask()
            if user_date is None:
                print("Cancelled.")
                return
            new_date = normalize_date_input(user_date, date_default)

        # Check if modified or if parent folder needs realignment
        changed = False
        if new_name != current_name:
            changed = True
        if new_date is not None and new_date != current_date:
            changed = True

        target_y, target_m = extract_year_month_from_name_or_date(new_name, new_date or current_date)
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
                new_parent_id = update_history_record(engine, selected_rec, new_name, new_date)
                print(f"\n✅ Successfully updated history (ID: {selected_id})!")
                if current_name != new_name:
                    print(f"   Name: '{current_name}' ➔ '{new_name}'")
                if new_date is not None and current_date != new_date:
                    print(f"   Date: '{current_date}' ➔ '{new_date}'")
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
