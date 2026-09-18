"""Application entry point for Rekordbox Collection Fixer GUI."""

import argparse
import sys
from typing import List, Optional
from PySide6.QtWidgets import QApplication

from rekordbox_collection_fixer.gui.assets import get_app_icon
from rekordbox_collection_fixer.gui.main_window import MainWindow
from rekordbox_collection_fixer.gui.theme import apply_dark_theme


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="rekordbox-gui",
        description="Rekordbox Collection Fixer - Native macOS Desktop Application",
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

    # Initialize Qt Application
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv[:1])

    app.setApplicationName("Rekordbox Collection Fixer")
    app.setApplicationDisplayName("Rekordbox Collection Fixer")
    app.setOrganizationName("Rekordbox Collection Fixer")

    # Set Application Icon
    app_icon = get_app_icon()
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)

    # Apply Pioneer DJ Dark Theme
    apply_dark_theme(app)

    # Launch Main Window
    window = MainWindow(initial_db_path=args.db_path)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
