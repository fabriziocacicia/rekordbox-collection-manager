"""Shared utilities for Rekordbox collection fixer tools.

Organized into:
1. Process & System Utilities
2. Database & Path Resolution Utilities
3. Date, Text & Sorting Utilities
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

import questionary
from pyrekordbox.db6.database import get_rekordbox_pid
from sqlalchemy.engine import Engine


# ==============================================================================
# 1. Process & System Utilities
# ==============================================================================

def is_rekordbox_running() -> bool:
    """Check if Rekordbox application is currently running.

    Uses `pgrep -il rekordbox` matching scripts/backup_rekordbox_mac.sh logic,
    with psutil and pyrekordbox fallbacks for cross-platform robustness.
    """
    current_pid = os.getpid()

    # 1. Primary check matching backup_rekordbox_mac.sh (macOS / Linux)
    if shutil.which("pgrep"):
        try:
            res = subprocess.run(
                ["pgrep", "-il", "rekordbox"],
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                for line in res.stdout.strip().splitlines():
                    parts = line.strip().split(maxsplit=1)
                    if parts and parts[0].isdigit():
                        pid = int(parts[0])
                        pname = parts[1].lower() if len(parts) > 1 else ""
                        if pid == current_pid:
                            continue
                        if "rekordbox-collection" in pname:
                            continue
                        return True
        except Exception:
            pass

    # 2. psutil check (cross-platform fallback)
    try:
        import psutil

        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if proc.info["pid"] == current_pid:
                    continue
                pname = (proc.info.get("name") or "").lower()
                if "rekordbox" in pname and "rekordbox-collection" not in pname:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    except Exception:
        pass

    # 3. Fallback to pyrekordbox helper
    try:
        pid = get_rekordbox_pid()
        if pid and pid != current_pid:
            return True
    except Exception:
        pass

    return False


def ensure_rekordbox_not_running(force: bool = False, exit_on_running: bool = True) -> bool:
    """Ensure Rekordbox is not running before modifying collection or databases.

    If Rekordbox is running and `force` is False, prints a warning message and exits
    with status code 1 (when `exit_on_running` is True).

    Returns True if Rekordbox is running, False otherwise.
    """
    if is_rekordbox_running():
        if force:
            print("⚠️  [Warning] Rekordbox is currently running. Proceeding anyway due to --force.\n")
            return True

        print("⚠️  Rekordbox is still running! Please close it before proceeding (Cmd + Q).")
        if exit_on_running:
            sys.exit(1)
        return True

    return False


# ==============================================================================
# 2. Database & Path Resolution Utilities
# ==============================================================================

DEVICE_LIBRARY_PLUS_KEY = "r8gddnr4k847830ar6cqzbkk0el6qytmb3trbbx805jm74vez64i5o8fnrqryqls"


def get_rekordbox_timestamp() -> str:
    """Generate a UTC timestamp formatted for Rekordbox databases (e.g. '2026-09-15 13:48:00.123 +00:00')."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " +00:00"


def get_default_master_db_path() -> Optional[str]:
    """Get the path to the Rekordbox master.db on this system."""
    try:
        from pyrekordbox.config import get_config

        for ver in ("rekordbox7", "rekordbox6"):
            cfg = get_config(ver)
            if cfg and cfg.get("db_path"):
                p = str(cfg["db_path"])
                if os.path.exists(p):
                    return p
    except Exception:
        pass

    # Standard macOS fallback
    mac_fallback = os.path.expanduser("~/Library/Pioneer/rekordbox/master.db")
    if os.path.exists(mac_fallback):
        return mac_fallback
    return None


def open_database(db_path: str):
    """Open an encrypted (SQLCipher) or unencrypted SQLite Rekordbox database.

    Tries Device Library Plus key, Master.db key, and plaintext.
    Returns (engine, table_format) where table_format is 'djmdHistory', 'history', or 'unknown'.
    """
    from pyrekordbox.db6.database import BLOB
    from pyrekordbox.utils import deobfuscate
    from sqlalchemy import create_engine
    from sqlcipher3 import dbapi2 as sqlcipher_driver

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

                if "djmdHistory" in table_names or "djmdContent" in table_names:
                    return engine, "djmdHistory" if "djmdHistory" in table_names else "djmdContent"
                elif "history" in table_names or "content" in table_names:
                    return engine, "history" if "history" in table_names else "content"
                elif len(table_names) > 0:
                    return engine, "unknown"
        except Exception:
            continue

    raise RuntimeError(
        f"Unable to decrypt or open database at '{db_path}'. "
        "File may be corrupted or encrypted with an unsupported key."
    )


def detect_content_table(engine: Engine) -> str:
    """Detect whether database uses djmdContent or content table."""
    with engine.connect() as conn:
        res = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table';"
        ).fetchall()
        table_names = {r[0] for r in res}
        if "djmdContent" in table_names:
            return "djmdContent"
        elif "content" in table_names:
            return "content"
    return "djmdContent"


def detect_history_table(engine: Engine) -> str:
    """Detect whether database uses djmdHistory or history table."""
    with engine.connect() as conn:
        res = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table';"
        ).fetchall()
        table_names = {r[0] for r in res}
        if "djmdHistory" in table_names:
            return "djmdHistory"
        elif "history" in table_names:
            return "history"
    return "djmdHistory"


def resolve_db_path(
    candidate_or_args: Any = None,
    prompt_message: str = "Enter path to Rekordbox database (master.db or exportLibrary.db):",
    allow_prompt: bool = True,
) -> str:
    """Determine database path from candidate string, argparse.Namespace, local exportLibrary.db, default master.db, or prompt."""
    candidate: Optional[str] = None
    if isinstance(candidate_or_args, str):
        candidate = candidate_or_args
    elif candidate_or_args is not None:
        candidate = getattr(candidate_or_args, "db_path_flag", None) or getattr(candidate_or_args, "db_path", None)

    if candidate:
        resolved = os.path.abspath(os.path.expanduser(candidate))
        if os.path.exists(resolved):
            return resolved
        if not allow_prompt:
            raise FileNotFoundError(f"Specified database file not found: {resolved}")
        print(f"⚠️  Specified database file not found: {resolved}")

    # If exportLibrary.db is in the current working directory, use it
    local_export = os.path.abspath("exportLibrary.db")
    if os.path.exists(local_export):
        return local_export

    # Try default Rekordbox master.db
    master_path = get_default_master_db_path()
    if not allow_prompt:
        if master_path and os.path.exists(master_path):
            return master_path
        raise FileNotFoundError("Could not find default Rekordbox master.db. Please specify a path.")

    default_prompt_path = master_path or "exportLibrary.db"

    user_entered = questionary.path(
        prompt_message,
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


# ==============================================================================
# 3. Date, Text & Sorting Utilities
# ==============================================================================

MONTH_INT_TO_NAME: Dict[int, str] = {
    1: "January",
    2: "February",
    3: "March",
    4: "April",
    5: "May",
    6: "June",
    7: "July",
    8: "August",
    9: "September",
    10: "October",
    11: "November",
    12: "December",
}

MONTH_NAME_TO_INT: Dict[str, int] = {
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
    """Extract YYYY-MM-DD date string from history/track/file name if present."""
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


def parse_session_paren_number(name: Optional[str]) -> int:
    """Extract session repetition number from parentheses in a session name.

    Returns:
        - -1 if the name has no parenthesized number (e.g. 'HISTORY 2026-07-08' or 'HISTORY 2026-07-08 @ OZeta')
          so that entries without () sort first.
        - int(N) if the name contains (N), e.g. (0) -> 0, (1) -> 1, (2) -> 2.
    """
    if not name:
        return -1
    m = re.search(r"\(\s*(\d+)\s*\)", str(name))
    if m:
        return int(m.group(1))
    return -1


def clean_session_name(name: Optional[str]) -> str:
    """Remove parenthesized session repetition numbers like (1), (2) from session name.

    e.g. 'HISTORY 2026-07-08 (1) @ OZeta' -> 'HISTORY 2026-07-08 @ OZeta'
    """
    if not name:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"\s*\(\s*\d+\s*\)", " ", str(name))).strip()


def parse_history_name(name: Optional[str]) -> Tuple[str, str, str]:
    """Parse history session name into (base_name, location, artist).

    Recognizes the custom schema:
        <Base Name> [@ <Location>] [w <Artist(s)>]

    Examples:
        - "HISTORY 2026-07-24" -> ("HISTORY 2026-07-24", "", "")
        - "HISTORY 2026-07-24 (1)" -> ("HISTORY 2026-07-24 (1)", "", "")
        - "HISTORY 2026-07-24 @ Fabric" -> ("HISTORY 2026-07-24", "Fabric", "")
        - "HISTORY 2026-07-24 w Carl Cox" -> ("HISTORY 2026-07-24", "", "Carl Cox")
        - "HISTORY 2026-07-24 @ Fabric w Carl Cox" -> ("HISTORY 2026-07-24", "Fabric", "Carl Cox")
        - "HISTORY 2026-07-24 (1) @ Fabric w Carl Cox" -> ("HISTORY 2026-07-24 (1)", "Fabric", "Carl Cox")
        - "HISTORY 2026-07-24 @ Fabric w/ Carl Cox" -> ("HISTORY 2026-07-24", "Fabric", "Carl Cox")

    Returns:
        Tuple of (base_name, location, artist) strings (defaults to empty strings if absent).
    """
    if not name:
        return ("", "", "")

    name_str = str(name).strip()

    # Case 1: Location present marked by '@'
    if "@" in name_str:
        base_part, rest = name_str.split("@", 1)
        base_name = base_part.strip()

        # Check if rest contains ' w ' or ' w/ ' indicating artist(s)
        match = re.search(r"\s+[wW](?:/)?(?:\s+|$)", rest)
        if match:
            location = rest[:match.start()].strip()
            artist = rest[match.end():].strip()
        else:
            location = rest.strip()
            artist = ""
    else:
        # Case 2: No '@', but artist may be present with ' w ' or ' w/ '
        match = re.search(r"\s+[wW](?:/)?(?:\s+|$)", name_str)
        if match:
            base_name = name_str[:match.start()].strip()
            location = ""
            artist = name_str[match.end():].strip()
        else:
            base_name = name_str
            location = ""
            artist = ""

    return (base_name, location, artist)


def compose_history_name(
    base_name: str,
    location: Optional[str] = None,
    artist: Optional[str] = None,
) -> str:
    """Compose full history session name from base name, optional location, and optional artist.

    Schema:
        <Base Name> [@ <Location>] [w <Artist(s)>]

    Automatically strips accidental duplicate '@' or 'w' / 'w/' from location and artist inputs.
    If base_name already contains '@' or 'w' sections and location/artist are provided,
    cleans up redundancies.

    Examples:
        compose_history_name("HISTORY 2026-07-24")
        -> "HISTORY 2026-07-24"
        compose_history_name("HISTORY 2026-07-24", location="Fabric")
        -> "HISTORY 2026-07-24 @ Fabric"
        compose_history_name("HISTORY 2026-07-24", artist="Carl Cox")
        -> "HISTORY 2026-07-24 w Carl Cox"
        compose_history_name("HISTORY 2026-07-24 (1)", location="Fabric", artist="Carl Cox")
        -> "HISTORY 2026-07-24 (1) @ Fabric w Carl Cox"
    """
    clean_base = str(base_name or "").strip()

    clean_loc = str(location or "").strip()
    if clean_loc.startswith("@"):
        clean_loc = clean_loc.lstrip("@").strip()

    clean_art = str(artist or "").strip()
    clean_art = re.sub(r"^[wW](?:/|\s+)", "", clean_art).strip()

    # If the user included '@' or 'w' inside clean_base, parse it to prevent duplication
    parsed_base, parsed_loc, parsed_art = parse_history_name(clean_base)
    if parsed_loc or parsed_art:
        clean_base = parsed_base
        if not clean_loc and parsed_loc:
            clean_loc = parsed_loc
        if not clean_art and parsed_art:
            clean_art = parsed_art

    parts = []
    if clean_base:
        parts.append(clean_base)
    if clean_loc:
        parts.append(f"@ {clean_loc}")
    if clean_art:
        parts.append(f"w {clean_art}")

    return " ".join(parts)


