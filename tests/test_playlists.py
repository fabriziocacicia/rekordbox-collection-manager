"""Tests for Playlists database queries, background workers, and PlaylistsView GUI."""

import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from rekordbox_collection_manager.gui.chip import PlaylistChip, PlaylistChipsContainer
from rekordbox_collection_manager.gui.main_window import MainWindow
from rekordbox_collection_manager.gui.playlists_view import PlaylistsView
from rekordbox_collection_manager.gui.track_table import TrackColumn, TrackTableConfig, TracksTableWidget
from rekordbox_collection_manager.playlists import (
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

    def test_parse_smartlist_to_sql_my_tag(self):
        xml_str = '<NODE LogicalOperator="1"><CONDITION PropertyName="myTag" Operator="8" ValueUnit="" ValueLeft="-1971334219" ValueRight=""/></NODE>'
        clause, params = parse_smartlist_to_sql(xml_str)
        self.assertIn("djmdSongMyTag", clause)
        # Should convert negative 32-bit int to unsigned 2323633077
        self.assertIn("2323633077", params.values())
        self.assertIn("-1971334219", params.values())


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

    def test_main_window_open_playlist_navigation(self):
        with patch.object(MainWindow, "load_database"), patch.object(MainWindow, "_check_rekordbox_status"):
            win = MainWindow(initial_db_path=None)
            try:
                # Verify initial nav setup has 7 tabs and index 2 is Playlists
                self.assertEqual(len(win.nav_items), 7)
                self.assertEqual(win.nav_items[2]["title"], "Playlists")
                self.assertEqual(win.btn_playlists.toolTip(), "Playlists")

                # Trigger open_playlist
                win.open_playlist("Afro House")
                self.assertEqual(win.stacked_widget.currentIndex(), 2)
                self.assertTrue(win.btn_playlists.isChecked())
            finally:
                win.safety_timer.stop()
                win.close()


if __name__ == "__main__":
    unittest.main()
