# Spec: NOT Filters for Playlists & Tags — V1

## Objective
Enable DJs and collection curators to filter the Tracks view by excluding specific playlists or tags via a "None (NOT)" match mode. This allows curators to find unorganized tracks (e.g. tracks not yet added to any set crate or archive playlist) and exclude unwanted tags (e.g. tracks tagged with specific genres, statuses, or markers).

### User Stories & Acceptance Criteria
1. **Multi-Select Filter Match Mode ("None (NOT)"):**
   - As a user opening the Playlist or Tag filter popup in Tracks view, I see three match mode buttons:
     - `Any (OR)`: Show tracks matching at least one selected item.
     - `All (AND)`: Show tracks matching all selected items simultaneously.
     - `None (NOT)`: Hide tracks matching any of the selected items (NOR logic).
   - Clicking `None (NOT)` activates NOT mode and updates the active styling on the match mode bar.
2. **Exclusion Query Semantics (`search_tracks`):**
   - As a user filtering by one or more playlists with `playlist_match_mode="none"`, tracks assigned to *any* of those playlists are excluded from the search results (`AND c.ID NOT IN (...)`).
   - As a user filtering by one or more tags with `tag_match_mode="none"`, tracks assigned to *any* of those tags are excluded from the search results (`AND c.ID NOT IN (...)`).
   - Tracks not assigned to the excluded playlists/tags remain visible (subject to other active search criteria).
3. **Filter Button Presentation:**
   - Single item selected in NOT mode: `{Icon} {Title}: NOT {Name} ▾` (e.g. `📁 Playlists: NOT Warmup ▾` or `🏷️ Tags: NOT #ambient ▾`).
   - Multiple items selected in NOT mode: `{Icon} {Title} ({count} [NOT]) ▾` (e.g. `📁 Playlists (3 [NOT]) ▾` or `🏷️ Tags (2 [NOT]) ▾`).
   - Hover tooltip reflects match mode: `{Title} filter (Match: NOT - Exclude any):\n{Preview}`.
   - Filter count badge counts NOT-mode selections as active filters.
   - Resetting filters clears selections and resets match mode to default (`"any"`).

---

## Tech Stack
- **Core / GUI:** Python >= 3.11, PySide6 (Qt6), SQLAlchemy, SQLCipher (via `pyrekordbox`), `uv`
- **Testing:** `pytest`, `pytest-qt` (Headless Qt configured via `QT_QPA_PLATFORM=offscreen` in `tests/conftest.py`)

---

## Commands
- Run focused database search tests: `uv run pytest tests/test_edit_play_count.py -k "test_search_tracks"`
- Run focused GUI filter tests: `uv run pytest tests/test_gui_models.py -k "test_multi_select"`
- Run full test suite: `uv run pytest`
- Launch GUI: `uv run rekordbox-manager`

---

## Project Structure
- `src/rekordbox_collection_manager/gui/multi_select_filter.py`
  - In `MultiSelectPopup`:
    - Add `btn_match_none = QPushButton("None (NOT)")` to match mode bar layout.
    - Set tooltip: `"Hide tracks matching ANY selected value"`.
    - Support match modes: `"any"`, `"all"`, `"none"` (with alias `"not"`).
    - Update `_update_match_buttons_style()` to highlight the active mode button in emerald green.
    - Update `get_match_mode()`, `set_selected()`, and emit mode in `selection_committed`.
  - In `MultiSelectSearchFilter`:
    - Update `_update_button_display()` to handle `mode_lbl = "NOT"` when `match_mode in ("none", "not")`.
    - Format single-item text: `{prefix}{self.title_text}: NOT {display_val} ▾`.
    - Format multi-item text: `{prefix}{self.title_text} ({count} [NOT]) ▾`.
    - Update tooltip with `(Match: NOT - Exclude any)`.
- `src/rekordbox_collection_manager/edit_play_count.py`
  - In `search_tracks()`:
    - Handle `tag_match_mode in ("none", "not")`:
      Construct `AND c.ID NOT IN (SELECT smt.ContentID FROM djmdSongMyTag smt JOIN djmdMyTag mt ON smt.MyTagID = mt.ID WHERE mt.Name IN (...) AND ...)`.
    - Handle `playlist_match_mode in ("none", "not")`:
      Construct `AND c.ID NOT IN (SELECT sp.ContentID FROM djmdSongPlaylist sp JOIN djmdPlaylist p ON sp.PlaylistID = p.ID WHERE p.Attribute = 0 AND p.Name IN (...) AND ...)`.
- `src/rekordbox_collection_manager/gui/workers.py`
  - Ensure `TrackSearchWorker` accepts and passes `tag_match_mode` and `playlist_match_mode` to `search_tracks()`.
- `src/rekordbox_collection_manager/gui/tracks_view.py`
  - Ensure `playlist_filter_widget` and `tag_filter_widget` match modes are queried during search execution.
  - In `_on_reset_filters()`, ensure match mode is reset to `"any"`.
- `tests/test_edit_play_count.py`
  - Unit tests verifying `search_tracks` with `playlist_match_mode="none"` and `tag_match_mode="none"` with single and multiple items.
- `tests/test_gui_models.py`
  - GUI unit tests verifying `MultiSelectPopup` "None (NOT)" button interaction, state persistence, signal emission, and button display rendering.

---

## Code Style

### Example Implementation Snippet (`multi_select_filter.py`)
```python
# Match Mode Bar in MultiSelectPopup.__init__
self.btn_match_none = QPushButton("None (NOT)")
self.btn_match_none.setFixedHeight(22)
self.btn_match_none.setCursor(Qt.CursorShape.PointingHandCursor)
self.btn_match_none.setToolTip("Hide tracks matching ANY selected value")
self.btn_match_none.clicked.connect(lambda: self._set_match_mode("none"))
match_layout.addWidget(self.btn_match_none)

# Button display in MultiSelectSearchFilter._update_button_display
if match_mode in ("none", "not"):
    mode_lbl = "NOT"
elif match_mode == "all":
    mode_lbl = "ALL"
else:
    mode_lbl = "ANY"

if count == 1:
    display_val = selected[0]
    if len(display_val) > 16:
        display_val = display_val[:14] + "…"
    if mode_lbl == "NOT":
        self.button.setText(f"{prefix}{self.title_text}: NOT {display_val} ▾")
    else:
        self.button.setText(f"{prefix}{self.title_text}: {display_val} ▾")
else:
    self.button.setText(f"{prefix}{self.title_text} ({count} [{mode_lbl}]) ▾")
```

### Example SQL Query Snippet (`edit_play_count.py`)
```python
if str(playlist_match_mode).strip().lower() in ("none", "not"):
    pl_placeholders = [f":pl_f_{i}" for i in range(len(playlist_names))]
    for i, name in enumerate(playlist_names):
        playlist_params[f"pl_f_{i}"] = name
    playlist_clause = f"""AND c.ID NOT IN (
        SELECT sp.ContentID FROM djmdSongPlaylist sp
        JOIN djmdPlaylist p ON sp.PlaylistID = p.ID
        WHERE p.Attribute = 0 AND p.Name IN ({", ".join(pl_placeholders)})
          AND (p.rb_local_deleted = 0 OR p.rb_local_deleted IS NULL)
          AND (sp.rb_local_deleted = 0 OR sp.rb_local_deleted IS NULL)
    )"""
```

---

## Testing Strategy
1. **Database Search Unit Tests (`tests/test_edit_play_count.py`):**
   - Setup: Test database with tracks across multiple playlists (`Festival 2026`, `Club Night`, `Unsorted`) and tags (`Peak Time`, `Chill`, `Untagged`).
   - Single playlist NOT filter: excluding `Festival 2026` returns only tracks not in that playlist.
   - Multi-playlist NOT filter: excluding `["Festival 2026", "Club Night"]` returns only tracks in neither playlist.
   - Single tag NOT filter: excluding `Peak Time` returns only tracks without that tag.
   - Multi-tag NOT filter: excluding `["Peak Time", "Chill"]` returns only tracks without either tag.
   - Combined filters: Verify combining a positive tag filter with a negative playlist filter functions as expected.
2. **GUI Unit Tests (`tests/test_gui_models.py`):**
   - Verify `MultiSelectPopup` renders `btn_match_none`.
   - Verify clicking `btn_match_none` emits `selection_committed` with match mode `"none"`.
   - Verify `widget.set_selected(["Afro House"], match_mode="none")` formats button as `🏷️ Tags: NOT Afro House ▾`.
   - Verify `widget.set_selected(["Afro House", "Tech House"], match_mode="none")` formats button as `🏷️ Tags (2 [NOT]) ▾`.
   - Verify `widget.clear_selection()` resets match mode to default `"any"`.

---

## Boundaries
- **Always:** Use parameter binding (`:tag_f_0`, `:pl_f_0`) for all dynamic playlist and tag names in SQL queries.
- **Always:** Respect Rekordbox soft-delete flags (`rb_local_deleted = 0 OR rb_local_deleted IS NULL`).
- **Always:** Ensure `NullPool` and non-blocking worker threads are preserved.
- **Ask first:** Modifying any other filter components or changing the default match mode away from `"any"`.
- **Never:** Break existing `"any"` or `"all"` match mode behavior.
- **Never:** Mutate the Rekordbox SQLite database schema.

---

## Success Criteria
1. The `MultiSelectPopup` match bar displays three options: `Any (OR)`, `All (AND)`, and `None (NOT)`.
2. Selecting `None (NOT)` excludes any tracks matching any selected playlist or tag from the tracks table.
3. The button display in `TracksView` clearly communicates the NOT state (e.g., `📁 Playlists: NOT Warmup ▾` or `📁 Playlists (2 [NOT]) ▾`).
4. Resetting filters returns match mode to `"any"` and clears selection.
5. All database search tests and GUI unit tests pass with zero regressions.

---

## Out of Scope (Deferred)
- Mixed inclusion/exclusion inside the same filter (e.g. "Playlist A AND NOT Playlist B" in one popup).
- Tri-state checkbox controls per item.
- Exclude modes for Format, Key, or BPM filters.

---

## Open Questions
- None. Requirements and scope were fully clarified during the interview phase.
