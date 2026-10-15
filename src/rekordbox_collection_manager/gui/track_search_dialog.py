"""Modal dialog for searching and manually associating tracks with the current collection."""

import os
import re
from typing import Any, Dict, List, Optional
from sqlalchemy import text
from sqlalchemy.engine import Engine

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

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
    COLOR_BORDER,
    COLOR_BORDER_LIGHT,
    COLOR_GREEN,
    COLOR_GREEN_BG,
    COLOR_GREEN_BORDER,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_SECONDARY,
)


def format_duration_sec(seconds: int) -> str:
    """Format seconds into MM:SS."""
    if not seconds or seconds < 0:
        return "--:--"
    m = seconds // 60
    s = seconds % 60
    return f"{m}:{s:02d}"


def format_bpm_val(bpm_raw: Any) -> str:
    """Format BPM cleanly (handles 12800 -> 128.00)."""
    if bpm_raw is None:
        return "—"
    try:
        val = float(bpm_raw)
        if val > 1000:
            val = val / 100.0
        return f"{val:.2f}"
    except (ValueError, TypeError):
        return str(bpm_raw)


class TrackSearchDialog(QDialog):
    """Search dialog allowing manual pairing of an unmatched history track with a collection track."""

    track_selected = Signal(dict)

    def __init__(
        self,
        current_engine: Optional[Engine],
        target_title: str,
        target_artist: str = "",
        target_duration_str: str = "",
        target_duration_sec: int = 0,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.current_engine = current_engine
        self.target_title = target_title or ""
        self.target_artist = target_artist or ""
        self.target_duration_str = target_duration_str or ""
        self.target_duration_sec = target_duration_sec or 0
        self.selected_track_data: Optional[Dict[str, Any]] = None
        self._current_results: List[Dict[str, Any]] = []

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self._execute_search)

        self.setWindowTitle("Associa a Traccia della Collezione")
        self.resize(880, 560)
        self.setMinimumSize(720, 440)
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {COLOR_BG_DARK};
                color: {COLOR_TEXT_PRIMARY};
            }}
        """)

        self._init_ui()

        # Initial search query
        initial_q = self.target_title.strip()
        if initial_q:
            self.search_input.setText(initial_q)
            self.search_input.selectAll()
            self._execute_search()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        # 1. Header Frame with Target Track Info
        header_frame = QFrame()
        header_frame.setStyleSheet(
            f"background-color: {COLOR_BG_PANEL}; border: 1px solid {COLOR_BORDER}; border-radius: 8px; padding: 10px 14px;"
        )
        h_layout = QVBoxLayout(header_frame)
        h_layout.setContentsMargins(4, 4, 4, 4)
        h_layout.setSpacing(4)

        t_title = QLabel("🔗 Associa Traccia della History alla Collezione")
        t_title.setStyleSheet("font-size: 15px; font-weight: 700; color: #ffffff;")
        h_layout.addWidget(t_title)

        info_box = QHBoxLayout()
        info_lbl = QLabel(
            f"Traccia da associare: <span style='color: #ffffff; font-weight: 600;'>{self.target_title}</span> "
            f"&nbsp;•&nbsp; Artista: <span style='color: {COLOR_TEXT_PRIMARY}; font-weight: 600;'>{self.target_artist or 'N/D'}</span> "
            f"&nbsp;•&nbsp; Durata: <span style='color: {COLOR_BLUE}; font-weight: 600;'>{self.target_duration_str or '--:--'}</span>"
        )
        info_lbl.setStyleSheet(f"font-size: 12px; color: {COLOR_TEXT_SECONDARY};")
        info_box.addWidget(info_lbl)
        info_box.addStretch(1)
        h_layout.addLayout(info_box)

        layout.addWidget(header_frame)

        # 2. Search Bar
        search_box = QHBoxLayout()
        search_box.setSpacing(8)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Cerca per titolo, artista o nome del file audio...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setFixedHeight(34)
        self.search_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {COLOR_BG_INPUT};
                color: {COLOR_TEXT_PRIMARY};
                border: 1px solid {COLOR_BORDER};
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 12px;
            }}
            QLineEdit:focus {{
                border: 1px solid {COLOR_BLUE};
                background-color: #1a1e29;
            }}
        """)
        self.search_input.textChanged.connect(self._on_search_text_changed)
        self.search_input.returnPressed.connect(self._on_return_pressed)
        search_box.addWidget(self.search_input, stretch=1)

        self.status_lbl = QLabel("0 risultati")
        self.status_lbl.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 500;")
        search_box.addWidget(self.status_lbl)

        layout.addLayout(search_box)

        # 3. Results Table
        self.table = QTableWidget()
        headers = ["Titolo", "Artista", "Durata", "BPM", "Riprod.", "Nome File"]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)

        self.table.setStyleSheet(f"""
            QTableWidget {{
                background-color: {COLOR_BG_DARK};
                alternate-background-color: {COLOR_BG_PANEL};
                color: {COLOR_TEXT_PRIMARY};
                border: 1px solid {COLOR_BORDER};
                border-radius: 6px;
                gridline-color: transparent;
                font-size: 11px;
            }}
            QTableWidget::item {{
                padding: 3px 6px;
                border-bottom: 1px solid rgba(255, 255, 255, 0.04);
            }}
            QTableWidget::item:selected {{
                background-color: {COLOR_BLUE_BG};
                color: #ffffff;
            }}
            QHeaderView::section {{
                background-color: {COLOR_BG_PANEL};
                color: {COLOR_TEXT_SECONDARY};
                font-size: 11px;
                font-weight: 600;
                padding: 4px 4px;
                border: none;
                border-bottom: 1px solid {COLOR_BORDER};
            }}
        """)

        th = self.table.horizontalHeader()
        th.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        th.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(1, 150)
        th.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(2, 60)
        th.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(3, 60)
        th.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(4, 55)
        th.setSectionResizeMode(5, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(5, 180)

        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.itemDoubleClicked.connect(self._on_table_double_clicked)
        layout.addWidget(self.table, stretch=1)

        # 4. Selected Preview Bar & Action Buttons
        bottom_frame = QFrame()
        bottom_frame.setStyleSheet(
            f"background-color: {COLOR_BG_PANEL}; border: 1px solid {COLOR_BORDER}; border-radius: 8px; padding: 8px 12px;"
        )
        b_layout = QHBoxLayout(bottom_frame)
        b_layout.setContentsMargins(4, 4, 4, 4)

        self.preview_lbl = QLabel("Seleziona una traccia dalla lista sopra...")
        self.preview_lbl.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px;")
        b_layout.addWidget(self.preview_lbl, stretch=1)

        self.btn_cancel = QPushButton("Annulla")
        self.btn_cancel.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_BG_CARD};
                color: {COLOR_TEXT_SECONDARY};
                border: 1px solid {COLOR_BORDER};
                border-radius: 5px;
                padding: 6px 14px;
                font-size: 11px;
            }}
            QPushButton:hover {{
                background-color: {COLOR_BG_HOVER};
                color: {COLOR_TEXT_PRIMARY};
            }}
        """)
        self.btn_cancel.clicked.connect(self.reject)
        b_layout.addWidget(self.btn_cancel)

        self.btn_confirm = QPushButton("✓ Associa a Questa Traccia")
        self.btn_confirm.setEnabled(False)
        self.btn_confirm.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_GREEN};
                color: #ffffff;
                border: none;
                border-radius: 5px;
                padding: 6px 18px;
                font-size: 11px;
                font-weight: 700;
            }}
            QPushButton:hover {{
                background-color: #22c55e;
            }}
            QPushButton:disabled {{
                background-color: #1e3a2b;
                color: #5d8a6e;
            }}
        """)
        self.btn_confirm.clicked.connect(self._on_confirm_clicked)
        b_layout.addWidget(self.btn_confirm)

        layout.addWidget(bottom_frame)

    def _on_search_text_changed(self, text_val: str) -> None:
        self._search_timer.start()

    def _on_return_pressed(self) -> None:
        if self.btn_confirm.isEnabled():
            self._on_confirm_clicked()
        else:
            self._execute_search()

    def _execute_search(self) -> None:
        if not self.current_engine:
            self.status_lbl.setText("Nessun database collegato")
            return

        query_text = self.search_input.text().strip()
        if not query_text:
            self.table.setRowCount(0)
            self._current_results = []
            self.status_lbl.setText("Inserisci un testo per cercare")
            self._update_preview()
            return

        # Sanitize for SQL LIKE
        like_term = f"%{query_text}%"
        prefix_term = f"{query_text}%"

        sql = """
            SELECT 
                c.ID, 
                c.Title, 
                COALESCE(a.Name, c.SrcArtistName, '') AS Artist,
                COALESCE(c.Length, 0) AS Length,
                c.BPM, 
                c.FileNameL, 
                COALESCE(c.DJPlayCount, 0) AS DJPlayCount,
                c.FolderPath
            FROM djmdContent c
            LEFT JOIN djmdArtist a ON c.ArtistID = a.ID
            WHERE (c.rb_local_deleted = 0 OR c.rb_local_deleted IS NULL)
              AND (
                  c.Title LIKE :q 
                  OR a.Name LIKE :q 
                  OR c.SrcArtistName LIKE :q 
                  OR c.FileNameL LIKE :q
              )
            ORDER BY 
                CASE 
                    WHEN c.Title LIKE :exact_prefix THEN 0 
                    WHEN a.Name LIKE :exact_prefix THEN 1
                    ELSE 2 
                END,
                c.Title ASC
            LIMIT 100;
        """

        results: List[Dict[str, Any]] = []
        try:
            with self.current_engine.connect() as conn:
                rows = conn.execute(
                    text(sql),
                    {"q": like_term, "exact_prefix": prefix_term},
                ).fetchall()

                for r in rows:
                    results.append({
                        "id": str(r[0]),
                        "title": str(r[1] or ""),
                        "artist": str(r[2] or ""),
                        "duration_sec": int(r[3] or 0),
                        "duration_str": format_duration_sec(int(r[3] or 0)),
                        "bpm": format_bpm_val(r[4]),
                        "file_name": str(r[5] or ""),
                        "play_count": int(r[6] or 0),
                        "folder_path": str(r[7] or ""),
                    })
        except Exception as e:
            self.status_lbl.setText(f"Errore ricerca: {e}")
            return

        self._current_results = results
        self.status_lbl.setText(f"{len(results)} risultati trovati")

        self.table.setRowCount(len(results))
        for row, item in enumerate(results):
            # Title
            t_item = QTableWidgetItem(item["title"])
            t_item.setToolTip(item["title"])
            self.table.setItem(row, 0, t_item)

            # Artist
            a_item = QTableWidgetItem(item["artist"])
            a_item.setToolTip(item["artist"])
            self.table.setItem(row, 1, a_item)

            # Duration
            d_item = QTableWidgetItem(item["duration_str"])
            d_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            # Highlight if duration difference is <= 3s
            if self.target_duration_sec > 0 and abs(item["duration_sec"] - self.target_duration_sec) <= 3:
                d_item.setForeground(QColor(COLOR_GREEN))
                d_item.setToolTip("Durata compatibile con la traccia della sessione (±3s)")
            self.table.setItem(row, 2, d_item)

            # BPM
            b_item = QTableWidgetItem(item["bpm"])
            b_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 3, b_item)

            # Play Count
            p_item = QTableWidgetItem(str(item["play_count"]))
            p_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 4, p_item)

            # File Name
            f_item = QTableWidgetItem(item["file_name"])
            f_item.setToolTip(os.path.join(item["folder_path"], item["file_name"]))
            self.table.setItem(row, 5, f_item)

        if results:
            self.table.selectRow(0)
        else:
            self._update_preview()

    def _on_selection_changed(self) -> None:
        sel = self.table.selectedItems()
        if not sel:
            self.selected_track_data = None
        else:
            row = sel[0].row()
            if 0 <= row < len(self._current_results):
                self.selected_track_data = self._current_results[row]
            else:
                self.selected_track_data = None
        self._update_preview()

    def _update_preview(self) -> None:
        if not self.selected_track_data:
            self.preview_lbl.setText("Seleziona una traccia dalla lista sopra per associarla.")
            self.btn_confirm.setEnabled(False)
            return

        t = self.selected_track_data
        dur_diff_txt = ""
        if self.target_duration_sec > 0:
            diff = t["duration_sec"] - self.target_duration_sec
            sign = "+" if diff > 0 else ""
            diff_color = COLOR_GREEN if abs(diff) <= 3 else COLOR_AMBER
            dur_diff_txt = f" &nbsp;•&nbsp; Diff. durata: <b style='color: {diff_color};'>{sign}{diff}s</b>"

        self.preview_lbl.setText(
            f"Selezionata: <b style='color: #ffffff;'>{t['title']}</b> &nbsp;•&nbsp; "
            f"<b>{t['artist']}</b> ({t['duration_str']}){dur_diff_txt}"
        )
        self.btn_confirm.setEnabled(True)

    def _on_table_double_clicked(self, item: QTableWidgetItem) -> None:
        if self.selected_track_data:
            self._on_confirm_clicked()

    def _on_confirm_clicked(self) -> None:
        if self.selected_track_data:
            self.track_selected.emit(self.selected_track_data)
            self.accept()
