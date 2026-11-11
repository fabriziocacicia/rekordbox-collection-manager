# src

## Overview
Source root for `rekordbox-collection-manager` (`module-root = "src"` in `pyproject.toml`), housing the installable `rekordbox_collection_manager` package and its nested PySide6 desktop UI packages without direct top-level source modules.

## Subdirectories & Boundaries
- [`rekordbox_collection_manager`](./rekordbox_collection_manager/README.md): Core library, database access, analytics, backup/restore, Turso cloud sync, CLI tools, macOS `launchd` daemon, and the nested [`gui`](./rekordbox_collection_manager/gui/README.md) and [`analytics_widgets`](./rekordbox_collection_manager/gui/analytics_widgets/README.md) desktop UI packages.
