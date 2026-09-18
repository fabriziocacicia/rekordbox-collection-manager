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
    get_rekordbox_timestamp,
    is_rekordbox_running,
    open_database,
    resolve_db_path,
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
    query: str,
    limit: int = 50,
    table_name: Optional[str] = None,
    sort_by: str = "title",
    sort_order: str = "asc",
) -> List[Dict[str, Any]]:
    """Search for tracks matching query string in Title, SearchStr, or Artist Name."""
    if table_name is None:
        table_name = detect_content_table(engine)

    query_str = query.strip()
    pattern = f"%{query_str}%"
    starts_with = f"{query_str}%"
    is_desc = str(sort_order).lower() == "desc"
    order_dir = "DESC" if is_desc else "ASC"

    records = []
    with engine.connect() as conn:
        cols = {
            r[1]
            for r in conn.exec_driver_sql(f"PRAGMA table_info({table_name});").fetchall()
        }

        res = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('djmdArtist', 'artist');"
        ).fetchall()
        artist_tables = {r[0] for r in res}
        artist_table = "djmdArtist" if "djmdArtist" in artist_tables else ("artist" if "artist" in artist_tables else None)

        if table_name == "djmdContent":
            join_clause = ""
            artist_col_expr = "c.SrcArtistName"
            if artist_table == "djmdArtist":
                join_clause = "LEFT JOIN djmdArtist a ON c.ArtistID = a.ID"
                artist_col_expr = "COALESCE(a.Name, c.SrcArtistName, 'Unknown Artist')"

            deleted_filter = "AND (c.rb_local_deleted = 0 OR c.rb_local_deleted IS NULL)" if "rb_local_deleted" in cols else ""

            if sort_by == "play_count":
                order_clause = f"c.DJPlayCount {order_dir}, c.Title ASC"
            elif sort_by == "artist":
                order_clause = f"{artist_col_expr} {order_dir}, c.Title ASC"
            elif sort_by == "location":
                order_clause = f"c.FileNameL {order_dir}, c.Title ASC"
            elif sort_by == "title" and is_desc:
                order_clause = "c.Title DESC"
            else:
                if query_str:
                    order_clause = """CASE 
                        WHEN lower(c.Title) = lower(:exact) THEN 1
                        WHEN lower(c.Title) LIKE lower(:starts_with) THEN 2
                        ELSE 3
                    END, c.Title ASC"""
                else:
                    order_clause = "c.Title ASC"

            sql = f"""
                SELECT 
                    c.ID, 
                    c.Title, 
                    {artist_col_expr} AS ArtistName, 
                    c.DJPlayCount,
                    c.FolderPath,
                    c.FileNameL
                FROM djmdContent c
                {join_clause}
                WHERE 1=1 {deleted_filter}
                  AND (
                      c.Title LIKE :pattern 
                      OR c.SearchStr LIKE :pattern 
                      {"OR a.Name LIKE :pattern" if artist_table == "djmdArtist" else ""}
                      OR c.SrcArtistName LIKE :pattern
                      OR c.FileNameL LIKE :pattern
                  )
                ORDER BY {order_clause}
                LIMIT :limit;
            """
            rows = conn.execute(
                text(sql),
                {
                    "pattern": pattern,
                    "exact": query_str,
                    "starts_with": starts_with,
                    "limit": limit,
                },
            ).fetchall()

            for r in rows:
                raw_count = r[3]
                try:
                    count_int = int(raw_count) if raw_count is not None else 0
                except (ValueError, TypeError):
                    count_int = 0

                records.append({
                    "id": str(r[0]),
                    "title": r[1] or "(Untitled)",
                    "artist": r[2] or "Unknown Artist",
                    "play_count": count_int,
                    "folder_path": r[4] or "",
                    "filename": r[5] or "",
                    "table": "djmdContent",
                })

        else:
            # Fallback for generic content table
            count_col = "DJPlayCount" if "DJPlayCount" in cols else ("PlayCount" if "PlayCount" in cols else None)
            count_expr = count_col if count_col else "0"

            if sort_by == "play_count":
                fallback_order = f"{count_expr} {order_dir}, Title ASC"
            elif sort_by == "title" and is_desc:
                fallback_order = "Title DESC"
            else:
                fallback_order = "Title ASC"

            sql = f"""
                SELECT ID, Title, {count_expr}
                FROM {table_name}
                WHERE Title LIKE :pattern
                ORDER BY {fallback_order}
                LIMIT :limit;
            """
            rows = conn.execute(
                text(sql),
                {"pattern": pattern, "limit": limit},
            ).fetchall()
            for r in rows:
                raw_count = r[2] if len(r) > 2 else 0
                try:
                    count_int = int(raw_count) if raw_count is not None else 0
                except (ValueError, TypeError):
                    count_int = 0
                records.append({
                    "id": str(r[0]),
                    "title": r[1] or "(Untitled)",
                    "artist": "Unknown Artist",
                    "play_count": count_int,
                    "folder_path": "",
                    "filename": "",
                    "table": table_name,
                })

    return records


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
