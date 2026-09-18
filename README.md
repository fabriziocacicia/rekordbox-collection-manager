# Rekordbox Collection Manager

A native macOS desktop application and collection of standalone utilities for managing, inspecting, and fixing Pioneer / AlphaTheta Rekordbox databases (`master.db` and DeviceSQL `exportLibrary.db`).

## Tools

### 1. Rename History (`rename-history`)
Interactively rename recorded DJ history playlists/sessions in your Rekordbox collection or USB export database.

Features:
- **Chronological Tree Ordering**: Automatically realigns Rekordbox `djmdHistory.Seq` so months are strictly chronological (January to December, month 1 to 12) instead of alphabetical, and history sessions are sorted chronologically from day 01 (top) to day 31 (bottom).
- **Auto-folder Hierarchy**: Ensures target Year and Month folders exist when moving/renaming sessions.
- **Auto-align on Rename**: Automatically sorts and realigns database order whenever a session is renamed.
- **Standalone Realign**: Realign all months and sessions anytime via the interactive menu or `--sort` / `--realign` CLI flag.

#### Run by Name (Console Script)
```bash
# Interactive mode
uv run rename-history

# Or if package is installed in your active environment
rename-history

# Realign/sort all months and history sessions without interactive prompt:
uv run rename-history --sort
```

You can optionally pass a database path:
```bash
uv run rename-history /path/to/master.db
# or
uv run rename-history -d /Volumes/USB/PIONEER/exportLibrary.db
```

#### Run by Script
```bash
# Direct script execution
uv run python src/rekordbox_collection_manager/rename_history.py

# Or as a module
uv run python -m rekordbox_collection_manager.rename_history
```

---

### 2. Edit Play Count (`edit-play-count`)
Interactively increment (+1) or decrement (-1) the play count of songs in your Rekordbox collection (`master.db`) or USB export database (`exportLibrary.db`).

Features:
- **Interactive track search**: Type track title keywords to filter matches.
- **Arrow-key selection**: Navigate matching results with arrow keys (↑ / ↓) and press Enter to select.
- **Safety check**: Automatically checks whether Rekordbox is running and prompts you to close it before any database modifications.
- **Floor at zero**: Decrementing will never cause play count to go negative.

#### Run by Name (Console Script)
```bash
# Interactive mode (prompts for action: +1, -1, or per-track):
uv run edit-play-count

# Search directly for a track title:
uv run edit-play-count "Titanium"

# Automatically increment (+1) on selection:
uv run edit-play-count --inc
uv run edit-play-count "Titanium" --inc

# Automatically decrement (-1) on selection:
uv run edit-play-count --dec
uv run edit-play-count "Titanium" --dec

# With custom database path:
uv run edit-play-count -d /Volumes/USB/PIONEER/exportLibrary.db
```

#### Run by Script
```bash
uv run python src/rekordbox_collection_manager/edit_play_count.py
# or
uv run python -m rekordbox_collection_manager.edit_play_count
```

---

### 3. Desktop Application (`rekordbox-manager` / `rekordbox-gui`)

**Rekordbox Collection Manager** is a native macOS desktop application built with **PySide6 (Qt6)** featuring a Pioneer DJ-inspired dark theme. It combines all tools into a single desktop interface with live Rekordbox safety detection.

Features:
- **Safety Guard Banner**: Live process monitor warns if Rekordbox is running and safely locks write transactions to prevent database conflicts.
- **History Sessions View**:
  - Full history hierarchy loaded at once.
  - Year/Month folder tree filter.
  - In-place editable cells for Date and Session Name.
  - `● Staged` indicators showing modified items.
  - One-click *Auto-Extract Dates from Names* and *Realign Chronology*.
  - Atomic *Save All Changes* transaction.
- **Play Count Manager View**:
  - Instant as-you-type search across Track Title, Artist, Album, and Filename.
  - In-line editable numeric play counts.
  - Dedicated `[-]` and `[+]` quick stepper buttons on each row.
  - Keyboard shortcuts (`+` and `-` keys adjust selected track).
  - Multi-select batch actions (+1, -1, reset to 0, or set custom value).
- **Quick Backup**: One-click desktop snapshot of your database file.

#### Launching the Desktop App

You can launch the app from the terminal or build a native macOS `.app` bundle:

**Option A: Native macOS App (Dock & Applications)**
```bash
# Build the native macOS .app bundle
./scripts/build_app.sh

# Open the native macOS app
open "dist/Rekordbox Collection Manager.app"
```
> **Tip:** You can drag `dist/Rekordbox Collection Manager.app` to your **Applications** folder or Dock to keep it permanently with its native icon and title!

**Option B: From Terminal**
```bash
# Launch with default master.db
uv run rekordbox-manager
# or
uv run rekordbox-gui

# Launch with custom database (e.g. USB exportLibrary.db)
uv run rekordbox-manager -d /Volumes/USB/PIONEER/exportLibrary.db
```

---


## Adding New Tools to this Repo

Each tool is self-contained in a single file under `src/rekordbox_collection_manager/`:

1. **Create the tool file**: `src/rekordbox_collection_manager/<your_tool>.py` with a `main()` function and `if __name__ == "__main__": main()`.
2. **Register the tool by name**: In `pyproject.toml`, add an entry under `[project.scripts]`:
   ```toml
   [project.scripts]
   rename-history = "rekordbox_collection_manager.rename_history:main"
   your-tool = "rekordbox_collection_manager.your_tool:main"
   ```

## Development & Testing

Run the test suite:
```bash
uv run python -m unittest discover tests
```
