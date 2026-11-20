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
    check_git_branch,
    check_git_clean,
    check_preflight_conditions,
    check_required_tools,
    check_tag_collision,
    get_git_checkpoint,
    rollback_to_checkpoint,
    run_git,
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
