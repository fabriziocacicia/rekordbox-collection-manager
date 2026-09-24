"""Tests for My Tags database queries, workers, and TagsView GUI."""

import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from rekordbox_collection_manager.gui.chip import TagChip, TagChipsContainer
from rekordbox_collection_manager.gui.main_window import MainWindow
from rekordbox_collection_manager.gui.tags_view import TagsView
from rekordbox_collection_manager.gui.track_table import TrackColumn, TrackTableConfig, TracksTableWidget
from rekordbox_collection_manager.tags import (
    _resolve_tag_matching_ids,
    fetch_tag_tracks,
    fetch_tags_tree,
)


class TestTagsBackend(unittest.TestCase):
    """Test suite for My Tags database functions."""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        with self.engine.begin() as conn:
            conn.exec_driver_sql("""
                CREATE TABLE djmdMyTag (
                    ID VARCHAR(255) PRIMARY KEY,
                    Seq INTEGER,
                    Name VARCHAR(255),
                    Attribute INTEGER DEFAULT 0,
                    ParentID VARCHAR(255),
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdSongMyTag (
                    ID VARCHAR(255) PRIMARY KEY,
                    MyTagID VARCHAR(255),
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

            # Insert Root Categories:
            # 1: Genre, 2: Components, 3: Situation, 4: Style
            conn.exec_driver_sql("""
                INSERT INTO djmdMyTag (ID, Seq, Name, Attribute, ParentID, rb_local_deleted)
                VALUES 
                    ('1', 1, 'Genre', 1, 'root', 0),
                    ('2', 2, 'Components', 1, 'root', 0),
                    ('3', 3, 'Situation', 1, 'root', 0),
                    ('4', 4, 'Style', 1, 'root', 0);
            """)

            # Insert Tags under categories:
            # Under Genre (ID 1): Melodic Techno (ID tag_melodic), Afro House (ID tag_afro)
            # Under Style (ID 4): Peak Time (ID tag_peak), Deep Vibes (ID tag_deep)
            # Under Situation (ID 3): Sunset (ID tag_sunset) -> SHOULD BE EXCLUDED BY DEFAULT
            conn.exec_driver_sql("""
                INSERT INTO djmdMyTag (ID, Seq, Name, Attribute, ParentID, rb_local_deleted)
                VALUES 
                    ('tag_melodic', 1, 'Melodic Techno', 0, '1', 0),
                    ('tag_afro', 2, 'Afro House', 0, '1', 0),
                    ('tag_peak', 1, 'Peak Time', 0, '4', 0),
                    ('tag_deep', 2, 'Deep Vibes', 0, '4', 0),
                    ('tag_sunset', 1, 'Sunset Session', 0, '3', 0);
            """)

            # Insert tracks into djmdContent
            conn.exec_driver_sql("""
                INSERT INTO djmdContent (ID, Title, SrcArtistName, BPM, Length, DJPlayCount, FileType, FileNameL, FolderPath)
                VALUES 
                    ('t1', 'Baianá', 'Bakermat', 12400, 180, 5, 1, '01 Baiana.mp3', '/Music/Afro'),
                    ('t2', 'Age of Love', 'ARTBAT', 12600, 240, 12, 12, '02 Age of Love.aiff', '/Music/Techno'),
                    ('t3', 'Consciousness', 'Anyma', 12500, 248, 20, 12, '03 Consciousness.aiff', '/Music/Techno');
            """)

            # Insert track tag relations
            # t1 -> Afro House
            # t2 -> Melodic Techno, Peak Time
            # t3 -> Melodic Techno, Deep Vibes
            # t1 -> Sunset Session (Situation)
            conn.exec_driver_sql("""
                INSERT INTO djmdSongMyTag (ID, MyTagID, ContentID, TrackNo, rb_local_deleted)
                VALUES 
                    ('st1', 'tag_afro', 't1', 1, 0),
                    ('st2', 'tag_melodic', 't2', 1, 0),
                    ('st3', 'tag_peak', 't2', 2, 0),
                    ('st4', 'tag_melodic', 't3', 1, 0),
                    ('st5', 'tag_deep', 't3', 2, 0),
                    ('st6', 'tag_sunset', 't1', 1, 0);
            """)

    def test_resolve_tag_matching_ids(self):
        # String name
        self.assertEqual(_resolve_tag_matching_ids("Melodic Techno"), ["Melodic Techno"])
        # String number
        ids = _resolve_tag_matching_ids("100")
        self.assertIn("100", ids)
        # Negative integer signed mapping to unsigned
        neg_ids = _resolve_tag_matching_ids(-108819745)
        self.assertIn("-108819745", neg_ids)
        self.assertIn("4186147551", neg_ids)
        # Unsigned mapping to signed
        uns_ids = _resolve_tag_matching_ids("4186147551")
        self.assertIn("-108819745", uns_ids)
        self.assertIn("4186147551", uns_ids)

    def test_fetch_tags_tree_genre_and_style_only(self):
        tree = fetch_tags_tree(self.engine, include_deleted=False)
        # Should contain ONLY 2 top-level category folders: Genre and Style
        self.assertEqual(len(tree), 2)
        category_names = [c["name"] for c in tree]
        self.assertIn("Genre", category_names)
        self.assertIn("Style", category_names)
        self.assertNotIn("Situation", category_names)
        self.assertNotIn("Components", category_names)

        # Check Genre children
        genre_folder = next(c for c in tree if c["name"] == "Genre")
        self.assertEqual(len(genre_folder["children"]), 2)
        genre_tags = {t["name"]: t["track_count"] for t in genre_folder["children"]}
        self.assertEqual(genre_tags["Melodic Techno"], 2)
        self.assertEqual(genre_tags["Afro House"], 1)

        # Check Style children
        style_folder = next(c for c in tree if c["name"] == "Style")
        self.assertEqual(len(style_folder["children"]), 2)
        style_tags = {t["name"]: t["track_count"] for t in style_folder["children"]}
        self.assertEqual(style_tags["Peak Time"], 1)
        self.assertEqual(style_tags["Deep Vibes"], 1)

    def test_fetch_tag_tracks_metadata(self):
        tracks = fetch_tag_tracks(self.engine, "tag_melodic", sort_by="title", sort_order="asc")
        self.assertEqual(len(tracks), 2)
        # Age of Love and Consciousness
        titles = [t["title"] for t in tracks]
        self.assertEqual(titles, ["Age of Love", "Consciousness"])

        t2 = next(t for t in tracks if t["title"] == "Age of Love")
        self.assertEqual(t2["artist"], "ARTBAT")
        self.assertEqual(t2["file_type"], "AIFF")
        self.assertEqual(t2["rating"], 0)
        self.assertEqual(t2["folder_name"], "Techno")
        self.assertEqual(t2["folder_dir"], "/Music/Techno")
        self.assertIn("Melodic Techno", t2["tags"])
        self.assertIn("Peak Time", t2["tags"])

    def test_fetch_tag_tracks_filtering_and_search(self):
        # Query filter
        tracks = fetch_tag_tracks(self.engine, "tag_melodic", query="Anyma")
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]["title"], "Consciousness")

        # Exclude streaming (none are streaming here)
        tracks_local = fetch_tag_tracks(self.engine, "tag_afro", exclude_streaming=True)
        self.assertEqual(len(tracks_local), 1)
        self.assertEqual(tracks_local[0]["title"], "Baianá")


class TestTagsGUI(unittest.TestCase):
    """Test suite for TagsView GUI widget and user interactions."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        with self.engine.begin() as conn:
            conn.exec_driver_sql("""
                CREATE TABLE djmdMyTag (
                    ID VARCHAR(255) PRIMARY KEY,
                    Seq INTEGER,
                    Name VARCHAR(255),
                    Attribute INTEGER DEFAULT 0,
                    ParentID VARCHAR(255),
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdSongMyTag (
                    ID VARCHAR(255) PRIMARY KEY,
                    MyTagID VARCHAR(255),
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
                INSERT INTO djmdMyTag (ID, Seq, Name, Attribute, ParentID, rb_local_deleted)
                VALUES 
                    ('1', 1, 'Genre', 1, 'root', 0),
                    ('4', 2, 'Style', 1, 'root', 0),
                    ('tag_melodic', 1, 'Melodic Techno', 0, '1', 0),
                    ('tag_peak', 1, 'Peak Time', 0, '4', 0);
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdContent (ID, Title, SrcArtistName, BPM, Length, DJPlayCount, FileType, FileNameL, FolderPath)
                VALUES 
                    ('t1', 'Hypercolour', 'CamelPhat', 12500, 210, 8, 1, 'Hypercolour.mp3', '/Music/Techno');
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdSongMyTag (ID, MyTagID, ContentID, TrackNo, rb_local_deleted)
                VALUES 
                    ('st1', 'tag_melodic', 't1', 1, 0);
            """)

        self.view = TagsView()
        self.view.engine = self.engine

    def tearDown(self):
        if self.view.active_loader_worker:
            try:
                self.view.active_loader_worker.success.disconnect()
                self.view.active_loader_worker.error.disconnect()
            except Exception:
                pass
            self.view.active_loader_worker.wait(50)
        if self.view.active_tracks_worker:
            try:
                self.view.active_tracks_worker.success.disconnect()
                self.view.active_tracks_worker.error.disconnect()
            except Exception:
                pass
            self.view.active_tracks_worker.wait(50)
        self.view.close()

    def test_tags_view_structure(self):
        self.assertIsNotNone(self.view.splitter)
        self.assertIsNotNone(self.view.tree)
        self.assertIsNotNone(self.view.table)
        self.assertIsNotNone(self.view.stack)
        self.assertEqual(self.view.stack.currentIndex(), 0)

    def test_populate_tags_tree(self):
        tree_data = fetch_tags_tree(self.engine)
        self.view._on_tree_loaded(tree_data)

        # Top level items should be Genre and Style
        self.assertEqual(self.view.tree.topLevelItemCount(), 2)
        top1 = self.view.tree.topLevelItem(0)
        top2 = self.view.tree.topLevelItem(1)
        self.assertIn("Genre", top1.text(0))
        self.assertIn("Style", top2.text(0))

        # Check child item under Genre
        self.assertEqual(top1.childCount(), 1)
        tag_child = top1.child(0)
        self.assertIn("Melodic Techno", tag_child.text(0))
        self.assertIn("(1)", tag_child.text(0))

    def test_open_tag_by_id_or_name(self):
        tree_data = fetch_tags_tree(self.engine)
        self.view._on_tree_loaded(tree_data)

        # Deep link by name
        found = self.view.open_tag_by_id_or_name("Melodic Techno")
        self.assertTrue(found)
        self.assertEqual(self.view.current_tag["id"], "tag_melodic")

        # Deep link by ID
        found_id = self.view.open_tag_by_id_or_name("tag_peak")
        self.assertTrue(found_id)
        self.assertEqual(self.view.current_tag["name"], "Peak Time")

        # Nonexistent
        self.assertFalse(self.view.open_tag_by_id_or_name("NonexistentTag"))

    def test_tag_chip_click_signal(self):
        chip = TagChip("Melodic Techno", clickable=True)
        clicked_tags = []
        chip.clicked.connect(clicked_tags.append)
        chip.clicked.emit(chip.text)
        self.assertEqual(clicked_tags, ["Melodic Techno"])

    def test_tag_chips_container_click_signal(self):
        container = TagChipsContainer()
        container.set_tags(["Melodic Techno", "Peak Time"])
        clicked_tags = []
        container.tag_clicked.connect(clicked_tags.append)

        # Trigger first chip click
        first_chip = container.chips[0]
        first_chip.clicked.emit(first_chip.text)
        self.assertEqual(clicked_tags, ["Melodic Techno"])

    def test_main_window_open_tag_navigation(self):
        with patch.object(MainWindow, "load_database"), patch.object(MainWindow, "_check_rekordbox_status"):
            win = MainWindow(initial_db_path=None)
            try:
                # Nav setup has 7 tabs and index 3 is Tags
                self.assertEqual(len(win.nav_items), 7)
                self.assertEqual(win.nav_items[3]["title"], "Tags")
                self.assertEqual(win.btn_tags.toolTip(), "Tags")

                # Trigger open_tag
                win.open_tag("Melodic Techno")
                self.assertEqual(win.stacked_widget.currentIndex(), 3)
                self.assertTrue(win.btn_tags.isChecked())
            finally:
                win.safety_timer.stop()
                win.close()


if __name__ == "__main__":
    unittest.main()
