"""Tests for Playlists database queries, background workers, and PlaylistsView GUI."""

import unittest
from unittest.mock import patch
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QMenu
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from rekordbox_collection_manager.gui.chip import PlaylistChip, PlaylistChipsContainer
from rekordbox_collection_manager.gui.create_playlist_dialog import CreatePlaylistDialog
from rekordbox_collection_manager.gui.main_window import MainWindow
from rekordbox_collection_manager.gui.playlists_view import PlaylistsView
from rekordbox_collection_manager.gui.track_table import TrackColumn, TrackTableConfig, TracksTableWidget
from rekordbox_collection_manager.playlists import (
    create_playlist,
    delete_playlist,
    fetch_playlist_folders,
    fetch_playlist_tracks,
    fetch_playlists_tree,
    parse_smartlist_to_sql,
)


class TestPlaylistsBackend(unittest.TestCase):
    """Test suite for playlists database functions."""

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
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdSongPlaylist (
                    ID VARCHAR(255) PRIMARY KEY,
                    PlaylistID VARCHAR(255),
                    ContentID VARCHAR(255),
                    TrackNo INTEGER,
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdContent (
                    ID VARCHAR(255) PRIMARY KEY,
                    Title VARCHAR(255),
                    SrcArtistName VARCHAR(255),
                    BPM INTEGER,
                    Length INTEGER,
                    DJPlayCount INTEGER DEFAULT 0,
                    Rating INTEGER DEFAULT 0,
                    ReleaseYear INTEGER,
                    BitRate INTEGER,
                    FileType INTEGER,
                    Commnt TEXT,
                    FolderPath VARCHAR(255),
                    FileNameL VARCHAR(255),
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)

            # Insert playlists: 1 root folder, 2 playlists inside folder, 1 root playlist, 1 smart playlist, 1 deleted playlist
            conn.exec_driver_sql("""
                INSERT INTO djmdPlaylist (ID, Seq, Name, Attribute, ParentID, SmartList, rb_local_deleted)
                VALUES 
                    ('fld_house', 1, 'House Music', 1, 'root', NULL, 0),
                    ('pl_afro', 1, 'Afro House', 0, 'fld_house', NULL, 0),
                    ('pl_deep', 2, 'Deep House', 0, 'fld_house', NULL, 0),
                    ('pl_velvet', 2, 'Velvet Underground', 0, 'root', NULL, 0),
                    ('pl_smart', 3, 'High Energy', 4, 'root', '<NODE LogicalOperator="1"><CONDITION PropertyName="bpm" Operator="3" ValueUnit="" ValueLeft="12450" ValueRight=""/></NODE>', 0),
                    ('pl_del', 4, 'Old Playlist', 0, 'root', NULL, 1);
            """)

            # Insert tracks into djmdContent (FileType 12 = AIFF, 1 = MP3)
            conn.exec_driver_sql("""
                INSERT INTO djmdContent (ID, Title, SrcArtistName, BPM, Length, DJPlayCount, FileType, FileNameL, FolderPath)
                VALUES 
                    ('t1', 'Baianá', 'Bakermat', 12400, 180, 5, 1, '01 Baiana.mp3', '/Music/House'),
                    ('t2', 'Sun Goes Down', 'Cloonee', 12600, 240, 12, 12, '02 Sun Goes Down.aiff', '/Music/House'),
                    ('t3', 'Losing It', 'FISHER', 12500, 248, 20, 1, '03 Losing It.mp3', '/Music/House');
            """)

            # Insert tracks into playlists
            conn.exec_driver_sql("""
                INSERT INTO djmdSongPlaylist (ID, PlaylistID, ContentID, TrackNo, rb_local_deleted)
                VALUES 
                    ('sp1', 'pl_afro', 't1', 1, 0),
                    ('sp2', 'pl_afro', 't2', 2, 0),
                    ('sp3', 'pl_deep', 't3', 1, 0),
                    ('sp4', 'pl_velvet', 't2', 1, 0);
            """)

    def test_fetch_playlists_tree_hierarchy_and_counts(self):
        tree = fetch_playlists_tree(self.engine, include_deleted=False)
        # Should have root nodes: 'fld_house', 'pl_velvet', 'pl_smart'
        self.assertEqual(len(tree), 3)

        folder = next(n for n in tree if n["id"] == "fld_house")
        self.assertEqual(folder["name"], "House Music")
        self.assertEqual(folder["attribute"], 1)
        self.assertEqual(len(folder["children"]), 2)

        afro = next(c for c in folder["children"] if c["id"] == "pl_afro")
        self.assertEqual(afro["track_count"], 2)
        deep = next(c for c in folder["children"] if c["id"] == "pl_deep")
        self.assertEqual(deep["track_count"], 1)

        velvet = next(n for n in tree if n["id"] == "pl_velvet")
        self.assertEqual(velvet["track_count"], 1)

    def test_fetch_playlists_tree_include_deleted(self):
        tree_active = fetch_playlists_tree(self.engine, include_deleted=False)
        all_ids_active = [n["id"] for n in tree_active]
        self.assertNotIn("pl_del", all_ids_active)

        tree_all = fetch_playlists_tree(self.engine, include_deleted=True)
        all_ids_all = [n["id"] for n in tree_all]
        self.assertIn("pl_del", all_ids_all)

    def test_fetch_playlist_tracks(self):
        tracks = fetch_playlist_tracks(self.engine, "pl_afro")
        self.assertEqual(len(tracks), 2)
        self.assertEqual(tracks[0]["track_no"], 1)
        self.assertEqual(tracks[0]["title"], "Baianá")
        self.assertEqual(tracks[0]["bpm"], 124.0)
        self.assertEqual(tracks[0]["duration_str"], "3:00")
        self.assertEqual(tracks[0]["folder_name"], "House")
        self.assertEqual(tracks[0]["file_name"], "01 Baiana.mp3")
        self.assertEqual(tracks[1]["track_no"], 2)
        self.assertEqual(tracks[1]["title"], "Sun Goes Down")
        self.assertEqual(tracks[1]["file_type"], "AIFF")

    def test_fetch_playlist_tracks_search_query(self):
        tracks = fetch_playlist_tracks(self.engine, "pl_afro", query="Cloonee")
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]["artist"], "Cloonee")

    def test_fetch_playlist_tracks_limit(self):
        tracks = fetch_playlist_tracks(self.engine, "pl_afro", limit=1)
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]["track_no"], 1)

    def test_fetch_smart_playlist_tracks(self):
        tracks = fetch_playlist_tracks(self.engine, "pl_smart")
        # 'High Energy' has BPM > 12450. t2 (12600) and t3 (12500) match, t1 (12400) does not.
        self.assertEqual(len(tracks), 2)
        titles = [t["title"] for t in tracks]
        self.assertIn("Sun Goes Down", titles)
        self.assertIn("Losing It", titles)
        self.assertNotIn("Baianá", titles)
        self.assertEqual(tracks[0]["track_no"], 1)
        self.assertEqual(tracks[1]["track_no"], 2)

    def test_fetch_playlists_tree_smart_playlist_count(self):
        tree = fetch_playlists_tree(self.engine, include_deleted=False)
        smart_node = next(n for n in tree if n["id"] == "pl_smart")
        self.assertEqual(smart_node["track_count"], 2)

    def test_fetch_playlists_tree_excludes_deleted_content_and_streaming(self):
        with self.engine.begin() as conn:
            # Add a deleted track to Afro House
            conn.exec_driver_sql("INSERT INTO djmdContent (ID, Title, FolderPath, rb_local_deleted) VALUES ('t_del', 'Deleted Track', '/path', 1);")
            conn.exec_driver_sql("INSERT INTO djmdSongPlaylist (ID, PlaylistID, ContentID, TrackNo) VALUES ('sp_del', 'pl_afro', 't_del', 99);")
            # Add a streaming track to Afro House (e.g. Beatport streaming URI)
            conn.exec_driver_sql("INSERT INTO djmdContent (ID, Title, FolderPath, FileType, rb_local_deleted) VALUES ('t_stream', 'Stream Track', 'beatport:track/123', 25, 0);")
            conn.exec_driver_sql("INSERT INTO djmdSongPlaylist (ID, PlaylistID, ContentID, TrackNo) VALUES ('sp_stream', 'pl_afro', 't_stream', 100);")

        tree = fetch_playlists_tree(self.engine, include_deleted=False)
        folder = next(n for n in tree if n["id"] == "fld_house")
        afro_node = next(n for n in folder["children"] if n["id"] == "pl_afro")
        self.assertEqual(afro_node["track_count"], 2)

    def test_parse_smartlist_to_sql_my_tag(self):
        xml_str = '<NODE LogicalOperator="1"><CONDITION PropertyName="myTag" Operator="8" ValueUnit="" ValueLeft="-1971334219" ValueRight=""/></NODE>'
        clause, params = parse_smartlist_to_sql(xml_str)
        self.assertIn("djmdSongMyTag", clause)
        # Should convert negative 32-bit int to unsigned 2323633077
        self.assertIn("2323633077", params.values())
        self.assertIn("-1971334219", params.values())

    def test_delete_playlist_empty_success(self):
        res = create_playlist(self.engine, "Brand New Empty PL")
        pl_id = res["id"]
        ok = delete_playlist(self.engine, pl_id)
        self.assertTrue(ok)
        from sqlalchemy import text
        with self.engine.connect() as conn:
            del_val = conn.execute(
                text("SELECT rb_local_deleted FROM djmdPlaylist WHERE ID = :pid;"),
                {"pid": pl_id},
            ).scalar()
            self.assertEqual(del_val, 1)

    def test_delete_playlist_non_empty_raises(self):
        # pl_velvet has 1 track in setUp
        with self.assertRaises(ValueError) as ctx:
            delete_playlist(self.engine, "pl_velvet")
        self.assertIn("not empty", str(ctx.exception).lower())

    def test_delete_folder_empty_success(self):
        res = create_playlist(self.engine, "Empty House Folder", is_folder=True)
        fld_id = res["id"]
        ok = delete_playlist(self.engine, fld_id)
        self.assertTrue(ok)

    def test_delete_folder_non_empty_raises(self):
        # fld_house contains pl_afro in setUp
        with self.assertRaises(ValueError) as ctx:
            delete_playlist(self.engine, "fld_house")
        self.assertIn("not empty", str(ctx.exception).lower())

    def test_delete_playlist_not_found_raises(self):
        with self.assertRaises(ValueError):
            delete_playlist(self.engine, "invalid_id_999999")


class TestPlaylistsGUI(unittest.TestCase):
    """Test suite for PlaylistsView and Chip click navigation."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        with self.engine.begin() as conn:
            conn.exec_driver_sql("""
                CREATE TABLE djmdPlaylist (
                    ID VARCHAR(255) PRIMARY KEY,
                    Seq INTEGER,
                    Name VARCHAR(255),
                    Attribute INTEGER DEFAULT 0,
                    ParentID VARCHAR(255),
                    SmartList TEXT,
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdSongPlaylist (
                    ID VARCHAR(255) PRIMARY KEY,
                    PlaylistID VARCHAR(255),
                    ContentID VARCHAR(255),
                    TrackNo INTEGER,
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdContent (
                    ID VARCHAR(255) PRIMARY KEY,
                    Title VARCHAR(255),
                    SrcArtistName VARCHAR(255),
                    BPM INTEGER,
                    Length INTEGER,
                    DJPlayCount INTEGER DEFAULT 0,
                    Rating INTEGER DEFAULT 0,
                    ReleaseYear INTEGER,
                    BitRate INTEGER,
                    FileType INTEGER,
                    Commnt TEXT,
                    FolderPath VARCHAR(255),
                    FileNameL VARCHAR(255),
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdPlaylist (ID, Seq, Name, Attribute, ParentID, rb_local_deleted)
                VALUES 
                    ('fld_house', 1, 'House Music', 1, 'root', 0),
                    ('pl_afro', 1, 'Afro House', 0, 'fld_house', 0),
                    ('pl_melodic', 2, 'Melodic House', 0, 'root', 0);
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdContent (ID, Title, SrcArtistName, BPM, Length, DJPlayCount, FileType, FileNameL, FolderPath)
                VALUES 
                    ('t1', 'Baianá', 'Bakermat', 12400, 180, 5, 1, '01 Baiana.mp3', '/Music/House');
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdSongPlaylist (ID, PlaylistID, ContentID, TrackNo, rb_local_deleted)
                VALUES ('sp1', 'pl_afro', 't1', 1, 0);
            """)

        self.view = PlaylistsView()
        self.view.engine = self.engine

    def tearDown(self):
        if self.view.active_loader_worker and self.view.active_loader_worker.isRunning():
            self.view.active_loader_worker.wait(1000)
        if self.view.active_tracks_worker and self.view.active_tracks_worker.isRunning():
            self.view.active_tracks_worker.wait(1000)
        self.view.close()

    def test_playlists_view_structure(self):
        self.assertIsNotNone(self.view.splitter)
        self.assertIsNotNone(self.view.tree)
        self.assertIsNotNone(self.view.table)
        self.assertIsNotNone(self.view.stack)
        self.assertEqual(self.view.stack.currentIndex(), 0)

    def test_smart_playlist_tree_item_text(self):
        tree_data = [
            {
                "id": "pl_smart",
                "name": "High Energy",
                "attribute": 4,
                "parent_id": None,
                "track_count": 8,
                "children": [],
            }
        ]
        self.view._on_tree_loaded(tree_data)
        item = self.view.tree.topLevelItem(0)
        self.assertIn("High Energy", item.text(0))
        self.assertIn("(8)", item.text(0))
        self.assertNotIn("[Smart]", item.text(0))

    def test_open_playlist_by_id_or_name(self):
        tree_data = fetch_playlists_tree(self.engine)
        self.view._on_tree_loaded(tree_data)

        # Deep link by name
        found = self.view.open_playlist_by_id_or_name("Afro House")
        self.assertTrue(found)
        self.assertEqual(self.view.current_playlist["id"], "pl_afro")

        # Deep link by ID
        found_id = self.view.open_playlist_by_id_or_name("pl_melodic")
        self.assertTrue(found_id)
        self.assertEqual(self.view.current_playlist["name"], "Melodic House")

        # Not found
        self.assertFalse(self.view.open_playlist_by_id_or_name("Nonexistent"))

    def test_playlist_chip_click_signal(self):
        chip = PlaylistChip("Melodic House", clickable=True)
        clicked_names = []
        chip.clicked.connect(clicked_names.append)
        chip.clicked.emit(chip.text)
        self.assertEqual(clicked_names, ["Melodic House"])

    def test_playlist_chips_container_click_signal(self):
        container = PlaylistChipsContainer()
        container.set_playlists(["Afro House", "Deep House"])
        clicked_playlists = []
        container.playlist_clicked.connect(clicked_playlists.append)

        # Trigger first chip click
        first_chip = container.chips[0]
        first_chip.clicked.emit(first_chip.text)
        self.assertEqual(clicked_playlists, ["Afro House"])

    def test_update_playlist_item_count(self):
        tree_data = [
            {
                "id": "pl_1",
                "name": "Warm Deep House",
                "attribute": 0,
                "parent_id": None,
                "track_count": 1,
                "children": [],
            }
        ]
        self.view._on_tree_loaded(tree_data)
        item = self.view.tree.topLevelItem(0)
        self.assertIn("(1)", item.text(0))

        # Update count to 3
        self.view._update_playlist_item_count("pl_1", 3)
        self.assertIn("(3)", item.text(0))
        self.assertIn("Tracks: 3", item.toolTip(0))
        self.assertEqual(self.view.playlists_tree_data[0]["track_count"], 3)

    def test_main_window_open_playlist_navigation(self):
        with patch.object(MainWindow, "load_database"), patch.object(MainWindow, "_check_rekordbox_status"):
            win = MainWindow(initial_db_path=None)
            try:
                # Verify initial nav setup has 7 tabs and index 1 is Playlists
                self.assertEqual(len(win.nav_items), 7)
                self.assertEqual(win.nav_items[1]["title"], "Playlists")
                self.assertEqual(win.btn_playlists.toolTip(), "Playlists")

                # Trigger open_playlist
                win.open_playlist("Afro House")
                self.assertEqual(win.stacked_widget.currentIndex(), 1)
                self.assertTrue(win.btn_playlists.isChecked())
            finally:
                win.safety_timer.stop()
                win.close()

    def test_main_window_nav_click_refreshes_playlists(self):
        with patch.object(MainWindow, "load_database"), patch.object(MainWindow, "_check_rekordbox_status"):
            win = MainWindow(initial_db_path=None)
            try:
                with patch.object(win.playlists_view, "refresh") as mock_refresh:
                    win._on_nav_clicked(1)
                    mock_refresh.assert_called_once_with(force_reload_tracks=True)
            finally:
                win.safety_timer.stop()
                win.close()


class TestCreatePlaylist(unittest.TestCase):
    """Test suite for creating playlists and folders in Rekordbox database and via GUI."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        with self.engine.begin() as conn:
            conn.exec_driver_sql("""
                CREATE TABLE djmdPlaylist (
                    ID VARCHAR(255) PRIMARY KEY,
                    Seq INTEGER,
                    Name VARCHAR(255),
                    Attribute INTEGER DEFAULT 0,
                    ParentID VARCHAR(255),
                    SmartList TEXT,
                    UUID VARCHAR(255),
                    rb_data_status INTEGER DEFAULT 0,
                    rb_local_data_status INTEGER DEFAULT 0,
                    rb_local_deleted INTEGER DEFAULT 0,
                    rb_local_synced INTEGER DEFAULT 0,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdSongPlaylist (
                    ID VARCHAR(255) PRIMARY KEY,
                    PlaylistID VARCHAR(255),
                    ContentID VARCHAR(255),
                    TrackNo INTEGER,
                    rb_local_deleted INTEGER DEFAULT 0,
                    created_at DATETIME,
                    updated_at DATETIME
                );
            """)
            # Initial folders: root folder 'Sets', nested folder '2026'
            conn.exec_driver_sql("""
                INSERT INTO djmdPlaylist (ID, Seq, Name, Attribute, ParentID, UUID, created_at, updated_at)
                VALUES 
                    ('fld_sets', 1, 'Sets', 1, 'root', 'uuid-f1', '2026-01-01', '2026-01-01'),
                    ('fld_2026', 1, '2026', 1, 'fld_sets', 'uuid-f2', '2026-01-01', '2026-01-01');
            """)
            # Initial playlists under Sets
            conn.exec_driver_sql("""
                INSERT INTO djmdPlaylist (ID, Seq, Name, Attribute, ParentID, UUID, rb_local_deleted, created_at, updated_at)
                VALUES 
                    ('101', 1, 'Ibiza Warmup', 0, 'fld_sets', 'uuid-p1', 0, '2026-01-01', '2026-01-01'),
                    ('102', 2, 'Closing Set', 0, 'fld_sets', 'uuid-p2', 0, '2026-01-01', '2026-01-01'),
                    ('103', 3, 'Old Set', 0, 'fld_sets', 'uuid-p3', 1, '2026-01-01', '2026-01-01');
            """)

    def test_fetch_playlist_folders(self):
        folders = fetch_playlist_folders(self.engine)
        self.assertEqual(len(folders), 3)  # Root, Sets, 2026
        self.assertEqual(folders[0]["id"], "root")
        self.assertEqual(folders[0]["name"], "Root (Top-level)")
        self.assertEqual(folders[0]["depth"], 0)

        self.assertEqual(folders[1]["id"], "fld_sets")
        self.assertEqual(folders[1]["name"], "Sets")
        self.assertEqual(folders[1]["depth"], 1)

        self.assertEqual(folders[2]["id"], "fld_2026")
        self.assertEqual(folders[2]["name"], "2026")
        self.assertEqual(folders[2]["depth"], 2)

    def test_create_playlist_success(self):
        result = create_playlist(self.engine, name="Peak Techno", parent_id_or_name="root", is_folder=False)
        self.assertEqual(result["name"], "Peak Techno")
        self.assertEqual(result["parent_id"], "root")
        self.assertEqual(result["attribute"], 0)
        self.assertFalse(result["is_folder"])
        self.assertFalse(result["reactivated"])
        self.assertTrue(result["id"].isdigit())

        # Verify persisted in database
        with self.engine.connect() as conn:
            row = conn.exec_driver_sql(
                "SELECT ID, Name, ParentID, Seq, Attribute, rb_local_deleted FROM djmdPlaylist WHERE ID = :id;",
                {"id": result["id"]},
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[1], "Peak Techno")
            self.assertEqual(row[2], "root")
            self.assertEqual(row[4], 0)  # Attribute 0 = playlist
            self.assertEqual(row[5], 0)  # Active

    def test_create_playlist_folder_success(self):
        result = create_playlist(self.engine, name="Festivals", parent_id_or_name="root", is_folder=True)
        self.assertEqual(result["name"], "Festivals")
        self.assertEqual(result["attribute"], 1)
        self.assertTrue(result["is_folder"])

        with self.engine.connect() as conn:
            row = conn.exec_driver_sql(
                "SELECT Attribute FROM djmdPlaylist WHERE ID = :id;",
                {"id": result["id"]},
            ).fetchone()
            self.assertEqual(row[0], 1)

    def test_create_playlist_under_folder(self):
        result = create_playlist(self.engine, name="Tomorrowland", parent_id_or_name="Sets", is_folder=False)
        self.assertEqual(result["name"], "Tomorrowland")
        self.assertEqual(result["parent_id"], "fld_sets")
        self.assertEqual(result["parent_name"], "Sets")
        self.assertEqual(result["seq"], 3)  # Active in Sets were 2, next is 3

    def test_create_playlist_whitespace_stripped(self):
        result = create_playlist(self.engine, name="   Afterhours   ", parent_id_or_name="root")
        self.assertEqual(result["name"], "Afterhours")

    def test_create_playlist_empty_name_raises(self):
        with self.assertRaises(ValueError) as ctx:
            create_playlist(self.engine, name="   ", parent_id_or_name="root")
        self.assertIn("empty", str(ctx.exception).lower())

    def test_create_playlist_invalid_parent_raises(self):
        with self.assertRaises(ValueError) as ctx:
            create_playlist(self.engine, name="Test", parent_id_or_name="NonExistentFolder")
        self.assertIn("not found", str(ctx.exception).lower())

    def test_create_playlist_duplicate_active_raises(self):
        with self.assertRaises(ValueError) as ctx:
            create_playlist(self.engine, name="ibiza warmup", parent_id_or_name="Sets")
        self.assertIn("already exists", str(ctx.exception).lower())

    def test_create_playlist_reactivates_soft_deleted(self):
        # 'Old Set' was soft-deleted under 'Sets'
        result = create_playlist(self.engine, name="Old Set", parent_id_or_name="Sets")
        self.assertEqual(result["id"], "103")
        self.assertTrue(result["reactivated"])
        self.assertEqual(result["parent_id"], "fld_sets")

        with self.engine.connect() as conn:
            row = conn.exec_driver_sql(
                "SELECT rb_local_deleted, Seq FROM djmdPlaylist WHERE ID = '103';"
            ).fetchone()
            self.assertEqual(row[0], 0, "Should be reactivated")
            self.assertEqual(row[1], 3)

    def test_create_playlist_dialog_ui(self):
        dlg = CreatePlaylistDialog(
            engine=self.engine,
            initial_name="Ibiza Sunset",
            default_parent="Sets",
            is_folder_default=False,
        )
        self.assertEqual(dlg.name_input.text(), "Ibiza Sunset")
        self.assertFalse(dlg.type_combo.currentData())  # Standard playlist

        # Switch to Folder
        dlg.type_combo.setCurrentIndex(1)
        self.assertTrue(dlg.type_combo.currentData())
        self.assertEqual(dlg.btn_create.text(), "Create Folder")

        # Switch back to Playlist
        dlg.type_combo.setCurrentIndex(0)
        self.assertEqual(dlg.btn_create.text(), "Create Playlist")

        # Submit dialog
        with patch("rekordbox_collection_manager.gui.create_playlist_dialog.is_rekordbox_running", return_value=False):
            dlg._on_submit()
        self.assertIsNotNone(dlg.created_playlist)
        self.assertEqual(dlg.created_playlist["name"], "Ibiza Sunset")
        dlg.close()

    def test_playlist_chips_container_custom_item_entered_signal(self):
        container = PlaylistChipsContainer()
        container.set_available_playlists(["Ibiza Warmup", "Closing Set"])

        emitted = []
        container.playlist_create_requested.connect(emitted.append)

        # Type custom playlist that does not exist
        container.input_edit.setText("My New Set")
        container._on_input_return_pressed()

        self.assertEqual(emitted, ["My New Set"])
        container.close()

    def test_playlists_view_create_playlist_actions(self):
        view = PlaylistsView()
        try:
            self.assertTrue(hasattr(view, "btn_new_playlist"))
            self.assertEqual(view.btn_new_playlist.text(), "＋")

            # Mock dialog execution
            mock_dlg = unittest.mock.MagicMock()
            mock_dlg.exec.return_value = 1
            mock_dlg.created_playlist = {
                "id": "205",
                "name": "Live Set",
                "is_folder": False,
                "attribute": 0,
            }

            view.engine = self.engine
            status_emitted = []
            view.notify_status.connect(status_emitted.append)

            with patch("rekordbox_collection_manager.gui.playlists_view.is_rekordbox_running", return_value=False), \
                 patch("rekordbox_collection_manager.gui.playlists_view.CreatePlaylistDialog", return_value=mock_dlg):
                view.btn_new_playlist.click()

            self.assertTrue(any("Created playlist 'Live Set'" in s for s in status_emitted))
        finally:
            view.cleanup()
            view.close()

    def test_playlists_view_context_menu_delete_enabled_for_empty(self):
        view = PlaylistsView()
        try:
            view.engine = self.engine
            tree_data = [
                {
                    "id": "pl_empty",
                    "name": "Empty PL",
                    "seq": 1,
                    "attribute": 0,
                    "parent_id": None,
                    "track_count": 0,
                    "children": [],
                },
                {
                    "id": "pl_full",
                    "name": "Full PL",
                    "seq": 2,
                    "attribute": 0,
                    "parent_id": None,
                    "track_count": 5,
                    "children": [],
                },
            ]
            view.playlists_tree_data = tree_data
            view._on_tree_loaded(tree_data)

            empty_item = None
            full_item = None
            for i in range(view.tree.topLevelItemCount()):
                item = view.tree.topLevelItem(i)
                node = item.data(0, Qt.ItemDataRole.UserRole)
                if node and node["id"] == "pl_empty":
                    empty_item = item
                elif node and node["id"] == "pl_full":
                    full_item = item

            self.assertIsNotNone(empty_item)
            self.assertIsNotNone(full_item)

            created_menus = []
            RealQMenu = QMenu
            class SpyMenu(RealQMenu):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    created_menus.append(self)
                def exec(self, *args, **kwargs):
                    return None
                def exec_(self, *args, **kwargs):
                    return None

            with patch.object(view.tree, "itemAt", return_value=empty_item), \
                 patch("rekordbox_collection_manager.gui.playlists_view.QMenu", SpyMenu):
                view._on_tree_context_menu(QPoint(10, 10))

            self.assertTrue(len(created_menus) > 0)
            empty_menu = created_menus[-1]
            del_actions = [a for a in empty_menu.actions() if "Delete Playlist" in a.text()]
            self.assertEqual(len(del_actions), 1)
            self.assertTrue(del_actions[0].isEnabled())

            # Test non-empty playlist context menu -> Delete action is DISABLED (grayed out)
            with patch.object(view.tree, "itemAt", return_value=full_item), \
                 patch("rekordbox_collection_manager.gui.playlists_view.QMenu", SpyMenu):
                view._on_tree_context_menu(QPoint(10, 10))

            full_menu = created_menus[-1]
            del_full_actions = [a for a in full_menu.actions() if "Delete Playlist" in a.text()]
            self.assertEqual(len(del_full_actions), 1)
            self.assertFalse(del_full_actions[0].isEnabled())
        finally:
            view.cleanup()
            view.close()

    def test_playlists_view_delete_item_success(self):
        view = PlaylistsView()
        try:
            view.engine = self.engine
            res = create_playlist(self.engine, "To Delete PL")
            pl_id = res["id"]

            db_saved_emitted = []
            status_emitted = []
            view.db_saved.connect(lambda: db_saved_emitted.append(True))
            view.notify_status.connect(status_emitted.append)

            from PySide6.QtWidgets import QMessageBox
            with patch("rekordbox_collection_manager.gui.playlists_view.is_rekordbox_running", return_value=False), \
                 patch("PySide6.QtWidgets.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
                view._delete_playlist_item(pl_id, "To Delete PL", "playlist")

            self.assertTrue(len(db_saved_emitted) > 0)
            self.assertTrue(any("Deleted playlist 'To Delete PL'" in s for s in status_emitted))
        finally:
            view.cleanup()
            view.close()


if __name__ == "__main__":
    unittest.main()
