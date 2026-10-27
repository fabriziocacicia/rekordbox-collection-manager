"""Track Replacement & Metadata Diff Screen.

Provides a rich 3-column diff comparison tool for Rekordbox track replacements:
- Left: Current Track (Being Replaced)
- Center: New Replacement (Audio file on disk or newly imported AIFF track)
- Right: Final Result (The metadata that will be stored in Rekordbox after replacement)

Highlights conflicting Rekordbox 'Info' metadata, allows granular field-by-field
or batch resolution, live preview, and safe database execution.
"""

import html
import os
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QImage,
    QKeyEvent,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.engine import Engine

from rekordbox_collection_manager.gui.theme import (
    COLOR_AMBER,
    COLOR_AMBER_BG,
    COLOR_AMBER_BORDER,
    COLOR_BG_CARD,
    COLOR_BG_DARK,
    COLOR_BG_HOVER,
    COLOR_BG_INPUT,
    COLOR_BG_PANEL,
    COLOR_BLUE,
    COLOR_BLUE_BG,
    COLOR_BLUE_HOVER,
    COLOR_BLUE_PRESSED,
    COLOR_BORDER,
    COLOR_BORDER_FOCUS,
    COLOR_BORDER_LIGHT,
    COLOR_GREEN,
    COLOR_GREEN_BG,
    COLOR_GREEN_BORDER,
    COLOR_RED,
    COLOR_RED_BG,
    COLOR_RED_BORDER,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_SECONDARY,
)
from rekordbox_collection_manager.replace_file import fetch_track_full_info
from rekordbox_collection_manager.utils import (
    format_file_size,
    format_rating_stars,
    is_aiff_track,
    is_mp3_track,
    is_rekordbox_running,
    resolve_artwork_path,
)


class FieldDiff:
    """Represents a comparison between Old, New, and Result for a single metadata field."""

    def __init__(
        self,
        key: str,
        label: str,
        category: str,  # "info", "audio", "preservation"
        old_val: Any,
        new_val: Any,
        default_origin: str = "old",  # "old", "new", or "merge"
        is_editable: bool = True,
        help_tip: str = "",
        old_result_val: Any = None,
        new_result_val: Any = None,
        merge_result_val: Any = None,
        old_items: Optional[List[str]] = None,
        new_items: Optional[List[str]] = None,
        merge_items: Optional[List[str]] = None,
        old_meta: str = "",
        new_meta: str = "",
    ):
        self.key = key
        self.label = label
        self.category = category
        self.old_val = old_val
        self.new_val = new_val
        self.default_origin = default_origin
        self.is_editable = is_editable
        self.help_tip = help_tip
        self.old_meta = old_meta
        self.new_meta = new_meta

        self.old_result_val = old_result_val if old_result_val is not None else old_val
        self.new_result_val = new_result_val if new_result_val is not None else new_val
        self.merge_result_val = merge_result_val if merge_result_val is not None else new_val

        self.old_items: List[str] = [str(x).strip() for x in (old_items or []) if str(x).strip()]
        self.new_items: List[str] = [str(x).strip() for x in (new_items or []) if str(x).strip()]
        if merge_items is not None:
            self.merge_items: List[str] = [str(x).strip() for x in merge_items if str(x).strip()]
        else:
            self.merge_items = sorted(list(set(self.old_items).union(set(self.new_items))), key=lambda s: s.lower())

        # Determine conflict / match status
        if self.key == "artwork":
            old_p = str(old_val or "").strip()
            new_p = str(new_val or "").strip()
            old_exists = bool(old_p and os.path.exists(old_p))
            new_exists = bool(new_p and os.path.exists(new_p))

            if not old_exists and not new_exists:
                self.is_match = False
                self.is_both_empty = True
                self.status = "match"
                self.origin = "old"
                self.result_val = ""
            elif old_exists and not new_exists:
                self.is_match = False
                self.is_both_empty = False
                self.status = "kept_old"
                self.origin = "old"
                self.result_val = old_val
            elif not old_exists and new_exists:
                self.is_match = False
                self.is_both_empty = False
                self.status = "new_data"
                self.origin = "new"
                self.result_val = new_val
            else:
                same_file = (old_p == new_p)
                if not same_file and os.path.exists(old_p) and os.path.exists(new_p):
                    try:
                        same_file = (os.path.getsize(old_p) == os.path.getsize(new_p))
                    except Exception:
                        pass
                if same_file:
                    self.is_match = True
                    self.is_both_empty = False
                    self.status = "match"
                    self.origin = "old"
                    self.result_val = old_val
                else:
                    self.is_match = False
                    self.is_both_empty = False
                    self.status = "conflict"
                    self.origin = default_origin
                    self.result_val = old_val if default_origin == "old" else new_val
        elif self.category == "preservation":
            old_s = self._str(old_val)
            new_s = self._str(new_val)
            self.is_match = (old_s.lower() == new_s.lower()) and bool(old_s or new_s)
            self.is_both_empty = not old_s and not new_s
            self.origin = default_origin
            if default_origin == "merge":
                self.status = "merged"
                self.result_val = self.merge_result_val
            elif default_origin == "new":
                self.status = "kept_new"
                self.result_val = self.new_result_val
            else:
                self.status = "kept_old"
                self.result_val = self.old_result_val
        else:
            old_s = self._str(old_val)
            new_s = self._str(new_val)
            self.is_match = (old_s.lower() == new_s.lower()) and bool(old_s or new_s)
            self.is_both_empty = not old_s and not new_s

            if self.is_match or self.is_both_empty:
                self.status = "match"
                self.origin = "old"
                self.result_val = old_val if old_s else new_val
            elif old_s and new_s and old_s.lower() != new_s.lower():
                self.status = "conflict"
                self.origin = default_origin
                self.result_val = old_val if default_origin == "old" else new_val
            elif not old_s and new_s:
                self.status = "new_data"
                self.origin = "new"
                self.result_val = new_val
            else:  # old_s and not new_s
                self.status = "kept_old"
                self.origin = "old"
                self.result_val = old_val

    def set_origin(self, origin: str) -> None:
        self.origin = origin
        if self.key == "artwork":
            if origin == "old":
                self.result_val = self.old_val
                self.status = "match" if self.is_match else "kept_old"
            elif origin == "new":
                self.result_val = self.new_val
                self.status = "match" if self.is_match else "new_data"
        elif self.category == "preservation":
            if origin == "old":
                self.result_val = self.old_result_val
                self.status = "kept_old"
            elif origin == "new":
                self.result_val = self.new_result_val
                self.status = "kept_new"
            elif origin == "merge":
                self.result_val = self.merge_result_val
                self.status = "merged"
        else:
            if origin == "old":
                self.result_val = self.old_val
            elif origin == "new":
                self.result_val = self.new_val

    @staticmethod
    def _str(val: Any) -> str:
        if val is None:
            return ""
        if isinstance(val, float) and val == 0.0:
            return ""
        if isinstance(val, int) and val == 0:
            return ""
        return str(val).strip()


def calculate_metadata_diff(
    old_data: Dict[str, Any],
    new_data: Dict[str, Any],
    mode: str = "file",
) -> List[FieldDiff]:
    """Build complete list of field comparisons between current track and replacement."""
    fields: List[FieldDiff] = []

    # 1. Rekordbox "Info" (Track Tags & Metadata)
    # Note: Genre, BPM, and Musical Key are deliberately excluded from conflict resolution & porting.
    # The user does not use Genre, and BPM and Musical Key are rediscovered by Rekordbox analysis.
    old_art = old_data.get("artwork_path") or old_data.get("image_path") or ""
    new_art = new_data.get("artwork_path") or new_data.get("image_path") or ""
    if old_art and not os.path.isabs(old_art):
        resolved = resolve_artwork_path(old_art)
        if resolved:
            old_art = resolved
    if new_art and not os.path.isabs(new_art):
        resolved = resolve_artwork_path(new_art)
        if resolved:
            new_art = resolved

    old_res = old_data.get("artwork_resolution") or ""
    if not old_res and old_art and os.path.exists(old_art):
        try:
            q = QImage(old_art)
            if not q.isNull():
                old_res = f"{q.width()}×{q.height()}"
        except Exception:
            pass

    new_res = new_data.get("artwork_resolution") or ""
    if not new_res and new_art and os.path.exists(new_art):
        try:
            q = QImage(new_art)
            if not q.isNull():
                new_res = f"{q.width()}×{q.height()}"
        except Exception:
            pass

    info_definitions = [
        ("title", "Track Title", old_data.get("title", ""), new_data.get("title", ""), "old", "", ""),
        ("artist", "Artist", old_data.get("artist", ""), new_data.get("artist", ""), "old", "", ""),
        ("album", "Album", old_data.get("album", ""), new_data.get("album", ""), "old", "", ""),
        ("album_artist", "Album Artist", old_data.get("album_artist", ""), new_data.get("album_artist", ""), "old", "", ""),
        ("original_artist", "Original Artist", old_data.get("original_artist", ""), new_data.get("original_artist", ""), "old", "", ""),
        ("artwork", "Album Artwork", old_art, new_art, "old", old_res, new_res),
        ("rating", "Rating", old_data.get("rating") or 0, new_data.get("rating") or 0, "old", "", ""),
        ("release_year", "Release Year", old_data.get("release_year") or old_data.get("year") or 0, new_data.get("release_year") or new_data.get("year") or 0, "old", "", ""),
        ("release_date", "Release Date", old_data.get("release_date", ""), new_data.get("release_date", ""), "old", "", ""),
        ("track_no", "Track No.", old_data.get("track_no") or 0, new_data.get("track_no") or 0, "old", "", ""),
        ("disc_no", "Disc No.", old_data.get("disc_no") or 0, new_data.get("disc_no") or 0, "old", "", ""),
        ("remixer", "Remixer", old_data.get("remixer", ""), new_data.get("remixer", ""), "old", "", ""),
        ("label", "Record Label", old_data.get("label", ""), new_data.get("label", ""), "old", "", ""),
        ("mix_name", "Mix Name", old_data.get("mix_name", ""), new_data.get("mix_name", ""), "old", "", ""),
        ("composer", "Composer", old_data.get("composer", ""), new_data.get("composer", ""), "old", "", ""),
        ("lyricist", "Lyricist", old_data.get("lyricist", ""), new_data.get("lyricist", ""), "old", "", ""),
        ("comment", "Comment / Notes", old_data.get("comment", ""), new_data.get("comment", ""), "old", "", ""),
        ("message", "Message", old_data.get("message", ""), new_data.get("message", ""), "old", "", ""),
        ("color_id", "Color Code", old_data.get("color_id", ""), new_data.get("color_id", ""), "old", "", ""),
    ]

    for item in info_definitions:
        key, label, old_v, new_v, def_origin = item[:5]
        old_m = item[5] if len(item) > 5 else ""
        new_m = item[6] if len(item) > 6 else ""
        fields.append(
            FieldDiff(
                key=key,
                label=label,
                category="info",
                old_val=old_v,
                new_val=new_v,
                default_origin=def_origin,
                is_editable=True,
                old_meta=old_m,
                new_meta=new_m,
            )
        )

    # 2. Audio Technical Specifications (Always upgrades to replacement audio specs)
    old_fmt = old_data.get("format_name") or old_data.get("file_type") or "Audio"
    new_fmt = new_data.get("format_name") or new_data.get("file_type") or "AIFF"

    old_br = f"{old_data.get('bitrate', 0)} kbps" if old_data.get("bitrate") else "—"
    new_br = f"{new_data.get('bitrate', 0)} kbps" if new_data.get("bitrate") else "—"

    old_sr = f"{old_data.get('sample_rate', 0)} Hz" if old_data.get("sample_rate") else "—"
    new_sr = f"{new_data.get('sample_rate', 0)} Hz" if new_data.get("sample_rate") else "—"

    old_bd = f"{old_data.get('bit_depth', 0)}-bit" if old_data.get("bit_depth") else "—"
    new_bd = f"{new_data.get('bit_depth', 0)}-bit" if new_data.get("bit_depth") else "—"

    old_dur = old_data.get("duration_str") or "--:--"
    new_dur = new_data.get("duration_str") or "--:--"

    old_sz = format_file_size(old_data.get("file_size", 0))
    new_sz = format_file_size(new_data.get("file_size", 0))

    old_path = old_data.get("file_path") or old_data.get("folder_path") or ""
    new_path = new_data.get("file_path") or new_data.get("folder_path") or ""

    audio_specs = [
        ("file_type", "Audio Format", old_fmt, new_fmt),
        ("bitrate", "Bitrate", old_br, new_br),
        ("sample_rate", "Sample Rate", old_sr, new_sr),
        ("bit_depth", "Bit Depth", old_bd, new_bd),
        ("duration", "Duration", old_dur, new_dur),
        ("file_size", "File Size", old_sz, new_sz),
        ("file_path", "Audio Location", old_path, new_path),
    ]

    for key, label, old_v, new_v in audio_specs:
        f = FieldDiff(
            key=key,
            label=label,
            category="audio",
            old_val=old_v,
            new_val=new_v,
            default_origin="new",
            is_editable=False,
            help_tip="Audio specs are automatically upgraded to the replacement audio file.",
        )
        f.result_val = new_v
        f.origin = "new"
        fields.append(f)

    # 3. Collection Preservation
    old_plays_num = int(old_data.get('play_count', 0) or 0)
    new_plays_num = int(new_data.get('play_count', 0) or 0)
    old_plays = f"{old_plays_num} {'play' if old_plays_num == 1 else 'plays'}"
    new_plays = f"{new_plays_num} {'play' if new_plays_num == 1 else 'plays'}" if mode == "merge" else "0 plays (New File)"

    old_date = old_data.get("date_added") or "—"
    new_date = (new_data.get("date_added") or "—") if mode == "merge" else "Today (New File)"

    old_h_cnt = old_data.get("history_count")
    if old_h_cnt is None:
        old_h_cnt = len(old_data.get("history_plays", []))
    new_h_cnt = new_data.get("history_count")
    if new_h_cnt is None:
        new_h_cnt = len(new_data.get("history_plays", [])) if mode == "merge" else 0

    old_p_cnt = old_data.get("playlist_count")
    if old_p_cnt is None:
        old_p_cnt = len(old_data.get("playlists", []))
    new_p_cnt = new_data.get("playlist_count")
    if new_p_cnt is None:
        new_p_cnt = len(new_data.get("playlists", [])) if mode == "merge" else 0

    old_t_cnt = old_data.get("tag_count")
    if old_t_cnt is None:
        old_t_cnt = len(old_data.get("tags", []))
    new_t_cnt = new_data.get("tag_count")
    if new_t_cnt is None:
        new_t_cnt = len(new_data.get("tags", [])) if mode == "merge" else 0

    old_c_cnt = old_data.get("cue_count", 0)
    new_c_cnt = new_data.get("cue_count", 0) if mode == "merge" else 0

    h_unit = "set" if old_h_cnt == 1 else "sets"
    p_unit = "playlist" if old_p_cnt == 1 else "playlists"
    t_unit = "tag" if old_t_cnt == 1 else "tags"
    c_unit = "cue" if old_c_cnt == 1 else "cues"

    new_h_unit = "set" if new_h_cnt == 1 else "sets"
    new_p_unit = "playlist" if new_p_cnt == 1 else "playlists"
    new_t_unit = "tag" if new_t_cnt == 1 else "tags"
    new_c_unit = "cue" if new_c_cnt == 1 else "cues"

    old_h_disp = f"{old_h_cnt} {h_unit}"
    new_h_disp = f"{new_h_cnt} {new_h_unit}" if mode == "merge" else "0 sets"

    old_p_disp = f"{old_p_cnt} {p_unit}"
    new_p_disp = f"{new_p_cnt} {new_p_unit}" if mode == "merge" else "0 playlists"

    old_t_disp = f"{old_t_cnt} {t_unit}"
    new_t_disp = f"{new_t_cnt} {new_t_unit}" if mode == "merge" else "0 tags"

    old_c_disp = f"{old_c_cnt} {c_unit}"
    new_c_disp = f"{new_c_cnt} {new_c_unit}" if mode == "merge" else "Re-analyzed"

    # Define results for each origin choice: Old, New/Target, and Merge
    sum_plays = old_plays_num + new_plays_num
    pc_old_res = f"Preserved ({old_plays})"
    pc_new_res = f"Preserved ({new_plays})"
    pc_merge_res = f"{sum_plays} {'play' if sum_plays == 1 else 'plays'} ({old_plays_num} old + {new_plays_num} target)"

    da_old_res = f"Preserved ({old_date})"
    da_new_res = f"Preserved ({new_date})"
    if old_date and old_date != "—" and new_date and new_date != "—":
        earliest_date = min(str(old_date), str(new_date))
    else:
        earliest_date = old_date if (old_date and old_date != "—") else new_date
    da_merge_res = f"{earliest_date} (Earliest Added)"

    hs_old_res = f"Preserved ({old_h_disp})" if old_h_cnt > 0 else "0 sets (Current)"
    hs_new_res = f"Preserved ({new_h_disp})" if new_h_cnt > 0 else "0 sets (Target)"
    tot_h = old_h_cnt + new_h_cnt
    hs_merge_res = f"{tot_h} {'set' if tot_h == 1 else 'sets'} (Combined all history plays)"

    pl_old_res = f"Preserved ({old_p_disp})" if old_p_cnt > 0 else "0 playlists (Current)"
    pl_new_res = f"Preserved ({new_p_disp})" if new_p_cnt > 0 else "0 playlists (Target)"
    pl_old_items = [str(x).strip() for x in (old_data.get("playlists") or []) if str(x).strip()]
    pl_new_items = [str(x).strip() for x in (new_data.get("playlists") or []) if str(x).strip()] if mode == "merge" else []
    pl_merge_items = sorted(list(set(pl_old_items).union(set(pl_new_items))), key=lambda s: s.lower())
    if pl_old_items or pl_new_items:
        pl_comb_cnt = len(pl_merge_items)
    else:
        pl_comb_cnt = old_p_cnt + new_p_cnt
    pl_merge_res = f"{pl_comb_cnt} {'playlist' if pl_comb_cnt == 1 else 'playlists'} (Combined memberships)"

    tag_old_res = f"Preserved ({old_t_disp})" if old_t_cnt > 0 else "0 tags (Current)"
    tag_new_res = f"Preserved ({new_t_disp})" if new_t_cnt > 0 else "0 tags (Target)"
    t_old_items = [str(x).strip() for x in (old_data.get("tags") or []) if str(x).strip()]
    t_new_items = [str(x).strip() for x in (new_data.get("tags") or []) if str(x).strip()] if mode == "merge" else []
    t_merge_items = sorted(list(set(t_old_items).union(set(t_new_items))), key=lambda s: s.lower())
    if t_old_items or t_new_items:
        t_comb_cnt = len(t_merge_items)
    else:
        t_comb_cnt = old_t_cnt + new_t_cnt
    tag_merge_res = f"{t_comb_cnt} {'tag' if t_comb_cnt == 1 else 'tags'} (Combined tags)"

    h_old_items = [str(x).strip() for x in (old_data.get("history_sessions") or old_data.get("history_plays") or []) if str(x).strip()]
    h_new_items = [str(x).strip() for x in (new_data.get("history_sessions") or new_data.get("history_plays") or []) if str(x).strip()] if mode == "merge" else []
    h_merge_items = sorted(list(set(h_old_items).union(set(h_new_items))), key=lambda s: s.lower())

    cue_old_res = f"Preserved ({old_c_disp})" if old_c_cnt > 0 else "All cue points from Current"
    cue_new_res = f"Preserved ({new_c_disp})" if new_c_disp and new_c_cnt > 0 else "All cue points from Target"
    tot_cues = max(old_c_cnt, new_c_cnt) if (old_c_cnt == 0 or new_c_cnt == 0) else (old_c_cnt + new_c_cnt)
    cue_merge_res = f"{tot_cues} {'cue' if tot_cues == 1 else 'cues'} (Combined cue points)"

    preservation_defs = [
        ("play_count", "Play Count", old_plays, new_plays, pc_old_res, pc_new_res, pc_merge_res, [], [], []),
        ("date_added", "Date Added (StockDate)", old_date, new_date, da_old_res, da_new_res, da_merge_res, [], [], []),
        ("history_sessions", "History Sets Played", old_h_disp, new_h_disp, hs_old_res, hs_new_res, hs_merge_res, h_old_items, h_new_items, h_merge_items),
        ("playlists", "Playlists", old_p_disp, new_p_disp, pl_old_res, pl_new_res, pl_merge_res, pl_old_items, pl_new_items, pl_merge_items),
        ("tags", "My Tags", old_t_disp, new_t_disp, tag_old_res, tag_new_res, tag_merge_res, t_old_items, t_new_items, t_merge_items),
        ("cues", "Cue Points & Hot Cues", old_c_disp, new_c_disp, cue_old_res, cue_new_res, cue_merge_res, [], [], []),
    ]

    is_merge_mode = (mode == "merge")
    default_origin = "merge" if is_merge_mode else "old"

    for key, label, old_v, new_v, old_r, new_r, merge_r, old_its, new_its, merge_its in preservation_defs:
        f = FieldDiff(
            key=key,
            label=label,
            category="preservation",
            old_val=old_v,
            new_val=new_v,
            default_origin=default_origin,
            is_editable=is_merge_mode,
            help_tip="Collection metrics and playlist/tag relationships.",
            old_result_val=old_r,
            new_result_val=new_r,
            merge_result_val=merge_r,
            old_items=old_its,
            new_items=new_its,
            merge_items=merge_its,
        )
        fields.append(f)

    return fields


class ItemsComparisonDialog(QDialog):
    """Side-by-side comparison modal dialog for Playlists, My Tags, or History Sessions."""

    def __init__(
        self,
        diff: FieldDiff,
        parent: Optional[QWidget] = None,
        on_origin_selected: Optional[Callable[[str], None]] = None,
    ):
        super().__init__(parent)
        self.diff = diff
        self.on_origin_selected = on_origin_selected
        self.col_frames: Dict[str, QFrame] = {}
        self._init_ui()

    def _init_ui(self) -> None:
        unit_name = self.diff.label
        self.setWindowTitle(f"Compare {unit_name} — Current vs. Target Track")
        self.resize(860, 520)
        self.setMinimumSize(700, 420)
        self.setStyleSheet(f"QDialog {{ background-color: {COLOR_BG_DARK}; color: {COLOR_TEXT_PRIMARY}; }}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)

        # Header
        header = QFrame()
        header.setStyleSheet("background: transparent; border: none;")
        hl = QVBoxLayout(header)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(4)

        icon = "📁" if self.diff.key == "playlists" else ("🏷️" if self.diff.key == "tags" else "🕒")
        title_lbl = QLabel(f"{icon} Compare {unit_name}")
        title_lbl.setStyleSheet("font-size: 16px; font-weight: 700; color: #ffffff;")
        hl.addWidget(title_lbl)

        sub_lbl = QLabel(
            f"Review items assigned to both tracks. You can choose to keep only Current track items, only Target track items, or merge both collections together."
        )
        sub_lbl.setStyleSheet(f"font-size: 12px; color: {COLOR_TEXT_MUTED};")
        sub_lbl.setWordWrap(True)
        hl.addWidget(sub_lbl)
        layout.addWidget(header)

        # Columns container (3 columns)
        cols_container = QWidget()
        cols_layout = QHBoxLayout(cols_container)
        cols_layout.setContentsMargins(0, 0, 0, 0)
        cols_layout.setSpacing(12)

        old_items = self.diff.old_items
        new_items = self.diff.new_items
        merge_items = self.diff.merge_items

        # Column 1: Current Track (Old)
        self.col_frames["old"] = self._create_column_card(
            col_id="old",
            title="Current Track (Old)",
            count=len(old_items),
            items=[(it, None) for it in old_items],
            accent_color=COLOR_BLUE,
            accent_bg=COLOR_BLUE_BG,
            btn_text="◀ Keep Current Only",
            btn_action=lambda: self._select_strategy("old"),
        )
        cols_layout.addWidget(self.col_frames["old"])

        # Column 2: Combined / Merged
        old_set = set(old_items)
        new_set = set(new_items)
        merged_annotated: List[Tuple[str, Optional[str]]] = []
        for it in merge_items:
            in_old = it in old_set
            in_new = it in new_set
            if in_old and in_new:
                origin_badge = "Both"
            elif in_old:
                origin_badge = "Current"
            else:
                origin_badge = "Target"
            merged_annotated.append((it, origin_badge))

        self.col_frames["merge"] = self._create_column_card(
            col_id="merge",
            title="Merged Result",
            count=len(merge_items),
            items=merged_annotated,
            accent_color="#c084fc",
            accent_bg="#2e1065",
            btn_text="🔀 Merge Both Collections",
            btn_action=lambda: self._select_strategy("merge"),
        )
        cols_layout.addWidget(self.col_frames["merge"])

        # Column 3: Target Track (New)
        self.col_frames["new"] = self._create_column_card(
            col_id="new",
            title="Target Track (New)",
            count=len(new_items),
            items=[(it, None) for it in new_items],
            accent_color=COLOR_GREEN,
            accent_bg=COLOR_GREEN_BG,
            btn_text="Keep Target Only ▶",
            btn_action=lambda: self._select_strategy("new"),
        )
        cols_layout.addWidget(self.col_frames["new"])

        layout.addWidget(cols_container, stretch=1)

        # Bottom Bar: Close button & status text
        bottom_bar = QFrame()
        b_layout = QHBoxLayout(bottom_bar)
        b_layout.setContentsMargins(0, 0, 0, 0)

        self.status_msg = QLabel("")
        self.status_msg.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px;")
        b_layout.addWidget(self.status_msg)
        b_layout.addStretch(1)

        btn_close = QPushButton("Done / Close")
        btn_close.setFixedSize(110, 32)
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setStyleSheet(
            f"QPushButton {{ background-color: {COLOR_BG_CARD}; color: {COLOR_TEXT_PRIMARY}; border: 1px solid {COLOR_BORDER}; border-radius: 5px; font-weight: 600; font-size: 12px; }} "
            f"QPushButton:hover {{ background-color: {COLOR_BG_HOVER}; border-color: {COLOR_BORDER_LIGHT}; }}"
        )
        btn_close.clicked.connect(self.accept)
        b_layout.addWidget(btn_close)
        layout.addWidget(bottom_bar)

        self._update_card_highlights()

    def _create_column_card(
        self,
        col_id: str,
        title: str,
        count: int,
        items: List[Tuple[str, Optional[str]]],
        accent_color: str,
        accent_bg: str,
        btn_text: str,
        btn_action: Any,
    ) -> QFrame:
        card = QFrame()
        card.setObjectName(f"colCard_{col_id}")
        card_l = QVBoxLayout(card)
        card_l.setContentsMargins(12, 12, 12, 12)
        card_l.setSpacing(10)

        # Header row
        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        t_lbl = QLabel(title)
        t_lbl.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {accent_color};")
        hdr.addWidget(t_lbl)
        hdr.addStretch(1)

        badge = QLabel(f"{count} {'item' if count == 1 else 'items'}")
        badge.setStyleSheet(f"font-size: 10px; font-weight: 700; background-color: {accent_bg}; color: {accent_color}; border: 1px solid {accent_color}; border-radius: 4px; padding: 1px 6px;")
        hdr.addWidget(badge)
        card_l.addLayout(hdr)

        # Action Button at top of card
        btn = QPushButton(btn_text)
        btn.setFixedHeight(28)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setEnabled(self.diff.is_editable)
        btn.setObjectName(f"btnAction_{col_id}")
        btn.clicked.connect(btn_action)
        card_l.addWidget(btn)

        # Active state indicator
        active_lbl = QLabel("✓ CURRENTLY SELECTED" if self.diff.origin == col_id else "")
        active_lbl.setObjectName(f"activeLbl_{col_id}")
        active_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        active_lbl.setStyleSheet(f"font-size: 10px; font-weight: 700; color: {accent_color}; height: 14px;")
        card_l.addWidget(active_lbl)

        # Scrollable items area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background-color: #0f121d; border: 1px solid #1e2233; border-radius: 6px; } QScrollBar:vertical { width: 8px; }")

        items_widget = QWidget()
        items_widget.setStyleSheet("background-color: transparent;")
        items_layout = QVBoxLayout(items_widget)
        items_layout.setContentsMargins(8, 8, 8, 8)
        items_layout.setSpacing(6)

        if not items:
            none_lbl = QLabel("(No items assigned)")
            none_lbl.setStyleSheet("color: #64748b; font-style: italic; font-size: 11px; padding: 10px;")
            none_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            items_layout.addWidget(none_lbl)
        else:
            for item_name, origin_badge in items:
                row_f = QFrame()
                row_f.setStyleSheet("background-color: #171b29; border: 1px solid #23283b; border-radius: 4px;")
                row_l = QHBoxLayout(row_f)
                row_l.setContentsMargins(8, 5, 8, 5)
                row_l.setSpacing(6)

                it_lbl = QLabel(item_name)
                it_lbl.setStyleSheet("color: #f1f5f9; font-size: 11px; font-weight: 500;")
                it_lbl.setWordWrap(True)
                row_l.addWidget(it_lbl, stretch=1)

                if origin_badge:
                    b_color = "#d8b4fe" if origin_badge == "Both" else ("#7dd3fc" if origin_badge == "Current" else "#86efac")
                    b_bg = "#4c1d95" if origin_badge == "Both" else ("#0369a1" if origin_badge == "Current" else "#14532d")
                    b_lbl = QLabel(origin_badge)
                    b_lbl.setStyleSheet(f"font-size: 9px; font-weight: 700; background-color: {b_bg}; color: {b_color}; border-radius: 3px; padding: 1px 5px;")
                    row_l.addWidget(b_lbl)

                items_layout.addWidget(row_f)

        items_layout.addStretch(1)
        scroll.setWidget(items_widget)
        card_l.addWidget(scroll, stretch=1)

        return card

    def _select_strategy(self, origin: str) -> None:
        if not self.diff.is_editable:
            return
        self.diff.origin = origin
        if self.on_origin_selected:
            self.on_origin_selected(origin)
        self._update_card_highlights()
        origin_str = "Current Track Only" if origin == "old" else ("Target Track Only" if origin == "new" else "Both Tracks (Merged)")
        self.status_msg.setText(f"✓ Selection updated: {origin_str}")

    def _update_card_highlights(self) -> None:
        for origin_key, card in self.col_frames.items():
            is_active = (self.diff.origin == origin_key)
            if origin_key == "old":
                border_color = COLOR_BLUE if is_active else COLOR_BORDER
                btn_bg = COLOR_BLUE if is_active else COLOR_BG_CARD
                btn_color = "#ffffff" if is_active else COLOR_BLUE
                active_text = "✓ CURRENTLY SELECTED" if is_active else ""
            elif origin_key == "new":
                border_color = COLOR_GREEN if is_active else COLOR_BORDER
                btn_bg = COLOR_GREEN if is_active else COLOR_BG_CARD
                btn_color = "#ffffff" if is_active else COLOR_GREEN
                active_text = "✓ CURRENTLY SELECTED" if is_active else ""
            else:  # merge
                border_color = "#8b5cf6" if is_active else COLOR_BORDER
                btn_bg = "#7c3aed" if is_active else COLOR_BG_CARD
                btn_color = "#ffffff" if is_active else "#c084fc"
                active_text = "✓ CURRENTLY SELECTED" if is_active else ""

            card.setStyleSheet(
                f"QFrame#colCard_{origin_key} {{ background-color: {COLOR_BG_CARD}; border: {2 if is_active else 1}px solid {border_color}; border-radius: 8px; }}"
            )

            btn = card.findChild(QPushButton, f"btnAction_{origin_key}")
            if btn:
                btn.setStyleSheet(
                    f"QPushButton {{ background-color: {btn_bg}; color: {btn_color}; border: 1px solid {border_color}; border-radius: 4px; font-weight: bold; font-size: 11px; }} "
                    f"QPushButton:hover {{ opacity: 0.9; }}"
                )

            lbl = card.findChild(QLabel, f"activeLbl_{origin_key}")
            if lbl:
                lbl.setText(active_text)


class DiffRowWidget(QFrame):
    """Interactive single row for comparing and resolving one metadata field."""

    changed = Signal()

    def __init__(self, diff: FieldDiff, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.diff = diff
        self._init_ui()
        self._update_styling()

    def _init_ui(self) -> None:
        self.setObjectName("diffRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)

        # 1. Field Label & Status Badge Column
        label_holder = QWidget()
        label_holder.setFixedWidth(170)
        label_col = QVBoxLayout(label_holder)
        label_col.setContentsMargins(0, 0, 0, 0)
        label_col.setSpacing(2)
        label_col.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self.label_title = QLabel(self.diff.label)
        self.label_title.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-size: 12px; font-weight: 600;")
        label_col.addWidget(self.label_title)

        self.badge_status = QLabel("")
        self.badge_status.setStyleSheet("font-size: 10px; font-weight: 700; border-radius: 3px; padding: 1px 5px;")
        label_col.addWidget(self.badge_status)
        layout.addWidget(label_holder)
        self.label_col_layout = label_col

        # 2. Left Column: Old Value (Current)
        self.old_frame = QFrame()
        self.old_frame.setObjectName("oldCell")
        self.old_frame.setCursor(Qt.CursorShape.PointingHandCursor if self.diff.is_editable else Qt.CursorShape.ArrowCursor)
        old_layout = QHBoxLayout(self.old_frame)
        old_layout.setContentsMargins(8, 6, 8, 6)
        old_layout.setSpacing(8)

        if self.diff.key == "artwork":
            self.old_art_thumb = QLabel()
            self.old_art_thumb.setFixedSize(40, 40)
            self.old_art_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.old_art_thumb.setStyleSheet("background-color: #121216; border-radius: 4px; font-size: 18px;")
            old_p = str(self.diff.old_val or "")
            if old_p and os.path.exists(old_p):
                pix = QPixmap(old_p).scaled(40, 40, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                self.old_art_thumb.setPixmap(pix)
            else:
                self.old_art_thumb.setText("🎵")
            old_layout.addWidget(self.old_art_thumb)

            old_text_col = QVBoxLayout()
            old_text_col.setSpacing(2)
            lbl_top = QLabel("Current Artwork")
            lbl_top.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-size: 12px; font-weight: 600;")
            old_text_col.addWidget(lbl_top)

            res_txt = getattr(self.diff, "old_meta", "") or ("Present" if (old_p and os.path.exists(old_p)) else "No Artwork")
            lbl_sub = QLabel(res_txt)
            lbl_sub.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 10px;")
            old_text_col.addWidget(lbl_sub)
            old_layout.addLayout(old_text_col, stretch=1)
        else:
            self.old_text_label = QLabel(self._format_display(self.diff.old_val))
            self.old_text_label.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-size: 12px;")
            self.old_text_label.setWordWrap(True)
            old_layout.addWidget(self.old_text_label, stretch=1)

        if self.diff.is_editable:
            self.btn_use_old = QPushButton("◀ Use")
            self.btn_use_old.setFixedSize(52, 22)
            self.btn_use_old.setCursor(Qt.CursorShape.PointingHandCursor)
            self.btn_use_old.setToolTip("Select Current (Old) value")
            self.btn_use_old.setStyleSheet(
                f"QPushButton {{ background-color: {COLOR_BG_CARD}; color: {COLOR_BLUE}; border: 1px solid {COLOR_BORDER_LIGHT}; border-radius: 4px; font-size: 10px; font-weight: bold; padding: 0px; }} "
                f"QPushButton:hover {{ background-color: {COLOR_BLUE_BG}; border-color: {COLOR_BLUE}; color: #ffffff; }}"
            )
            self.btn_use_old.clicked.connect(self._select_old)
            old_layout.addWidget(self.btn_use_old)

        layout.addWidget(self.old_frame, stretch=1)

        # 3. Center Column: New Value (Replacement)
        self.new_frame = QFrame()
        self.new_frame.setObjectName("newCell")
        self.new_frame.setCursor(Qt.CursorShape.PointingHandCursor if self.diff.is_editable else Qt.CursorShape.ArrowCursor)
        new_layout = QHBoxLayout(self.new_frame)
        new_layout.setContentsMargins(8, 6, 8, 6)
        new_layout.setSpacing(8)

        if self.diff.key == "artwork":
            self.new_art_thumb = QLabel()
            self.new_art_thumb.setFixedSize(40, 40)
            self.new_art_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.new_art_thumb.setStyleSheet("background-color: #121216; border-radius: 4px; font-size: 18px;")
            new_p = str(self.diff.new_val or "")
            has_new = bool(new_p and os.path.exists(new_p))
            if has_new:
                pix = QPixmap(new_p).scaled(40, 40, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                self.new_art_thumb.setPixmap(pix)
            else:
                self.new_art_thumb.setText("📁")
            new_layout.addWidget(self.new_art_thumb)

            new_text_col = QVBoxLayout()
            new_text_col.setSpacing(2)
            lbl_top_n = QLabel("Replacement Cover")
            lbl_top_n.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-size: 12px; font-weight: 600;")
            new_text_col.addWidget(lbl_top_n)

            res_txt_n = getattr(self.diff, "new_meta", "") or ("Present" if has_new else "No Embedded Art")
            lbl_sub_n = QLabel(res_txt_n)
            lbl_sub_n.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 10px;")
            new_text_col.addWidget(lbl_sub_n)
            new_layout.addLayout(new_text_col, stretch=1)
        else:
            self.new_text_label = QLabel(self._format_display(self.diff.new_val))
            self.new_text_label.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-size: 12px;")
            self.new_text_label.setWordWrap(True)
            new_layout.addWidget(self.new_text_label, stretch=1)

        if self.diff.is_editable:
            self.btn_use_new = QPushButton("Use ▶")
            self.btn_use_new.setFixedSize(52, 22)
            self.btn_use_new.setCursor(Qt.CursorShape.PointingHandCursor)
            self.btn_use_new.setToolTip("Select New value")
            self.btn_use_new.setStyleSheet(
                f"QPushButton {{ background-color: {COLOR_BG_CARD}; color: {COLOR_GREEN}; border: 1px solid {COLOR_BORDER_LIGHT}; border-radius: 4px; font-size: 10px; font-weight: bold; padding: 0px; }} "
                f"QPushButton:hover {{ background-color: {COLOR_GREEN_BG}; border-color: {COLOR_GREEN}; color: #ffffff; }}"
            )
            if self.diff.key == "artwork" and not (self.diff.new_val and os.path.exists(str(self.diff.new_val))):
                self.btn_use_new.setEnabled(False)
                self.btn_use_new.setToolTip("Replacement audio file has no embedded artwork")
            self.btn_use_new.clicked.connect(self._select_new)
            new_layout.addWidget(self.btn_use_new)

        layout.addWidget(self.new_frame, stretch=1)

        # 4. Right Column: Result (Final Applied Value)
        self.result_frame = QFrame()
        self.result_frame.setObjectName("resultCell")
        res_layout = QHBoxLayout(self.result_frame)
        res_layout.setContentsMargins(8, 4, 8, 4)
        res_layout.setSpacing(8)

        # Origin pill: [OLD] / [NEW] / [CUSTOM] / [MERGED]
        self.origin_pill = QLabel("OLD")
        self.origin_pill.setFixedHeight(18)
        self.origin_pill.setStyleSheet("font-size: 9px; font-weight: bold; border-radius: 3px; padding: 1px 5px;")
        res_layout.addWidget(self.origin_pill)

        if self.diff.key == "artwork":
            self.res_art_thumb = QLabel()
            self.res_art_thumb.setFixedSize(40, 40)
            self.res_art_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.res_art_thumb.setStyleSheet("background-color: #121216; border-radius: 4px; font-size: 18px;")
            res_layout.addWidget(self.res_art_thumb)

            res_text_col = QVBoxLayout()
            res_text_col.setSpacing(2)
            self.res_art_title = QLabel("Using Current Artwork")
            self.res_art_title.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-size: 12px; font-weight: bold;")
            res_text_col.addWidget(self.res_art_title)

            self.res_art_sub = QLabel("")
            self.res_art_sub.setStyleSheet(f"color: {COLOR_GREEN}; font-size: 10px; font-weight: 600;")
            res_text_col.addWidget(self.res_art_sub)
            res_layout.addLayout(res_text_col, stretch=1)
        elif self.diff.is_editable and self.diff.category == "info":
            self.result_input = QLineEdit()
            self.result_input.setText(str(self.diff.result_val) if self.diff.result_val is not None else "")
            self.result_input.setStyleSheet(
                f"QLineEdit {{ background-color: {COLOR_BG_INPUT}; color: {COLOR_TEXT_PRIMARY}; border: 1px solid {COLOR_BORDER}; border-radius: 4px; padding: 4px 8px; font-size: 12px; }} "
                f"QLineEdit:focus {{ border-color: {COLOR_BORDER_FOCUS}; background-color: {COLOR_BG_DARK}; }}"
            )
            self.result_input.textChanged.connect(self._on_result_text_edited)
            res_layout.addWidget(self.result_input, stretch=1)
        else:
            self.result_label = QLabel(self._format_display(self.diff.result_val))
            self.result_label.setStyleSheet(f"color: {COLOR_GREEN}; font-size: 12px; font-weight: 600;")
            self.result_label.setWordWrap(True)
            res_layout.addWidget(self.result_label, stretch=1)

            if self.diff.category == "preservation" and self.diff.is_editable:
                self.btn_use_merge = QPushButton("🔀 Merge")
                self.btn_use_merge.setFixedSize(68, 22)
                self.btn_use_merge.setCursor(Qt.CursorShape.PointingHandCursor)
                self.btn_use_merge.setToolTip("Combine and merge data from both tracks")
                self.btn_use_merge.clicked.connect(self._select_merge)
                res_layout.addWidget(self.btn_use_merge)

        layout.addWidget(self.result_frame, stretch=1)

        # Connect cell click events
        if self.diff.is_editable:
            self.old_frame.mousePressEvent = lambda e: self._select_old()
            if hasattr(self, "old_text_label"):
                self.old_text_label.mousePressEvent = lambda e: self._select_old()
            if hasattr(self, "old_art_thumb"):
                self.old_art_thumb.mousePressEvent = lambda e: self._select_old()

            self.new_frame.mousePressEvent = lambda e: self._select_new()
            if hasattr(self, "new_text_label"):
                self.new_text_label.mousePressEvent = lambda e: self._select_new()
            if hasattr(self, "new_art_thumb"):
                self.new_art_thumb.mousePressEvent = lambda e: self._select_new()
            if self.diff.category == "preservation":
                self.result_frame.setCursor(Qt.CursorShape.PointingHandCursor)
                self.result_frame.mousePressEvent = lambda e: self._select_merge()
                if hasattr(self, "result_label"):
                    self.result_label.setCursor(Qt.CursorShape.PointingHandCursor)
                    self.result_label.mousePressEvent = lambda e: self._select_merge()

        self._update_tooltips()

    def _get_result_items(self) -> List[str]:
        if self.diff.origin == "old":
            return self.diff.old_items
        elif self.diff.origin == "new":
            return self.diff.new_items
        else:
            return self.diff.merge_items

    def _build_tooltip(self, origin_type: str) -> str:
        """Build rich HTML tooltip displaying old/new/merged items and active choices."""
        if self.diff.category == "preservation":
            icon = "📁" if self.diff.key == "playlists" else ("🏷️" if self.diff.key == "tags" else "🕒")
            unit_name = self.diff.label
            has_items = bool(self.diff.old_items or self.diff.new_items or self.diff.merge_items)

            if origin_type == "old":
                if has_items:
                    items = self.diff.old_items
                    items_html = ""
                    if not items:
                        items_html = "<div style='color: #94a3b8; font-style: italic;'>None (0 items assigned)</div>"
                    else:
                        limit = 20
                        shown = items[:limit]
                        list_rows = "".join(f"<div style='margin: 1px 0;'>• {html.escape(it)}</div>" for it in shown)
                        more_cnt = len(items) - limit
                        more_txt = f"<div style='color: #94a3b8; font-size: 10px; margin-top: 3px;'>... and {more_cnt} more</div>" if more_cnt > 0 else ""
                        items_html = f"<div style='margin-left: 4px;'>{list_rows}{more_txt}</div>"

                    click_hint = ""
                    if self.diff.is_editable:
                        click_hint = (
                            "<hr style='border: 0; border-top: 1px solid #334155; margin: 6px 0;'/>"
                            f"<span style='color: #38bdf8; font-size: 11px;'>💡 Click cell or [◀ Use] to keep only Current track {unit_name.lower()}</span>"
                        )
                    return (
                        f"<div style='font-size: 12px; line-height: 1.35;'>"
                        f"<b style='color: #38bdf8;'>{icon} Current Track {unit_name} ({len(items)}):</b>"
                        f"<div style='margin-top: 4px;'>{items_html}</div>"
                        f"{click_hint}"
                        f"</div>"
                    )
                else:
                    hint = f"\n\nClick to keep Current (Old) {unit_name.lower()}" if self.diff.is_editable else ""
                    return f"Current Track: {self.diff.old_val}{hint}"

            elif origin_type == "new":
                if has_items:
                    items = self.diff.new_items
                    items_html = ""
                    if not items:
                        items_html = "<div style='color: #94a3b8; font-style: italic;'>None (0 items assigned)</div>"
                    else:
                        limit = 20
                        shown = items[:limit]
                        list_rows = "".join(f"<div style='margin: 1px 0;'>• {html.escape(it)}</div>" for it in shown)
                        more_cnt = len(items) - limit
                        more_txt = f"<div style='color: #94a3b8; font-size: 10px; margin-top: 3px;'>... and {more_cnt} more</div>" if more_cnt > 0 else ""
                        items_html = f"<div style='margin-left: 4px;'>{list_rows}{more_txt}</div>"

                    click_hint = ""
                    if self.diff.is_editable:
                        click_hint = (
                            "<hr style='border: 0; border-top: 1px solid #334155; margin: 6px 0;'/>"
                            f"<span style='color: #4ade80; font-size: 11px;'>💡 Click cell or [Use ▶] to keep only Target track {unit_name.lower()}</span>"
                        )
                    return (
                        f"<div style='font-size: 12px; line-height: 1.35;'>"
                        f"<b style='color: #4ade80;'>{icon} Target Track {unit_name} ({len(items)}):</b>"
                        f"<div style='margin-top: 4px;'>{items_html}</div>"
                        f"{click_hint}"
                        f"</div>"
                    )
                else:
                    hint = f"\n\nClick to keep Target (New) {unit_name.lower()}" if self.diff.is_editable else ""
                    return f"Target Track: {self.diff.new_val}{hint}"

            else:  # result
                if has_items:
                    if self.diff.origin == "merge":
                        old_s = set(self.diff.old_items)
                        new_s = set(self.diff.new_items)
                        limit = 20
                        shown = self.diff.merge_items[:limit]
                        rows = []
                        for it in shown:
                            in_old = it in old_s
                            in_new = it in new_s
                            if in_old and in_new:
                                badge = "<span style='color: #d8b4fe; font-size: 10px;'> (in both)</span>"
                            elif in_old:
                                badge = "<span style='color: #7dd3fc; font-size: 10px;'> (from Current)</span>"
                            else:
                                badge = "<span style='color: #86efac; font-size: 10px;'> (from Target)</span>"
                            rows.append(f"<div style='margin: 1px 0;'>• <b>{html.escape(it)}</b>{badge}</div>")
                        more_cnt = len(self.diff.merge_items) - limit
                        more_txt = f"<div style='color: #94a3b8; font-size: 10px; margin-top: 3px;'>... and {more_cnt} more</div>" if more_cnt > 0 else ""
                        items_html = f"<div style='margin-left: 4px;'>{''.join(rows)}{more_txt}</div>"

                        click_hint = (
                            "<hr style='border: 0; border-top: 1px solid #334155; margin: 6px 0;'/>"
                            "<span style='color: #c084fc; font-size: 11px;'>💡 Active choice: Merged collection from both tracks</span>"
                        )
                        return (
                            f"<div style='font-size: 12px; line-height: 1.35;'>"
                            f"<b style='color: #c084fc;'>🔀 Merged {unit_name} ({len(self.diff.merge_items)} total):</b>"
                            f"<div style='margin-top: 4px;'>{items_html}</div>"
                            f"{click_hint}"
                            f"</div>"
                        )
                    elif self.diff.origin == "old":
                        items = self.diff.old_items
                        limit = 20
                        shown = items[:limit]
                        list_rows = "".join(f"<div style='margin: 1px 0;'>• {html.escape(it)}</div>" for it in shown)
                        more_cnt = len(items) - limit
                        more_txt = f"<div style='color: #94a3b8; font-size: 10px; margin-top: 3px;'>... and {more_cnt} more</div>" if more_cnt > 0 else ""
                        click_hint = (
                            "<hr style='border: 0; border-top: 1px solid #334155; margin: 6px 0;'/>"
                            "<span style='color: #38bdf8; font-size: 11px;'>💡 Active choice: Keeping Current track only</span>"
                        )
                        return (
                            f"<div style='font-size: 12px; line-height: 1.35;'>"
                            f"<b style='color: #38bdf8;'>◀ Kept Current Track {unit_name} ({len(items)}):</b>"
                            f"<div style='margin-top: 4px;'><div style='margin-left: 4px;'>{list_rows}{more_txt}</div></div>"
                            f"{click_hint}"
                            f"</div>"
                        )
                    else:  # new
                        items = self.diff.new_items
                        limit = 20
                        shown = items[:limit]
                        list_rows = "".join(f"<div style='margin: 1px 0;'>• {html.escape(it)}</div>" for it in shown)
                        more_cnt = len(items) - limit
                        more_txt = f"<div style='color: #94a3b8; font-size: 10px; margin-top: 3px;'>... and {more_cnt} more</div>" if more_cnt > 0 else ""
                        click_hint = (
                            "<hr style='border: 0; border-top: 1px solid #334155; margin: 6px 0;'/>"
                            "<span style='color: #4ade80; font-size: 11px;'>💡 Active choice: Keeping Target track only</span>"
                        )
                        return (
                            f"<div style='font-size: 12px; line-height: 1.35;'>"
                            f"<b style='color: #4ade80;'>▶ Kept Target Track {unit_name} ({len(items)}):</b>"
                            f"<div style='margin-top: 4px;'><div style='margin-left: 4px;'>{list_rows}{more_txt}</div></div>"
                            f"{click_hint}"
                            f"</div>"
                        )
                else:
                    return f"Result: {self.diff.result_val}"

        elif self.diff.category == "info":
            if origin_type == "old":
                return f"Current Track:\n\"{self.diff.old_val}\"\n\nClick to use Current value"
            elif origin_type == "new":
                return f"Target Track:\n\"{self.diff.new_val}\"\n\nClick to use Target value"
            else:
                return "Resolved value that will be saved in your Rekordbox collection."

        elif self.diff.category == "audio":
            if origin_type == "old":
                return f"Current Audio:\n{self.diff.old_val}"
            elif origin_type == "new":
                return f"Target Audio:\n{self.diff.new_val}\n\n(Specifications will be upgraded to this file)"
            else:
                return f"Upgraded audio specifications: {self.diff.result_val}"

        return ""

    def _update_tooltips(self) -> None:
        old_tip = self._build_tooltip("old")
        new_tip = self._build_tooltip("new")
        res_tip = self._build_tooltip("result")

        self.old_frame.setToolTip(old_tip)
        if hasattr(self, "old_text_label"):
            self.old_text_label.setToolTip(old_tip)

        self.new_frame.setToolTip(new_tip)
        if hasattr(self, "new_text_label"):
            self.new_text_label.setToolTip(new_tip)

        self.result_frame.setToolTip(res_tip)
        if hasattr(self, "result_label"):
            self.result_label.setToolTip(res_tip)

    def _format_display(self, val: Any) -> str:
        if val is None or val == "":
            return "<span style='color: #686c7a; font-style: italic;'>(Empty)</span>"
        if self.diff.key == "rating":
            try:
                r_int = int(val)
                return f"{format_rating_stars(r_int)} ({r_int})"
            except (ValueError, TypeError):
                pass
        if self.diff.key == "bpm" and val:
            try:
                return f"{float(val):.1f} BPM"
            except (ValueError, TypeError):
                pass
        if self.diff.key == "artwork":
            return "Cover Art" if val else "(No Artwork)"
        return str(val)

    def _update_styling(self) -> None:
        """Update row highlights and origin pill according to state."""
        # 1. Row styling
        if self.diff.category == "info":
            if self.diff.status == "conflict":
                self.setStyleSheet(
                    f"QFrame#diffRow {{ background-color: rgba(245, 158, 11, 0.08); border-left: 3px solid {COLOR_AMBER}; border-bottom: 1px solid {COLOR_BORDER}; }}"
                )
                self.badge_status.setText("⚠️ DIFFERENT")
                self.badge_status.setStyleSheet(
                    f"background-color: {COLOR_AMBER_BG}; color: {COLOR_AMBER}; border: 1px solid {COLOR_AMBER_BORDER}; border-radius: 3px; padding: 1px 6px; font-size: 10px; font-weight: bold;"
                )
            elif self.diff.status == "new_data":
                self.setStyleSheet(
                    f"QFrame#diffRow {{ background-color: rgba(0, 162, 255, 0.05); border-left: 3px solid {COLOR_BLUE}; border-bottom: 1px solid {COLOR_BORDER}; }}"
                )
                self.badge_status.setText("⚡ NEW VALUE")
                self.badge_status.setStyleSheet(
                    f"background-color: {COLOR_BLUE_BG}; color: {COLOR_BLUE}; border: 1px solid #005f9e; border-radius: 3px; padding: 1px 6px; font-size: 10px; font-weight: bold;"
                )
            elif self.diff.status == "kept_old":
                self.setStyleSheet(
                    f"QFrame#diffRow {{ background-color: transparent; border-left: 3px solid transparent; border-bottom: 1px solid {COLOR_BORDER}; }}"
                )
                self.badge_status.setText("ℹ️ KEPT OLD")
                self.badge_status.setStyleSheet(
                    f"background-color: {COLOR_BG_CARD}; color: {COLOR_TEXT_MUTED}; border: 1px solid {COLOR_BORDER}; border-radius: 3px; padding: 1px 6px; font-size: 10px; font-weight: bold;"
                )
            else:
                self.setStyleSheet(
                    f"QFrame#diffRow {{ background-color: transparent; border-left: 3px solid transparent; border-bottom: 1px solid {COLOR_BORDER}; }}"
                )
                self.badge_status.setText("✅ MATCH")
                self.badge_status.setStyleSheet(
                    f"background-color: {COLOR_GREEN_BG}; color: {COLOR_GREEN}; border: 1px solid {COLOR_GREEN_BORDER}; border-radius: 3px; padding: 1px 6px; font-size: 10px; font-weight: bold;"
                )
        elif self.diff.category == "audio":
            self.setStyleSheet(
                f"QFrame#diffRow {{ background-color: rgba(16, 185, 129, 0.04); border-left: 3px solid {COLOR_GREEN}; border-bottom: 1px solid {COLOR_BORDER}; }}"
            )
            self.badge_status.setText("⚡ UPGRADED")
            self.badge_status.setStyleSheet(
                f"background-color: {COLOR_GREEN_BG}; color: {COLOR_GREEN}; border: 1px solid {COLOR_GREEN_BORDER}; border-radius: 3px; padding: 1px 6px; font-size: 10px; font-weight: bold;"
            )
        else:  # preservation
            if self.diff.is_editable:
                if self.diff.origin == "merge":
                    self.setStyleSheet(
                        f"QFrame#diffRow {{ background-color: rgba(139, 92, 246, 0.05); border-left: 3px solid #8b5cf6; border-bottom: 1px solid {COLOR_BORDER}; }}"
                    )
                    self.badge_status.setText("🔀 MERGED")
                    self.badge_status.setStyleSheet(
                        "background-color: #2e1065; color: #c084fc; border: 1px solid #7c3aed; border-radius: 3px; padding: 1px 6px; font-size: 10px; font-weight: bold;"
                    )
                elif self.diff.origin == "old":
                    self.setStyleSheet(
                        f"QFrame#diffRow {{ background-color: rgba(0, 162, 255, 0.04); border-left: 3px solid {COLOR_BLUE}; border-bottom: 1px solid {COLOR_BORDER}; }}"
                    )
                    self.badge_status.setText("◀ KEPT OLD")
                    self.badge_status.setStyleSheet(
                        f"background-color: {COLOR_BLUE_BG}; color: {COLOR_BLUE}; border: 1px solid #005f9e; border-radius: 3px; padding: 1px 6px; font-size: 10px; font-weight: bold;"
                    )
                elif self.diff.origin == "new":
                    self.setStyleSheet(
                        f"QFrame#diffRow {{ background-color: rgba(16, 185, 129, 0.04); border-left: 3px solid {COLOR_GREEN}; border-bottom: 1px solid {COLOR_BORDER}; }}"
                    )
                    self.badge_status.setText("▶ KEPT TARGET")
                    self.badge_status.setStyleSheet(
                        f"background-color: {COLOR_GREEN_BG}; color: {COLOR_GREEN}; border: 1px solid {COLOR_GREEN_BORDER}; border-radius: 3px; padding: 1px 6px; font-size: 10px; font-weight: bold;"
                    )
            else:
                self.setStyleSheet(
                    f"QFrame#diffRow {{ background-color: transparent; border-left: 3px solid transparent; border-bottom: 1px solid {COLOR_BORDER}; }}"
                )
                self.badge_status.setText("🔒 PRESERVED")
                self.badge_status.setStyleSheet(
                    f"background-color: {COLOR_BLUE_BG}; color: {COLOR_BLUE}; border: 1px solid #005f9e; border-radius: 3px; padding: 1px 6px; font-size: 10px; font-weight: bold;"
                )

        # 2. Origin pill
        if self.diff.category == "preservation":
            if self.diff.is_editable:
                if self.diff.origin == "merge":
                    self.origin_pill.setText("MERGED")
                    self.origin_pill.setStyleSheet(
                        "background-color: #2e1065; color: #c084fc; border: 1px solid #7c3aed; border-radius: 3px; padding: 1px 5px; font-size: 9px; font-weight: bold;"
                    )
                    if hasattr(self, "result_label"):
                        self.result_label.setStyleSheet("color: #c084fc; font-size: 12px; font-weight: 600;")
                elif self.diff.origin == "old":
                    self.origin_pill.setText("OLD")
                    self.origin_pill.setStyleSheet(
                        f"background-color: {COLOR_BLUE_BG}; color: {COLOR_BLUE}; border: 1px solid #005f9e; border-radius: 3px; padding: 1px 5px; font-size: 9px; font-weight: bold;"
                    )
                    if hasattr(self, "result_label"):
                        self.result_label.setStyleSheet(f"color: {COLOR_BLUE}; font-size: 12px; font-weight: 600;")
                elif self.diff.origin == "new":
                    self.origin_pill.setText("TARGET")
                    self.origin_pill.setStyleSheet(
                        f"background-color: {COLOR_GREEN_BG}; color: {COLOR_GREEN}; border: 1px solid {COLOR_GREEN_BORDER}; border-radius: 3px; padding: 1px 5px; font-size: 9px; font-weight: bold;"
                    )
                    if hasattr(self, "result_label"):
                        self.result_label.setStyleSheet(f"color: {COLOR_GREEN}; font-size: 12px; font-weight: 600;")
            else:
                self.origin_pill.setText("KEEP")
                self.origin_pill.setStyleSheet(
                    f"background-color: {COLOR_GREEN_BG}; color: {COLOR_GREEN}; border: 1px solid {COLOR_GREEN_BORDER}; border-radius: 3px; padding: 1px 5px; font-size: 9px; font-weight: bold;"
                )
                if hasattr(self, "result_label"):
                    self.result_label.setStyleSheet(f"color: {COLOR_GREEN}; font-size: 12px; font-weight: 600;")
        elif self.diff.origin == "old":
            self.origin_pill.setText("OLD")
            self.origin_pill.setStyleSheet(
                f"background-color: {COLOR_BLUE_BG}; color: {COLOR_BLUE}; border: 1px solid #005f9e; border-radius: 3px; padding: 1px 5px; font-size: 9px; font-weight: bold;"
            )
        elif self.diff.origin == "new":
            self.origin_pill.setText("NEW")
            self.origin_pill.setStyleSheet(
                f"background-color: {COLOR_GREEN_BG}; color: {COLOR_GREEN}; border: 1px solid {COLOR_GREEN_BORDER}; border-radius: 3px; padding: 1px 5px; font-size: 9px; font-weight: bold;"
            )
        else:
            self.origin_pill.setText("CUSTOM")
            self.origin_pill.setStyleSheet(
                "background-color: #3b1d54; color: #c084fc; border: 1px solid #6b21a8; border-radius: 3px; padding: 1px 5px; font-size: 9px; font-weight: bold;"
            )

        # 3. Action buttons active highlight
        if hasattr(self, "btn_use_old"):
            is_active_old = (self.diff.origin == "old")
            self.btn_use_old.setStyleSheet(
                f"QPushButton {{ background-color: {COLOR_BLUE if is_active_old else COLOR_BG_CARD}; color: {'#ffffff' if is_active_old else COLOR_BLUE}; border: 1px solid {COLOR_BLUE if is_active_old else COLOR_BORDER_LIGHT}; border-radius: 4px; font-size: 10px; font-weight: bold; padding: 0px; }} "
                f"QPushButton:hover {{ background-color: {COLOR_BLUE_BG}; border-color: {COLOR_BLUE}; color: #ffffff; }}"
            )

        if hasattr(self, "btn_use_new"):
            is_active_new = (self.diff.origin == "new")
            self.btn_use_new.setStyleSheet(
                f"QPushButton {{ background-color: {COLOR_GREEN if is_active_new else COLOR_BG_CARD}; color: {'#ffffff' if is_active_new else COLOR_GREEN}; border: 1px solid {COLOR_GREEN if is_active_new else COLOR_BORDER_LIGHT}; border-radius: 4px; font-size: 10px; font-weight: bold; padding: 0px; }} "
                f"QPushButton:hover {{ background-color: {COLOR_GREEN_BG}; border-color: {COLOR_GREEN}; color: #ffffff; }}"
            )

        if hasattr(self, "btn_use_merge"):
            is_active_merge = (self.diff.origin == "merge")
            self.btn_use_merge.setStyleSheet(
                f"QPushButton {{ background-color: {'#7c3aed' if is_active_merge else COLOR_BG_CARD}; color: {'#ffffff' if is_active_merge else '#c084fc'}; border: 1px solid {'#8b5cf6' if is_active_merge else COLOR_BORDER_LIGHT}; border-radius: 4px; font-size: 10px; font-weight: bold; padding: 0px; }} "
                f"QPushButton:hover {{ background-color: #2e1065; border-color: #8b5cf6; color: #ffffff; }}"
            )

        # 4. Artwork custom result updates
        if self.diff.key == "artwork":
            is_new = (self.diff.origin == "new")
            cur_p = str(self.diff.new_val if is_new else self.diff.old_val or "")
            cur_res = getattr(self.diff, "new_meta" if is_new else "old_meta", "")

            if hasattr(self, "res_art_thumb"):
                if cur_p and os.path.exists(cur_p):
                    pix = QPixmap(cur_p).scaled(40, 40, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                    self.res_art_thumb.setPixmap(pix)
                    self.res_art_thumb.setText("")
                else:
                    self.res_art_thumb.setPixmap(QPixmap())
                    self.res_art_thumb.setText("🎵")

            if hasattr(self, "res_art_title"):
                self.res_art_title.setText("Using Replacement Artwork" if is_new else "Using Current Artwork")
                self.res_art_title.setStyleSheet(f"color: {COLOR_GREEN if is_new else COLOR_BLUE}; font-size: 12px; font-weight: bold;")

            if hasattr(self, "res_art_sub"):
                self.res_art_sub.setText(cur_res or ("Present" if (cur_p and os.path.exists(cur_p)) else "No Artwork"))

        self._update_tooltips()

    def _select_old(self) -> None:
        if not self.diff.is_editable:
            return
        self.set_origin("old")

    def _select_new(self) -> None:
        if not self.diff.is_editable:
            return
        self.set_origin("new")

    def _select_merge(self) -> None:
        if not self.diff.is_editable or self.diff.category != "preservation":
            return
        self.set_origin("merge")

    def _on_result_text_edited(self, text: str) -> None:
        self.diff.result_val = text
        text_clean = text.strip()
        old_clean = str(self.diff.old_val or "").strip()
        new_clean = str(self.diff.new_val or "").strip()

        if text_clean == old_clean:
            self.diff.origin = "old"
        elif text_clean == new_clean:
            self.diff.origin = "new"
        else:
            self.diff.origin = "custom"
        self._update_styling()
        self.changed.emit()

    def set_origin(self, origin: str) -> None:
        """Programmatically apply resolution preset."""
        if not self.diff.is_editable:
            return
        if origin == "auto":
            if self.diff.key == "artwork":
                has_old = bool(self.diff.old_val and os.path.exists(str(self.diff.old_val)))
                has_new = bool(self.diff.new_val and os.path.exists(str(self.diff.new_val)))
                target_origin = "new" if (not has_old and has_new) else "old"
                self.set_origin(target_origin)
                return
            if self.diff.category == "info":
                old_s = str(self.diff.old_val or "").strip()
                new_s = str(self.diff.new_val or "").strip()
                target_origin = "new" if (not old_s and new_s) else "old"
                self.set_origin(target_origin)
            return

        if origin == "new" and self.diff.key == "artwork":
            if not (self.diff.new_val and os.path.exists(str(self.diff.new_val))):
                return

        self.diff.set_origin(origin)
        if hasattr(self, "result_input"):
            self.result_input.blockSignals(True)
            self.result_input.setText(str(self.diff.result_val) if self.diff.result_val is not None else "")
            self.result_input.blockSignals(False)
        elif hasattr(self, "result_label"):
            self.result_label.setText(self._format_display(self.diff.result_val))
        self._update_styling()
        self.changed.emit()


class TrackReplacementDiffView(QWidget):
    """Full 3-column diff comparison & conflict resolution view."""

    confirmed = Signal(dict, dict)  # Emits (resolved_metadata, options)
    cancelled = Signal()

    def __init__(
        self,
        engine: Engine,
        mode: str,  # "file" or "merge"
        old_track: Dict[str, Any],
        new_item: Dict[str, Any],  # specs dict for mode="file", target_track dict for mode="merge"
        options: Optional[Dict[str, Any]] = None,
        db_path: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.engine = engine
        self.mode = mode
        self.old_track = dict(old_track)
        self.new_item = dict(new_item)
        self.options = dict(options or {})
        self.db_path = db_path

        # If old_track lacks full info, fetch from DB
        content_id = str(self.old_track.get("id") or self.old_track.get("content_id") or "")
        if self.engine and content_id:
            full_old = fetch_track_full_info(self.engine, content_id)
            if full_old:
                base = dict(self.old_track)
                base.update(full_old)
                self.old_track = base

        if self.mode == "merge":
            tgt_id = str(self.new_item.get("id") or self.new_item.get("content_id") or "")
            if self.engine and tgt_id:
                full_tgt = fetch_track_full_info(self.engine, tgt_id)
                if full_tgt:
                    base = dict(self.new_item)
                    base.update(full_tgt)
                    self.new_item = base

        self.diffs = calculate_metadata_diff(self.old_track, self.new_item, mode=self.mode)
        self.row_widgets: List[DiffRowWidget] = []
        self.preservation_strategy = "merge" if self.mode == "merge" else "old"

        self._init_ui()

    def _init_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(18, 16, 18, 16)
        root_layout.setSpacing(14)

        # ----------------------------------------------------------------------
        # 1. Header Row & Safety Status
        # ----------------------------------------------------------------------
        header_row = QHBoxLayout()
        header_row.setSpacing(12)

        icon_label = QLabel("🔀")
        icon_label.setStyleSheet("font-size: 26px;")
        header_row.addWidget(icon_label)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)

        title_label = QLabel("Track Replacement & Metadata Diff")
        title_label.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-size: 17px; font-weight: bold;")
        title_col.addWidget(title_label)

        sub_text = (
            "Review conflicting metadata between current collection track and replacement AIFF file on disk."
            if self.mode == "file"
            else "Review conflicting metadata between old collection track and newly imported AIFF track."
        )
        sub_label = QLabel(sub_text)
        sub_label.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-size: 12px;")
        title_col.addWidget(sub_label)
        header_row.addLayout(title_col, stretch=1)

        # Rekordbox Safety Chip
        self.safety_chip = QLabel("")
        self._update_safety_chip()
        header_row.addWidget(self.safety_chip)

        root_layout.addLayout(header_row)

        # ----------------------------------------------------------------------
        # 2. Conflict Summary Banner & Quick Preset Buttons
        # ----------------------------------------------------------------------
        summary_bar = QFrame()
        summary_bar.setObjectName("summaryBar")
        summary_bar.setStyleSheet(
            f"QFrame#summaryBar {{ background-color: {COLOR_BG_PANEL}; border: 1px solid {COLOR_BORDER}; border-radius: 8px; padding: 10px 14px; }}"
        )
        summary_layout = QHBoxLayout(summary_bar)
        summary_layout.setContentsMargins(12, 8, 12, 8)
        summary_layout.setSpacing(12)

        self.conflict_banner_label = QLabel("")
        self.conflict_banner_label.setStyleSheet("font-size: 13px; font-weight: 600;")
        summary_layout.addWidget(self.conflict_banner_label, stretch=1)

        # Quick action presets
        btn_preset_old = QPushButton("◀ Keep All Current (Old)")
        btn_preset_old.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_preset_old.setToolTip("Preserve all existing collection track metadata text")
        btn_preset_old.setStyleSheet(
            f"QPushButton {{ background-color: {COLOR_BG_CARD}; color: {COLOR_BLUE}; border: 1px solid {COLOR_BORDER}; border-radius: 5px; padding: 6px 12px; font-size: 11px; font-weight: 600; }} "
            f"QPushButton:hover {{ background-color: {COLOR_BLUE_BG}; border-color: {COLOR_BLUE}; color: #ffffff; }}"
        )
        btn_preset_old.clicked.connect(lambda: self._apply_global_preset("old"))
        summary_layout.addWidget(btn_preset_old)

        btn_preset_new = QPushButton("▶ Use All New")
        btn_preset_new.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_preset_new.setToolTip("Apply all metadata tags from the replacement file / track")
        btn_preset_new.setStyleSheet(
            f"QPushButton {{ background-color: {COLOR_BG_CARD}; color: {COLOR_GREEN}; border: 1px solid {COLOR_BORDER}; border-radius: 5px; padding: 6px 12px; font-size: 11px; font-weight: 600; }} "
            f"QPushButton:hover {{ background-color: {COLOR_GREEN_BG}; border-color: {COLOR_GREEN}; color: #ffffff; }}"
        )
        btn_preset_new.clicked.connect(lambda: self._apply_global_preset("new"))
        summary_layout.addWidget(btn_preset_new)

        btn_preset_auto = QPushButton("⚡ Auto-Resolve (Keep Non-Empty)")
        btn_preset_auto.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_preset_auto.setToolTip("Keeps current value unless empty, in which case replacement data is used")
        btn_preset_auto.setStyleSheet(
            f"QPushButton {{ background-color: {COLOR_BG_CARD}; color: {COLOR_TEXT_PRIMARY}; border: 1px solid {COLOR_BORDER}; border-radius: 5px; padding: 6px 12px; font-size: 11px; font-weight: 600; }} "
            f"QPushButton:hover {{ background-color: {COLOR_BG_HOVER}; border-color: {COLOR_BORDER_LIGHT}; }}"
        )
        btn_preset_auto.clicked.connect(lambda: self._apply_global_preset("auto"))
        summary_layout.addWidget(btn_preset_auto)

        root_layout.addWidget(summary_bar)

        # ----------------------------------------------------------------------
        # 3. 3-Column Hero Cards Header
        # ----------------------------------------------------------------------
        hero_layout = QHBoxLayout()
        hero_layout.setSpacing(12)

        # Empty spacer to match field label width
        spacer_label = QWidget()
        spacer_label.setFixedWidth(170)
        hero_layout.addWidget(spacer_label)

        # Left Hero Card (Current)
        self.left_hero_card = self._build_hero_card(
            title="1. CURRENT TRACK (BEING REPLACED)",
            track_data=self.old_track,
            border_color=COLOR_BORDER_LIGHT,
            bg_color=COLOR_BG_CARD,
            chip_text="Old Record",
            chip_color=COLOR_AMBER,
        )
        hero_layout.addWidget(self.left_hero_card, stretch=1)

        # Center Hero Card (New Replacement)
        center_chip = "Replacement File" if self.mode == "file" else "Target Track"
        self.center_hero_card = self._build_hero_card(
            title="2. NEW REPLACEMENT",
            track_data=self.new_item,
            border_color=COLOR_GREEN_BORDER,
            bg_color="#14241d",
            chip_text=center_chip,
            chip_color=COLOR_GREEN,
            is_file=(self.mode == "file"),
        )
        hero_layout.addWidget(self.center_hero_card, stretch=1)

        # Right Hero Card (Final Result)
        self.right_hero_card = self._build_result_hero_card()
        hero_layout.addWidget(self.right_hero_card, stretch=1)

        root_layout.addLayout(hero_layout)

        # ----------------------------------------------------------------------
        # 4. Scrollable 3-Column Diff Comparison Table
        # ----------------------------------------------------------------------
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setStyleSheet(
            f"QScrollArea {{ background-color: transparent; border: 1px solid {COLOR_BORDER}; border-radius: 8px; }} "
            f"QScrollBar:vertical {{ background: {COLOR_BG_DARK}; width: 10px; border-radius: 5px; }} "
            f"QScrollBar::handle:vertical {{ background: {COLOR_BORDER_LIGHT}; border-radius: 5px; }} "
            f"QScrollBar::handle:vertical:hover {{ background: {COLOR_TEXT_MUTED}; }}"
        )

        table_container = QWidget()
        table_container.setStyleSheet(f"background-color: {COLOR_BG_PANEL};")
        self.table_layout = QVBoxLayout(table_container)
        self.table_layout.setContentsMargins(0, 0, 0, 0)
        self.table_layout.setSpacing(0)

        # Build Section Rows
        self._populate_diff_sections()

        scroll_area.setWidget(table_container)
        root_layout.addWidget(scroll_area, stretch=1)

        # ----------------------------------------------------------------------
        # 5. Bottom Options & Action Bar
        # ----------------------------------------------------------------------
        bottom_bar = QFrame()
        bottom_bar.setObjectName("bottomBar")
        bottom_bar.setStyleSheet(
            f"QFrame#bottomBar {{ background-color: {COLOR_BG_PANEL}; border-top: 1px solid {COLOR_BORDER}; padding-top: 10px; }}"
        )
        bottom_layout = QHBoxLayout(bottom_bar)
        bottom_layout.setContentsMargins(4, 8, 4, 0)
        bottom_layout.setSpacing(14)

        # Options on disk
        opt_layout = QHBoxLayout()
        opt_layout.setSpacing(8)

        lbl_action = QLabel("Old Audio File:")
        lbl_action.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-size: 11px; font-weight: 600;")
        opt_layout.addWidget(lbl_action)

        self.old_file_combo = QComboBox()
        self.old_file_combo.addItems([
            "Keep old file on disk (Safe)",
            "Move old file to macOS Trash (Clean space)",
            "Rename old file to .bak (Backup)",
        ])
        default_idx = 0
        if self.options.get("old_file_action") == "trash":
            default_idx = 1
        elif self.options.get("old_file_action") == "backup":
            default_idx = 2
        self.old_file_combo.setCurrentIndex(default_idx)
        opt_layout.addWidget(self.old_file_combo)

        if self.mode == "file":
            self.chk_move_folder = QCheckBox("Move replacement file into track folder")
            self.chk_move_folder.setChecked(self.options.get("move_new_file_to_track_folder", False))
            self.chk_move_folder.setStyleSheet("font-size: 11px;")
            self.chk_move_folder.setToolTip(
                "Moves the replacement audio file into the existing track's folder instead of keeping it in its current location."
            )
            self.chk_move_folder.toggled.connect(self._on_move_folder_toggled)
            opt_layout.addWidget(self.chk_move_folder)
            if self.chk_move_folder.isChecked():
                self._on_move_folder_toggled(True)

        bottom_layout.addLayout(opt_layout, stretch=1)

        # Cancel button
        btn_cancel = QPushButton("✕ Cancel")
        btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_cancel.setStyleSheet(
            f"QPushButton {{ background-color: {COLOR_BG_CARD}; color: {COLOR_TEXT_PRIMARY}; border: 1px solid {COLOR_BORDER}; border-radius: 6px; padding: 9px 18px; font-size: 13px; font-weight: 500; }} "
            f"QPushButton:hover {{ background-color: {COLOR_BG_HOVER}; border-color: {COLOR_BORDER_LIGHT}; }}"
        )
        btn_cancel.clicked.connect(self.cancelled.emit)
        bottom_layout.addWidget(btn_cancel)

        # Confirm & Replace Primary Button
        action_name = "🔄  Confirm & Replace Audio File" if self.mode == "file" else "🔀  Confirm & Merge Tracks"
        self.btn_confirm = QPushButton(action_name)
        self.btn_confirm.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_color = COLOR_BLUE if self.mode == "file" else COLOR_GREEN
        btn_hover = COLOR_BLUE_HOVER if self.mode == "file" else "#1cd496"
        self.btn_confirm.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {btn_color};
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 9px 24px;
                font-size: 13px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {btn_hover};
            }}
            QPushButton:disabled {{
                background-color: #2e2e38;
                color: #686c7a;
            }}
            """
        )
        self.btn_confirm.clicked.connect(self._on_confirm_clicked)
        bottom_layout.addWidget(self.btn_confirm)

        root_layout.addWidget(bottom_bar)

        self._update_conflict_banner()
        self._update_result_hero_card()

    def _build_hero_card(
        self,
        title: str,
        track_data: Dict[str, Any],
        border_color: str,
        bg_color: str,
        chip_text: str,
        chip_color: str,
        is_file: bool = False,
    ) -> QFrame:
        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ background-color: {bg_color}; border: 1px solid {border_color}; border-radius: 8px; padding: 10px 12px; }}"
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        # Header with chip
        top_h = QHBoxLayout()
        lbl_head = QLabel(title)
        lbl_head.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 10px; font-weight: bold; letter-spacing: 0.5px;")
        top_h.addWidget(lbl_head, stretch=1)

        chip = QLabel(chip_text)
        chip.setStyleSheet(
            f"background-color: {chip_color}22; color: {chip_color}; border: 1px solid {chip_color}66; border-radius: 3px; padding: 1px 6px; font-size: 9px; font-weight: bold;"
        )
        top_h.addWidget(chip)
        layout.addLayout(top_h)

        # Track Info Row
        info_row = QHBoxLayout()
        info_row.setSpacing(10)

        # Artwork
        art_label = QLabel()
        art_label.setFixedSize(48, 48)
        art_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        art_label.setStyleSheet("background-color: #121216; border-radius: 6px; font-size: 22px;")

        img_p = resolve_artwork_path(track_data.get("image_path"), db_path=self.db_path)
        if not img_p and track_data.get("artwork_path"):
            img_p = track_data.get("artwork_path")
        if img_p and os.path.exists(img_p):
            pix = QPixmap(img_p).scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            art_label.setPixmap(pix)
            art_label.setText("")
        else:
            art_label.setText("📁" if is_file else "🎵")
        info_row.addWidget(art_label)

        meta_col = QVBoxLayout()
        meta_col.setSpacing(2)
        lbl_title = QLabel(track_data.get("title") or "(Untitled)")
        lbl_title.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-size: 13px; font-weight: bold;")
        meta_col.addWidget(lbl_title)

        lbl_artist = QLabel(track_data.get("artist") or "Unknown Artist")
        lbl_artist.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-size: 11px;")
        meta_col.addWidget(lbl_artist)

        fmt = track_data.get("format_name") or track_data.get("file_type") or ("AIFF" if is_file else "MP3")
        br = f"{track_data.get('bitrate', 0)} kbps" if track_data.get("bitrate") else ""
        dur = track_data.get("duration_str") or ""
        lbl_extra = QLabel(f"{fmt} · {br} · {dur}".strip(" · "))
        lbl_extra.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 10px;")
        meta_col.addWidget(lbl_extra)
        info_row.addLayout(meta_col, stretch=1)

        layout.addLayout(info_row)
        return card

    def _build_result_hero_card(self) -> QFrame:
        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ background-color: #0d2338; border: 1px solid {COLOR_BLUE}; border-radius: 8px; padding: 10px 12px; }}"
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        # Header with chip
        top_h = QHBoxLayout()
        lbl_head = QLabel("3. FINAL RESULT (IN REKORDBOX)")
        lbl_head.setStyleSheet(f"color: {COLOR_BLUE}; font-size: 10px; font-weight: bold; letter-spacing: 0.5px;")
        top_h.addWidget(lbl_head, stretch=1)

        chip = QLabel("Saved Track")
        chip.setStyleSheet(
            f"background-color: {COLOR_BLUE_BG}; color: {COLOR_BLUE}; border: 1px solid {COLOR_BLUE}; border-radius: 3px; padding: 1px 6px; font-size: 9px; font-weight: bold;"
        )
        top_h.addWidget(chip)
        layout.addLayout(top_h)

        # Track Info Row
        info_row = QHBoxLayout()
        info_row.setSpacing(10)

        # Artwork
        self.res_art_label = QLabel()
        self.res_art_label.setFixedSize(48, 48)
        self.res_art_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.res_art_label.setStyleSheet("background-color: #121216; border-radius: 6px; font-size: 22px;")

        img_p = resolve_artwork_path(self.old_track.get("image_path"), db_path=self.db_path)
        if not img_p and self.old_track.get("artwork_path"):
            img_p = self.old_track.get("artwork_path")
        if img_p and os.path.exists(img_p):
            pix = QPixmap(img_p).scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            self.res_art_label.setPixmap(pix)
            self.res_art_label.setText("")
        else:
            self.res_art_label.setText("🎵")
        info_row.addWidget(self.res_art_label)

        meta_col = QVBoxLayout()
        meta_col.setSpacing(2)
        self.res_title_label = QLabel(self.old_track.get("title") or "(Untitled)")
        self.res_title_label.setStyleSheet("color: #ffffff; font-size: 13px; font-weight: bold;")
        meta_col.addWidget(self.res_title_label)

        self.res_artist_label = QLabel(self.old_track.get("artist") or "Unknown Artist")
        self.res_artist_label.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-size: 11px;")
        meta_col.addWidget(self.res_artist_label)

        self.res_extra_label = QLabel("AIFF (Lossless) · All History & Plays Preserved")
        self.res_extra_label.setStyleSheet(f"color: {COLOR_GREEN}; font-size: 10px; font-weight: 600;")
        meta_col.addWidget(self.res_extra_label)
        info_row.addLayout(meta_col, stretch=1)

        layout.addLayout(info_row)
        return card

    def _populate_diff_sections(self) -> None:
        """Add grouped sections with section headers and comparison rows."""
        self.row_widgets.clear()

        # Section 1: Rekordbox Info
        self._add_section_header("📌 TRACK METADATA (REKORDBOX INFO)", "Core track identification and tagging attributes")
        for d in self.diffs:
            if d.category == "info":
                row_w = DiffRowWidget(d, self)
                row_w.changed.connect(self._on_row_value_changed)
                self.table_layout.addWidget(row_w)
                self.row_widgets.append(row_w)

        # Section 2: Audio Specs
        self._add_section_header("🔊 AUDIO SPECIFICATIONS (UPGRADED)", "Audio quality, uncompressed bit depth, and file location")
        for d in self.diffs:
            if d.category == "audio":
                row_w = DiffRowWidget(d, self)
                self.table_layout.addWidget(row_w)
                self.row_widgets.append(row_w)

        # Section 3: Collection Preservation
        if self.mode == "merge":
            sec3_actions = QWidget()
            s3_layout = QHBoxLayout(sec3_actions)
            s3_layout.setContentsMargins(0, 0, 0, 0)
            s3_layout.setSpacing(6)

            self.btn_sec3_old = QPushButton("◀ Keep All Old")
            self.btn_sec3_merge = QPushButton("🔀 Merge All Both")
            self.btn_sec3_new = QPushButton("Keep All Target ▶")

            for b in (self.btn_sec3_old, self.btn_sec3_merge, self.btn_sec3_new):
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.setFixedHeight(24)

            self.btn_sec3_old.clicked.connect(lambda: self._apply_preservation_preset("old"))
            self.btn_sec3_merge.clicked.connect(lambda: self._apply_preservation_preset("merge"))
            self.btn_sec3_new.clicked.connect(lambda: self._apply_preservation_preset("new"))

            s3_layout.addWidget(self.btn_sec3_old)
            s3_layout.addWidget(self.btn_sec3_merge)
            s3_layout.addWidget(self.btn_sec3_new)

            self._add_section_header(
                "🔀 COLLECTION PRESERVATION & MERGE RESOLUTION",
                "Plays, history sets, date added, playlists, and cues - choose Old, Target, or Merge",
                action_widget=sec3_actions,
            )
        else:
            self._add_section_header(
                "🔒 COLLECTION PRESERVATION (100% GUARANTEED)",
                "Plays, history sets, date added, playlists, and cue points preserved intact",
            )

        for d in self.diffs:
            if d.category == "preservation":
                row_w = DiffRowWidget(d, self)
                if self.mode == "merge":
                    row_w.changed.connect(self._on_row_value_changed)
                self.table_layout.addWidget(row_w)
                self.row_widgets.append(row_w)

        if self.mode == "merge":
            self._update_preservation_header_buttons()

        self.table_layout.addStretch(1)

    def _add_section_header(self, title: str, subtitle: str, action_widget: Optional[QWidget] = None) -> QFrame:
        hdr = QFrame()
        hdr.setStyleSheet(
            f"background-color: {COLOR_BG_CARD}; border-top: 1px solid {COLOR_BORDER}; border-bottom: 1px solid {COLOR_BORDER}; padding: 6px 14px;"
        )
        l = QHBoxLayout(hdr)
        l.setContentsMargins(12, 6, 12, 6)
        l.setSpacing(10)

        t_lbl = QLabel(title)
        t_lbl.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-size: 11px; font-weight: 700; letter-spacing: 0.8px;")
        l.addWidget(t_lbl)

        s_lbl = QLabel(f"·  {subtitle}")
        s_lbl.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px;")
        l.addWidget(s_lbl, stretch=1)

        if action_widget:
            l.addWidget(action_widget)

        self.table_layout.addWidget(hdr)
        return hdr

    def _on_row_value_changed(self) -> None:
        self._update_conflict_banner()
        self._update_result_hero_card()
        if self.mode == "merge":
            self._update_preservation_header_buttons()

    def _update_result_hero_card(self) -> None:
        """Update live preview card when title, artist, or artwork is changed."""
        res_title = ""
        res_artist = ""
        res_art_path = ""
        for d in self.diffs:
            if d.key == "title":
                res_title = str(d.result_val or "").strip()
            elif d.key == "artist":
                res_artist = str(d.result_val or "").strip()
            elif d.key == "artwork":
                res_art_path = str(d.new_val if d.origin == "new" else d.old_val or "")

        self.res_title_label.setText(res_title or "(Untitled)")
        self.res_artist_label.setText(res_artist or "Unknown Artist")

        if hasattr(self, "res_art_label"):
            if not res_art_path:
                res_art_path = self.old_track.get("artwork_path") or self.old_track.get("image_path") or ""
            img_p = resolve_artwork_path(res_art_path, db_path=self.db_path)
            if not img_p and os.path.exists(res_art_path):
                img_p = res_art_path
            if img_p and os.path.exists(img_p):
                pix = QPixmap(img_p).scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                self.res_art_label.setPixmap(pix)
                self.res_art_label.setText("")
            else:
                self.res_art_label.setPixmap(QPixmap())
                self.res_art_label.setText("🎵")

    def _update_conflict_banner(self) -> None:
        """Recalculate conflict statistics and update top summary badge."""
        conflicts = [d for d in self.diffs if d.category == "info" and d.status == "conflict"]
        new_datas = [d for d in self.diffs if d.category == "info" and d.status == "new_data"]

        if conflicts:
            count = len(conflicts)
            self.conflict_banner_label.setText(
                f"⚠️ <span style='color:{COLOR_AMBER};'>{count} metadata conflict{'s' if count > 1 else ''} detected</span> between tracks. "
                "Review highlighted rows below and choose which value to keep."
            )
        elif new_datas:
            count = len(new_datas)
            self.conflict_banner_label.setText(
                f"⚡ <span style='color:{COLOR_BLUE};'>{count} new metadata field{'s' if count > 1 else ''} available</span> from replacement file. "
                "All conflicts resolved."
            )
        else:
            self.conflict_banner_label.setText(
                f"✅ <span style='color:{COLOR_GREEN};'>All metadata fields match identically.</span> "
                "Audio will be upgraded to AIFF with zero tag conflicts."
            )

    def _apply_global_preset(self, origin: str) -> None:
        """Apply preset resolution to all editable metadata tag rows."""
        for row in self.row_widgets:
            if row.diff.is_editable and row.diff.category == "info":
                row.set_origin(origin)
        self._update_conflict_banner()
        self._update_result_hero_card()

    def _apply_preservation_preset(self, origin: str) -> None:
        """Apply preservation preset (old, merge, new) to all preservation rows."""
        self.preservation_strategy = origin
        for row in self.row_widgets:
            if row.diff.category == "preservation" and row.diff.is_editable:
                row.set_origin(origin)
        self._update_preservation_header_buttons()

    def _update_preservation_header_buttons(self) -> None:
        """Update active highlights on Section 3 header buttons."""
        if self.mode != "merge" or not hasattr(self, "btn_sec3_old"):
            return
        pres_rows = [r for r in self.row_widgets if r.diff.category == "preservation"]
        if not pres_rows:
            return
        origins = {r.diff.origin for r in pres_rows}
        current_state = origins.pop() if len(origins) == 1 else "custom"

        style_inactive_old = f"QPushButton {{ background-color: {COLOR_BG_CARD}; color: {COLOR_BLUE}; border: 1px solid {COLOR_BORDER}; border-radius: 4px; padding: 2px 10px; font-size: 10px; font-weight: 600; }} QPushButton:hover {{ background-color: {COLOR_BLUE_BG}; border-color: {COLOR_BLUE}; }}"
        style_active_old = f"QPushButton {{ background-color: {COLOR_BLUE}; color: #ffffff; border: 1px solid {COLOR_BLUE_HOVER}; border-radius: 4px; padding: 2px 10px; font-size: 10px; font-weight: bold; }}"

        style_inactive_merge = f"QPushButton {{ background-color: {COLOR_BG_CARD}; color: #c084fc; border: 1px solid {COLOR_BORDER}; border-radius: 4px; padding: 2px 10px; font-size: 10px; font-weight: 600; }} QPushButton:hover {{ background-color: #2e1065; border-color: #8b5cf6; }}"
        style_active_merge = f"QPushButton {{ background-color: #7c3aed; color: #ffffff; border: 1px solid #8b5cf6; border-radius: 4px; padding: 2px 10px; font-size: 10px; font-weight: bold; }}"

        style_inactive_new = f"QPushButton {{ background-color: {COLOR_BG_CARD}; color: {COLOR_GREEN}; border: 1px solid {COLOR_BORDER}; border-radius: 4px; padding: 2px 10px; font-size: 10px; font-weight: 600; }} QPushButton:hover {{ background-color: {COLOR_GREEN_BG}; border-color: {COLOR_GREEN}; }}"
        style_active_new = f"QPushButton {{ background-color: {COLOR_GREEN}; color: #ffffff; border: 1px solid #1cd496; border-radius: 4px; padding: 2px 10px; font-size: 10px; font-weight: bold; }}"

        self.btn_sec3_old.setStyleSheet(style_active_old if current_state == "old" else style_inactive_old)
        self.btn_sec3_merge.setStyleSheet(style_active_merge if current_state == "merge" else style_inactive_merge)
        self.btn_sec3_new.setStyleSheet(style_active_new if current_state == "new" else style_inactive_new)

    def _update_safety_chip(self) -> None:
        running = is_rekordbox_running()
        if running:
            self.safety_chip.setText("🔴 Rekordbox Running (Database Locked)")
            self.safety_chip.setObjectName("dangerBadge")
            self.safety_chip.setToolTip("Please close Rekordbox (Cmd + Q) before replacing collection tracks.")
            if hasattr(self, "btn_confirm"):
                self.btn_confirm.setEnabled(False)
        else:
            self.safety_chip.setText("🟢 Rekordbox Closed (Safe)")
            self.safety_chip.setObjectName("safeBadge")
            self.safety_chip.setToolTip("Rekordbox is closed. Safe to perform replacement.")
            if hasattr(self, "btn_confirm"):
                self.btn_confirm.setEnabled(True)
        self.safety_chip.style().unpolish(self.safety_chip)
        self.safety_chip.style().polish(self.safety_chip)

    def get_resolved_metadata(self) -> Dict[str, Any]:
        """Collect final resolved metadata dictionary to be stored in database."""
        result: Dict[str, Any] = {}
        for d in self.diffs:
            if d.category == "info" and d.key != "artwork":
                result[d.key] = d.result_val
        return result

    def get_options(self) -> Dict[str, Any]:
        """Collect user-selected replacement options."""
        act_idx = self.old_file_combo.currentIndex()
        action_val = "keep" if act_idx == 0 else ("trash" if act_idx == 1 else "backup")

        preservation_fields: Dict[str, str] = {}
        for d in self.diffs:
            if d.category == "preservation":
                preservation_fields[d.key] = str(d.origin)

        artwork_origin = "old"
        artwork_src_path = ""
        for d in self.diffs:
            if d.key == "artwork":
                artwork_origin = d.origin
                artwork_src_path = str(d.new_val if d.origin == "new" else d.old_val or "")
                break

        opts = {
            "old_file_action": action_val,
            "move_new_file_to_track_folder": self.chk_move_folder.isChecked() if hasattr(self, "chk_move_folder") else False,
            "resolved_metadata": self.get_resolved_metadata(),
            "preservation_fields": preservation_fields,
            "preservation_strategy": getattr(self, "preservation_strategy", "merge"),
            "artwork_choice": artwork_origin,
            "artwork_source_path": artwork_src_path,
            "db_path": self.db_path,
        }
        return opts

    def _on_move_folder_toggled(self, checked: bool) -> None:
        """Update Audio Location in diff view when Move checkbox is toggled."""
        old_path = str(self.old_track.get("file_path") or self.old_track.get("FolderPath") or self.old_track.get("folder_path") or "")
        new_path = str(self.new_item.get("file_path") or self.new_item.get("folder_path") or "")
        if os.path.isdir(old_path) or (old_path and not os.path.splitext(old_path)[1]):
            old_dir = old_path
        else:
            old_dir = os.path.dirname(old_path) if old_path else ""
        for row in self.row_widgets:
            if row.diff.key == "file_path":
                if checked and old_dir and new_path:
                    dest = os.path.join(old_dir, os.path.basename(new_path))
                    row.diff.result_val = dest
                else:
                    row.diff.result_val = new_path
                if hasattr(row, "result_label"):
                    row.result_label.setText(row._format_display(row.diff.result_val))
                    row.result_label.setToolTip(str(row.diff.result_val))
                break

    def _on_confirm_clicked(self) -> None:
        if is_rekordbox_running():
            QMessageBox.warning(
                self,
                "Rekordbox Running",
                "⚠️ Rekordbox is currently running!\n\n"
                "Please quit Rekordbox (Cmd + Q) before replacing collection files to prevent database corruption.",
            )
            return

        resolved_meta = self.get_resolved_metadata()
        options = self.get_options()
        self.confirmed.emit(resolved_meta, options)


class TrackReplacementDiffDialog(QDialog):
    """Modal dialog wrapper hosting TrackReplacementDiffView."""

    def __init__(
        self,
        engine: Engine,
        mode: str,
        old_track: Dict[str, Any],
        new_item: Dict[str, Any],
        options: Optional[Dict[str, Any]] = None,
        db_path: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        title_str = old_track.get("title") or "Track"
        self.setWindowTitle(f"Review Metadata & Replacement — {title_str}")
        self.resize(1080, 750)
        self.setMinimumSize(920, 580)
        self.setStyleSheet(f"QDialog {{ background-color: {COLOR_BG_DARK}; color: {COLOR_TEXT_PRIMARY}; }}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.diff_view = TrackReplacementDiffView(
            engine=engine,
            mode=mode,
            old_track=old_track,
            new_item=new_item,
            options=options,
            db_path=db_path,
            parent=self,
        )
        self.diff_view.confirmed.connect(self._on_confirmed)
        self.diff_view.cancelled.connect(self.reject)
        layout.addWidget(self.diff_view)

        self.resolved_metadata: Dict[str, Any] = {}
        self.resolved_options: Dict[str, Any] = {}

    def _on_confirmed(self, resolved_metadata: Dict[str, Any], options: Dict[str, Any]) -> None:
        self.resolved_metadata = resolved_metadata
        self.resolved_options = options
        self.accept()

    def get_resolved_metadata(self) -> Dict[str, Any]:
        return self.resolved_metadata if self.resolved_metadata else self.diff_view.get_resolved_metadata()

    def get_options(self) -> Dict[str, Any]:
        return self.resolved_options if self.resolved_options else self.diff_view.get_options()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            event.accept()
            return
        super().keyPressEvent(event)
