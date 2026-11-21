"""
Unit tests for desktop & monorepo changelog generator (scripts/changelog.py).
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.changelog import (
    CommitInfo,
    parse_commit_line,
    is_noise_commit,
    is_ios_commit,
    format_bullet,
    categorize_commits,
    render_release_section,
    render_unreleased_section,
    render_compare_links,
    rotate_changelog,
    check_changelog,
    is_bullet_present,
    parse_markdown_categories,
    merge_notes_with_git_commits,
)


def test_parse_conventional_commits():
    c1 = parse_commit_line(
        "sha1", "2026-11-18", "Dev", "feat(filters): add NOT exclusion match mode (#10)", ["src/file.py"]
    )
    assert c1.commit_type == "feat"
    assert c1.scope == "filters"
    assert c1.description == "add NOT exclusion match mode (#10)"

    c2 = parse_commit_line(
        "sha2", "2026-11-18", "Dev", "fix: resolve track sorting by time and plays", ["src/file.py"]
    )
    assert c2.commit_type == "fix"
    assert c2.scope is None
    assert c2.description == "resolve track sorting by time and plays"

    c3 = parse_commit_line(
        "sha3", "2026-11-18", "Dev", "chore(desktop): bump version to 0.20.0", ["pyproject.toml"]
    )
    assert c3.commit_type == "chore"
    assert c3.scope == "desktop"

    c4 = parse_commit_line(
        "sha4", "2026-11-18", "Dev", "Non-conventional commit message", ["README.md"]
    )
    assert c4.commit_type is None
    assert c4.scope is None
    assert c4.description == "Non-conventional commit message"


def test_is_noise_commit():
    bump_desktop = parse_commit_line(
        "sha1", "2026-11-18", "Dev", "chore(desktop): bump version to 0.20.0", ["pyproject.toml"]
    )
    assert is_noise_commit(bump_desktop) is True

    bump_generic = parse_commit_line(
        "sha2", "2026-11-18", "Dev", "chore: bump up version number to 0.5.0", ["pyproject.toml"]
    )
    assert is_noise_commit(bump_generic) is True

    merge_pr = parse_commit_line(
        "sha3", "2026-11-18", "Dev", "Merge pull request #14 from branch", ["src/file.py"]
    )
    assert is_noise_commit(merge_pr) is True

    regular_feat = parse_commit_line(
        "sha4", "2026-11-18", "Dev", "feat(gui): add volume control slider", ["src/file.py"]
    )
    assert is_noise_commit(regular_feat) is False


def test_is_ios_commit():
    # 1. Scope is ios
    ios_scope = parse_commit_line(
        "sha1", "2026-11-18", "Dev", "fix(ios): keep section title visible above keyboard", ["ios/TrackDetail.swift"]
    )
    assert is_ios_commit(ios_scope) is True

    # 2. Files only under ios/
    ios_files_only = parse_commit_line(
        "sha2", "2026-11-18", "Dev", "refactor: extract filter bar view", ["ios/LocalDatabase.swift", "ios/TracksView.swift"]
    )
    assert is_ios_commit(ios_files_only) is True

    # 3. Explicit iOS companion announcement
    ios_app_add = parse_commit_line(
        "sha3", "2026-11-18", "Dev", "feat: add iOS companion app with SwiftUI", ["ios/App.swift", "README.md"]
    )
    assert is_ios_commit(ios_app_add) is True

    # 4. Desktop commit touching only python files
    desktop_commit = parse_commit_line(
        "sha4", "2026-11-18", "Dev", "feat(analytics): add turnover graph widget", ["src/analytics.py"]
    )
    assert is_ios_commit(desktop_commit) is False

    # 5. Repo-wide doc commit touching multi directories including ios/README.md
    repo_wide_docs = parse_commit_line(
        "sha5", "2026-11-18", "Dev", "docs(egm): add directory architecture contracts", [
            "src/README.md", "scripts/README.md", "ios/README.md"
        ]
    )
    assert is_ios_commit(repo_wide_docs) is False


def test_format_bullet():
    c_scope = parse_commit_line(
        "sha1", "2026-11-18", "Dev", "feat(filters): add NOT exclusion match mode (#10)", ["src/file.py"]
    )
    assert format_bullet(c_scope) == "- **filters**: Add NOT exclusion match mode (#10)"

    c_no_scope = parse_commit_line(
        "sha2", "2026-11-18", "Dev", "fix: resolve track sorting by time", ["src/file.py"]
    )
    assert format_bullet(c_no_scope) == "- Resolve track sorting by time"


def test_categorize_commits():
    commits = [
        parse_commit_line("1", "2026-11-18", "Dev", "feat(ui): add dark mode", ["src/ui.py"]),
        parse_commit_line("2", "2026-11-18", "Dev", "fix(db): handle null timeout", ["src/db.py"]),
        parse_commit_line("3", "2026-11-18", "Dev", "refactor: simplify query builder", ["src/db.py"]),
        parse_commit_line("4", "2026-11-18", "Dev", "build(app): bundle macOS app", ["scripts/build.sh"]),
        parse_commit_line("5", "2026-11-18", "Dev", "chore(desktop): bump version to 0.20.0", ["pyproject.toml"]),
        parse_commit_line("6", "2026-11-18", "Dev", "feat(ios): mobile sync screen", ["ios/Sync.swift"]),
        parse_commit_line("7", "2026-11-18", "Dev", "initial prototype commit", ["main.py"]),
    ]

    categorized = categorize_commits(commits, target="desktop")

    assert "Added" in categorized
    assert categorized["Added"] == ["- **ui**: Add dark mode"]

    assert "Fixed" in categorized
    assert categorized["Fixed"] == ["- **db**: Handle null timeout"]

    assert "Changed" in categorized
    assert categorized["Changed"] == ["- Simplify query builder"]

    assert "Maintenance" in categorized
    assert categorized["Maintenance"] == ["- **app**: Bundle macOS app"]

    assert "Other Changes" in categorized
    assert categorized["Other Changes"] == ["- Initial prototype commit"]

    # Bump version and iOS feature must not appear
    for cat, items in categorized.items():
        assert not any("bump version" in item for item in items)
        assert not any("mobile sync screen" in item for item in items)


def test_render_release_section():
    categorized = {
        "Added": ["- **filters**: Add NOT filter"],
        "Fixed": ["- Resolve sorting bug"],
    }
    rendered = render_release_section("0.20.0", "2026-11-18", categorized)
    assert "## [0.20.0] - 2026-11-18" in rendered
    assert "### Added\n\n- **filters**: Add NOT filter" in rendered
    assert "### Fixed\n\n- Resolve sorting bug" in rendered
    assert "### Maintenance" not in rendered


def test_render_compare_links():
    tags = ["v0.1.0", "v0.2.0", "v0.3.0"]
    links = render_compare_links(tags, repo_url="https://github.com/example/repo")
    expected = (
        "[Unreleased]: https://github.com/example/repo/compare/v0.3.0...HEAD\n"
        "[0.3.0]: https://github.com/example/repo/compare/v0.2.0...v0.3.0\n"
        "[0.2.0]: https://github.com/example/repo/compare/v0.1.0...v0.2.0\n"
        "[0.1.0]: https://github.com/example/repo/releases/tag/v0.1.0"
    )
    assert links == expected


def test_rotate_changelog(tmp_path: Path):
    sample_changelog = """# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **filters**: Add NOT exclusion match mode (#10)

## [0.20.0] - 2026-11-18

### Maintenance

- Routine updates and maintenance.

[Unreleased]: https://github.com/example/repo/compare/v0.20.0...HEAD
[0.20.0]: https://github.com/example/repo/releases/tag/v0.20.0
"""
    test_file = tmp_path / "CHANGELOG.md"
    test_file.write_text(sample_changelog, encoding="utf-8")

    rotated = rotate_changelog(
        new_version="0.21.0",
        date_str="2026-11-19",
        changelog_path=test_file,
    )
    assert rotated is True

    content = test_file.read_text(encoding="utf-8")
    assert "## [Unreleased]\n\n## [0.21.0] - 2026-11-19" in content
    assert "### Added\n\n- **filters**: Add NOT exclusion match mode (#10)" in content
    assert "## [0.20.0] - 2026-11-18" in content
    assert "[Unreleased]: https://github.com/example/repo/compare/v0.21.0...HEAD" in content
    assert "[0.21.0]: https://github.com/example/repo/compare/v0.20.0...v0.21.0" in content


def test_check_changelog(tmp_path: Path):
    test_file = tmp_path / "CHANGELOG.md"
    # Missing file returns False
    assert check_changelog(changelog_path=test_file) is False

    # Valid content matching latest tag and compare link
    valid_content = """# Changelog
All notable changes to this project will be documented in this file.

## [Unreleased]

## [0.20.0] - 2026-11-18

[Unreleased]: https://github.com/fabriziocacicia/rekordbox-collection-manager/compare/v0.20.0...HEAD
[0.20.0]: https://github.com/fabriziocacicia/rekordbox-collection-manager/releases/tag/v0.20.0
"""
    test_file.write_text(valid_content, encoding="utf-8")
    assert check_changelog(changelog_path=test_file) is True


def test_merge_notes_with_git_commits():
    # 1. Empty existing notes: populates all from git
    git_cats = {
        "Added": ["- **gui**: Add stores summary view (#6)"],
        "Maintenance": ["- **release**: Automate release pipeline (#16)"],
    }
    merged, added = merge_notes_with_git_commits("", git_cats)
    assert len(added) == 2
    assert "### Added" in merged
    assert "- **gui**: Add stores summary view (#6)" in merged
    assert "### Maintenance" in merged
    assert "- **release**: Automate release pipeline (#16)" in merged

    # 2. Existing notes with partial entries: merges missing entries without duplicates
    existing = """### Maintenance

- **release**: Automate release pipeline (#16)
"""
    merged2, added2 = merge_notes_with_git_commits(existing, git_cats)
    assert len(added2) == 1
    assert added2 == ["- **gui**: Add stores summary view (#6)"]
    assert "### Added" in merged2
    assert "- **gui**: Add stores summary view (#6)" in merged2
    # Ensure Maintenance wasn't duplicated
    assert merged2.count("- **release**: Automate release pipeline (#16)") == 1

    # 3. All entries already present: returns unchanged
    merged3, added3 = merge_notes_with_git_commits(merged2, git_cats)
    assert len(added3) == 0
    assert merged3 == merged2

