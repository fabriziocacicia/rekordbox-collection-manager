"""Shared utilities for Rekordbox collection manager tools.

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
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple, Union

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


def resolve_tag_matching_ids(raw_tag_id: Any) -> List[str]:
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


_resolve_tag_matching_ids = resolve_tag_matching_ids


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
    from sqlalchemy.pool import NullPool
    from sqlcipher3 import dbapi2 as sqlcipher_driver

    master_key = deobfuscate(BLOB)

    # Determine probe order based on filename
    is_export = "export" in os.path.basename(db_path).lower()
    if is_export:
        candidate_keys = [DEVICE_LIBRARY_PLUS_KEY, master_key, None]
    else:
        candidate_keys = [master_key, DEVICE_LIBRARY_PLUS_KEY, None]

    engine_kwargs = {
        "poolclass": NullPool,
        "connect_args": {"check_same_thread": False, "timeout": 30.0},
    }

    for key in candidate_keys:
        try:
            if key:
                url = f"sqlite+pysqlcipher://:{key}@/{db_path}?"
                engine = create_engine(url, module=sqlcipher_driver, **engine_kwargs)
            else:
                engine = create_engine(f"sqlite:///{db_path}", **engine_kwargs)

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


def format_file_size(size_bytes: int) -> str:
    """Format file size in bytes to human-readable MB or KB string."""
    if not size_bytes or size_bytes <= 0:
        return "—"
    mb = size_bytes / (1024 * 1024)
    if mb >= 1.0:
        return f"{mb:.1f} MB"
    kb = size_bytes / 1024
    return f"{kb:.0f} KB"


def format_duration(duration_sec: Optional[Union[int, float]]) -> str:
    """Format duration in seconds into 'M:SS' or '--:--' if 0 or None."""
    try:
        sec = int(round(duration_sec or 0))
    except (ValueError, TypeError):
        return "--:--"
    if sec > 0:
        mins = sec // 60
        secs = sec % 60
        return f"{mins}:{secs:02d}"
    return "--:--"


def format_rating_stars(rating: Optional[int]) -> str:
    """Format numeric rating (0..255 or 1..5) to 5-star string (e.g. '★★★★☆')."""
    if not rating or rating <= 0:
        return "☆☆☆☆☆"
    if rating > 5:
        stars = max(1, min(5, round(rating / 51.0)))
    else:
        stars = max(1, min(5, rating))
    return "★" * stars + "☆" * (5 - stars)


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


def parse_played_with(artist_str: Optional[str]) -> List[str]:
    """Parse comma-separated DJ / artist names into a clean list of individual DJ names.

    Examples:
        - "Carl Cox, Marco Carola" -> ["Carl Cox", "Marco Carola"]
        - "Carl Cox" -> ["Carl Cox"]
        - "  Carl Cox ,  Richie Hawtin  " -> ["Carl Cox", "Richie Hawtin"]
        - "" or None -> []

    Returns:
        List of stripped, non-empty DJ names.
    """
    if not artist_str:
        return []
    raw = str(artist_str).strip()
    if not raw:
        return []
    parts = [part.strip() for part in raw.split(",")]
    return [p for p in parts if p]


def compose_played_with(djs: Optional[Iterable[str]]) -> str:
    """Compose an iterable of DJ / artist names into a clean comma-separated string.

    Examples:
        - ["Carl Cox", "Marco Carola"] -> "Carl Cox, Marco Carola"
        - ["Carl Cox"] -> "Carl Cox"
        - [] or None -> ""

    Returns:
        Clean comma-separated string of DJs.
    """
    if not djs:
        return ""
    clean = [str(d).strip() for d in djs if str(d).strip()]
    return ", ".join(clean)


def compose_history_name(
    base_name: str,
    location: Optional[str] = None,
    artist: Union[str, Iterable[str], None] = None,
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
        compose_history_name("HISTORY 2026-07-24", artist=["Carl Cox", "Marco Carola"])
        -> "HISTORY 2026-07-24 w Carl Cox, Marco Carola"
        compose_history_name("HISTORY 2026-07-24 (1)", location="Fabric", artist="Carl Cox")
        -> "HISTORY 2026-07-24 (1) @ Fabric w Carl Cox"
    """
    clean_base = str(base_name or "").strip()

    clean_loc = str(location or "").strip()
    if clean_loc.startswith("@"):
        clean_loc = clean_loc.lstrip("@").strip()

    if isinstance(artist, (list, tuple, set)):
        clean_art = compose_played_with(artist)
    else:
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


def open_in_folder(path: str) -> bool:
    """Open containing directory in the system file manager (Finder on macOS, Explorer on Windows).

    Returns True if successfully launched, False otherwise.
    """
    if not path:
        return False
    # If path is a file, get its directory
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    if not folder or not os.path.exists(folder):
        return False
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        elif sys.platform == "win32":
            subprocess.Popen(["explorer", os.path.normpath(folder)])
        else:
            subprocess.Popen(["xdg-open", folder])
        return True
    except Exception:
        return False


def reveal_in_file_manager(path: str) -> bool:
    """Reveal and highlight file in the system file manager (Finder -R on macOS, Explorer /select on Windows).

    Returns True if successfully launched, False otherwise.
    """
    if not path or not os.path.exists(path):
        return False
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        elif sys.platform == "win32":
            subprocess.Popen(["explorer", f"/select,{os.path.normpath(path)}"])
        else:
            folder = os.path.dirname(path) if os.path.isfile(path) else path
            subprocess.Popen(["xdg-open", folder])
        return True
    except Exception:
        return False


STREAMING_FILE_TYPES: Tuple[int, ...] = (25, 26)  # 25: Spotify, 26: Apple Music
STREAMING_URI_PREFIXES: Tuple[str, ...] = (
    "spotify:",
    "apple-music:",
    "beatport:",
    "beatsource:",
    "soundcloud:",
    "tidal:",
)


def is_streaming_track(
    raw_file_type: Any = None,
    filename: Optional[str] = None,
    folder_path: Optional[str] = None,
    ext_info: Optional[str] = None,
) -> bool:
    """Check if a track is a streaming service track (Spotify, Apple Music, Tidal, etc.)."""
    try:
        if raw_file_type is not None and int(raw_file_type) in STREAMING_FILE_TYPES:
            return True
    except (ValueError, TypeError):
        pass

    target_str = filename or folder_path or ""
    if any(target_str.startswith(prefix) for prefix in STREAMING_URI_PREFIXES):
        return True

    fp = folder_path or ""
    if any(fp.startswith(prefix) for prefix in STREAMING_URI_PREFIXES):
        return True

    if ext_info and "StreamingInfo" in str(ext_info):
        return True

    return False


def build_exclude_streaming_clause(cols: Set[str], table_alias: Optional[str] = "c") -> str:
    """Build SQL WHERE conditions to exclude streaming tracks from queries."""
    prefix = f"{table_alias}." if table_alias else ""
    parts = []

    if "FileType" in cols:
        ft_list = ", ".join(str(ft) for ft in STREAMING_FILE_TYPES)
        parts.append(f"({prefix}FileType NOT IN ({ft_list}) OR {prefix}FileType IS NULL)")

    if "FileNameL" in cols:
        for p in STREAMING_URI_PREFIXES:
            parts.append(f"({prefix}FileNameL NOT LIKE '{p}%' OR {prefix}FileNameL IS NULL)")

    if "FolderPath" in cols:
        for p in STREAMING_URI_PREFIXES:
            parts.append(f"({prefix}FolderPath NOT LIKE '{p}%' OR {prefix}FolderPath IS NULL)")

    if "ExtInfo" in cols:
        parts.append(f"({prefix}ExtInfo NOT LIKE '%StreamingInfo%' OR {prefix}ExtInfo IS NULL)")

    if not parts:
        return ""
    return "AND " + " AND ".join(parts)


# Rekordbox FileType mappings
EXTENSION_TO_FILE_TYPE: Dict[str, int] = {
    "MP3": 1,
    "M4A": 4,
    "AAC": 4,
    "FLAC": 5,
    "WAV": 11,
    "WAVE": 11,
    "AIF": 12,
    "AIFF": 12,
}

FILE_TYPE_TO_NAME: Dict[int, str] = {
    1: "MP3",
    4: "M4A",
    5: "FLAC",
    11: "WAV",
    12: "AIFF",
    25: "Spotify",
    26: "Apple Music",
}


def is_mp3_track(track_dict_or_path: Any) -> bool:
    """Check if a track dictionary or file path represents an MP3 file."""
    if not track_dict_or_path:
        return False
    if isinstance(track_dict_or_path, str):
        return os.path.splitext(track_dict_or_path)[1].lower() == ".mp3"
    if isinstance(track_dict_or_path, dict):
        if track_dict_or_path.get("raw_file_type") == 1:
            return True
        ft = str(track_dict_or_path.get("file_type") or "").strip().upper()
        if ft == "MP3":
            return True
        target = (
            track_dict_or_path.get("file_name")
            or track_dict_or_path.get("filename")
            or track_dict_or_path.get("file_path")
            or ""
        )
        return os.path.splitext(target)[1].lower() == ".mp3"
    return False


def is_aiff_track(track_dict_or_path: Any) -> bool:
    """Check if a track dictionary or file path represents an AIFF file."""
    if not track_dict_or_path:
        return False
    if isinstance(track_dict_or_path, str):
        return os.path.splitext(track_dict_or_path)[1].lower() in (".aiff", ".aif")
    if isinstance(track_dict_or_path, dict):
        if track_dict_or_path.get("raw_file_type") == 12:
            return True
        ft = str(track_dict_or_path.get("file_type") or "").strip().upper()
        if ft in ("AIFF", "AIF"):
            return True
        target = (
            track_dict_or_path.get("file_name")
            or track_dict_or_path.get("filename")
            or track_dict_or_path.get("file_path")
            or ""
        )
        return os.path.splitext(target)[1].lower() in (".aiff", ".aif")
    return False


def is_replaceable_track(track_dict_or_path: Any) -> bool:
    """Check if a track dictionary or file path represents a replaceable format (MP3 or AIFF)."""
    return is_mp3_track(track_dict_or_path) or is_aiff_track(track_dict_or_path)


def resolve_file_type(
    raw_file_type: Any,
    filename: Optional[str] = None,
    folder_path: Optional[str] = None,
) -> str:
    """Resolve human-readable audio file format (MP3, AIFF, FLAC, WAV, M4A, etc.)."""
    try:
        if raw_file_type is not None:
            ft_int = int(raw_file_type)
            if ft_int in FILE_TYPE_TO_NAME:
                return FILE_TYPE_TO_NAME[ft_int]
    except (ValueError, TypeError):
        pass

    target_str = filename or folder_path or ""
    if target_str.startswith("apple-music:"):
        return "Apple Music"
    if target_str.startswith("spotify:"):
        return "Spotify"
    for prefix in STREAMING_URI_PREFIXES:
        if target_str.startswith(prefix):
            return prefix.rstrip(":").replace("-", " ").title()

    ext = os.path.splitext(target_str)[1].lstrip(".").upper()
    if ext:
        if ext in ("AIF", "AIFF"):
            return "AIFF"
        return ext

    return "—"


def format_location_display(
    filename: Optional[str],
    folder_path: Optional[str],
) -> str:
    """Return 'containing_folder/filename' if containing folder exists, else filename or path."""
    fp = folder_path or ""
    fn = filename or ""
    if any(fp.startswith(p) for p in STREAMING_URI_PREFIXES):
        return fp

    if fp:
        parent_dir = os.path.basename(os.path.dirname(fp))
        file_part = fn or os.path.basename(fp)
        if parent_dir and file_part:
            return f"{parent_dir}/{file_part}"
        if file_part:
            return file_part
        return fp

    return fn or "—"


def resolve_file_and_folder_paths(
    filename: Optional[str],
    folder_path: Optional[str],
) -> Tuple[str, str, str, str]:
    """Resolve (folder_name, folder_dir, file_name, file_path).

    Returns:
        folder_name: containing directory basename for concise display (e.g. 'Afro House - BP')
        folder_dir: full directory path (e.g. '/Users/.../Music/Downloaded/Afro House - BP')
        file_name: track audio file name (e.g. 'On My Mind - Ajna (BE).mp3')
        file_path: full absolute path to the audio file
    """
    fp = folder_path or ""
    fn = filename or ""

    if any(fp.startswith(p) for p in STREAMING_URI_PREFIXES):
        return ("Streaming", fp, fn or fp, fp)

    if not fp and not fn:
        return ("—", "", "—", "")

    if fp:
        if os.path.splitext(fp)[1] or (fn and fp.endswith(f"/{fn}")):
            file_path = fp
            file_name = fn or os.path.basename(fp)
            folder_dir = os.path.dirname(fp)
            folder_name = os.path.basename(folder_dir)
        else:
            folder_dir = fp
            folder_name = os.path.basename(fp)
            file_name = fn or ""
            file_path = os.path.join(fp, fn) if fn else fp
        return (folder_name or "—", folder_dir, file_name or "—", file_path)

    return ("—", "", fn or "—", fn or "")


def is_purchased_path(path: Optional[str]) -> bool:
    """Determine if a file or folder path contains a folder named 'Purchased' in its folder tree.

    A track is deemed purchased if any directory component in its containing
    folder hierarchy is named 'Purchased' (case-insensitive), distinguishing it
    from downloaded or other tracks.
    """
    if not path or path == "—":
        return False
    norm = os.path.normpath(str(path))
    parts = norm.replace("\\", "/").split("/")
    # Exclude filename at the end if it has an extension
    if len(parts) > 1 and ("." in parts[-1] and not parts[-1].startswith(".")):
        dir_parts = parts[:-1]
    else:
        dir_parts = parts
    return any(p.strip().lower() == "purchased" for p in dir_parts)


def is_purchased_track(track: Any) -> bool:
    """Check if a track dictionary or path represents a purchased track."""
    if isinstance(track, str):
        return is_purchased_path(track)
    if not isinstance(track, Mapping):
        return False
    if "is_purchased" in track:
        return bool(track["is_purchased"])
    for key in ("file_path", "folder_dir", "folder_path", "FolderPath", "FilePath", "FileNameL"):
        val = track.get(key)
        if val and is_purchased_path(str(val)):
            return True
    return False


STORE_BEATPORT = "Beatport"
STORE_BANDCAMP = "Bandcamp"
STORE_QOBUZ = "Qobuz"
STORE_TRAXSOURCE = "Traxsource"

KNOWN_STORES: List[str] = [STORE_BEATPORT, STORE_BANDCAMP, STORE_QOBUZ, STORE_TRAXSOURCE]

STORE_COMMENT_TEMPLATES: Dict[str, str] = {
    STORE_BEATPORT: "Purchased at Beatport.com",
    STORE_BANDCAMP: "Visit https://bandcamp.com",
    STORE_QOBUZ: "Purchased at qobuz.com",
    STORE_TRAXSOURCE: "Purchased at Traxsource.com",
}


def parse_store_from_comment(comment: Optional[str]) -> Optional[str]:
    """Parse store name from track comment metadata.

    Supported stores:
    - Beatport: 'Purchased at Beatport.com'
    - Traxsource: 'Purchased at Traxsource.com'
    - Qobuz: 'Purchased at qobuz.com'
    - Bandcamp: 'Visit https://*.bandcamp.com' or 'Visit https://bandcamp.com'
    """
    if not comment:
        return None
    c = str(comment).strip()
    if not c:
        return None

    if re.search(r"purchased\s+at\s+beatport\.com", c, re.IGNORECASE):
        return STORE_BEATPORT
    if re.search(r"purchased\s+at\s+traxsource\.com", c, re.IGNORECASE):
        return STORE_TRAXSOURCE
    if re.search(r"purchased\s+at\s+qobuz\.com", c, re.IGNORECASE):
        return STORE_QOBUZ
    if re.search(r"bandcamp\.com", c, re.IGNORECASE) and (
        "visit" in c.lower() or "purchased" in c.lower() or c.lower().startswith("http")
    ):
        return STORE_BANDCAMP

    return None


def get_store_comment(store_name: str) -> str:
    """Return standard comment string for a store."""
    return STORE_COMMENT_TEMPLATES.get(store_name, "")


def update_track_comment(
    engine: Any,
    content_id: str,
    comment: str,
) -> bool:
    """Update Commnt field of a track in djmdContent."""
    from sqlalchemy import text

    now_utc = get_rekordbox_timestamp()
    with engine.begin() as conn:
        cols = {
            r[1]
            for r in conn.exec_driver_sql("PRAGMA table_info(djmdContent);").fetchall()
        }
        if not cols or "Commnt" not in cols:
            raise ValueError("Table djmdContent or column Commnt not found.")

        updates = ["Commnt = :comment"]
        params: Dict[str, Any] = {
            "id": str(content_id),
            "comment": (comment or "").strip(),
        }
        if "updated_at" in cols:
            updates.append("updated_at = :updated_at")
            params["updated_at"] = now_utc

        sql = f"UPDATE djmdContent SET {', '.join(updates)} WHERE ID = :id;"
        conn.execute(text(sql), params)
    return True


def build_track_record(
    row: Mapping[str, Any],
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Normalize a database row mapping into a standard collection track dictionary.

    Extracts, calculates, and formats:
    - ID / content_id
    - Title, Artist, Album, Genre, Key
    - BPM (divided by 100 and rounded to 1 decimal place)
    - Length (seconds) and formatted duration ('MM:SS')
    - Play count (DJPlayCount or PlayCount)
    - Rating, Year / ReleaseYear, Bitrate
    - Audio format string and raw FileType
    - Tag list and Playlist list (splitting delimiter '|||')
    - Resolved folder and filename paths
    - Creation, date added (StockDate), and audio specs (sample rate, bit depth, file size, color)
    """
    raw_count = row.get("DJPlayCount")
    if raw_count is None:
        raw_count = row.get("PlayCount")
    try:
        count_int = int(raw_count) if raw_count is not None else 0
    except (ValueError, TypeError):
        count_int = 0

    bpm_raw = row.get("BPM")
    bpm_val = round(float(bpm_raw) / 100.0, 1) if bpm_raw and bpm_raw > 0 else 0.0

    duration_sec = int(row.get("Length") or 0)
    dur_str = format_duration(duration_sec)

    raw_tags = row.get("TagNames")
    tags_list = list(dict.fromkeys(t.strip() for t in raw_tags.split("|||") if t.strip())) if raw_tags else []

    raw_playlists = row.get("PlaylistNames")
    playlists_list = list(dict.fromkeys(p.strip() for p in raw_playlists.split("|||") if p.strip())) if raw_playlists else []

    raw_fn = row.get("FileNameL") or ""
    raw_fp = row.get("FolderPath") or ""
    file_type_str = resolve_file_type(row.get("FileType"), raw_fn, raw_fp)
    loc_disp = format_location_display(raw_fn, raw_fp)
    f_name, f_dir, fn_disp, f_full = resolve_file_and_folder_paths(raw_fn, raw_fp)

    content_id = str(row.get("ContentID") or row.get("ID") or "")
    rating_val = int(row.get("Rating") or 0)
    release_year = row.get("ReleaseYear")
    try:
        year_int = int(release_year) if release_year is not None else 0
    except (ValueError, TypeError):
        year_int = 0

    purchased = is_purchased_path(raw_fp) or is_purchased_path(f_dir) or is_purchased_path(f_full)

    record: Dict[str, Any] = {
        "id": content_id,
        "content_id": content_id,
        "title": row.get("Title") or "(Untitled)",
        "artist": row.get("ArtistName") or "Unknown Artist",
        "album": row.get("AlbumName") or "",
        "genre": row.get("GenreName") or "",
        "key": row.get("KeyName") or "",
        "bpm": bpm_val,
        "duration_sec": duration_sec,
        "duration_str": dur_str,
        "play_count": count_int,
        "plays": count_int,
        "rating": rating_val,
        "year": year_int,
        "release_year": release_year,
        "bitrate": int(row.get("BitRate") or 0),
        "raw_file_type": row.get("FileType"),
        "file_type": file_type_str,
        "is_purchased": purchased,
        "tags": tags_list,
        "playlists": playlists_list,
        "folder": f_name,
        "folder_name": f_name,
        "folder_dir": f_dir,
        "file_name": fn_disp,
        "filename": raw_fn or fn_disp,
        "folder_path": raw_fp or f_dir,
        "file_path": f_full,
        "location": loc_disp,
        "location_display": loc_disp,
        "comment": str(row.get("Commnt") or ""),
        "comments": str(row.get("Commnt") or ""),
        "store": parse_store_from_comment(str(row.get("Commnt") or "")),
        "image_path": row.get("ImagePath") or "",
        "date_added": str(row.get("StockDate") or "")[:10],
        "created_at": str(row.get("created_at") or "") if row.get("created_at") is not None else "",
        "sample_rate": int(row.get("SampleRate") or 0),
        "bit_depth": int(row.get("BitDepth") or 0),
        "file_size": int(row.get("FileSize") or 0),
        "color_id": str(row.get("ColorID") or ""),
        "table": str(row.get("table") or "djmdContent"),
    }
    if extra:
        record.update(extra)
    return record


def resolve_artwork_path(
    image_path: Optional[str],
    db_path: Optional[str] = None,
) -> Optional[str]:
    """Resolve Rekordbox internal artwork path to absolute path on disk.

    Rekordbox stores artwork references like '/PIONEER/Artwork/9e9/1e.../artwork.jpg'.
    On macOS / Pioneer installations, artwork lives under the 'share' directory
    next to master.db (e.g. ~/Library/Pioneer/rekordbox/share/PIONEER/Artwork/...)
    or relative to exportLibrary.db on USB drives.
    """
    if not image_path:
        return None

    if os.path.isabs(image_path) and os.path.exists(image_path):
        return image_path

    clean_rel = image_path.lstrip("/\\")
    candidates = []

    if db_path:
        db_dir = os.path.dirname(db_path)
        candidates.append(os.path.join(db_dir, "share", clean_rel))
        candidates.append(os.path.join(db_dir, clean_rel))
        volume_root = db_dir
        for _ in range(4):
            volume_pioneer = os.path.join(volume_root, "PIONEER")
            if os.path.exists(volume_pioneer):
                candidates.append(os.path.join(volume_root, clean_rel))
                break
            parent = os.path.dirname(volume_root)
            if parent == volume_root:
                break
            volume_root = parent

    home_pioneer = os.path.expanduser("~/Library/Pioneer/rekordbox")
    candidates.append(os.path.join(home_pioneer, "share", clean_rel))
    candidates.append(os.path.join(home_pioneer, clean_rel))

    for c in candidates:
        if os.path.exists(c):
            return c

    return None


def extract_embedded_artwork(file_path: str) -> Optional[Tuple[str, str]]:
    """Extract embedded cover artwork from audio file (AIFF, MP3, M4A, FLAC).

    Caches the extracted image as a JPEG in ~/.cache/rekordbox-collection-manager/artwork_cache/
    and returns (cached_preview_path, resolution_str) or None if no embedded artwork.
    """
    if not file_path or not os.path.exists(file_path):
        return None

    try:
        import mutagen

        try:
            audio = mutagen.File(file_path)
        except Exception:
            audio = None

        if not audio:
            try:
                from mutagen.id3 import ID3
                audio = ID3(file_path)
            except Exception:
                return None

        raw_data: Optional[bytes] = None
        # 1. ID3 / AIFF / MP3
        tags = getattr(audio, "tags", audio)
        if tags:
            for k in tags.keys():
                if str(k).startswith("APIC"):
                    val = tags[k]
                    if hasattr(val, "data") and val.data:
                        raw_data = bytes(val.data)
                        break
            if not raw_data and "covr" in tags:
                covers = tags["covr"]
                if covers and len(covers) > 0:
                    raw_data = bytes(covers[0])

        # 2. FLAC
        if not raw_data and hasattr(audio, "pictures") and audio.pictures:
            raw_data = bytes(audio.pictures[0].data)

        if not raw_data:
            return None

        import hashlib

        cache_dir = os.path.expanduser("~/.cache/rekordbox-collection-manager/artwork_cache")
        os.makedirs(cache_dir, exist_ok=True)
        mtime = os.path.getmtime(file_path)
        h = hashlib.md5(f"{file_path}_{mtime}".encode()).hexdigest()
        out_path = os.path.join(cache_dir, f"{h}.jpg")

        from PySide6.QtGui import QImage

        img = QImage.fromData(raw_data)
        if img.isNull():
            return None

        img.save(out_path, "JPEG", 95)
        res_str = f"{img.width()}×{img.height()}"
        return out_path, res_str
    except Exception:
        return None


def install_rekordbox_artwork(
    track_uuid: str,
    artwork_file_or_bytes: Union[str, bytes, Any],
    db_path: Optional[str] = None,
) -> Optional[str]:
    """Install artwork for a Rekordbox track by UUID into the Pioneer share directory.

    Generates:
      - artwork.jpg (full size, JPEG 95)
      - artwork_m.jpg (240x240, JPEG 90)
      - artwork_s.jpg (80x80, JPEG 90)

    Returns the relative ImagePath (e.g. '/PIONEER/Artwork/797/7e8a4-811f-4d7b-aeb4-7a4326ba6326/artwork.jpg')
    or None on failure.
    """
    if not track_uuid:
        return None
    clean_uuid = str(track_uuid).strip()
    if len(clean_uuid) < 4:
        return None

    # Determine share dir
    share_dir = None
    if db_path:
        db_dir = os.path.dirname(os.path.abspath(db_path))
        share_cand = os.path.join(db_dir, "share")
        if os.path.isdir(share_cand):
            share_dir = share_cand
        else:
            pioneer_default = os.path.expanduser("~/Library/Pioneer/rekordbox")
            if os.path.exists(db_path) and not os.path.abspath(db_path).startswith(pioneer_default):
                share_dir = share_cand

    if not share_dir:
        candidates = [
            os.path.expanduser("~/Library/Pioneer/rekordbox/share"),
            os.path.expanduser("~/Library/Pioneer/rekordbox"),
        ]
        for c in candidates:
            if os.path.exists(c) and os.path.isdir(c):
                share_dir = c
                break

    if not share_dir:
        share_dir = os.path.expanduser("~/Library/Pioneer/rekordbox/share")

    rel_dir = os.path.join("PIONEER", "Artwork", clean_uuid[:3], clean_uuid[3:])
    full_dir = os.path.join(share_dir, rel_dir)
    os.makedirs(full_dir, exist_ok=True)

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage

    if isinstance(artwork_file_or_bytes, bytes):
        img = QImage.fromData(artwork_file_or_bytes)
    elif hasattr(artwork_file_or_bytes, "isNull"):
        img = artwork_file_or_bytes.toImage() if hasattr(artwork_file_or_bytes, "toImage") else artwork_file_or_bytes
    else:
        img = QImage(str(artwork_file_or_bytes))

    if img.isNull():
        return None

    full_p = os.path.join(full_dir, "artwork.jpg")
    med_p = os.path.join(full_dir, "artwork_m.jpg")
    small_p = os.path.join(full_dir, "artwork_s.jpg")

    img.save(full_p, "JPEG", 95)
    img.scaled(240, 240, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation).save(med_p, "JPEG", 90)
    img.scaled(80, 80, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation).save(small_p, "JPEG", 90)

    return f"/PIONEER/Artwork/{clean_uuid[:3]}/{clean_uuid[3:]}/artwork.jpg"

