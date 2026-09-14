"""Shared utilities for Rekordbox collection fixer tools."""

import os
import shutil
import subprocess
import sys

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

        print("⚠️  Rekordbox è ancora in esecuzione! Chiudilo prima di procedere (Cmd + Q).")
        print("⚠️  Rekordbox is still running! Please close it before proceeding (Cmd + Q).")
        if exit_on_running:
            sys.exit(1)
        return True

    return False
