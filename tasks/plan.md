# Implementation Plan: Purchasable Track "Buy At" Store Tracking

Source spec: [SPEC-purchasable-buy-at-store.md](file:///Users/fabriziocacicia/Projects/Personal/Music/rekordbox-collection-manager/docs/specs/SPEC-purchasable-buy-at-store.md)

## Overview
Enable tracking of the target digital download store (Beatport, Bandcamp, Qobuz, Traxsource) for purchasable / unpurchased tracks. The target store is recorded in Rekordbox's `djmdContent.Commnt` column (e.g. `Buy at Beatport.com`), displayed as a dedicated "Buy At" column in the collection table when filtering by `"💳 Purchasable (≥3 plays)"`, and accessible via a clickable `[+ Buy at]` / `[Buy at: <Store>]` badge in `TrackDetailView`.

---

## Architecture Decisions
1. **Comment Template Distinction:**
   - Purchased tracks continue using `STORE_COMMENT_TEMPLATES` (e.g. `Purchased at Beatport.com`).
   - Purchasable / buy-later tracks use `BUY_STORE_COMMENT_TEMPLATES` (e.g. `Buy at Beatport.com`).
   - Distinct regexes prevent any collision or misidentification between already-purchased tracks and planned purchases.
2. **Rekordbox Database Safety:**
   - Uses the standard `djmdContent.Commnt` column via `update_track_comment()`.
   - Zero modifications to Rekordbox SQLite database schemas. Fully compatible with Pioneer Rekordbox, SQLCipher, and Turso cloud sync.
3. **Table Column Visibility:**
   - Follows the exact pattern established for `TrackColumn.PURCHASED_AT`: hidden by default, dynamically unhidden when `purchase_combo` is `"purchasable"`, and re-hidden when switching to `"all"` or `"purchased"`.
4. **Interactive Store Selection:**
   - Both the table cell and the detail view chip open the existing `SelectStoreDialog` (customized with appropriate title/prompts), allowing direct selection of Beatport, Bandcamp, Qobuz, Traxsource, or clearing.

---

## Task List

### Phase 1: Foundation (Data & Comment Helpers)
- [ ] Task 1: Store comment templates, parsing, and record building in `utils.py`
  - Define `BUY_STORE_COMMENT_TEMPLATES`.
  - Implement `parse_buy_store_from_comment(comment)` and `get_buy_store_comment(store_name)`.
  - Update `build_track_record` to include `"buy_at_store"`.
  - Unit tests in `tests/test_utils.py`.

### Checkpoint: Foundation
- [ ] `uv run pytest tests/test_utils.py -k "buy_store"` passes.

### Phase 2: Track Details View
- [ ] Task 2: "Buy at" badge chip and store selector in `TrackDetailView`
  - In `_refresh_store_badge_ui()`, display `[+ Buy at]` (empty) or `[Buy at: <Store>]` (set) for unpurchased tracks.
  - Connect badge click to `SelectStoreDialog(title="Select Store to Buy")`.
  - Handle staging, bottom bar alert, comment updating on save, and `db_saved` signal emission.
  - Tests in `tests/test_track_details.py`.

### Checkpoint: Track Details
- [ ] `uv run pytest tests/test_track_details.py -k "test_buy_at"` passes.

### Phase 3: Collection Table & Tracks View
- [ ] Task 3: "Buy At" column in `TracksTableWidget` and dynamic visibility in `TracksView`
  - Add `TrackColumn.BUY_AT = "Buy At"` to `TrackColumn` and `DEFAULT_COLLECTION_COLUMNS`.
  - Implement `TracksTableModel.data()` for DisplayRole (`Buy at: <Store>` / `—`), ToolTipRole, ForegroundRole.
  - Add cell click and context menu action (`🛒 Set Buy At Store...`) in `TracksTableWidget`.
  - Toggle `TrackColumn.BUY_AT` visibility in `TracksView` when `purchase_combo` selection changes.
  - Tests in `tests/test_gui_models.py`.

### Checkpoint: Collection View & Table
- [ ] `uv run pytest tests/test_gui_models.py -k "TestBuyAtStore"` passes.

### Phase 4: Full Suite Verification & Documentation
- [ ] Task 4: Run full test suite and update documentation
  - Verify full test suite passes with `uv run pytest`.
  - Update `src/rekordbox_collection_manager/gui/README.md`.

---

## Risks and Mitigations
| Risk | Impact | Mitigation |
|------|--------|------------|
| Comment collision with custom user comments | Low | Specific regex `r"buy\s+at\s+<store>\.com"` prevents false matches on arbitrary comments. |
| Table column order or width issues | Low | Placed right next to `PURCHASED_AT` with default auto-resize and hidden state when not in purchasable view. |
| In-memory unit test worker threading | Low | Follow established project convention of executing filter/track queries synchronously for `:memory:` SQLite test databases. |

---

## Open Questions
- None.
