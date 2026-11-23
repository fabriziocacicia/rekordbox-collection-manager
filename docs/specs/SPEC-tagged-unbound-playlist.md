# Spec: Dynamic Catch-All Playlist for Playlistless Tagged Tracks (πagged-Unbound) — V1

## Objective
Provide an automated, self-reconciling root-level playlist named `πagged-Unbound` in Rekordbox `master.db`. This playlist ensures that all tracks with at least one MyTag that do not belong to any regular user-created playlist are automatically grouped together, allowing Rekordbox Sync Manager to export them to USB drives for standalone CDJ/XDJ browsing and MyTag filtering.

This feature uses a **"Shared Triggers, Decoupled Execution"** architecture: it fires on the exact same lifecycle events as the synchronization daemon (Rekordbox exit, mobile event ingestion, daemon startup, manual sync), but runs as a fault-isolated, local-first operation that never depends on Turso credentials, network connectivity, or cloud availability.

---

### User Stories & Acceptance Criteria

1. **Shared Event Triggers (Orchestration):**
   - The playlist reconciliation runs on the exact same triggers that drive synchronization:
     - **Trigger A (Rekordbox Termination):** Fired when Rekordbox closes (via Cocoa `NSWorkspace` notification or process polling).
     - **Trigger B (Mobile Event Ingestion):** Fired immediately after pending mobile changelog events (e.g. tag additions made in the iOS app) are written into local `master.db`.
     - **Trigger C (Daemon Startup):** Fired when `rekordbox-daemon` starts up and Rekordbox is closed.
     - **Trigger D (Manual Invocation):** Fired via `rekordbox-daemon sync`, `sync-unbound` CLI, or Desktop GUI Sync actions.

2. **Decoupled & Fault-Isolated Execution:**
   - Local reconciliation logic is housed in a standalone module (`unbound_playlist.py`) with zero Turso or network dependencies.
   - If Turso credentials are not configured, or if Turso experiences network failures/timeouts, local reconciliation **still runs and succeeds**.
   - If local reconciliation encounters an error, it logs the failure and does not corrupt or abort subsequent independent tasks.
   - If Turso is configured, local reconciliation completes *before* Turso exports collection snapshots, ensuring the cloud copy reflects `πagged-Unbound`.

3. **Database Reconciliation Rules:**
   - Always verify Rekordbox is not running (`is_rekordbox_running()`).
   - Create a safety backup of `master.db` before applying mutations (`create_database_backup()`).
   - Ensure `πagged-Unbound` exists in `djmdPlaylist` (`Attribute = 0`, `ParentID = 'root'`). If absent or soft-deleted, create or reactivate it with appropriate sequence numbering so it sorts at the end.
   - Identify the target set of tracks:
     - Active content in `djmdContent` (`rb_local_deleted = 0 OR NULL`).
     - Exclude streaming tracks (`build_exclude_streaming_clause`).
     - Active MyTag association in `djmdSongMyTag`.
     - Not present in any active regular user playlist (`djmdPlaylist` where `Attribute = 0` and `ID != unbound_playlist_id`).
     - Smart playlists (`Attribute = 2`) and history sessions (`djmdHistory`) do **not** disqualify a track.
   - Diff target tracks against current entries in `djmdSongPlaylist` for `πagged-Unbound`:
     - Tracks no longer qualifying (added to a user playlist, or tags removed) are removed/soft-deleted.
     - Newly qualifying tracks are appended with sequential `TrackNo` values.
   - If changes occur, update playlist timestamps and bump sequence numbers.

4. **Alphabetical Sorting Invariant:**
   - The playlist name uses the Greek lowercase letter `π` (`πagged-Unbound`, Unicode U+03C0) so that in standard lexicographical ordering (ASCII followed by Unicode Greek), Rekordbox displays the playlist at the bottom of the playlist tree without cluttering active gig playlists.

---

## Tech Stack
- **Core Engine:** Python >= 3.11, SQLAlchemy, SQLCipher (via `pyrekordbox`), `uv`
- **Background Daemon:** Cocoa `NSWorkspace` / `launchd` via `daemon.py`
- **GUI Integration:** PySide6 (Qt6) worker thread in `gui/workers.py`
- **Testing:** `pytest` (Headless Qt configured via `QT_QPA_PLATFORM=offscreen` in `tests/conftest.py`)

---

## Commands
- Run focused unbound playlist tests: `uv run pytest tests/test_unbound_playlist.py`
- Run full test suite: `uv run pytest`
- Run standalone CLI reconciliation: `uv run sync-unbound`
- Launch GUI: `uv run rekordbox-manager`

---

## Architecture: Shared Triggers, Decoupled Execution

```
                       [ Lifecycle Triggers ]
     ┌───────────────────────────┬───────────────────────────┐
     │                           │                           │
Rekordbox Quit           Mobile Events Ingested       Manual Trigger / Start
     │                           │                           │
     └───────────────────────────┼───────────────────────────┘
                                 │
                                 ▼
                     [ Sync Cycle Coordinator ]
                                 │
             ┌───────────────────┴───────────────────┐
             │                                       │
             ▼ (Fault Isolated)                      ▼ (Independent)
[ Local Unbound Reconciliation ]             [ Turso Cloud Pipeline ]
 - Zero network calls                        - Ingest mobile events
 - Runs on master.db directly                - Export snapshot
 - Works even if Turso offline               - Skips cleanly if not configured
```

---

## Project Structure
- `src/rekordbox_collection_manager/unbound_playlist.py` (New module)
  - Pure local module; zero Turso imports.
  - `UNBOUND_PLAYLIST_NAME = "πagged-Unbound"`
  - `ensure_unbound_playlist_exists(conn, cols, has_rb_del, del_filter) -> str`: Finds, creates, or reactivates `πagged-Unbound` under `ParentID = 'root'`.
  - `get_unbound_target_track_ids(conn) -> Set[str]`: SQL query resolving all non-streaming, tagged tracks not in any user playlist (`Attribute = 0`).
  - `reconcile_unbound_playlist(engine: Optional[Engine] = None, db_path: Optional[str] = None, force: bool = False) -> Dict[str, Any]`: Reconciles `djmdSongPlaylist`, applies safety checks, creates backup, and returns stats `{"added": int, "removed": int, "total": int}`.
  - CLI entrypoint: `sync_unbound_cli()` exposed via `pyproject.toml` as `sync-unbound`.
- `src/rekordbox_collection_manager/daemon.py`
  - Shares triggers: Calls `reconcile_unbound_playlist()` on Rekordbox quit, on daemon startup, and after mobile changes arrive.
- `src/rekordbox_collection_manager/turso_sync.py`
  - In `run_sync_cycle()`: Safely invokes `reconcile_unbound_playlist(engine)` in a decoupled block so cloud exports reflect current unbound state, but network/cloud issues never block local reconciliation.
  - In `export_collection_to_turso()`: When exporting `playlist_tracks`, execute `DELETE FROM playlist_tracks WHERE playlist_id = :unbound_id;` prior to uploading the fresh entries for `πagged-Unbound`. This ensures obsolete tracks pruned locally are cleanly removed in Turso without stale rows lingering in the cloud or iOS app.
- `src/rekordbox_collection_manager/gui/`
  - `workers.py`: `UnboundPlaylistReconcileWorker(QThread)` for asynchronous execution in desktop app.
  - `playlists_view.py`: Button or menu action to trigger reconciliation and view result status.
- `tests/test_unbound_playlist.py` (New test file)
  - Isolated test fixtures validating:
    - Creation and reactivation of `πagged-Unbound`.
    - Tracks with tags but no playlist are added.
    - Tracks added to regular playlists are removed.
    - Smart playlists and history sessions do not cause removal.
    - Streaming tracks are excluded.
    - Idempotency when no changes occur.
    - Runs completely without Turso or network.

---

## Query Logic Specification

### Target Tracks Query (`get_unbound_target_track_ids`)
```sql
SELECT DISTINCT c.ID
FROM djmdContent c
JOIN djmdSongMyTag smt ON smt.ContentID = c.ID
WHERE 1=1
  -- Active content
  AND (c.rb_local_deleted = 0 OR c.rb_local_deleted IS NULL)
  -- Active tag
  AND (smt.rb_local_deleted = 0 OR smt.rb_local_deleted IS NULL)
  -- Exclude streaming
  AND (c.StockType = 0 OR c.StockType IS NULL)
  -- Not in any regular user playlist (Attribute = 0, excluding πagged-Unbound itself)
  AND c.ID NOT IN (
      SELECT sp.ContentID
      FROM djmdSongPlaylist sp
      JOIN djmdPlaylist p ON sp.PlaylistID = p.ID
      WHERE p.Attribute = 0
        AND p.Name != :unbound_name
        AND (p.rb_local_deleted = 0 OR p.rb_local_deleted IS NULL)
        AND (sp.rb_local_deleted = 0 OR sp.rb_local_deleted IS NULL)
  );
```

### Reconciliation Diffing
```python
# current entries in πagged-Unbound
current_entries = {row.ContentID: row for row in fetch_current_entries(conn, playlist_id)}
target_ids = get_unbound_target_track_ids(conn)

to_remove = set(current_entries.keys()) - target_ids
to_add = target_ids - set(current_entries.keys())

# Soft-delete or remove to_remove
# Insert to_add with incremented TrackNo
```

---

## Testing Strategy
- **Framework:** `pytest` using in-memory or temporary SQLite databases initialized with `master.db` schema fixtures (`tests/conftest.py`).
- **Zero Network/Cloud Requirement:** Tests execute entirely offline with no Turso mocking or dependencies.
- **Test Scenarios:**
  1. `test_ensure_unbound_playlist_created_at_root`: Verifies playlist creation with `ParentID = 'root'` and `Attribute = 0`.
  2. `test_track_with_tags_added_when_no_playlist`: Verifies tagged track is inserted into `djmdSongPlaylist`.
  3. `test_track_removed_when_added_to_user_playlist`: Verifies track is removed from `πagged-Unbound` once assigned to a user playlist.
  4. `test_smart_playlist_and_history_ignored`: Verifies presence in `djmdHistory` or a smart playlist (`Attribute = 2`) does NOT prevent track from being in `πagged-Unbound`.
  5. `test_streaming_track_excluded`: Verifies Soundcloud/Beatport/Tidal streaming tracks are never added.
  6. `test_idempotent_sync`: Verifies running multiple times with no underlying collection changes executes cleanly with 0 adds / 0 removes.
  7. `test_decoupled_execution_when_turso_fails`: Verifies reconciliation runs and succeeds even if cloud sync fails or is unconfigured.

---

## Boundaries
- **Always:**
  - Verify Rekordbox is not running before mutating `master.db` (`is_rekordbox_running()`).
  - Create a safety backup before writing changes to `master.db` (`create_database_backup()`).
  - Follow SQLCipher / SQLite connection pooling rules (`NullPool`, timeout 30s).
- **Ask first:**
  - Altering the playlist name away from `πagged-Unbound`.
  - Changing the exclusion rule to count smart playlists as user playlists.
- **Never:**
  - Mutate `master.db` while Pioneer Rekordbox process is running without explicit `force=True`.
  - Fail or skip local playlist reconciliation because of a Turso network or auth error.
  - Hard-delete user-created playlists or tracks outside `πagged-Unbound`.

---

## Success Criteria
- [ ] `πagged-Unbound` appears at the root of the Rekordbox playlist tree and sorts last.
- [ ] Reconcile logic shares all daemon triggers (app close, mobile events, startup, manual sync) while remaining 100% fault-isolated from Turso.
- [ ] Dedicated CLI command `sync-unbound` works directly against local `master.db`.
- [ ] All non-streaming tracks with >= 1 MyTag and 0 regular user playlists are present in `πagged-Unbound`.
- [ ] Tracks added to any regular user playlist are pruned on the next reconciliation.
- [ ] 100% test pass rate for `tests/test_unbound_playlist.py` and regression suite.

---

## Out of Scope (Deferred)
- Multi-playlist grouping (e.g., splitting unbound tracks into sub-folders per genre or rating).
- Live real-time sync while Rekordbox is open (restricted by Rekordbox SQLite database locking).

---

## Open Questions
- None.
