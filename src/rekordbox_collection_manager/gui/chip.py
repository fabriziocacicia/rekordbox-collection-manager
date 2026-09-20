"""Reusable, high-DPI vector badge chips for the Rekordbox Collection Manager GUI.

Provides smooth anti-aliased rendering for metadata badges (Tags, Playlists,
Play Count, History Sessions) with consistent styling, zero rasterization artifacts,
and interactive hover/removal capabilities.
"""

from typing import Any, Dict, Optional

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

# Predefined curated color schemes for badges
CHIP_SCHEMES: Dict[str, Dict[str, QColor]] = {
    "teal": {
        "bg": QColor(20, 184, 166, 28),
        "bg_hover": QColor(20, 184, 166, 48),
        "border": QColor(45, 212, 191, 75),
        "border_hover": QColor(45, 212, 191, 150),
        "text": QColor("#5eead4"),
    },
    "purple": {
        "bg": QColor(139, 92, 246, 28),
        "bg_hover": QColor(139, 92, 246, 48),
        "border": QColor(167, 139, 250, 75),
        "border_hover": QColor(167, 139, 250, 150),
        "text": QColor("#d8b4fe"),
    },
    "green": {
        "bg": QColor(16, 185, 129, 28),
        "bg_hover": QColor(16, 185, 129, 48),
        "border": QColor(52, 211, 153, 75),
        "border_hover": QColor(52, 211, 153, 150),
        "text": QColor("#6ee7b7"),
    },
    "blue": {
        "bg": QColor(14, 165, 233, 28),
        "bg_hover": QColor(14, 165, 233, 48),
        "border": QColor(56, 189, 248, 75),
        "border_hover": QColor(56, 189, 248, 150),
        "text": QColor("#7dd3fc"),
    },
    "amber": {
        "bg": QColor(245, 158, 11, 28),
        "bg_hover": QColor(245, 158, 11, 48),
        "border": QColor(251, 191, 36, 75),
        "border_hover": QColor(251, 191, 36, 150),
        "text": QColor("#fde68a"),
    },
}


class ChipDeleteButton(QPushButton):
    """Anti-aliased vector delete button for removable badge chips."""

    def __init__(
        self,
        border_color: Optional[QColor] = None,
        text_color: Optional[QColor] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("chipDeleteBtn")
        self.setFixedSize(14, 14)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Remove")
        self.border_color = border_color or QColor(255, 255, 255, 100)
        self.text_color = text_color or QColor("#ffffff")
        self._hovered = False

    def set_colors(self, border_color: QColor, text_color: QColor) -> None:
        self.border_color = border_color
        self.text_color = text_color
        self.update()

    def enterEvent(self, event: Any) -> None:
        try:
            super().enterEvent(event)
        except Exception:
            pass
        self._hovered = True
        self.update()

    def leaveEvent(self, event: Any) -> None:
        try:
            super().leaveEvent(event)
        except Exception:
            pass
        self._hovered = False
        self.update()

    def paintEvent(self, event: Any) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0, 0, 14, 14)
        if self._hovered:
            p.setBrush(QColor("#ef4444"))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(rect)
            pen = QPen(QColor("#ffffff"), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        else:
            p.setBrush(QColor(22, 24, 32, 235))
            p.setPen(QPen(self.border_color, 1.0))
            p.drawEllipse(rect.adjusted(0.5, 0.5, -0.5, -0.5))
            pen = QPen(self.text_color, 1.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        pad = 4.0
        p.drawLine(pad, pad, 14.0 - pad, 14.0 - pad)
        p.drawLine(pad, 14.0 - pad, 14.0 - pad, pad)
        p.end()


class BadgeChip(QFrame):
    """Universal anti-aliased badge chip for displaying tags, metadata, and playlists.

    Features vector-drawn rounded borders, completely balanced text padding (no blank space),
    and an elegant top-right corner removal badge on hover.
    """

    delete_requested = Signal(str)

    def __init__(
        self,
        text: str,
        scheme: str = "teal",
        removable: bool = False,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.name = text
        self.text = text
        self.scheme_name = scheme
        self.scheme = CHIP_SCHEMES.get(scheme, CHIP_SCHEMES["teal"])
        self.removable = removable
        self._hovered = False

        self.headroom_top = 5 if removable else 0
        self.headroom_right = 5 if removable else 0
        self.pill_h = 24
        self.setFixedHeight(self.pill_h + self.headroom_top)

        layout = QHBoxLayout(self)
        # Symmetrical 10px horizontal padding inside pill (no right-side blank space)
        layout.setContentsMargins(10, self.headroom_top, 10 + self.headroom_right, 0)
        layout.setSpacing(0)

        self.label = QLabel(text)
        self.label.setStyleSheet(
            f"color: {self.scheme['text'].name()}; font-size: 11px; font-weight: 500; "
            "background: transparent; border: none;"
        )
        layout.addWidget(self.label)

        if self.removable:
            self.x_btn = ChipDeleteButton(
                border_color=self.scheme["border"],
                text_color=self.scheme["text"],
                parent=self,
            )
            self.x_btn.setToolTip(f"Remove '{text}'")
            self.x_btn.setVisible(False)
            self.x_btn.clicked.connect(lambda: self.delete_requested.emit(self.text))
            self._position_x_btn()

    def text(self) -> str:
        return self.label.text()

    def setText(self, text: str) -> None:
        self.set_text(text)

    def set_text(self, text: str) -> None:
        self.name = text
        self.label.setText(text)
        if self.removable and hasattr(self, "x_btn"):
            self.x_btn.setToolTip(f"Remove '{text}'")
            self._position_x_btn()

    def set_scheme(self, scheme: str) -> None:
        self.scheme_name = scheme
        self.scheme = CHIP_SCHEMES.get(scheme, CHIP_SCHEMES["teal"])
        self.label.setStyleSheet(
            f"color: {self.scheme['text'].name()}; font-size: 11px; font-weight: 500; "
            "background: transparent; border: none;"
        )
        if self.removable and hasattr(self, "x_btn"):
            self.x_btn.set_colors(self.scheme["border"], self.scheme["text"])
        self.update()

    def _position_x_btn(self) -> None:
        if self.removable and hasattr(self, "x_btn"):
            pill_right = self.width() - self.headroom_right - 1.0
            pill_top = self.headroom_top + 0.5
            btn_w = self.x_btn.width()
            btn_h = self.x_btn.height()
            self.x_btn.move(int(pill_right - btn_w / 2), int(pill_top - btn_h / 2 + 1))

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._position_x_btn()

    def enterEvent(self, event: Any) -> None:
        try:
            super().enterEvent(event)
        except Exception:
            pass
        self._hovered = True
        if self.removable and hasattr(self, "x_btn"):
            self._position_x_btn()
            self.x_btn.setVisible(True)
        self.update()

    def leaveEvent(self, event: Any) -> None:
        try:
            super().leaveEvent(event)
        except Exception:
            pass
        self._hovered = False
        if self.removable and hasattr(self, "x_btn"):
            self.x_btn.setVisible(False)
        self.update()

    def paintEvent(self, event: Any) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pill_rect = QRectF(
            0.5,
            self.headroom_top + 0.5,
            self.width() - self.headroom_right - 1.0,
            self.pill_h - 1.0,
        )
        radius = 5.0
        if self._hovered:
            p.setBrush(self.scheme["bg_hover"])
            p.setPen(QPen(self.scheme["border_hover"], 1.0))
        else:
            p.setBrush(self.scheme["bg"])
            p.setPen(QPen(self.scheme["border"], 1.0))
        p.drawRoundedRect(pill_rect, radius, radius)
        p.end()


class PlaylistChip(BadgeChip):
    """Specialized removable badge chip for playlist membership (teal color scheme)."""

    def __init__(self, name: str, parent: Optional[QWidget] = None):
        super().__init__(text=name, scheme="teal", removable=True, parent=parent)


class TagChip(BadgeChip):
    """Specialized removable badge chip for My Tags (purple color scheme)."""

    def __init__(self, name: str, parent: Optional[QWidget] = None):
        super().__init__(text=name, scheme="purple", removable=True, parent=parent)

