"""Replace File Tool for Rekordbox Collection Manager.

Enables upgrading collection MP3 tracks to AIFF, or replacing AIFF tracks with AIFF,
while preserving 100% of history session entries, play counts, original "Date Added"
(StockDate), cue points, playlists, and tags.
"""

import argparse
import math
import os
import shutil
import struct
import subprocess
import sys
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Engine

from rekordbox_collection_manager.utils import (
    EXTENSION_TO_FILE_TYPE,
    FILE_TYPE_TO_NAME,
    _resolve_tag_matching_ids,
    ensure_rekordbox_not_running,
    format_duration,
    get_rekordbox_timestamp,
    is_rekordbox_running,
    open_database,
    resolve_db_path,
    resolve_file_type,
)


# Re-export for backward compatibility
__all__ = [
    "EXTENSION_TO_FILE_TYPE",
    "FILE_TYPE_TO_NAME",
    "inspect_audio_file",
    "fetch_track_full_info",
    "replace_track_audio_file",
    "merge_imported_tracks",
    "trash_file_macos",
]


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
        "bpm": 0.0,
        "key": "",
        "comment": "",
        "remixer": "",
        "label": "",
        "composer": "",
        "mix_name": "",
        "track_no": 0,
        "disc_no": 0,
        "rating": 0,
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
                    info["duration_str"] = format_duration(sec)
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
                raw_year = get_tag_val(["TDRC", "TYER", "date", "\xa9day"])
                if raw_year and len(raw_year) >= 4 and raw_year[:4].isdigit():
                    info["year"] = int(raw_year[:4])

                # Additional Rekordbox Info tags
                info["key"] = get_tag_val(["TKEY", "initialkey", "key"])
                info["remixer"] = get_tag_val(["TPE4", "remixer"])
                info["label"] = get_tag_val(["TPUB", "publisher", "label", "organization"])
                info["composer"] = get_tag_val(["TCOM", "composer", "\xa9wrt"])
                info["mix_name"] = get_tag_val(["TIT3", "subtitle"])

                raw_bpm = get_tag_val(["TBPM", "bpm", "tmpo"])
                if raw_bpm:
                    try:
                        clean_bpm = float(raw_bpm.replace(",", ".").strip())
                        if clean_bpm > 0:
                            info["bpm"] = round(clean_bpm, 1)
                    except (ValueError, TypeError):
                        pass

                raw_trck = get_tag_val(["TRCK", "tracknumber"])
                if raw_trck:
                    try:
                        info["track_no"] = int(raw_trck.split("/")[0].strip())
                    except Exception:
                        pass

                raw_disc = get_tag_val(["TPOS", "discnumber"])
                if raw_disc:
                    try:
                        info["disc_no"] = int(raw_disc.split("/")[0].strip())
                    except Exception:
                        pass

                # Comment tags
                for k in tags.keys():
                    k_str = str(k)
                    if k_str.startswith("COMM") or k_str.lower() in ("comment", "description", "\xa9cmt"):
                        val = tags[k]
                        if hasattr(val, "text") and len(val.text) > 0:
                            info["comment"] = str(val.text[0]).strip()
                            break
                        elif isinstance(val, list) and len(val) > 0:
                            info["comment"] = str(val[0]).strip()
                            break
                        else:
                            info["comment"] = str(val).strip()
                            break

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


def fetch_track_full_info(engine: Engine, content_id: str) -> Dict[str, Any]:
    """Fetch all Rekordbox 'Info' fields for a track from database."""
    with engine.connect() as conn:
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdContent);").fetchall()}
        tables = {r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table';").fetchall()}

        joins = []
        has_src_artist = "SrcArtistName" in cols
        has_src_album = "SrcAlbumName" in cols

        artist_fallback = "COALESCE(c.SrcArtistName, '')" if has_src_artist else "''"
        artist_expr = artist_fallback
        if "djmdArtist" in tables and "ArtistID" in cols:
            joins.append("LEFT JOIN djmdArtist a ON c.ArtistID = a.ID")
            artist_expr = f"COALESCE(a.Name, {artist_fallback}, '')"

        album_fallback = "COALESCE(c.SrcAlbumName, '')" if has_src_album else "''"
        album_expr = album_fallback
        if "djmdAlbum" in tables and "AlbumID" in cols:
            joins.append("LEFT JOIN djmdAlbum al ON c.AlbumID = al.ID")
            album_expr = f"COALESCE(al.Name, {album_fallback}, '')"

        genre_expr = "''"
        if "djmdGenre" in tables and "GenreID" in cols:
            joins.append("LEFT JOIN djmdGenre g ON c.GenreID = g.ID")
            genre_expr = "COALESCE(g.Name, '')"

        key_expr = "''"
        if "djmdKey" in tables and "KeyID" in cols:
            joins.append("LEFT JOIN djmdKey k ON c.KeyID = k.ID")
            key_expr = "COALESCE(k.ScaleName, '')"

        remixer_expr = "''"
        if "djmdArtist" in tables and "RemixerID" in cols:
            joins.append("LEFT JOIN djmdArtist rem ON c.RemixerID = rem.ID")
            remixer_expr = "COALESCE(rem.Name, '')"

        label_expr = "''"
        if "djmdLabel" in tables and "LabelID" in cols:
            joins.append("LEFT JOIN djmdLabel lbl ON c.LabelID = lbl.ID")
            label_expr = "COALESCE(lbl.Name, '')"

        composer_expr = "''"
        if "djmdArtist" in tables and "ComposerID" in cols:
            joins.append("LEFT JOIN djmdArtist comp ON c.ComposerID = comp.ID")
            composer_expr = "COALESCE(comp.Name, '')"

        bpm_col = "COALESCE(c.BPM, 0)" if "BPM" in cols else "0"
        length_col = "COALESCE(c.Length, 0)" if "Length" in cols else "0"
        br_col = "COALESCE(c.BitRate, 0)" if "BitRate" in cols else "0"
        bd_col = "COALESCE(c.BitDepth, 0)" if "BitDepth" in cols else "0"
        sr_col = "COALESCE(c.SampleRate, 0)" if "SampleRate" in cols else "0"
        fs_col = "COALESCE(c.FileSize, 0)" if "FileSize" in cols else "0"
        ft_col = "COALESCE(c.FileType, 0)" if "FileType" in cols else "0"
        com_col = "COALESCE(c.Commnt, '')" if "Commnt" in cols else "''"
        rat_col = "COALESCE(c.Rating, 0)" if "Rating" in cols else "0"
        yr_col = "COALESCE(c.ReleaseYear, 0)" if "ReleaseYear" in cols else "0"
        tno_col = "COALESCE(c.TrackNo, 0)" if "TrackNo" in cols else "0"
        dno_col = "COALESCE(c.DiscNo, 0)" if "DiscNo" in cols else "0"
        sub_col = "COALESCE(c.Subtitle, '')" if "Subtitle" in cols else "''"
        col_col = "COALESCE(c.ColorID, '')" if "ColorID" in cols else "''"
        stock_col = "COALESCE(c.StockDate, '')" if "StockDate" in cols else "''"
        play_col = "COALESCE(c.DJPlayCount, 0)" if "DJPlayCount" in cols else "0"
        img_col = "COALESCE(c.ImagePath, '')" if "ImagePath" in cols else "''"
        fp_col = "COALESCE(c.FolderPath, '')" if "FolderPath" in cols else "''"
        fn_col = "COALESCE(c.FileNameL, '')" if "FileNameL" in cols else "''"
        title_col = "COALESCE(c.Title, '')" if "Title" in cols else "''"

        sql = f"""
            SELECT 
                c.ID,
                {title_col} AS Title,
                {artist_expr} AS Artist,
                {album_expr} AS Album,
                {genre_expr} AS Genre,
                {key_expr} AS KeyName,
                {bpm_col} AS BPM,
                {length_col} AS Length,
                {br_col} AS BitRate,
                {bd_col} AS BitDepth,
                {sr_col} AS SampleRate,
                {fs_col} AS FileSize,
                {ft_col} AS FileType,
                {com_col} AS Commnt,
                {rat_col} AS Rating,
                {yr_col} AS ReleaseYear,
                {tno_col} AS TrackNo,
                {dno_col} AS DiscNo,
                {remixer_expr} AS Remixer,
                {label_expr} AS Label,
                {sub_col} AS MixName,
                {composer_expr} AS Composer,
                {col_col} AS ColorID,
                {stock_col} AS StockDate,
                {play_col} AS DJPlayCount,
                {img_col} AS ImagePath,
                {fp_col} AS FolderPath,
                {fn_col} AS FileNameL
            FROM djmdContent c
            {' '.join(joins)}
            WHERE c.ID = :content_id;
        """
        row = conn.execute(text(sql), {"content_id": str(content_id)}).mappings().fetchone()
        if not row:
            return {}

        r = dict(row)
        bpm_raw = r.get("BPM") or 0
        bpm_val = round(float(bpm_raw) / 100.0, 1) if bpm_raw > 0 else 0.0
        dur_sec = int(r.get("Length") or 0)

        fn = r.get("FileNameL") or ""
        fp = r.get("FolderPath") or ""
        ft_code = r.get("FileType") or 1
        ft_str = resolve_file_type(ft_code, fn, fp)

        # Query preserved relational metrics (History sets, Playlists, My Tags, Cues)
        cid_str = str(content_id)
        cid_params = {"cid": cid_str}

        # 1. History sets played
        history_count = 0
        if "djmdSongHistory" in tables:
            try:
                sh_cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdSongHistory);").fetchall()}
                sh_del = "AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL)" if "rb_local_deleted" in sh_cols else ""
                h_sql = f"""
                    SELECT count(DISTINCT HistoryID)
                    FROM djmdSongHistory
                    WHERE (ContentID = :cid OR ContentID = CAST(:cid AS INTEGER) OR CAST(ContentID AS TEXT) = :cid)
                    {sh_del};
                """
                row_h = conn.execute(text(h_sql), cid_params).fetchone()
                history_count = int(row_h[0]) if row_h and row_h[0] is not None else 0
            except Exception:
                pass

        # 2. Playlists
        playlist_count = 0
        playlists_list: List[str] = []
        if "djmdSongPlaylist" in tables:
            try:
                sp_cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdSongPlaylist);").fetchall()}
                sp_del = "AND (sp.rb_local_deleted = 0 OR sp.rb_local_deleted IS NULL)" if "rb_local_deleted" in sp_cols else ""

                if "djmdPlaylist" in tables:
                    p_cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdPlaylist);").fetchall()}
                    p_del = "AND (p.rb_local_deleted = 0 OR p.rb_local_deleted IS NULL)" if "rb_local_deleted" in p_cols else ""
                    p_attr = "AND (p.Attribute = 0 OR p.Attribute IS NULL)" if "Attribute" in p_cols else ""
                    rows_p = conn.execute(
                        text(f"""
                            SELECT DISTINCT p.Name
                            FROM djmdSongPlaylist sp
                            JOIN djmdPlaylist p ON sp.PlaylistID = p.ID
                            WHERE (sp.ContentID = :cid OR sp.ContentID = CAST(:cid AS INTEGER) OR CAST(sp.ContentID AS TEXT) = :cid)
                              {p_attr} {p_del} {sp_del}
                            ORDER BY lower(p.Name) ASC;
                        """),
                        cid_params,
                    ).fetchall()
                    playlists_list = [str(rp[0]).strip() for rp in rows_p if rp[0] and str(rp[0]).strip()]
                    playlist_count = len(playlists_list)
                else:
                    row_p = conn.execute(
                        text(f"SELECT count(DISTINCT PlaylistID) FROM djmdSongPlaylist WHERE (ContentID = :cid OR ContentID = CAST(:cid AS INTEGER) OR CAST(ContentID AS TEXT) = :cid) {sp_del};"),
                        cid_params,
                    ).fetchone()
                    playlist_count = int(row_p[0]) if row_p and row_p[0] is not None else 0
            except Exception:
                pass

        # 3. My Tags
        tag_count = 0
        tags_list: List[str] = []
        if "djmdSongMyTag" in tables:
            try:
                smt_cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdSongMyTag);").fetchall()}
                smt_del = "AND (smt.rb_local_deleted = 0 OR smt.rb_local_deleted IS NULL)" if "rb_local_deleted" in smt_cols else ""

                if "djmdMyTag" in tables:
                    t_cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdMyTag);").fetchall()}
                    t_del = "AND (t.rb_local_deleted = 0 OR t.rb_local_deleted IS NULL)" if "rb_local_deleted" in t_cols else ""
                    t_attr = "AND (t.Attribute = 0 OR t.Attribute IS NULL)" if "Attribute" in t_cols else ""
                    rows_t = conn.execute(
                        text(f"""
                            SELECT DISTINCT t.Name
                            FROM djmdSongMyTag smt
                            JOIN djmdMyTag t ON smt.MyTagID = t.ID
                            WHERE (smt.ContentID = :cid OR smt.ContentID = CAST(:cid AS INTEGER) OR CAST(smt.ContentID AS TEXT) = :cid)
                              {smt_del} {t_del} {t_attr}
                            ORDER BY lower(t.Name) ASC;
                        """),
                        cid_params,
                    ).fetchall()
                    tags_list = [str(rt[0]).strip() for rt in rows_t if rt[0] and str(rt[0]).strip()]
                    tag_count = len(tags_list)
                else:
                    row_t = conn.execute(
                        text(f"SELECT count(DISTINCT MyTagID) FROM djmdSongMyTag WHERE (ContentID = :cid OR ContentID = CAST(:cid AS INTEGER) OR CAST(ContentID AS TEXT) = :cid) {smt_del};"),
                        cid_params,
                    ).fetchone()
                    tag_count = int(row_t[0]) if row_t and row_t[0] is not None else 0
            except Exception:
                pass

        # 4. Cue points
        cue_count = 0
        if "djmdCue" in tables:
            try:
                c_cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdCue);").fetchall()}
                c_del = "AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL)" if "rb_local_deleted" in c_cols else ""
                row_c = conn.execute(
                    text(f"SELECT count(*) FROM djmdCue WHERE (ContentID = :cid OR ContentID = CAST(:cid AS INTEGER) OR CAST(ContentID AS TEXT) = :cid) {c_del};"),
                    cid_params,
                ).fetchone()
                cue_count = int(row_c[0]) if row_c and row_c[0] is not None else 0
            except Exception:
                pass

        return {
            "id": str(r["ID"]),
            "content_id": str(r["ID"]),
            "title": r.get("Title") or "",
            "artist": r.get("Artist") or "",
            "album": r.get("Album") or "",
            "genre": r.get("Genre") or "",
            "bpm": bpm_val,
            "key": r.get("KeyName") or "",
            "rating": int(r.get("Rating") or 0),
            "release_year": int(r.get("ReleaseYear") or 0),
            "year": int(r.get("ReleaseYear") or 0),
            "track_no": int(r.get("TrackNo") or 0),
            "disc_no": int(r.get("DiscNo") or 0),
            "remixer": r.get("Remixer") or "",
            "label": r.get("Label") or "",
            "mix_name": r.get("MixName") or "",
            "composer": r.get("Composer") or "",
            "comment": r.get("Commnt") or "",
            "color_id": str(r.get("ColorID") or ""),
            "date_added": str(r.get("StockDate") or "")[:10],
            "play_count": int(r.get("DJPlayCount") or 0),
            "image_path": r.get("ImagePath") or "",
            "folder_path": fp,
            "file_path": fp,
            "file_name": fn,
            "file_type": ft_str,
            "file_type_code": ft_code,
            "bitrate": int(r.get("BitRate") or 0),
            "sample_rate": int(r.get("SampleRate") or 0),
            "bit_depth": int(r.get("BitDepth") or 0),
            "duration_sec": dur_sec,
            "duration_str": format_duration(dur_sec),
            "file_size": int(r.get("FileSize") or 0),
            "history_count": history_count,
            "playlist_count": playlist_count,
            "playlists": playlists_list,
            "tag_count": tag_count,
            "tags": tags_list,
            "cue_count": cue_count,
        }


def _find_or_create_artist(conn: Any, name: str, now_utc: str) -> Optional[str]:
    """Find existing djmdArtist ID or insert a new artist."""
    if not name or not str(name).strip():
        return None
    name_clean = str(name).strip()
    try:
        row = conn.execute(
            text("SELECT ID FROM djmdArtist WHERE lower(trim(Name)) = lower(trim(:n)) AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL) LIMIT 1;"),
            {"n": name_clean},
        ).fetchone()
        if row and row[0]:
            return str(row[0])
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdArtist);").fetchall()}
        new_id = str(uuid.uuid4())
        data: Dict[str, Any] = {
            "ID": new_id,
            "Name": name_clean,
            "SearchStr": name_clean.lower(),
            "UUID": new_id,
            "rb_data_status": 0,
            "rb_local_data_status": 0,
            "rb_local_deleted": 0,
            "rb_local_synced": 1,
            "created_at": now_utc,
            "updated_at": now_utc,
        }
        ins_cols = [k for k in data if k in cols]
        conn.execute(
            text(f"INSERT INTO djmdArtist ({', '.join(ins_cols)}) VALUES ({', '.join(f':{k}' for k in ins_cols)});"),
            {k: data[k] for k in ins_cols},
        )
        return new_id
    except Exception:
        return None


def _find_or_create_album(conn: Any, name: str, now_utc: str, artist_id: Optional[str] = None) -> Optional[str]:
    """Find existing djmdAlbum ID or insert a new album."""
    if not name or not str(name).strip():
        return None
    name_clean = str(name).strip()
    try:
        row = conn.execute(
            text("SELECT ID FROM djmdAlbum WHERE lower(trim(Name)) = lower(trim(:n)) AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL) LIMIT 1;"),
            {"n": name_clean},
        ).fetchone()
        if row and row[0]:
            return str(row[0])
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdAlbum);").fetchall()}
        new_id = str(uuid.uuid4())
        data: Dict[str, Any] = {
            "ID": new_id,
            "Name": name_clean,
            "AlbumArtistID": artist_id,
            "SearchStr": name_clean.lower(),
            "UUID": new_id,
            "rb_data_status": 0,
            "rb_local_data_status": 0,
            "rb_local_deleted": 0,
            "rb_local_synced": 1,
            "created_at": now_utc,
            "updated_at": now_utc,
        }
        ins_cols = [k for k in data if k in cols]
        conn.execute(
            text(f"INSERT INTO djmdAlbum ({', '.join(ins_cols)}) VALUES ({', '.join(f':{k}' for k in ins_cols)});"),
            {k: data[k] for k in ins_cols},
        )
        return new_id
    except Exception:
        return None


def _find_or_create_genre(conn: Any, name: str, now_utc: str) -> Optional[str]:
    """Find existing djmdGenre ID or insert a new genre."""
    if not name or not str(name).strip():
        return None
    name_clean = str(name).strip()
    try:
        row = conn.execute(
            text("SELECT ID FROM djmdGenre WHERE lower(trim(Name)) = lower(trim(:n)) AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL) LIMIT 1;"),
            {"n": name_clean},
        ).fetchone()
        if row and row[0]:
            return str(row[0])
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdGenre);").fetchall()}
        new_id = str(uuid.uuid4())
        data: Dict[str, Any] = {
            "ID": new_id,
            "Name": name_clean,
            "UUID": new_id,
            "rb_data_status": 0,
            "rb_local_data_status": 0,
            "rb_local_deleted": 0,
            "rb_local_synced": 1,
            "created_at": now_utc,
            "updated_at": now_utc,
        }
        ins_cols = [k for k in data if k in cols]
        conn.execute(
            text(f"INSERT INTO djmdGenre ({', '.join(ins_cols)}) VALUES ({', '.join(f':{k}' for k in ins_cols)});"),
            {k: data[k] for k in ins_cols},
        )
        return new_id
    except Exception:
        return None


def _find_or_create_label(conn: Any, name: str, now_utc: str) -> Optional[str]:
    """Find existing djmdLabel ID or insert a new record label."""
    if not name or not str(name).strip():
        return None
    name_clean = str(name).strip()
    try:
        row = conn.execute(
            text("SELECT ID FROM djmdLabel WHERE lower(trim(Name)) = lower(trim(:n)) AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL) LIMIT 1;"),
            {"n": name_clean},
        ).fetchone()
        if row and row[0]:
            return str(row[0])
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdLabel);").fetchall()}
        new_id = str(uuid.uuid4())
        data: Dict[str, Any] = {
            "ID": new_id,
            "Name": name_clean,
            "UUID": new_id,
            "rb_data_status": 0,
            "rb_local_data_status": 0,
            "rb_local_deleted": 0,
            "rb_local_synced": 1,
            "created_at": now_utc,
            "updated_at": now_utc,
        }
        ins_cols = [k for k in data if k in cols]
        conn.execute(
            text(f"INSERT INTO djmdLabel ({', '.join(ins_cols)}) VALUES ({', '.join(f':{k}' for k in ins_cols)});"),
            {k: data[k] for k in ins_cols},
        )
        return new_id
    except Exception:
        return None


def _find_key_id(conn: Any, key_str: str) -> Optional[str]:
    """Find matching KeyID in djmdKey table by ScaleName (musical key string)."""
    if not key_str or not str(key_str).strip():
        return None
    k_clean = str(key_str).strip()
    try:
        row = conn.execute(
            text("SELECT ID FROM djmdKey WHERE lower(trim(ScaleName)) = lower(trim(:k)) LIMIT 1;"),
            {"k": k_clean},
        ).fetchone()
        if row and row[0]:
            return str(row[0])
        return None
    except Exception:
        return None


def _apply_resolved_metadata(
    conn: Any,
    cols: Set[str],
    tables: Set[str],
    content_id: str,
    resolved_meta: Dict[str, Any],
    now_utc: str,
) -> None:
    """Apply resolved metadata fields to a djmdContent record."""
    if not resolved_meta:
        return

    update_clauses: List[str] = []
    params: Dict[str, Any] = {"id": str(content_id), "now_utc": now_utc}

    # 1. Title
    if "title" in resolved_meta and "Title" in cols:
        update_clauses.append("Title = :res_title")
        params["res_title"] = str(resolved_meta["title"]).strip()

    # 2. Artist
    if "artist" in resolved_meta:
        art_val = str(resolved_meta["artist"]).strip()
        if "SrcArtistName" in cols:
            update_clauses.append("SrcArtistName = :res_artist")
            params["res_artist"] = art_val
        if "ArtistID" in cols and "djmdArtist" in tables:
            art_id = _find_or_create_artist(conn, art_val, now_utc) if art_val else None
            update_clauses.append("ArtistID = :res_artist_id")
            params["res_artist_id"] = art_id

    # 3. Album
    if "album" in resolved_meta:
        alb_val = str(resolved_meta["album"]).strip()
        if "SrcAlbumName" in cols:
            update_clauses.append("SrcAlbumName = :res_album")
            params["res_album"] = alb_val
        if "AlbumID" in cols and "djmdAlbum" in tables:
            alb_id = _find_or_create_album(conn, alb_val, now_utc) if alb_val else None
            update_clauses.append("AlbumID = :res_album_id")
            params["res_album_id"] = alb_id

    # 4. Genre
    if "genre" in resolved_meta and "GenreID" in cols and "djmdGenre" in tables:
        gen_val = str(resolved_meta["genre"]).strip()
        gen_id = _find_or_create_genre(conn, gen_val, now_utc) if gen_val else None
        update_clauses.append("GenreID = :res_genre_id")
        params["res_genre_id"] = gen_id

    # 5. Key
    if "key" in resolved_meta and "KeyID" in cols and "djmdKey" in tables:
        k_val = str(resolved_meta["key"]).strip()
        k_id = _find_key_id(conn, k_val) if k_val else None
        update_clauses.append("KeyID = :res_key_id")
        params["res_key_id"] = k_id

    # 6. BPM
    if "bpm" in resolved_meta and "BPM" in cols:
        raw_bpm = resolved_meta["bpm"]
        try:
            bpm_f = float(raw_bpm)
            bpm_int = int(round(bpm_f * 100.0)) if bpm_f < 1000 else int(round(bpm_f))
            if bpm_int > 0:
                update_clauses.append("BPM = :res_bpm")
                params["res_bpm"] = bpm_int
        except (ValueError, TypeError):
            pass

    # 7. Rating
    if "rating" in resolved_meta and "Rating" in cols:
        try:
            r_val = int(resolved_meta["rating"])
            update_clauses.append("Rating = :res_rating")
            params["res_rating"] = r_val
        except (ValueError, TypeError):
            pass

    # 8. ReleaseYear
    if "release_year" in resolved_meta and "ReleaseYear" in cols:
        try:
            y_val = int(resolved_meta["release_year"])
            update_clauses.append("ReleaseYear = :res_year")
            params["res_year"] = y_val
        except (ValueError, TypeError):
            pass

    # 9. Comment
    if "comment" in resolved_meta and "Commnt" in cols:
        update_clauses.append("Commnt = :res_comment")
        params["res_comment"] = str(resolved_meta["comment"]).strip()

    # 10. Remixer
    if "remixer" in resolved_meta and "RemixerID" in cols and "djmdArtist" in tables:
        rem_val = str(resolved_meta["remixer"]).strip()
        rem_id = _find_or_create_artist(conn, rem_val, now_utc) if rem_val else None
        update_clauses.append("RemixerID = :res_remixer_id")
        params["res_remixer_id"] = rem_id

    # 11. Label
    if "label" in resolved_meta and "LabelID" in cols and "djmdLabel" in tables:
        lbl_val = str(resolved_meta["label"]).strip()
        lbl_id = _find_or_create_label(conn, lbl_val, now_utc) if lbl_val else None
        update_clauses.append("LabelID = :res_label_id")
        params["res_label_id"] = lbl_id

    # 12. Composer
    if "composer" in resolved_meta and "ComposerID" in cols and "djmdArtist" in tables:
        comp_val = str(resolved_meta["composer"]).strip()
        comp_id = _find_or_create_artist(conn, comp_val, now_utc) if comp_val else None
        update_clauses.append("ComposerID = :res_composer_id")
        params["res_composer_id"] = comp_id

    # 13. Mix Name (Subtitle)
    if "mix_name" in resolved_meta and "Subtitle" in cols:
        update_clauses.append("Subtitle = :res_mix_name")
        params["res_mix_name"] = str(resolved_meta["mix_name"]).strip()

    # 14. TrackNo
    if "track_no" in resolved_meta and "TrackNo" in cols:
        try:
            tno = int(resolved_meta["track_no"])
            update_clauses.append("TrackNo = :res_track_no")
            params["res_track_no"] = tno
        except (ValueError, TypeError):
            pass

    # 15. DiscNo
    if "disc_no" in resolved_meta and "DiscNo" in cols:
        try:
            dno = int(resolved_meta["disc_no"])
            update_clauses.append("DiscNo = :res_disc_no")
            params["res_disc_no"] = dno
        except (ValueError, TypeError):
            pass

    # 16. ColorID
    if "color_id" in resolved_meta and "ColorID" in cols:
        update_clauses.append("ColorID = :res_color_id")
        params["res_color_id"] = str(resolved_meta["color_id"])

    if update_clauses:
        if "updated_at" in cols:
            update_clauses.append("updated_at = :now_utc")
        if "TrackInfoUpdated" in cols:
            update_clauses.append("TrackInfoUpdated = COALESCE(CAST(CAST(TrackInfoUpdated AS INTEGER) + 1 AS TEXT), '1')")
        sql = f"UPDATE djmdContent SET {', '.join(update_clauses)} WHERE ID = :id;"
        conn.execute(text(sql), params)


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

        # Enforce: Source track must be MP3 or AIFF
        old_ext = os.path.splitext(old_file_name or old_file_path)[1].lower()
        if old_file_type not in (1, 12) and old_ext not in (".mp3", ".aiff", ".aif"):
            old_fmt = resolve_file_type(old_file_type, old_file_name, old_file_path)
            raise ValueError(
                f"Only MP3 and AIFF tracks can be replaced. The selected track is {old_fmt}."
            )

        if old_file_path and os.path.abspath(old_file_path) == resolved_new_path:
            raise ValueError(
                "The replacement audio file is identical to the current track's audio file path."
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

        # 2b. Apply resolved metadata if provided
        resolved_meta = opts.get("resolved_metadata")
        if resolved_meta:
            tables = {r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table';").fetchall()}
            _apply_resolved_metadata(conn, cols, tables, str(content_id), resolved_meta, now_utc)

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

        if str(source_content_id) == str(target_content_id):
            raise ValueError("Source and target track cannot be the same track.")

        # Enforce: Source must be MP3 or AIFF, and Target must be AIFF
        src_ext = os.path.splitext(src_dict.get("FileNameL") or src_dict.get("FolderPath") or "")[1].lower()
        if src_dict.get("FileType") not in (1, 12) and src_ext not in (".mp3", ".aiff", ".aif"):
            src_fmt = resolve_file_type(src_dict.get("FileType"), src_dict.get("FileNameL"), src_dict.get("FolderPath"))
            raise ValueError(f"Source track must be an MP3 or AIFF file (selected track is {src_fmt}).")

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

        # 2b. Apply resolved metadata if provided
        resolved_meta = opts.get("resolved_metadata")
        if resolved_meta:
            cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdContent);").fetchall()}
            tables = {r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table';").fetchall()}
            _apply_resolved_metadata(conn, cols, tables, str(target_content_id), resolved_meta, now_utc)

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
        tgt_tags: Set[str] = set()
        for r in conn.execute(
            text("SELECT MyTagID FROM djmdSongMyTag WHERE ContentID = :id;"),
            {"id": str(target_content_id)},
        ).fetchall():
            if r[0] is not None:
                for mid in _resolve_tag_matching_ids(r[0]):
                    tgt_tags.add(mid)

        src_tag_rows = conn.execute(
            text("SELECT ID, MyTagID FROM djmdSongMyTag WHERE ContentID = :id;"),
            {"id": str(source_content_id)},
        ).fetchall()
        tags_transferred = 0
        for tag_entry_id, tag_id in src_tag_rows:
            tag_id_str = str(tag_id) if tag_id is not None else ""
            if tag_id_str and not any(mid in tgt_tags for mid in _resolve_tag_matching_ids(tag_id_str)):
                conn.execute(
                    text("UPDATE djmdSongMyTag SET ContentID = :target_id WHERE ID = :entry_id;"),
                    {"target_id": str(target_content_id), "entry_id": tag_entry_id},
                )
                for mid in _resolve_tag_matching_ids(tag_id_str):
                    tgt_tags.add(mid)
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
        description="Rekordbox Collection Manager - Replace Audio File Tool (MP3/AIFF to AIFF)",
    )
    parser.add_argument("--db-path", dest="db_path_flag", help="Path to master.db")
    parser.add_argument("--track-id", help="Content ID of MP3 or AIFF track to replace")
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
