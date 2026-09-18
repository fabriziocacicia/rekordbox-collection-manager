"""Asset management and path resolution for Rekordbox Collection Manager GUI."""

import os
import sys
from pathlib import Path
from typing import Optional
from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


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
    # File is in src/rekordbox_collection_manager/gui/assets.py -> parents[2] is root
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


def create_svg_icon(svg_template: str, color: str = "#a0a4b0", active_color: str = "#f4f5f7", size: int = 48) -> QIcon:
    """Generate a crisp high-DPI QIcon from an SVG template string with normal and active states."""
    icon = QIcon()
    for mode, stroke_color in [(QIcon.Mode.Normal, color), (QIcon.Mode.Active, active_color)]:
        svg_xml = svg_template.format(color=stroke_color)
        renderer = QSvgRenderer(QByteArray(svg_xml.encode("utf-8")))
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter)
        painter.end()
        icon.addPixmap(pixmap, mode)
    return icon


def get_refresh_icon() -> QIcon:
    """Return a crisp vector reload/refresh icon."""
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/>
      <path d="M21 3v5h-5"/>
      <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/>
      <path d="M8 16H3v5"/>
    </svg>"""
    return create_svg_icon(svg, color="#a0a4b0", active_color="#f4f5f7", size=48)

