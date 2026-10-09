"""Track Replacement & Metadata Diff Screen.

Provides a rich 3-column diff comparison tool for Rekordbox track replacements:
- Left: Current Track (Being Replaced)
- Center: New Replacement (Audio file on disk or newly imported AIFF track)
- Right: Final Result (The metadata that will be stored in Rekordbox after replacement)

Highlights conflicting Rekordbox 'Info' metadata, allows granular field-by-field
or batch resolution, live preview, and safe database execution.
"""

import os
from typing import Any, Dict, List, Optional, Set, Tuple

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
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
        default_origin: str = "old",  # "old" or "new"
        is_editable: bool = True,
        help_tip: str = "",
    ):
        self.key = key
        self.label = label
        self.category = category
        self.old_val = old_val
        self.new_val = new_val
        self.is_editable = is_editable
        self.help_tip = help_tip

        # Determine conflict / match status
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
    info_definitions = [
        ("title", "Track Title", old_data.get("title", ""), new_data.get("title", ""), "old"),
        ("artist", "Artist", old_data.get("artist", ""), new_data.get("artist", ""), "old"),
        ("album", "Album", old_data.get("album", ""), new_data.get("album", ""), "old"),
        ("genre", "Genre", old_data.get("genre", ""), new_data.get("genre", ""), "old"),
        ("bpm", "BPM / Tempo", old_data.get("bpm") or 0.0, new_data.get("bpm") or 0.0, "old"),
        ("key", "Musical Key", old_data.get("key", ""), new_data.get("key", ""), "old"),
        ("rating", "Rating", old_data.get("rating") or 0, new_data.get("rating") or 0, "old"),
        ("release_year", "Release Year", old_data.get("release_year") or old_data.get("year") or 0, new_data.get("release_year") or new_data.get("year") or 0, "old"),
        ("track_no", "Track No.", old_data.get("track_no") or 0, new_data.get("track_no") or 0, "old"),
        ("disc_no", "Disc No.", old_data.get("disc_no") or 0, new_data.get("disc_no") or 0, "old"),
        ("remixer", "Remixer", old_data.get("remixer", ""), new_data.get("remixer", ""), "old"),
        ("label", "Record Label", old_data.get("label", ""), new_data.get("label", ""), "old"),
        ("mix_name", "Mix Name", old_data.get("mix_name", ""), new_data.get("mix_name", ""), "old"),
        ("composer", "Composer", old_data.get("composer", ""), new_data.get("composer", ""), "old"),
        ("comment", "Comment / Notes", old_data.get("comment", ""), new_data.get("comment", ""), "old"),
        ("color_id", "Color Code", old_data.get("color_id", ""), new_data.get("color_id", ""), "old"),
    ]

    for key, label, old_v, new_v, def_origin in info_definitions:
        fields.append(
            FieldDiff(
                key=key,
                label=label,
                category="info",
                old_val=old_v,
                new_val=new_v,
                default_origin=def_origin,
                is_editable=True,
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

    # 3. Collection Preservation (100% Guaranteed from Current Track)
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
    res_h_disp = f"Preserved ({old_h_disp})" if old_h_cnt > 0 else "100% Preserved intact"

    old_p_disp = f"{old_p_cnt} {p_unit}"
    new_p_disp = f"{new_p_cnt} {new_p_unit}" if mode == "merge" else "0 playlists"
    res_p_disp = f"Preserved ({old_p_disp})" if old_p_cnt > 0 else "All playlist memberships preserved"

    old_t_disp = f"{old_t_cnt} {t_unit}"
    new_t_disp = f"{new_t_cnt} {new_t_unit}" if mode == "merge" else "0 tags"
    res_t_disp = f"Preserved ({old_t_disp})" if old_t_cnt > 0 else "All My Tags preserved"

    old_c_disp = f"{old_c_cnt} {c_unit}"
    new_c_disp = f"{new_c_cnt} {new_c_unit}" if mode == "merge" else "Re-analyzed"
    res_c_disp = f"Preserved ({old_c_disp})" if old_c_cnt > 0 else "All cue points preserved"

    preservation_defs = [
        ("play_count", "Play Count", old_plays, new_plays, f"Preserved ({old_plays})"),
        ("date_added", "Date Added (StockDate)", old_date, new_date, f"Preserved ({old_date})"),
        ("history_sessions", "History Sets Played", old_h_disp, new_h_disp, res_h_disp),
        ("playlists", "Playlists", old_p_disp, new_p_disp, res_p_disp),
        ("tags", "My Tags", old_t_disp, new_t_disp, res_t_disp),
        ("cues", "Cue Points & Hot Cues", old_c_disp, new_c_disp, res_c_disp),
    ]

    for key, label, old_v, new_v, res_v in preservation_defs:
        f = FieldDiff(
            key=key,
            label=label,
            category="preservation",
            old_val=old_v,
            new_val=new_v,
            default_origin="old",
            is_editable=False,
            help_tip="Historical collection metrics are 100% preserved into the replacement track.",
        )
        f.result_val = res_v
        f.origin = "old"
        fields.append(f)

    return fields


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
        self.old_frame.setToolTip("Click to use Current (Old) value for this field" if self.diff.is_editable else "")
        old_layout = QHBoxLayout(self.old_frame)
        old_layout.setContentsMargins(8, 6, 8, 6)
        old_layout.setSpacing(6)

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
        self.new_frame.setToolTip("Click to use New value for this field" if self.diff.is_editable else "")
        new_layout = QHBoxLayout(self.new_frame)
        new_layout.setContentsMargins(8, 6, 8, 6)
        new_layout.setSpacing(6)

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
            self.btn_use_new.clicked.connect(self._select_new)
            new_layout.addWidget(self.btn_use_new)

        layout.addWidget(self.new_frame, stretch=1)

        # 4. Right Column: Result (Final Applied Value)
        self.result_frame = QFrame()
        self.result_frame.setObjectName("resultCell")
        res_layout = QHBoxLayout(self.result_frame)
        res_layout.setContentsMargins(8, 4, 8, 4)
        res_layout.setSpacing(8)

        # Origin pill: [OLD] / [NEW] / [CUSTOM]
        self.origin_pill = QLabel("OLD")
        self.origin_pill.setFixedHeight(18)
        self.origin_pill.setStyleSheet("font-size: 9px; font-weight: bold; border-radius: 3px; padding: 1px 5px;")
        res_layout.addWidget(self.origin_pill)

        if self.diff.is_editable:
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

        layout.addWidget(self.result_frame, stretch=1)

        # Connect cell click events
        if self.diff.is_editable:
            self.old_frame.mousePressEvent = lambda e: self._select_old()
            self.new_frame.mousePressEvent = lambda e: self._select_new()

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
            self.setStyleSheet(
                f"QFrame#diffRow {{ background-color: transparent; border-left: 3px solid transparent; border-bottom: 1px solid {COLOR_BORDER}; }}"
            )
            self.badge_status.setText("🔒 PRESERVED")
            self.badge_status.setStyleSheet(
                f"background-color: {COLOR_BLUE_BG}; color: {COLOR_BLUE}; border: 1px solid #005f9e; border-radius: 3px; padding: 1px 6px; font-size: 10px; font-weight: bold;"
            )

        # 2. Origin pill
        if self.diff.category == "preservation":
            self.origin_pill.setText("KEEP")
            self.origin_pill.setStyleSheet(
                f"background-color: {COLOR_GREEN_BG}; color: {COLOR_GREEN}; border: 1px solid {COLOR_GREEN_BORDER}; border-radius: 3px; padding: 1px 5px; font-size: 9px; font-weight: bold;"
            )
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

    def _select_old(self) -> None:
        if not self.diff.is_editable:
            return
        self.diff.origin = "old"
        self.diff.result_val = self.diff.old_val
        if hasattr(self, "result_input"):
            self.result_input.blockSignals(True)
            self.result_input.setText(str(self.diff.old_val) if self.diff.old_val is not None else "")
            self.result_input.blockSignals(False)
        self._update_styling()
        self.changed.emit()

    def _select_new(self) -> None:
        if not self.diff.is_editable:
            return
        self.diff.origin = "new"
        self.diff.result_val = self.diff.new_val
        if hasattr(self, "result_input"):
            self.result_input.blockSignals(True)
            self.result_input.setText(str(self.diff.new_val) if self.diff.new_val is not None else "")
            self.result_input.blockSignals(False)
        self._update_styling()
        self.changed.emit()

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
        if origin == "old":
            self._select_old()
        elif origin == "new":
            self._select_new()
        elif origin == "auto":
            # Auto: if new has value and old is empty, take new; else keep old
            old_s = str(self.diff.old_val or "").strip()
            new_s = str(self.diff.new_val or "").strip()
            if not old_s and new_s:
                self._select_new()
            else:
                self._select_old()


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
            opt_layout.addWidget(self.chk_move_folder)

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
        if img_p and os.path.exists(img_p):
            pix = QPixmap(img_p).scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            art_label.setPixmap(pix)
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
        if img_p and os.path.exists(img_p):
            pix = QPixmap(img_p).scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            self.res_art_label.setPixmap(pix)
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
        self._add_section_header("🔒 COLLECTION PRESERVATION (100% GUARANTEED)", "Plays, history sets, date added, playlists, and cue points preserved intact")
        for d in self.diffs:
            if d.category == "preservation":
                row_w = DiffRowWidget(d, self)
                self.table_layout.addWidget(row_w)
                self.row_widgets.append(row_w)

        self.table_layout.addStretch(1)

    def _add_section_header(self, title: str, subtitle: str) -> None:
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

        self.table_layout.addWidget(hdr)

    def _on_row_value_changed(self) -> None:
        self._update_conflict_banner()
        self._update_result_hero_card()

    def _update_result_hero_card(self) -> None:
        """Update live preview card when title or artist is changed."""
        res_title = ""
        res_artist = ""
        for d in self.diffs:
            if d.key == "title":
                res_title = str(d.result_val or "").strip()
            elif d.key == "artist":
                res_artist = str(d.result_val or "").strip()

        self.res_title_label.setText(res_title or "(Untitled)")
        self.res_artist_label.setText(res_artist or "Unknown Artist")

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
        """Apply preset resolution to all editable rows."""
        for row in self.row_widgets:
            if row.diff.is_editable:
                row.set_origin(origin)
        self._update_conflict_banner()
        self._update_result_hero_card()

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
            if d.category == "info":
                result[d.key] = d.result_val
        return result

    def get_options(self) -> Dict[str, Any]:
        """Collect user-selected replacement options."""
        act_idx = self.old_file_combo.currentIndex()
        action_val = "keep" if act_idx == 0 else ("trash" if act_idx == 1 else "backup")
        opts = {
            "old_file_action": action_val,
            "move_new_file_to_track_folder": self.chk_move_folder.isChecked() if hasattr(self, "chk_move_folder") else False,
            "resolved_metadata": self.get_resolved_metadata(),
        }
        return opts

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
