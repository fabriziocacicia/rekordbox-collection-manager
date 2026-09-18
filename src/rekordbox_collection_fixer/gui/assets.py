"""Asset management and path resolution for Rekordbox Collection Fixer GUI."""

import os
import sys
from pathlib import Path
from typing import Optional
from PySide6.QtGui import QIcon, QPixmap


def get_asset_path(filename: str = "icon.png") -> Optional[str]:
    """Find absolute path to an asset file across development, installed, or packaged layouts."""
    candidates = []

    # 1. PyInstaller bundle directory
    if hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "assets" / filename)
        candidates.append(Path(sys._MEIPASS) / filename)

    # 2. Package assets directory
    pkg_dir = Path(__file__).resolve().parent
    candidates.append(pkg_dir / "assets" / filename)
    candidates.append(pkg_dir.parent / "assets" / filename)

    # 3. Project root directory (during development)
    # File is in src/rekordbox_collection_fixer/gui/assets.py -> parents[2] is root
    if len(pkg_dir.parents) >= 3:
        project_root = pkg_dir.parents[2]
        candidates.append(project_root / "assets" / filename)

    # 4. Current working directory
    candidates.append(Path.cwd() / "assets" / filename)
    candidates.append(Path.cwd() / filename)

    for path in candidates:
        if path.exists():
            return str(path.resolve())

    return None


def get_app_icon() -> QIcon:
    """Return QIcon for the application if asset exists, otherwise empty QIcon."""
    icon_path = get_asset_path("icon.png")
    if icon_path:
        return QIcon(icon_path)
    return QIcon()
