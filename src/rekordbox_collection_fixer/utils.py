"""Shared utilities for Rekordbox collection fixer tools."""

import os
import shutil
import subprocess
import sys
from typing import Optional, Tuple

import questionary
from pyrekordbox.db6.database import get_rekordbox_pid


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


DEVICE_LIBRARY_PLUS_KEY = "r8gddnr4k847830ar6cqzbkk0el6qytmb3trbbx805jm74vez64i5o8fnrqryqls"


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


def resolve_db_path(
    candidate: Optional[str] = None,
    prompt_message: str = "Enter path to Rekordbox database (master.db or exportLibrary.db):",
) -> str:
    """Determine database path from candidate string, local exportLibrary.db, default master.db, or prompt."""
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


