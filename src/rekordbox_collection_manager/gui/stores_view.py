"""Stores Summary View for Rekordbox Collection Manager.

Displays high-level cards for the 4 supported download stores (Beatport, Bandcamp,
Qobuz, Traxsource) showing purchased and purchasable ("To Buy") track counts, with
direct deep-linking to pre-filtered collection lists in Tracks View.
"""

from datetime import datetime
from typing import Any, Dict, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QCursor, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.engine import Engine

from rekordbox_collection_manager.gui.theme import (
    COLOR_AMBER,
    COLOR_BG_CARD,
    COLOR_BG_DARK,
    COLOR_BG_INPUT,
    COLOR_BG_PANEL,
    COLOR_BLUE,
    COLOR_BORDER,
    COLOR_BORDER_FOCUS,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_SECONDARY,
)
from rekordbox_collection_manager.gui.workers import StoresSummaryWorker, stop_worker
from rekordbox_collection_manager.utils import (
    KNOWN_STORES,
    STORE_BANDCAMP,
    STORE_BEATPORT,
    STORE_QOBUZ,
    STORE_TRAXSOURCE,
)

STORE_ICONS: Dict[str, str] = {
    STORE_BEATPORT: "🎧",
    STORE_BANDCAMP: "🏷️",
    STORE_QOBUZ: "🎼",
    STORE_TRAXSOURCE: "🎛️",
}


class StoreRatioBar(QWidget):
    """Custom-painted ratio bar depicting purchased vs to-buy track proportion."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedHeight(6)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.purchased: int = 0
        self.to_buy: int = 0

    def set_counts(self, purchased: int, to_buy: int) -> None:
        self.purchased = max(0, purchased)
        self.to_buy = max(0, to_buy)
        total = self.purchased + self.to_buy
        pct_str = f"{int(round((self.purchased / total) * 100))}%" if total > 0 else "0%"
        self.setToolTip(f"Purchased: {self.purchased} ({pct_str}) • To Buy: {self.to_buy}")
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()
        total = self.purchased + self.to_buy
        radius = rect.height() / 2.0

        # Draw empty background track
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(COLOR_BORDER))
        painter.drawRoundedRect(rect, radius, radius)

        if total <= 0:
            return

        w = rect.width()
        p_ratio = self.purchased / total
        p_w = w * p_ratio

        # Clip path so rounded caps are preserved across segments
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.setClipPath(path)

        if self.purchased > 0:
            painter.setBrush(QColor(COLOR_BLUE))
            painter.drawRect(0, 0, int(round(p_w)) if self.to_buy > 0 else w, rect.height())

        if self.to_buy > 0:
            painter.setBrush(QColor(COLOR_AMBER))
            x_start = int(round(p_w)) if self.purchased > 0 else 0
            painter.drawRect(x_start, 0, w - x_start, rect.height())


class StoreMetricBox(QFrame):
    """Clickable metric block inside a store card (e.g. Purchased or To Buy)."""

    clicked = Signal()

    def __init__(
        self,
        title: str,
        icon: str,
        accent_color: str,
        bg_color: str,
        border_color: str,
        hover_bg: str,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.accent_color = accent_color
        self.count = 0
        self.setObjectName("storeMetricBox")
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(2)

        header_layout = QHBoxLayout()
        header_layout.setSpacing(6)

        self.icon_label = QLabel(icon)
        self.icon_label.setStyleSheet("font-size: 12px;")
        header_layout.addWidget(self.icon_label)

        self.title_label = QLabel(title.upper())
        self.title_label.setStyleSheet(
            f"color: {COLOR_TEXT_MUTED}; font-size: 9px; font-weight: 700; letter-spacing: 0.5px;"
        )
        header_layout.addWidget(self.title_label)
        header_layout.addStretch()

        layout.addLayout(header_layout)

        self.count_label = QLabel("0")
        self.count_label.setStyleSheet(
            f"color: {accent_color}; font-size: 22px; font-weight: 800; padding-top: 1px;"
        )
        layout.addWidget(self.count_label)

        self.action_label = QLabel("Click to view tracks →")
        self.action_label.setStyleSheet(
            f"color: {COLOR_TEXT_SECONDARY}; font-size: 10px; font-weight: 500;"
        )
        layout.addWidget(self.action_label)

        self._style_template = (
            f"QFrame#storeMetricBox {{"
            f"  background-color: {bg_color};"
            f"  border: 1px solid {border_color};"
            f"  border-radius: 6px;"
            f"}}"
            f"QFrame#storeMetricBox:hover {{"
            f"  background-color: {hover_bg};"
            f"  border: 1px solid {accent_color};"
            f"}}"
        )
        self.setStyleSheet(self._style_template)

    def set_count(self, count: int) -> None:
        self.count = max(0, count)
        self.count_label.setText(str(self.count))
        if self.count > 0:
            self.action_label.setText("Click to view tracks →")
            self.action_label.setStyleSheet(
                f"color: {COLOR_TEXT_SECONDARY}; font-size: 11px; font-weight: 500;"
            )
            self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            self.setStyleSheet(self._style_template)
        else:
            self.action_label.setText("No tracks")
            self.action_label.setStyleSheet(
                f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 400;"
            )
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            self.setStyleSheet(
                "QFrame#storeMetricBox {"
                f"  background-color: #16161c;"
                f"  border: 1px solid {COLOR_BORDER};"
                "  border-radius: 6px;"
                "}"
            )

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self.count > 0:
                self.clicked.emit()
            event.accept()
        else:
            super().mousePressEvent(event)


class StoreCardWidget(QFrame):
    """Card widget displaying purchased and planned purchase counts for a store."""

    navigate_requested = Signal(str, str)  # (purchase_status, store_name)

    def __init__(self, store_name: str, icon: str = "🏪", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.store_name = store_name
        self.icon = icon
        self.purchased_count = 0
        self.to_buy_count = 0
        self.setObjectName("storeCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.setStyleSheet(
            f"QFrame#storeCard {{"
            f"  background-color: {COLOR_BG_PANEL};"
            f"  border: 1px solid {COLOR_BORDER};"
            f"  border-radius: 8px;"
            f"}}"
            f"QFrame#storeCard:hover {{"
            f"  border: 1px solid {COLOR_BORDER_FOCUS};"
            f"}}"
        )
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        # 1. Card Header
        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)

        title_lbl = QLabel(f"{self.icon}  {self.store_name}")
        title_lbl.setStyleSheet(
            f"color: {COLOR_TEXT_PRIMARY}; font-size: 15px; font-weight: 700;"
        )
        header_layout.addWidget(title_lbl)
        header_layout.addStretch()

        self.total_badge = QLabel("0 tracks")
        self.total_badge.setFixedHeight(22)
        self.total_badge.setStyleSheet(
            f"background-color: {COLOR_BG_INPUT}; color: {COLOR_TEXT_SECONDARY}; "
            f"border: 1px solid {COLOR_BORDER}; border-radius: 11px; padding: 2px 10px; "
            f"font-size: 11px; font-weight: 600;"
        )
        header_layout.addWidget(self.total_badge)

        layout.addLayout(header_layout)

        # 2. Ratio Bar
        self.ratio_bar = StoreRatioBar(self)
        layout.addWidget(self.ratio_bar)

        # 3. Metric Boxes
        metrics_layout = QHBoxLayout()
        metrics_layout.setSpacing(10)

        # Purchased Metric
        self.box_purchased = StoreMetricBox(
            title="Purchased",
            icon="🛒",
            accent_color=COLOR_BLUE,
            bg_color="#0e1a2e",
            border_color="#1a3558",
            hover_bg="#142642",
            parent=self,
        )
        self.box_purchased.clicked.connect(
            lambda: self.navigate_requested.emit("purchased", self.store_name)
        )
        metrics_layout.addWidget(self.box_purchased)

        # To Buy (Purchasable) Metric
        self.box_to_buy = StoreMetricBox(
            title="To Buy (≥3 plays)",
            icon="💳",
            accent_color=COLOR_AMBER,
            bg_color="#2b1c09",
            border_color="#5c3b12",
            hover_bg="#3d270c",
            parent=self,
        )
        self.box_to_buy.clicked.connect(
            lambda: self.navigate_requested.emit("purchasable", self.store_name)
        )
        metrics_layout.addWidget(self.box_to_buy)

        layout.addLayout(metrics_layout)

    def set_counts(self, purchased: int, to_buy: int) -> None:
        self.purchased_count = max(0, purchased)
        self.to_buy_count = max(0, to_buy)
        self.box_purchased.set_count(self.purchased_count)
        self.box_to_buy.set_count(self.to_buy_count)
        self.ratio_bar.set_counts(self.purchased_count, self.to_buy_count)

        total = self.purchased_count + self.to_buy_count
        unit = "track" if total == 1 else "tracks"
        self.total_badge.setText(f"{total} {unit}")


class StoresView(QWidget):
    """Main Stores Overview dashboard view."""

    open_tracks_by_store = Signal(str, str)  # (purchase_status, store_name)
    notify_status = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.engine: Optional[Engine] = None
        self.db_path: Optional[str] = None
        self.active_worker: Optional[StoresSummaryWorker] = None
        self.cards: Dict[str, StoreCardWidget] = {}
        self._init_ui()

    def _init_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(20, 20, 20, 20)
        root_layout.setSpacing(16)

        # 1. Header Toolbar
        header_layout = QHBoxLayout()
        header_layout.setSpacing(12)

        title_col = QVBoxLayout()
        title_col.setSpacing(4)

        title = QLabel("🏪  MUSIC STORES")
        title.setStyleSheet(
            f"color: {COLOR_TEXT_PRIMARY}; font-size: 18px; font-weight: 800; letter-spacing: 0.5px;"
        )
        title_col.addWidget(title)

        subtitle = QLabel(
            "Overview of purchased tracks and planned purchases by store. "
            "Click any counter to view and manage filtered tracks."
        )
        subtitle.setStyleSheet(
            f"color: {COLOR_TEXT_SECONDARY}; font-size: 12px;"
        )
        title_col.addWidget(subtitle)

        header_layout.addLayout(title_col, stretch=1)

        self.btn_refresh = QPushButton("🔄 Refresh")
        self.btn_refresh.setToolTip("Recalculate store summary counts")
        self.btn_refresh.setStyleSheet(
            f"QPushButton {{ background-color: {COLOR_BG_INPUT}; color: {COLOR_TEXT_PRIMARY}; "
            f"border: 1px solid {COLOR_BORDER}; border-radius: 4px; padding: 6px 14px; font-size: 12px; font-weight: 600; }} "
            f"QPushButton:hover {{ border-color: {COLOR_BORDER_FOCUS}; color: {COLOR_TEXT_PRIMARY}; }}"
        )
        self.btn_refresh.clicked.connect(self.refresh)
        header_layout.addWidget(self.btn_refresh)

        root_layout.addLayout(header_layout)

        # 2. Status / Last updated label
        self.status_label = QLabel("Connect a database to load store metrics.")
        self.status_label.setStyleSheet(
            f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-style: italic;"
        )
        root_layout.addWidget(self.status_label)

        # 3. Scrollable Cards Container
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background-color: transparent;")

        container = QWidget()
        container.setStyleSheet("background-color: transparent;")
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 4, 0, 4)
        container_layout.setSpacing(16)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(16)

        # Position cards in 2x2 grid
        for idx, store_name in enumerate(KNOWN_STORES):
            row = idx // 2
            col = idx % 2
            icon = STORE_ICONS.get(store_name, "🏪")
            card = StoreCardWidget(store_name, icon=icon, parent=self)
            card.navigate_requested.connect(self._on_card_navigate_requested)
            grid.addWidget(card, row, col)
            self.cards[store_name] = card

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        container_layout.addLayout(grid)
        container_layout.addStretch(1)

        scroll.setWidget(container)
        root_layout.addWidget(scroll, stretch=1)

    def set_engine(self, engine: Optional[Engine], db_path: Optional[str] = None) -> None:
        """Assign active database engine and compute store counts."""
        self.engine = engine
        self.db_path = db_path
        self.refresh()

    def refresh(self) -> None:
        """Recalculate store summary metrics asynchronously."""
        if not self.engine:
            self.status_label.setText("No database loaded.")
            return

        stop_worker(self.active_worker)

        self.status_label.setText("Calculating store metrics...")
        self.btn_refresh.setEnabled(False)

        worker = StoresSummaryWorker(self.engine, parent=self)
        self.active_worker = worker
        worker.success.connect(self._on_counts_ready)
        worker.error.connect(self._on_counts_error)
        worker.finished.connect(lambda: self._on_worker_finished(worker))
        worker.start()

    def _on_worker_finished(self, worker: StoresSummaryWorker) -> None:
        if self.active_worker is worker:
            self.active_worker = None
        self.btn_refresh.setEnabled(True)

    def _on_counts_ready(self, counts: Dict[str, Dict[str, int]]) -> None:
        total_purchased = 0
        total_to_buy = 0

        for store_name, card in self.cards.items():
            store_data = counts.get(store_name, {})
            p_cnt = int(store_data.get("purchased", 0))
            b_cnt = int(store_data.get("to_buy", 0))
            card.set_counts(p_cnt, b_cnt)
            total_purchased += p_cnt
            total_to_buy += b_cnt

        now_str = datetime.now().strftime("%H:%M:%S")
        self.status_label.setText(
            f"Totals: {total_purchased} purchased • {total_to_buy} to buy (Updated at {now_str})"
        )

    def _on_counts_error(self, err_msg: str) -> None:
        self.status_label.setText(f"❌ Error calculating metrics: {err_msg}")
        self.notify_status.emit(f"Error calculating store metrics: {err_msg}")

    def _on_card_navigate_requested(self, purchase_status: str, store_name: str) -> None:
        """Bubble up navigation signal to MainWindow."""
        self.open_tracks_by_store.emit(purchase_status, store_name)

    def cleanup(self) -> None:
        """Cleanly terminate running worker thread."""
        if self.active_worker:
            stop_worker(self.active_worker, wait_ms=500)
            self.active_worker = None
