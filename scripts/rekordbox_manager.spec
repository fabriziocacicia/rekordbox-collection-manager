# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification file for Rekordbox Collection Manager desktop application."""

import os
import re
from pathlib import Path
from PyInstaller.config import CONF

# Project paths
# When PyInstaller executes a .spec file, SPECPATH is set to the spec directory.
SPEC_DIR = Path(SPECPATH).resolve() if "SPECPATH" in locals() else Path(__file__).resolve().parent
PROJECT_ROOT = SPEC_DIR.parent
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"

# Keep PyInstaller cache inside project build directory instead of ~/Library/Application Support
CONF['cachedir'] = str(PROJECT_ROOT / "build" / "pyinstaller_cache")

# Extract version from pyproject.toml
app_version = "0.20.0"
if PYPROJECT_PATH.exists():
    match = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', PYPROJECT_PATH.read_text(encoding="utf-8"), re.MULTILINE)
    if match:
        app_version = match.group(1).strip()

assets_dir = PROJECT_ROOT / "assets"
icon_icns = str(assets_dir / "icon.icns") if (assets_dir / "icon.icns").exists() else None
icon_png = str(assets_dir / "icon.png") if (assets_dir / "icon.png").exists() else None

datas = []
if icon_png:
    datas.append((icon_png, "assets"))
if icon_icns:
    datas.append((icon_icns, "assets"))

hidden_imports = [
    # Dynamic database drivers
    "sqlcipher3",
    "sqlcipher3.dbapi2",
    "sqlalchemy.dialects.sqlite",
    "sqlalchemy.dialects.sqlite.pysqlcipher",
    # Pyrekordbox dynamic modules
    "pyrekordbox",
    "pyrekordbox.config",
    "pyrekordbox.db6",
    "pyrekordbox.db6.database",
    "pyrekordbox.utils",
    # Mutagen audio metadata formats
    "mutagen",
    "mutagen.aiff",
    "mutagen.mp3",
    "mutagen.id3",
    "mutagen.flac",
    # Libsql client
    "libsql_client",
    "libsql_client.http",
    "libsql_client.sqlite3_utils",
    # PySide6 components
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtSvg",
]

excluded_modules = [
    "pytest",
    "unittest",
    "tkinter",
    "PySide6.QtWebEngine",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQml",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    "PySide6.QtSensors",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtNetworkAuth",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtVirtualKeyboard",
    "PySide6.QtPositioning",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
]

a = Analysis(
    [str(PROJECT_ROOT / "src/rekordbox_collection_manager/gui/app.py")],
    pathex=[str(PROJECT_ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excluded_modules,
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Rekordbox Collection Manager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="arm64",
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Rekordbox Collection Manager",
)

app = BUNDLE(
    coll,
    name="Rekordbox Collection Manager.app",
    icon=icon_icns,
    bundle_identifier="com.fabriziocacicia.rekordbox-collection-manager",
    info_plist={
        "CFBundleDisplayName": "Rekordbox Collection Manager",
        "CFBundleName": "Rekordbox Collection Manager",
        "CFBundleExecutable": "Rekordbox Collection Manager",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": app_version,
        "CFBundleVersion": app_version,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "14.0",
        "NSRequiresAquaSystemAppearance": False,
    },
)
