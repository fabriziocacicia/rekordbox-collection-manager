# Spec: Purchasable Track "Buy At" Store Tracking — V1

## Objective
Enable DJs and music collectors to track which digital download store (Beatport, Bandcamp, Qobuz, Traxsource) has the best price or is the designated store to purchase each purchasable track from.

### User Stories & Acceptance Criteria
1. **Tracks Table View ("Purchasable" Filter):**
   - As a user reviewing purchasable tracks (filtered by `"💳 Purchasable (≥3 plays)"` and sorted by play count), I want to see a **"Buy At"** column in the table showing where I plan to buy each track (or `—` if unresearched).
   - Clicking a cell in the "Buy At" column opens a store selection dialog allowing me to assign one of the 4 stores (`Beatport`, `Bandcamp`, `Qobuz`, `Traxsource`) or clear it.
2. **Track Detail View:**
   - As a user viewing an unpurchased track in the detail screen, I want to see an interactive badge in the Hero row next to `[Downloaded]`:
     - If unset: `[+ Buy at]`
     - If set: `[Buy at: <Store>]`
   - Clicking this badge opens the store selection dialog to change or clear the target store.
3. **Database & Sync Persistence:**
   - The selected store is saved to the track's Rekordbox comment field (`djmdContent.Commnt`) as `Buy at <Store>.com` (e.g. `Buy at Beatport.com`, `Buy at Bandcamp.com`, `Buy at qobuz.com`, `Buy at Traxsource.com`).
   - Clearing the store clears the buy-at comment (or restores empty comment).
   - This ensures persistence across Rekordbox sessions, database backups, and Turso cloud sync without modifying the database schema.

---

## Tech Stack
- **Core / GUI:** Python >= 3.11, PySide6 (Qt6), SQLAlchemy, SQLCipher (via `pyrekordbox`), `uv`
- **Testing:** `pytest`, `pytest-qt` (Headless Qt configured via `QT_QPA_PLATFORM=offscreen` in `tests/conftest.py`)

---

## Commands
- Run focused tests: `uv run pytest tests/test_gui_models.py -k "TestBuyAtStore"`
- Run track details tests: `uv run pytest tests/test_track_details.py`
- Run full test suite: `uv run pytest`
- Launch GUI: `uv run rekordbox-manager`

---

## Project Structure
- `src/rekordbox_collection_manager/utils.py`
  - Define `BUY_STORE_COMMENT_TEMPLATES`.
  - Implement `parse_buy_store_from_comment(comment: Optional[str]) -> Optional[str]`.
  - Implement `get_buy_store_comment(store_name: str) -> str`.
  - Update `build_track_record` to include `"buy_at_store": parse_buy_store_from_comment(...)`.
- `src/rekordbox_collection_manager/gui/track_table.py`
  - Add column constant: `TrackColumn.BUY_AT = "Buy At"`.
  - Add to default collection columns.
  - Implement data rendering in `TracksTableModel.data()` for DisplayRole (`🛒 <Store>` / `—`), ToolTipRole, ForegroundRole (omits redundant "Buy at:" prefix since column header already states "Buy At").
  - Handle cell click / interaction on `TrackColumn.BUY_AT` to launch store picker.
  - Add right-click context menu action: `"🛒 Set Buy At Store..."` (for unpurchased tracks).
- `src/rekordbox_collection_manager/gui/tracks_view.py`
  - Toggle `TrackColumn.BUY_AT` visibility: visible when `purchase_combo` is `"purchasable"` (or `"buyable"`), hidden otherwise.
- `src/rekordbox_collection_manager/gui/track_detail_view.py`
  - In `_refresh_store_badge_ui()`, if track is unpurchased (`is_purchased == False`):
    - If `buy_at_store` is set: show `[Buy at: <Store>]`.
    - If `buy_at_store` is unset: show `[+ Buy at]`.
  - Clicking opens `SelectStoreDialog(title="Select Store to Buy")`.
  - Selecting updates staged comment and tracks changes; saving writes via `update_track_comment` and emits `db_saved`.
- `tests/test_utils.py`
  - Unit tests for comment parsing, generation, and record building.
- `tests/test_gui_models.py` & `tests/test_track_details.py`
  - GUI unit tests for table column visibility, cell clicking, and badge interactions.

---

## Code Style

### Example Implementation Snippet (`utils.py`)
```python
BUY_STORE_COMMENT_TEMPLATES: Dict[str, str] = {
    STORE_BEATPORT: "Buy at Beatport.com",
    STORE_BANDCAMP: "Buy at Bandcamp.com",
    STORE_QOBUZ: "Buy at qobuz.com",
    STORE_TRAXSOURCE: "Buy at Traxsource.com",
}

def parse_buy_store_from_comment(comment: Optional[str]) -> Optional[str]:
    """Parse planned purchase store from track comment metadata.

    Supported formats:
    - Beatport: 'Buy at Beatport.com'
    - Bandcamp: 'Buy at Bandcamp.com'
    - Qobuz: 'Buy at qobuz.com'
    - Traxsource: 'Buy at Traxsource.com'
    """
    if not comment:
        return None
    c = str(comment).strip()
    if not c:
        return None

    if re.search(r"buy\s+at\s+beatport\.com", c, re.IGNORECASE):
        return STORE_BEATPORT
    if re.search(r"buy\s+at\s+traxsource\.com", c, re.IGNORECASE):
        return STORE_TRAXSOURCE
    if re.search(r"buy\s+at\s+qobuz\.com", c, re.IGNORECASE):
        return STORE_QOBUZ
    if re.search(r"buy\s+at\s+bandcamp\.com", c, re.IGNORECASE):
        return STORE_BANDCAMP

    return None

def get_buy_store_comment(store_name: str) -> str:
    """Return standard planned purchase comment string for a store."""
    return BUY_STORE_COMMENT_TEMPLATES.get(store_name, "")
```

---

## Testing Strategy
1. **Unit Tests (Small / Fast):**
   - Verify `parse_buy_store_from_comment` with each store, mixed casing, extra spaces.
   - Verify that comments like `Purchased at Beatport.com` are NOT parsed as buy-at stores.
   - Verify `build_track_record` populates `buy_at_store`.
2. **GUI Tests (Medium / Headless Qt):**
   - Verify `TracksView` displays `TrackColumn.BUY_AT` when `"purchasable"` filter is selected, and hides it for `"all"` and `"purchased"`.
   - Verify `TracksTableWidget` displays `🛒 Beatport` or `—`.
   - Verify `TrackDetailView` displays `+ Buy at` for unpurchased tracks without store, and `Buy at: Beatport` when set.
   - Verify clicking the badge or table cell launches the store selector and records staged edits.

---

## Boundaries
- **Always:** Use `KNOWN_STORES` (`Beatport`, `Bandcamp`, `Qobuz`, `Traxsource`).
- **Always:** Maintain process mutual exclusion checks (`ensure_rekordbox_not_running()`).
- **Always:** Keep `NullPool` connections and standard transaction safety.
- **Ask first:** Changing the standard comment template format.
- **Never:** Add custom tables or alter Rekordbox SQLite schema.
- **Never:** Interfere with or overwrite purchased track detection (`Purchased/` path check).

---

## Success Criteria
1. `parse_buy_store_from_comment` and `get_buy_store_comment` work deterministically for all 4 stores.
2. In `TracksView`, selecting `"💳 Purchasable (≥3 plays)"` reveals the `Buy At` column; selecting `"All Tracks"` or `"🛒 Purchased Only"` hides it.
3. In `TracksTableWidget`, clicking a cell in the `Buy At` column prompts the store picker to assign or clear the store.
4. In `TrackDetailView`, unpurchased tracks show `[+ Buy at]` or `[Buy at: <Store>]` in the Hero badges row next to `[Downloaded]`.
5. Saving changes in `TrackDetailView` or the table updates `djmdContent.Commnt` and emits `db_saved`.
6. Full test suite passes without regressions.

---

## Out of Scope (Deferred)
- Numeric price tracking / currency symbols (Deferred: 4-store enum is sufficient and cleaner).
- Automated web price scraping (Deferred: brittle, requires auth / IP rotation).
- Cart integration or direct store checkout (Deferred: out of scope for collection manager).
- Modifying behavior for tracks already in `Purchased/` (Out of scope: they are already purchased).

---

## Open Questions
- None. All functional and UI requirements were resolved during intent confirmation.
