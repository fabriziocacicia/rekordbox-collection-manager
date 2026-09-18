"""Visual theme and QSS styles for Rekordbox Collection Fixer.

Inspired by modern DJ applications (Rekordbox, CDJ/XDJ interfaces) featuring
a deep dark slate aesthetic with electric blue and amber accents.
"""

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

# Color Palette Constants
COLOR_BG_DARK = "#121215"
COLOR_BG_PANEL = "#1a1a20"
COLOR_BG_CARD = "#22222a"
COLOR_BG_INPUT = "#181820"
COLOR_BG_HOVER = "#2a2a35"
COLOR_BG_SELECTED = "#0f3a63"

COLOR_BORDER = "#2e2e38"
COLOR_BORDER_LIGHT = "#3d3d4b"
COLOR_BORDER_FOCUS = "#00a2ff"

COLOR_TEXT_PRIMARY = "#f4f5f7"
COLOR_TEXT_SECONDARY = "#a0a4b0"
COLOR_TEXT_MUTED = "#686c7a"

COLOR_BLUE = "#00a2ff"
COLOR_BLUE_HOVER = "#2bb2ff"
COLOR_BLUE_PRESSED = "#0085d4"
COLOR_BLUE_BG = "#0d2b45"

COLOR_AMBER = "#f59e0b"
COLOR_AMBER_BG = "#382305"
COLOR_AMBER_BORDER = "#784b08"

COLOR_GREEN = "#10b981"
COLOR_GREEN_BG = "#043021"
COLOR_GREEN_BORDER = "#08573c"

COLOR_RED = "#ef4444"
COLOR_RED_BG = "#361010"
COLOR_RED_BORDER = "#6e1d1d"

MODERN_DARK_QSS = f"""
/* Global Window Styling */
QMainWindow, QWidget {{
    background-color: {COLOR_BG_DARK};
    color: {COLOR_TEXT_PRIMARY};
    font-family: ".AppleSystemUIFont", "Helvetica Neue", "Segoe UI", Arial, sans-serif;
    font-size: 13px;
}}

/* Top Header & Toolbars */
QFrame#headerFrame {{
    background-color: {COLOR_BG_PANEL};
    border-bottom: 1px solid {COLOR_BORDER};
    padding: 10px 16px;
}}

QFrame#sidebarFrame {{
    background-color: {COLOR_BG_PANEL};
    border-right: 1px solid {COLOR_BORDER};
    padding: 8px 0px;
}}

QFrame#cardFrame {{
    background-color: {COLOR_BG_CARD};
    border: 1px solid {COLOR_BORDER};
    border-radius: 8px;
}}

/* Sidebar Navigation Buttons */
QPushButton#navButton {{
    background-color: transparent;
    color: {COLOR_TEXT_SECONDARY};
    text-align: left;
    padding: 12px 18px;
    border: none;
    border-left: 3px solid transparent;
    font-size: 13px;
    font-weight: 500;
}}

QPushButton#navButton:hover {{
    background-color: {COLOR_BG_HOVER};
    color: {COLOR_TEXT_PRIMARY};
}}

QPushButton#navButton:checked {{
    background-color: {COLOR_BG_HOVER};
    color: {COLOR_BLUE};
    border-left: 3px solid {COLOR_BLUE};
    font-weight: 600;
}}

/* Standard Buttons */
QPushButton {{
    background-color: {COLOR_BG_CARD};
    color: {COLOR_TEXT_PRIMARY};
    border: 1px solid {COLOR_BORDER};
    border-radius: 6px;
    padding: 7px 14px;
    font-weight: 500;
}}

QPushButton:hover {{
    background-color: {COLOR_BG_HOVER};
    border-color: {COLOR_BORDER_LIGHT};
}}

QPushButton:pressed {{
    background-color: {COLOR_BG_DARK};
}}

QPushButton:disabled {{
    background-color: {COLOR_BG_DARK};
    color: {COLOR_TEXT_MUTED};
    border-color: {COLOR_BORDER};
}}

/* Primary Action Buttons (Glowing Electric Blue) */
QPushButton#primaryButton {{
    background-color: {COLOR_BLUE};
    color: #ffffff;
    border: 1px solid {COLOR_BLUE_HOVER};
    border-radius: 6px;
    font-weight: 600;
    padding: 8px 18px;
}}

QPushButton#primaryButton:hover {{
    background-color: {COLOR_BLUE_HOVER};
}}

QPushButton#primaryButton:pressed {{
    background-color: {COLOR_BLUE_PRESSED};
}}

QPushButton#primaryButton:disabled {{
    background-color: {COLOR_BG_PANEL};
    color: {COLOR_TEXT_MUTED};
    border-color: {COLOR_BORDER};
}}

/* Quick Stepper Buttons [-] and [+] */
QPushButton#stepperButton {{
    background-color: {COLOR_BG_CARD};
    color: {COLOR_TEXT_PRIMARY};
    border: 1px solid {COLOR_BORDER_LIGHT};
    border-radius: 4px;
    padding: 3px 9px;
    font-size: 14px;
    font-weight: bold;
    min-width: 26px;
    max-width: 26px;
    min-height: 24px;
    max-height: 24px;
}}

QPushButton#stepperButton:hover {{
    background-color: {COLOR_BLUE};
    color: #ffffff;
    border-color: {COLOR_BLUE};
}}

/* Inputs, SpinBoxes & ComboBoxes */
QLineEdit, QSpinBox, QComboBox {{
    background-color: {COLOR_BG_INPUT};
    color: {COLOR_TEXT_PRIMARY};
    border: 1px solid {COLOR_BORDER};
    border-radius: 6px;
    padding: 6px 10px;
    selection-background-color: {COLOR_BLUE};
}}

QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border: 1px solid {COLOR_BORDER_FOCUS};
    background-color: {COLOR_BG_DARK};
}}

QComboBox::drop-down {{
    border: none;
    padding-right: 8px;
}}

/* Tables and Trees */
QTableView, QTableWidget, QTreeView, QTreeWidget {{
    background-color: {COLOR_BG_PANEL};
    alternate-background-color: {COLOR_BG_DARK};
    color: {COLOR_TEXT_PRIMARY};
    border: 1px solid {COLOR_BORDER};
    border-radius: 6px;
    gridline-color: {COLOR_BORDER};
    selection-background-color: {COLOR_BG_SELECTED};
    selection-color: #ffffff;
    outline: none;
}}

QTableView::item, QTreeView::item {{
    padding: 6px 8px;
}}

QTableView::item:hover, QTreeView::item:hover {{
    background-color: {COLOR_BG_HOVER};
}}

QHeaderView::section {{
    background-color: {COLOR_BG_CARD};
    color: {COLOR_TEXT_SECONDARY};
    padding: 6px 8px;
    border: none;
    border-right: 1px solid {COLOR_BORDER};
    border-bottom: 1px solid {COLOR_BORDER};
    font-weight: 600;
    font-size: 11px;
    text-transform: uppercase;
}}

/* Scrollbars */
QScrollBar:vertical {{
    background: {COLOR_BG_DARK};
    width: 10px;
    margin: 0px;
}}

QScrollBar::handle:vertical {{
    background: {COLOR_BORDER_LIGHT};
    min-height: 24px;
    border-radius: 5px;
    margin: 2px;
}}

QScrollBar::handle:vertical:hover {{
    background: {COLOR_TEXT_MUTED};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

QScrollBar:horizontal {{
    background: {COLOR_BG_DARK};
    height: 10px;
    margin: 0px;
}}

QScrollBar::handle:horizontal {{
    background: {COLOR_BORDER_LIGHT};
    min-width: 24px;
    border-radius: 5px;
    margin: 2px;
}}

/* Status Chips & Badges */
QLabel#safeBadge {{
    background-color: {COLOR_GREEN_BG};
    color: {COLOR_GREEN};
    border: 1px solid {COLOR_GREEN_BORDER};
    border-radius: 12px;
    padding: 4px 10px;
    font-size: 11px;
    font-weight: 600;
}}

QLabel#dangerBadge {{
    background-color: {COLOR_RED_BG};
    color: {COLOR_RED};
    border: 1px solid {COLOR_RED_BORDER};
    border-radius: 12px;
    padding: 4px 10px;
    font-size: 11px;
    font-weight: 600;
}}

QLabel#warningBadge {{
    background-color: {COLOR_AMBER_BG};
    color: {COLOR_AMBER};
    border: 1px solid {COLOR_AMBER_BORDER};
    border-radius: 12px;
    padding: 4px 10px;
    font-size: 11px;
    font-weight: 600;
}}

QLabel#stagedBadge {{
    background-color: {COLOR_AMBER_BG};
    color: {COLOR_AMBER};
    border: 1px solid {COLOR_AMBER_BORDER};
    border-radius: 4px;
    padding: 2px 6px;
    font-size: 11px;
    font-weight: 600;
}}

/* Status Bar */
QStatusBar {{
    background-color: {COLOR_BG_PANEL};
    border-top: 1px solid {COLOR_BORDER};
    color: {COLOR_TEXT_SECONDARY};
    font-size: 11px;
    padding: 4px 8px;
}}
"""


def apply_dark_theme(app: QApplication) -> None:
    """Apply modern Pioneer DJ dark palette and stylesheet to the QApplication."""
    app.setStyle("Fusion")

    # Set base palette for Qt native elements
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(COLOR_BG_DARK))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(COLOR_TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Base, QColor(COLOR_BG_INPUT))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(COLOR_BG_CARD))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(COLOR_BG_CARD))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(COLOR_TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Text, QColor(COLOR_TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Button, QColor(COLOR_BG_CARD))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(COLOR_TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(COLOR_BLUE))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))

    app.setPalette(palette)
    app.setStyleSheet(MODERN_DARK_QSS)
