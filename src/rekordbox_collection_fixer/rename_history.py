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
    ensure_rekordbox_not_running,
    is_rekordbox_running,
)

DEVICE_LIBRARY_PLUS_KEY = "r8gddnr4k847830ar6cqzbkk0el6qytmb3trbbx805jm74vez64i5o8fnrqryqls"




def get_default_master_db_path() -> Optional[str]:
    """Get the path to the Rekordbox master.db on this system."""
    for ver in ("rekordbox7", "rekordbox6"):
        cfg = get_config(ver)
        if cfg and cfg.get("db_path"):
            p = str(cfg["db_path"])
            if os.path.exists(p):
                return p
    # Standard macOS fallback
    mac_fallback = os.path.expanduser("~/Library/Pioneer/rekordbox/master.db")
    if os.path.exists(mac_fallback):
        return mac_fallback
    return None


def open_database(db_path: str) -> Tuple[Engine, str]:
    """Open an encrypted (SQLCipher) or unencrypted SQLite Rekordbox database.

    Tries Device Library Plus key, Master.db key, and plaintext.
    Returns (engine, table_format) where table_format is 'djmdHistory' or 'history'.
    """
    master_key = deobfuscate(BLOB)

    # Determine probe order based on filename
    is_export = "export" in os.path.basename(db_path).lower()
    if is_export:
        candidate_keys = [DEVICE_LIBRARY_PLUS_KEY, master_key, None]
    else:
        candidate_keys = [master_key, DEVICE_LIBRARY_PLUS_KEY, None]

    for key in candidate_keys:
        try:
            if key:
                url = f"sqlite+pysqlcipher://:{key}@/{db_path}?"
                engine = create_engine(url, module=sqlcipher_driver)
            else:
                engine = create_engine(f"sqlite:///{db_path}")

            with engine.connect() as conn:
                res = conn.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table';"
                ).fetchall()
                table_names = {r[0] for r in res}

                if "djmdHistory" in table_names:
                    return engine, "djmdHistory"
                elif "history" in table_names:
                    return engine, "history"
                elif len(table_names) > 0:
                    # Generic table check: maybe custom schema
                    return engine, "unknown"
        except Exception:
            continue

    raise RuntimeError(
        f"Unable to decrypt or open database at '{db_path}'. "
        "File may be corrupted or encrypted with an unsupported key."
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


def normalize_date_input(new_date: str, current_date: Optional[str] = None) -> str:
    """Normalize user date input: if user inputs only YYYY-MM-DD and current_date had a time part, preserve it."""
    cleaned = new_date.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", cleaned):
        if current_date and " " in current_date.strip():
            time_part = current_date.strip().split(" ", 1)[1]
            if time_part:
                return f"{cleaned} {time_part}"
    return cleaned


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

            date_for_folder = new_date if new_date is not None else record.get("date")
            if date_for_folder and "ParentID" in cols:
                y, m = parse_year_month(date_for_folder)
                if y and m:
                    target_parent_id = ensure_djmd_history_folders(conn, y, m, date_for_folder, cols)

            if new_date is not None:
                set_clauses = ["Name = :name", "DateCreated = :date"]
                params: Dict[str, Any] = {"name": new_name, "date": new_date, "id": record["id"]}

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
    return parser.parse_args(argv)


def resolve_db_path(args: argparse.Namespace) -> str:
    """Determine database path from args, environment, default master.db, or interactive input."""
    candidate = args.db_path_flag or args.db_path

    if candidate:
        resolved = os.path.abspath(os.path.expanduser(candidate))
        if os.path.exists(resolved):
            return resolved
        print(f"⚠️  Specified database file not found: {resolved}")

    # If exportLibrary.db is in the current working directory, use it
    local_export = os.path.abspath("exportLibrary.db")
    if os.path.exists(local_export):
        return local_export

    # Try default Rekordbox master.db
    master_path = get_default_master_db_path()
    default_prompt_path = master_path or "exportLibrary.db"

    user_entered = questionary.path(
        "Enter path to Rekordbox database (master.db or exportLibrary.db):",
        default=default_prompt_path,
    ).ask()

    if not user_entered:
        print("No path provided. Exiting.")
        sys.exit(1)

    resolved = os.path.abspath(os.path.expanduser(user_entered))
    if not os.path.exists(resolved):
        print(f"❌ File not found: {resolved}")
        sys.exit(1)

    return resolved


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
            ey, em = parse_year_month(r["date"])
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
            user_date = questionary.text(
                "Edit history Date (DateCreated):",
                default=current_date,
            ).ask()
            if user_date is None:
                print("Cancelled.")
                return
            new_date = normalize_date_input(user_date, current_date)

        # Check if modified or if parent folder needs realignment
        changed = False
        if new_name != current_name:
            changed = True
        if new_date is not None and new_date != current_date:
            changed = True

        target_y, target_m = parse_year_month(new_date or current_date)
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
