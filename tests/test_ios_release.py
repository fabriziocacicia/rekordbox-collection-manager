"""
Unit tests for iOS Companion Release Automation suite (scripts/ios_version.py).
Focuses on pre-flight verification, git checkpointing, rollback mechanics,
pbxproj updates, .ipa packaging, and the 7-step release pipeline.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ios_version import (
    REQUIRED_TOOLS,
    IpaArtifact,
    build_and_package_ios,
    bump_semver,
    check_git_branch,
    check_git_clean,
    check_preflight_conditions,
    check_required_tools,
    check_tag_collision,
    check_unreleased_notes,
    cleanup_release_artifacts,
    confirm_release_prompt,
    extract_release_notes,
    extract_unreleased_notes,
    get_current_versions,
    get_git_checkpoint,
    git_commit_and_tag,
    publish_git_push,
    publish_github_release,
    rollback_to_checkpoint,
    run_git,
    run_release_pipeline,
    sync_changelog_for_release,
    update_pbxproj,
    validate_semver,
)


# ---------------------------------------------------------------------------
# Pre-Flight Checks
# ---------------------------------------------------------------------------

def test_check_required_tools_all_present():
    with patch("shutil.which", return_value="/usr/bin/tool"), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="Logged in to github.com")
        errors = check_required_tools()
        assert errors == []


def test_check_required_tools_missing_tool():
    def mock_which(tool):
        if tool == "xcodebuild":
            return None
        return f"/usr/bin/{tool}"

    with patch("shutil.which", side_effect=mock_which), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="Logged in")
        errors = check_required_tools()
        assert len(errors) == 1
        assert "xcodebuild" in errors[0]


def test_check_required_tools_gh_unauthenticated():
    with patch("shutil.which", return_value="/usr/bin/tool"), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="You are not logged into any GitHub hosts.")
        errors = check_required_tools()
        assert len(errors) == 1
        assert "GitHub CLI ('gh') is not authenticated" in errors[0]


def test_check_git_clean_clean():
    with patch("scripts.ios_version.run_git", return_value=""):
        errors = check_git_clean()
        assert errors == []


def test_check_git_clean_dirty():
    with patch("scripts.ios_version.run_git", return_value=" M ios/RekordboxCompanion/..."):
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

    with patch("scripts.ios_version.run_git", side_effect=mock_run_git):
        errors = check_git_branch(expected_branch="main", check_remote=True)
        assert errors == []


def test_check_git_branch_wrong_branch():
    with patch("scripts.ios_version.run_git", return_value="build/ios-release-automation"):
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
            return "2\t1"
        return ""

    with patch("scripts.ios_version.run_git", side_effect=mock_run_git):
        errors = check_git_branch(expected_branch="main", check_remote=True)
        assert len(errors) == 1
        assert "2 ahead, 1 behind" in errors[0]


def test_check_tag_collision_none():
    def mock_run_git(cmd, cwd=None):
        if cmd == ["tag", "-l", "ios-v0.2.0"]:
            return ""
        if cmd == ["ls-remote", "--tags", "origin", "refs/tags/ios-v0.2.0"]:
            return ""
        return ""

    with patch("scripts.ios_version.run_git", side_effect=mock_run_git):
        errors = check_tag_collision("0.2.0", check_remote=True)
        assert errors == []


def test_check_tag_collision_local():
    def mock_run_git(cmd, cwd=None):
        if cmd == ["tag", "-l", "ios-v0.2.0"]:
            return "ios-v0.2.0"
        if cmd == ["ls-remote", "--tags", "origin", "refs/tags/ios-v0.2.0"]:
            return ""
        return ""

    with patch("scripts.ios_version.run_git", side_effect=mock_run_git):
        errors = check_tag_collision("0.2.0", check_remote=True)
        assert len(errors) == 1
        assert "Local tag 'ios-v0.2.0' already exists" in errors[0]


def test_check_tag_collision_remote():
    def mock_run_git(cmd, cwd=None):
        if cmd == ["tag", "-l", "ios-v0.2.0"]:
            return ""
        if cmd == ["ls-remote", "--tags", "origin", "refs/tags/ios-v0.2.0"]:
            return "abc1234\trefs/tags/ios-v0.2.0"
        return ""

    with patch("scripts.ios_version.run_git", side_effect=mock_run_git):
        errors = check_tag_collision("0.2.0", check_remote=True)
        assert len(errors) == 1
        assert "Remote tag 'ios-v0.2.0' already exists on origin" in errors[0]


def test_check_unreleased_notes_populated(tmp_path: Path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n## [Unreleased]\n\n- **ios**: Added Live Set tracking\n\n## [0.1.0]\n")
    assert check_unreleased_notes(changelog_path=changelog) == []


def test_check_unreleased_notes_empty(tmp_path: Path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n## [Unreleased]\n\n## [0.1.0]\n")
    errors = check_unreleased_notes(changelog_path=changelog)
    assert len(errors) == 1
    assert "is empty" in errors[0]


def test_check_preflight_conditions_clean():
    with patch("scripts.ios_version.check_required_tools", return_value=[]), \
         patch("scripts.ios_version.check_git_clean", return_value=[]), \
         patch("scripts.ios_version.check_git_branch", return_value=[]), \
         patch("scripts.ios_version.check_tag_collision", return_value=[]), \
         patch("scripts.ios_version.check_unreleased_notes", return_value=[]):
        errors = check_preflight_conditions("0.2.0")
        assert errors == []


# ---------------------------------------------------------------------------
# Version Management & pbxproj Updates
# ---------------------------------------------------------------------------

SAMPLE_PBXPROJ = """// !$*UTF8*$!
{
	archiveVersion = 1;
	classes = {
	};
	objectVersion = 56;
	objects = {
		60DDAEA657775AE491C30EE5 /* Release */ = {
			isa = XCBuildConfiguration;
			buildSettings = {
				CURRENT_PROJECT_VERSION = 2;
				MARKETING_VERSION = 0.1.1;
				PRODUCT_NAME = "$(TARGET_NAME)";
			};
			name = Release;
		};
	};
	rootObject = 247A833E016921200021A374 /* Project object */;
}
"""

def test_get_current_versions():
    marketing, build_num = get_current_versions(SAMPLE_PBXPROJ)
    assert marketing == "0.1.1"
    assert build_num == 2


def test_bump_semver():
    assert bump_semver("0.1.1", "patch") == "0.1.2"
    assert bump_semver("0.1.1", "minor") == "0.2.0"
    assert bump_semver("0.1.1", "major") == "1.0.0"
    with pytest.raises(ValueError):
        bump_semver("0.1.1", "invalid")


def test_update_pbxproj(tmp_path: Path):
    pbx_file = tmp_path / "project.pbxproj"
    pbx_file.write_text(SAMPLE_PBXPROJ, encoding="utf-8")

    update_pbxproj("0.2.0", 3, pbxproj_path=pbx_file, dry_run=False)

    updated_text = pbx_file.read_text(encoding="utf-8")
    new_m, new_b = get_current_versions(updated_text)
    assert new_m == "0.2.0"
    assert new_b == 3


# ---------------------------------------------------------------------------
# Checkpoints & Rollback
# ---------------------------------------------------------------------------

def test_get_git_checkpoint():
    with patch("scripts.ios_version.run_git", return_value="sha_checkpoint_123"):
        assert get_git_checkpoint() == "sha_checkpoint_123"


def test_rollback_to_checkpoint_resets_commit_and_tag():
    with patch("subprocess.run") as mock_sub_run, \
         patch("scripts.ios_version.run_git") as mock_run_git:
        mock_run_git.side_effect = ["new_sha", "ios-v0.2.0"]

        rollback_to_checkpoint("orig_sha", tag_name="ios-v0.2.0")

        assert mock_sub_run.call_count == 3
        calls = [c[0][0] for c in mock_sub_run.call_args_list]
        assert calls[0] == ["git", "checkout", "--", "."]
        assert calls[1] == ["git", "reset", "--hard", "orig_sha"]
        assert calls[2] == ["git", "tag", "-d", "ios-v0.2.0"]


# ---------------------------------------------------------------------------
# Build & Packaging
# ---------------------------------------------------------------------------

def test_build_and_package_ios_dry_run():
    artifact = build_and_package_ios("0.2.0", dry_run=True)
    assert artifact.ipa_path.name == "RekordboxCompanion-v0.2.0.ipa"
    assert artifact.ipa_size == "~800K"
    assert "dry-run" in artifact.sha256


def test_build_and_package_ios_missing_script(tmp_path: Path):
    non_existent = tmp_path / "build_ios.sh"
    with pytest.raises(FileNotFoundError, match="iOS build script not found"):
        build_and_package_ios("0.2.0", build_script=non_existent)


def test_build_and_package_ios_failure(tmp_path: Path):
    fake_script = tmp_path / "build_ios.sh"
    fake_script.write_text("#!/bin/sh\nexit 1\n")
    fake_script.chmod(0o755)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="xcodebuild failed", stdout="")
        with pytest.raises(RuntimeError, match="iOS build failed with exit code 1"):
            build_and_package_ios("0.2.0", build_script=fake_script, dist_dir=tmp_path)


def test_build_and_package_ios_success(tmp_path: Path):
    fake_script = tmp_path / "build_ios.sh"
    fake_script.write_text("#!/bin/sh\nexit 0\n")
    fake_script.chmod(0o755)

    expected_ipa = tmp_path / "RekordboxCompanion-v0.2.0.ipa"

    def fake_build(cmd, **kwargs):
        expected_ipa.write_bytes(b"dummy ipa binary content")
        return MagicMock(returncode=0, stdout="Success")

    with patch("subprocess.run", side_effect=fake_build) as mock_run:
        artifact = build_and_package_ios("0.2.0", build_script=fake_script, dist_dir=tmp_path)
        assert artifact.ipa_path == expected_ipa
        assert len(artifact.sha256) == 64
        env = mock_run.call_args[1].get("env", {})
        assert env.get("APP_VERSION") == "0.2.0"


# ---------------------------------------------------------------------------
# Release Notes & Changelog Sync
# ---------------------------------------------------------------------------

def test_sync_changelog_for_release(tmp_path: Path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n"
        "## [Unreleased]\n\n"
        "### Added\n\n"
        "- **ios**: Mobile live set tracking\n\n"
        "## [0.1.0] - 2026-11-20\n\n"
        "[Unreleased]: https://example.com/compare/ios-v0.1.0...HEAD\n"
        "[0.1.0]: https://example.com/releases/tag/ios-v0.1.0\n"
    )

    updated, notes = sync_changelog_for_release(
        "0.2.0",
        date_str="2026-11-23",
        changelog_path=changelog,
        dry_run=False,
    )
    assert updated is True
    assert "### Added" in notes
    assert "- **ios**: Mobile live set tracking" in notes

    content = changelog.read_text(encoding="utf-8")
    assert "## [0.2.0] - 2026-11-23" in content
    assert "[Unreleased]: https://example.com/compare/ios-v0.2.0...HEAD" in content


# ---------------------------------------------------------------------------
# Git & GitHub Operations
# ---------------------------------------------------------------------------

def test_git_commit_and_tag():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        git_commit_and_tag("0.2.0", 3, create_tag=True)
        assert mock_run.call_count == 3
        calls = [c[0][0] for c in mock_run.call_args_list]
        assert calls[0][0:2] == ["git", "add"]
        assert calls[1][0:3] == ["git", "commit", "-m"]
        assert "chore(ios): bump version to 0.2.0 (build 3)" in calls[1][3]
        assert calls[2][0:3] == ["git", "tag", "-a"]
        assert calls[2][3] == "ios-v0.2.0"


def test_publish_git_push():
    with patch("scripts.ios_version.run_git") as mock_run_git:
        mock_run_git.side_effect = ["main", "", ""]
        publish_git_push("0.2.0")
        assert mock_run_git.call_count == 3


def test_publish_github_release(tmp_path: Path):
    ipa_path = tmp_path / "RekordboxCompanion-v0.2.0.ipa"
    ipa_path.write_bytes(b"ipa bytes")

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="https://github.com/example/releases/tag/ios-v0.2.0\n")
        url = publish_github_release(
            "0.2.0",
            ipa_path=ipa_path,
            release_notes="- Added feature",
            draft=True,
            dist_dir=tmp_path,
        )
        assert "releases/tag/ios-v0.2.0" in url
        call_cmd = mock_run.call_args[0][0]
        assert call_cmd[0:3] == ["gh", "release", "create"]
        assert "ios-v0.2.0" in call_cmd
        assert "--draft" in call_cmd


def test_cleanup_release_artifacts(tmp_path: Path):
    notes = tmp_path / "RELEASE_NOTES_ios_0.2.0.md"
    notes.write_text("notes")
    other = tmp_path / "RekordboxCompanion-v0.2.0.ipa"
    other.write_bytes(b"ipa")

    removed = cleanup_release_artifacts(dist_dir=tmp_path)
    assert len(removed) == 1
    assert removed[0] == notes
    assert not notes.exists()
    assert other.exists()  # IPA preserved


# ---------------------------------------------------------------------------
# Full Release Pipeline Simulation
# ---------------------------------------------------------------------------

def test_run_release_pipeline_dry_run(tmp_path: Path, capsys):
    pbx_file = tmp_path / "project.pbxproj"
    pbx_file.write_text(SAMPLE_PBXPROJ, encoding="utf-8")

    changelog_file = tmp_path / "CHANGELOG.md"
    changelog_file.write_text(
        "# Changelog\n\n## [Unreleased]\n\n- **ios**: Live Set feature\n\n## [0.1.0]\n"
    )

    with patch("scripts.ios_version.check_preflight_conditions", return_value=[]):
        run_release_pipeline(
            action="patch",
            dry_run=True,
            pbxproj_path=pbx_file,
            changelog_path=changelog_file,
            dist_dir=tmp_path,
        )

    out = capsys.readouterr().out
    assert "[dry-run] Release Preview" in out
    assert "Target Tag: ios-v0.1.2" in out
    assert "Dry-run release simulation completed successfully" in out
    # pbxproj and changelog remain untouched
    assert pbx_file.read_text(encoding="utf-8") == SAMPLE_PBXPROJ
