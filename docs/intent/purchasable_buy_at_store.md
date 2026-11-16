# Statement of Intent: Purchasable Track "Buy At" Store Tracking

## Confirmed Intent Summary

- **Outcome:** Track which of the 4 supported stores (Beatport, Bandcamp, Qobuz, Traxsource) you plan to buy each purchasable track from, via an interactive "Buy At" column in the collection table and a matching hero badge in Track Details.
- **User:** DJ / Collection Curator managing purchasable tracks sorted by play count to prepare an actionable purchase plan.
- **Why now:** Compare prices across the 4 stores once per track during library curation, record the winning store, and know exactly where to purchase each track when executing purchases without needing to re-check prices.
- **Success:**
  1. **Tracks Table View ("Purchasable" Filter):** When filtering by `"💳 Purchasable (≥3 plays)"` in Tracks View, a **"Buy At"** column appears showing the store (or `—` when unresearched). Clicking any cell in this column opens the 4-store picker dialog to set or clear the store.
  2. **Track Detail View:** An unpurchased track displays a clickable badge in the hero row right next to `[Downloaded]`: showing `[+ Buy at]` when unassigned, and `[Buy at: <Store>]` when set. Clicking it opens the store selector dialog.
  3. **Database & Cloud Persistence:** The chosen store is saved to Rekordbox's `Commnt` field (e.g., `Buy at Beatport.com`), ensuring seamless persistence across local sessions, database backups, and Turso cloud synchronization without modifying Rekordbox's proprietary database schema.
- **Constraint:** Restricted to the 4 known stores (`Beatport`, `Bandcamp`, `Qobuz`, `Traxsource`) and saved directly into the existing Rekordbox `djmdContent.Commnt` column.
- **Out of scope:** Numeric price tracking / currencies, automated web scrapers or price lookup APIs, store cart integrations, or altering existing behavior for tracks already located in `Purchased/`.

---

## Technical Context & Integration Touchpoints

1. **Store Metadata Helpers (`utils.py`):**
   - Stores: `Beatport`, `Bandcamp`, `Qobuz`, `Traxsource` (`KNOWN_STORES`).
   - Planned purchase comment format: e.g. `Buy at Beatport.com`, `Buy at Bandcamp.com`, `Buy at qobuz.com`, `Buy at Traxsource.com`.
   - Helper functions to parse both purchased stores (`Purchased at ...`) and planned stores (`Buy at ...`).
   - `update_track_comment(engine, content_id, comment)` for persisting changes to `djmdContent.Commnt`.

2. **Collection Tracks View (`tracks_view.py`):**
   - When `purchase_combo` selection is `"purchasable"` (or `"buyable"`), toggle visibility of the `TrackColumn.BUY_AT` column (or dynamic store column).
   - Support clicking table cells to trigger the store selector dialog.

3. **Track Detail View (`track_detail_view.py`):**
   - For unpurchased tracks (`is_purchased_track(self.track)` is `False`):
     - If planned store is set: Display `[Buy at: <Store>]` chip in the hero badges layout.
     - If planned store is unset: Display `[+ Buy at]` chip in the hero badges layout.
   - Clicking the chip opens `SelectStoreDialog` with options: `Beatport`, `Bandcamp`, `Qobuz`, `Traxsource`, and `Clear`.
   - Staging & saving triggers `update_track_comment` and emits `db_saved`.

4. **Table View (`track_table.py`):**
   - Add column identifier (e.g. `TrackColumn.BUY_AT = "Buy At"`).
   - Render `🛒 <Store>` or `—` (store name with cart icon; omits "Buy at:" as column header already says "Buy At").
   - Connect cell click / interaction to launch store selection.
