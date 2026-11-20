#!/usr/bin/env python3
"""
Desktop Version Management CLI for Rekordbox Collection Manager.

Manages Semantic Versioning in pyproject.toml, synchronizes uv.lock,
and automates git commits and release tagging.
"""

import argparse
import datetime
import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

# Paths
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"
UV_LOCK_PATH = PROJECT_ROOT / "uv.lock"
CHANGELOG_PATH = PROJECT_ROOT / "CHANGELOG.md"
BUILD_SCRIPT_PATH = SCRIPT_DIR / "build_app.sh"
DIST_DIR = PROJECT_ROOT / "dist"

try:
    from scripts.changelog import (
        CATEGORIES_ORDER,
        categorize_commits,
        get_commits_for_range,
        get_desktop_tags,
        rotate_changelog,
    )
except ImportError:
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from scripts.changelog import (
        CATEGORIES_ORDER,
        categorize_commits,
        get_commits_for_range,
        get_desktop_tags,
        rotate_changelog,
    )


REQUIRED_TOOLS = ("uv", "clang", "ditto", "gh")


def run_git(cmd: list[str], cwd: Path = PROJECT_ROOT) -> str:
    """Run git command and return stripped standard output."""
    try:
        res = subprocess.run(
            ["git"] + cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip()
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Git command failed: {' '.join(cmd)}\n{e.stderr.strip()}") from e


def check_required_tools(tools: tuple[str, ...] = REQUIRED_TOOLS) -> list[str]:
    """Verify presence of required CLI tools and gh auth status. Returns list of error messages."""
    errors: list[str] = []
    for tool in tools:
        if not shutil.which(tool):
            errors.append(f"Missing required tool: '{tool}' was not found in PATH.")

    if shutil.which("gh"):
        try:
            res = subprocess.run(
                ["gh", "auth", "status"],
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode != 0:
                err_detail = (res.stderr or res.stdout).strip()
                errors.append(f"GitHub CLI ('gh') is not authenticated: {err_detail}")
        except Exception as e:
            errors.append(f"Failed to check GitHub CLI auth status: {e}")
    return errors


def check_git_clean(cwd: Path = PROJECT_ROOT) -> list[str]:
    """Verify that working tree has no uncommitted changes or untracked files."""
    try:
        status = run_git(["status", "--porcelain"], cwd=cwd)
        if status:
            return ["Git working tree is dirty. Please commit or stash changes before releasing."]
        return []
    except Exception as e:
        return [f"Failed to check git working tree status: {e}"]


def check_git_branch(
    expected_branch: str = "main",
    check_remote: bool = True,
    cwd: Path = PROJECT_ROOT,
) -> list[str]:
    """Verify that current branch is expected_branch and is in sync with origin."""
    errors: list[str] = []
    try:
        curr_branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
        if curr_branch != expected_branch:
            errors.append(
                f"Current branch is '{curr_branch}', but releases must be cut from '{expected_branch}'."
            )
            return errors
    except Exception as e:
        return [f"Failed to determine current git branch: {e}"]

    if check_remote:
        try:
            run_git(["fetch", "origin", expected_branch], cwd=cwd)
            local_sha = run_git(["rev-parse", "HEAD"], cwd=cwd)
            remote_sha = run_git(["rev-parse", f"origin/{expected_branch}"], cwd=cwd)
            if local_sha != remote_sha:
                counts = run_git(["rev-list", "--left-right", "--count", f"HEAD...origin/{expected_branch}"], cwd=cwd)
                ahead, behind = counts.split()
                errors.append(
                    f"Local branch '{expected_branch}' is not in sync with origin/{expected_branch} "
                    f"({ahead} ahead, {behind} behind)."
                )
        except Exception as e:
            errors.append(f"Failed to verify upstream sync with origin/{expected_branch}: {e}")

    return errors


def check_tag_collision(
    version: str,
    check_remote: bool = True,
    cwd: Path = PROJECT_ROOT,
) -> list[str]:
    """Verify that tag v<version> does not exist locally or on remote origin."""
    errors: list[str] = []
    tag_name = f"v{version.lstrip('v')}"
    try:
        local_tags = run_git(["tag", "-l", tag_name], cwd=cwd)
        if local_tags:
            errors.append(f"Local tag '{tag_name}' already exists.")
    except Exception as e:
        errors.append(f"Failed to check local git tags: {e}")

    if check_remote:
        try:
            remote_tag_output = run_git(["ls-remote", "--tags", "origin", f"refs/tags/{tag_name}"], cwd=cwd)
            if remote_tag_output:
                errors.append(f"Remote tag '{tag_name}' already exists on origin.")
        except Exception as e:
            errors.append(f"Failed to check remote tags on origin: {e}")

    return errors


def check_preflight_conditions(
    version: str,
    expected_branch: str = "main",
    check_remote: bool = True,
    cwd: Path = PROJECT_ROOT,
) -> list[str]:
    """Run all pre-flight release checks and return consolidated list of errors."""
    errors: list[str] = []
    errors.extend(check_required_tools())
    errors.extend(check_git_clean(cwd=cwd))
    errors.extend(check_git_branch(expected_branch=expected_branch, check_remote=check_remote, cwd=cwd))
    errors.extend(check_tag_collision(version=version, check_remote=check_remote, cwd=cwd))
    return errors


def get_git_checkpoint(cwd: Path = PROJECT_ROOT) -> str:
    """Get current git commit SHA to serve as a rollback checkpoint."""
    return run_git(["rev-parse", "HEAD"], cwd=cwd)


def rollback_to_checkpoint(
    checkpoint_sha: str,
    tag_name: str | None = None,
    cwd: Path = PROJECT_ROOT,
) -> None:
    """
    Roll back working tree and project files to checkpoint_sha.
    Deletes local tag if it was created.
    """
    try:
        # Revert unstaged changes to tracked files
        subprocess.run(["git", "checkout", "--", "."], cwd=cwd, check=True, capture_output=True)
        # In case a commit was created after checkpoint_sha:
        current_sha = run_git(["rev-parse", "HEAD"], cwd=cwd)
        if current_sha != checkpoint_sha:
            subprocess.run(["git", "reset", "--hard", checkpoint_sha], cwd=cwd, check=True, capture_output=True)
        # Delete local tag if created
        if tag_name:
            tags = run_git(["tag", "-l", tag_name], cwd=cwd)
            if tags:
                subprocess.run(["git", "tag", "-d", tag_name], cwd=cwd, check=True, capture_output=True)
    except Exception as e:
        print(f"⚠️  Warning: Failed during rollback to checkpoint {checkpoint_sha}: {e}", file=sys.stderr)


class BuildArtifactInfo(NamedTuple):
    app_path: Path
    zip_path: Path
    zip_size: str
    sha256: str


def compute_sha256(file_path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def get_expected_zip_path(version: str, dist_dir: Path = DIST_DIR) -> Path:
    """Return expected release zip path for a given version."""
    clean_ver = version.lstrip("v")
    return dist_dir / f"Rekordbox-Collection-Manager-v{clean_ver}-macOS-arm64.zip"


def build_and_package_app(
    version: str,
    cwd: Path = PROJECT_ROOT,
    build_script: Path = BUILD_SCRIPT_PATH,
    dist_dir: Path = DIST_DIR,
    dry_run: bool = False,
) -> BuildArtifactInfo:
    """
    Execute scripts/build_app.sh --package to compile standalone .app and create .zip archive.
    Returns BuildArtifactInfo with paths, human-readable size, and sha256 checksum.
    """
    expected_zip = get_expected_zip_path(version, dist_dir=dist_dir)
    app_path = dist_dir / "Rekordbox Collection Manager.app"

    if dry_run:
        return BuildArtifactInfo(
            app_path=app_path,
            zip_path=expected_zip,
            zip_size="[dry-run]",
            sha256="[dry-run-sha256]",
        )

    if not build_script.exists():
        raise FileNotFoundError(f"Build script not found at {build_script}")

    print("🔨 Compiling standalone macOS bundle & packaging release zip...")
    res = subprocess.run(
        ["bash", str(build_script), "--package"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"Build and packaging script failed with code {res.returncode}:\n{res.stderr.strip() or res.stdout.strip()}"
        )

    if not expected_zip.exists():
        raise FileNotFoundError(
            f"Expected release archive was not found at {expected_zip} after build."
        )

    size_bytes = expected_zip.stat().st_size
    size_mb = size_bytes / (1024 * 1024)
    zip_size_str = f"{size_mb:.1f} MB"
    checksum = compute_sha256(expected_zip)

    print(f"📦 Successfully packaged release archive: {expected_zip.name} ({zip_size_str})")
    print(f"🔑 SHA-256: {checksum}")

    return BuildArtifactInfo(
        app_path=app_path,
        zip_path=expected_zip,
        zip_size=zip_size_str,
        sha256=checksum,
    )


def extract_unreleased_notes(changelog_path: Path = CHANGELOG_PATH) -> str:
    """Extract body text under ## [Unreleased] from CHANGELOG.md if present."""
    if not changelog_path.exists():
        return ""
    text = changelog_path.read_text(encoding="utf-8")
    unreleased_pattern = re.compile(
        r"## \[Unreleased\][^\n]*\n(?P<body>.*?)(?=\n## \[\d|\n\[Unreleased\]:|\Z)",
        re.DOTALL,
    )
    match = unreleased_pattern.search(text)
    if match:
        return match.group("body").strip()
    return ""


def extract_release_notes(version: str, changelog_path: Path = CHANGELOG_PATH) -> str:
    """Extract release notes for version from CHANGELOG.md."""
    if not changelog_path.exists():
        return ""
    clean_ver = version.lstrip("v")
    text = changelog_path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"## \[{re.escape(clean_ver)}\][^\n]*\n(?P<body>.*?)(?=\n## \[\d|\n\[|\Z)",
        re.DOTALL,
    )
    match = pattern.search(text)
    if match:
        return match.group("body").strip()
    return ""


def sync_changelog_for_release(
    version: str,
    date_str: str | None = None,
    cwd: Path = PROJECT_ROOT,
    changelog_path: Path = CHANGELOG_PATH,
    dry_run: bool = False,
) -> tuple[bool, str]:
    """
    Ensure [Unreleased] is populated (auto-populating from desktop conventional commits if empty),
    rotate CHANGELOG.md for new_version, and return (updated_bool, release_notes_markdown).
    """
    clean_ver = version.lstrip("v")
    if date_str is None:
        date_str = datetime.date.today().isoformat()

    unreleased_notes = extract_unreleased_notes(changelog_path=changelog_path)

    # If unreleased notes are empty, auto-extract commits since last desktop tag
    if not unreleased_notes and not dry_run and changelog_path.exists():
        tags = get_desktop_tags(cwd=cwd)
        if tags:
            latest_tag = tags[-1]
            commits = get_commits_for_range(f"{latest_tag}..HEAD", cwd=cwd)
            categorized = categorize_commits(commits, target="desktop")
            if categorized:
                lines = []
                for cat in CATEGORIES_ORDER:
                    items = categorized.get(cat)
                    if items:
                        lines.append(f"### {cat}\n")
                        for item in items:
                            lines.append(item)
                        lines.append("")
                auto_body = "\n".join(lines).strip()
                if auto_body:
                    content = changelog_path.read_text(encoding="utf-8")
                    unreleased_pattern = re.compile(
                        r"(## \[Unreleased\][^\n]*\n)(?P<body>.*?)(?=\n## \[(?P<next_ver>\d+\.\d+(?:\.\d+)?)\])",
                        re.DOTALL,
                    )
                    match = unreleased_pattern.search(content)
                    if match:
                        new_content = unreleased_pattern.sub(
                            rf"\g<1>\n{auto_body}\n",
                            content,
                            count=1,
                        )
                        changelog_path.write_text(new_content, encoding="utf-8")

    # Rotate CHANGELOG.md
    updated = rotate_changelog(
        new_version=clean_ver,
        date_str=date_str,
        cwd=cwd,
        changelog_path=changelog_path,
        dry_run=dry_run,
    )

    release_notes = extract_release_notes(clean_ver, changelog_path=changelog_path)
    if not release_notes and dry_run:
        if unreleased_notes:
            release_notes = unreleased_notes
        else:
            tags = get_desktop_tags(cwd=cwd)
            if tags:
                latest_tag = tags[-1]
                commits = get_commits_for_range(f"{latest_tag}..HEAD", cwd=cwd)
                categorized = categorize_commits(commits, target="desktop")
                lines = []
                for cat in CATEGORIES_ORDER:
                    items = categorized.get(cat)
                    if items:
                        lines.append(f"### {cat}\n")
                        for item in items:
                            lines.append(item)
                        lines.append("")
                release_notes = "\n".join(lines).strip()
        if not release_notes:
            release_notes = "- Routine updates and maintenance."

    return updated, release_notes


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


def git_commit_and_tag(
    new_version: str,
    create_tag: bool,
    uv_lock_updated: bool,
    changelog_updated: bool = False,
) -> None:
    """Commit changes to git and optionally create an annotated tag."""
    commit_msg = f"chore(desktop): bump version to {new_version}"
    try:
        files_to_add = [str(PYPROJECT_PATH)]
        if uv_lock_updated and UV_LOCK_PATH.exists():
            files_to_add.append(str(UV_LOCK_PATH))
        if changelog_updated and CHANGELOG_PATH.exists():
            files_to_add.append(str(CHANGELOG_PATH))

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
        "--no-changelog",
        action="store_true",
        help="Skip rotating CHANGELOG.md upon version bump",
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

    changelog_updated = False
    if not args.no_changelog and CHANGELOG_PATH.exists():
        try:
            changelog_updated = rotate_changelog(
                new_version,
                cwd=PROJECT_ROOT,
                changelog_path=CHANGELOG_PATH,
                dry_run=args.dry_run,
            )
        except Exception as e:
            print(f"⚠️  Warning: Failed to rotate CHANGELOG.md: {e}", file=sys.stderr)

    if not args.dry_run:
        print("✅ pyproject.toml updated successfully.")
        if args.commit or args.tag:
            git_commit_and_tag(
                new_version,
                create_tag=args.tag,
                uv_lock_updated=uv_lock_updated,
                changelog_updated=changelog_updated,
            )


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
