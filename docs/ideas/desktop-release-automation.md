# Desktop Release Automation (Issue #13)

## Problem Statement
How might we automate the end-to-end desktop release pipeline — version bumping, smart changelog rotation, PyInstaller compilation, packaging, git tagging/pushing, and GitHub release creation — with a transactional, fail-safe CLI that provides one-shot execution, interactive preview guards, and zero-drift state recovery?

## Recommended Direction: Transactional Conductor CLI (`desktop_version.py --release`)
Enhance `scripts/desktop_version.py` with an atomic `--release` workflow that bundles all manual steps into a single verified pipeline. It operates across two explicit safety zones separated by a confirmation gate:

1. **Zone 1 (Local Verification & Packaging):**
   - **Pre-flight Audit:** Enforce clean working tree (`git status --porcelain`), branch up-to-date with `origin/main`, remote tag collision check (`git ls-remote --tags origin vX.Y.Z`), and tool availability (`gh auth status`, `uv`, `clang`, `ditto`).
   - **Standalone Compilation:** Invoke `scripts/build_app.sh --package` to compile and package `Rekordbox-Collection-Manager-v<version>-macOS-arm64.zip`.
   - **Metadata & Smart Changelog Sync:** Bump `pyproject.toml` and synchronize `uv.lock`. Inspect `CHANGELOG.md`: if `[Unreleased]` has curated notes, rotate it into the new version; if empty, auto-extract desktop conventional commits from `git log` since the last tag via `scripts/changelog.py`, populate the section, and rotate.
2. **Confirmation Gate:**
   - Display a release summary (version bump, package size, release notes preview).
   - In interactive mode, prompt `Proceed with push and publishing GitHub release? [Y/n]`.
   - When `-y` / `--yes` is passed (or non-interactive), bypass prompt for true one-shot automation.
3. **Zone 2 (Remote Publish & State Recovery):**
   - Commit `chore(desktop): bump version to X.Y.Z` and create annotated tag `vX.Y.Z`.
   - Push commit to `origin/main` and tag to `origin vX.Y.Z`.
   - Publish GitHub Release via `gh release create vX.Y.Z <zip_path> --title "Rekordbox Collection Manager vX.Y.Z" --notes-file <notes_file>` (supporting optional `--draft`).
   - **Failure Safety (Strategy A):** Checkpoint initial git SHA before file changes. If build fails or user cancels at prompt, cleanly roll back files and local tag. If network or upload fails after push, preserve artifacts and print exact resume command.

## Key Assumptions to Validate
- [ ] **GitHub Authentication:** `gh auth status` has write/release permissions to `fabriziocacicia/rekordbox-collection-manager`.
- [ ] **PyInstaller Environment:** Xcode CLI tools (`clang`, `ditto`, `sips`, `iconutil`) and `uv` are available on the release host.
- [ ] **Branch Alignment:** Release is run from `main` tracked against `origin/main`.
- [ ] **Monorepo Commit Filtering:** Commits touching `ios/` or scoped as `ios` are strictly filtered out of desktop changelogs via existing `scripts/changelog.py:is_ios_commit()`.

## MVP Scope
1. **CLI Flag Additions to `scripts/desktop_version.py`:**
   - `--release`: Activates the end-to-end release pipeline (requires action: `patch`, `minor`, `major`, or `set <version>`).
   - `-y`, `--yes`: Non-interactive one-shot execution (bypasses preview prompt).
   - `--draft`: Marks GitHub release as draft instead of immediately public.
   - `--dry-run`: Full simulation displaying calculated version, changelog diff, and GitHub release notes preview without touching files, compiling binaries, or calling network APIs.
2. **Pre-flight Check Suite:**
   - Git working directory cleanliness check.
   - Upstream branch tracking and divergence check (`HEAD == origin/main`).
   - Remote tag conflict check (`git ls-remote`).
   - Binaries check (`gh`, `uv`, `clang`, `ditto`).
3. **Smart Changelog & Notes Extraction:**
   - Integration with `scripts.changelog` to extract release notes and synchronize `CHANGELOG.md` with zero drift.
4. **Binary Build & Packaging Integration:**
   - Call `scripts/build_app.sh --package` and locate resulting `.zip`.
5. **Transactional Checkpoint & Rollback:**
   - Clean rollback on local build failure or interactive cancellation (`git reset --hard <checkpoint>`, `git tag -d <tag>`).
   - Idempotent resume output if `gh release create` drops network.
6. **Test Suite (`tests/test_desktop_release.py`):**
   - Unit tests for pre-flight validators, rollback handler, changelog note extraction, and CLI argument parsing.

## Not Doing List
- ❌ **Not running PyInstaller in GitHub Actions CI:** Builds remain local to developer Apple Silicon Mac to avoid macOS runner costs and complex secrets management.
- ❌ **Not automating iOS companion releases in this script:** iOS release cycle remains separate under `scripts/ios_version.py`.
- ❌ **Not force-pushing or deleting remote tags upon network drop:** Once pushed to remote GitHub, automatic destructive rollbacks are prohibited to protect git integrity; resume instructions are printed instead.
- ❌ **Not altering existing standalone flags:** `--commit`, `--tag`, `--no-uv-lock`, `--no-changelog`, and standalone version queries continue to work with zero regressions.

## Open Questions & Verification
- What should the default GitHub Release title format be? (Proposed: `"Rekordbox Collection Manager vX.Y.Z"`).
- Should the script clean up intermediate `dist/` artifacts before building? (Proposed: Clean only PyInstaller intermediates, preserving old release archives unless matching the new version).
