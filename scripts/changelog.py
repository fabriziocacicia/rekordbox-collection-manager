#!/usr/bin/env python3
"""
Desktop & Monorepo Changelog Management CLI for Rekordbox Collection Manager.

Extracts git commit history between Semantic Versioning tags, cleans noise and
iOS monorepo commits, categorizes entries into Keep a Changelog 1.1.0 standard,
generates GitHub compare links, and automates changelog rotation upon version bumps.
"""

from __future__ import annotations

import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
CHANGELOG_PATH = PROJECT_ROOT / "CHANGELOG.md"
IOS_CHANGELOG_PATH = PROJECT_ROOT / "ios" / "CHANGELOG.md"
GITHUB_REPO_URL = "https://github.com/fabriziocacicia/rekordbox-collection-manager"

# Keep a Changelog 1.1.0 standard headers
CATEGORIES_ORDER = [
    "Added",
    "Changed",
    "Fixed",
    "Maintenance",
    "Other Changes",
]

CONVENTIONAL_PATTERN = re.compile(
    r"^(?P<type>feat|fix|refactor|perf|chore|docs|build|test|ci)(?:\((?P<scope>[^)]+)\))?!?:\s*(?P<desc>.+)$",
    re.IGNORECASE,
)

CATEGORY_MAPPING: dict[str, str] = {
    "feat": "Added",
    "fix": "Fixed",
    "refactor": "Changed",
    "perf": "Changed",
    "chore": "Maintenance",
    "docs": "Maintenance",
    "build": "Maintenance",
    "test": "Maintenance",
    "ci": "Maintenance",
}

NOISE_BUMP_PATTERN = re.compile(
    r"^chore(\([^)]+\))?:\s*bump\s+(up\s+)?version",
    re.IGNORECASE,
)

NOISE_MERGE_PATTERN = re.compile(
    r"^Merge\s+(pull request|branch|remote-tracking branch)",
    re.IGNORECASE,
)


class CommitInfo(NamedTuple):
    sha: str
    message: str
    date: str
    author: str
    files: list[str]
    commit_type: str | None
    scope: str | None
    description: str


def parse_commit_line(sha: str, date: str, author: str, subject: str, files: list[str]) -> CommitInfo:
    """Parse raw git commit data into structured CommitInfo."""
    match = CONVENTIONAL_PATTERN.match(subject.strip())
    if match:
        c_type = match.group("type").lower()
        scope = match.group("scope").strip() if match.group("scope") else None
        desc = match.group("desc").strip()
    else:
        c_type = None
        scope = None
        desc = subject.strip()

    return CommitInfo(
        sha=sha,
        message=subject.strip(),
        date=date,
        author=author,
        files=files,
        commit_type=c_type,
        scope=scope,
        description=desc,
    )


def is_noise_commit(commit: CommitInfo) -> bool:
    """Filter out version bump commits and merge commits."""
    msg = commit.message
    if NOISE_BUMP_PATTERN.match(msg):
        return True
    if NOISE_MERGE_PATTERN.match(msg):
        return True
    return False


def is_ios_commit(commit: CommitInfo) -> bool:
    """
    Detect if a commit belongs exclusively or primarily to the iOS companion app.

    - Scope explicitly targeting 'ios' (e.g., feat(ios): ..., chore(ios): ...).
    - Touches only files under 'ios/' or iOS release scripts.
    - Explicitly announces adding or modifying the iOS companion app with ios/ files touched.
    """
    if commit.scope and commit.scope.lower() in ("ios", "companion"):
        return True

    ios_related_scripts = ("scripts/build_ios.sh", "scripts/ios_version.py")
    if commit.files:
        has_ios_files = any(
            f.startswith("ios/") or f in ios_related_scripts
            for f in commit.files
        )
        if has_ios_files:
            non_ios = [
                f for f in commit.files
                if not f.startswith("ios/") and f not in ios_related_scripts and f not in (".gitignore", "README.md")
            ]
            if not non_ios:
                return True
            if re.search(r"\b(ios|companion)\b", commit.message, re.IGNORECASE):
                return True

    return False


def run_git(cmd: list[str], cwd: Path = PROJECT_ROOT) -> str:
    """Run git command and return standard output."""
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
        raise RuntimeError(f"Git command failed: {' '.join(cmd)}\n{e.stderr}") from e


def get_desktop_tags(cwd: Path = PROJECT_ROOT) -> list[str]:
    """
    Get all desktop tags matching 'vX.Y.Z' in ascending Semantic Version order.
    Ignores iOS tags ('ios-v*').
    """
    raw_tags = run_git(["tag", "-l", "v*"], cwd=cwd)
    if not raw_tags:
        return []

    def semver_key(tag: str) -> tuple[int, ...]:
        clean = tag.lstrip("v")
        parts = clean.split(".")
        try:
            return tuple(int(p) for p in parts)
        except ValueError:
            return (0, 0, 0)

    tags = [t.strip() for t in raw_tags.splitlines() if re.match(r"^v\d+\.\d+(\.\d+)?$", t.strip())]
    return sorted(tags, key=semver_key)


def get_ios_tags(cwd: Path = PROJECT_ROOT) -> list[str]:
    """
    Get all iOS tags matching 'ios-vX.Y.Z' in ascending Semantic Version order.
    """
    raw_tags = run_git(["tag", "-l", "ios-v*"], cwd=cwd)
    if not raw_tags:
        return []

    def semver_key(tag: str) -> tuple[int, ...]:
        clean = tag.replace("ios-v", "")
        parts = clean.split(".")
        try:
            return tuple(int(p) for p in parts)
        except ValueError:
            return (0, 0, 0)

    tags = [t.strip() for t in raw_tags.splitlines() if re.match(r"^ios-v\d+\.\d+(\.\d+)?$", t.strip())]
    return sorted(tags, key=semver_key)


def get_commits_for_range(commit_range: str, cwd: Path = PROJECT_ROOT) -> list[CommitInfo]:
    """
    Fetch all commits in range with touched files.
    commit_range can be 'v0.1.0' (all commits up to tag) or 'v0.1.0..v0.2.0' or 'v0.20.0..HEAD'.
    """
    format_str = "%x1e%H%x1f%cs%x1f%an%x1f%s"
    cmd = ["log", "--reverse", f"--format={format_str}", "--name-only", commit_range]
    output = run_git(cmd, cwd=cwd)
    if not output:
        return []

    commits: list[CommitInfo] = []
    records = output.split("\x1e")

    for rec in records:
        rec = rec.strip()
        if not rec:
            continue

        lines = [line.strip() for line in rec.splitlines() if line.strip()]
        if not lines:
            continue

        header = lines[0]
        fields = header.split("\x1f")
        if len(fields) < 4:
            continue

        sha, date, author, subject = fields[0], fields[1], fields[2], fields[3]
        files = lines[1:]

        commit = parse_commit_line(sha, date, author, subject, files)
        commits.append(commit)

    return commits


def format_bullet(commit: CommitInfo) -> str:
    """Format single commit entry into clean markdown bullet."""
    desc = commit.description.strip()
    if not desc:
        desc = commit.message.strip()

    # Capitalize first letter of description for clean changelog typography
    if desc:
        desc = desc[0].upper() + desc[1:]

    if commit.scope:
        return f"- **{commit.scope}**: {desc}"
    return f"- {desc}"


def categorize_commits(commits: list[CommitInfo], target: str = "desktop") -> dict[str, list[str]]:
    """Group commits into Keep a Changelog categories, filtering noise and target exclusions."""
    categories: dict[str, list[str]] = {cat: [] for cat in CATEGORIES_ORDER}

    for commit in commits:
        if is_noise_commit(commit):
            continue

        if target == "desktop" and is_ios_commit(commit):
            continue
        elif target == "ios" and not is_ios_commit(commit):
            continue

        category = CATEGORY_MAPPING.get(commit.commit_type or "", "Other Changes")
        categories[category].append(format_bullet(commit))

    # Remove empty categories
    return {cat: items for cat, items in categories.items() if items}


def render_release_section(version: str, date_str: str, categorized: dict[str, list[str]]) -> str:
    """Render single version section in markdown."""
    lines = [f"## [{version}] - {date_str}"]
    if not categorized:
        lines.append("")
        lines.append("- Routine updates and maintenance.")
        lines.append("")
        return "\n".join(lines)

    for cat in CATEGORIES_ORDER:
        items = categorized.get(cat)
        if items:
            lines.append("")
            lines.append(f"### {cat}")
            lines.append("")
            for item in items:
                lines.append(item)

    lines.append("")
    return "\n".join(lines)


def render_unreleased_section(categorized: dict[str, list[str]]) -> str:
    """Render active Unreleased section in markdown."""
    lines = ["## [Unreleased]"]
    if not categorized:
        lines.append("")
        return "\n".join(lines)

    for cat in CATEGORIES_ORDER:
        items = categorized.get(cat)
        if items:
            lines.append("")
            lines.append(f"### {cat}")
            lines.append("")
            for item in items:
                lines.append(item)

    lines.append("")
    return "\n".join(lines)


def render_compare_links(tags: list[str], target: str = "desktop", repo_url: str = GITHUB_REPO_URL) -> str:
    """
    Render bottom reference links for GitHub compare and tags.
    """
    if not tags:
        return ""

    tag_prefix = "ios-v" if target == "ios" else "v"
    latest_tag = tags[-1]
    lines = [f"[Unreleased]: {repo_url}/compare/{latest_tag}...HEAD"]

    for i in range(len(tags) - 1, 0, -1):
        curr_ver = tags[i].replace(tag_prefix, "")
        prev_tag = tags[i - 1]
        curr_tag = tags[i]
        lines.append(f"[{curr_ver}]: {repo_url}/compare/{prev_tag}...{curr_tag}")

    first_ver = tags[0].replace(tag_prefix, "")
    lines.append(f"[{first_ver}]: {repo_url}/releases/tag/{tags[0]}")

    return "\n".join(lines)


def generate_backfill(cwd: Path = PROJECT_ROOT, target: str = "desktop") -> str:
    """Generate complete CHANGELOG content backfilling all tags up to HEAD."""
    tags = get_ios_tags(cwd=cwd) if target == "ios" else get_desktop_tags(cwd=cwd)
    tag_prefix = "ios-v" if target == "ios" else "v"
    if not tags:
        app_desc = "to this project" if target == "desktop" else "to the Rekordbox Companion iOS application"
        content_parts = [
            "# Changelog",
            "",
            f"All notable changes {app_desc} will be documented in this file.",
            "",
            "The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),",
            "and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).",
            "",
            "## [Unreleased]",
            "",
        ]
        return "\n".join(content_parts)

    app_desc = "to this project" if target == "desktop" else "to the Rekordbox Companion iOS application"
    content_parts = [
        "# Changelog",
        "",
        f"All notable changes {app_desc} will be documented in this file.",
        "",
        "The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),",
        "and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).",
        "",
    ]

    # 1. Unreleased section (latest tag .. HEAD)
    latest_tag = tags[-1]
    unreleased_commits = get_commits_for_range(f"{latest_tag}..HEAD", cwd=cwd)
    unreleased_categorized = categorize_commits(unreleased_commits, target=target)
    content_parts.append(render_unreleased_section(unreleased_categorized))

    # 2. Releases in reverse chronological order
    for i in range(len(tags) - 1, -1, -1):
        tag = tags[i]
        version = tag.replace(tag_prefix, "")
        date_str = run_git(["log", "-1", "--format=%cs", tag], cwd=cwd)

        if i == 0:
            commit_range = tag
        else:
            prev_tag = tags[i - 1]
            commit_range = f"{prev_tag}..{tag}"

        commits = get_commits_for_range(commit_range, cwd=cwd)
        categorized = categorize_commits(commits, target=target)
        section = render_release_section(version, date_str, categorized)
        content_parts.append(section)

    # 3. Compare links
    compare_links = render_compare_links(tags, target=target)
    content_parts.append(compare_links)
    content_parts.append("")

    return "\n".join(content_parts)


def rotate_changelog(
    new_version: str,
    date_str: str | None = None,
    target: str = "desktop",
    cwd: Path = PROJECT_ROOT,
    changelog_path: Path | None = None,
    dry_run: bool = False,
) -> bool:
    """
    Rotate [Unreleased] section into [new_version] - YYYY-MM-DD.
    Re-creates an empty [Unreleased] section and updates compare links.
    """
    if changelog_path is None:
        changelog_path = IOS_CHANGELOG_PATH if target == "ios" else CHANGELOG_PATH

    if not changelog_path.exists():
        print(f"⚠️  CHANGELOG not found at {changelog_path}. Generating backfill first...")
        full_content = generate_backfill(cwd=cwd, target=target)
        if not dry_run:
            changelog_path.write_text(full_content, encoding="utf-8")
        return True

    text = changelog_path.read_text(encoding="utf-8")
    if date_str is None:
        date_str = datetime.date.today().isoformat()

    clean_version = new_version.replace("ios-v", "").lstrip("v")
    tag_prefix = "ios-v" if target == "ios" else "v"

    # Match Unreleased section
    unreleased_pattern = re.compile(
        r"(## \[Unreleased\]\s*\n)(?P<body>.*?)(\n## \[(?P<next_ver>\d+\.\d+(?:\.\d+)?)\])",
        re.DOTALL,
    )
    match = unreleased_pattern.search(text)
    if not match:
        raise ValueError(f"Could not find '## [Unreleased]' section before release headers in {changelog_path}")

    def _replace_unreleased(m: re.Match[str]) -> str:
        body = m.group("body").strip()
        new_release_header = f"## [{clean_version}] - {date_str}"
        if body:
            new_section = f"{new_release_header}\n\n{body}\n"
        else:
            new_section = f"{new_release_header}\n\n- Routine updates and maintenance.\n"
        next_header = f"\n## [{m.group('next_ver')}]"
        return f"## [Unreleased]\n\n{new_section}{next_header}"

    new_text = unreleased_pattern.sub(_replace_unreleased, text, count=1)

    # Update compare links at bottom
    # Replace [Unreleased]: .../compare/<prev_tag>...HEAD
    # with [Unreleased]: .../compare/<new_tag>...HEAD\n[clean_version]: .../compare/<prev_tag>...<new_tag>
    compare_unreleased_pattern = re.compile(
        r"\[Unreleased\]:\s*(?P<url>https://github\.com/[^/]+/[^/]+/compare/)(?P<prev_tag>(?:ios-)?v[^\.]+[\d\.]+)\.\.\.HEAD",
    )
    unreleased_link_match = compare_unreleased_pattern.search(new_text)
    if unreleased_link_match:
        url_prefix = unreleased_link_match.group("url")
        prev_tag = unreleased_link_match.group("prev_tag")
        new_tag = f"{tag_prefix}{clean_version}"
        new_links = (
            f"[Unreleased]: {url_prefix}{new_tag}...HEAD\n"
            f"[{clean_version}]: {url_prefix}{prev_tag}...{new_tag}"
        )
        new_text = compare_unreleased_pattern.sub(new_links, new_text, count=1)

    if dry_run:
        print(f"[dry-run] Would rotate {changelog_path.name} for version {clean_version} ({date_str}).")
        return True

    changelog_path.write_text(new_text, encoding="utf-8")
    print(f"📝 Rotated {changelog_path.name} for version {clean_version} ({date_str}).")
    return True


def check_changelog(
    target: str = "desktop",
    cwd: Path = PROJECT_ROOT,
    changelog_path: Path | None = None,
) -> bool:
    """Validate CHANGELOG existence, standard headers, and compare links."""
    if changelog_path is None:
        changelog_path = IOS_CHANGELOG_PATH if target == "ios" else CHANGELOG_PATH

    if not changelog_path.exists():
        print(f"❌ Error: {changelog_path} does not exist. Run with --backfill to generate.", file=sys.stderr)
        return False

    content = changelog_path.read_text(encoding="utf-8")
    errors = []

    if "# Changelog" not in content:
        errors.append("Missing root '# Changelog' header")
    if "## [Unreleased]" not in content:
        errors.append("Missing '## [Unreleased]' section header")

    tags = get_ios_tags(cwd=cwd) if target == "ios" else get_desktop_tags(cwd=cwd)
    tag_prefix = "ios-v" if target == "ios" else "v"
    if tags:
        latest = tags[-1].replace(tag_prefix, "")
        if f"## [{latest}]" not in content:
            errors.append(f"Latest tag '{tags[-1]}' not found in release headers")

        expected_unreleased_link = f"[Unreleased]: {GITHUB_REPO_URL}/compare/{tags[-1]}...HEAD"
        if expected_unreleased_link not in content:
            errors.append(f"Missing or mismatched unreleased compare link: {expected_unreleased_link}")

    if errors:
        print(f"❌ {changelog_path.name} validation failed:", file=sys.stderr)
        for err in errors:
            print(f"   - {err}", file=sys.stderr)
        return False

    print(f"✅ {changelog_path.name} is valid and adheres to Keep a Changelog 1.1.0.")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rekordbox Collection Manager Desktop & Monorepo Changelog Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  %(prog)s --backfill           # Backfill CHANGELOG.md for all historical desktop releases
  %(prog)s --unreleased         # Print unreleased changes since latest tag
  %(prog)s --rotate 0.21.0      # Rotate [Unreleased] section into release 0.21.0
  %(prog)s --check              # Validate CHANGELOG.md structure and links
  %(prog)s --target ios --check # Validate ios/CHANGELOG.md
""",
    )

    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Generate or regenerate CHANGELOG.md backfilled from git history",
    )
    parser.add_argument(
        "--unreleased",
        action="store_true",
        help="Display unreleased changes since latest git tag",
    )
    parser.add_argument(
        "--rotate",
        metavar="VERSION",
        help="Rotate [Unreleased] section to specified version (e.g. 0.21.0)",
    )
    parser.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        help="Optional release date for --rotate (defaults to today)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate CHANGELOG.md integrity and compare links",
    )
    parser.add_argument(
        "--target",
        default="desktop",
        choices=["desktop", "ios"],
        help="Changelog target scope (default: desktop)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without modifying project files",
    )

    args = parser.parse_args()

    if args.backfill:
        print(f"⏳ Generating {args.target} changelog backfill from git history...")
        content = generate_backfill(target=args.target)
        out_path = IOS_CHANGELOG_PATH if args.target == "ios" else CHANGELOG_PATH
        if args.dry_run:
            print(content)
        else:
            out_path.write_text(content, encoding="utf-8")
            print(f"✅ Generated {out_path} successfully.")
        return

    if args.unreleased:
        tags = get_ios_tags() if args.target == "ios" else get_desktop_tags()
        if not tags:
            print(f"No {args.target} tags found.")
            return
        commits = get_commits_for_range(f"{tags[-1]}..HEAD")
        categorized = categorize_commits(commits, target=args.target)
        section = render_unreleased_section(categorized)
        print(section)
        return

    if args.rotate:
        rotate_changelog(
            new_version=args.rotate,
            date_str=args.date,
            target=args.target,
            dry_run=args.dry_run,
        )
        return

    if args.check:
        valid = check_changelog(target=args.target)
        sys.exit(0 if valid else 1)

    parser.print_help()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)
