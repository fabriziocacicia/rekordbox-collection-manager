"""Interactive CLI tool to rename Rekordbox history sessions in master.db or exportLibrary.db."""

import argparse
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import questionary
from pyrekordbox.config import get_config
from pyrekordbox.db6.database import BLOB, get_rekordbox_pid
from pyrekordbox.utils import deobfuscate
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlcipher3 import dbapi2 as sqlcipher_driver

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


def fetch_history_records(engine: Engine, table_format: str) -> List[Dict[str, Any]]:
    """Fetch history items from either djmdHistory or history table."""
    records = []
    with engine.connect() as conn:
        if table_format == "djmdHistory":
            query = """
                SELECT ID, Name, DateCreated, Attribute 
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
                    "table": "history",
                })
    return records


def update_history_record(
    engine: Engine,
    record: Dict[str, Any],
    new_name: str,
    new_date: Optional[str] = None,
) -> None:
    """Update a history entry in place in the database."""
    with engine.begin() as conn:
        if record["table"] == "djmdHistory":
            if new_date is not None:
                conn.execute(
                    text(
                        "UPDATE djmdHistory SET Name = :name, DateCreated = :date WHERE ID = :id"
                    ),
                    {"name": new_name, "date": new_date, "id": record["id"]},
                )
            else:
                conn.execute(
                    text("UPDATE djmdHistory SET Name = :name WHERE ID = :id"),
                    {"name": new_name, "id": record["id"]},
                )
        elif record["table"] == "history":
            conn.execute(
                text("UPDATE history SET name = :name WHERE history_id = :id"),
                {"name": new_name, "id": int(record["id"])},
            )


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
    if get_rekordbox_pid():
        print("⚠️  [Notice] Rekordbox is currently running.")
        print("   If you encounter lock errors, please quit Rekordbox and run again.\n")

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
            new_date = questionary.text(
                "Edit history Date (DateCreated):",
                default=current_date,
            ).ask()
            if new_date is None:
                print("Cancelled.")
                return

        # Check if modified
        changed = False
        if new_name != current_name:
            changed = True
        if new_date is not None and new_date != current_date:
            changed = True

        if not changed:
            print("ℹ️  No changes made.")
        else:
            try:
                update_history_record(engine, selected_rec, new_name, new_date)
                print(f"\n✅ Successfully updated history (ID: {selected_id})!")
                if current_name != new_name:
                    print(f"   Name: '{current_name}' ➔ '{new_name}'")
                if new_date is not None and current_date != new_date:
                    print(f"   Date: '{current_date}' ➔ '{new_date}'")
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
