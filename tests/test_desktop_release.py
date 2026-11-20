"""
Unit tests for Desktop Release Automation suite (scripts/desktop_version.py).
Focuses on pre-flight verification, git checkpointing, and rollback mechanics.
"""

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.desktop_version import (
    REQUIRED_TOOLS,
    BuildArtifactInfo,
    build_and_package_app,
    check_git_branch,
    check_git_clean,
    check_preflight_conditions,
    check_required_tools,
    check_tag_collision,
    compute_sha256,
    extract_release_notes,
    extract_unreleased_notes,
    get_expected_zip_path,
    get_git_checkpoint,
    rollback_to_checkpoint,
    run_git,
    sync_changelog_for_release,
)


def test_run_git_success():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="commit-sha-123\n", returncode=0)
        output = run_git(["rev-parse", "HEAD"])
        assert output == "commit-sha-123"
        mock_run.assert_called_once()


def test_run_git_failure():
    with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, ["git"], stderr="fatal: error")):
        with pytest.raises(RuntimeError) as exc_info:
            run_git(["status"])
        assert "Git command failed" in str(exc_info.value)


def test_check_required_tools_all_present():
    with patch("shutil.which", return_value="/usr/bin/tool"), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="Logged in to github.com")
        errors = check_required_tools()
        assert errors == []


def test_check_required_tools_missing_tool():
    def mock_which(tool):
        if tool == "ditto":
            return None
        return f"/usr/bin/{tool}"

    with patch("shutil.which", side_effect=mock_which), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="Logged in")
        errors = check_required_tools()
        assert len(errors) == 1
        assert "ditto" in errors[0]


def test_check_required_tools_gh_unauthenticated():
    with patch("shutil.which", return_value="/usr/bin/tool"), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="You are not logged into any GitHub hosts.")
        errors = check_required_tools()
        assert len(errors) == 1
        assert "GitHub CLI ('gh') is not authenticated" in errors[0]


def test_check_git_clean_clean():
    with patch("scripts.desktop_version.run_git", return_value=""):
        errors = check_git_clean()
        assert errors == []


def test_check_git_clean_dirty():
    with patch("scripts.desktop_version.run_git", return_value=" M pyproject.toml"):
        errors = check_git_clean()
        assert len(errors) == 1
        assert "working tree is dirty" in errors[0]


def test_check_git_branch_valid():
    def mock_run_git(cmd, cwd=None):
        if cmd == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return "main"
        if cmd == ["fetch", "origin", "main"]:
            return ""
        if cmd == ["rev-parse", "HEAD"]:
            return "sha123"
        if cmd == ["rev-parse", "origin/main"]:
            return "sha123"
        return ""

    with patch("scripts.desktop_version.run_git", side_effect=mock_run_git):
        errors = check_git_branch(expected_branch="main", check_remote=True)
        assert errors == []


def test_check_git_branch_wrong_branch():
    with patch("scripts.desktop_version.run_git", return_value="feature/test"):
        errors = check_git_branch(expected_branch="main", check_remote=True)
        assert len(errors) == 1
        assert "releases must be cut from 'main'" in errors[0]


def test_check_git_branch_diverged():
    def mock_run_git(cmd, cwd=None):
        if cmd == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return "main"
        if cmd == ["fetch", "origin", "main"]:
            return ""
        if cmd == ["rev-parse", "HEAD"]:
            return "sha_local"
        if cmd == ["rev-parse", "origin/main"]:
            return "sha_remote"
        if cmd == ["rev-list", "--left-right", "--count", "HEAD...origin/main"]:
            return "1\t2"
        return ""

    with patch("scripts.desktop_version.run_git", side_effect=mock_run_git):
        errors = check_git_branch(expected_branch="main", check_remote=True)
        assert len(errors) == 1
        assert "1 ahead, 2 behind" in errors[0]


def test_check_tag_collision_none():
    def mock_run_git(cmd, cwd=None):
        if cmd == ["tag", "-l", "v0.21.0"]:
            return ""
        if cmd == ["ls-remote", "--tags", "origin", "refs/tags/v0.21.0"]:
            return ""
        return ""

    with patch("scripts.desktop_version.run_git", side_effect=mock_run_git):
        errors = check_tag_collision("0.21.0", check_remote=True)
        assert errors == []


def test_check_tag_collision_local():
    def mock_run_git(cmd, cwd=None):
        if cmd == ["tag", "-l", "v0.21.0"]:
            return "v0.21.0"
        if cmd == ["ls-remote", "--tags", "origin", "refs/tags/v0.21.0"]:
            return ""
        return ""

    with patch("scripts.desktop_version.run_git", side_effect=mock_run_git):
        errors = check_tag_collision("0.21.0", check_remote=True)
        assert len(errors) == 1
        assert "Local tag 'v0.21.0' already exists" in errors[0]


def test_check_tag_collision_remote():
    def mock_run_git(cmd, cwd=None):
        if cmd == ["tag", "-l", "v0.21.0"]:
            return ""
        if cmd == ["ls-remote", "--tags", "origin", "refs/tags/v0.21.0"]:
            return "abc1234\trefs/tags/v0.21.0"
        return ""

    with patch("scripts.desktop_version.run_git", side_effect=mock_run_git):
        errors = check_tag_collision("0.21.0", check_remote=True)
        assert len(errors) == 1
        assert "Remote tag 'v0.21.0' already exists on origin" in errors[0]


def test_check_preflight_conditions_all_clean():
    with patch("scripts.desktop_version.check_required_tools", return_value=[]), \
         patch("scripts.desktop_version.check_git_clean", return_value=[]), \
         patch("scripts.desktop_version.check_git_branch", return_value=[]), \
         patch("scripts.desktop_version.check_tag_collision", return_value=[]):
        errors = check_preflight_conditions("0.21.0")
        assert errors == []


def test_check_preflight_conditions_accumulates_errors():
    with patch("scripts.desktop_version.check_required_tools", return_value=["tool error"]), \
         patch("scripts.desktop_version.check_git_clean", return_value=["dirty error"]), \
         patch("scripts.desktop_version.check_git_branch", return_value=["branch error"]), \
         patch("scripts.desktop_version.check_tag_collision", return_value=["tag collision error"]):
        errors = check_preflight_conditions("0.21.0")
        assert len(errors) == 4
        assert errors == ["tool error", "dirty error", "branch error", "tag collision error"]


def test_get_git_checkpoint():
    with patch("scripts.desktop_version.run_git", return_value="initial_sha_123"):
        sha = get_git_checkpoint()
        assert sha == "initial_sha_123"


def test_rollback_to_checkpoint_resets_commit_and_tag():
    with patch("subprocess.run") as mock_sub_run, \
         patch("scripts.desktop_version.run_git") as mock_run_git:
        # Initial call checks current sha: "new_sha" vs "orig_sha"
        # Second call checks if tag exists: "v0.21.0"
        mock_run_git.side_effect = ["new_sha", "v0.21.0"]

        rollback_to_checkpoint("orig_sha", tag_name="v0.21.0")

        # 1. git checkout -- .
        # 2. git reset --hard orig_sha
        # 3. git tag -d v0.21.0
        assert mock_sub_run.call_count == 3
        calls = [c[0][0] for c in mock_sub_run.call_args_list]
        assert calls[0] == ["git", "checkout", "--", "."]
        assert calls[1] == ["git", "reset", "--hard", "orig_sha"]
        assert calls[2] == ["git", "tag", "-d", "v0.21.0"]


def test_compute_sha256(tmp_path):
    test_file = tmp_path / "sample.bin"
    test_file.write_bytes(b"hello rekordbox release")
    expected = "8b7bc9e0c9add1b89848c6394aa04616c3eee56a38cc7bc1313596f0c986bc0e"
    assert compute_sha256(test_file) == expected


def test_get_expected_zip_path():
    dist_dir = Path("/custom/dist")
    p1 = get_expected_zip_path("0.21.0", dist_dir=dist_dir)
    assert p1 == Path("/custom/dist/Rekordbox-Collection-Manager-v0.21.0-macOS-arm64.zip")

    p2 = get_expected_zip_path("v1.0.0", dist_dir=dist_dir)
    assert p2 == Path("/custom/dist/Rekordbox-Collection-Manager-v1.0.0-macOS-arm64.zip")


def test_build_and_package_app_dry_run():
    info = build_and_package_app("0.21.0", dry_run=True)
    assert info.zip_size == "[dry-run]"
    assert info.sha256 == "[dry-run-sha256]"
    assert info.zip_path.name == "Rekordbox-Collection-Manager-v0.21.0-macOS-arm64.zip"


def test_build_and_package_app_script_missing(tmp_path):
    non_existent = tmp_path / "non_existent.sh"
    with pytest.raises(FileNotFoundError, match="Build script not found"):
        build_and_package_app("0.21.0", build_script=non_existent)


def test_build_and_package_app_failure(tmp_path):
    fake_script = tmp_path / "build_app.sh"
    fake_script.write_text("#!/bin/sh\nexit 1\n")
    fake_script.chmod(0o755)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="Compilation error", stdout="")
        with pytest.raises(RuntimeError, match="Build and packaging script failed"):
            build_and_package_app("0.21.0", build_script=fake_script, dist_dir=tmp_path)


def test_build_and_package_app_zip_not_created(tmp_path):
    fake_script = tmp_path / "build_app.sh"
    fake_script.write_text("#!/bin/sh\nexit 0\n")
    fake_script.chmod(0o755)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="Done")
        with pytest.raises(FileNotFoundError, match="Expected release archive was not found"):
            build_and_package_app("0.21.0", build_script=fake_script, dist_dir=tmp_path)


def test_build_and_package_app_success(tmp_path):
    fake_script = tmp_path / "build_app.sh"
    fake_script.write_text("#!/bin/sh\nexit 0\n")
    fake_script.chmod(0o755)

    expected_zip = tmp_path / "Rekordbox-Collection-Manager-v0.21.0-macOS-arm64.zip"
    expected_zip.write_bytes(b"dummy archive content")

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="Done")
        info = build_and_package_app("0.21.0", build_script=fake_script, dist_dir=tmp_path)

        assert info.zip_path == expected_zip
        assert info.zip_size == "0.0 MB"
        assert len(info.sha256) == 64


def test_extract_unreleased_notes(tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n"
        "## [Unreleased]\n\n"
        "### Maintenance\n\n"
        "- **app**: Bundle standalone app\n\n"
        "## [0.20.0] - 2026-11-18\n\n"
        "- Some change\n\n"
        "[Unreleased]: https://example.com\n"
    )

    notes = extract_unreleased_notes(changelog)
    assert "### Maintenance" in notes
    assert "- **app**: Bundle standalone app" in notes

    empty_changelog = tmp_path / "EMPTY_CHANGELOG.md"
    empty_changelog.write_text(
        "# Changelog\n\n"
        "## [Unreleased]\n\n"
        "## [0.20.0] - 2026-11-18\n\n"
        "- Some change\n"
    )
    assert extract_unreleased_notes(empty_changelog) == ""


def test_extract_release_notes(tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n"
        "## [Unreleased]\n\n"
        "## [0.21.0] - 2026-11-20\n\n"
        "### Added\n\n"
        "- **filters**: NOT exclusion mode\n\n"
        "## [0.20.0] - 2026-11-18\n\n"
        "### Fixed\n\n"
        "- Old fix\n\n"
        "[Unreleased]: https://example.com\n"
    )

    notes = extract_release_notes("0.21.0", changelog)
    assert "### Added" in notes
    assert "- **filters**: NOT exclusion mode" in notes
    assert "Old fix" not in notes


def test_sync_changelog_for_release_with_existing_unreleased(tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n"
        "## [Unreleased]\n\n"
        "### Added\n\n"
        "- **filters**: Manual curated note\n\n"
        "## [0.20.0] - 2026-11-18\n\n"
        "- Prior release\n\n"
        "[Unreleased]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.20.0...HEAD\n"
        "[0.20.0]: https://github.com/fabriziocacicia/rekordbox-collection-manager/releases/tag/v0.20.0\n"
    )

    updated, notes = sync_changelog_for_release(
        "0.21.0",
        date_str="2026-11-20",
        changelog_path=changelog,
        dry_run=False,
    )

    assert updated is True
    assert "Manual curated note" in notes
    content = changelog.read_text()
    assert "## [0.21.0] - 2026-11-20" in content
    assert "## [Unreleased]\n\n## [0.21.0]" in content


def test_sync_changelog_for_release_auto_extracts_commits_when_empty(tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n"
        "## [Unreleased]\n\n"
        "## [0.20.0] - 2026-11-18\n\n"
        "- Prior release\n\n"
        "[Unreleased]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.20.0...HEAD\n"
        "[0.20.0]: https://github.com/fabriziocacicia/rekordbox-collection-manager/releases/tag/v0.20.0\n"
    )

    mock_commit = MagicMock()
    mock_commit.message = "feat(gui): auto extracted feature"
    mock_commit.commit_type = "feat"
    mock_commit.scope = "gui"
    mock_commit.description = "auto extracted feature"

    with patch("scripts.desktop_version.get_desktop_tags", return_value=["v0.20.0"]), \
         patch("scripts.desktop_version.get_commits_for_range", return_value=[mock_commit]), \
         patch("scripts.desktop_version.categorize_commits", return_value={"Added": ["- **gui**: Auto extracted feature"]}):
        updated, notes = sync_changelog_for_release(
            "0.21.0",
            date_str="2026-11-20",
            changelog_path=changelog,
            dry_run=False,
        )

        assert updated is True
        assert "Auto extracted feature" in notes
        content = changelog.read_text()
        assert "## [0.21.0] - 2026-11-20" in content
        assert "Auto extracted feature" in content


def test_sync_changelog_for_release_dry_run(tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    initial_content = (
        "# Changelog\n\n"
        "## [Unreleased]\n\n"
        "### Maintenance\n\n"
        "- Pending work\n\n"
        "## [0.20.0] - 2026-11-18\n\n"
        "- Prior release\n\n"
        "[Unreleased]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.20.0...HEAD\n"
    )
    changelog.write_text(initial_content)

    updated, notes = sync_changelog_for_release(
        "0.21.0",
        date_str="2026-11-20",
        changelog_path=changelog,
        dry_run=True,
    )

    assert updated is True
    assert "Pending work" in notes
    # File content must remain completely unchanged in dry run
    assert changelog.read_text() == initial_content

