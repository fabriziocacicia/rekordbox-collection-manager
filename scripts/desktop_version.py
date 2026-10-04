#!/usr/bin/env python3
"""
Desktop Version Management CLI for Rekordbox Collection Manager.

Manages Semantic Versioning in pyproject.toml, synchronizes uv.lock,
and automates git commits and release tagging.
"""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Paths
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"
UV_LOCK_PATH = PROJECT_ROOT / "uv.lock"


def get_current_version(content: str) -> str:
    """Extract version from pyproject.toml content."""
    match = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
    if not match:
        raise ValueError("Could not find 'version = ...' in pyproject.toml")
    return match.group(1).strip()


def validate_semver(version_str: str) -> tuple[int, int, int]:
    """Validate and parse semantic version string (MAJOR.MINOR.PATCH)."""
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)$", version_str.strip())
    if not match:
        raise ValueError(
            f"Invalid SemVer string '{version_str}'. Expected format: MAJOR.MINOR.PATCH (e.g., 0.9.0 or 1.0.0)."
        )
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def bump_semver(current: str, bump_type: str) -> str:
    """Bump major, minor, or patch component of current SemVer string."""
    major, minor, patch = validate_semver(current)
    if bump_type == "major":
        return f"{major + 1}.0.0"
    elif bump_type == "minor":
        return f"{major}.{minor + 1}.0"
    elif bump_type == "patch":
        return f"{major}.{minor}.{patch + 1}"
    else:
        raise ValueError(f"Unknown bump type: {bump_type}")


def update_pyproject(new_version: str, dry_run: bool = False) -> None:
    """Update version in pyproject.toml."""
    content = PYPROJECT_PATH.read_text(encoding="utf-8")

    new_content, count = re.subn(
        r'^(version\s*=\s*["\'])[^"\']+(["\'])',
        rf"\g<1>{new_version}\g<2>",
        content,
        count=1,
        flags=re.MULTILINE,
    )

    if count == 0:
        raise ValueError("Failed to replace version in pyproject.toml")

    if dry_run:
        print("[dry-run] Changes were not written to pyproject.toml.")
        return

    tmp_path = PYPROJECT_PATH.with_suffix(".tmp")
    tmp_path.write_text(new_content, encoding="utf-8")
    tmp_path.replace(PYPROJECT_PATH)


def update_uv_lock(dry_run: bool = False) -> bool:
    """Update uv.lock if uv is installed. Returns True if uv.lock was updated."""
    if dry_run:
        print("[dry-run] uv.lock was not updated.")
        return False

    uv_bin = shutil.which("uv")
    if not uv_bin:
        print("⚠️  Warning: 'uv' executable not found; skipping uv.lock synchronization.")
        return False

    try:
        subprocess.run([uv_bin, "lock"], cwd=PROJECT_ROOT, check=True, capture_output=True)
        print("🔒 Synchronized uv.lock.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"⚠️  Warning: 'uv lock' failed ({e}); uv.lock might be out of date.", file=sys.stderr)
        return False


def git_commit_and_tag(new_version: str, create_tag: bool, uv_lock_updated: bool) -> None:
    """Commit changes to git and optionally create an annotated tag."""
    commit_msg = f"chore(desktop): bump version to {new_version}"
    try:
        files_to_add = [str(PYPROJECT_PATH)]
        if uv_lock_updated and UV_LOCK_PATH.exists():
            files_to_add.append(str(UV_LOCK_PATH))

        subprocess.run(
            ["git", "add"] + files_to_add,
            cwd=PROJECT_ROOT,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=PROJECT_ROOT,
            check=True,
        )
        print(f"📦 Committed: '{commit_msg}'")

        if create_tag:
            tag_name = f"v{new_version}"
            subprocess.run(
                ["git", "tag", "-a", tag_name, "-m", f"Rekordbox Collection Manager v{new_version}"],
                cwd=PROJECT_ROOT,
                check=True,
            )
            print(f"🏷️  Tagged: {tag_name}")
    except subprocess.CalledProcessError as e:
        print(f"❌ Git command failed: {e}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rekordbox Collection Manager Desktop Semantic Versioning Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  %(prog)s                      # Show current desktop version
  %(prog)s patch                # Bump patch: 0.9.0 -> 0.9.1
  %(prog)s minor                # Bump minor: 0.9.0 -> 0.10.0
  %(prog)s major                # Bump major: 0.9.0 -> 1.0.0
  %(prog)s set 1.0.0            # Set specific SemVer
  %(prog)s patch --commit --tag # Bump patch, commit to git, and create tag vX.Y.Z
""",
    )

    parser.add_argument(
        "action",
        nargs="?",
        default="current",
        choices=["current", "show", "patch", "minor", "major", "set"],
        help="Action to perform (default: current)",
    )
    parser.add_argument(
        "version",
        nargs="?",
        help="Target version when action is 'set' (e.g., 1.0.0)",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Automatically git commit the version bump (and uv.lock)",
    )
    parser.add_argument(
        "--tag",
        action="store_true",
        help="Automatically create an annotated git tag (e.g., v0.9.1)",
    )
    parser.add_argument(
        "--no-uv-lock",
        action="store_true",
        help="Skip synchronizing uv.lock",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without modifying project files",
    )

    args = parser.parse_args()

    if not PYPROJECT_PATH.exists():
        print(f"❌ Error: Cannot find pyproject.toml at {PYPROJECT_PATH}", file=sys.stderr)
        sys.exit(1)

    content = PYPROJECT_PATH.read_text(encoding="utf-8")
    curr_version = get_current_version(content)

    if args.action in ("current", "show"):
        print(f"💻 Rekordbox Collection Manager Desktop:")
        print(f"   Version (SemVer): {curr_version}")
        return

    # Determine new version
    if args.action in ("patch", "minor", "major"):
        new_version = bump_semver(curr_version, args.action)
    elif args.action == "set":
        if not args.version:
            print("❌ Error: Must specify a version string when using 'set' (e.g., ./scripts/desktop_version.py set 1.0.0)", file=sys.stderr)
            sys.exit(1)
        validate_semver(args.version)
        new_version = args.version
    else:
        new_version = curr_version

    print("💻 Rekordbox Collection Manager Desktop Version Bump:")
    print(f"   Version: {curr_version} -> {new_version}")

    update_pyproject(new_version, dry_run=args.dry_run)

    uv_lock_updated = False
    if not args.dry_run and not args.no_uv_lock:
        uv_lock_updated = update_uv_lock(dry_run=args.dry_run)

    if not args.dry_run:
        print("✅ pyproject.toml updated successfully.")
        if args.commit or args.tag:
            git_commit_and_tag(new_version, create_tag=args.tag, uv_lock_updated=uv_lock_updated)


if __name__ == "__main__":
    try:
        main()
    except ValueError as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        print(f"❌ Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)
