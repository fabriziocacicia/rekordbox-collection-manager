# Spec: Stores Summary View & Store-Based Purchasable Filtering — V1

## Objective
Provide DJs and music collection curators with a high-level "Stores" overview page in the desktop application that summarizes track counts across the 4 supported music stores (`Beatport`, `Bandcamp`, `Qobuz`, `Traxsource`) for both purchased tracks and planned/purchasable tracks ("To Buy"), with 1-click deep-linking into pre-filtered collection lists in Tracks View.

### User Stories & Acceptance Criteria

1. **High-Level Stores Summary View (`StoresView`):**
   - As a user opening the application, I can navigate to a dedicated **Stores** tab (`🏪 Stores`) in the navigation sidebar.
   - I see 4 distinct store cards representing the 4 supported stores: `Beatport`, `Bandcamp`, `Qobuz`, and `Traxsource`.
   - Each store card displays:
     - Store name and brand-aligned styling / icon.
     - **Purchased** count: Total number of tracks in my collection purchased from this store.
     - **To Buy** count: Total number of downloaded, purchasable tracks (DJPlayCount ≥ 3) planned for purchase at this store (marked `Buy at <Store>` in comments).
   - An asynchronous loading state / skeleton loader is shown while computing counts so the UI remains fluid on large databases (e.g. >20k tracks).

2. **One-Click Deep-Linking into Tracks View:**
   - Clicking the **Purchased** counter on any store card automatically:
     - Switches navigation to the **Tracks** tab.
     - Sets the Purchase Filter dropdown to `"🛒 Purchased Only"`.
     - Sets the Store filter to the chosen store.
     - Expands the filter toolbar so the active filter is immediately visible.
     - Refreshes the track table to show those purchased tracks with the `"Purchased At"` column visible.
   - Clicking the **To Buy** counter on any store card automatically:
     - Switches navigation to the **Tracks** tab.
     - Sets the Purchase Filter dropdown to `"💳 Purchasable (≥3 plays)"`.
     - Sets the Store filter to the chosen store.
     - Expands the filter toolbar so the active filter is immediately visible.
     - Refreshes the track table to show those purchasable tracks with the `"Buy At"` column visible.

3. **Store Filtering for Purchasable Tracks in Tracks View:**
   - When the user manually selects `"💳 Purchasable (≥3 plays)"` in `TracksView`, the `Store` filter widget remains visible and active (matching its behavior under `"🛒 Purchased Only"`).
   - Selecting one or more stores in the Store filter widget filters purchasable tracks by their planned store (`Buy at <Store>`).
   - The status label in `TracksView` clearly indicates active purchasable store filtering (e.g., `purchasable: Beatport`).

4. **Zero Database Schema Modifications:**
   - All counts and queries rely strictly on existing Rekordbox database fields:
     - `FolderPath` / directory hierarchy containing `Purchased` for purchased tracks.
     - `Commnt` containing `Purchased at ...` or `Visit https://...bandcamp.com` for purchased store attribution.
     - `Commnt` containing `Buy at <Store>.com` for planned store attribution.
     - `DJPlayCount >= 3` and non-purchased status for purchasable qualification.

---

## Tech Stack
- **Core / GUI:** Python >= 3.11, PySide6 (Qt6), SQLAlchemy, SQLCipher (via `pyrekordbox`), `uv`
- **Testing:** `pytest`, `pytest-qt` (Headless Qt configured via `QT_QPA_PLATFORM=offscreen` in `tests/conftest.py`)

---

## Commands
- Run focused query tests: `uv run pytest tests/test_edit_play_count.py -k "test_store_filter"`
- Run stores view GUI tests: `uv run pytest tests/test_stores_view.py`
- Run full test suite: `uv run pytest`
- Launch GUI: `uv run rekordbox-manager`

---

## Project Structure

```
src/rekordbox_collection_manager/
├── edit_play_count.py                  # Update store_filter SQL generation for purchasable/buy_at tracks
├── utils.py                            # Aggregation query helper get_store_summary_counts(engine)
└── gui/
    ├── main_window.py                  # Add Stores nav item, instantiate StoresView, handle deep-link signal
    ├── stores_view.py                  # New QWidget: 4 store summary cards, counts, deep-link triggers
    ├── tracks_view.py                  # Keep store_filter visible for purchasable, add navigate_to_store_filter()
    └── workers.py                      # StoresSummaryWorker background QThread for async count calculation

tests/
├── test_stores_view.py                 # PySide6 GUI tests for StoresView and deep-linking navigation
├── test_edit_play_count.py             # Query tests for purchasable store filtering
└── test_utils.py                       # Unit tests for get_store_summary_counts helper
```

---

## Data & Query Architecture

### 1. Store Summary Aggregation (`utils.py:get_store_summary_counts`)
Computes counts across the 4 known stores in a single or bounded query:
```python
def get_store_summary_counts(engine: Engine) -> Dict[str, Dict[str, int]]:
    """Return purchased and to-buy track counts for each known store.
    
    Returns:
        Dict mapping store name to {"purchased": int, "to_buy": int}
        e.g.:
        {
            "Beatport": {"purchased": 142, "to_buy": 18},
            "Bandcamp": {"purchased": 87, "to_buy": 24},
            "Qobuz": {"purchased": 15, "to_buy": 3},
            "Traxsource": {"purchased": 6, "to_buy": 5},
        }
    """
```
- **Purchased query:** Non-streaming tracks whose `FolderPath` contains `Purchased` and `Commnt` matches the respective store purchase pattern.
- **To Buy query:** Non-streaming tracks whose `FolderPath` does NOT contain `Purchased`, `DJPlayCount >= 3`, and `Commnt` matches `Buy at <Store>.com`.

### 2. Query Filtering in `search_collection_tracks` (`edit_play_count.py`)
Update `store_clause` generation:
- If `purchased_filter in ("purchasable", "buyable")` and `store_filter` is present:
  - Generate SQL conditions targeting `Buy at <Store>`:
    - Beatport: `lower(c.Commnt) LIKE '%buy at beatport.com%'`
    - Bandcamp: `lower(c.Commnt) LIKE '%buy at bandcamp.com%'`
    - Qobuz: `lower(c.Commnt) LIKE '%buy at qobuz.com%'`
    - Traxsource: `lower(c.Commnt) LIKE '%buy at traxsource.com%'`
- If `purchased_filter == "purchased"`:
  - Retain existing `purchased at ...` condition generation.

---

## GUI Architecture

### 1. `StoresView` (`src/rekordbox_collection_manager/gui/stores_view.py`)
- **Layout:**
  - Header: Section title `"🏪 MUSIC STORES"`, subtitle explaining the counts, and a refresh button (`🔄`).
  - Content: 2x2 grid or horizontal card layout displaying the 4 stores.
- **Store Card Component (`StoreCardWidget` / `QFrame`):**
  - Card background: `#1a1a24` with border `COLOR_BORDER` (`#2d2d3d`), hover border highlight `#38bdf8`.
  - Header: Store icon / badge + store title (`Beatport`, `Bandcamp`, `Qobuz`, `Traxsource`).
  - Row 1: `🛒 Purchased`: count formatted prominently in `COLOR_BLUE` / `#38bdf8`, styled as a clickable button/chip with tooltip `"View <N> purchased tracks in Tracks View"`.
  - Row 2: `💳 To Buy (Purchasable)`: count formatted in `COLOR_AMBER` / `#f59e0b`, styled as a clickable button/chip with tooltip `"View <N> purchasable tracks queued for <Store>"`.
- **Signals:**
  - `open_tracks_by_store = Signal(str, str)`: emits `(purchase_status, store_name)`, e.g. `("purchasable", "Bandcamp")`.

### 2. `MainWindow` Integration (`src/rekordbox_collection_manager/gui/main_window.py`)
- Navigation item inserted at index 7:
  ```python
  {
      "id": 7,
      "icon": "🏪",
      "title": "Stores",
  }
  ```
  (Info view moves to index 8).
- `self.stores_view` added to `self.stacked_widget`.
- Signal wiring:
  ```python
  self.stores_view.open_tracks_by_store.connect(self.open_tracks_filtered_by_store)
  ```
- Method `open_tracks_filtered_by_store(purchase_status: str, store_name: str)`:
  - Switches to index 0 (Tracks).
  - Calls `self.tracks_view.navigate_to_store_filter(purchase_status, store_name)`.

### 3. `TracksView` Integration (`src/rekordbox_collection_manager/gui/tracks_view.py`)
- In `_on_filter_changed`:
  ```python
  is_purchased_only = (p_filter == "purchased")
  is_purchasable = (p_filter in ("purchasable", "buyable"))
  show_store = is_purchased_only or is_purchasable
  
  if hasattr(self, "store_filter_widget"):
      if not show_store and self.store_filter_widget.is_active():
          self.store_filter_widget.clear_selection()
      self.store_filter_widget.setVisible(show_store)
  ```
- In `_execute_search`:
  - Pass `store_filter` to worker when `purchased_filter` is `"purchased"` OR `"purchasable"`.
- Method `navigate_to_store_filter(purchase_status: str, store_name: str)`:
  - Sets `self.purchase_combo` to the target status.
  - Sets `self.store_filter_widget` selected items to `[store_name]`.
  - Ensures `self.filter_panel.setVisible(True)`.
  - Calls `self._execute_search()`.

---

## Code Style

### Example Implementation Snippet (`stores_view.py`)
```python
class StoreCardWidget(QFrame):
    """Interactive card displaying purchased and planned purchase counts for a store."""
    
    navigate_requested = Signal(str, str)  # (status, store_name)

    def __init__(self, store_name: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.store_name = store_name
        self._init_ui()

    def set_counts(self, purchased: int, to_buy: int) -> None:
        self.btn_purchased.setText(f"🛒 Purchased: {purchased}")
        self.btn_to_buy.setText(f"💳 To Buy: {to_buy}")
        self.btn_purchased.setEnabled(purchased > 0)
        self.btn_to_buy.setEnabled(to_buy > 0)
```

---

## Testing Strategy
1. **Database & Query Tests (`tests/test_edit_play_count.py`):**
   - Verify `search_collection_tracks` with `purchased_filter="purchasable"` and `store_filter=["Bandcamp"]` returns only purchasable tracks with `Buy at Bandcamp.com`.
   - Verify `get_store_summary_counts` correctly counts across mock databases for all 4 stores.
2. **GUI Unit Tests (`tests/test_stores_view.py`):**
   - Test `StoresView` rendering with empty and populated store counts.
   - Test clicking the "Purchased" button emits `open_tracks_by_store("purchased", store_name)`.
   - Test clicking the "To Buy" button emits `open_tracks_by_store("purchasable", store_name)`.
   - Test `navigate_to_store_filter` in `TracksView` correctly updates combo box, store filter widget, and triggers table reload.
3. **Regression Tests:**
   - Run existing `test_gui_models.py`, `test_track_details.py`, and `test_analytics.py` to ensure 8-tab / 9-tab indexing and navigation invariants remain fully intact.

---

## Boundaries
- **Always:**
  - Execute background operations via `QThread` workers so the GUI never freezes.
  - Run headless Qt tests with `QT_QPA_PLATFORM=offscreen`.
  - Respect `NullPool` and Rekordbox mutual exclusion process checks.
- **Ask first:**
  - Any changes that would touch external sync or daemon behavior.
  - Any layout changes that alter existing Tracks View default columns or search inputs.
- **Never:**
  - Alter Rekordbox database schema or write new tables.
  - Hardcode store names outside the canonical `KNOWN_STORES` tuple in `utils.py`.

---

## Success Criteria
- [ ] `StoresView` appears in navigation sidebar as `🏪 Stores`.
- [ ] 4 store cards display accurate `Purchased` and `To Buy` numbers.
- [ ] Clicking any non-zero count switches to `TracksView` pre-filtered to that store and status.
- [ ] Selecting `💳 Purchasable (≥3 plays)` in `TracksView` keeps the `Store` filter dropdown visible and functional.
- [ ] Full test suite passes cleanly with 0 failures (`uv run pytest`).

---

## Out of Scope (Deferred)
- Dynamic store additions (user adding custom shops beyond the 4 supported ones) — deferred to V2.
- Direct web link opening / browser cart integration — deferred to V2.
- Price or currency totaling — deferred to V2.

---

## Open Questions
- None. Requirements and interaction boundaries have been validated and confirmed.
