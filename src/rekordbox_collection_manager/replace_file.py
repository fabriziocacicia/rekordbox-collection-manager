"""Replace File Tool for Rekordbox Collection Manager.

Enables upgrading collection MP3 tracks to AIFF while preserving 100% of
history session entries, play counts, original "Date Added" (StockDate),
cue points, playlists, and tags.
"""

import argparse
import math
import os
import shutil
import struct
import subprocess
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Engine

from rekordbox_collection_manager.utils import (
    ensure_rekordbox_not_running,
    get_rekordbox_timestamp,
    is_rekordbox_running,
    open_database,
    resolve_db_path,
    resolve_file_type,
)


# Rekordbox FileType mappings
EXTENSION_TO_FILE_TYPE: Dict[str, int] = {
    "MP3": 1,
    "M4A": 4,
    "AAC": 4,
    "FLAC": 5,
    "WAV": 11,
    "WAVE": 11,
    "AIF": 12,
    "AIFF": 12,
}

FILE_TYPE_TO_NAME: Dict[int, str] = {
    1: "MP3",
    4: "M4A",
    5: "FLAC",
    11: "WAV",
    12: "AIFF",
}


def inspect_audio_file(file_path: str) -> Dict[str, Any]:
    """Extract audio technical specifications and embedded metadata from an audio file.

    Supports AIFF, WAV, FLAC, MP3, and M4A. Uses mutagen with fallback to raw audio
    chunk readers.

    Returns a rich dictionary:
      - file_path, file_name, file_size
      - file_type (int code for Rekordbox)
      - format_name (e.g. 'AIFF', 'MP3')
      - sample_rate (int, Hz)
      - bit_depth (int, bits per sample)
      - bitrate (int, kbps)
      - duration_sec (int)
      - duration_str (str "MM:SS")
      - channels (int)
      - title, artist, album, genre, year (extracted from ID3 / metadata tags)
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Audio file not found: {file_path}")

    ext = os.path.splitext(file_path)[1].lstrip(".").upper()
    file_size = os.path.getsize(file_path)
    file_name = os.path.basename(file_path)
    file_type_code = EXTENSION_TO_FILE_TYPE.get(ext, 1)
    format_name = FILE_TYPE_TO_NAME.get(file_type_code, ext or "Audio")

    info: Dict[str, Any] = {
        "file_path": os.path.abspath(file_path),
        "file_name": file_name,
        "extension": ext.lower(),
        "file_size": file_size,
        "file_type": file_type_code,
        "format_name": format_name,
        "sample_rate": 44100,
        "bit_depth": 16,
        "bitrate": 320,
        "duration_sec": 0,
        "duration_str": "--:--",
        "channels": 2,
        "title": "",
        "artist": "",
        "album": "",
        "genre": "",
        "year": 0,
    }

    # 1. Primary: Use mutagen
    try:
        import mutagen

        audio = mutagen.File(file_path)
        if audio is not None:
            if hasattr(audio, "info") and audio.info is not None:
                ainfo = audio.info
                # Sample rate
                if hasattr(ainfo, "sample_rate") and ainfo.sample_rate:
                    info["sample_rate"] = int(ainfo.sample_rate)
                # Bit depth
                if hasattr(ainfo, "bits_per_sample") and ainfo.bits_per_sample:
                    info["bit_depth"] = int(ainfo.bits_per_sample)
                # Channels
                if hasattr(ainfo, "channels") and ainfo.channels:
                    info["channels"] = int(ainfo.channels)
                # Length
                if hasattr(ainfo, "length") and ainfo.length:
                    sec = int(round(ainfo.length))
                    info["duration_sec"] = sec
                    mins = sec // 60
                    s = sec % 60
                    info["duration_str"] = f"{mins}:{s:02d}"
                # Bitrate
                if hasattr(ainfo, "bitrate") and ainfo.bitrate:
                    raw_br = ainfo.bitrate
                    # mutagen AIFF/FLAC may return bps (e.g. 1411200) instead of kbps
                    if raw_br > 10000:
                        info["bitrate"] = int(round(raw_br / 1000.0))
                    else:
                        info["bitrate"] = int(raw_br)

            # Extract tags
            tags = getattr(audio, "tags", None)
            if tags:
                # Standard ID3 or Vorbis or MP4 tags
                def get_tag_val(keys: List[str]) -> str:
                    for k in keys:
                        if k in tags:
                            val = tags[k]
                            if isinstance(val, list) and len(val) > 0:
                                return str(val[0]).strip()
                            if hasattr(val, "text") and len(val.text) > 0:
                                return str(val.text[0]).strip()
                            return str(val).strip()
                    return ""

                info["title"] = get_tag_val(["TIT2", "title", "\xa9nam"])
                info["artist"] = get_tag_val(["TPE1", "artist", "\xa9ART"])
                info["album"] = get_tag_val(["TALB", "album", "\xa9alb"])
                info["genre"] = get_tag_val(["TCON", "genre", "\xa9gen"])
                raw_year = get_tag_val(["TDRC", "date", "\xa9day"])
                if raw_year and len(raw_year) >= 4 and raw_year[:4].isdigit():
                    info["year"] = int(raw_year[:4])

    except Exception:
        pass

    # 2. Refine bitrate for uncompressed audio (AIFF/WAV) if missing or default
    if ext in ("AIFF", "AIF", "WAV", "WAVE"):
        if info["sample_rate"] > 0 and info["bit_depth"] > 0 and info["channels"] > 0:
            calc_kbps = int(round((info["sample_rate"] * info["bit_depth"] * info["channels"]) / 1000.0))
            if info["bitrate"] <= 320 or abs(info["bitrate"] - calc_kbps) > 100:
                info["bitrate"] = calc_kbps

    # 3. Fallback AIFF binary header inspection if duration was not resolved
    if ext in ("AIFF", "AIF") and info["duration_sec"] == 0:
        try:
            with open(file_path, "rb") as f:
                header = f.read(12)
                if header[:4] == b"FORM" and header[8:12] in (b"AIFF", b"AIFC"):
                    while True:
                        chunk_hdr = f.read(8)
                        if len(chunk_hdr) < 8:
                            break
                        cid = chunk_hdr[:4]
                        csize = struct.unpack(">I", chunk_hdr[4:8])[0]
                        if cid == b"COMM":
                            comm = f.read(min(csize, 26))
                            ch = struct.unpack(">h", comm[0:2])[0]
                            num_frames = struct.unpack(">I", comm[2:6])[0]
                            sample_size = struct.unpack(">h", comm[6:8])[0]
                            exp = struct.unpack(">H", comm[8:10])[0]
                            mant = struct.unpack(">Q", comm[10:18])[0]
                            if exp == 0 and mant == 0:
                                srate = 0
                            else:
                                srate = int(mant * (2 ** (exp - 16383 - 63)))
                            if srate > 0:
                                info["sample_rate"] = srate
                                info["bit_depth"] = sample_size
                                info["channels"] = ch
                                info["duration_sec"] = int(num_frames / srate)
                                mins = info["duration_sec"] // 60
                                s = info["duration_sec"] % 60
                                info["duration_str"] = f"{mins}:{s:02d}"
                                info["bitrate"] = int(round((srate * sample_size * ch) / 1000.0))
                            break
                        else:
                            f.seek(csize + (csize % 2), 1)
        except Exception:
            pass

    return info


def trash_file_macos(file_path: str) -> bool:
    """Safely move a file to macOS Finder Trash, or fallback to renaming with .bak extension."""
    if not os.path.exists(file_path):
        return False

    # 1. Primary macOS: AppleScript via Finder (moves to Trash can safely)
    if sys.platform == "darwin":
        try:
            abs_p = os.path.abspath(file_path).replace('"', '\\"')
            cmd = f'tell application "Finder" to delete POSIX file "{abs_p}"'
            res = subprocess.run(["osascript", "-e", cmd], capture_output=True, text=True, check=False)
            if res.returncode == 0:
                return True
        except Exception:
            pass

    # 2. Cross-platform fallback: Rename to .bak to avoid permanent deletion
    try:
        bak_path = f"{file_path}.bak"
        counter = 1
        while os.path.exists(bak_path):
            bak_path = f"{file_path}.bak{counter}"
            counter += 1
        os.rename(file_path, bak_path)
        return True
    except Exception:
        return False


def replace_track_audio_file(
    engine: Engine,
    content_id: str,
    new_file_path: str,
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Replace an existing collection track's audio file in-place in Rekordbox master.db.

    Preserves 100% of:
      - Track ID
      - DJPlayCount (Play Count)
      - StockDate (original "Date Added")
      - All djmdSongHistory records (every session played)
      - All djmdCue records (Hot Cues A-H & Memory Cues)
      - All djmdSongPlaylist records (Playlists)
      - All djmdSongMyTag records (My Tags)
      - Rating, Comments, ColorID, Artwork (ImagePath)

    Updates in djmdContent:
      - FolderPath (full absolute path to new audio file)
      - FileNameL (new file name)
      - FileNameS ("")
      - FileType (1: MP3, 4: M4A, 5: FLAC, 11: WAV, 12: AIFF)
      - FileSize (new file size in bytes)
      - BitRate (new audio bitrate in kbps)
      - BitDepth (16 or 24 bit)
      - SampleRate (44100 or 48000 Hz)
      - Length (duration in seconds)
      - updated_at (current UTC timestamp)
      - TrackInfoUpdated (incremented counter)

    Options:
      - old_file_action: 'keep' (default), 'trash', or 'backup'
      - update_id3_metadata: bool = False (if True, updates Title/Artist from new file)
      - move_new_file_to_track_folder: bool = False (if True, moves new file into track's folder)
    """
    opts = options or {}
    old_file_action = opts.get("old_file_action", "keep")
    update_id3_metadata = opts.get("update_id3_metadata", False)
    move_new_file_to_track_folder = opts.get("move_new_file_to_track_folder", False)

    resolved_new_path = os.path.abspath(new_file_path)
    if not os.path.exists(resolved_new_path):
        raise FileNotFoundError(f"Replacement audio file does not exist: {resolved_new_path}")

    # Inspect replacement file specs
    new_info = inspect_audio_file(resolved_new_path)
    now_utc = get_rekordbox_timestamp()

    # Enforce: Replacement file must be AIFF
    new_ext = os.path.splitext(resolved_new_path)[1].lower()
    if new_ext not in (".aiff", ".aif") or new_info["file_type"] != 12:
        raise ValueError(
            f"Unsupported replacement file format '{new_info['format_name']}'. "
            "Currently only AIFF files (.aiff, .aif) are supported."
        )

    with engine.begin() as conn:
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdContent);").fetchall()}

        # 1. Fetch current track record
        select_sql = "SELECT * FROM djmdContent WHERE ID = :id;"
        row = conn.execute(text(select_sql), {"id": str(content_id)}).mappings().fetchone()
        if not row:
            raise ValueError(f"Track with ID '{content_id}' not found in djmdContent.")

        old_track = dict(row)
        old_file_path = old_track.get("FolderPath") or ""
        old_file_name = old_track.get("FileNameL") or ""
        old_file_type = old_track.get("FileType")

        # Enforce: Source track must be MP3
        old_ext = os.path.splitext(old_file_name or old_file_path)[1].lower()
        if old_file_type != 1 and old_ext != ".mp3":
            old_fmt = resolve_file_type(old_file_type, old_file_name, old_file_path)
            raise ValueError(
                f"Only MP3 tracks can be replaced. The selected track is {old_fmt}."
            )

        old_bitrate = old_track.get("BitRate")
        old_size = old_track.get("FileSize")
        old_length = old_track.get("Length")
        old_play_count = int(old_track.get("DJPlayCount") or 0)
        old_stock_date = str(old_track.get("StockDate") or "")

        # Optional: Move new file into track folder
        target_dest_path = resolved_new_path
        if move_new_file_to_track_folder and old_file_path:
            old_dir = os.path.dirname(old_file_path)
            if os.path.isdir(old_dir) and old_dir != os.path.dirname(resolved_new_path):
                dest_candidate = os.path.join(old_dir, new_info["file_name"])
                if dest_candidate != resolved_new_path:
                    shutil.copy2(resolved_new_path, dest_candidate)
                    target_dest_path = dest_candidate
                    new_info["file_path"] = target_dest_path
                    new_info["file_name"] = os.path.basename(target_dest_path)

        # 2. Build UPDATE statement for djmdContent
        update_clauses = [
            "FolderPath = :new_folder_path",
            "FileNameL = :new_file_name",
            "FileNameS = :new_file_name_s",
            "FileType = :new_file_type",
            "FileSize = :new_file_size",
            "BitRate = :new_bitrate",
            "BitDepth = :new_bit_depth",
            "SampleRate = :new_sample_rate",
            "updated_at = :updated_at",
        ]
        params: Dict[str, Any] = {
            "id": str(content_id),
            "new_folder_path": target_dest_path,
            "new_file_name": new_info["file_name"],
            "new_file_name_s": "",
            "new_file_type": new_info["file_type"],
            "new_file_size": new_info["file_size"],
            "new_bitrate": new_info["bitrate"],
            "new_bit_depth": new_info["bit_depth"],
            "new_sample_rate": new_info["sample_rate"],
            "updated_at": now_utc,
        }

        # Update Length only if positive duration extracted
        if new_info["duration_sec"] > 0 and "Length" in cols:
            update_clauses.append("Length = :new_length")
            params["new_length"] = new_info["duration_sec"]

        # Track info updated counter
        if "TrackInfoUpdated" in cols:
            curr_val = old_track.get("TrackInfoUpdated")
            try:
                next_val = str(int(curr_val) + 1) if curr_val and str(curr_val).isdigit() else "1"
            except Exception:
                next_val = "1"
            update_clauses.append("TrackInfoUpdated = :track_info_updated")
            params["track_info_updated"] = next_val

        # Reset analysis so Rekordbox cleanly calculates a fresh beatgrid and waveform for the new audio file
        if "AnalysisUpdated" in cols:
            update_clauses.append("AnalysisUpdated = '0'")
        if "Analysed" in cols:
            update_clauses.append("Analysed = 0")
        if "AnalysisDataPath" in cols:
            update_clauses.append("AnalysisDataPath = ''")

        # Optional: ID3 tag updates
        if update_id3_metadata:
            if new_info.get("title") and "Title" in cols:
                update_clauses.append("Title = :new_title")
                params["new_title"] = new_info["title"]

        update_sql = f"UPDATE djmdContent SET {', '.join(update_clauses)} WHERE ID = :id;"
        conn.execute(text(update_sql), params)

        # 3. Query preserved relational metrics
        history_count = 0
        try:
            h_row = conn.execute(
                text("SELECT count(*) FROM djmdSongHistory WHERE ContentID = :id;"),
                {"id": str(content_id)},
            ).fetchone()
            history_count = int(h_row[0]) if h_row else 0
        except Exception:
            pass

        cue_count = 0
        try:
            c_row = conn.execute(
                text("SELECT count(*) FROM djmdCue WHERE ContentID = :id;"),
                {"id": str(content_id)},
            ).fetchone()
            cue_count = int(c_row[0]) if c_row else 0
        except Exception:
            pass

        playlist_count = 0
        try:
            p_row = conn.execute(
                text("SELECT count(*) FROM djmdSongPlaylist WHERE ContentID = :id;"),
                {"id": str(content_id)},
            ).fetchone()
            playlist_count = int(p_row[0]) if p_row else 0
        except Exception:
            pass

    # 4. Post-transaction: Handle old file on disk
    old_file_handled = "kept"
    if old_file_path and os.path.exists(old_file_path) and os.path.abspath(old_file_path) != os.path.abspath(target_dest_path):
        if old_file_action == "trash":
            trashed = trash_file_macos(old_file_path)
            old_file_handled = "trashed" if trashed else "failed_trash"
        elif old_file_action == "backup":
            bak_path = f"{old_file_path}.bak"
            try:
                os.rename(old_file_path, bak_path)
                old_file_handled = f"renamed_to_{os.path.basename(bak_path)}"
            except Exception:
                old_file_handled = "failed_rename"

    return {
        "success": True,
        "content_id": str(content_id),
        "title": old_track.get("Title") or "(Untitled)",
        "artist_name": old_track.get("ArtistName") or "",
        "play_count": old_play_count,
        "stock_date": old_stock_date,
        "history_plays_preserved": history_count,
        "cues_preserved": cue_count,
        "playlists_preserved": playlist_count,
        "old_file": {
            "path": old_file_path,
            "filename": old_file_name,
            "format": resolve_file_type(old_file_type, old_file_name, old_file_path),
            "bitrate": old_bitrate,
            "size": old_size,
            "length": old_length,
            "status": old_file_handled,
        },
        "new_file": {
            "path": target_dest_path,
            "filename": new_info["file_name"],
            "format": new_info["format_name"],
            "bitrate": new_info["bitrate"],
            "size": new_info["file_size"],
            "length": new_info["duration_sec"],
        },
    }


def merge_imported_tracks(
    engine: Engine,
    source_content_id: str,
    target_content_id: str,
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Merge an old collection track (with history & play count) into a newly imported track.

    Transfers:
      - DJPlayCount from source -> target
      - StockDate (Date Added) from source -> target
      - All djmdSongHistory records from source -> target
      - All djmdSongPlaylist records from source -> target (avoiding duplicates)
      - All djmdSongMyTag records from source -> target
      - All djmdCue records from source -> target (if target has none)
      - Sets rb_local_deleted = 1 on the old source track (soft delete)
    """
    opts = options or {}
    old_file_action = opts.get("old_file_action", "keep")
    now_utc = get_rekordbox_timestamp()

    with engine.begin() as conn:
        # 1. Fetch source and target records
        src = conn.execute(
            text("SELECT * FROM djmdContent WHERE ID = :id;"),
            {"id": str(source_content_id)},
        ).mappings().fetchone()
        if not src:
            raise ValueError(f"Source track ID '{source_content_id}' not found.")

        tgt = conn.execute(
            text("SELECT * FROM djmdContent WHERE ID = :id;"),
            {"id": str(target_content_id)},
        ).mappings().fetchone()
        if not tgt:
            raise ValueError(f"Target track ID '{target_content_id}' not found.")

        src_dict = dict(src)
        tgt_dict = dict(tgt)

        # Enforce: Source must be MP3 and Target must be AIFF
        src_ext = os.path.splitext(src_dict.get("FileNameL") or src_dict.get("FolderPath") or "")[1].lower()
        if src_dict.get("FileType") != 1 and src_ext != ".mp3":
            src_fmt = resolve_file_type(src_dict.get("FileType"), src_dict.get("FileNameL"), src_dict.get("FolderPath"))
            raise ValueError(f"Source track must be an MP3 file (selected track is {src_fmt}).")

        tgt_ext = os.path.splitext(tgt_dict.get("FileNameL") or tgt_dict.get("FolderPath") or "")[1].lower()
        if tgt_dict.get("FileType") != 12 and tgt_ext not in (".aiff", ".aif"):
            tgt_fmt = resolve_file_type(tgt_dict.get("FileType"), tgt_dict.get("FileNameL"), tgt_dict.get("FolderPath"))
            raise ValueError(f"Target track must be an AIFF file (selected track is {tgt_fmt}).")

        src_plays = int(src_dict.get("DJPlayCount") or 0)
        src_stock_date = str(src_dict.get("StockDate") or "")

        # 2. Transfer play count and StockDate to target
        conn.execute(
            text("""
                UPDATE djmdContent
                SET DJPlayCount = :plays,
                    StockDate = :stock_date,
                    updated_at = :updated_at
                WHERE ID = :target_id;
            """),
            {
                "plays": src_plays,
                "stock_date": src_stock_date,
                "updated_at": now_utc,
                "target_id": str(target_content_id),
            },
        )

        # 3. Transfer history entries
        hist_res = conn.execute(
            text("UPDATE djmdSongHistory SET ContentID = :target_id WHERE ContentID = :source_id;"),
            {"target_id": str(target_content_id), "source_id": str(source_content_id)},
        )
        hist_transferred = hist_res.rowcount or 0

        # 4. Transfer playlists
        tgt_pls = {
            r[0]
            for r in conn.execute(
                text("SELECT PlaylistID FROM djmdSongPlaylist WHERE ContentID = :id;"),
                {"id": str(target_content_id)},
            ).fetchall()
        }
        src_pl_rows = conn.execute(
            text("SELECT ID, PlaylistID FROM djmdSongPlaylist WHERE ContentID = :id;"),
            {"id": str(source_content_id)},
        ).fetchall()
        pl_transferred = 0
        for pl_entry_id, pl_id in src_pl_rows:
            if pl_id not in tgt_pls:
                conn.execute(
                    text("UPDATE djmdSongPlaylist SET ContentID = :target_id WHERE ID = :entry_id;"),
                    {"target_id": str(target_content_id), "entry_id": pl_entry_id},
                )
                tgt_pls.add(pl_id)
                pl_transferred += 1
            else:
                # Target already in playlist; remove duplicate source entry
                conn.execute(
                    text("DELETE FROM djmdSongPlaylist WHERE ID = :entry_id;"),
                    {"entry_id": pl_entry_id},
                )

        # 5. Transfer tags
        tgt_tags = {
            r[0]
            for r in conn.execute(
                text("SELECT MyTagID FROM djmdSongMyTag WHERE ContentID = :id;"),
                {"id": str(target_content_id)},
            ).fetchall()
        }
        src_tag_rows = conn.execute(
            text("SELECT ID, MyTagID FROM djmdSongMyTag WHERE ContentID = :id;"),
            {"id": str(source_content_id)},
        ).fetchall()
        tags_transferred = 0
        for tag_entry_id, tag_id in src_tag_rows:
            if tag_id not in tgt_tags:
                conn.execute(
                    text("UPDATE djmdSongMyTag SET ContentID = :target_id WHERE ID = :entry_id;"),
                    {"target_id": str(target_content_id), "entry_id": tag_entry_id},
                )
                tgt_tags.add(tag_id)
                tags_transferred += 1
            else:
                conn.execute(
                    text("DELETE FROM djmdSongMyTag WHERE ID = :entry_id;"),
                    {"entry_id": tag_entry_id},
                )

        # 6. Transfer cues if target has none
        tgt_cues_count = conn.execute(
            text("SELECT count(*) FROM djmdCue WHERE ContentID = :id;"),
            {"id": str(target_content_id)},
        ).fetchone()[0]
        cues_transferred = 0
        if tgt_cues_count == 0:
            cue_res = conn.execute(
                text("UPDATE djmdCue SET ContentID = :target_id WHERE ContentID = :source_id;"),
                {"target_id": str(target_content_id), "source_id": str(source_content_id)},
            )
            cues_transferred = cue_res.rowcount or 0

        # 7. Soft delete old source track
        conn.execute(
            text("""
                UPDATE djmdContent
                SET rb_local_deleted = 1,
                    updated_at = :updated_at
                WHERE ID = :source_id;
            """),
            {"source_id": str(source_content_id), "updated_at": now_utc},
        )

    # 8. Post-transaction: handle old file
    old_file_path = src_dict.get("FolderPath") or ""
    old_file_handled = "kept"
    if old_file_path and os.path.exists(old_file_path):
        if old_file_action == "trash":
            trashed = trash_file_macos(old_file_path)
            old_file_handled = "trashed" if trashed else "failed_trash"
        elif old_file_action == "backup":
            bak_path = f"{old_file_path}.bak"
            try:
                os.rename(old_file_path, bak_path)
                old_file_handled = f"renamed_to_{os.path.basename(bak_path)}"
            except Exception:
                old_file_handled = "failed_rename"

    return {
        "success": True,
        "source_id": str(source_content_id),
        "target_id": str(target_content_id),
        "title": tgt_dict.get("Title") or "(Untitled)",
        "play_count_transferred": src_plays,
        "stock_date_transferred": src_stock_date,
        "history_transferred": hist_transferred,
        "playlists_transferred": pl_transferred,
        "tags_transferred": tags_transferred,
        "cues_transferred": cues_transferred,
        "old_file_handled": old_file_handled,
    }


def main() -> None:
    """Command-line interface for the Replace File tool."""
    parser = argparse.ArgumentParser(
        description="Rekordbox Collection Manager - Replace Audio File Tool (MP3 to AIFF)",
    )
    parser.add_argument("--db-path", dest="db_path_flag", help="Path to master.db")
    parser.add_argument("--track-id", help="Content ID of MP3 track to replace")
    parser.add_argument("--new-file", help="Path to replacement AIFF audio file (.aiff, .aif)")
    parser.add_argument(
        "--action",
        choices=["keep", "trash", "backup"],
        default="keep",
        help="Action for old audio file on disk",
    )
    parser.add_argument("--force", action="store_true", help="Force proceed if Rekordbox is open")
    args = parser.parse_args()

    ensure_rekordbox_not_running(force=args.force)

    db_path = resolve_db_path(args)
    print(f"Opening Rekordbox database: {db_path}")
    engine, _ = open_database(db_path)

    if args.track_id and args.new_file:
        res = replace_track_audio_file(
            engine,
            args.track_id,
            args.new_file,
            options={"old_file_action": args.action},
        )
        print(f"✅ Successfully replaced audio file for track '{res['title']}' (ID: {res['content_id']})")
        print(f"   Play count preserved: {res['play_count']}")
        print(f"   Date added preserved: {res['stock_date']}")
        print(f"   History sessions preserved: {res['history_plays_preserved']}")
        print(f"   Cues preserved: {res['cues_preserved']}")
        print(f"   New file: {res['new_file']['path']} ({res['new_file']['format']}, {res['new_file']['bitrate']} kbps)")
    else:
        print("Interactive CLI mode: Use GUI ('rekordbox-manager') or specify --track-id and --new-file.")


if __name__ == "__main__":
    main()
