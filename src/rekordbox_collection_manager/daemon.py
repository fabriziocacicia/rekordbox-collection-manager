"""Rekordbox Collection Sync Daemon.

Monitors Rekordbox process state on macOS. When Rekordbox closes:
1. Automatically creates a local safety backup of master.db.
2. Ingests and applies pending changes made from the iOS app.
3. Exports the fresh collection snapshot to Turso.
"""

import argparse
import logging
import os
import plistlib
import subprocess
import sys
import time
from typing import Optional

from rekordbox_collection_manager.backup_restore import create_database_backup
from rekordbox_collection_manager.turso_sync import (
    apply_changelog_events,
    create_turso_client,
    export_collection_to_turso,
)
from rekordbox_collection_manager.utils import (
    get_default_master_db_path,
    is_rekordbox_running,
    open_database,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("rekordbox-daemon")

LAUNCHD_PLIST_LABEL = "com.fabriziocacicia.rekordbox-sync"
LAUNCHD_PLIST_PATH = os.path.expanduser(f"~/Library/LaunchAgents/{LAUNCHD_PLIST_LABEL}.plist")


def run_sync_cycle(force: bool = False) -> bool:
    """Execute a single sync cycle.

    If Rekordbox is running and force=False, skips to avoid database lock/conflict.
    """
    if is_rekordbox_running() and not force:
        logger.info("Rekordbox is currently running. Sync deferred until Rekordbox closes.")
        return False

    db_path = get_default_master_db_path()
    if not db_path or not os.path.exists(db_path):
        logger.error("Could not locate Rekordbox master.db")
        return False

    logger.info("Starting sync cycle for database: %s", db_path)

    # 1. Connect to Turso
    try:
        client = create_turso_client()
    except Exception as e:
        logger.error("Failed to connect to Turso: %s", e)
        return False

    # 2. Open master.db
    engine, fmt = open_database(db_path)

    # 3. Create safety backup before touching anything
    try:
        backup_res = create_database_backup(db_path, force=True)
        logger.info("Created safety backup at: %s", backup_res.get("backup_path"))
    except Exception as e:
        logger.warning("Backup warning (proceeding with caution): %s", e)

    # 4. Ingest pending changelog events from iOS
    try:
        processed = apply_changelog_events(engine, client)
        if processed > 0:
            logger.info("Successfully applied %d change events to master.db", processed)
        else:
            logger.info("No pending changelog events to apply.")
    except Exception as e:
        logger.error("Error applying changelog events: %s", e)

    # 5. Export fresh snapshot to Turso
    try:
        stats = export_collection_to_turso(engine, client)
        logger.info(
            "Export to Turso complete: %d tracks, %d playlists, %d tags synced.",
            stats["tracks"],
            stats["playlists"],
            stats["tags"],
        )
    except Exception as e:
        logger.error("Error exporting to Turso: %s", e)
        return False

    return True


try:
    from AppKit import (
        NSWorkspace,
        NSWorkspaceDidLaunchApplicationNotification,
        NSWorkspaceDidTerminateApplicationNotification,
    )
    from Foundation import NSDate, NSObject, NSRunLoop
    HAS_PYOBJC = True
except ImportError:
    HAS_PYOBJC = False


if HAS_PYOBJC:
    class RekordboxAppObserver(NSObject):
        """Native macOS Cocoa observer listening for Rekordbox launch and quit events."""

        def appLaunched_(self, notif) -> None:
            app = notif.userInfo().objectForKey_("NSWorkspaceApplicationKey")
            name = (app.localizedName() or "").lower() if app else ""
            if "rekordbox" in name and "rekordbox-collection" not in name:
                logger.info("Rekordbox is now RUNNING 🟢 (received macOS NSWorkspace launch event)")

        def appTerminated_(self, notif) -> None:
            app = notif.userInfo().objectForKey_("NSWorkspaceApplicationKey")
            name = (app.localizedName() or "").lower() if app else ""
            if "rekordbox" in name and "rekordbox-collection" not in name:
                logger.info("Rekordbox was CLOSED ⚪ (received macOS NSWorkspace quit event)! Triggering sync...")
                run_sync_cycle()


def run_daemon_loop(poll_interval: int = 2) -> None:
    """Run daemon listening for Rekordbox events via NSWorkspace or polling fallback."""
    logger.info("Starting Rekordbox Sync Daemon...")
    currently_running = is_rekordbox_running()
    logger.info("Initial Rekordbox state: %s", "RUNNING 🟢" if currently_running else "CLOSED ⚪")

    # On startup: only process pending changelog events if closed, avoiding redundant full export
    if not currently_running:
        try:
            client = create_turso_client()
            pending = client.execute("SELECT COUNT(*) FROM changelog_events WHERE status = 'PENDING';").rows[0][0]
            if pending > 0:
                logger.info("Found %d pending changelog events on startup. Processing...", pending)
                run_sync_cycle()
            else:
                logger.info("Daemon ready and listening for Rekordbox events.")
        except Exception as e:
            logger.debug("Startup check error: %s", e)

    # 1. Native macOS event-driven mode (NSWorkspace notifications)
    if HAS_PYOBJC:
        logger.info("Listening for native macOS NSWorkspace events (zero-polling).")
        obs = RekordboxAppObserver.alloc().init()
        center = NSWorkspace.sharedWorkspace().notificationCenter()
        center.addObserver_selector_name_object_(
            obs, "appLaunched:", NSWorkspaceDidLaunchApplicationNotification, None
        )
        center.addObserver_selector_name_object_(
            obs, "appTerminated:", NSWorkspaceDidTerminateApplicationNotification, None
        )

        run_loop = NSRunLoop.currentRunLoop()
        last_turso_check = time.time()
        turso_check_interval = 30  # Check Turso for mobile events every 30s

        while True:
            try:
                # Sleep in macOS Mach runloop, dispatching Cocoa events instantly
                run_loop.runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(1.0))

                # Periodically check for mobile events when Rekordbox is closed
                if not is_rekordbox_running() and (time.time() - last_turso_check) >= turso_check_interval:
                    last_turso_check = time.time()
                    try:
                        client = create_turso_client()
                        pending = client.execute(
                            "SELECT COUNT(*) FROM changelog_events WHERE status = 'PENDING';"
                        ).rows[0][0]
                        if pending > 0:
                            logger.info("Detected %d pending events from mobile. Processing...", pending)
                            run_sync_cycle()
                    except Exception as e:
                        logger.debug("Check pending events error: %s", e)

            except KeyboardInterrupt:
                logger.info("Daemon stopped by user.")
                break
            except Exception as e:
                logger.error("Unexpected error in event loop: %s", e)
                time.sleep(2)
        return

    # 2. Polling fallback (if PyObjC is not available)
    logger.info("Using polling fallback (poll interval: %ds)...", poll_interval)
    was_running = currently_running
    last_turso_check = time.time()
    turso_check_interval = 30

    while True:
        try:
            time.sleep(poll_interval)
            currently_running = is_rekordbox_running()

            if currently_running and not was_running:
                logger.info("Rekordbox is now RUNNING 🟢. Monitoring session...")

            elif was_running and not currently_running:
                logger.info("Rekordbox was CLOSED ⚪! Triggering post-session sync...")
                run_sync_cycle()

            elif not currently_running and (time.time() - last_turso_check) >= turso_check_interval:
                last_turso_check = time.time()
                try:
                    client = create_turso_client()
                    pending = client.execute(
                        "SELECT COUNT(*) FROM changelog_events WHERE status = 'PENDING';"
                    ).rows[0][0]
                    if pending > 0:
                        logger.info("Detected %d pending events from mobile. Processing...", pending)
                        run_sync_cycle()
                except Exception as e:
                    logger.debug("Check pending events error: %s", e)

            was_running = currently_running

        except KeyboardInterrupt:
            logger.info("Daemon stopped by user.")
            break
        except Exception as e:
            logger.error("Unexpected error in daemon loop: %s", e)
            time.sleep(2)


def install_launchd_service() -> None:
    """Generate and load macOS LaunchAgent plist so daemon starts on user login."""
    python_bin = sys.executable
    project_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    plist_data = {
        "Label": LAUNCHD_PLIST_LABEL,
        "ProgramArguments": [
            python_bin,
            "-m",
            "rekordbox_collection_manager.daemon",
            "run",
        ],
        "WorkingDirectory": project_dir,
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": os.path.expanduser("~/Library/Logs/rekordbox-sync.log"),
        "StandardErrorPath": os.path.expanduser("~/Library/Logs/rekordbox-sync-err.log"),
        "EnvironmentVariables": {
            "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:" + os.environ.get("PATH", "")
        },
    }

    os.makedirs(os.path.dirname(LAUNCHD_PLIST_PATH), exist_ok=True)
    with open(LAUNCHD_PLIST_PATH, "wb") as f:
        plistlib.dump(plist_data, f)

    logger.info("Created LaunchAgent plist at: %s", LAUNCHD_PLIST_PATH)
    subprocess.run(["launchctl", "unload", LAUNCHD_PLIST_PATH], capture_output=True)
    res = subprocess.run(["launchctl", "load", LAUNCHD_PLIST_PATH], capture_output=True, text=True)
    if res.returncode == 0:
        logger.info("Successfully loaded LaunchAgent! Daemon will run in background automatically.")
    else:
        logger.error("Failed to load LaunchAgent: %s", res.stderr)


def uninstall_launchd_service() -> None:
    """Unload and remove the macOS LaunchAgent plist."""
    if os.path.exists(LAUNCHD_PLIST_PATH):
        subprocess.run(["launchctl", "unload", LAUNCHD_PLIST_PATH], capture_output=True)
        os.remove(LAUNCHD_PLIST_PATH)
        logger.info("Removed LaunchAgent from %s", LAUNCHD_PLIST_PATH)
    else:
        logger.info("No LaunchAgent installed at %s", LAUNCHD_PLIST_PATH)


def show_status() -> None:
    """Display current Rekordbox status and Turso sync statistics."""
    rb_running = is_rekordbox_running()
    print("==========================================")
    print("Rekordbox Collection Sync Daemon Status")
    print("==========================================")
    print(f"Rekordbox Process: {'RUNNING 🟢' if rb_running else 'CLOSED ⚪'}")
    print(f"LaunchAgent installed: {'YES' if os.path.exists(LAUNCHD_PLIST_PATH) else 'NO'}")

    try:
        from rekordbox_collection_manager.turso_sync import init_turso_schema
        client = create_turso_client()
        init_turso_schema(client)
        pending = client.execute("SELECT COUNT(*) FROM changelog_events WHERE status = 'PENDING';").rows[0][0]
        processed = client.execute("SELECT COUNT(*) FROM changelog_events WHERE status = 'PROCESSED';").rows[0][0]
        failed = client.execute("SELECT COUNT(*) FROM changelog_events WHERE status = 'FAILED';").rows[0][0]
        tracks = client.execute("SELECT COUNT(*) FROM tracks;").rows[0][0]
        playlists = client.execute("SELECT COUNT(*) FROM playlists;").rows[0][0]
        tags = client.execute("SELECT COUNT(*) FROM tags;").rows[0][0]

        print(f"Turso Synced Tracks:    {tracks}")
        print(f"Turso Synced Playlists: {playlists}")
        print(f"Turso Synced Tags:      {tags}")
        print(f"Pending Events:         {pending}")
        print(f"Processed Events:       {processed}")
        print(f"Failed Events:          {failed}")
    except Exception as e:
        print(f"Turso connection error: {e}")
    print("==========================================")


def main():
    parser = argparse.ArgumentParser(description="Rekordbox Collection Sync Daemon")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("sync", help="Run a single sync right now")
    run_parser = subparsers.add_parser("run", help="Run the daemon loop in foreground")
    run_parser.add_argument("--interval", type=int, default=15, help="Poll interval in seconds (default: 15)")
    subparsers.add_parser("status", help="Show current daemon & sync status")
    subparsers.add_parser("install", help="Install macOS launchd agent to run automatically on login")
    subparsers.add_parser("uninstall", help="Remove macOS launchd agent")

    args = parser.parse_args()

    if args.command == "sync":
        success = run_sync_cycle(force=False)
        sys.exit(0 if success else 1)
    elif args.command == "run":
        run_daemon_loop(poll_interval=args.interval)
    elif args.command == "status":
        show_status()
    elif args.command == "install":
        install_launchd_service()
    elif args.command == "uninstall":
        uninstall_launchd_service()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
