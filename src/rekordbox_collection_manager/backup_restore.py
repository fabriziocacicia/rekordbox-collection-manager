"""Backup and disaster recovery operations for Rekordbox databases and collections."""

import logging
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from rekordbox_collection_manager.utils import (
    format_file_size,
    is_rekordbox_running,
    resolve_db_path,
)

logger = logging.getLogger(__name__)

DEFAULT_PIONEER_DIR = Path.home() / "Library" / "Pioneer" / "rekordbox"
DEFAULT_APPSUPPORT_DIR = Path.home() / "Library" / "Application Support" / "Pioneer" / "rekordbox"


def get_default_backup_dir() -> Path:
    """Return default backup directory path (~/Music/rekordbox/Backups)."""
    return Path.home() / "Music" / "rekordbox" / "Backups"


def list_backups(backup_dir: Optional[Union[str, Path]] = None) -> List[Dict[str, Any]]:
    """Scan and list all valid Rekordbox backups found in backup_dir.

    Returns a list of dictionaries sorted newest first:
    [
        {
            "name": folder_name,
            "path": Path,
            "db_path": Path,
            "db_size": int,
            "db_size_formatted": str,
            "created_at": datetime,
            "is_safety": bool,
            "type": "Full Library" | "DB Snapshot",
            "has_app_support": bool,
        },
        ...
    ]
    """
    root = Path(backup_dir) if backup_dir else get_default_backup_dir()
    if not root.exists() or not root.is_dir():
        return []

    backups: List[Dict[str, Any]] = []

    for item in root.iterdir():
        if not item.is_dir():
            continue

        # Look for master.db inside Pioneer_Library or at root of item
        pioneer_lib = item / "Pioneer_Library"
        db_path: Optional[Path] = None
        backup_type = "DB Snapshot"

        if pioneer_lib.is_dir() and (pioneer_lib / "master.db").is_file():
            db_path = pioneer_lib / "master.db"
            backup_type = "Full Library"
        elif (item / "master.db").is_file():
            db_path = item / "master.db"
            backup_type = "DB Snapshot"
        elif (item / "exportLibrary.db").is_file():
            db_path = item / "exportLibrary.db"
            backup_type = "USB Export DB"

        if not db_path:
            continue

        try:
            stat_info = db_path.stat()
            db_size = stat_info.st_size
            mod_time = datetime.fromtimestamp(stat_info.st_mtime)
        except OSError:
            db_size = 0
            mod_time = datetime.fromtimestamp(item.stat().st_mtime)

        # Parse timestamp from folder name if possible (YYYYMMDD_HHMMSS)
        folder_time = mod_time
        match = re.search(r"(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})", item.name)
        if match:
            try:
                folder_time = datetime(
                    int(match.group(1)),
                    int(match.group(2)),
                    int(match.group(3)),
                    int(match.group(4)),
                    int(match.group(5)),
                    int(match.group(6)),
                )
            except ValueError:
                folder_time = mod_time

        is_safety = "pre_restore_safety" in item.name
        has_app_support = (item / "Application_Support").is_dir()

        backups.append(
            {
                "name": item.name,
                "path": item,
                "db_path": db_path,
                "db_size": db_size,
                "db_size_formatted": format_file_size(db_size),
                "created_at": folder_time,
                "is_safety": is_safety,
                "type": backup_type,
                "has_app_support": has_app_support,
            }
        )

    # Sort descending by created_at
    backups.sort(key=lambda b: b["created_at"], reverse=True)
    return backups


def create_database_backup(
    db_path: Optional[str] = None,
    backup_dir: Optional[Union[str, Path]] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Create a timestamped backup of the Rekordbox database or collection.

    - For the default macOS Rekordbox collection, backs up the entire Pioneer
      library folder and Application Support configuration.
    - For standalone or USB databases, backs up the database file and companion files.
    """
    if not force and is_rekordbox_running():
        raise RuntimeError("Rekordbox is currently running! Please close it before creating a backup.")

    target_db = Path(resolve_db_path(db_path))
    if not target_db.exists():
        raise FileNotFoundError(f"Database file not found: {target_db}")

    out_root = Path(backup_dir) if backup_dir else get_default_backup_dir()
    out_root.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    is_default_pioneer = DEFAULT_PIONEER_DIR.resolve() in target_db.resolve().parents or target_db.name == "master.db"

    if is_default_pioneer and DEFAULT_PIONEER_DIR.is_dir():
        backup_folder = out_root / f"rekordbox_mac_backup_{timestamp}"
        pioneer_backup_dest = backup_folder / "Pioneer_Library"
        pioneer_backup_dest.mkdir(parents=True, exist_ok=True)

        # Copy entire Pioneer library directory
        _sync_copy(DEFAULT_PIONEER_DIR, pioneer_backup_dest)

        # Copy Application Support directory if present
        if DEFAULT_APPSUPPORT_DIR.is_dir():
            appsupport_dest = backup_folder / "Application_Support"
            appsupport_dest.mkdir(parents=True, exist_ok=True)
            _sync_copy(DEFAULT_APPSUPPORT_DIR, appsupport_dest)

        backup_type = "Full Library"
        saved_db = pioneer_backup_dest / "master.db"
    else:
        backup_folder = out_root / f"rekordbox_backup_{timestamp}"
        backup_folder.mkdir(parents=True, exist_ok=True)

        # Copy database file
        saved_db = backup_folder / target_db.name
        shutil.copy2(target_db, saved_db)

        # Also copy wal and shm files if present
        for ext in ["-wal", "-shm"]:
            extra = target_db.parent / (target_db.name + ext)
            if extra.is_file():
                shutil.copy2(extra, backup_folder / extra.name)

        backup_type = "DB Snapshot"

    db_size = saved_db.stat().st_size if saved_db.exists() else 0

    return {
        "success": True,
        "backup_folder": backup_folder,
        "db_path": saved_db,
        "db_size": db_size,
        "db_size_formatted": format_file_size(db_size),
        "timestamp": timestamp,
        "type": backup_type,
    }


def perform_restore(
    backup_path: Union[str, Path],
    target_pioneer_dir: Optional[Union[str, Path]] = None,
    target_appsupport_dir: Optional[Union[str, Path]] = None,
    backup_root: Optional[Union[str, Path]] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Safely restore a Rekordbox backup folder into live directories.

    1. Checks that Rekordbox is not running.
    2. Creates an emergency snapshot of current live files.
    3. Restores database and configuration files.
    4. Purges orphaned WAL/SHM files.
    """
    if not force and is_rekordbox_running():
        raise RuntimeError(
            "Rekordbox or Rekordbox Collection Manager is currently running! "
            "Please close the application before restoring to prevent corruption."
        )

    b_path = Path(backup_path).resolve()
    if not b_path.is_dir():
        raise FileNotFoundError(f"Backup folder not found: {b_path}")

    # Identify Pioneer library source
    if (b_path / "Pioneer_Library" / "master.db").is_file():
        pioneer_src = b_path / "Pioneer_Library"
    elif (b_path / "master.db").is_file():
        pioneer_src = b_path
    elif (b_path / "exportLibrary.db").is_file():
        pioneer_src = b_path
    else:
        raise ValueError(f"No valid Rekordbox database found in backup folder: {b_path}")

    dest_pioneer = Path(target_pioneer_dir).resolve() if target_pioneer_dir else DEFAULT_PIONEER_DIR
    dest_appsupport = Path(target_appsupport_dir).resolve() if target_appsupport_dir else DEFAULT_APPSUPPORT_DIR
    backups_folder = Path(backup_root).resolve() if backup_root else get_default_backup_dir()
    backups_folder.mkdir(parents=True, exist_ok=True)

    # 1. Emergency Safety Snapshot of current active state
    safety_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safety_dir = backups_folder / f"rekordbox_pre_restore_safety_{safety_timestamp}"
    safety_dir.mkdir(parents=True, exist_ok=True)

    if dest_pioneer.is_dir():
        safety_pioneer = safety_dir / "Pioneer_Library"
        safety_pioneer.mkdir(parents=True, exist_ok=True)
        _sync_copy(dest_pioneer, safety_pioneer)

    if dest_appsupport.is_dir():
        safety_appsupport = safety_dir / "Application_Support"
        safety_appsupport.mkdir(parents=True, exist_ok=True)
        _sync_copy(dest_appsupport, safety_appsupport)

    # 2. Restore Pioneer Library (Clean sync)
    dest_pioneer.mkdir(parents=True, exist_ok=True)
    _sync_replace(pioneer_src, dest_pioneer)

    # Ensure stale wal/shm are removed if not in backup
    for extra_name in ["master.db-wal", "master.db-shm"]:
        if not (pioneer_src / extra_name).exists():
            stale_file = dest_pioneer / extra_name
            if stale_file.exists():
                try:
                    stale_file.unlink()
                except OSError:
                    pass

    # 3. Restore Application Support configs if present in backup
    app_support_src = b_path / "Application_Support"
    app_support_restored = False
    if app_support_src.is_dir():
        dest_appsupport.mkdir(parents=True, exist_ok=True)
        _sync_replace(app_support_src, dest_appsupport)
        app_support_restored = True

    # 4. Verify restored master.db
    master_db = dest_pioneer / "master.db"
    if not master_db.exists():
        # Maybe it's exportLibrary.db
        master_db = dest_pioneer / "exportLibrary.db"

    if not master_db.exists():
        raise RuntimeError(f"Critical error: database file missing in {dest_pioneer} after restoration.")

    db_size = master_db.stat().st_size

    return {
        "success": True,
        "restored_db_path": str(master_db),
        "db_size": db_size,
        "db_size_formatted": format_file_size(db_size),
        "safety_backup_path": str(safety_dir),
        "app_support_restored": app_support_restored,
    }


def _sync_copy(src: Path, dst: Path) -> None:
    """Copy contents of directory src into directory dst."""
    if shutil.which("rsync"):
        subprocess.run(
            ["rsync", "-aI", f"{src}/", f"{dst}/"],
            check=True,
            capture_output=True,
        )
    else:
        for item in src.iterdir():
            target = dst / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)


def _sync_replace(src: Path, dst: Path) -> None:
    """Synchronize contents of src into dst, deleting files in dst not in src."""
    if shutil.which("rsync"):
        subprocess.run(
            ["rsync", "-aI", "--delete", f"{src}/", f"{dst}/"],
            check=True,
            capture_output=True,
        )
    else:
        for existing in dst.iterdir():
            if existing.is_file() or existing.is_symlink():
                existing.unlink()
            elif existing.is_dir():
                shutil.rmtree(existing)
        for item in src.iterdir():
            target = dst / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)
