# Rekordbox Collection Fixer

A collection of standalone utilities and tools for managing, inspecting, and fixing Pioneer / AlphaTheta Rekordbox databases (`master.db` and DeviceSQL `exportLibrary.db`).

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
uv run python src/rekordbox_collection_fixer/rename_history.py

# Or as a module
uv run python -m rekordbox_collection_fixer.rename_history
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
uv run python src/rekordbox_collection_fixer/edit_play_count.py
# or
uv run python -m rekordbox_collection_fixer.edit_play_count
```

---


## Adding New Tools to this Repo

Each tool is self-contained in a single file under `src/rekordbox_collection_fixer/`:

1. **Create the tool file**: `src/rekordbox_collection_fixer/<your_tool>.py` with a `main()` function and `if __name__ == "__main__": main()`.
2. **Register the tool by name**: In `pyproject.toml`, add an entry under `[project.scripts]`:
   ```toml
   [project.scripts]
   rename-history = "rekordbox_collection_fixer.rename_history:main"
   your-tool = "rekordbox_collection_fixer.your_tool:main"
   ```

## Development & Testing

Run the test suite:
```bash
uv run python -m unittest discover tests
```
