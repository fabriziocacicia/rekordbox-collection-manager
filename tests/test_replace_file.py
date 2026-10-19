"""Unit tests for the Replace File tool (backend engine, workers, and GUI view)."""

import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Headless Qt for unit testing
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication
from sqlalchemy import create_engine, text

from rekordbox_collection_manager.gui.replace_file_view import FileDropZone, ReplaceFileView
from rekordbox_collection_manager.gui.workers import AudioInspectWorker, TrackMergeWorker, TrackReplaceWorker
from rekordbox_collection_manager.replace_file import (
    inspect_audio_file,
    merge_imported_tracks,
    replace_track_audio_file,
    trash_file_macos,
)

app = QApplication.instance()
if app is None:
    app = QApplication([])


class TestReplaceFileBackend(unittest.TestCase):
    """Test audio inspection, in-place file replacement, and track merging."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.engine = create_engine("sqlite:///:memory:")

        # Create mock tables matching Rekordbox master.db schema
        with self.engine.begin() as conn:
            conn.exec_driver_sql("""
                CREATE TABLE djmdContent (
                    ID VARCHAR(255) PRIMARY KEY,
                    FolderPath VARCHAR(255),
                    FileNameL VARCHAR(255),
                    FileNameS VARCHAR(255),
                    Title VARCHAR(255),
                    ArtistID VARCHAR(255),
                    AlbumID VARCHAR(255),
                    GenreID VARCHAR(255),
                    BPM INTEGER,
                    Length INTEGER,
                    BitRate INTEGER,
                    BitDepth INTEGER,
                    SampleRate INTEGER,
                    FileSize INTEGER,
                    FileType INTEGER,
                    DJPlayCount INTEGER,
                    StockDate VARCHAR(255),
                    DateCreated VARCHAR(255),
                    AnalysisDataPath VARCHAR(255),
                    TrackInfoUpdated VARCHAR(255),
                    AnalysisUpdated VARCHAR(255),
                    rb_local_deleted SMALLINT DEFAULT 0,
                    created_at DATETIME,
                    updated_at DATETIME
                );
            """)

            conn.exec_driver_sql("""
                CREATE TABLE djmdSongHistory (
                    ID VARCHAR(255) PRIMARY KEY,
                    HistoryID VARCHAR(255),
                    ContentID VARCHAR(255),
                    TrackNo INTEGER
                );
            """)

            conn.exec_driver_sql("""
                CREATE TABLE djmdCue (
                    ID VARCHAR(255) PRIMARY KEY,
                    ContentID VARCHAR(255),
                    InMsec INTEGER,
                    Kind INTEGER,
                    Comment VARCHAR(255)
                );
            """)

            conn.exec_driver_sql("""
                CREATE TABLE djmdSongPlaylist (
                    ID VARCHAR(255) PRIMARY KEY,
                    PlaylistID VARCHAR(255),
                    ContentID VARCHAR(255),
                    TrackNo INTEGER
                );
            """)

            conn.exec_driver_sql("""
                CREATE TABLE djmdSongMyTag (
                    ID VARCHAR(255) PRIMARY KEY,
                    MyTagID VARCHAR(255),
                    ContentID VARCHAR(255),
                    TrackNo INTEGER
                );
            """)

        # Create dummy old MP3 file on disk
        self.old_mp3_path = os.path.join(self.temp_dir, "OldSong.mp3")
        with open(self.old_mp3_path, "wb") as f:
            f.write(b"ID3" + b"\x00" * 1024)

        # Create dummy new AIFF file on disk
        self.new_aiff_path = os.path.join(self.temp_dir, "NewSong.aiff")
        with open(self.new_aiff_path, "wb") as f:
            f.write(b"FORM" + b"\x00\x00\x08\x00" + b"AIFF" + b"\x00" * 2048)

        # Insert test track: 10 plays, StockDate 2024-01-15
        with self.engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO djmdContent (
                        ID, FolderPath, FileNameL, FileNameS, Title, Length, BitRate, BitDepth,
                        SampleRate, FileSize, FileType, DJPlayCount, StockDate, AnalysisDataPath,
                        TrackInfoUpdated, rb_local_deleted, created_at, updated_at
                    ) VALUES (
                        'track-100', :folder_path, 'OldSong.mp3', '', 'Classic Track', 240, 320, 16,
                        44100, 1024, 1, 10, '2024-01-15', '/PIONEER/ANLZ/ANLZ0000.DAT',
                        '1', 0, '2024-01-15 12:00:00', '2024-01-15 12:00:00'
                    );
                """),
                {"folder_path": self.old_mp3_path},
            )

            # Add 3 history session plays
            for i in range(1, 4):
                conn.execute(
                    text("""
                        INSERT INTO djmdSongHistory (ID, HistoryID, ContentID, TrackNo)
                        VALUES (:id, :hist_id, 'track-100', :track_no);
                    """),
                    {"id": f"sh-{i}", "hist_id": f"hist-{i}", "track_no": i},
                )

            # Add 2 cue points
            conn.execute(
                text("""
                    INSERT INTO djmdCue (ID, ContentID, InMsec, Kind, Comment)
                    VALUES ('cue-1', 'track-100', 15000, 1, 'Intro'),
                           ('cue-2', 'track-100', 45000, 2, 'Drop');
                """)
            )

            # Add 2 playlists
            conn.execute(
                text("""
                    INSERT INTO djmdSongPlaylist (ID, PlaylistID, ContentID, TrackNo)
                    VALUES ('sp-1', 'pl-afro', 'track-100', 1),
                           ('sp-2', 'pl-favorites', 'track-100', 5);
                """)
            )

            # Add 1 tag
            conn.execute(
                text("""
                    INSERT INTO djmdSongMyTag (ID, MyTagID, ContentID, TrackNo)
                    VALUES ('st-1', 'tag-peak-time', 'track-100', 1);
                """)
            )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_inspect_audio_file(self):
        """Verify inspect_audio_file extracts format, extension, size, and handles errors."""
        info = inspect_audio_file(self.new_aiff_path)
        self.assertEqual(info["file_type"], 12)  # AIFF
        self.assertEqual(info["format_name"], "AIFF")
        self.assertEqual(info["extension"], "aiff")
        self.assertGreater(info["file_size"], 0)

        with self.assertRaises(FileNotFoundError):
            inspect_audio_file("/non/existent/path.aiff")

    def test_replace_track_audio_file_preserves_everything(self):
        """Verify audio file replacement preserves ID, play count, date added, history, cues, and playlists."""
        res = replace_track_audio_file(
            self.engine,
            "track-100",
            self.new_aiff_path,
            options={"old_file_action": "keep"},
        )

        self.assertTrue(res["success"])
        self.assertEqual(res["content_id"], "track-100")
        self.assertEqual(res["play_count"], 10)  # Preserved!
        self.assertEqual(res["stock_date"], "2024-01-15")  # Preserved!
        self.assertEqual(res["history_plays_preserved"], 3)  # Preserved!
        self.assertEqual(res["cues_preserved"], 2)  # Preserved!
        self.assertEqual(res["playlists_preserved"], 2)  # Preserved!

        # Verify database record
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM djmdContent WHERE ID = 'track-100';")
            ).mappings().fetchone()

            self.assertEqual(row["FolderPath"], self.new_aiff_path)
            self.assertEqual(row["FileNameL"], "NewSong.aiff")
            self.assertEqual(row["FileType"], 12)  # Upgraded to AIFF
            self.assertEqual(row["DJPlayCount"], 10)  # Preserved
            self.assertEqual(row["StockDate"], "2024-01-15")  # Preserved
            self.assertEqual(row["AnalysisDataPath"], "")  # Reset for fresh Rekordbox analysis
            self.assertEqual(row["AnalysisUpdated"], "0")  # Flagged for fresh analysis
            self.assertEqual(row["TrackInfoUpdated"], "2")  # Incremented

            # Verify history sessions still point to track-100
            h_count = conn.execute(
                text("SELECT count(*) FROM djmdSongHistory WHERE ContentID = 'track-100';")
            ).fetchone()[0]
            self.assertEqual(h_count, 3)

            # Verify cues still point to track-100
            c_count = conn.execute(
                text("SELECT count(*) FROM djmdCue WHERE ContentID = 'track-100';")
            ).fetchone()[0]
            self.assertEqual(c_count, 2)

            # Verify playlists still point to track-100
            p_count = conn.execute(
                text("SELECT count(*) FROM djmdSongPlaylist WHERE ContentID = 'track-100';")
            ).fetchone()[0]
            self.assertEqual(p_count, 2)

    def test_replace_track_old_file_backup_action(self):
        """Verify that choosing 'backup' renames the old file to .bak."""
        res = replace_track_audio_file(
            self.engine,
            "track-100",
            self.new_aiff_path,
            options={"old_file_action": "backup"},
        )
        self.assertTrue(res["success"])
        self.assertFalse(os.path.exists(self.old_mp3_path))
        self.assertTrue(os.path.exists(f"{self.old_mp3_path}.bak"))

    def test_merge_imported_tracks(self):
        """Verify merging old track into a newly imported track transfers history, plays, and stock date."""
        # Insert target track (newly imported AIFF, 0 plays, added today)
        with self.engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO djmdContent (
                        ID, FolderPath, FileNameL, FileNameS, Title, Length, BitRate, BitDepth,
                        SampleRate, FileSize, FileType, DJPlayCount, StockDate, rb_local_deleted,
                        created_at, updated_at
                    ) VALUES (
                        'track-200', '/path/to/imported.aiff', 'imported.aiff', '', 'Classic Track (AIFF)',
                        240, 1411, 16, 44100, 4096, 12, 0, '2026-09-20', 0,
                        '2026-09-20 10:00:00', '2026-09-20 10:00:00'
                    );
                """)
            )

        res = merge_imported_tracks(
            self.engine,
            source_content_id="track-100",
            target_content_id="track-200",
            options={"old_file_action": "keep"},
        )

        self.assertTrue(res["success"])
        self.assertEqual(res["play_count_transferred"], 10)
        self.assertEqual(res["stock_date_transferred"], "2024-01-15")
        self.assertEqual(res["history_transferred"], 3)
        self.assertEqual(res["playlists_transferred"], 2)

        with self.engine.connect() as conn:
            tgt = conn.execute(
                text("SELECT * FROM djmdContent WHERE ID = 'track-200';")
            ).mappings().fetchone()
            self.assertEqual(tgt["DJPlayCount"], 10)  # Transferred
            self.assertEqual(tgt["StockDate"], "2024-01-15")  # Transferred

            # Verify history sessions now point to track-200
            h_count = conn.execute(
                text("SELECT count(*) FROM djmdSongHistory WHERE ContentID = 'track-200';")
            ).fetchone()[0]
            self.assertEqual(h_count, 3)

            # Verify source track soft-deleted
            src = conn.execute(
                text("SELECT rb_local_deleted FROM djmdContent WHERE ID = 'track-100';")
            ).fetchone()
            self.assertEqual(src[0], 1)

    def test_replace_track_rejects_unsupported_source(self):
        """Verify that replacing a non-MP3 and non-AIFF track (e.g. WAV or FLAC) raises ValueError."""
        with self.engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO djmdContent (
                        ID, FolderPath, FileNameL, FileNameS, Title, Length, BitRate, BitDepth,
                        SampleRate, FileSize, FileType, DJPlayCount, StockDate, rb_local_deleted
                    ) VALUES (
                        'track-wav', '/path/to/old.wav', 'old.wav', '', 'WAV Track',
                        240, 1411, 16, 44100, 4096, 11, 5, '2024-01-01', 0
                    );
                """)
            )

        with self.assertRaises(ValueError) as ctx:
            replace_track_audio_file(self.engine, "track-wav", self.new_aiff_path)
        self.assertIn("Only MP3 and AIFF tracks can be replaced", str(ctx.exception))

    def test_replace_track_aiff_to_aiff_preserves_everything(self):
        """Verify replacing an existing AIFF track with another AIFF preserves all metadata and relations."""
        old_aiff_path = os.path.join(self.temp_dir, "OriginalSong.aiff")
        with open(old_aiff_path, "wb") as f:
            f.write(b"FORM" + b"\x00\x00\x04\x00" + b"AIFF" + b"\x00" * 1024)

        updated_aiff_path = os.path.join(self.temp_dir, "RemasteredSong.aiff")
        with open(updated_aiff_path, "wb") as f:
            f.write(b"FORM" + b"\x00\x00\x08\x00" + b"AIFF" + b"\x00" * 2048)

        with self.engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO djmdContent (
                        ID, FolderPath, FileNameL, FileNameS, Title, Length, BitRate, BitDepth,
                        SampleRate, FileSize, FileType, DJPlayCount, StockDate, AnalysisDataPath,
                        TrackInfoUpdated, rb_local_deleted, created_at, updated_at
                    ) VALUES (
                        'track-aiff-1', :folder_path, 'OriginalSong.aiff', '', 'Hi-Res Anthem',
                        300, 1411, 16, 44100, 2048, 12, 25, '2023-06-01', '/PIONEER/ANLZ/OLD.DAT',
                        '1', 0, '2023-06-01 10:00:00', '2023-06-01 10:00:00'
                    );
                """),
                {"folder_path": old_aiff_path},
            )

            conn.execute(
                text("""
                    INSERT INTO djmdSongHistory (ID, HistoryID, ContentID, TrackNo)
                    VALUES ('sh-aiff-1', 'hist-1', 'track-aiff-1', 1);
                """)
            )
            conn.execute(
                text("""
                    INSERT INTO djmdCue (ID, ContentID, InMsec, Kind, Comment)
                    VALUES ('cue-aiff-1', 'track-aiff-1', 12000, 1, 'Intro');
                """)
            )
            conn.execute(
                text("""
                    INSERT INTO djmdSongPlaylist (ID, PlaylistID, ContentID, TrackNo)
                    VALUES ('sp-aiff-1', 'pl-1', 'track-aiff-1', 1);
                """)
            )

        res = replace_track_audio_file(
            self.engine,
            "track-aiff-1",
            updated_aiff_path,
            options={"old_file_action": "keep"},
        )

        self.assertTrue(res["success"])
        self.assertEqual(res["content_id"], "track-aiff-1")
        self.assertEqual(res["play_count"], 25)
        self.assertEqual(res["stock_date"], "2023-06-01")
        self.assertEqual(res["history_plays_preserved"], 1)
        self.assertEqual(res["cues_preserved"], 1)
        self.assertEqual(res["playlists_preserved"], 1)
        self.assertEqual(res["new_file"]["format"], "AIFF")

        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM djmdContent WHERE ID = 'track-aiff-1';")
            ).mappings().fetchone()
            self.assertEqual(row["FolderPath"], updated_aiff_path)
            self.assertEqual(row["FileNameL"], "RemasteredSong.aiff")
            self.assertEqual(row["FileType"], 12)
            self.assertEqual(row["DJPlayCount"], 25)
            self.assertEqual(row["StockDate"], "2023-06-01")
            self.assertEqual(row["AnalysisDataPath"], "")

    def test_replace_track_rejects_identical_file(self):
        """Verify that replacing a track with its own existing file path raises ValueError."""
        with self.engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO djmdContent (
                        ID, FolderPath, FileNameL, FileNameS, Title, Length, BitRate, BitDepth,
                        SampleRate, FileSize, FileType, DJPlayCount, StockDate, rb_local_deleted
                    ) VALUES (
                        'track-aiff-same', :folder_path, 'NewSong.aiff', '', 'Same Track',
                        240, 1411, 16, 44100, 2048, 12, 5, '2024-01-01', 0
                    );
                """),
                {"folder_path": self.new_aiff_path},
            )

        with self.assertRaises(ValueError) as ctx:
            replace_track_audio_file(self.engine, "track-aiff-same", self.new_aiff_path)
        self.assertIn("identical to the current track's audio file path", str(ctx.exception))

    def test_replace_track_rejects_non_aiff_replacement(self):
        """Verify that passing a non-AIFF replacement file raises ValueError."""
        flac_path = os.path.join(self.temp_dir, "test.flac")
        with open(flac_path, "wb") as f:
            f.write(b"fLaC" + b"\x00" * 100)

        with self.assertRaises(ValueError) as ctx:
            replace_track_audio_file(self.engine, "track-100", flac_path)
        self.assertIn("Currently only AIFF files", str(ctx.exception))

    def test_merge_rejects_invalid_formats(self):
        """Verify merge raises ValueError if source is not MP3 or AIFF, or target is not AIFF."""
        with self.engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO djmdContent (
                        ID, FolderPath, FileNameL, FileNameS, Title, Length, BitRate, BitDepth,
                        SampleRate, FileSize, FileType, DJPlayCount, StockDate, rb_local_deleted
                    ) VALUES (
                        'track-wav-src', '/path/to/old.wav', 'old.wav', '', 'WAV Source',
                        240, 1411, 16, 44100, 4096, 11, 5, '2024-01-01', 0
                    ), (
                        'track-mp3-tgt', '/path/to/new.mp3', 'new.mp3', '', 'MP3 Target',
                        240, 320, 16, 44100, 1024, 1, 0, '2026-09-20', 0
                    );
                """)
            )

        # Non-MP3 / Non-AIFF source
        with self.assertRaises(ValueError) as ctx:
            merge_imported_tracks(self.engine, "track-wav-src", "track-100")
        self.assertIn("Source track must be an MP3 or AIFF file", str(ctx.exception))

    def test_merge_imported_tracks_aiff_to_aiff(self):
        """Verify merging an existing AIFF track into a new AIFF track transfers metadata cleanly."""
        with self.engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO djmdContent (
                        ID, FolderPath, FileNameL, FileNameS, Title, Length, BitRate, BitDepth,
                        SampleRate, FileSize, FileType, DJPlayCount, StockDate, rb_local_deleted,
                        created_at, updated_at
                    ) VALUES (
                        'track-aiff-src', '/path/to/old.aiff', 'old.aiff', '', 'AIFF Source',
                        240, 1411, 16, 44100, 4096, 12, 18, '2023-01-10', 0,
                        '2023-01-10 10:00:00', '2023-01-10 10:00:00'
                    ), (
                        'track-aiff-tgt', '/path/to/new.aiff', 'new.aiff', '', 'AIFF Target',
                        240, 1411, 24, 48000, 8192, 12, 0, '2026-09-25', 0,
                        '2026-09-25 10:00:00', '2026-09-25 10:00:00'
                    );
                """)
            )

        res = merge_imported_tracks(
            self.engine,
            source_content_id="track-aiff-src",
            target_content_id="track-aiff-tgt",
            options={"old_file_action": "keep"},
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["play_count_transferred"], 18)
        self.assertEqual(res["stock_date_transferred"], "2023-01-10")


class TestReplaceFileGUI(unittest.TestCase):
    """Test ReplaceFileView GUI components and user flows."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.engine = create_engine("sqlite:///:memory:")

        with self.engine.begin() as conn:
            conn.exec_driver_sql("""
                CREATE TABLE djmdContent (
                    ID VARCHAR(255) PRIMARY KEY,
                    FolderPath VARCHAR(255),
                    FileNameL VARCHAR(255),
                    Title VARCHAR(255),
                    Length INTEGER,
                    BitRate INTEGER,
                    FileSize INTEGER,
                    FileType INTEGER,
                    DJPlayCount INTEGER,
                    StockDate VARCHAR(255),
                    rb_local_deleted SMALLINT DEFAULT 0
                );
            """)

        self.view = ReplaceFileView()
        self.view.set_engine(self.engine)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        self.view.deleteLater()

    def test_gui_mode_switching(self):
        """Verify tab switching between Direct File mode and Merge mode."""
        self.assertEqual(self.view.stacked_modes.currentIndex(), 0)

        # Switch to Merge mode
        self.view.btn_mode_merge.click()
        self.assertEqual(self.view.stacked_modes.currentIndex(), 1)

        # Switch back to Direct File mode
        self.view.btn_mode_file.click()
        self.assertEqual(self.view.stacked_modes.currentIndex(), 0)

    def test_preselect_track(self):
        """Verify preselecting a track populates summary card and activates comparison."""
        sample_track = {
            "id": "trk-1",
            "content_id": "trk-1",
            "title": "Solaris",
            "artist": "Argy",
            "file_type": "MP3",
            "bitrate": 320,
            "play_count": 7,
            "date_added": "2024-05-10",
            "duration_sec": 300,
            "duration_str": "05:00",
            "file_path": "/Music/Solaris.mp3",
            "file_size": 12000000,
        }

        self.view.preselect_track(sample_track)
        self.assertEqual(self.view.mode1_track_title.text(), "Solaris")
        self.assertEqual(self.view.mode1_track_artist.text(), "Argy")
        self.assertIn("7 plays", self.view.mode1_plays_chip.text())
        self.assertIn("2024-05-10", self.view.mode1_added_chip.text())
        self.assertEqual(self.view.mode1_results_table.rowCount(), 1)
        self.assertEqual(self.view.mode1_results_table.item(0, 0).text(), "Solaris")

        # Verify Mode 2 (Merge Mode) is also pre-populated
        self.assertIsNotNone(self.view.merge_source_track)
        self.assertEqual(self.view.merge_source_track["id"], "trk-1")
        self.assertEqual(self.view.merge_src_table.rowCount(), 1)
        self.assertEqual(self.view.merge_src_table.item(0, 0).text(), "Solaris")
        self.assertIn("Solaris", self.view.merge_src_summary.text())
        self.assertIn("7 plays", self.view.merge_src_summary.text())
        self.assertEqual(self.view.merge_tgt_input.text(), "Solaris")

    def test_preselect_track_and_switch_to_merge_mode(self):
        """Verify switching to Mode 2 displays pre-populated track without needing DB search."""
        sample_track = {
            "id": "trk-1",
            "content_id": "trk-1",
            "title": "Solaris",
            "artist": "Argy",
            "file_type": "MP3",
            "bitrate": 320,
            "play_count": 7,
            "date_added": "2024-05-10",
            "duration_sec": 300,
            "duration_str": "05:00",
            "file_path": "/Music/Solaris.mp3",
            "file_size": 12000000,
        }
        self.view.preselect_track(sample_track)
        self.assertEqual(self.view.stacked_modes.currentIndex(), 0)

        # Switch to Merge mode tab
        self.view.btn_mode_merge.click()
        self.assertEqual(self.view.stacked_modes.currentIndex(), 1)

        # Ensure Mode 2 still has source track pre-selected
        self.assertEqual(self.view.merge_source_track["id"], "trk-1")
        self.assertEqual(self.view.merge_src_table.rowCount(), 1)
        self.assertEqual(self.view.merge_src_table.currentRow(), 0)
        self.assertIn("Solaris", self.view.merge_src_summary.text())
        self.assertIn("7 plays", self.view.merge_src_summary.text())

    def test_audio_inspect_and_timing_match(self):
        """Verify comparison diff updates with duration match when replacement file inspected."""
        sample_track = {
            "id": "trk-1",
            "title": "Solaris",
            "duration_sec": 300,
            "duration_str": "05:00",
            "file_type": "MP3",
        }
        self.view.preselect_track(sample_track)

        dummy_info = {
            "format_name": "AIFF",
            "file_name": "Solaris.aiff",
            "file_path": "/Music/Solaris.aiff",
            "file_type": 12,
            "bitrate": 1411,
            "sample_rate": 44100,
            "bit_depth": 16,
            "duration_sec": 300,
            "duration_str": "05:00",
            "file_size": 52000000,
        }

        self.view.replacement_file_path = dummy_info["file_path"]
        self.view._on_audio_inspected(dummy_info)
        self.assertIn("Perfect Timing Match", self.view.duration_match_banner.text())
        self.assertTrue(self.view.btn_execute_replace.isEnabled())

    def test_gui_preselect_rejects_unsupported_format(self):
        """Verify preselecting an unsupported track (e.g. WAV) is rejected."""
        wav_track = {
            "id": "trk-wav",
            "title": "WAV Anthem",
            "artist": "Producer",
            "file_type": "WAV",
            "file_path": "/Music/Anthem.wav",
        }
        with patch("PySide6.QtWidgets.QMessageBox.warning") as mock_warn:
            self.view.preselect_track(wav_track)
            mock_warn.assert_called_once()
            self.assertIsNone(self.view.selected_track)

    def test_gui_preselect_accepts_aiff(self):
        """Verify preselecting an AIFF track successfully populates UI for AIFF to AIFF replacement."""
        aiff_track = {
            "id": "trk-aiff",
            "content_id": "trk-aiff",
            "title": "Lossless Master",
            "artist": "Audiophile",
            "file_type": "AIFF",
            "bitrate": 1411,
            "play_count": 14,
            "date_added": "2024-08-01",
            "duration_sec": 360,
            "duration_str": "06:00",
            "file_path": "/Music/Master.aiff",
            "file_size": 60000000,
        }
        self.view.preselect_track(aiff_track)
        self.assertEqual(self.view.mode1_track_title.text(), "Lossless Master")
        self.assertEqual(self.view.mode1_track_artist.text(), "Audiophile")
        self.assertIn("14 plays", self.view.mode1_plays_chip.text())
        self.assertIn("AIFF", self.view.mode1_format_chip.text())
        self.assertEqual(self.view.selected_track["id"], "trk-aiff")

    def test_gui_rejects_identical_replacement_file(self):
        """Verify selecting the same file as current track shows warning and does not proceed."""
        sample_track = {
            "id": "trk-aiff",
            "title": "Master",
            "file_type": "AIFF",
            "file_path": "/Music/Same.aiff",
        }
        self.view.selected_track = sample_track
        with patch("PySide6.QtWidgets.QMessageBox.warning") as mock_warn:
            self.view._on_new_file_selected("/Music/Same.aiff")
            mock_warn.assert_called_once()
            self.assertIsNone(self.view.replacement_file_path)


if __name__ == "__main__":
    unittest.main()
