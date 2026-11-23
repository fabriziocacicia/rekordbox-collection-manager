# Intent: Dynamic Playlist for Playlistless Tagged Tracks (πagged-Unbound)

## Status
Confirmed by user on 2026-11-23. Refined: Shared event triggers, decoupled execution.

## Outcome
A single catch-all root-level playlist named `πagged-Unbound` (alphabetically sorting last via the Greek letter `π`) containing all tracks that have at least one MyTag but belong to no regular user-created playlist (`Attribute = 0`).

It shares the exact lifecycle triggers with the synchronization daemon, but remains completely decoupled in execution:
- **Shared Triggers:** Runs whenever the sync daemon activates—when Rekordbox closes, when mobile/remote changelog events arrive and are applied, on daemon startup, and upon manual sync triggers.
- **Decoupled Execution:** Functions as an independent, fault-isolated local SQLite operation. It does not depend on cloud credentials, Turso availability, or network reachability.

## User
DJ preparing USB drives via Rekordbox Sync Manager who wants every tagged track exported to USB drives for MyTag filtering on standalone CDJs/XDJs, even if the track has not yet been curated into a specific gig or vibe playlist.

## Why Now
Rekordbox Sync Manager requires selecting playlists to transfer tracks to a USB drive. Tagged tracks that only reside in the general collection without belonging to any playlist are left behind and cannot be browsed or filtered on CDJs.

## Success Criteria
- **Shared Event Triggers:**
  1. Rekordbox termination (NSWorkspace quit notification or polling detection).
  2. Mobile changelog ingestion (after pending events from the iOS app are written to `master.db`).
  3. Daemon startup (when Rekordbox is closed).
  4. Manual sync commands (`rekordbox-daemon sync` / GUI sync button).
- **Decoupled Fault Isolation:** Local reconciliation succeeds even if Turso is unconfigured, disabled, or fails due to network errors.
- **Accuracy:** Newly tagged tracks lacking a user playlist are automatically added to `πagged-Unbound`; tracks assigned to any regular user playlist (`Attribute = 0`, excluding `πagged-Unbound` itself) or whose tags are cleared are automatically removed.
- **Exclusion:** Streaming tracks are excluded.

## Constraints
- Rekordbox process mutual exclusion: Local database mutations only run when Pioneer Rekordbox is not running.
- Safety: Automatic pre-mutation backup created before modifying `master.db`.

## Out of Scope
- Smart playlists (`Attribute = 2`) and history sessions (`djmdHistory`) do not count as user playlists; membership in them does not remove tracks from `πagged-Unbound`.
- Live in-app Rekordbox UI hooks or runtime monkey-patching while Rekordbox is running.
