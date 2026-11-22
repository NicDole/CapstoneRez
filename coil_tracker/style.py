"""Colours and Qt stylesheet (McGill red with neutral greys)."""

RED = "#ED1B2F"          # McGill red
RED_DARK = "#B5121B"
RED_TINT = "#FDECEE"
INK = "#1F1F1F"
MUTED = "#6B6B70"
LINE = "#E4E4E7"
BG = "#F5F5F6"
CARD = "#FFFFFF"

STATUS = {   # background, text, accent
    "OK": ("#E8F5EC", "#1E6B34", "#2E8B4A"),
    "WATCH": ("#FFF5E1", "#7A4A00", "#E39B2B"),
    "FAULT": (RED_TINT, RED_DARK, RED),
}
LEVEL = {    # text colour, chip background
    "Fault": (RED_DARK, RED_TINT),
    "Watch": ("#7A4A00", "#FFF5E1"),
    "Routine check": ("#3D4A5C", "#EEF1F5"),
    "Low signal": (MUTED, "#F0F0F1"),
}

QSS = f"""
* {{ font-family: "Segoe UI", "Helvetica Neue", Arial; font-size: 10pt; color: {INK}; }}
QMainWindow, QWidget#page, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {BG}; }}
QFrame#appHeader {{ background: {RED}; }}
QFrame#appHeader QLabel {{ color: white; }}
QLabel#appTitle {{ font-size: 15pt; font-weight: 600; color: white; }}
QLabel#appSub {{ font-size: 9pt; color: #FFE3E6; }}
QFrame#sidebar {{ background: {CARD}; border-right: 1px solid {LINE}; }}
QFrame#card {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 10px; }}
QLabel#h1 {{ font-size: 16pt; font-weight: 600; }}
QLabel#h2 {{ font-size: 11.5pt; font-weight: 600; }}
QLabel#muted {{ color: {MUTED}; font-size: 9pt; }}
QPushButton {{ background: {CARD}; border: 1px solid #CFCFD4; border-radius: 6px; padding: 6px 14px; }}
QPushButton:hover {{ border-color: {RED}; color: {RED_DARK}; }}
QPushButton:disabled {{ color: #A8A8AD; border-color: {LINE}; }}
QPushButton#primary {{ background: {RED}; color: white; border: none; font-weight: 600; }}
QPushButton#primary:hover {{ background: {RED_DARK}; color: white; }}
QPushButton#headerPrimary {{ background: white; color: {RED_DARK}; border: none; font-weight: 600; padding: 7px 16px; }}
QPushButton#headerPrimary:hover {{ background: #FFE3E6; }}
QPushButton#headerGhost {{ background: transparent; color: white; border: 1px solid rgba(255,255,255,0.55); }}
QPushButton#headerGhost:hover {{ background: rgba(255,255,255,0.15); color: white; border-color: white; }}
QPushButton#small {{ padding: 3px 10px; font-size: 9pt; }}
QListWidget {{ background: {CARD}; border: none; outline: none; }}
QListWidget::item {{ padding: 10px 10px; border-radius: 6px; margin: 2px 4px; }}
QListWidget::item:selected {{ background: {RED_TINT}; color: {RED_DARK}; border-left: 3px solid {RED}; }}
QListWidget::item:hover:!selected {{ background: #F4F4F5; }}
QTableWidget {{ background: {CARD}; border: none; gridline-color: {LINE}; selection-background-color: {RED_TINT};
               selection-color: {INK}; alternate-background-color: #FAFAFB; }}
QHeaderView::section {{ background: #F7F7F8; color: {MUTED}; border: none; border-bottom: 1px solid {LINE};
                        padding: 6px 8px; font-weight: 600; font-size: 9pt; }}
QLineEdit, QComboBox {{ background: white; border: 1px solid #CFCFD4; border-radius: 6px; padding: 5px 8px; }}
QLineEdit:focus, QComboBox:focus {{ border-color: {RED}; }}
QRadioButton::indicator:checked {{ background: {RED}; border: 2px solid white; border-radius: 6px; }}
QSplitter::handle {{ background: {LINE}; width: 1px; }}
QProgressDialog QLabel {{ font-size: 10pt; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: #CFCFD4; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
"""
