#!/usr/bin/env python3
"""
iOS Version & Release Management CLI for Rekordbox Companion.

Manages Semantic Versioning (MARKETING_VERSION) and build numbers
(CURRENT_PROJECT_VERSION) in Xcode, synchronizes ios/CHANGELOG.md,
and automates the 7-step safe release pipeline with .ipa packaging.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

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
IOS_CHANGELOG_PATH = PROJECT_ROOT / "ios" / "CHANGELOG.md"
BUILD_IOS_SCRIPT_PATH = SCRIPT_DIR / "build_ios.sh"
DIST_DIR = PROJECT_ROOT / "dist"

try:
    from scripts.changelog import (
        get_ios_tags,
        rotate_changelog,
        run_git,
    )
except ImportError:
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from scripts.changelog import (
        get_ios_tags,
        rotate_changelog,
        run_git,
    )


REQUIRED_TOOLS = ("xcodebuild", "ditto", "gh")


class IpaArtifact(NamedTuple):
    ipa_path: Path
    ipa_size: str
    sha256: str


# ---------------------------------------------------------------------------
# Pre-Flight Verifications
# ---------------------------------------------------------------------------

def check_required_tools(tools: tuple[str, ...] = REQUIRED_TOOLS) -> list[str]:
    """Verify presence of required CLI tools and gh auth status."""
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
    """Verify that tag ios-v<version> does not exist locally or on remote origin."""
    errors: list[str] = []
    clean_ver = version.replace("ios-v", "").lstrip("v")
    tag_name = f"ios-v{clean_ver}"
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


def extract_unreleased_notes(changelog_path: Path = IOS_CHANGELOG_PATH) -> str:
    """Extract markdown text from ## [Unreleased] section of changelog."""
    if not changelog_path.exists():
        return ""
    text = changelog_path.read_text(encoding="utf-8")
    unreleased_pattern = re.compile(
        r"## \[Unreleased\]\s*\n(?P<body>.*?)(?=\n## \[\d|\Z)",
        re.DOTALL,
    )
    match = unreleased_pattern.search(text)
    if match:
        return match.group("body").strip()
    return ""


def extract_release_notes(version: str, changelog_path: Path = IOS_CHANGELOG_PATH) -> str:
    """Extract release notes for version from changelog."""
    if not changelog_path.exists():
        return ""
    clean_ver = version.replace("ios-v", "").lstrip("v")
    text = changelog_path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"## \[{re.escape(clean_ver)}\][^\n]*\n(?P<body>.*?)(?=\n## \[\d|\n\[|\Z)",
        re.DOTALL,
    )
    match = pattern.search(text)
    if match:
        return match.group("body").strip()
    return ""


def check_unreleased_notes(changelog_path: Path = IOS_CHANGELOG_PATH) -> list[str]:
    """Verify that changelog exists and has non-empty ## [Unreleased] notes."""
    if not changelog_path.exists():
        return [f"Changelog file not found at {changelog_path}"]
    notes = extract_unreleased_notes(changelog_path=changelog_path)
    if not notes:
        return [
            f"The '## [Unreleased]' section in {changelog_path.name} is empty. "
            f"Please document release changes under '## [Unreleased]' before releasing."
        ]
    return []


def check_preflight_conditions(
    version: str,
    expected_branch: str = "main",
    check_remote: bool = True,
    cwd: Path = PROJECT_ROOT,
    changelog_path: Path = IOS_CHANGELOG_PATH,
) -> list[str]:
    """Run all pre-flight release checks and return consolidated list of errors."""
    errors: list[str] = []
    errors.extend(check_required_tools())
    errors.extend(check_git_clean(cwd=cwd))
    errors.extend(check_git_branch(expected_branch=expected_branch, check_remote=check_remote, cwd=cwd))
    errors.extend(check_tag_collision(version=version, check_remote=check_remote, cwd=cwd))
    errors.extend(check_unreleased_notes(changelog_path=changelog_path))
    return errors


# ---------------------------------------------------------------------------
# Checkpoints & Rollbacks
# ---------------------------------------------------------------------------

def get_git_checkpoint(cwd: Path = PROJECT_ROOT) -> str:
    """Get current git commit SHA to serve as a rollback checkpoint."""
    return run_git(["rev-parse", "HEAD"], cwd=cwd)


def rollback_to_checkpoint(
    checkpoint_sha: str,
    tag_name: str | None = None,
    cwd: Path = PROJECT_ROOT,
) -> None:
    """Roll back working tree and project files to checkpoint_sha."""
    try:
        subprocess.run(["git", "checkout", "--", "."], cwd=cwd, check=True, capture_output=True)
        current_sha = run_git(["rev-parse", "HEAD"], cwd=cwd)
        if current_sha != checkpoint_sha:
            subprocess.run(["git", "reset", "--hard", checkpoint_sha], cwd=cwd, check=True, capture_output=True)
        if tag_name:
            tags = run_git(["tag", "-l", tag_name], cwd=cwd)
            if tags:
                subprocess.run(["git", "tag", "-d", tag_name], cwd=cwd, check=True, capture_output=True)
    except Exception as e:
        print(f"⚠️  Warning: Failed during rollback to checkpoint {checkpoint_sha}: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Version Parsing & Xcode pbxproj Management
# ---------------------------------------------------------------------------

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


def update_pbxproj(
    new_marketing: str,
    new_build: int,
    pbxproj_path: Path = PBXPROJ_PATH,
    dry_run: bool = False,
) -> None:
    """Update all occurrences of MARKETING_VERSION and CURRENT_PROJECT_VERSION in pbxproj."""
    content = pbxproj_path.read_text(encoding="utf-8")

    new_content = re.sub(
        r"(MARKETING_VERSION\s*=\s*)([^;]+)(;)",
        rf"\g<1>{new_marketing}\g<3>",
        content,
    )
    new_content = re.sub(
        r"(CURRENT_PROJECT_VERSION\s*=\s*)([^;]+)(;)",
        rf"\g<1>{new_build}\g<3>",
        new_content,
    )

    if dry_run:
        print("[dry-run] Changes were not written to disk.")
        return

    tmp_path = pbxproj_path.with_suffix(".tmp")
    tmp_path.write_text(new_content, encoding="utf-8")
    tmp_path.replace(pbxproj_path)


# ---------------------------------------------------------------------------
# Build & Release Operations
# ---------------------------------------------------------------------------

def build_and_package_ios(
    version: str,
    cwd: Path = PROJECT_ROOT,
    build_script: Path = BUILD_IOS_SCRIPT_PATH,
    dist_dir: Path = DIST_DIR,
    dry_run: bool = False,
) -> IpaArtifact:
    """Execute scripts/build_ios.sh --clean --package and verify output .ipa archive."""
    clean_ver = version.replace("ios-v", "").lstrip("v")
    expected_ipa_name = f"RekordboxCompanion-v{clean_ver}.ipa"
    expected_ipa_path = dist_dir / expected_ipa_name

    if dry_run:
        return IpaArtifact(
            ipa_path=expected_ipa_path,
            ipa_size="~800K",
            sha256="dry-run-sha256-placeholder",
        )

    if not build_script.exists():
        raise FileNotFoundError(f"iOS build script not found at {build_script}")

    print(f"📦 Compiling and packaging {expected_ipa_name} via {build_script.name}...")
    env = dict(os.environ, APP_VERSION=clean_ver)
    res = subprocess.run(
        [str(build_script), "--clean", "--package"],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        err_msg = res.stderr.strip() or res.stdout.strip()
        raise RuntimeError(f"iOS build failed with exit code {res.returncode}:\n{err_msg}")

    if not expected_ipa_path.exists():
        raise FileNotFoundError(f"Expected .ipa archive not found at {expected_ipa_path}")

    size_bytes = expected_ipa_path.stat().st_size
    size_mb = size_bytes / (1024 * 1024)
    ipa_size = f"{size_mb:.1f}M" if size_mb >= 1.0 else f"{size_bytes // 1024}K"
    sha = hashlib.sha256(expected_ipa_path.read_bytes()).hexdigest()

    print(f"✅ Packaged {expected_ipa_name} ({ipa_size}) [SHA256: {sha[:16]}...]")
    return IpaArtifact(
        ipa_path=expected_ipa_path,
        ipa_size=ipa_size,
        sha256=sha,
    )


def sync_changelog_for_release(
    version: str,
    date_str: str | None = None,
    cwd: Path = PROJECT_ROOT,
    changelog_path: Path = IOS_CHANGELOG_PATH,
    dry_run: bool = False,
) -> tuple[bool, str]:
    """Ensure [Unreleased] is populated in ios/CHANGELOG.md, rotate, and return release notes."""
    clean_ver = version.replace("ios-v", "").lstrip("v")
    if date_str is None:
        date_str = datetime.date.today().isoformat()

    unreleased_notes = extract_unreleased_notes(changelog_path=changelog_path)
    if not unreleased_notes:
        raise ValueError(
            f"The '## [Unreleased]' section in {changelog_path.name} is empty. "
            f"Please document release changes under '## [Unreleased]' before releasing."
        )

    updated = rotate_changelog(
        new_version=clean_ver,
        date_str=date_str,
        target="ios",
        cwd=cwd,
        changelog_path=changelog_path,
        dry_run=dry_run,
    )

    release_notes = extract_release_notes(clean_ver, changelog_path=changelog_path)
    if not release_notes and dry_run:
        release_notes = unreleased_notes

    return updated, release_notes


def git_commit_and_tag(
    new_marketing: str,
    new_build: int,
    create_tag: bool = True,
    changelog_updated: bool = False,
    cwd: Path = PROJECT_ROOT,
    pbxproj_path: Path = PBXPROJ_PATH,
    changelog_path: Path = IOS_CHANGELOG_PATH,
) -> None:
    """Commit changes to git and optionally create an annotated tag."""
    commit_msg = f"chore(ios): bump version to {new_marketing} (build {new_build})"
    try:
        files_to_add = [str(pbxproj_path)]
        if changelog_updated and changelog_path.exists():
            files_to_add.append(str(changelog_path))

        subprocess.run(["git", "add"] + files_to_add, cwd=cwd, check=True)
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=cwd, check=True)
        print(f"📦 Committed: '{commit_msg}'")

        if create_tag:
            tag_name = f"ios-v{new_marketing}"
            subprocess.run(
                ["git", "tag", "-a", tag_name, "-m", f"Rekordbox Companion iOS v{new_marketing}"],
                cwd=cwd,
                check=True,
            )
            print(f"🏷️  Tagged: {tag_name}")
    except subprocess.CalledProcessError as e:
        print(f"❌ Git command failed: {e}", file=sys.stderr)
        raise


def publish_git_push(version: str, cwd: Path = PROJECT_ROOT) -> None:
    """Push current branch (main) and new tag to origin."""
    clean_ver = version.replace("ios-v", "").lstrip("v")
    tag_name = f"ios-v{clean_ver}"
    curr_branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
    print(f"📤 Pushing branch '{curr_branch}' and tag '{tag_name}' to origin...")
    run_git(["push", "origin", curr_branch], cwd=cwd)
    run_git(["push", "origin", tag_name], cwd=cwd)
    print("✅ Pushed commits and tag to origin.")


def publish_github_release(
    version: str,
    ipa_path: Path,
    release_notes: str,
    draft: bool = False,
    dist_dir: Path = DIST_DIR,
    cwd: Path = PROJECT_ROOT,
) -> str:
    """Publish GitHub release using gh CLI attaching the .ipa archive."""
    clean_ver = version.replace("ios-v", "").lstrip("v")
    tag_name = f"ios-v{clean_ver}"
    dist_dir.mkdir(parents=True, exist_ok=True)
    notes_file = dist_dir / f"RELEASE_NOTES_ios_{clean_ver}.md"
    notes_file.write_text(release_notes, encoding="utf-8")

    cmd = [
        "gh",
        "release",
        "create",
        tag_name,
        str(ipa_path),
        "--title",
        f"Rekordbox Companion iOS v{clean_ver}",
        "--notes-file",
        str(notes_file),
    ]
    if draft:
        cmd.append("--draft")

    print(f"📢 Publishing GitHub release {tag_name}...")
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        err_msg = res.stderr.strip() or res.stdout.strip()
        print(f"\n❌ Error: Failed to publish GitHub release ({err_msg}).", file=sys.stderr)
        print("⚠️  Git commit and tag were already pushed to origin.", file=sys.stderr)
        print("   To retry publishing the GitHub release without rebuilding, run:", file=sys.stderr)
        print(f"   {' '.join(cmd)}\n", file=sys.stderr)
        raise RuntimeError(f"GitHub release creation failed: {err_msg}")

    release_url = res.stdout.strip()
    return release_url


def cleanup_release_artifacts(dist_dir: Path = DIST_DIR) -> list[Path]:
    """Clean up temporary release notes markdown files from dist/."""
    removed: list[Path] = []
    if not dist_dir.exists():
        return removed

    for md_file in dist_dir.glob("RELEASE_NOTES_ios_*.md"):
        try:
            md_file.unlink()
            removed.append(md_file)
        except Exception as e:
            print(f"⚠️  Warning: Could not remove temporary notes file {md_file.name}: {e}", file=sys.stderr)

    return removed


def confirm_release_prompt(summary: str) -> bool:
    """Prompt user interactively to confirm release publishing."""
    print(summary)
    try:
        reply = input("\nProceed with git push and publishing GitHub release? [Y/n]: ").strip().lower()
        return reply in ("", "y", "yes")
    except (KeyboardInterrupt, EOFError):
        print()
        return False


# ---------------------------------------------------------------------------
# Release Pipeline Orchestrator
# ---------------------------------------------------------------------------

def run_release_pipeline(
    action: str,
    target_version: str | None = None,
    yes: bool = False,
    draft: bool = False,
    dry_run: bool = False,
    cwd: Path = PROJECT_ROOT,
    pbxproj_path: Path = PBXPROJ_PATH,
    changelog_path: Path = IOS_CHANGELOG_PATH,
    dist_dir: Path = DIST_DIR,
    build_script: Path = BUILD_IOS_SCRIPT_PATH,
) -> None:
    """Execute the 7-step safe iOS release pipeline."""
    if action not in ("patch", "minor", "major", "set"):
        raise ValueError(
            f"Action must be one of 'patch', 'minor', 'major', or 'set' when using --release (got '{action}')."
        )

    if not pbxproj_path.exists():
        raise FileNotFoundError(f"Cannot find Xcode project at {pbxproj_path}")

    content = pbxproj_path.read_text(encoding="utf-8")
    curr_marketing, curr_build = get_current_versions(content)

    if action in ("patch", "minor", "major"):
        new_marketing = bump_semver(curr_marketing, action)
    else:  # set
        if not target_version:
            raise ValueError("Must specify a version string when using 'set' (e.g., set 0.2.0)")
        validate_semver(target_version)
        new_marketing = target_version

    new_build = curr_build + 1

    print("🚀 Rekordbox Companion iOS Release Automation:")
    print(f"   Marketing Version: {curr_marketing} -> {new_marketing}")
    print(f"   Build Number:      {curr_build} -> {new_build}")

    # Step 1: Pre-flight Verification
    print("🔍 Running pre-flight verification...")
    errors = check_preflight_conditions(
        new_marketing,
        expected_branch="main",
        check_remote=not dry_run,
        cwd=cwd,
        changelog_path=changelog_path,
    )
    if errors:
        print("❌ Pre-flight checks failed:", file=sys.stderr)
        for err in errors:
            print(f"   • {err}", file=sys.stderr)
        sys.exit(1)
    print("✅ Pre-flight checks passed.")

    # Step 2: Build & Packaging Preview (dry-run)
    if dry_run:
        artifact = build_and_package_ios(
            new_marketing,
            cwd=cwd,
            build_script=build_script,
            dist_dir=dist_dir,
            dry_run=True,
        )
        _, release_notes = sync_changelog_for_release(
            new_marketing,
            cwd=cwd,
            changelog_path=changelog_path,
            dry_run=True,
        )

        print("\n--- [dry-run] Release Preview ---")
        print(f"Target Tag: ios-v{new_marketing}")
        print(f"Archive:    {artifact.ipa_path.name} ({artifact.ipa_size})")
        print(f"Checksum:   {artifact.sha256}")
        print(f"\n--- [dry-run] Release Notes Preview ---\n{release_notes}")
        print("---------------------------------------")
        print(f"[dry-run] Would commit 'chore(ios): bump version to {new_marketing} (build {new_build})'")
        print(f"[dry-run] Would create annotated tag 'ios-v{new_marketing}'")
        print(f"[dry-run] Would push branch to origin main and tag to origin ios-v{new_marketing}")
        gh_cmd = f"gh release create ios-v{new_marketing} {artifact.ipa_path} --title 'Rekordbox Companion iOS v{new_marketing}'"
        if draft:
            gh_cmd += " --draft"
        print(f"[dry-run] Would publish release via: {gh_cmd}")
        print("\n✅ Dry-run release simulation completed successfully. Zero changes were made.")
        return

    # Real Execution
    checkpoint_sha = get_git_checkpoint(cwd=cwd)
    tag_name = f"ios-v{new_marketing}"
    try:
        update_pbxproj(new_marketing, new_build, pbxproj_path=pbxproj_path, dry_run=False)
        changelog_updated, release_notes = sync_changelog_for_release(
            new_marketing,
            cwd=cwd,
            changelog_path=changelog_path,
            dry_run=False,
        )

        artifact = build_and_package_ios(
            new_marketing,
            cwd=cwd,
            build_script=build_script,
            dist_dir=dist_dir,
            dry_run=False,
        )

        summary = (
            f"\n🚀 Ready to publish iOS companion release:\n"
            f"   • Version:  {curr_marketing} -> {new_marketing} (build {new_build})\n"
            f"   • Archive:  {artifact.ipa_path.name} ({artifact.ipa_size})\n"
            f"   • Checksum: {artifact.sha256}\n"
            f"\n--- Release Notes Preview ({new_marketing}) ---\n"
            f"{release_notes}\n"
            f"----------------------------------------------"
        )

        if not yes:
            if not sys.stdin.isatty():
                raise RuntimeError(
                    "Non-interactive environment detected without -y / --yes. Pass -y to publish unattended."
                )
            confirmed = confirm_release_prompt(summary)
            if not confirmed:
                print("\n❌ Release aborted by user. Rolling back local changes...")
                rollback_to_checkpoint(checkpoint_sha, cwd=cwd)
                print("✅ Working tree restored to clean state.")
                sys.exit(1)
        else:
            print(summary)

    except Exception as e:
        print(f"\n⚠️  Error occurred during release preparation ({e}). Rolling back local state...", file=sys.stderr)
        try:
            rollback_to_checkpoint(checkpoint_sha, cwd=cwd)
            print("✅ Local working tree restored to checkpoint.", file=sys.stderr)
        except Exception as rb_err:
            print(f"⚠️  Rollback attempt encountered error: {rb_err}", file=sys.stderr)
        raise

    # Step 5: Commit and Tag
    try:
        git_commit_and_tag(
            new_marketing,
            new_build,
            create_tag=True,
            changelog_updated=changelog_updated,
            cwd=cwd,
            pbxproj_path=pbxproj_path,
            changelog_path=changelog_path,
        )
    except Exception as e:
        print(f"\n❌ Error during git commit/tag: {e}. Rolling back working tree and tag...", file=sys.stderr)
        try:
            rollback_to_checkpoint(checkpoint_sha, tag_name=tag_name, cwd=cwd)
            print("✅ Working tree and tag rolled back.", file=sys.stderr)
        except Exception as rb_err:
            print(f"⚠️  Rollback attempt encountered error: {rb_err}", file=sys.stderr)
        raise

    # Step 6: Remote Push
    try:
        publish_git_push(new_marketing, cwd=cwd)
    except Exception as e:
        print(f"\n❌ Error during git push: {e}.", file=sys.stderr)
        print("⚠️  Local commit and tag were preserved. Origin push failed.", file=sys.stderr)
        print(f"   To retry push manually, run:\n   git push origin main {tag_name}", file=sys.stderr)
        raise

    # Step 7: GitHub Release Publishing
    try:
        release_url = publish_github_release(
            new_marketing,
            ipa_path=artifact.ipa_path,
            release_notes=release_notes,
            draft=draft,
            dist_dir=dist_dir,
            cwd=cwd,
        )
        print(f"\n🎉 Successfully published release {tag_name}!")
        if release_url:
            print(f"🔗 Release URL: {release_url}")
        cleanup_release_artifacts(dist_dir=dist_dir)
    except Exception as e:
        print(f"\n⚠️  Release upload encountered error: {e}", file=sys.stderr)
        raise


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rekordbox Companion iOS Semantic Versioning & Release CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  %(prog)s                      # Show current version
  %(prog)s patch --release      # Run 7-step automated release pipeline
  %(prog)s minor --release -y   # Run release unattended (skip confirmation)
  %(prog)s patch --release --dry-run # Preview release simulation
  %(prog)s set 0.2.0 --release  # Release specific version
  %(prog)s patch --commit --tag # Bump patch, commit, and tag (without full release)
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
        help="Target version when action is 'set' (e.g. 0.2.0)",
    )
    parser.add_argument(
        "--release",
        action="store_true",
        help="Orchestrate the full 7-step automated release pipeline",
    )
    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Non-interactive mode; auto-confirm prompts during release",
    )
    parser.add_argument(
        "--draft",
        action="store_true",
        help="Publish GitHub release as a private draft",
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

    # 1. Release pipeline mode
    if args.release:
        if args.action not in ("patch", "minor", "major", "set"):
            print(
                f"❌ Error: When using --release, action must be 'patch', 'minor', 'major', or 'set' (got '{args.action}').",
                file=sys.stderr,
            )
            sys.exit(1)

        run_release_pipeline(
            action=args.action,
            target_version=args.version,
            yes=args.yes,
            draft=args.draft,
            dry_run=args.dry_run,
        )
        return

    # 2. Standalone / inspection mode
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
            print("❌ Error: Must specify a version string when using 'set' (e.g., set 0.2.0)", file=sys.stderr)
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
