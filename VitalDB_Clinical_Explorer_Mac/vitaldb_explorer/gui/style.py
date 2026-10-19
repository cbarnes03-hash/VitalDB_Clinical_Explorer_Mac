"""Visual design system: palette, typography and the application stylesheet.

One consistent look across Windows and macOS: a deep-navy navigation rail, white "cards" on a cool grey canvas,
a single teal accent, restrained amber for cautions and red only for errors.
"""
from __future__ import annotations

import sys

# --- palette ---------------------------------------------------------------------------------------------
NAVY = "#0E2536"          # navigation rail
NAVY_2 = "#16364C"        # hover/selected on the rail
RAIL_TEXT = "#A9BCCB"
RAIL_TEXT_ACTIVE = "#FFFFFF"
ACCENT = "#0E8AA0"        # primary teal
ACCENT_DARK = "#0A6E80"
ACCENT_SOFT = "#E4F3F6"
ACCENT_LIGHT = "#5EEAD4"  # active marker on the rail
BG = "#F3F6F9"            # canvas
CARD = "#FFFFFF"
BORDER = "#E1E7ED"
BORDER_STRONG = "#C9D3DC"
TEXT = "#1B2A38"
MUTED = "#617080"
FAINT = "#8795A3"
WARN_BG = "#FFF6DB"
WARN_BORDER = "#F0DC9A"
WARN_TEXT = "#6F5300"
OK = "#0F7B5F"
ERR = "#B3261E"
ERR_BG = "#FDECEA"

# kept for older imports
NAVY_LEGACY = NAVY
TEAL = ACCENT
TEAL_DARK = ACCENT_DARK

# Colour-blind-friendly series colours (Okabe-Ito order, black replaced by a dark slate)
SERIES_COLOURS = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#44546A", "#B8A100"]

# --- typography ------------------------------------------------------------------------------------------
if sys.platform == "darwin":
    FONT_STACK = '".AppleSystemUIFont", "SF Pro Text", "Helvetica Neue", Arial'
    BASE_PX = 13
    QT_FONT = "Helvetica Neue"
elif sys.platform.startswith("win"):
    FONT_STACK = '"Segoe UI Variable Text", "Segoe UI", Arial'
    BASE_PX = 13
    QT_FONT = "Segoe UI"
else:
    FONT_STACK = '"Inter", "Segoe UI", "DejaVu Sans", Arial'
    BASE_PX = 13
    QT_FONT = "Inter"

FONT_FAMILY = QT_FONT  # legacy name

STYLESHEET = f"""
* {{ font-family: {FONT_STACK}; font-size: {BASE_PX}px; color: {TEXT}; }}
QMainWindow, QWidget#page, QStackedWidget {{ background: {BG}; }}
QWidget#canvas {{ background: {BG}; }}
QToolTip {{ background: {NAVY}; color: white; border: none; padding: 5px 8px; border-radius: 4px; }}

/* --- navigation rail ------------------------------------------------------------------------------ */
QWidget#sidebar {{ background: {NAVY}; }}
QLabel#brandMark {{ background: {ACCENT}; color: white; border-radius: 9px; font-weight: 700; font-size: 15px; }}
QLabel#brandTitle {{ color: white; font-size: 15px; font-weight: 700; }}
QLabel#brandSub {{ color: {RAIL_TEXT}; font-size: 11px; }}
QLabel#navSection {{ color: #6F8798; font-size: 10px; font-weight: 700; letter-spacing: 1px;
    padding: 14px 20px 4px 20px; }}
QListWidget#nav {{ background: transparent; border: none; outline: none; padding: 2px 10px; }}
QListWidget#nav::item {{ color: {RAIL_TEXT}; padding: 9px 12px; margin: 1px 0; border-radius: 8px; }}
QListWidget#nav::item:hover {{ background: {NAVY_2}; color: white; }}
QListWidget#nav::item:selected {{ background: {NAVY_2}; color: {RAIL_TEXT_ACTIVE}; font-weight: 600; }}
QListWidget#nav::item:disabled {{ color: #4F6677; }}
QFrame#caseCard {{ background: {NAVY_2}; border-radius: 10px; }}
QLabel#caseCardTitle {{ color: white; font-weight: 600; }}
QLabel#caseCardSub {{ color: {RAIL_TEXT}; font-size: 11px; }}

/* --- disclaimer banner ---------------------------------------------------------------------------- */
QFrame#banner {{ background: {WARN_BG}; border: none; border-bottom: 1px solid {WARN_BORDER}; }}
QLabel#bannerText {{ color: {WARN_TEXT}; font-size: 12px; }}

/* --- typography helpers --------------------------------------------------------------------------- */
QLabel#pageTitle {{ font-size: 22px; font-weight: 700; color: {NAVY}; }}
QLabel#pageHint {{ color: {MUTED}; font-size: 13px; }}
QLabel#note {{ color: {MUTED}; font-size: 12px; }}
QLabel#cardTitle {{ font-size: 14px; font-weight: 700; color: {NAVY}; }}
QLabel#statusOk {{ color: {OK}; }}
QLabel#statusWarn {{ color: {WARN_TEXT}; background: {WARN_BG}; border: 1px solid {WARN_BORDER};
    padding: 6px 10px; border-radius: 6px; }}
QLabel#chip {{ background: {ACCENT_SOFT}; color: {ACCENT_DARK}; border-radius: 10px; padding: 3px 10px;
    font-size: 12px; font-weight: 600; }}
QLabel#chipMuted {{ background: #EDF1F5; color: {MUTED}; border-radius: 10px; padding: 3px 10px; font-size: 12px; }}

/* --- cards ---------------------------------------------------------------------------------------- */
QFrame#card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 12px; }}
QGroupBox {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 12px; margin-top: 22px;
    padding: 16px 12px 12px 12px; font-weight: 700; color: {NAVY}; }}
QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; left: 4px; top: 0px;
    padding: 0 4px; color: {NAVY}; font-size: 13px; background: transparent; }}
QGroupBox QLabel {{ font-weight: 400; }}

/* --- buttons -------------------------------------------------------------------------------------- */
QPushButton {{ background: {CARD}; border: 1px solid {BORDER_STRONG}; border-radius: 8px; padding: 7px 14px;
    color: {TEXT}; }}
QPushButton:hover {{ background: #F0F5F8; border-color: #AEBBC7; }}
QPushButton:pressed {{ background: #E4ECF1; }}
QPushButton:disabled {{ color: #9BA8B4; background: #F4F7F9; border-color: {BORDER}; }}
QPushButton:focus {{ border: 1px solid {ACCENT}; }}
QPushButton#primary {{ background: {ACCENT}; color: white; border: 1px solid {ACCENT_DARK}; font-weight: 600; }}
QPushButton#primary:hover {{ background: {ACCENT_DARK}; }}
QPushButton#primary:pressed {{ background: #085868; }}
QPushButton#primary:disabled {{ background: #A9D2DA; border-color: #A9D2DA; color: #EEF8FA; }}
QPushButton#ghost {{ background: transparent; border: 1px solid transparent; color: {ACCENT_DARK}; }}
QPushButton#ghost:hover {{ background: {ACCENT_SOFT}; }}
QPushButton#chipButton {{ background: #EDF1F5; border: 1px solid transparent; border-radius: 14px; padding: 5px 12px;
    color: {MUTED}; }}
QPushButton#chipButton:hover {{ background: {ACCENT_SOFT}; color: {ACCENT_DARK}; }}

/* --- inputs --------------------------------------------------------------------------------------- */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background: white; border: 1px solid {BORDER_STRONG};
    border-radius: 8px; padding: 6px 10px; selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {ACCENT}; }}
QLineEdit:disabled, QComboBox:disabled, QDoubleSpinBox:disabled {{ background: #F0F3F6; color: #9BA8B4; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: white; border: 1px solid {BORDER_STRONG}; selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT}; outline: none; padding: 4px; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {BORDER_STRONG}; border-radius: 4px; background: white; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT_DARK}; }}
QTextEdit, QTextBrowser {{ background: white; border: 1px solid {BORDER}; border-radius: 10px; padding: 8px; }}

/* --- tables / lists / trees ----------------------------------------------------------------------- */
QTableView, QTableWidget, QTreeWidget, QListWidget {{ background: white; border: 1px solid {BORDER}; border-radius: 10px;
    alternate-background-color: #F8FAFC; selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT};
    gridline-color: #EEF2F6; outline: none; }}
QTableView::item, QTableWidget::item {{ padding: 4px 8px; }}
QTreeWidget::item {{ padding: 4px 4px; }}
QListWidget::item {{ padding: 5px 6px; }}
QHeaderView::section {{ background: #F1F5F8; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER};
    border-right: 1px solid #EAEFF3; padding: 8px 10px; font-weight: 600; font-size: 12px; }}
QTableCornerButton::section {{ background: #F1F5F8; border: none; }}

/* --- tabs ----------------------------------------------------------------------------------------- */
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 10px; background: {CARD}; top: -1px; padding: 8px; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 9px 16px; margin-right: 4px;
    border-bottom: 2px solid transparent; font-weight: 600; }}
QTabBar::tab:selected {{ color: {ACCENT_DARK}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover:!selected {{ color: {TEXT}; }}

/* --- misc ----------------------------------------------------------------------------------------- */
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #C5CFD8; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #A8B5C1; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #C5CFD8; border-radius: 5px; min-width: 30px; }}
QScrollBar::handle:horizontal:hover {{ background: #A8B5C1; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QProgressBar {{ border: none; border-radius: 3px; background: #E3EAF0; max-height: 5px; text-align: center; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QStatusBar {{ background: {CARD}; border-top: 1px solid {BORDER}; color: {MUTED}; font-size: 12px; }}
QStatusBar::item {{ border: none; }}
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 10px; }}
QSplitter::handle:vertical {{ height: 10px; }}
QToolBar {{ background: transparent; border: none; spacing: 2px; }}
QToolButton {{ border: 1px solid transparent; border-radius: 6px; padding: 3px; }}
QToolButton:hover {{ background: #E9EFF4; }}
QToolButton:checked {{ background: {ACCENT_SOFT}; border-color: #BFE0E7; }}
QDialog {{ background: {BG}; }}
QMessageBox {{ background: {CARD}; }}
"""


def apply_matplotlib_style() -> None:
    """Consistent, clean look for every figure (also used when saving)."""
    import matplotlib as mpl
    mpl.rcParams.update({
        "figure.facecolor": "white", "axes.facecolor": "white", "savefig.facecolor": "white",
        "axes.edgecolor": "#C9D3DC", "axes.linewidth": 0.8, "axes.labelcolor": MUTED, "axes.labelsize": 8.5,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "grid.color": "#E7EDF2", "grid.linewidth": 0.8,
        "xtick.color": MUTED, "ytick.color": MUTED, "xtick.labelsize": 8, "ytick.labelsize": 8,
        "xtick.major.size": 3, "ytick.major.size": 3,
        "legend.frameon": False, "legend.fontsize": 8, "text.color": TEXT,
        "font.family": "sans-serif",
        "font.sans-serif": ["Inter", "Segoe UI", "Helvetica Neue", "Arial", "DejaVu Sans"],
        "axes.prop_cycle": mpl.cycler(color=SERIES_COLOURS),
    })
