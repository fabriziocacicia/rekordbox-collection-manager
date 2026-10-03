#!/usr/bin/env python3
"""
iOS Version Management CLI for Rekordbox Companion.

Manages Semantic Versioning (MARKETING_VERSION / CFBundleShortVersionString)
and build numbers (CURRENT_PROJECT_VERSION / CFBundleVersion) in Xcode.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Paths
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
PBXPROJ_PATH = (
    PROJECT_ROOT
    / "ios"
    / "RekordboxCompanion"
    / "RekordboxCompanion.xcodeproj"
    / "project.pbxproj"
)


def get_current_versions(content: str) -> tuple[str, int]:
    """Extract (marketing_version, current_project_version) from pbxproj content."""
    m_match = re.search(r"MARKETING_VERSION\s*=\s*([^;]+);", content)
    b_match = re.search(r"CURRENT_PROJECT_VERSION\s*=\s*([^;]+);", content)

    if not m_match:
        raise ValueError("Could not find MARKETING_VERSION in project.pbxproj")
    if not b_match:
        raise ValueError("Could not find CURRENT_PROJECT_VERSION in project.pbxproj")

    marketing_version = m_match.group(1).strip().strip('"').strip("'")
    build_version = int(b_match.group(1).strip().strip('"').strip("'"))
    return marketing_version, build_version


def validate_semver(version_str: str) -> tuple[int, int, int]:
    """Validate and parse semantic version string (MAJOR.MINOR.PATCH)."""
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)$", version_str.strip())
    if not match:
        raise ValueError(
            f"Invalid SemVer string '{version_str}'. iOS requires 3 dot-separated integers (e.g., 0.1.0 or 1.2.3)."
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


def update_pbxproj(new_marketing: str, new_build: int, dry_run: bool = False) -> None:
    """Update all occurrences of MARKETING_VERSION and CURRENT_PROJECT_VERSION in pbxproj."""
    content = PBXPROJ_PATH.read_text(encoding="utf-8")

    # Replace MARKETING_VERSION
    new_content = re.sub(
        r"(MARKETING_VERSION\s*=\s*)([^;]+)(;)",
        rf"\g<1>{new_marketing}\g<3>",
        content,
    )

    # Replace CURRENT_PROJECT_VERSION
    new_content = re.sub(
        r"(CURRENT_PROJECT_VERSION\s*=\s*)([^;]+)(;)",
        rf"\g<1>{new_build}\g<3>",
        new_content,
    )

    if dry_run:
        print("[dry-run] Changes were not written to disk.")
        return

    # Atomic write
    tmp_path = PBXPROJ_PATH.with_suffix(".tmp")
    tmp_path.write_text(new_content, encoding="utf-8")
    tmp_path.replace(PBXPROJ_PATH)


def git_commit_and_tag(new_marketing: str, new_build: int, create_tag: bool) -> None:
    """Commit changes to git and optionally create a git tag."""
    commit_msg = f"chore(ios): bump version to {new_marketing} (build {new_build})"
    try:
        subprocess.run(
            ["git", "add", str(PBXPROJ_PATH)],
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
            tag_name = f"ios-v{new_marketing}"
            subprocess.run(
                ["git", "tag", "-a", tag_name, "-m", f"Rekordbox Companion v{new_marketing} (build {new_build})"],
                cwd=PROJECT_ROOT,
                check=True,
            )
            print(f"🏷️  Tagged: {tag_name}")
    except subprocess.CalledProcessError as e:
        print(f"❌ Git command failed: {e}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rekordbox Companion iOS Semantic Versioning Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  %(prog)s                    # Show current version
  %(prog)s patch              # Bump patch: 0.1.0 -> 0.1.1 (build incremented)
  %(prog)s minor              # Bump minor: 0.1.0 -> 0.2.0 (build incremented)
  %(prog)s major              # Bump major: 0.1.0 -> 1.0.0 (build incremented)
  %(prog)s build              # Keep SemVer, bump build number: build 1 -> 2
  %(prog)s set 1.0.0          # Set specific SemVer
  %(prog)s patch --commit --tag # Bump patch, commit to git and create tag ios-vX.Y.Z
""",
    )

    parser.add_argument(
        "action",
        nargs="?",
        default="current",
        choices=["current", "show", "patch", "minor", "major", "build", "set"],
        help="Action to perform (default: current)",
    )
    parser.add_argument(
        "version",
        nargs="?",
        help="Target version when action is 'set' (e.g. 1.2.3)",
    )
    parser.add_argument(
        "--build",
        type=int,
        help="Explicit build number to set",
    )
    parser.add_argument(
        "--reset-build",
        action="store_true",
        help="Reset build number to 1 instead of incrementing",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Automatically git commit the version bump",
    )
    parser.add_argument(
        "--tag",
        action="store_true",
        help="Automatically create an annotated git tag (e.g. ios-v0.1.1)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without modifying project files",
    )

    args = parser.parse_args()

    if not PBXPROJ_PATH.exists():
        print(f"❌ Error: Cannot find Xcode project at {PBXPROJ_PATH}", file=sys.stderr)
        sys.exit(1)

    content = PBXPROJ_PATH.read_text(encoding="utf-8")
    curr_marketing, curr_build = get_current_versions(content)

    if args.action in ("current", "show"):
        print(f"📱 Rekordbox Companion iOS:")
        print(f"   Marketing Version (SemVer): {curr_marketing}")
        print(f"   Build Number (Internal):   {curr_build}")
        return

    # Determine new marketing version
    if args.action in ("patch", "minor", "major"):
        new_marketing = bump_semver(curr_marketing, args.action)
    elif args.action == "set":
        if not args.version:
            print("❌ Error: Must specify a version string when using 'set' (e.g., ./scripts/ios_version.py set 0.2.0)", file=sys.stderr)
            sys.exit(1)
        validate_semver(args.version)
        new_marketing = args.version
    elif args.action == "build":
        new_marketing = curr_marketing
    else:
        new_marketing = curr_marketing

    # Determine new build number
    if args.build is not None:
        new_build = args.build
    elif args.reset_build and args.action in ("minor", "major", "set"):
        new_build = 1
    elif args.action == "current":
        new_build = curr_build
    else:
        new_build = curr_build + 1

    print("📱 Rekordbox Companion Version Bump:")
    print(f"   Marketing Version: {curr_marketing} -> {new_marketing}")
    print(f"   Build Number:      {curr_build} -> {new_build}")

    update_pbxproj(new_marketing, new_build, dry_run=args.dry_run)

    if not args.dry_run:
        print("✅ project.pbxproj updated successfully.")
        if args.commit or args.tag:
            git_commit_and_tag(new_marketing, new_build, create_tag=args.tag)


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
