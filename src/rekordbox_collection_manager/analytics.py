"""Analytics and KPI engine for Rekordbox collections and history sessions.

Implements ETL, event-driven temporal normalization (Gig Filter), deterministic KPI calculations
(Exposure, Pick Rate, Consecutive Missed Gigs, Adoption Latency, Lifespan), track clustering
(Peak Bangers, Core Rotation, Deep Cuts, Untested, Dead Weight, Burnout Tracks),
and aggregations for:
1. Crate Health Matrix (Exposure x Pick Rate heatmap)
2. Camelot Wheel Radial Heatmap (24-sector harmonic coverage vs live play)
3. Set Turnover & DNA (100% stacked composition over chronological gigs)
4. Survival Decay Curve (Kaplan-Meier step-line event analysis)
5. Transition & Combos Network Graph (pruned sequence graph)
"""

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rekordbox_collection_manager.utils import (
    build_exclude_streaming_clause,
    build_track_record,
    detect_content_table,
    detect_history_table,
    get_rekordbox_timestamp,
)

logger = logging.getLogger(__name__)

# Standard 24 Camelot mapping
# 1A..12A (Minor), 1B..12B (Major)
TRADITIONAL_KEY_TO_CAMELOT: Dict[str, str] = {
    # Minors (A ring)
    "abm": "1A", "g#m": "1A", "ab minor": "1A", "g# minor": "1A",
    "ebm": "2A", "d#m": "2A", "eb minor": "2A", "d# minor": "2A",
    "bbm": "3A", "a#m": "3A", "bb minor": "3A", "a# minor": "3A",
    "fm": "4A", "f minor": "4A",
    "cm": "5A", "c minor": "5A",
    "gm": "6A", "g minor": "6A",
    "dm": "7A", "d minor": "7A",
    "am": "8A", "a minor": "8A",
    "em": "9A", "e minor": "9A",
    "bm": "10A", "b minor": "10A",
    "f#m": "11A", "gbm": "11A", "f# minor": "11A", "gb minor": "11A",
    "c#m": "12A", "dbm": "12A", "c# minor": "12A", "db minor": "12A",

    # Majors (B ring)
    "b": "1B", "bmaj": "1B", "b major": "1B",
    "f#": "2B", "gb": "2B", "f#maj": "2B", "gbmaj": "2B", "f# major": "2B", "gb major": "2B",
    "db": "3B", "c#": "3B", "dbmaj": "3B", "c#maj": "3B", "db major": "3B", "c# major": "3B",
    "ab": "4B", "g#": "4B", "abmaj": "4B", "g#maj": "4B", "ab major": "4B", "g# major": "4B",
    "eb": "5B", "d#": "5B", "ebmaj": "5B", "d#maj": "5B", "eb major": "5B", "d# major": "5B",
    "bb": "6B", "a#": "6B", "bbmaj": "6B", "a#maj": "6B", "bb major": "6B", "a# major": "6B",
    "f": "7B", "fmaj": "7B", "f major": "7B",
    "c": "8B", "cmaj": "8B", "c major": "8B",
    "g": "9B", "gmaj": "9B", "g major": "9B",
    "d": "10B", "dmaj": "10B", "d major": "10B",
    "a": "11B", "amaj": "11B", "a major": "11B",
    "e": "12B", "emaj": "12B", "e major": "12B",
}

CAMELOT_TO_TRADITIONAL: Dict[str, str] = {
    "1A": "Abm", "1B": "B",
    "2A": "Ebm", "2B": "F#",
    "3A": "Bbm", "3B": "Db",
    "4A": "Fm", "4B": "Ab",
    "5A": "Cm", "5B": "Eb",
    "6A": "Gm", "6B": "Bb",
    "7A": "Dm", "7B": "F",
    "8A": "Am", "8B": "C",
    "9A": "Em", "9B": "G",
    "10A": "Bm", "10B": "D",
    "11A": "F#m", "11B": "A",
    "12A": "Dbm", "12B": "E",
}


def normalize_to_camelot(key_str: Optional[str]) -> Tuple[str, str, int, str]:
    """Normalize a musical key string to standard Camelot notation.

    Returns:
        Tuple: (camelot_code, ring, number, traditional_name)
        e.g. ("8A", "A", 8, "Am") or ("", "", 0, "")
    """
    if not key_str:
        return "", "", 0, ""

    k = key_str.strip()
    if not k:
        return "", "", 0, ""

    # Check if already in Camelot notation like 8A, 11B, 08A
    m = re.match(r"^0?([1-9]|1[0-2])([ABab])$", k)
    if m:
        num = int(m.group(1))
        ring = m.group(2).upper()
        code = f"{num}{ring}"
        trad = CAMELOT_TO_TRADITIONAL.get(code, "")
        return code, ring, num, trad

    # Try mapping from traditional key name
    k_clean = k.lower().replace("  ", " ").strip()
    if k_clean in TRADITIONAL_KEY_TO_CAMELOT:
        code = TRADITIONAL_KEY_TO_CAMELOT[k_clean]
        num = int(code[:-1])
        ring = code[-1]
        trad = CAMELOT_TO_TRADITIONAL.get(code, "")
        return code, ring, num, trad

    return "", "", 0, k


def parse_session_datetime(date_str: Optional[str], session_name: Optional[str] = None) -> datetime:
    """Robustly parse a session datetime from date string or fallback to title regex."""
    if date_str:
        s = str(date_str).strip()
        # Handle '2025-05-10 22:30:00' or '2025-05-10T22:30:00' or with timezone
        s_clean = s.replace("T", " ").split("+")[0].split(".")[0].strip()
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d",
            "%d-%m-%Y %H:%M:%S",
            "%d/%m/%Y",
        ):
            try:
                return datetime.strptime(s_clean, fmt)
            except ValueError:
                pass

    if session_name:
        m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", session_name)
        if m:
            try:
                return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                pass

    return datetime(1970, 1, 1, 0, 0, 0)


@dataclass
class ValidSession:
    """Represents a validated live gig session."""
    session_id: str
    name: str
    date_dt: datetime
    date_str: str
    track_count: int
    duration_sec: int
    track_ids_in_order: List[str] = field(default_factory=list)


@dataclass
class TrackAnalytics:
    """Analytics KPIs and metrics computed for a single track."""
    content_id: str
    title: str
    artist: str
    album: str
    bpm: float
    key: str
    camelot_code: str
    camelot_ring: str
    camelot_num: int
    date_added_dt: datetime
    date_added_str: str
    total_time_sec: int
    rating: int
    dj_play_count: int
    file_path: str = ""

    # Computed Event-Driven KPIs
    exposure_gigs: int = 0          # E_i
    set_presence_gigs: int = 0      # S_i
    pick_rate: float = 0.0          # P_i = S_i / E_i (0% - 100%)
    consecutive_missed: int = 0     # M_i
    adoption_latency: Optional[int] = None  # L_adopt
    active_lifespan_gigs: int = 0
    active_lifespan_days: int = 0
    first_played_session_idx: Optional[int] = None
    last_played_session_idx: Optional[int] = None
    first_played_dt: Optional[datetime] = None
    last_played_dt: Optional[datetime] = None

    # Assigned Cluster
    cluster: str = "Standard"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to track record dictionary compatible with TracksTableWidget."""
        return {
            "id": self.content_id,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "bpm": self.bpm,
            "key": self.camelot_code or self.key,
            "original_key": self.key,
            "camelot_code": self.camelot_code,
            "duration_sec": self.total_time_sec,
            "play_count": self.dj_play_count,
            "rating": self.rating,
            "date_added": self.date_added_str,
            "created_at": self.date_added_str,
            "folder_path": self.file_path,
            "exposure_gigs": self.exposure_gigs,
            "set_presence_gigs": self.set_presence_gigs,
            "pick_rate": self.pick_rate,
            "consecutive_missed": self.consecutive_missed,
            "adoption_latency": self.adoption_latency if self.adoption_latency is not None else "—",
            "cluster": self.cluster,
        }


@dataclass
class CamelotSliceData:
    code: str
    traditional: str
    ring: str
    number: int
    catalog_count: int = 0
    catalog_share_pct: float = 0.0
    live_plays_count: int = 0
    live_share_pct: float = 0.0
    imbalance_pct: float = 0.0  # live_share - catalog_share
    track_ids: List[str] = field(default_factory=list)


@dataclass
class SessionTurnoverData:
    session_id: str
    name: str
    date_str: str
    session_index: int
    total_tracks: int

    # Mode 1: Turnover Temporale (Originale)
    new_entries_count: int = 0
    new_entries_pct: float = 0.0
    new_entries_ids: List[str] = field(default_factory=list)
    in_rotation_count: int = 0
    in_rotation_pct: float = 0.0
    in_rotation_ids: List[str] = field(default_factory=list)
    deep_cuts_count: int = 0
    deep_cuts_pct: float = 0.0
    deep_cuts_ids: List[str] = field(default_factory=list)
    one_shot_count: int = 0
    one_shot_pct: float = 0.0
    one_shot_ids: List[str] = field(default_factory=list)

    # Mode 2: Performance Clusters (Proposta A)
    peak_bangers_count: int = 0
    peak_bangers_pct: float = 0.0
    peak_bangers_ids: List[str] = field(default_factory=list)
    core_rotation_count: int = 0
    core_rotation_pct: float = 0.0
    core_rotation_ids: List[str] = field(default_factory=list)
    untested_count: int = 0
    untested_pct: float = 0.0
    untested_ids: List[str] = field(default_factory=list)
    deep_cuts_cluster_count: int = 0
    deep_cuts_cluster_pct: float = 0.0
    deep_cuts_cluster_ids: List[str] = field(default_factory=list)
    burnout_count: int = 0
    burnout_pct: float = 0.0
    burnout_ids: List[str] = field(default_factory=list)
    standard_count: int = 0
    standard_pct: float = 0.0
    standard_ids: List[str] = field(default_factory=list)

    # Mode 3: Riciclo dal Set Precedente
    repeated_prev_count: int = 0
    repeated_prev_pct: float = 0.0
    repeated_prev_ids: List[str] = field(default_factory=list)
    fresh_tracks_count: int = 0
    fresh_tracks_pct: float = 0.0
    fresh_tracks_ids: List[str] = field(default_factory=list)

    # Set Archetype & Descriptive Profile
    archetype: str = "Signature Flow / Equilibrato"
    archetype_icon: str = "⚖️"
    archetype_desc: str = ""
    archetype_tags: List[str] = field(default_factory=list)


def classify_session_archetype(s: SessionTurnoverData) -> Tuple[str, str, str, List[str]]:
    """Determine the descriptive DJ archetype and secondary tags for a live set.

    Returns:
        (archetype_name, icon_emoji, description, secondary_tags)
    """
    tags: List[str] = []

    # Secondary tags / micro-badges
    if s.peak_bangers_pct >= 35.0:
        tags.append("🔥 High Energy")
    if s.new_entries_pct >= 45.0 and s.session_index > 1:
        tags.append("✨ Fresh Wave")
    if s.session_index > 1 and s.new_entries_pct < 10.0 and s.repeated_prev_pct >= 50.0:
        tags.append("⚠️ Rischio Stagnazione")
    if s.deep_cuts_cluster_pct >= 25.0 or s.deep_cuts_pct >= 25.0:
        tags.append("🕰️ Vintage Digger")

    # Primary Archetype evaluation (hierarchical priority):
    # 1. Genesis: 1st set in history or total catalog renewal
    if s.session_index == 1 or s.new_entries_pct >= 85.0:
        return (
            "Genesis / Battesimo",
            "🌱",
            "Punto zero dello storico: debutto o rinnovo quasi totale del catalogo live.",
            tags,
        )

    # 2. Twin: High retention from immediately preceding set
    if s.session_index > 1 and s.repeated_prev_pct >= 55.0:
        return (
            "Twin / Serata Fotocopia",
            "🔁",
            "Set replica: oltre il 55% della scaletta riproposto dal set precedente.",
            tags,
        )

    # 3. Testing Lab: High percentage of untested / new tracks
    if s.untested_pct >= 35.0 or s.new_entries_pct >= 35.0:
        return (
            "Testing Lab / Rodaggio",
            "🧪",
            "Set di sperimentazione: oltre un terzo di musica nuova mai testata prima.",
            tags,
        )

    # 4. Prime-Time / Festival: Dominance of Peak Bangers & Core Rotation
    if (s.peak_bangers_pct + s.core_rotation_pct >= 65.0) and s.peak_bangers_pct >= 25.0:
        return (
            "Prime-Time / Festival",
            "🚀",
            "Set ad alto impatto e zero rischi: dominano hit collaudate e rotazione fissa.",
            tags,
        )

    # 5. Digger's Set: High percentage of Deep Cuts / Ripescaggi
    if s.deep_cuts_cluster_pct >= 25.0 or s.deep_cuts_pct >= 25.0:
        return (
            "Digger's Set / Ricerca",
            "🔍",
            "Set da collezionista: ampio spazio a perle ripescate dal catalogo storico.",
            tags,
        )

    # 6. Default: Balanced Signature Flow
    return (
        "Signature Flow / Equilibrato",
        "⚖️",
        "Flusso armonioso: solida rotazione con il giusto innesto di novità e ripescaggi.",
        tags,
    )


@dataclass
class SurvivalStepData:
    set_number: int
    total_exposed: int
    unplayed_count: int
    unplayed_pct: float  # 100.0% -> 0.0%


@dataclass
class TransitionEdgeData:
    source_id: str
    source_title: str
    source_artist: str
    target_id: str
    target_title: str
    target_artist: str
    count: int
    session_names: List[str] = field(default_factory=list)


@dataclass
class AnalyticsSnapshot:
    """Complete aggregated analytics dataset ready for instant UI visualization."""
    total_tracks: int
    total_valid_sessions: int
    total_live_plays: int
    burnout_threshold_plays: int

    # KPI counts
    peak_bangers_count: int
    core_rotation_count: int
    deep_cuts_count: int
    untested_count: int
    dead_weight_count: int
    burnout_count: int
    avg_rotation_rate: float
    avg_new_entries_rate: float

    tracks_by_id: Dict[str, TrackAnalytics]
    cluster_track_ids: Dict[str, List[str]]

    # Vista 1: Crate Health Matrix
    # Keys: (exposure_bracket, pick_rate_bracket) -> List[str] of track IDs
    health_matrix: Dict[Tuple[str, str], List[str]]
    exposure_brackets: List[str]
    pick_rate_brackets: List[str]

    # Vista 2: Camelot Wheel
    camelot_slices: Dict[str, CamelotSliceData]

    # Vista 3: Set Turnover & DNA
    turnover_series: List[SessionTurnoverData]
    stagnation_risk: bool

    # Vista 4: Survival Decay
    survival_curve: List[SurvivalStepData]
    point_of_no_return_set: int

    # Vista 5: Transition & Combos Network
    transition_edges: List[TransitionEdgeData]
    transition_nodes: Dict[str, Dict[str, Any]]


EXPOSURE_BRACKETS = [
    "1-2 Set (Test)",
    "3-5 Set (Focus)",
    "6-10 Set (Avanzato)",
    ">10 Set (Veterano)",
]

PICK_RATE_BRACKETS = [
    "76-100%",
    "51-75%",
    "26-50%",
    "1-25%",
    "0% (Mai suonata)",
]


def _get_exposure_bracket(exposure: int) -> Optional[str]:
    if 1 <= exposure <= 2:
        return "1-2 Set (Test)"
    elif 3 <= exposure <= 5:
        return "3-5 Set (Focus)"
    elif 6 <= exposure <= 10:
        return "6-10 Set (Avanzato)"
    elif exposure > 10:
        return ">10 Set (Veterano)"
    return None


def _get_pick_rate_bracket(pick_rate: float) -> str:
    if pick_rate <= 0.0001:
        return "0% (Mai suonata)"
    elif pick_rate <= 25.0:
        return "1-25%"
    elif pick_rate <= 50.0:
        return "26-50%"
    elif pick_rate <= 75.0:
        return "51-75%"
    else:
        return "76-100%"


def compute_analytics(
    engine: Engine,
    min_session_duration_sec: int = 1200,  # 20 minutes
    min_session_tracks: int = 5,
    transition_min_frequency: int = 2,
    burnout_percentile: float = 80.0,
) -> AnalyticsSnapshot:
    """Run full ETL and compute deterministic analytics from Rekordbox SQLite database."""
    content_table = detect_content_table(engine)
    history_table = detect_history_table(engine)

    with engine.connect() as conn:
        tables_res = conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
        existing_tables = {r[0] for r in tables_res}

        # ----------------------------------------------------------------------
        # 1. Fetch Collection Tracks
        # ----------------------------------------------------------------------
        c_cols = {r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({content_table});").fetchall()}
        has_c_del = "rb_local_deleted" in c_cols
        c_del_clause = f"AND (c.rb_local_deleted = 0 OR c.rb_local_deleted IS NULL)" if has_c_del else ""
        c_stream_clause = build_exclude_streaming_clause(c_cols, table_alias="c")

        # Key column resolution
        has_djmd_key = "djmdKey" in existing_tables
        key_select = "COALESCE(k.ScaleName, '') AS ScaleName" if has_djmd_key else "'' AS ScaleName"
        key_join = "LEFT JOIN djmdKey k ON c.KeyID = k.ID" if has_djmd_key else ""

        # Artist resolution
        has_djmd_artist = "djmdArtist" in existing_tables
        artist_select = "COALESCE(a.Name, c.SrcArtistName, 'Unknown Artist') AS ArtistName" if has_djmd_artist else "COALESCE(c.SrcArtistName, 'Unknown Artist') AS ArtistName"
        artist_join = "LEFT JOIN djmdArtist a ON c.ArtistID = a.ID" if has_djmd_artist else ""

        # Album resolution
        has_djmd_album = "djmdAlbum" in existing_tables
        album_select = "COALESCE(al.Name, '') AS AlbumName" if has_djmd_album else "'' AS AlbumName"
        album_join = "LEFT JOIN djmdAlbum al ON c.AlbumID = al.ID" if has_djmd_album else ""

        # Date added resolution
        date_col = "c.created_at" if "created_at" in c_cols else ("c.DateCreated" if "DateCreated" in c_cols else ("c.DateAdded" if "DateAdded" in c_cols else "NULL"))
        folder_col = "c.FolderPath" if "FolderPath" in c_cols else "''"
        bpm_col = "c.BPM" if "BPM" in c_cols else "0"
        time_col = "c.Length" if "Length" in c_cols else ("c.TotalTime" if "TotalTime" in c_cols else "0")
        rating_col = "c.Rating" if "Rating" in c_cols else ("c.rating" if "rating" in c_cols else "0")
        plays_col = "c.DJPlayCount" if "DJPlayCount" in c_cols else ("c.play_count" if "play_count" in c_cols else "0")

        track_query = text(f"""
            SELECT
                c.ID,
                COALESCE(c.Title, '(Untitled)') AS Title,
                {artist_select},
                {album_select},
                {key_select},
                {bpm_col} AS BPM,
                {time_col} AS TotalTime,
                {rating_col} AS Rating,
                {plays_col} AS DJPlayCount,
                {date_col} AS DateAdded,
                {folder_col} AS FolderPath
            FROM {content_table} c
            {artist_join}
            {album_join}
            {key_join}
            WHERE 1=1
            {c_del_clause}
            {c_stream_clause};
        """)
        raw_tracks = conn.execute(track_query).mappings().fetchall()

        # ----------------------------------------------------------------------
        # 2. Fetch History Sessions & Played Songs
        # ----------------------------------------------------------------------
        song_history_table = "djmdSongHistory" if "djmdSongHistory" in existing_tables else "history_song"
        h_cols = {r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({history_table});").fetchall()}
        sh_cols = {r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({song_history_table});").fetchall()} if song_history_table in existing_tables else set()

        has_h_del = "rb_local_deleted" in h_cols
        h_del_clause = f"AND (h.rb_local_deleted = 0 OR h.rb_local_deleted IS NULL)" if has_h_del else ""

        has_sh_del = "rb_local_deleted" in sh_cols
        sh_del_clause = f"AND (sh.rb_local_deleted = 0 OR sh.rb_local_deleted IS NULL)" if has_sh_del else ""

        h_id_col = "h.ID" if "ID" in h_cols else "h.history_id"
        h_name_col = "h.Name" if "Name" in h_cols else "h.name"
        h_date_col = "h.DateCreated" if "DateCreated" in h_cols else ("h.created_at" if "created_at" in h_cols else "NULL")

        sh_order_col = "sh.HistoryOrder" if "HistoryOrder" in sh_cols else ("sh.sort_order" if "sort_order" in sh_cols else "sh.ID")
        sh_content_col = "sh.ContentID" if "ContentID" in sh_cols else "sh.content_id"
        sh_h_id_col = "sh.HistoryID" if "HistoryID" in sh_cols else "sh.history_id"

        history_rows: List[Dict[str, Any]] = []
        if song_history_table in existing_tables:
            sessions_query = text(f"""
                SELECT
                    {h_id_col} AS HistoryID,
                    {h_name_col} AS SessionName,
                    {h_date_col} AS DateCreated,
                    {sh_content_col} AS ContentID,
                    {sh_order_col} AS HistoryOrder,
                    {time_col} AS TrackDuration
                FROM {history_table} h
                JOIN {song_history_table} sh ON {h_id_col} = {sh_h_id_col}
                JOIN {content_table} c ON {sh_content_col} = c.ID
                WHERE 1=1
                {h_del_clause}
                {sh_del_clause}
                {c_del_clause}
                ORDER BY {h_id_col}, {sh_order_col} ASC;
            """)
            history_rows = conn.execute(sessions_query).mappings().fetchall()

    # --------------------------------------------------------------------------
    # 3. Process History into Valid Sessions (Gig Filter)
    # --------------------------------------------------------------------------
    grouped_sessions: Dict[str, Dict[str, Any]] = {}
    for r in history_rows:
        hid = str(r["HistoryID"])
        if hid not in grouped_sessions:
            grouped_sessions[hid] = {
                "id": hid,
                "name": str(r["SessionName"] or "(Untitled)"),
                "date_raw": str(r["DateCreated"] or ""),
                "tracks": [],
                "duration_sum": 0,
            }
        cid = str(r["ContentID"])
        dur = int(r["TrackDuration"] or 0)
        grouped_sessions[hid]["tracks"].append(cid)
        grouped_sessions[hid]["duration_sum"] += dur

    valid_sessions: List[ValidSession] = []
    for hid, data in grouped_sessions.items():
        dt = parse_session_datetime(data["date_raw"], data["name"])
        t_count = len(data["tracks"])
        dur = data["duration_sum"]

        # Gig Filter: Duration >= min_duration OR track_count >= min_tracks
        # If duration is 0, rely strictly on track_count >= min_tracks
        is_valid = (t_count >= min_session_tracks) and (dur >= min_session_duration_sec or dur == 0)
        if is_valid:
            valid_sessions.append(
                ValidSession(
                    session_id=hid,
                    name=data["name"],
                    date_dt=dt,
                    date_str=dt.strftime("%Y-%m-%d"),
                    track_count=t_count,
                    duration_sec=dur,
                    track_ids_in_order=data["tracks"],
                )
            )

    # Sort valid sessions chronologically: oldest first -> newest last
    valid_sessions.sort(key=lambda s: (s.date_dt, s.session_id))
    num_valid_sessions = len(valid_sessions)

    # Precompute session track presence sets
    session_presence_sets: List[Set[str]] = [set(s.track_ids_in_order) for s in valid_sessions]

    # Precompute first and last played valid session indices for each track
    track_first_valid_idx: Dict[str, int] = {}
    track_last_valid_idx: Dict[str, int] = {}
    track_session_presence_count: Dict[str, int] = {}
    total_live_plays = 0

    for idx, s in enumerate(valid_sessions):
        total_live_plays += s.track_count
        for cid in s.track_ids_in_order:
            if cid not in track_first_valid_idx:
                track_first_valid_idx[cid] = idx
            track_last_valid_idx[cid] = idx

        for cid in session_presence_sets[idx]:
            track_session_presence_count[cid] = track_session_presence_count.get(cid, 0) + 1

    # --------------------------------------------------------------------------
    # 4. Process Collection Tracks & Compute KPIs
    # --------------------------------------------------------------------------
    tracks_by_id: Dict[str, TrackAnalytics] = {}
    play_counts_nonzero: List[int] = []

    for r in raw_tracks:
        cid = str(r["ID"])
        bpm_raw = r["BPM"] or 0
        bpm_val = (float(bpm_raw) / 100.0) if bpm_raw > 1000 else float(bpm_raw)
        key_raw = str(r["ScaleName"] or "")
        code, ring, num, _ = normalize_to_camelot(key_raw)
        dt_added = parse_session_datetime(str(r["DateAdded"] or ""))
        dj_plays = int(r["DJPlayCount"] or 0)
        if dj_plays > 0:
            play_counts_nonzero.append(dj_plays)

        t_obj = TrackAnalytics(
            content_id=cid,
            title=str(r["Title"] or "(Untitled)"),
            artist=str(r["ArtistName"] or "Unknown Artist"),
            album=str(r["AlbumName"] or ""),
            bpm=bpm_val,
            key=key_raw,
            camelot_code=code,
            camelot_ring=ring,
            camelot_num=num,
            date_added_dt=dt_added,
            date_added_str=dt_added.strftime("%Y-%m-%d") if dt_added.year > 1970 else "",
            total_time_sec=int(r["TotalTime"] or 0),
            rating=int(r["Rating"] or 0),
            dj_play_count=dj_plays,
            file_path=str(r["FolderPath"] or ""),
        )
        tracks_by_id[cid] = t_obj

    # Dynamic Burnout Threshold: 80th percentile of non-zero play counts
    burnout_threshold = 10
    if play_counts_nonzero:
        play_counts_nonzero.sort()
        idx_p = int(math.ceil((burnout_percentile / 100.0) * len(play_counts_nonzero))) - 1
        idx_p = max(0, min(idx_p, len(play_counts_nonzero) - 1))
        burnout_threshold = max(5, play_counts_nonzero[idx_p])

    # Compute Event-Driven metrics for each track
    now_dt = datetime.now()
    three_months_ago = datetime.fromtimestamp(now_dt.timestamp() - (90 * 86400))

    clusters_map: Dict[str, List[str]] = {
        "Peak Bangers": [],
        "Core Rotation": [],
        "Deep Cuts / Occasionals": [],
        "In Incubazione / Untested": [],
        "Dead Weight (Zavorra)": [],
        "Burnout Tracks": [],
        "Standard": [],
    }

    # Vista 1 Grid
    health_matrix: Dict[Tuple[str, str], List[str]] = {
        (e_b, p_b): [] for e_b in EXPOSURE_BRACKETS for p_b in PICK_RATE_BRACKETS
    }

    # Vista 2 Camelot Slices (24 slices + 1 unassigned)
    camelot_slices: Dict[str, CamelotSliceData] = {}
    for n in range(1, 13):
        for r_code in ("A", "B"):
            c_code = f"{n}{r_code}"
            camelot_slices[c_code] = CamelotSliceData(
                code=c_code,
                traditional=CAMELOT_TO_TRADITIONAL.get(c_code, ""),
                ring="minor" if r_code == "A" else "major",
                number=n,
            )

    total_catalog_tracks = max(1, len(tracks_by_id))

    for cid, t in tracks_by_id.items():
        # First played / last played
        f_idx = track_first_valid_idx.get(cid)
        l_idx = track_last_valid_idx.get(cid)
        t.first_played_session_idx = f_idx
        t.last_played_session_idx = l_idx
        if f_idx is not None:
            t.first_played_dt = valid_sessions[f_idx].date_dt
        if l_idx is not None:
            t.last_played_dt = valid_sessions[l_idx].date_dt

        # Calculate Exposure E_i: valid sessions with date >= min(date_added, first_played)
        effective_added_dt = t.date_added_dt
        if t.first_played_dt and (effective_added_dt.year <= 1970 or t.first_played_dt < effective_added_dt):
            effective_added_dt = t.first_played_dt

        # Find first session >= effective_added_dt
        start_session_idx = num_valid_sessions  # default: added after all sessions
        if effective_added_dt.year <= 1970:
            # If no date added is recorded, use first played index or assume exposed to all
            start_session_idx = f_idx if f_idx is not None else 0
        else:
            for s_i, s in enumerate(valid_sessions):
                if s.date_dt >= effective_added_dt:
                    start_session_idx = s_i
                    break

        # If played before start_session_idx, clamp start_session_idx
        if f_idx is not None and f_idx < start_session_idx:
            start_session_idx = f_idx

        e_i = max(0, num_valid_sessions - start_session_idx)
        s_i = track_session_presence_count.get(cid, 0)
        p_i = (float(s_i) / float(e_i) * 100.0) if e_i > 0 else 0.0

        # Missed Gigs M_i: consecutive valid sessions since last played (or since introduction)
        if l_idx is not None:
            m_i = (num_valid_sessions - 1) - l_idx
        else:
            m_i = e_i

        # Adoption Latency
        if f_idx is not None:
            l_adopt = max(0, f_idx - start_session_idx)
        else:
            l_adopt = None

        # Lifespan
        if f_idx is not None and l_idx is not None:
            lifespan_gigs = l_idx - f_idx + 1
            lifespan_days = max(0, (t.last_played_dt - t.first_played_dt).days) if t.last_played_dt and t.first_played_dt else 0
        else:
            lifespan_gigs = 0
            lifespan_days = 0

        t.exposure_gigs = e_i
        t.set_presence_gigs = s_i
        t.pick_rate = round(p_i, 1)
        t.consecutive_missed = m_i
        t.adoption_latency = l_adopt
        t.active_lifespan_gigs = lifespan_gigs
        t.active_lifespan_days = lifespan_days

        # Cluster Assignment
        # 1. Untested: E_i <= 2 AND S_i == 0
        if e_i <= 2 and s_i == 0:
            c_name = "In Incubazione / Untested"
        # 2. Burnout Tracks: high play count and not played in >= 8 sets
        elif t.dj_play_count >= burnout_threshold and m_i >= 8 and s_i > 0:
            c_name = "Burnout Tracks"
        # 3. Peak Bangers: E_i >= 4 AND P_i >= 60%
        elif e_i >= 4 and p_i >= 60.0:
            c_name = "Peak Bangers"
        # 4. Core Rotation: E_i >= 4 AND 25% <= P_i < 60%
        elif e_i >= 4 and 25.0 <= p_i < 60.0:
            c_name = "Core Rotation"
        # 5. Deep Cuts / Occasionals: E_i >= 6 AND P_i < 25% AND played at least once in last 3 months
        elif e_i >= 6 and p_i < 25.0 and s_i > 0 and (t.last_played_dt and t.last_played_dt >= three_months_ago):
            c_name = "Deep Cuts / Occasionals"
        # 6. Dead Weight (Zavorra): (E_i >= 5 AND P_i == 0%) OR M_i >= 6
        elif (e_i >= 5 and s_i == 0) or (m_i >= 6 and e_i >= 6):
            c_name = "Dead Weight (Zavorra)"
        else:
            c_name = "Standard"

        t.cluster = c_name
        clusters_map[c_name].append(cid)

        # Vista 1 Health Matrix placement
        e_bracket = _get_exposure_bracket(e_i)
        p_bracket = _get_pick_rate_bracket(p_i)
        if e_bracket and p_bracket:
            health_matrix[(e_bracket, p_bracket)].append(cid)

        # Vista 2 Camelot Slice accumulation
        if t.camelot_code in camelot_slices:
            slice_data = camelot_slices[t.camelot_code]
            slice_data.catalog_count += 1
            slice_data.track_ids.append(cid)

    # --------------------------------------------------------------------------
    # Vista 2 Camelot Shares & Live Play Frequencies
    # --------------------------------------------------------------------------
    # Count live plays per camelot key
    for s in valid_sessions:
        for cid in s.track_ids_in_order:
            t_obj = tracks_by_id.get(cid)
            if t_obj and t_obj.camelot_code in camelot_slices:
                camelot_slices[t_obj.camelot_code].live_plays_count += 1

    total_valid_live_plays = max(1, total_live_plays)
    for c_code, s_data in camelot_slices.items():
        s_data.catalog_share_pct = round((s_data.catalog_count / total_catalog_tracks) * 100.0, 1)
        s_data.live_share_pct = round((s_data.live_plays_count / total_valid_live_plays) * 100.0, 1)
        s_data.imbalance_pct = round(s_data.live_share_pct - s_data.catalog_share_pct, 1)

    # --------------------------------------------------------------------------
    # Vista 3 Set Turnover & DNA (100% Stacked Composition - Point-in-Time)
    # --------------------------------------------------------------------------
    turnover_series: List[SessionTurnoverData] = []
    seen_so_far: Set[str] = set()

    # Track point-in-time history: plays count and last played session index
    track_prior_plays: Dict[str, int] = {}
    track_last_played_idx: Dict[str, int] = {}

    for idx, s in enumerate(valid_sessions):
        t_total = s.track_count
        if t_total == 0:
            continue

        new_entries: List[str] = []
        in_rotation: List[str] = []
        deep_cuts: List[str] = []
        one_shot: List[str] = []

        # Performance Clusters (Point-in-Time as of session idx - NO FUTURE LEAKAGE)
        pb_ids: List[str] = []
        core_ids: List[str] = []
        untested_ids: List[str] = []
        deep_cluster_ids: List[str] = []
        burnout_ids: List[str] = []
        standard_ids: List[str] = []

        # Previous session retention
        prev_set_tracks = session_presence_sets[idx - 1] if idx > 0 else set()
        rep_prev_ids: List[str] = []
        fresh_ids: List[str] = []

        # Tracks played in the last 3 preceding sessions
        recent_3_sets: Set[str] = set()
        for prev_i in range(max(0, idx - 3), idx):
            recent_3_sets.update(session_presence_sets[prev_i])

        recent_5_indices = list(range(max(0, idx - 5), idx))

        for cid in s.track_ids_in_order:
            prior_plays = track_prior_plays.get(cid, 0)
            last_idx = track_last_played_idx.get(cid)
            missed_since_last = (idx - 1 - last_idx) if last_idx is not None else None

            # Presence in recent sessions
            plays_in_last_3 = sum(1 for prev_i in range(max(0, idx - 3), idx) if cid in session_presence_sets[prev_i])
            plays_in_last_5 = sum(1 for prev_i in recent_5_indices if cid in session_presence_sets[prev_i])

            # ------------------------------------------------------------------
            # 1. Turnover Temporale (Point-in-Time)
            # ------------------------------------------------------------------
            if prior_plays == 0:
                new_entries.append(cid)
            elif cid in recent_3_sets:
                in_rotation.append(cid)
            else:
                if prior_plays == 1:
                    one_shot.append(cid)
                else:
                    deep_cuts.append(cid)

            # ------------------------------------------------------------------
            # 2. Performance Clusters (Point-in-Time al momento del set)
            # Note: nessun brano suonato in un live set è una "Zavorra".
            # ------------------------------------------------------------------
            # A) Novità / Debutto assoluto: mai suonata prima nei live set
            if prior_plays == 0:
                untested_ids.append(cid)
            # B) Sovraesposta / Burnout temporale: suonata a raffica nelle ultime serate
            elif (idx >= 3 and plays_in_last_3 >= 3) or (idx >= 4 and plays_in_last_5 >= 4):
                burnout_ids.append(cid)
            # C) Deep Cuts / Ripescaggio dal catalogo: non suonata da almeno 3 serate
            elif missed_since_last is not None and missed_since_last >= 3:
                deep_cluster_ids.append(cid)
            # D) Peak Bangers: hit costante del periodo (presente in >= 2 delle ultime 3 serate o >= 50% di presenza)
            elif plays_in_last_3 >= 2 or (prior_plays >= 3 and (prior_plays / max(1, idx)) >= 0.5):
                pb_ids.append(cid)
            # E) Core Rotation: in rotazione attiva recente
            elif plays_in_last_3 >= 1 or (missed_since_last is not None and missed_since_last <= 2):
                core_ids.append(cid)
            else:
                standard_ids.append(cid)

            # ------------------------------------------------------------------
            # 3. Direct Retention (dal set precedente)
            # ------------------------------------------------------------------
            if idx > 0 and cid in prev_set_tracks:
                rep_prev_ids.append(cid)
            else:
                fresh_ids.append(cid)

        # Update point-in-time tracking after completing session idx
        for cid in session_presence_sets[idx]:
            track_prior_plays[cid] = track_prior_plays.get(cid, 0) + 1
            track_last_played_idx[cid] = idx

        # Update global seen tracks
        seen_so_far.update(s.track_ids_in_order)

        t_data = SessionTurnoverData(
            session_id=s.session_id,
            name=s.name,
            date_str=s.date_str,
            session_index=idx + 1,
            total_tracks=t_total,
            # Mode 1: Turnover Temporale
            new_entries_count=len(new_entries),
            new_entries_pct=round((len(new_entries) / t_total) * 100.0, 1),
            new_entries_ids=new_entries,
            in_rotation_count=len(in_rotation),
            in_rotation_pct=round((len(in_rotation) / t_total) * 100.0, 1),
            in_rotation_ids=in_rotation,
            deep_cuts_count=len(deep_cuts),
            deep_cuts_pct=round((len(deep_cuts) / t_total) * 100.0, 1),
            deep_cuts_ids=deep_cuts,
            one_shot_count=len(one_shot),
            one_shot_pct=round((len(one_shot) / t_total) * 100.0, 1),
            one_shot_ids=one_shot,
            # Mode 2: Performance Clusters
            peak_bangers_count=len(pb_ids),
            peak_bangers_pct=round((len(pb_ids) / t_total) * 100.0, 1),
            peak_bangers_ids=pb_ids,
            core_rotation_count=len(core_ids),
            core_rotation_pct=round((len(core_ids) / t_total) * 100.0, 1),
            core_rotation_ids=core_ids,
            untested_count=len(untested_ids),
            untested_pct=round((len(untested_ids) / t_total) * 100.0, 1),
            untested_ids=untested_ids,
            deep_cuts_cluster_count=len(deep_cluster_ids),
            deep_cuts_cluster_pct=round((len(deep_cluster_ids) / t_total) * 100.0, 1),
            deep_cuts_cluster_ids=deep_cluster_ids,
            burnout_count=len(burnout_ids),
            burnout_pct=round((len(burnout_ids) / t_total) * 100.0, 1),
            burnout_ids=burnout_ids,
            standard_count=len(standard_ids),
            standard_pct=round((len(standard_ids) / t_total) * 100.0, 1),
            standard_ids=standard_ids,
            # Mode 3: Riciclo Set Precedente
            repeated_prev_count=len(rep_prev_ids),
            repeated_prev_pct=round((len(rep_prev_ids) / t_total) * 100.0, 1),
            repeated_prev_ids=rep_prev_ids,
            fresh_tracks_count=len(fresh_ids),
            fresh_tracks_pct=round((len(fresh_ids) / t_total) * 100.0, 1),
            fresh_tracks_ids=fresh_ids,
        )
        arch_name, arch_icon, arch_desc, arch_tags = classify_session_archetype(t_data)
        t_data.archetype = arch_name
        t_data.archetype_icon = arch_icon
        t_data.archetype_desc = arch_desc
        t_data.archetype_tags = arch_tags

        turnover_series.append(t_data)

    # Stagnation warning: check average new entries in the last 3 sessions
    stagnation_risk = False
    if len(turnover_series) >= 3:
        last_3_new_pct = sum(t.new_entries_pct for t in turnover_series[-3:]) / 3.0
        if last_3_new_pct < 15.0:
            stagnation_risk = True

    # --------------------------------------------------------------------------
    # Vista 4 Survival Decay Curve (Kaplan-Meier Step Line)
    # --------------------------------------------------------------------------
    # For set offset k = 0, 1, 2, ...
    # How many tracks exposed to at least k sets were still at 0 plays at step k?
    survival_curve: List[SurvivalStepData] = []
    max_k = min(20, num_valid_sessions)
    detected_pnr: Optional[int] = None

    if max_k > 0:
        prev_pct = 100.0
        for k in range(0, max_k + 1):
            exposed_k = [t for t in tracks_by_id.values() if t.exposure_gigs >= k]
            total_exp = len(exposed_k)
            if total_exp == 0:
                continue

            if k == 0:
                unplayed_count = total_exp
                unplayed_pct = 100.0
            else:
                # Tracks whose adoption latency is >= k or never played
                unplayed = [
                    t for t in exposed_k
                    if t.adoption_latency is None or t.adoption_latency >= k
                ]
                unplayed_count = len(unplayed)
                unplayed_pct = round((unplayed_count / total_exp) * 100.0, 1)

            survival_curve.append(
                SurvivalStepData(
                    set_number=k,
                    total_exposed=total_exp,
                    unplayed_count=unplayed_count,
                    unplayed_pct=unplayed_pct,
                )
            )

            # Detect point of no return: first set where adoption plateau occurs (delta < 2.0%)
            if k >= 3 and (prev_pct - unplayed_pct) < 2.0 and detected_pnr is None:
                detected_pnr = k
            prev_pct = unplayed_pct

    # Fallback to 4 (or max_k) only if insufficient plateau data
    point_of_no_return = detected_pnr if detected_pnr is not None else (min(4, max_k) if max_k > 0 else 4)

    # --------------------------------------------------------------------------
    # Vista 5 Transition & Combos Network Graph
    # --------------------------------------------------------------------------
    transitions_counter: Dict[Tuple[str, str], int] = {}
    transitions_sessions: Dict[Tuple[str, str], List[str]] = {}

    for s in valid_sessions:
        t_seq = s.track_ids_in_order
        for i in range(len(t_seq) - 1):
            src_id = t_seq[i]
            tgt_id = t_seq[i + 1]
            if src_id == tgt_id:
                continue
            pair = (src_id, tgt_id)
            transitions_counter[pair] = transitions_counter.get(pair, 0) + 1
            if pair not in transitions_sessions:
                transitions_sessions[pair] = []
            if s.name not in transitions_sessions[pair]:
                transitions_sessions[pair].append(s.name)

    transition_edges: List[TransitionEdgeData] = []
    transition_nodes: Dict[str, Dict[str, Any]] = {}

    for (s_id, t_id), cnt in sorted(transitions_counter.items(), key=lambda item: item[1], reverse=True):
        if cnt >= transition_min_frequency:
            src_track = tracks_by_id.get(s_id)
            tgt_track = tracks_by_id.get(t_id)
            if not src_track or not tgt_track:
                continue

            transition_edges.append(
                TransitionEdgeData(
                    source_id=s_id,
                    source_title=src_track.title,
                    source_artist=src_track.artist,
                    target_id=t_id,
                    target_title=tgt_track.title,
                    target_artist=tgt_track.artist,
                    count=cnt,
                    session_names=transitions_sessions.get((s_id, t_id), []),
                )
            )

            for tr in (src_track, tgt_track):
                if tr.content_id not in transition_nodes:
                    transition_nodes[tr.content_id] = {
                        "id": tr.content_id,
                        "title": tr.title,
                        "artist": tr.artist,
                        "bpm": tr.bpm,
                        "key": tr.camelot_code or tr.key,
                        "camelot_num": tr.camelot_num,
                        "ring": tr.camelot_ring,
                        "plays": tr.dj_play_count,
                        "cluster": tr.cluster,
                    }

    # Global KPI averages
    avg_rotation = (
        sum(t.in_rotation_pct for t in turnover_series) / len(turnover_series)
        if turnover_series
        else 0.0
    )
    avg_new_entries = (
        sum(t.new_entries_pct for t in turnover_series) / len(turnover_series)
        if turnover_series
        else 0.0
    )

    return AnalyticsSnapshot(
        total_tracks=len(tracks_by_id),
        total_valid_sessions=num_valid_sessions,
        total_live_plays=total_live_plays,
        burnout_threshold_plays=burnout_threshold,
        peak_bangers_count=len(clusters_map["Peak Bangers"]),
        core_rotation_count=len(clusters_map["Core Rotation"]),
        deep_cuts_count=len(clusters_map["Deep Cuts / Occasionals"]),
        untested_count=len(clusters_map["In Incubazione / Untested"]),
        dead_weight_count=len(clusters_map["Dead Weight (Zavorra)"]),
        burnout_count=len(clusters_map["Burnout Tracks"]),
        avg_rotation_rate=round(avg_rotation, 1),
        avg_new_entries_rate=round(avg_new_entries, 1),
        tracks_by_id=tracks_by_id,
        cluster_track_ids=clusters_map,
        health_matrix=health_matrix,
        exposure_brackets=EXPOSURE_BRACKETS,
        pick_rate_brackets=PICK_RATE_BRACKETS,
        camelot_slices=camelot_slices,
        turnover_series=turnover_series,
        stagnation_risk=stagnation_risk,
        survival_curve=survival_curve,
        point_of_no_return_set=point_of_no_return,
        transition_edges=transition_edges,
        transition_nodes=transition_nodes,
    )


# ------------------------------------------------------------------------------
# Export Utilities: M3U8, XML, and Rekordbox Playlist creation
# ------------------------------------------------------------------------------

def export_tracks_to_m3u8(file_path: str, tracks: List[Dict[str, Any]]) -> int:
    """Export list of track dictionaries to standard UTF-8 M3U8 playlist file."""
    count = 0
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for t in tracks:
            path = t.get("folder_path") or t.get("path") or ""
            artist = t.get("artist") or "Unknown"
            title = t.get("title") or "Unknown"
            duration = int(t.get("duration_sec") or t.get("total_time_sec") or 0)
            f.write(f"#EXTINF:{duration},{artist} - {title}\n")
            f.write(f"{path}\n")
            count += 1
    return count


def export_tracks_to_rekordbox_xml(file_path: str, playlist_name: str, tracks: List[Dict[str, Any]]) -> int:
    """Export track dictionaries to Rekordbox-compatible XML format."""
    import xml.etree.ElementTree as ET

    root = ET.Element("DJ_PLAYLISTS", Version="1.0.0")
    product = ET.SubElement(root, "PRODUCT", Name="rekordbox", Version="6.0.0", Company="Pioneer DJ")
    collection = ET.SubElement(root, "COLLECTION", Entries=str(len(tracks)))

    playlists = ET.SubElement(root, "PLAYLISTS")
    node_root = ET.SubElement(playlists, "NODE", Type="0", Name="ROOT", Count="1")
    pl_node = ET.SubElement(node_root, "NODE", Name=playlist_name, Type="1", KeyType="0", Entries=str(len(tracks)))

    count = 0
    for idx, t in enumerate(tracks, 1):
        tid = str(t.get("id") or idx)
        title = t.get("title") or "Unknown"
        artist = t.get("artist") or "Unknown"
        album = t.get("album") or ""
        bpm = f"{float(t.get('bpm') or 0):.2f}"
        duration = str(int(t.get("duration_sec") or 0))
        location = t.get("folder_path") or ""
        if location and not location.startswith("file://"):
            location = f"file://localhost{location}"

        ET.SubElement(
            collection,
            "TRACK",
            TrackID=tid,
            Name=title,
            Artist=artist,
            Album=album,
            TotalTime=duration,
            AverageBpm=bpm,
            Location=location,
        )
        ET.SubElement(pl_node, "TRACK", Key=tid)
        count += 1

    tree = ET.ElementTree(root)
    tree.write(file_path, encoding="utf-8", xml_declaration=True)
    return count


def create_purge_playlist_in_rekordbox(
    engine: Engine,
    playlist_name: str,
    track_ids: List[str],
) -> Dict[str, Any]:
    """Create a new purge playlist directly in Rekordbox djmdPlaylist & populate with tracks."""
    from rekordbox_collection_manager.playlists import create_playlist

    # 1. Create playlist
    pl_info = create_playlist(engine, name=playlist_name, parent_id_or_name="root", is_folder=False)
    pl_id = pl_info["id"]

    now_utc = get_rekordbox_timestamp()

    # 2. Insert tracks into djmdSongPlaylist
    with engine.begin() as conn:
        existing_tables = {r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table';").fetchall()}
        if "djmdSongPlaylist" not in existing_tables:
            return pl_info

        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(djmdSongPlaylist);").fetchall()}
        has_id = "ID" in cols
        has_del = "rb_local_deleted" in cols
        has_created = "created_at" in cols
        has_updated = "updated_at" in cols

        # Find max ID or use UUID
        max_id_val = 0
        if has_id:
            try:
                row = conn.execute(text("SELECT MAX(CAST(ID AS INTEGER)) FROM djmdSongPlaylist;")).fetchone()
                if row and row[0]:
                    max_id_val = int(row[0])
            except Exception:
                max_id_val = 100000

        for track_no, cid in enumerate(track_ids, 1):
            max_id_val += 1
            col_names = ["PlaylistID", "ContentID", "TrackNo"]
            placeholders = [":pid", ":cid", ":tno"]
            params: Dict[str, Any] = {
                "pid": pl_id,
                "cid": cid,
                "tno": track_no,
            }

            if has_id:
                col_names.append("ID")
                placeholders.append(":id")
                params["id"] = str(max_id_val)
            if has_del:
                col_names.append("rb_local_deleted")
                placeholders.append(":del")
                params["del"] = 0
            if has_created:
                col_names.append("created_at")
                placeholders.append(":created_at")
                params["created_at"] = now_utc
            if has_updated:
                col_names.append("updated_at")
                placeholders.append(":updated_at")
                params["updated_at"] = now_utc

            sql = f"INSERT INTO djmdSongPlaylist ({', '.join(col_names)}) VALUES ({', '.join(placeholders)});"
            conn.execute(text(sql), params)

    return pl_info

