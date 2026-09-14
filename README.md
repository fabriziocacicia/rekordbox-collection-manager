# Rekordbox Collection Fixer

A collection of standalone utilities and tools for managing, inspecting, and fixing Pioneer / AlphaTheta Rekordbox databases (`master.db` and DeviceSQL `exportLibrary.db`).

## Tools

### 1. Rename History (`rename-history`)
Interactively rename recorded DJ history playlists/sessions in your Rekordbox collection or USB export database.

#### Run by Name (Console Script)
```bash
# With uv
uv run rename-history

# Or if package is installed in your active environment
rename-history
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
