"""Application entry point for Rekordbox Collection Manager GUI."""

import argparse
import sys
from typing import List, Optional
from PySide6.QtWidgets import QApplication

from rekordbox_collection_manager.gui.assets import get_app_icon, get_asset_path
from rekordbox_collection_manager.gui.macos import (
    setup_macos_app_metadata,
    update_macos_menu_title,
)
from rekordbox_collection_manager.gui.main_window import MainWindow
from rekordbox_collection_manager.gui.theme import apply_dark_theme


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="rekordbox-manager",
        description="Rekordbox Collection Manager - Native macOS Desktop Application",
    )
    parser.add_argument(
        "-d",
        "--db-path",
        dest="db_path",
        default=None,
        help="Optional path to master.db or exportLibrary.db",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    # Configure native macOS process name, bundle info, and dock icon before QApplication
    setup_macos_app_metadata(
        app_name="Rekordbox Collection Manager",
        icon_path=get_asset_path("icon.png"),
    )

    # Initialize Qt Application
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv[:1])

    app.setApplicationName("Rekordbox Collection Manager")
    app.setApplicationDisplayName("Rekordbox Collection Manager")
    app.setOrganizationName("Rekordbox Collection Manager")

    # Set Application Icon
    app_icon = get_app_icon()
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)

    # Apply Pioneer DJ Dark Theme
    apply_dark_theme(app)

    # Launch Main Window
    window = MainWindow(initial_db_path=args.db_path)
    window.show()

    # Update macOS App menu title
    update_macos_menu_title("Rekordbox Collection Manager")

    ret = app.exec()
    from rekordbox_collection_manager.gui.workers import wait_for_retired_workers
    wait_for_retired_workers(timeout_ms=1000)
    return ret


if __name__ == "__main__":
    sys.exit(main())
