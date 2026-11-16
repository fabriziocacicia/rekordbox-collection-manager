# Statement of Intent: NOT Filters for Playlists & Tags (Issue #2)

## Confirmed Intent Summary

- **Outcome:** Add a "None (NOT)" exclusion match mode to both the Playlist and Tag multi-select filters in the Tracks view to hide tracks matching any of the selected items.
- **User:** DJ / Collection Curator filtering the library to find unsorted tracks or curate crates by excluding specific playlists (e.g., archive crates, already-sorted sets) or tags (e.g., unwanted subgenres or markers).
- **Why now:** (Issue #2) Current filters only support positive inclusion ("Any (OR)" and "All (AND)"), making it impossible to inspect collection tracks that do not belong to certain playlists or tags.
- **Success:**
  1. **Match Mode Bar:** A third button **"None (NOT)"** is added to `MultiSelectPopup` alongside *Any (OR)* and *All (AND)*, enabled on both Playlist and Tag filter popups.
  2. **Exclusion Logic:** When in NOT mode, `search_tracks` in `edit_play_count.py` excludes all tracks matching any of the checked items using `NOT IN (...)` query logic (NOR semantics).
  3. **Visual Feedback:** The filter button label, badge, and tooltip clearly indicate the active NOT mode (e.g., `📁 Playlists (2 [NOT]) ▾` or `📁 Playlists: NOT <Name> ▾`).
- **Constraint:** Must reuse the existing `MultiSelectSearchFilter` widget and `search_tracks` query pipeline without breaking existing AND/OR semantics or backwards compatibility.
- **Out of scope:** Per-item tri-state checkboxes in the list, or mixed inclusion/exclusion within the same single filter popup (e.g., "In Playlist A AND NOT Playlist B" at the same time).

---

## Technical Context & Integration Touchpoints

1. **Multi-Select Filter Component (`multi_select_filter.py`):**
   - In `MultiSelectPopup`:
     - Add `btn_match_none = QPushButton("None (NOT)")` to the match mode bar.
     - Tooltip: `"Hide tracks matching ANY selected value"`.
     - Accept match modes: `"any"`, `"all"`, `"none"` (or alias `"not"`).
     - Update button styling so active match mode button gets the emerald highlight.
   - In `MultiSelectSearchFilter`:
     - Update `_update_button_display()`: handle `mode_lbl = "NOT"` when `match_mode in ("none", "not")`.
     - When 1 item selected in NOT mode: display e.g. `{icon} {Title}: NOT {Name} ▾`.
     - When >1 items selected in NOT mode: display e.g. `{icon} {Title} ({count} [NOT]) ▾`.

2. **Database Track Search (`edit_play_count.py`):**
   - In `search_tracks()`:
     - Handle `tag_match_mode in ("none", "not")`:
       ```sql
       AND c.ID NOT IN (
           SELECT ContentID FROM djmdSongMyTag smt
           JOIN djmdMyTag mt ON smt.MyTagID = mt.ID
           WHERE mt.Name IN (...)
             AND (mt.rb_local_deleted = 0 OR mt.rb_local_deleted IS NULL)
             AND (smt.rb_local_deleted = 0 OR smt.rb_local_deleted IS NULL)
       )
       ```
     - Handle `playlist_match_mode in ("none", "not")`:
       ```sql
       AND c.ID NOT IN (
           SELECT sp.ContentID FROM djmdSongPlaylist sp
           JOIN djmdPlaylist p ON sp.PlaylistID = p.ID
           WHERE p.Attribute = 0 AND p.Name IN (...)
             AND (p.rb_local_deleted = 0 OR p.rb_local_deleted IS NULL)
             AND (sp.rb_local_deleted = 0 OR sp.rb_local_deleted IS NULL)
       )
       ```

3. **Background Worker & GUI Integration (`workers.py`, `tracks_view.py`):**
   - Ensure `TrackSearchWorker` passes `"none"` / `"not"` through without modification.
   - Verify filter resets in `TracksView._on_reset_filters()` reset both selections and match modes appropriately.
   - Verify filter count badge counts NOT-mode filters as active.

4. **Testing Suite:**
   - Unit tests in `tests/test_edit_play_count.py` validating NOR exclusion SQL execution for both tags and playlists.
   - Unit tests for `MultiSelectPopup` and `MultiSelectSearchFilter` verifying the "None (NOT)" button toggle, state reporting, and display formatting.
