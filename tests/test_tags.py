"""Tests for My Tags database queries, workers, and TagsView GUI."""

import unittest
from unittest.mock import patch
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QMenu
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from rekordbox_collection_manager.edit_play_count import add_track_to_tag
from rekordbox_collection_manager.gui.chip import TagChip, TagChipsContainer
from rekordbox_collection_manager.gui.main_window import MainWindow
from rekordbox_collection_manager.gui.tags_view import TagsView
from rekordbox_collection_manager.gui.track_table import TrackColumn, TrackTableConfig, TracksTableWidget
from rekordbox_collection_manager.gui.create_tag_dialog import CreateTagDialog
from rekordbox_collection_manager.tags import (
    _resolve_tag_matching_ids,
    cleanup_duplicate_song_tags,
    create_tag,
    delete_tag,
    fetch_tag_categories,
    fetch_tag_tracks,
    fetch_tags_tree,
    sort_tags_in_folder,
)
from rekordbox_collection_manager.utils import build_track_record


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

    def test_fetch_tag_tracks_sorting(self):
        # t2: Age of Love, Length=240, DJPlayCount=12, BPM=12600, FileNameL='02 Age of Love.aiff'
        # t3: Consciousness, Length=248, DJPlayCount=20, BPM=12500, FileNameL='03 Consciousness.aiff'
        
        # Sort by duration / time
        tracks_dur_desc = fetch_tag_tracks(self.engine, "tag_melodic", sort_by="duration", sort_order="desc")
        self.assertEqual([t["title"] for t in tracks_dur_desc], ["Consciousness", "Age of Love"])

        tracks_dur_asc = fetch_tag_tracks(self.engine, "tag_melodic", sort_by="duration", sort_order="asc")
        self.assertEqual([t["title"] for t in tracks_dur_asc], ["Age of Love", "Consciousness"])

        tracks_time_desc = fetch_tag_tracks(self.engine, "tag_melodic", sort_by="time", sort_order="desc")
        self.assertEqual([t["title"] for t in tracks_time_desc], ["Consciousness", "Age of Love"])

        tracks_time_asc = fetch_tag_tracks(self.engine, "tag_melodic", sort_by="time", sort_order="asc")
        self.assertEqual([t["title"] for t in tracks_time_asc], ["Age of Love", "Consciousness"])

        # Sort by play_count / plays
        tracks_plays_desc = fetch_tag_tracks(self.engine, "tag_melodic", sort_by="play_count", sort_order="desc")
        self.assertEqual([t["title"] for t in tracks_plays_desc], ["Consciousness", "Age of Love"])

        tracks_plays_asc = fetch_tag_tracks(self.engine, "tag_melodic", sort_by="play_count", sort_order="asc")
        self.assertEqual([t["title"] for t in tracks_plays_asc], ["Age of Love", "Consciousness"])

        tracks_plays_alias = fetch_tag_tracks(self.engine, "tag_melodic", sort_by="plays", sort_order="desc")
        self.assertEqual([t["title"] for t in tracks_plays_alias], ["Consciousness", "Age of Love"])

        # Sort by filename / file_name
        tracks_fn_desc = fetch_tag_tracks(self.engine, "tag_melodic", sort_by="filename", sort_order="desc")
        self.assertEqual([t["title"] for t in tracks_fn_desc], ["Consciousness", "Age of Love"])

        tracks_fn_asc = fetch_tag_tracks(self.engine, "tag_melodic", sort_by="filename", sort_order="asc")
        self.assertEqual([t["title"] for t in tracks_fn_asc], ["Age of Love", "Consciousness"])

    def test_sort_tags_in_folder_asc(self):
        # Under Genre ('1'): Initially 'Melodic Techno' (Seq 1), 'Afro House' (Seq 2)
        count = sort_tags_in_folder(self.engine, "1", descending=False)
        self.assertEqual(count, 2)

        tree = fetch_tags_tree(self.engine)
        genre_folder = next(c for c in tree if c["name"] == "Genre")
        child_names = [t["name"] for t in genre_folder["children"]]
        # Afro House (Seq 1) should now come before Melodic Techno (Seq 2)
        self.assertEqual(child_names, ["Afro House", "Melodic Techno"])
        self.assertEqual(genre_folder["children"][0]["seq"], 1)
        self.assertEqual(genre_folder["children"][1]["seq"], 2)

    def test_sort_tags_in_folder_desc(self):
        # Under Style ('4'): Initially 'Peak Time' (Seq 1), 'Deep Vibes' (Seq 2)
        # In descending order, Peak Time ('P') should come before Deep Vibes ('D')
        count = sort_tags_in_folder(self.engine, "4", descending=True)
        self.assertEqual(count, 2)

        tree = fetch_tags_tree(self.engine)
        style_folder = next(c for c in tree if c["name"] == "Style")
        child_names = [t["name"] for t in style_folder["children"]]
        self.assertEqual(child_names, ["Peak Time", "Deep Vibes"])
        self.assertEqual(style_folder["children"][0]["seq"], 1)
        self.assertEqual(style_folder["children"][1]["seq"], 2)

    def test_sort_tags_in_folder_empty_or_nonexistent(self):
        count = sort_tags_in_folder(self.engine, "999999", descending=False)
        self.assertEqual(count, 0)

    def test_delete_tag_empty_success(self):
        res = create_tag(self.engine, "Brand New Empty Tag", parent_id_or_name="Genre")
        tag_id = res["id"]
        ok = delete_tag(self.engine, tag_id)
        self.assertTrue(ok)
        from sqlalchemy import text
        with self.engine.connect() as conn:
            del_val = conn.execute(
                text("SELECT rb_local_deleted FROM djmdMyTag WHERE ID = :tid;"),
                {"tid": tag_id},
            ).scalar()
            self.assertEqual(del_val, 1)

    def test_delete_tag_non_empty_raises(self):
        # tag_afro has content t1 in setUp
        with self.assertRaises(ValueError) as ctx:
            delete_tag(self.engine, "tag_afro")
        self.assertIn("not empty", str(ctx.exception).lower())

    def test_delete_tag_not_found_raises(self):
        with self.assertRaises(ValueError):
            delete_tag(self.engine, "non_existent_999999")

    def test_delete_category_non_empty_raises(self):
        # Genre (ID 1) has active children
        with self.assertRaises(ValueError) as ctx:
            delete_tag(self.engine, "1")
        self.assertIn("not empty", str(ctx.exception).lower())


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
                    ('tag_afro', 2, 'Afro House', 0, '1', 0),
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
        if self.view.active_sort_worker:
            try:
                self.view.active_sort_worker.success.disconnect()
                self.view.active_sort_worker.error.disconnect()
            except Exception:
                pass
            self.view.active_sort_worker.wait(50)
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

        # Check child items under Genre
        self.assertEqual(top1.childCount(), 2)
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
        chip.clicked.emit(chip.text())
        self.assertEqual(clicked_tags, ["Melodic Techno"])

    def test_tag_chips_container_click_signal(self):
        container = TagChipsContainer()
        container.set_tags(["Melodic Techno", "Peak Time"])
        clicked_tags = []
        container.tag_clicked.connect(clicked_tags.append)

        # Trigger first chip click
        first_chip = container.chips[0]
        first_chip.clicked.emit(first_chip.text())
        self.assertEqual(clicked_tags, ["Melodic Techno"])

    def test_main_window_open_tag_navigation(self):
        with patch.object(MainWindow, "load_database"), patch.object(MainWindow, "_check_rekordbox_status"):
            win = MainWindow(initial_db_path=None)
            try:
                # Nav setup has 8 tabs and index 2 is Tags
                self.assertEqual(len(win.nav_items), 8)
                self.assertEqual(win.nav_items[2]["title"], "Tags")
                self.assertEqual(win.btn_tags.toolTip(), "Tags")

                # Trigger open_tag
                win.open_tag("Melodic Techno")
                self.assertEqual(win.stacked_widget.currentIndex(), 2)
                self.assertTrue(win.btn_tags.isChecked())
            finally:
                win.safety_timer.stop()
                win.close()

    def test_tags_view_sort_folder_rekordbox_running(self):
        from PySide6.QtWidgets import QMessageBox

        with patch("rekordbox_collection_manager.gui.tags_view.is_rekordbox_running", return_value=True), \
             patch.object(QMessageBox, "warning") as mock_warn:
            self.view._sort_folder_tags("1", "Genre")
            mock_warn.assert_called_once()
            self.assertIn("Rekordbox is Running", mock_warn.call_args[0][1])

    def test_tags_view_sort_folder_success(self):
        from PySide6.QtWidgets import QMessageBox

        tree_data = fetch_tags_tree(self.engine)
        self.view._on_tree_loaded(tree_data)

        db_saved_called = []
        self.view.db_saved.connect(lambda: db_saved_called.append(True))
        notify_status_calls = []
        self.view.notify_status.connect(notify_status_calls.append)

        with patch("rekordbox_collection_manager.gui.tags_view.is_rekordbox_running", return_value=False), \
             patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.view._sort_folder_tags("1", "Genre", descending=False)
            if self.view.active_sort_worker:
                self.view.active_sort_worker.wait(2000)
            QApplication.processEvents()

        self.assertTrue(len(db_saved_called) > 0)
        self.assertTrue(
            "Sorted 2 tags in 'Genre' alphabetically" in self.view.status_label.text()
            or any("Sorted 2 tags in 'Genre' alphabetically" in m for m in notify_status_calls)
        )

    def test_tags_view_tree_context_menu_policy(self):
        self.assertEqual(self.view.tree.contextMenuPolicy(), Qt.ContextMenuPolicy.CustomContextMenu)

    def test_tags_view_on_tree_context_menu_triggers_sort(self):
        from unittest.mock import MagicMock
        from PySide6.QtCore import QPoint

        tree_data = fetch_tags_tree(self.engine)
        self.view._on_tree_loaded(tree_data)

        item = self.view.tree.topLevelItem(0)

        sort_calls = []
        with patch.object(self.view.tree, "itemAt", return_value=item), \
             patch.object(self.view, "_sort_folder_tags", side_effect=lambda fid, fname, descending=False: sort_calls.append((fid, fname, descending))), \
             patch("rekordbox_collection_manager.gui.tags_view.QMenu") as mock_menu_cls:
            mock_menu = MagicMock()
            mock_menu_cls.return_value = mock_menu
            mock_act_new = MagicMock()
            mock_act_az = MagicMock()
            mock_act_za = MagicMock()
            mock_menu.addAction.side_effect = [mock_act_new, mock_act_az, mock_act_za]
            mock_menu.exec.return_value = mock_act_az

            self.view._on_tree_context_menu(QPoint(10, 10))

        self.assertEqual(len(sort_calls), 1)
        self.assertEqual(sort_calls[0][0], "1")
        self.assertEqual(sort_calls[0][1], "Genre")
        self.assertFalse(sort_calls[0][2])

    def test_tags_view_table_sort_by_time_and_plays(self):
        # Insert a second track to test relative sorting
        with self.engine.begin() as conn:
            conn.exec_driver_sql("""
                INSERT INTO djmdContent (ID, Title, SrcArtistName, BPM, Length, DJPlayCount, FileType, FileNameL, FolderPath)
                VALUES ('t2', 'Zanzibar', 'ARTBAT', 12600, 300, 15, 1, 'Zanzibar.mp3', '/Music/Techno');
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdSongMyTag (ID, MyTagID, ContentID, TrackNo, rb_local_deleted)
                VALUES ('st2', 'tag_melodic', 't2', 2, 0);
            """)

        self.view.current_tag = {"id": "tag_melodic", "name": "Melodic Techno"}

        # Sort by duration DESC (Zanzibar 300s, Hypercolour 210s)
        self.view._on_table_sort_requested("duration", Qt.SortOrder.DescendingOrder)
        self.assertEqual(self.view.current_sort_field, "duration")
        self.assertEqual(self.view.current_sort_order, Qt.SortOrder.DescendingOrder)
        if self.view.active_tracks_worker:
            self.view.active_tracks_worker.wait(2000)
        QApplication.processEvents()
        self.assertEqual([t["title"] for t in self.view.records], ["Zanzibar", "Hypercolour"])

        # Sort by duration ASC (Hypercolour 210s, Zanzibar 300s)
        self.view._on_table_sort_requested("duration", Qt.SortOrder.AscendingOrder)
        if self.view.active_tracks_worker:
            self.view.active_tracks_worker.wait(2000)
        QApplication.processEvents()
        self.assertEqual([t["title"] for t in self.view.records], ["Hypercolour", "Zanzibar"])

        # Sort by play_count DESC (Zanzibar 15 plays, Hypercolour 8 plays)
        self.view._on_table_sort_requested("play_count", Qt.SortOrder.DescendingOrder)
        self.assertEqual(self.view.current_sort_field, "play_count")
        self.assertEqual(self.view.current_sort_order, Qt.SortOrder.DescendingOrder)
        if self.view.active_tracks_worker:
            self.view.active_tracks_worker.wait(2000)
        QApplication.processEvents()
        self.assertEqual([t["title"] for t in self.view.records], ["Zanzibar", "Hypercolour"])

        # Sort by play_count ASC (Hypercolour 8 plays, Zanzibar 15 plays)
        self.view._on_table_sort_requested("play_count", Qt.SortOrder.AscendingOrder)
        self.assertEqual(self.view.current_sort_field, "play_count")
        self.assertEqual(self.view.current_sort_order, Qt.SortOrder.AscendingOrder)
        if self.view.active_tracks_worker:
            self.view.active_tracks_worker.wait(2000)
        QApplication.processEvents()
        self.assertEqual([t["title"] for t in self.view.records], ["Hypercolour", "Zanzibar"])

    def test_load_tag_prompts_when_detail_view_has_unsaved_changes(self):
        self.view.current_tag = {"id": "tag_melodic", "name": "Melodic Techno", "attribute": 0}
        self.view.stack.setCurrentIndex(1)
        with patch.object(self.view.detail_view, "_confirm_unsaved_changes", return_value=False) as mock_confirm:
            self.view.load_tag({"id": "tag_peak", "name": "Peak Time", "attribute": 0})
            mock_confirm.assert_called_once()
        self.assertEqual(self.view.current_tag["id"], "tag_melodic")
        self.assertEqual(self.view.stack.currentIndex(), 1)

    def test_tags_view_cleanup_stops_search_timer(self):
        self.view.search_debounce_timer.start(5000)
        self.assertTrue(self.view.search_debounce_timer.isActive())
        self.view.cleanup()
        self.assertFalse(self.view.search_debounce_timer.isActive())


class TestTagDeduplication(unittest.TestCase):
    """Test suite ensuring tags and playlists are never duplicated in display, queries, or database."""

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
                    created_at VARCHAR(255),
                    updated_at VARCHAR(255),
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                CREATE TABLE djmdPlaylist (
                    ID VARCHAR(255) PRIMARY KEY,
                    Name VARCHAR(255),
                    Attribute INTEGER DEFAULT 0,
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

            # Insert category and tags
            conn.exec_driver_sql("""
                INSERT INTO djmdMyTag (ID, Seq, Name, Attribute, ParentID) VALUES
                    ('cat_genre', 1, 'Genre', 1, 'root'),
                    ('3153602631', 1, 'Tech House', 0, 'cat_genre'),
                    ('1178900277', 2, 'Groovy', 0, 'cat_genre');
            """)

            # Insert a track
            conn.exec_driver_sql("""
                INSERT INTO djmdContent (ID, Title, SrcArtistName, BPM, Length, DJPlayCount, FileType, FileNameL, FolderPath)
                VALUES ('100', 'Turn My Speakers Up', 'Tre Reynolds', 12800, 200, 10, 1, 'track.aiff', '/Music');
            """)

    def test_build_track_record_deduplicates_tags_and_playlists(self):
        """Verify build_track_record eliminates duplicate tags and playlists."""
        row = {
            "ID": "100",
            "Title": "Turn My Speakers Up",
            "TagNames": "Tech House|||Groovy|||Tech House|||Groovy",
            "PlaylistNames": "Festival|||Club|||Festival",
        }
        rec = build_track_record(row)
        self.assertEqual(rec["tags"], ["Tech House", "Groovy"])
        self.assertEqual(rec["playlists"], ["Festival", "Club"])

    def test_fetch_tags_tree_deduplicates_duplicate_rows(self):
        """Verify fetch_tags_tree counts each track once even if djmdSongMyTag has duplicate rows."""
        with self.engine.begin() as conn:
            # Insert duplicate active associations for track 100 with Tech House
            conn.exec_driver_sql("""
                INSERT INTO djmdSongMyTag (ID, MyTagID, ContentID, created_at, rb_local_deleted) VALUES
                    ('smt_1', '3153602631', '100', '2026-09-04 10:00:00', 0),
                    ('smt_2', '3153602631', '100', '2026-09-10 10:00:00', 0);
            """)

        tree = fetch_tags_tree(self.engine, category_names=("Genre",))
        genre_node = tree[0]
        tech_house = next(c for c in genre_node["children"] if c["name"] == "Tech House")
        # Track count must be 1, not 2
        self.assertEqual(tech_house["track_count"], 1)

    def test_fetch_tag_tracks_deduplicates_tracks_and_tags(self):
        """Verify fetch_tag_tracks returns each track once and track tags are unique."""
        with self.engine.begin() as conn:
            # Duplicate Tech House rows, plus Groovy rows
            conn.exec_driver_sql("""
                INSERT INTO djmdSongMyTag (ID, MyTagID, ContentID, created_at, rb_local_deleted) VALUES
                    ('smt_1', '3153602631', '100', '2026-09-04 10:00:00', 0),
                    ('smt_2', '3153602631', '100', '2026-09-10 10:00:00', 0),
                    ('smt_3', '1178900277', '100', '2026-09-04 10:00:00', 0);
            """)

        tracks = fetch_tag_tracks(self.engine, "3153602631")
        # Only 1 track returned, not duplicated
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]["id"], "100")
        # Tags on the track are unique
        self.assertEqual(tracks[0]["tags"], ["Tech House", "Groovy"])

    def test_add_track_to_tag_prevents_duplicate_rows(self):
        """Verify add_track_to_tag does not insert duplicate rows when called multiple times."""
        # Add to Tech House
        self.assertTrue(add_track_to_tag(self.engine, "100", "Tech House"))
        # Add again
        self.assertTrue(add_track_to_tag(self.engine, "100", "Tech House"))

        with self.engine.connect() as conn:
            cnt = conn.exec_driver_sql(
                "SELECT count(*) FROM djmdSongMyTag WHERE ContentID = '100' AND MyTagID = '3153602631';"
            ).scalar()
            self.assertEqual(cnt, 1)

    def test_add_track_to_tag_matches_signed_unsigned_id(self):
        """Verify add_track_to_tag matches signed 32-bit ID (-1141364665) for 3153602631."""
        signed_id = str(3153602631 - (1 << 32))  # -1141364665
        with self.engine.begin() as conn:
            conn.exec_driver_sql(f"""
                INSERT INTO djmdSongMyTag (ID, MyTagID, ContentID, created_at, rb_local_deleted)
                VALUES ('smt_signed', '{signed_id}', '100', '2026-09-04 10:00:00', 0);
            """)

        # Attempt to add using unsigned ID/tag name
        self.assertTrue(add_track_to_tag(self.engine, "100", "Tech House"))

        with self.engine.connect() as conn:
            total_rows = conn.exec_driver_sql(
                "SELECT count(*) FROM djmdSongMyTag WHERE ContentID = '100';"
            ).scalar()
            self.assertEqual(total_rows, 1, "Should recognize existing signed ID and avoid inserting duplicate")

    def test_cleanup_duplicate_song_tags(self):
        """Verify cleanup_duplicate_song_tags soft-deletes duplicate active rows."""
        with self.engine.begin() as conn:
            conn.exec_driver_sql("""
                INSERT INTO djmdSongMyTag (ID, MyTagID, ContentID, created_at, rb_local_deleted) VALUES
                    ('row_old', '3153602631', '100', '2026-09-04 10:00:00', 0),
                    ('row_new', '3153602631', '100', '2026-09-10 10:00:00', 0);
            """)

        cleaned = cleanup_duplicate_song_tags(self.engine)
        self.assertEqual(cleaned, 1)

        with self.engine.connect() as conn:
            active = conn.exec_driver_sql(
                "SELECT ID FROM djmdSongMyTag WHERE ContentID = '100' AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL);"
            ).fetchall()
            self.assertEqual(len(active), 1)
            self.assertEqual(active[0][0], "row_old", "Earliest row should be preserved")

            del_status = conn.exec_driver_sql(
                "SELECT rb_local_deleted FROM djmdSongMyTag WHERE ID = 'row_new';"
            ).scalar()
            self.assertEqual(del_status, 1, "Duplicate row should be marked soft-deleted")


class TestCreateTag(unittest.TestCase):
    """Test suite for creating tags in Rekordbox database and via GUI."""

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
                CREATE TABLE djmdMyTag (
                    ID VARCHAR(255) PRIMARY KEY,
                    Seq INTEGER,
                    Name VARCHAR(255),
                    Attribute INTEGER DEFAULT 0,
                    ParentID VARCHAR(255),
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
                CREATE TABLE djmdSongMyTag (
                    ID VARCHAR(255) PRIMARY KEY,
                    MyTagID VARCHAR(255),
                    ContentID VARCHAR(255),
                    TrackNo INTEGER,
                    rb_local_deleted INTEGER DEFAULT 0,
                    created_at DATETIME,
                    updated_at DATETIME
                );
            """)
            # Categories
            conn.exec_driver_sql("""
                INSERT INTO djmdMyTag (ID, Seq, Name, Attribute, ParentID, UUID, created_at, updated_at)
                VALUES 
                    ('1', 1, 'Genre', 1, 'root', 'uuid-1', '2026-01-01', '2026-01-01'),
                    ('4', 2, 'Style', 1, 'root', 'uuid-4', '2026-01-01', '2026-01-01');
            """)
            # Existing tags under Genre
            conn.exec_driver_sql("""
                INSERT INTO djmdMyTag (ID, Seq, Name, Attribute, ParentID, UUID, rb_local_deleted, created_at, updated_at)
                VALUES 
                    ('101', 1, 'Techno', 0, '1', 'uuid-t1', 0, '2026-01-01', '2026-01-01'),
                    ('102', 2, 'House', 0, '1', 'uuid-t2', 0, '2026-01-01', '2026-01-01'),
                    ('103', 3, 'Trance', 0, '1', 'uuid-t3', 1, '2026-01-01', '2026-01-01');
            """)

    def test_fetch_tag_categories(self):
        cats = fetch_tag_categories(self.engine)
        self.assertEqual(len(cats), 2)
        cat_names = [c["name"] for c in cats]
        self.assertIn("Genre", cat_names)
        self.assertIn("Style", cat_names)

    def test_create_tag_success(self):
        result = create_tag(self.engine, name="Acid House", parent_id_or_name="Genre")
        self.assertEqual(result["name"], "Acid House")
        self.assertEqual(result["parent_id"], "1")
        self.assertEqual(result["category_name"], "Genre")
        self.assertEqual(result["seq"], 3)  # Active tags in Genre were 2, next is 3
        self.assertFalse(result["reactivated"])
        self.assertTrue(result["id"].isdigit())

        # Verify persisted in database
        with self.engine.connect() as conn:
            row = conn.exec_driver_sql(
                "SELECT ID, Name, ParentID, Seq, Attribute, rb_local_deleted FROM djmdMyTag WHERE ID = :id;",
                {"id": result["id"]},
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[1], "Acid House")
            self.assertEqual(row[2], "1")
            self.assertEqual(row[3], 3)
            self.assertEqual(row[4], 0)  # Attribute 0 = tag
            self.assertEqual(row[5], 0)  # Active

    def test_create_tag_by_category_id(self):
        result = create_tag(self.engine, name="Peak Time", parent_id_or_name="4")
        self.assertEqual(result["name"], "Peak Time")
        self.assertEqual(result["parent_id"], "4")
        self.assertEqual(result["category_name"], "Style")
        self.assertEqual(result["seq"], 1)

    def test_create_tag_whitespace_stripped(self):
        result = create_tag(self.engine, name="   Minimal Tech   ", parent_id_or_name="Genre")
        self.assertEqual(result["name"], "Minimal Tech")

    def test_create_tag_empty_name_raises(self):
        with self.assertRaises(ValueError) as ctx:
            create_tag(self.engine, name="   ", parent_id_or_name="Genre")
        self.assertIn("empty", str(ctx.exception).lower())

    def test_create_tag_invalid_category_raises(self):
        with self.assertRaises(ValueError) as ctx:
            create_tag(self.engine, name="Dubstep", parent_id_or_name="NonExistent")
        self.assertIn("not found", str(ctx.exception).lower())

    def test_create_tag_duplicate_active_raises(self):
        with self.assertRaises(ValueError) as ctx:
            create_tag(self.engine, name="techno", parent_id_or_name="Genre")
        self.assertIn("already exists", str(ctx.exception).lower())

    def test_create_tag_reactivates_soft_deleted(self):
        # 'Trance' is soft-deleted with ID 103
        result = create_tag(self.engine, name="Trance", parent_id_or_name="Style")
        self.assertEqual(result["id"], "103")
        self.assertTrue(result["reactivated"])
        self.assertEqual(result["parent_id"], "4")  # Moved to Style

        with self.engine.connect() as conn:
            row = conn.exec_driver_sql(
                "SELECT rb_local_deleted, ParentID FROM djmdMyTag WHERE ID = '103';"
            ).fetchone()
            self.assertEqual(row[0], 0, "Should be reactivated")
            self.assertEqual(row[1], "4")

    def test_create_tag_dialog_ui(self):
        dlg = CreateTagDialog(
            engine=self.engine,
            initial_name="Ambient",
            default_category="Genre",
        )
        self.assertEqual(dlg.name_input.text(), "Ambient")
        # Submit dialog
        with patch("rekordbox_collection_manager.gui.create_tag_dialog.is_rekordbox_running", return_value=False):
            dlg._on_submit()
        self.assertIsNotNone(dlg.created_tag)
        self.assertEqual(dlg.created_tag["name"], "Ambient")
        dlg.close()

    def test_tag_chips_container_custom_item_entered_signal(self):
        container = TagChipsContainer()
        container.set_available_tags(["Techno", "House"])

        emitted_tags = []
        container.tag_create_requested.connect(emitted_tags.append)

        # Type custom tag that doesn't exist
        container.input_edit.setText("Progressive")
        container._on_input_return_pressed()

        self.assertEqual(emitted_tags, ["Progressive"])
        container.close()

    def test_tags_view_context_menu_delete_enabled_for_empty(self):
        view = TagsView()
        try:
            view.engine = self.engine
            tree_data = [
                {
                    "id": "1",
                    "name": "Genre",
                    "seq": 1,
                    "attribute": 1,
                    "parent_id": "root",
                    "track_count": 5,
                    "children": [
                        {
                            "id": "t_empty",
                            "name": "Empty Tag",
                            "seq": 1,
                            "attribute": 0,
                            "parent_id": "1",
                            "category_name": "Genre",
                            "track_count": 0,
                            "children": [],
                        },
                        {
                            "id": "t_full",
                            "name": "Full Tag",
                            "seq": 2,
                            "attribute": 0,
                            "parent_id": "1",
                            "category_name": "Genre",
                            "track_count": 3,
                            "children": [],
                        },
                    ],
                }
            ]
            view.tags_tree_data = tree_data
            view._on_tree_loaded(tree_data)

            empty_item = None
            full_item = None
            cat_item = view.tree.topLevelItem(0)
            for i in range(cat_item.childCount()):
                child = cat_item.child(i)
                node = child.data(0, Qt.ItemDataRole.UserRole)
                if node and node["id"] == "t_empty":
                    empty_item = child
                elif node and node["id"] == "t_full":
                    full_item = child

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

            # Test empty tag context menu -> Delete Tag is ENABLED (selectable)
            with patch.object(view.tree, "itemAt", return_value=empty_item), \
                 patch("rekordbox_collection_manager.gui.tags_view.QMenu", SpyMenu):
                view._on_tree_context_menu(QPoint(10, 10))

            self.assertTrue(len(created_menus) > 0)
            empty_menu = created_menus[-1]
            del_actions = [a for a in empty_menu.actions() if "Delete Tag" in a.text()]
            self.assertEqual(len(del_actions), 1)
            self.assertTrue(del_actions[0].isEnabled())

            # Test non-empty tag context menu -> Delete Tag is DISABLED (grayed out)
            with patch.object(view.tree, "itemAt", return_value=full_item), \
                 patch("rekordbox_collection_manager.gui.tags_view.QMenu", SpyMenu):
                view._on_tree_context_menu(QPoint(10, 10))

            full_menu = created_menus[-1]
            del_full_actions = [a for a in full_menu.actions() if "Delete Tag" in a.text()]
            self.assertEqual(len(del_full_actions), 1)
            self.assertFalse(del_full_actions[0].isEnabled())
        finally:
            view.cleanup()
            view.close()

    def test_tags_view_delete_item_success(self):
        view = TagsView()
        try:
            view.engine = self.engine
            res = create_tag(self.engine, "To Delete Tag", parent_id_or_name="Genre")
            tag_id = res["id"]

            db_saved_emitted = []
            status_emitted = []
            view.db_saved.connect(lambda: db_saved_emitted.append(True))
            view.notify_status.connect(status_emitted.append)

            from PySide6.QtWidgets import QMessageBox
            with patch("rekordbox_collection_manager.gui.tags_view.is_rekordbox_running", return_value=False), \
                 patch("PySide6.QtWidgets.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
                view._delete_tag_item(tag_id, "To Delete Tag", "tag")

            self.assertTrue(len(db_saved_emitted) > 0)
            self.assertTrue(any("Deleted tag 'To Delete Tag'" in s for s in status_emitted))
        finally:
            view.cleanup()
            view.close()


if __name__ == "__main__":
    unittest.main()

