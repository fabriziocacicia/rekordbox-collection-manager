"""Rekordbox Collection Manager - collection of tools for managing Rekordbox databases."""

import sys
from pathlib import Path

from rekordbox_collection_manager.utils import ensure_rekordbox_not_running, is_rekordbox_running


def _get_version() -> str:
    try:
        if sys.version_info >= (3, 11):
            import tomllib

            pyproject_path = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"
            if pyproject_path.exists():
                with open(pyproject_path, "rb") as f:
                    data = tomllib.load(f)
                    ver = data.get("project", {}).get("version")
                    if ver:
                        return str(ver)
    except Exception:
        pass

    try:
        from importlib.metadata import version

        return version("rekordbox-collection-manager")
    except Exception:
        return "0.2.0"


__version__ = _get_version()

__all__ = ["__version__", "ensure_rekordbox_not_running", "is_rekordbox_running"]

