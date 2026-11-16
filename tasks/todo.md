# Task Checklist: Purchasable Track "Buy At" Store Tracking

## Task 1: Store comment templates, parsing, and record building in `utils.py`
- **Description:** Implement templates and parsing utilities for recording intended purchase stores in Rekordbox `djmdContent.Commnt` metadata.
- **Acceptance criteria:**
  - [ ] `BUY_STORE_COMMENT_TEMPLATES` maps Beatport, Bandcamp, Qobuz, and Traxsource to standard strings (e.g. `Buy at Beatport.com`).
  - [ ] `parse_buy_store_from_comment(comment)` extracts the store name from comment strings, case-insensitively.
  - [ ] `get_buy_store_comment(store_name)` returns the template string.
  - [ ] `build_track_record(row)` extracts and populates `"buy_at_store"`.
  - [ ] Existing `parse_store_from_comment()` does not falsely trigger on `Buy at ...` comments.
- **Verification:**
  - [ ] Unit tests pass: `uv run pytest tests/test_utils.py -k "test_buy_store"`
- **Dependencies:** None
- **Files likely touched:**
  - `src/rekordbox_collection_manager/utils.py`
  - `tests/test_utils.py`
- **Estimated scope:** S (2 files)

---

## Checkpoint 1: Foundation
- [ ] `uv run pytest tests/test_utils.py -k "test_buy_store"` passes cleanly.

---

## Task 2: "Buy at" badge chip and store selector in `TrackDetailView`
- **Description:** Enable unpurchased tracks in `TrackDetailView` to show a clickable `[+ Buy at]` or `[Buy at: <Store>]` chip in the Hero row next to `[Downloaded]`, connecting to the store selection dialog and staged comment persistence.
- **Acceptance criteria:**
  - [ ] For unpurchased tracks (`is_purchased == False`), `self.store_badge` is visible.
  - [ ] If `buy_at_store` is empty/None, badge text is `+ Buy at` with clickable pointer cursor.
  - [ ] If `buy_at_store` is set, badge text is `Buy at: <Store>`.
  - [ ] Clicking the badge opens `SelectStoreDialog` titled "Select Store to Buy" with the 4 stores.
  - [ ] Selecting or clearing a store stages changes, updates the bottom bar, and saves via `update_track_comment()`.
- **Verification:**
  - [ ] Tests pass: `uv run pytest tests/test_track_details.py -k "test_buy_at"`
- **Dependencies:** Task 1
- **Files likely touched:**
  - `src/rekordbox_collection_manager/gui/track_detail_view.py`
  - `tests/test_track_details.py`
- **Estimated scope:** S (2 files)

---

## Checkpoint 2: Track Details
- [ ] `uv run pytest tests/test_track_details.py -k "test_buy_at"` passes cleanly.

---

## Task 3: "Buy At" column in `TracksTableWidget` and dynamic visibility in `TracksView`
- **Description:** Add `TrackColumn.BUY_AT` to the collection tracks table, showing `Buy at: <Store>` or `—`, allow cell-clicking to pick or clear the store, and dynamically toggle column visibility when filtering by Purchasable tracks.
- **Acceptance criteria:**
  - [ ] `TrackColumn.BUY_AT = "Buy At"` added to `TrackColumn` and `DEFAULT_COLLECTION_COLUMNS`.
  - [ ] Table renders `Buy at: <Store>` (accent color) or `—` (muted) in the `BUY_AT` column.
  - [ ] In `TracksView`, when `purchase_combo` is `"purchasable"` (or `"buyable"`), the `BUY_AT` column is visible; when `"all"` or `"purchased"`, it is hidden.
  - [ ] Clicking a `BUY_AT` cell in `TracksTableWidget` (or right-clicking `🛒 Set Buy At Store...`) opens `SelectStoreDialog` and stages/applies the comment update.
- **Verification:**
  - [ ] Tests pass: `uv run pytest tests/test_gui_models.py -k "TestBuyAtStore"`
- **Dependencies:** Task 1, Task 2
- **Files likely touched:**
  - `src/rekordbox_collection_manager/gui/track_table.py`
  - `src/rekordbox_collection_manager/gui/tracks_view.py`
  - `tests/test_gui_models.py`
- **Estimated scope:** M (3 files)

---

## Checkpoint 3: Collection View & Table
- [ ] `uv run pytest tests/test_gui_models.py -k "TestBuyAtStore"` passes cleanly.

---

## Task 4: Full Suite Verification & Documentation
- **Description:** Run the full test suite to guarantee zero regressions across the codebase and update desktop GUI documentation.
- **Acceptance criteria:**
  - [ ] All unit and GUI tests pass.
  - [ ] `src/rekordbox_collection_manager/gui/README.md` documents `TrackColumn.BUY_AT` and purchasable filter column behavior.
- **Verification:**
  - [ ] `uv run pytest` passes cleanly.
- **Dependencies:** Tasks 1-3
- **Files likely touched:**
  - `src/rekordbox_collection_manager/gui/README.md`
- **Estimated scope:** S (1 file)

---

## Final Checkpoint
- [ ] All acceptance criteria in `SPEC-purchasable-buy-at-store.md` met.
- [ ] Full test suite green.
