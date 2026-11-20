# Statement of Intent: Stores Summary View & Store-Based Purchasable Filtering

## Confirmed Intent Summary

- **Outcome:** A dedicated "Stores" summary view in the desktop app displaying cards for the 4 supported stores with their purchased and to-buy track counts, deep-linking into pre-filtered Tracks View lists when clicked.
- **User:** DJ / Collection curator planning store-specific buying sessions (e.g. Bandcamp Friday, Beatport runs) and reviewing purchasing history by store.
- **Why now:** Tracks are tagged with planned stores (`Buy at <Store>`) and purchased stores, but there is no bird's-eye view showing how many tracks are queued or purchased per store.
- **Success:**
  1. A new top-level sidebar view (`🏪 Stores`) displaying 4 store cards (Beatport, Bandcamp, Qobuz, Traxsource).
  2. Each card displays two counts: **Purchased** count and **To Buy** (purchasable) count.
  3. Clicking either count switches to Tracks View, applies the corresponding status (`Purchased Only` or `Purchasable (≥3 plays)`) and store filter, and updates the table.
  4. In Tracks View, the existing `Store` filter dropdown works for `Purchasable (≥3 plays)` just as it does for `Purchased Only`.
- **Constraint:** Strictly limited to the 4 existing supported stores (`Beatport`, `Bandcamp`, `Qobuz`, `Traxsource`) using existing Rekordbox comments and directory metadata without database schema changes.
- **Out of scope:** In-app checkout / cart synchronization, price/currency fetching or totals, custom user-defined stores, or extra UI elements added to Tracks View.

---

## Technical Context & Integration Touchpoints

1. **Navigation & Main Window (`main_window.py`):**
   - Add a new navigation item for Stores: `{"id": 8, "icon": "🏪", "title": "Stores"}` (or inserted logically adjacent to Tracks/Analytics).
   - Instantiate `StoresView` and add it to `self.stack`.
   - Connect deep-linking signal from `StoresView` to navigate to `TracksView` with filter state.

2. **Stores Summary View (`stores_view.py`):**
   - A dedicated view displaying 4 store cards for `Beatport`, `Bandcamp`, `Qobuz`, and `Traxsource`.
   - Card displays:
     - Store title & icon/badge.
     - Purchased track count (interactive button/pill).
     - To Buy (purchasable ≥3 plays) track count (interactive button/pill).
   - Card click actions emit navigation requests: `(status: "purchased" | "purchasable", store: str)`.
   - Responsive, dark-themed Qt layout adhering to `theme.py` design tokens.

3. **Tracks View (`tracks_view.py`):**
   - Keep the existing `Store` filter widget visible and active when `purchase_combo` is `"purchasable"` (in addition to `"purchased"`).
   - When filtering by `"purchasable"`, query tracks whose planned store matches (`parse_buy_store_from_comment(commnt)`).
   - Expose navigation API `navigate_to_store_filter(purchase_status: str, store_name: str)` to programmatically select the filters, reveal the filter toolbar if hidden, and trigger `_execute_search()`.

4. **Data Aggregation Worker / Query (`workers.py` / `stores_view.py`):**
   - Background worker or cached query computing store totals asynchronously to avoid UI stalls on large databases.
