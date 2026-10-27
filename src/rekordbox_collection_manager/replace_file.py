"""Replace File Tool for Rekordbox Collection Manager.

Enables upgrading collection MP3 tracks to AIFF, or replacing AIFF tracks with AIFF,
while preserving 100% of history session entries, play counts, original "Date Added"
(StockDate), cue points, playlists, and tags.
"""

import argparse
import math
import os
import secrets
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
    extract_embedded_artwork,
    format_duration,
    get_rekordbox_timestamp,
    install_rekordbox_artwork,
    is_rekordbox_running,
    open_database,
    resolve_artwork_path,
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
    "repair_uuid_entity_ids",
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
        "album_artist": "",
        "original_artist": "",
        "lyricist": "",
        "message": "",
        "release_date": "",
        "track_no": 0,
        "disc_no": 0,
        "rating": 0,
        "image_path": "",
        "artwork_path": "",
        "artwork_resolution": "",
        "has_artwork": False,
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
                info["release_date"] = raw_year

                # Additional Rekordbox Info tags
                info["key"] = get_tag_val(["TKEY", "initialkey", "key"])
                info["remixer"] = get_tag_val(["TPE4", "remixer"])
                info["label"] = get_tag_val(["TPUB", "publisher", "label", "organization"])
                info["composer"] = get_tag_val(["TCOM", "composer", "\xa9wrt"])
                info["mix_name"] = get_tag_val(["TIT3", "subtitle"])
                info["album_artist"] = get_tag_val(["TPE2", "albumartist", "album_artist", "aART"])
                info["original_artist"] = get_tag_val(["TOPE", "originalartist", "original_artist"])
                info["lyricist"] = get_tag_val(["TEXT", "lyricist"])
                info["message"] = get_tag_val(["TXXX:message", "TXXX:Message", "TXXX:MESSAGE"])

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

    # Extract embedded artwork
    art_info = extract_embedded_artwork(file_path)
    if art_info:
        info["image_path"] = art_info[0]
        info["artwork_path"] = art_info[0]
        info["artwork_resolution"] = art_info[1]
        info["has_artwork"] = True

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
        album_artist_expr = "''"
        if "djmdAlbum" in tables and "AlbumID" in cols:
            joins.append("LEFT JOIN djmdAlbum al ON c.AlbumID = al.ID")
            album_expr = f"COALESCE(al.Name, {album_fallback}, '')"
            if "djmdArtist" in tables:
                joins.append("LEFT JOIN djmdArtist alb_art ON al.AlbumArtistID = alb_art.ID")
                album_artist_expr = "COALESCE(alb_art.Name, '')"

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

        org_artist_expr = "''"
        if "djmdArtist" in tables and "OrgArtistID" in cols:
            joins.append("LEFT JOIN djmdArtist org ON c.OrgArtistID = org.ID")
            org_artist_expr = "COALESCE(org.Name, '')"

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
        lyricist_col = "COALESCE(c.Lyricist, '')" if "Lyricist" in cols else "''"
        msg_col = "COALESCE(c.DeliveryComment, '')" if "DeliveryComment" in cols else "''"
        reldate_col = "COALESCE(c.ReleaseDate, '')" if "ReleaseDate" in cols else "''"

        uuid_col = "COALESCE(c.UUID, '')" if "UUID" in cols else "''"

        sql = f"""
            SELECT 
                c.ID,
                {uuid_col} AS UUID,
                {title_col} AS Title,
                {artist_expr} AS Artist,
                {album_expr} AS Album,
                {album_artist_expr} AS AlbumArtist,
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
                {reldate_col} AS ReleaseDate,
                {tno_col} AS TrackNo,
                {dno_col} AS DiscNo,
                {remixer_expr} AS Remixer,
                {label_expr} AS Label,
                {sub_col} AS MixName,
                {composer_expr} AS Composer,
                {org_artist_expr} AS OriginalArtist,
                {lyricist_col} AS Lyricist,
                {msg_col} AS Message,
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
        history_list: List[str] = []
        if "djmdSongHistory" in tables:
            try:
                sh_cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdSongHistory);").fetchall()}
                sh_del = "AND (sh.rb_local_deleted = 0 OR sh.rb_local_deleted IS NULL)" if "rb_local_deleted" in sh_cols else ""
                if "djmdHistory" in tables:
                    h_cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdHistory);").fetchall()}
                    h_del = "AND (h.rb_local_deleted = 0 OR h.rb_local_deleted IS NULL)" if "rb_local_deleted" in h_cols else ""
                    h_attr = "AND (h.Attribute = 0 OR h.Attribute IS NULL)" if "Attribute" in h_cols else ""
                    rows_h = conn.execute(
                        text(f"""
                            SELECT DISTINCT h.Name
                            FROM djmdSongHistory sh
                            JOIN djmdHistory h ON sh.HistoryID = h.ID
                            WHERE (sh.ContentID = :cid OR sh.ContentID = CAST(:cid AS INTEGER) OR CAST(sh.ContentID AS TEXT) = :cid)
                              {h_attr} {h_del} {sh_del}
                            ORDER BY lower(h.Name) ASC;
                        """),
                        cid_params,
                    ).fetchall()
                    history_list = [str(rh[0]).strip() for rh in rows_h if rh[0] and str(rh[0]).strip()]
                    history_count = len(history_list)
                else:
                    h_del_raw = "AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL)" if "rb_local_deleted" in sh_cols else ""
                    row_h = conn.execute(
                        text(f"SELECT count(DISTINCT HistoryID) FROM djmdSongHistory WHERE (ContentID = :cid OR ContentID = CAST(:cid AS INTEGER) OR CAST(ContentID AS TEXT) = :cid) {h_del_raw};"),
                        cid_params,
                    ).fetchone()
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
            "album_artist": r.get("AlbumArtist") or "",
            "original_artist": r.get("OriginalArtist") or "",
            "lyricist": r.get("Lyricist") or "",
            "message": r.get("Message") or "",
            "release_date": str(r.get("ReleaseDate") or "")[:10],
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
            "history_sessions": history_list,
            "history_plays": history_list,
            "playlist_count": playlist_count,
            "playlists": playlists_list,
            "tag_count": tag_count,
            "tags": tags_list,
            "cue_count": cue_count,
            "uuid": r.get("UUID") or "",
            "artwork_path": resolve_artwork_path(r.get("ImagePath"), db_path=getattr(getattr(engine, "url", None), "database", None)) or "",
            "has_artwork": bool(r.get("ImagePath")),
        }


def _generate_unused_numeric_id(conn: Any, table_name: str, id_col: str = "ID") -> str:
    """Generate a random unused numeric ID (28-bit unsigned integer) matching Rekordbox convention."""
    for _ in range(1000000):
        buf = secrets.token_bytes(4)
        num = ((buf[0] << 24) | (buf[1] << 16) | (buf[2] << 8) | buf[3]) >> 4
        if num < 100:
            continue
        id_str = str(num)
        exists = conn.execute(
            text(f"SELECT 1 FROM {table_name} WHERE {id_col} = :id LIMIT 1;"),
            {"id": id_str},
        ).fetchone()
        if not exists:
            return id_str
    raise RuntimeError(f"Could not generate unused numeric ID for {table_name}")


def _get_next_local_usn(conn: Any) -> Optional[int]:
    """Get and increment localUpdateCount from agentRegistry, or fallback to max rb_local_usn."""
    try:
        tables = {r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table';").fetchall()}
        if "agentRegistry" in tables:
            row = conn.execute(
                text("SELECT int_1 FROM agentRegistry WHERE registry_id = 'localUpdateCount' LIMIT 1;")
            ).fetchone()
            if row and row[0] is not None:
                next_usn = int(row[0]) + 1
                conn.execute(
                    text("UPDATE agentRegistry SET int_1 = :usn, updated_at = CURRENT_TIMESTAMP WHERE registry_id = 'localUpdateCount';"),
                    {"usn": next_usn},
                )
                return next_usn
    except Exception:
        pass
    try:
        row = conn.execute(text("SELECT COALESCE(MAX(rb_local_usn), 0) + 1 FROM djmdContent;")).fetchone()
        if row and row[0]:
            return int(row[0])
    except Exception:
        pass
    return None


def _find_or_create_artist(conn: Any, name: str, now_utc: str) -> Optional[str]:
    """Find existing djmdArtist ID or insert a new artist with a valid Rekordbox numeric ID."""
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
        new_numeric_id = _generate_unused_numeric_id(conn, "djmdArtist")
        new_uuid = str(uuid.uuid4())
        usn = _get_next_local_usn(conn)
        data: Dict[str, Any] = {
            "ID": new_numeric_id,
            "Name": name_clean,
            "SearchStr": None,
            "UUID": new_uuid,
            "rb_data_status": 0,
            "rb_local_data_status": 0,
            "rb_local_deleted": 0,
            "rb_local_synced": 1,
            "rb_local_usn": usn,
            "created_at": now_utc,
            "updated_at": now_utc,
        }
        ins_cols = [k for k in data if k in cols]
        conn.execute(
            text(f"INSERT INTO djmdArtist ({', '.join(ins_cols)}) VALUES ({', '.join(f':{k}' for k in ins_cols)});"),
            {k: data[k] for k in ins_cols},
        )
        return new_numeric_id
    except Exception:
        return None


def _find_or_create_album(conn: Any, name: str, now_utc: str, artist_id: Optional[str] = None) -> Optional[str]:
    """Find existing djmdAlbum ID or insert a new album with a valid Rekordbox numeric ID."""
    if not name or not str(name).strip():
        return None
    name_clean = str(name).strip()
    try:
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdAlbum);").fetchall()}
        has_album_artist = "AlbumArtistID" in cols
        sel_cols = "ID, AlbumArtistID" if has_album_artist else "ID"
        row = conn.execute(
            text(f"SELECT {sel_cols} FROM djmdAlbum WHERE lower(trim(Name)) = lower(trim(:n)) AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL) LIMIT 1;"),
            {"n": name_clean},
        ).fetchone()
        if row and row[0]:
            existing_id = str(row[0])
            if has_album_artist and artist_id and (len(row) > 1 and not row[1]):
                conn.execute(
                    text("UPDATE djmdAlbum SET AlbumArtistID = :aid, updated_at = :now WHERE ID = :id;"),
                    {"aid": str(artist_id), "now": now_utc, "id": existing_id},
                )
            return existing_id
        new_numeric_id = _generate_unused_numeric_id(conn, "djmdAlbum")
        new_uuid = str(uuid.uuid4())
        usn = _get_next_local_usn(conn)
        data: Dict[str, Any] = {
            "ID": new_numeric_id,
            "Name": name_clean,
            "AlbumArtistID": artist_id,
            "SearchStr": None,
            "UUID": new_uuid,
            "rb_data_status": 0,
            "rb_local_data_status": 0,
            "rb_local_deleted": 0,
            "rb_local_synced": 1,
            "rb_local_usn": usn,
            "created_at": now_utc,
            "updated_at": now_utc,
        }
        ins_cols = [k for k in data if k in cols]
        conn.execute(
            text(f"INSERT INTO djmdAlbum ({', '.join(ins_cols)}) VALUES ({', '.join(f':{k}' for k in ins_cols)});"),
            {k: data[k] for k in ins_cols},
        )
        return new_numeric_id
    except Exception:
        return None


def _find_or_create_genre(conn: Any, name: str, now_utc: str) -> Optional[str]:
    """Find existing djmdGenre ID or insert a new genre with a valid Rekordbox numeric ID."""
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
        new_numeric_id = _generate_unused_numeric_id(conn, "djmdGenre")
        new_uuid = str(uuid.uuid4())
        usn = _get_next_local_usn(conn)
        data: Dict[str, Any] = {
            "ID": new_numeric_id,
            "Name": name_clean,
            "UUID": new_uuid,
            "rb_data_status": 0,
            "rb_local_data_status": 0,
            "rb_local_deleted": 0,
            "rb_local_synced": 1,
            "rb_local_usn": usn,
            "created_at": now_utc,
            "updated_at": now_utc,
        }
        ins_cols = [k for k in data if k in cols]
        conn.execute(
            text(f"INSERT INTO djmdGenre ({', '.join(ins_cols)}) VALUES ({', '.join(f':{k}' for k in ins_cols)});"),
            {k: data[k] for k in ins_cols},
        )
        return new_numeric_id
    except Exception:
        return None


def _find_or_create_label(conn: Any, name: str, now_utc: str) -> Optional[str]:
    """Find existing djmdLabel ID or insert a new record label with a valid Rekordbox numeric ID."""
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
        new_numeric_id = _generate_unused_numeric_id(conn, "djmdLabel")
        new_uuid = str(uuid.uuid4())
        usn = _get_next_local_usn(conn)
        data: Dict[str, Any] = {
            "ID": new_numeric_id,
            "Name": name_clean,
            "UUID": new_uuid,
            "rb_data_status": 0,
            "rb_local_data_status": 0,
            "rb_local_deleted": 0,
            "rb_local_synced": 0,
            "rb_local_usn": usn,
            "created_at": now_utc,
            "updated_at": now_utc,
        }
        ins_cols = [k for k in data if k in cols]
        conn.execute(
            text(f"INSERT INTO djmdLabel ({', '.join(ins_cols)}) VALUES ({', '.join(f':{k}' for k in ins_cols)});"),
            {k: data[k] for k in ins_cols},
        )
        return new_numeric_id
    except Exception:
        return None


def repair_uuid_entity_ids(engine: Engine) -> Dict[str, int]:
    """Repair entity tables where ID was incorrectly saved as a UUID instead of a numeric Rekordbox ID."""
    stats = {
        "artists_fixed": 0,
        "albums_fixed": 0,
        "genres_fixed": 0,
        "labels_fixed": 0,
    }
    with engine.begin() as conn:
        tables = {r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table';").fetchall()}
        c_cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdContent);").fetchall()} if "djmdContent" in tables else set()
        now_utc = get_rekordbox_timestamp()

        # 1. djmdArtist
        if "djmdArtist" in tables:
            rows = conn.execute(text("SELECT ID, Name FROM djmdArtist WHERE ID LIKE '%-%';")).fetchall()
            for old_id, name in rows:
                clean_name = str(name).strip() if name else ""
                existing_num = None
                if clean_name:
                    existing_row = conn.execute(
                        text("SELECT ID FROM djmdArtist WHERE lower(trim(Name)) = lower(trim(:n)) AND ID NOT LIKE '%-%' AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL) LIMIT 1;"),
                        {"n": clean_name},
                    ).fetchone()
                    if existing_row and existing_row[0]:
                        existing_num = str(existing_row[0])

                if existing_num:
                    new_id = existing_num
                    conn.execute(text("DELETE FROM djmdArtist WHERE ID = :old_id;"), {"old_id": str(old_id)})
                else:
                    new_id = _generate_unused_numeric_id(conn, "djmdArtist")
                    usn = _get_next_local_usn(conn)
                    conn.execute(
                        text("UPDATE djmdArtist SET ID = :new_id, SearchStr = NULL, rb_local_usn = :usn, updated_at = :now WHERE ID = :old_id;"),
                        {"new_id": new_id, "usn": usn, "now": now_utc, "old_id": str(old_id)},
                    )

                if "djmdContent" in tables:
                    for col in ["ArtistID", "RemixerID", "ComposerID", "OrgArtistID"]:
                        if col in c_cols:
                            conn.execute(
                                text(f"UPDATE djmdContent SET {col} = :new_id, TrackInfoUpdated = COALESCE(CAST(CAST(TrackInfoUpdated AS INTEGER) + 1 AS TEXT), '1'), updated_at = :now WHERE {col} = :old_id;"),
                                {"new_id": new_id, "now": now_utc, "old_id": str(old_id)},
                            )
                if "djmdAlbum" in tables:
                    alb_cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdAlbum);").fetchall()}
                    if "AlbumArtistID" in alb_cols:
                        conn.execute(
                            text("UPDATE djmdAlbum SET AlbumArtistID = :new_id, updated_at = :now WHERE AlbumArtistID = :old_id;"),
                            {"new_id": new_id, "now": now_utc, "old_id": str(old_id)},
                        )
                stats["artists_fixed"] += 1

        # 2. djmdAlbum
        if "djmdAlbum" in tables:
            rows = conn.execute(text("SELECT ID, Name FROM djmdAlbum WHERE ID LIKE '%-%';")).fetchall()
            for old_id, name in rows:
                clean_name = str(name).strip() if name else ""
                existing_num = None
                if clean_name:
                    existing_row = conn.execute(
                        text("SELECT ID FROM djmdAlbum WHERE lower(trim(Name)) = lower(trim(:n)) AND ID NOT LIKE '%-%' AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL) LIMIT 1;"),
                        {"n": clean_name},
                    ).fetchone()
                    if existing_row and existing_row[0]:
                        existing_num = str(existing_row[0])

                if existing_num:
                    new_id = existing_num
                    conn.execute(text("DELETE FROM djmdAlbum WHERE ID = :old_id;"), {"old_id": str(old_id)})
                else:
                    new_id = _generate_unused_numeric_id(conn, "djmdAlbum")
                    usn = _get_next_local_usn(conn)
                    conn.execute(
                        text("UPDATE djmdAlbum SET ID = :new_id, SearchStr = NULL, rb_local_usn = :usn, updated_at = :now WHERE ID = :old_id;"),
                        {"new_id": new_id, "usn": usn, "now": now_utc, "old_id": str(old_id)},
                    )

                if "djmdContent" in tables and "AlbumID" in c_cols:
                    conn.execute(
                        text("UPDATE djmdContent SET AlbumID = :new_id, TrackInfoUpdated = COALESCE(CAST(CAST(TrackInfoUpdated AS INTEGER) + 1 AS TEXT), '1'), updated_at = :now WHERE AlbumID = :old_id;"),
                        {"new_id": new_id, "now": now_utc, "old_id": str(old_id)},
                    )
                stats["albums_fixed"] += 1

        # 3. djmdGenre
        if "djmdGenre" in tables:
            rows = conn.execute(text("SELECT ID, Name FROM djmdGenre WHERE ID LIKE '%-%';")).fetchall()
            for old_id, name in rows:
                clean_name = str(name).strip() if name else ""
                existing_num = None
                if clean_name:
                    existing_row = conn.execute(
                        text("SELECT ID FROM djmdGenre WHERE lower(trim(Name)) = lower(trim(:n)) AND ID NOT LIKE '%-%' AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL) LIMIT 1;"),
                        {"n": clean_name},
                    ).fetchone()
                    if existing_row and existing_row[0]:
                        existing_num = str(existing_row[0])

                if existing_num:
                    new_id = existing_num
                    conn.execute(text("DELETE FROM djmdGenre WHERE ID = :old_id;"), {"old_id": str(old_id)})
                else:
                    new_id = _generate_unused_numeric_id(conn, "djmdGenre")
                    usn = _get_next_local_usn(conn)
                    conn.execute(
                        text("UPDATE djmdGenre SET ID = :new_id, rb_local_usn = :usn, updated_at = :now WHERE ID = :old_id;"),
                        {"new_id": new_id, "usn": usn, "now": now_utc, "old_id": str(old_id)},
                    )

                if "djmdContent" in tables and "GenreID" in c_cols:
                    conn.execute(
                        text("UPDATE djmdContent SET GenreID = :new_id, TrackInfoUpdated = COALESCE(CAST(CAST(TrackInfoUpdated AS INTEGER) + 1 AS TEXT), '1'), updated_at = :now WHERE GenreID = :old_id;"),
                        {"new_id": new_id, "now": now_utc, "old_id": str(old_id)},
                    )
                stats["genres_fixed"] += 1

        # 4. djmdLabel
        if "djmdLabel" in tables:
            rows = conn.execute(text("SELECT ID, Name FROM djmdLabel WHERE ID LIKE '%-%';")).fetchall()
            for old_id, name in rows:
                clean_name = str(name).strip() if name else ""
                existing_num = None
                if clean_name:
                    existing_row = conn.execute(
                        text("SELECT ID FROM djmdLabel WHERE lower(trim(Name)) = lower(trim(:n)) AND ID NOT LIKE '%-%' AND (rb_local_deleted = 0 OR rb_local_deleted IS NULL) LIMIT 1;"),
                        {"n": clean_name},
                    ).fetchone()
                    if existing_row and existing_row[0]:
                        existing_num = str(existing_row[0])

                if existing_num:
                    new_id = existing_num
                    conn.execute(text("DELETE FROM djmdLabel WHERE ID = :old_id;"), {"old_id": str(old_id)})
                else:
                    new_id = _generate_unused_numeric_id(conn, "djmdLabel")
                    usn = _get_next_local_usn(conn)
                    conn.execute(
                        text("UPDATE djmdLabel SET ID = :new_id, rb_local_usn = :usn, updated_at = :now WHERE ID = :old_id;"),
                        {"new_id": new_id, "usn": usn, "now": now_utc, "old_id": str(old_id)},
                    )

                if "djmdContent" in tables and "LabelID" in c_cols:
                    conn.execute(
                        text("UPDATE djmdContent SET LabelID = :new_id, TrackInfoUpdated = COALESCE(CAST(CAST(TrackInfoUpdated AS INTEGER) + 1 AS TEXT), '1'), updated_at = :now WHERE LabelID = :old_id;"),
                        {"new_id": new_id, "now": now_utc, "old_id": str(old_id)},
                    )
                stats["labels_fixed"] += 1

    return stats


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

    # 3. Album & Album Artist
    alb_artist_id = None
    if "album_artist" in resolved_meta and "djmdArtist" in tables:
        alb_art_val = str(resolved_meta["album_artist"]).strip()
        alb_artist_id = _find_or_create_artist(conn, alb_art_val, now_utc) if alb_art_val else None

    if "album" in resolved_meta:
        alb_val = str(resolved_meta["album"]).strip()
        if "SrcAlbumName" in cols:
            update_clauses.append("SrcAlbumName = :res_album")
            params["res_album"] = alb_val
        if "AlbumID" in cols and "djmdAlbum" in tables:
            alb_id = _find_or_create_album(conn, alb_val, now_utc, artist_id=alb_artist_id) if alb_val else None
            update_clauses.append("AlbumID = :res_album_id")
            params["res_album_id"] = alb_id
    elif alb_artist_id and "AlbumID" in cols and "djmdAlbum" in tables:
        curr_alb_id = conn.execute(text("SELECT AlbumID FROM djmdContent WHERE ID = :id;"), {"id": str(content_id)}).scalar()
        if curr_alb_id:
            conn.execute(
                text("UPDATE djmdAlbum SET AlbumArtistID = :aid, updated_at = :now WHERE ID = :id;"),
                {"aid": alb_artist_id, "now": now_utc, "id": str(curr_alb_id)},
            )

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

    # Release Date
    if "release_date" in resolved_meta and "ReleaseDate" in cols:
        update_clauses.append("ReleaseDate = :res_release_date")
        params["res_release_date"] = str(resolved_meta["release_date"]).strip()

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

    # Original Artist
    if "original_artist" in resolved_meta and "OrgArtistID" in cols and "djmdArtist" in tables:
        org_val = str(resolved_meta["original_artist"]).strip()
        org_id = _find_or_create_artist(conn, org_val, now_utc) if org_val else None
        update_clauses.append("OrgArtistID = :res_org_artist_id")
        params["res_org_artist_id"] = org_id

    # Lyricist
    if "lyricist" in resolved_meta and "Lyricist" in cols:
        update_clauses.append("Lyricist = :res_lyricist")
        params["res_lyricist"] = str(resolved_meta["lyricist"]).strip()

    # Message (DeliveryComment)
    if "message" in resolved_meta and "DeliveryComment" in cols:
        update_clauses.append("DeliveryComment = :res_message")
        params["res_message"] = str(resolved_meta["message"]).strip()

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
        if "rb_local_usn" in cols:
            usn = _get_next_local_usn(conn)
            if usn:
                update_clauses.append("rb_local_usn = :meta_usn")
                params["meta_usn"] = usn
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

    moved_new_file = False
    original_new_path = resolved_new_path
    target_dest_path = resolved_new_path
    tmp_old_file_path: Optional[str] = None
    old_file_path = ""
    old_file_handled = "kept"

    try:
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

            if old_file_path and os.path.realpath(old_file_path) == os.path.realpath(resolved_new_path):
                raise ValueError(
                    "The replacement audio file is identical to the current track's audio file path."
                )

            old_bitrate = old_track.get("BitRate")
            old_size = old_track.get("FileSize")
            old_length = old_track.get("Length")
            old_play_count = int(old_track.get("DJPlayCount") or 0)
            old_stock_date = str(old_track.get("StockDate") or "")

            # Optional: Move new file into track folder
            if move_new_file_to_track_folder and old_file_path:
                old_dir = os.path.dirname(old_file_path)
                if os.path.isdir(old_dir) and os.path.realpath(old_dir) != os.path.realpath(os.path.dirname(resolved_new_path)):
                    dest_candidate = os.path.join(old_dir, new_info["file_name"])

                    # Handle destination collision if dest_candidate already exists on disk
                    if os.path.exists(dest_candidate):
                        if os.path.realpath(dest_candidate) == os.path.realpath(old_file_path):
                            if old_file_action == "keep":
                                # Keep old file in place, disambiguate replacement file in old_dir
                                base, ext = os.path.splitext(new_info["file_name"])
                                counter = 1
                                while os.path.exists(dest_candidate):
                                    dest_candidate = os.path.join(old_dir, f"{base} ({counter}){ext}")
                                    counter += 1
                            else:
                                # old_file_action is 'trash' or 'backup'
                                # Temporarily rename old file out of the way so dest_candidate is free
                                base, ext = os.path.splitext(old_file_path)
                                tmp_candidate = f"{base}.tmp_replace_{secrets.token_hex(4)}{ext}"
                                try:
                                    os.rename(old_file_path, tmp_candidate)
                                    tmp_old_file_path = tmp_candidate
                                except Exception:
                                    base_new, ext_new = os.path.splitext(new_info["file_name"])
                                    counter = 1
                                    while os.path.exists(dest_candidate):
                                        dest_candidate = os.path.join(old_dir, f"{base_new} ({counter}){ext_new}")
                                        counter += 1
                        else:
                            # Existing file is an unrelated file; disambiguate replacement file
                            base, ext = os.path.splitext(new_info["file_name"])
                            counter = 1
                            while os.path.exists(dest_candidate):
                                dest_candidate = os.path.join(old_dir, f"{base} ({counter}){ext}")
                                counter += 1

                    if dest_candidate != resolved_new_path:
                        shutil.move(resolved_new_path, dest_candidate)
                        moved_new_file = True
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

            if "rb_local_usn" in cols:
                usn = _get_next_local_usn(conn)
                if usn:
                    update_clauses.append("rb_local_usn = :track_usn")
                    params["track_usn"] = usn

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

            # Artwork replacement handling
            artwork_choice = opts.get("artwork_choice", "old")
            artwork_src = opts.get("artwork_source_path")
            if artwork_choice == "new":
                if not artwork_src or not os.path.exists(artwork_src):
                    extracted = extract_embedded_artwork(target_dest_path)
                    if extracted:
                        artwork_src = extracted[0]

                if artwork_src and os.path.exists(artwork_src):
                    track_uuid = old_track.get("UUID")
                    if not track_uuid:
                        track_uuid = str(uuid.uuid4())
                        if "UUID" in cols:
                            update_clauses.append("UUID = :new_uuid")
                            params["new_uuid"] = track_uuid

                    db_p = opts.get("db_path") or getattr(getattr(engine, "url", None), "database", None)
                    new_img_rel = install_rekordbox_artwork(track_uuid, artwork_src, db_path=db_p)
                    if new_img_rel and "ImagePath" in cols:
                        update_clauses.append("ImagePath = :new_image_path")
                        params["new_image_path"] = new_img_rel

            update_sql = f"UPDATE djmdContent SET {', '.join(update_clauses)} WHERE ID = :id;"
            conn.execute(text(update_sql), params)

            # 2b. Apply resolved metadata if provided (excluding genre, bpm, key which are not ported)
            resolved_meta = opts.get("resolved_metadata")
            if resolved_meta:
                filtered_meta = {k: v for k, v in resolved_meta.items() if k not in ("genre", "bpm", "key")}
                if filtered_meta:
                    tables = {r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table';").fetchall()}
                    _apply_resolved_metadata(conn, cols, tables, str(content_id), filtered_meta, now_utc)

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
    except Exception:
        # Rollback moved files on transaction failure
        if moved_new_file and os.path.exists(target_dest_path) and not os.path.exists(original_new_path):
            try:
                shutil.move(target_dest_path, original_new_path)
            except Exception:
                pass
        if tmp_old_file_path and os.path.exists(tmp_old_file_path) and old_file_path and not os.path.exists(old_file_path):
            try:
                os.rename(tmp_old_file_path, old_file_path)
            except Exception:
                pass
        raise

    # 4. Post-transaction: Handle old file on disk
    old_target = tmp_old_file_path if tmp_old_file_path else old_file_path
    if old_target and os.path.exists(old_target) and os.path.realpath(old_target) != os.path.realpath(target_dest_path):
        if old_file_action == "trash":
            trashed = trash_file_macos(old_target)
            old_file_handled = "trashed" if trashed else "failed_trash"
        elif old_file_action == "backup":
            bak_path = f"{old_file_path}.bak"
            counter = 1
            while os.path.exists(bak_path):
                bak_path = f"{old_file_path}.{counter}.bak"
                counter += 1
            try:
                os.rename(old_target, bak_path)
                old_file_handled = f"renamed_to_{os.path.basename(bak_path)}"
            except Exception:
                old_file_handled = "failed_rename"
        else:
            old_file_handled = "kept"
    else:
        old_file_handled = "kept"

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
            "status": "moved" if moved_new_file else "kept",
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
        tgt_plays = int(tgt_dict.get("DJPlayCount") or 0)
        src_stock_date = str(src_dict.get("StockDate") or "").strip()
        tgt_stock_date = str(tgt_dict.get("StockDate") or "").strip()

        pres_fields = opts.get("preservation_fields") or {}
        default_strategy = opts.get("preservation_strategy", "merge")

        play_mode = pres_fields.get("play_count", default_strategy)
        date_mode = pres_fields.get("date_added", default_strategy)
        hist_mode = pres_fields.get("history_sessions", default_strategy)
        pl_mode = pres_fields.get("playlists", default_strategy)
        tag_mode = pres_fields.get("tags", default_strategy)
        cue_mode = pres_fields.get("cues", default_strategy)

        # 2. Determine resolved Play Count and StockDate (Date Added)
        if play_mode == "old":
            final_plays = src_plays
        elif play_mode == "new":
            final_plays = tgt_plays
        else:  # merge
            final_plays = src_plays + tgt_plays

        if date_mode == "old":
            final_date = src_stock_date or tgt_stock_date
        elif date_mode == "new":
            final_date = tgt_stock_date or src_stock_date
        else:  # merge
            if src_stock_date and tgt_stock_date:
                final_date = min(src_stock_date, tgt_stock_date)
            else:
                final_date = src_stock_date or tgt_stock_date

        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdContent);").fetchall()}
        tables = {r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table';").fetchall()}

        set_clauses = ["DJPlayCount = :plays", "StockDate = :stock_date"]
        update_params: Dict[str, Any] = {
            "plays": final_plays,
            "stock_date": final_date,
            "target_id": str(target_content_id),
        }
        if "updated_at" in cols:
            set_clauses.append("updated_at = :updated_at")
            update_params["updated_at"] = now_utc

        conn.execute(
            text(f"""
                UPDATE djmdContent
                SET {', '.join(set_clauses)}
                WHERE ID = :target_id;
            """),
            update_params,
        )

        # 2b. Apply resolved metadata if provided (excluding genre, bpm, key which are not ported)
        resolved_meta = opts.get("resolved_metadata")
        if resolved_meta:
            filtered_meta = {k: v for k, v in resolved_meta.items() if k not in ("genre", "bpm", "key")}
            if filtered_meta:
                _apply_resolved_metadata(conn, cols, tables, str(target_content_id), filtered_meta, now_utc)

        # 2c. Artwork merge handling
        artwork_choice = opts.get("artwork_choice", "old" if default_strategy == "old" else "new")
        if artwork_choice == "old":
            src_img = src_dict.get("ImagePath")
            db_p = opts.get("db_path") or getattr(getattr(engine, "url", None), "database", None)
            resolved_src_img = resolve_artwork_path(src_img, db_path=db_p)
            if resolved_src_img and os.path.exists(resolved_src_img):
                tgt_uuid = tgt_dict.get("UUID")
                if tgt_uuid:
                    new_img_rel = install_rekordbox_artwork(tgt_uuid, resolved_src_img, db_path=db_p)
                    if new_img_rel and "ImagePath" in cols:
                        conn.execute(
                            text("UPDATE djmdContent SET ImagePath = :img WHERE ID = :id;"),
                            {"img": new_img_rel, "id": str(target_content_id)},
                        )

        # 3. Transfer or manage history entries
        hist_transferred = 0
        if "djmdSongHistory" in tables:
            if hist_mode == "merge":
                hist_res = conn.execute(
                    text("UPDATE djmdSongHistory SET ContentID = :target_id WHERE ContentID = :source_id;"),
                    {"target_id": str(target_content_id), "source_id": str(source_content_id)},
                )
                hist_transferred = hist_res.rowcount or 0
            elif hist_mode == "old":
                conn.execute(
                    text("DELETE FROM djmdSongHistory WHERE ContentID = :target_id;"),
                    {"target_id": str(target_content_id)},
                )
                hist_res = conn.execute(
                    text("UPDATE djmdSongHistory SET ContentID = :target_id WHERE ContentID = :source_id;"),
                    {"target_id": str(target_content_id), "source_id": str(source_content_id)},
                )
                hist_transferred = hist_res.rowcount or 0
            elif hist_mode == "new":
                conn.execute(
                    text("DELETE FROM djmdSongHistory WHERE ContentID = :source_id;"),
                    {"source_id": str(source_content_id)},
                )
                hist_transferred = 0

        # 4. Transfer or manage playlists
        pl_transferred = 0
        if "djmdSongPlaylist" in tables:
            if pl_mode == "merge":
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
            elif pl_mode == "old":
                conn.execute(
                    text("DELETE FROM djmdSongPlaylist WHERE ContentID = :target_id;"),
                    {"target_id": str(target_content_id)},
                )
                pl_res = conn.execute(
                    text("UPDATE djmdSongPlaylist SET ContentID = :target_id WHERE ContentID = :source_id;"),
                    {"target_id": str(target_content_id), "source_id": str(source_content_id)},
                )
                pl_transferred = pl_res.rowcount or 0
            elif pl_mode == "new":
                conn.execute(
                    text("DELETE FROM djmdSongPlaylist WHERE ContentID = :source_id;"),
                    {"source_id": str(source_content_id)},
                )
                pl_transferred = 0

        # 5. Transfer or manage tags
        tags_transferred = 0
        if "djmdSongMyTag" in tables:
            if tag_mode == "merge":
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
            elif tag_mode == "old":
                conn.execute(
                    text("DELETE FROM djmdSongMyTag WHERE ContentID = :target_id;"),
                    {"target_id": str(target_content_id)},
                )
                tag_res = conn.execute(
                    text("UPDATE djmdSongMyTag SET ContentID = :target_id WHERE ContentID = :source_id;"),
                    {"target_id": str(target_content_id), "source_id": str(source_content_id)},
                )
                tags_transferred = tag_res.rowcount or 0
            elif tag_mode == "new":
                conn.execute(
                    text("DELETE FROM djmdSongMyTag WHERE ContentID = :source_id;"),
                    {"source_id": str(source_content_id)},
                )
                tags_transferred = 0

        # 6. Transfer or manage cues
        cues_transferred = 0
        if "djmdCue" in tables:
            if cue_mode == "merge":
                tgt_cues_count = conn.execute(
                    text("SELECT count(*) FROM djmdCue WHERE ContentID = :id;"),
                    {"id": str(target_content_id)},
                ).fetchone()[0]
                if tgt_cues_count == 0:
                    cue_res = conn.execute(
                        text("UPDATE djmdCue SET ContentID = :target_id WHERE ContentID = :source_id;"),
                        {"target_id": str(target_content_id), "source_id": str(source_content_id)},
                    )
                    cues_transferred = cue_res.rowcount or 0
                else:
                    tgt_inmsec_rows = conn.execute(
                        text("SELECT InMsec FROM djmdCue WHERE ContentID = :id;"),
                        {"id": str(target_content_id)},
                    ).fetchall()
                    tgt_msecs = [int(r[0]) for r in tgt_inmsec_rows if r[0] is not None]

                    src_cue_rows = conn.execute(
                        text("SELECT ID, InMsec FROM djmdCue WHERE ContentID = :id;"),
                        {"id": str(source_content_id)},
                    ).fetchall()
                    for c_entry_id, c_inmsec in src_cue_rows:
                        is_collision = any(abs(int(c_inmsec or 0) - tm) < 100 for tm in tgt_msecs) if c_inmsec is not None else False
                        if not is_collision:
                            conn.execute(
                                text("UPDATE djmdCue SET ContentID = :target_id WHERE ID = :entry_id;"),
                                {"target_id": str(target_content_id), "entry_id": c_entry_id},
                            )
                            if c_inmsec is not None:
                                tgt_msecs.append(int(c_inmsec))
                            cues_transferred += 1
                        else:
                            conn.execute(
                                text("DELETE FROM djmdCue WHERE ID = :entry_id;"),
                                {"entry_id": c_entry_id},
                            )
            elif cue_mode == "old":
                conn.execute(
                    text("DELETE FROM djmdCue WHERE ContentID = :target_id;"),
                    {"target_id": str(target_content_id)},
                )
                cue_res = conn.execute(
                    text("UPDATE djmdCue SET ContentID = :target_id WHERE ContentID = :source_id;"),
                    {"target_id": str(target_content_id), "source_id": str(source_content_id)},
                )
                cues_transferred = cue_res.rowcount or 0
            elif cue_mode == "new":
                conn.execute(
                    text("DELETE FROM djmdCue WHERE ContentID = :source_id;"),
                    {"source_id": str(source_content_id)},
                )
                cues_transferred = 0

        # 7. Soft delete old source track
        del_clauses = ["rb_local_deleted = 1"]
        del_params: Dict[str, Any] = {"source_id": str(source_content_id)}
        if "updated_at" in cols:
            del_clauses.append("updated_at = :updated_at")
            del_params["updated_at"] = now_utc
        conn.execute(
            text(f"""
                UPDATE djmdContent
                SET {', '.join(del_clauses)}
                WHERE ID = :source_id;
            """),
            del_params,
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
        "play_count": final_plays,
        "stock_date": final_date,
        "play_count_transferred": final_plays,
        "stock_date_transferred": final_date,
        "history_transferred": hist_transferred,
        "playlists_transferred": pl_transferred,
        "tags_transferred": tags_transferred,
        "cues_transferred": cues_transferred,
        "old_file_handled": old_file_handled,
        "preservation_modes": {
            "play_count": play_mode,
            "date_added": date_mode,
            "history_sessions": hist_mode,
            "playlists": pl_mode,
            "tags": tag_mode,
            "cues": cue_mode,
        },
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
    parser.add_argument(
        "--move",
        action="store_true",
        help="Move replacement audio file into original track's folder",
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
            options={
                "old_file_action": args.action,
                "move_new_file_to_track_folder": args.move,
            },
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
