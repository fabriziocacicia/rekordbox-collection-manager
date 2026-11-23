"""Unit and integration tests for the πagged-Unbound dynamic playlist reconciliation engine."""

import unittest
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine, text

from rekordbox_collection_manager.unbound_playlist import (
    UNBOUND_PLAYLIST_NAME,
    ensure_unbound_playlist_exists,
    get_unbound_target_track_ids,
    reconcile_unbound_playlist,
)


class TestUnboundPlaylistBackend(unittest.TestCase):
    """Test suite for πagged-Unbound playlist creation, query, and reconciliation."""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        with self.engine.begin() as conn:
            conn.exec_driver_sql("""
                CREATE TABLE djmdPlaylist (
                    ID VARCHAR(255) PRIMARY KEY,
                    Seq INTEGER,
                    Name VARCHAR(255),
                    Attribute INTEGER DEFAULT 0,
                    ParentID VARCHAR(255),
                    SmartList TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    rb_local_deleted INTEGER DEFAULT 0,
                    rb_local_synced INTEGER DEFAULT 0,
                    rb_local_data_status INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdSongPlaylist (
                    ID VARCHAR(255) PRIMARY KEY,
                    PlaylistID VARCHAR(255),
                    ContentID VARCHAR(255),
                    TrackNo INTEGER,
                    created_at TEXT,
                    updated_at TEXT,
                    rb_local_deleted INTEGER DEFAULT 0,
                    rb_local_synced INTEGER DEFAULT 0,
                    rb_local_data_status INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdContent (
                    ID VARCHAR(255) PRIMARY KEY,
                    Title VARCHAR(255),
                    SrcArtistName VARCHAR(255),
                    FileType INTEGER DEFAULT 1,
                    FileNameL VARCHAR(255),
                    FolderPath VARCHAR(255),
                    StockType INTEGER DEFAULT 0,
                    created_at TEXT,
                    updated_at TEXT,
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdMyTag (
                    ID VARCHAR(255) PRIMARY KEY,
                    Name VARCHAR(255),
                    ParentID VARCHAR(255),
                    Seq INTEGER,
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdSongMyTag (
                    ID VARCHAR(255) PRIMARY KEY,
                    ContentID VARCHAR(255),
                    MyTagID VARCHAR(255),
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdHistory (
                    ID VARCHAR(255) PRIMARY KEY,
                    Name VARCHAR(255),
                    ParentID VARCHAR(255),
                    Attribute INTEGER DEFAULT 0,
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdSongHistory (
                    ID VARCHAR(255) PRIMARY KEY,
                    HistoryID VARCHAR(255),
                    ContentID VARCHAR(255),
                    TrackNo INTEGER,
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)

            # Pre-populate sample library:
            # Playlists: 1 regular user playlist ('pl_curated'), 1 smart playlist ('pl_smart')
            conn.exec_driver_sql("""
                INSERT INTO djmdPlaylist (ID, Seq, Name, Attribute, ParentID, rb_local_deleted)
                VALUES 
                    ('pl_curated', 1, 'Deep Vibes', 0, 'root', 0),
                    ('pl_smart', 2, 'High Energy', 4, 'root', 0);
            """)

            # Tags: 'tag_afro', 'tag_deep'
            conn.exec_driver_sql("""
                INSERT INTO djmdMyTag (ID, Name, ParentID, Seq, rb_local_deleted)
                VALUES 
                    ('tag_afro', 'Afro', 'root', 1, 0),
                    ('tag_deep', 'Deep', 'root', 2, 0);
            """)

            # Tracks:
            # t1: Tagged with 'tag_afro', already in 'pl_curated' -> SHOULD NOT be unbound
            # t2: Tagged with 'tag_deep', NOT in any user playlist -> SHOULD BE unbound
            # t3: No tags, NOT in any user playlist -> SHOULD NOT be unbound
            # t4: Tagged with 'tag_afro', in smart playlist and history -> SHOULD BE unbound (smart/history don't count)
            # t5: Tagged with 'tag_deep', streaming track (StockType = 1) -> SHOULD NOT be unbound
            conn.exec_driver_sql("""
                INSERT INTO djmdContent (ID, Title, SrcArtistName, StockType, rb_local_deleted)
                VALUES 
                    ('t1', 'Curated Track', 'Artist 1', 0, 0),
                    ('t2', 'Unassigned Tagged Track', 'Artist 2', 0, 0),
                    ('t3', 'Untagged Track', 'Artist 3', 0, 0),
                    ('t4', 'Smart & History Track', 'Artist 4', 0, 0),
                    ('t5', 'Streaming Track', 'Artist 5', 1, 0);
            """)

            # Tag associations:
            conn.exec_driver_sql("""
                INSERT INTO djmdSongMyTag (ID, ContentID, MyTagID, rb_local_deleted)
                VALUES 
                    ('smt1', 't1', 'tag_afro', 0),
                    ('smt2', 't2', 'tag_deep', 0),
                    ('smt4', 't4', 'tag_afro', 0),
                    ('smt5', 't5', 'tag_deep', 0);
            """)

            # Playlist associations:
            conn.exec_driver_sql("""
                INSERT INTO djmdSongPlaylist (ID, PlaylistID, ContentID, TrackNo, rb_local_deleted)
                VALUES 
                    ('sp1', 'pl_curated', 't1', 1, 0);
            """)

            # History association for t4:
            conn.exec_driver_sql("""
                INSERT INTO djmdHistory (ID, Name, ParentID, Attribute, rb_local_deleted)
                VALUES ('hist1', '2026-11 Club Night', 'root', 0, 0);
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdSongHistory (ID, HistoryID, ContentID, TrackNo, rb_local_deleted)
                VALUES ('sh1', 'hist1', 't4', 1, 0);
            """)

    def test_ensure_unbound_playlist_created_at_root(self):
        """Verify that πagged-Unbound is created under root with Attribute = 0."""
        with self.engine.begin() as conn:
            pl_id = ensure_unbound_playlist_exists(conn)
            self.assertTrue(pl_id)

            row = conn.execute(
                text("SELECT ID, Name, Attribute, ParentID, Seq, rb_local_deleted FROM djmdPlaylist WHERE ID = :pid;"),
                {"pid": pl_id},
            ).fetchone()

            self.assertIsNotNone(row)
            self.assertEqual(row[1], UNBOUND_PLAYLIST_NAME)
            self.assertEqual(row[1], "πagged-Unbound")
            self.assertEqual(row[2], 0)  # Standard playlist
            self.assertEqual(row[3], "root")  # At root level
            self.assertEqual(row[5], 0)  # Active, not deleted

    def test_ensure_unbound_playlist_reactivated_if_soft_deleted(self):
        """Verify that if πagged-Unbound was soft deleted, it is reactivated."""
        with self.engine.begin() as conn:
            # Create a soft-deleted entry first
            conn.execute(
                text("""
                    INSERT INTO djmdPlaylist (ID, Seq, Name, Attribute, ParentID, rb_local_deleted)
                    VALUES ('old_unbound', 99, :name, 0, 'root', 1);
                """),
                {"name": UNBOUND_PLAYLIST_NAME},
            )
            pl_id = ensure_unbound_playlist_exists(conn)
            self.assertEqual(pl_id, "old_unbound")

            row = conn.execute(
                text("SELECT rb_local_deleted FROM djmdPlaylist WHERE ID = :pid;"),
                {"pid": pl_id},
            ).fetchone()
            self.assertEqual(row[0], 0)

    def test_get_unbound_target_track_ids(self):
        """Verify target tracks: only t2 and t4 should qualify."""
        with self.engine.connect() as conn:
            # Pass a dummy unbound playlist ID
            targets = get_unbound_target_track_ids(conn, unbound_playlist_id="dummy_unbound")
            self.assertEqual(targets, {"t2", "t4"})

    @patch("rekordbox_collection_manager.unbound_playlist.create_database_backup")
    @patch("rekordbox_collection_manager.unbound_playlist.is_rekordbox_running", return_value=False)
    def test_reconcile_unbound_playlist_full_flow(self, mock_rb_running, mock_backup):
        """Verify reconciliation adds t2 and t4, removes none on first run."""
        stats = reconcile_unbound_playlist(engine=self.engine, force=True, create_backup=False)
        self.assertEqual(stats["added"], 2)
        self.assertEqual(stats["removed"], 0)
        self.assertEqual(stats["total"], 2)

        # Verify tracks in djmdSongPlaylist
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT sp.ContentID, sp.TrackNo, sp.rb_local_deleted
                    FROM djmdSongPlaylist sp
                    JOIN djmdPlaylist p ON sp.PlaylistID = p.ID
                    WHERE p.Name = :name
                    ORDER BY sp.TrackNo;
                """),
                {"name": UNBOUND_PLAYLIST_NAME},
            ).fetchall()
            active_ids = {r[0] for r in rows if r[2] == 0}
            self.assertEqual(active_ids, {"t2", "t4"})

    @patch("rekordbox_collection_manager.unbound_playlist.create_database_backup")
    @patch("rekordbox_collection_manager.unbound_playlist.is_rekordbox_running", return_value=False)
    def test_reconcile_idempotency(self, mock_rb_running, mock_backup):
        """Running reconcile twice with no data change yields 0 added and 0 removed."""
        reconcile_unbound_playlist(engine=self.engine, force=True, create_backup=False)
        stats2 = reconcile_unbound_playlist(engine=self.engine, force=True, create_backup=False)
        self.assertEqual(stats2["added"], 0)
        self.assertEqual(stats2["removed"], 0)
        self.assertEqual(stats2["total"], 2)

    @patch("rekordbox_collection_manager.unbound_playlist.create_database_backup")
    @patch("rekordbox_collection_manager.unbound_playlist.is_rekordbox_running", return_value=False)
    def test_reconcile_removes_track_when_added_to_user_playlist(self, mock_rb_running, mock_backup):
        """When t2 is assigned to 'pl_curated', subsequent reconcile removes it from πagged-Unbound."""
        reconcile_unbound_playlist(engine=self.engine, force=True, create_backup=False)

        # Assign t2 to user playlist 'pl_curated'
        with self.engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO djmdSongPlaylist (ID, PlaylistID, ContentID, TrackNo, rb_local_deleted)
                    VALUES ('sp_new_t2', 'pl_curated', 't2', 2, 0);
                """)
            )

        # Run reconcile again
        stats = reconcile_unbound_playlist(engine=self.engine, force=True, create_backup=False)
        self.assertEqual(stats["added"], 0)
        self.assertEqual(stats["removed"], 1)
        self.assertEqual(stats["total"], 1)

        with self.engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT sp.ContentID, sp.rb_local_deleted
                    FROM djmdSongPlaylist sp
                    JOIN djmdPlaylist p ON sp.PlaylistID = p.ID
                    WHERE p.Name = :name AND (sp.rb_local_deleted = 0 OR sp.rb_local_deleted IS NULL);
                """),
                {"name": UNBOUND_PLAYLIST_NAME},
            ).fetchall()
            remaining_ids = {r[0] for r in rows}
            self.assertEqual(remaining_ids, {"t4"})

    @patch("rekordbox_collection_manager.unbound_playlist.create_database_backup")
    @patch("rekordbox_collection_manager.unbound_playlist.is_rekordbox_running", return_value=False)
    def test_reconcile_removes_track_when_tags_removed(self, mock_rb_running, mock_backup):
        """When t4 has its tags deleted, it is removed from πagged-Unbound."""
        reconcile_unbound_playlist(engine=self.engine, force=True, create_backup=False)

        # Soft delete tag for t4
        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE djmdSongMyTag SET rb_local_deleted = 1 WHERE ContentID = 't4';")
            )

        stats = reconcile_unbound_playlist(engine=self.engine, force=True, create_backup=False)
        self.assertEqual(stats["removed"], 1)
        self.assertEqual(stats["total"], 1)

    @patch("rekordbox_collection_manager.unbound_playlist.is_rekordbox_running", return_value=True)
    def test_reconcile_blocked_when_rekordbox_running(self, mock_rb_running):
        """Reconciliation must raise RuntimeError if Rekordbox is running and force=False."""
        with self.assertRaises(RuntimeError):
            reconcile_unbound_playlist(engine=self.engine, force=False)

    def test_unbound_playlist_itself_does_not_exclude_tracks(self):
        """Verify that presence in πagged-Unbound does not cause get_unbound_target_track_ids to drop tracks."""
        with self.engine.begin() as conn:
            pl_id = ensure_unbound_playlist_exists(conn)
            # Add t2 to πagged-Unbound
            conn.execute(
                text("""
                    INSERT INTO djmdSongPlaylist (ID, PlaylistID, ContentID, TrackNo, rb_local_deleted)
                    VALUES ('sp_unbound_t2', :pid, 't2', 1, 0);
                """),
                {"pid": pl_id},
            )
            # Query target track IDs: t2 must still be included
            targets = get_unbound_target_track_ids(conn, unbound_playlist_id=pl_id)
            self.assertIn("t2", targets)
            self.assertIn("t4", targets)

    def test_unbound_playlist_reconcile_worker(self):
        """Verify that UnboundPlaylistReconcileWorker executes and emits success signal."""
        from PySide6.QtWidgets import QApplication
        from rekordbox_collection_manager.gui.workers import UnboundPlaylistReconcileWorker

        _app = QApplication.instance() or QApplication([])

        worker = UnboundPlaylistReconcileWorker(
            engine=self.engine,
            force=True,
        )

        results = []
        worker.success.connect(lambda stats: results.append(stats))
        worker.run()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["added"], 2)
        self.assertEqual(results[0]["total"], 2)

    @patch("sys.argv", ["sync-unbound", "--dry-run"])
    @patch("rekordbox_collection_manager.unbound_playlist.open_database")
    @patch("rekordbox_collection_manager.unbound_playlist.get_default_master_db_path", return_value="/tmp/fake_master.db")
    @patch("os.path.exists", return_value=True)
    def test_cli_dry_run(self, mock_exists, mock_get_path, mock_open_db):
        """Verify CLI --dry-run terminates with exit code 0."""
        from rekordbox_collection_manager.unbound_playlist import sync_unbound_cli

        mock_open_db.return_value = (self.engine, None)

        with self.assertRaises(SystemExit) as cm:
            sync_unbound_cli()
        self.assertEqual(cm.exception.code, 0)

    @patch("rekordbox_collection_manager.utils.is_rekordbox_running", return_value=False)
    @patch("rekordbox_collection_manager.gui.workers.open_database")
    @patch("rekordbox_collection_manager.gui.workers.resolve_db_path", return_value="/tmp/fake_master.db")
    def test_database_loader_worker_auto_reconciles_unbound(self, mock_resolve, mock_open, mock_rb_running):
        """Verify DatabaseLoaderWorker auto-reconciles πagged-Unbound on database load."""
        from PySide6.QtWidgets import QApplication
        from rekordbox_collection_manager.gui.workers import DatabaseLoaderWorker
        from rekordbox_collection_manager.unbound_playlist import UNBOUND_PLAYLIST_NAME

        _app = QApplication.instance() or QApplication([])
        mock_open.return_value = (self.engine, None)

        loader = DatabaseLoaderWorker(db_path="/tmp/fake_master.db")
        loader.run()

        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT ID FROM djmdPlaylist WHERE Name = :name;"),
                {"name": UNBOUND_PLAYLIST_NAME},
            ).fetchone()
            self.assertIsNotNone(row)
            pl_id = row[0]
            track_count = conn.execute(
                text("SELECT count(*) FROM djmdSongPlaylist WHERE PlaylistID = :pl_id;"),
                {"pl_id": pl_id},
            ).scalar()
            self.assertEqual(track_count, 2)

    def test_cue_analysis_playlist_does_not_bind_tracks(self):
        """Verify that presence in CUE Analysis Playlist does not disqualify a tagged track from unbound targets."""
        with self.engine.begin() as conn:
            # Add CUE Analysis Playlist
            conn.exec_driver_sql("""
                INSERT INTO djmdPlaylist (ID, Seq, Name, Attribute, ParentID, rb_local_deleted)
                VALUES ('200000', 99, 'CUE Analysis Playlist', 0, 'root', 0);
            """)
            # Put t2 into CUE Analysis Playlist
            conn.exec_driver_sql("""
                INSERT INTO djmdSongPlaylist (ID, PlaylistID, ContentID, TrackNo, rb_local_deleted)
                VALUES ('sp_cue_t2', '200000', 't2', 1, 0);
            """)

        with self.engine.connect() as conn:
            targets = get_unbound_target_track_ids(conn, unbound_playlist_id="dummy_unbound")
            # t2 should STILL be considered unbound
            self.assertIn("t2", targets)
            self.assertEqual(targets, {"t2", "t4"})


if __name__ == "__main__":
    unittest.main()
