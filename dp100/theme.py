"""深色仪表风主题：配色表 + 全局 QSS。"""

from __future__ import annotations

from PyQt6.QtWidgets import QWidget

# 配色
BG = "#0B0F14"
SIDEBAR = "#0F151D"
CARD = "#131A23"
CARD_HOVER = "#18212D"
BORDER = "#22303F"
TEXT = "#E8F0F8"
MUTED = "#7C8CA0"
ACCENT = "#35D6A4"      # 主色：青绿
ACCENT_DIM = "#1F8E6D"
BLUE = "#4CB8FF"        # 电流色
AMBER = "#FFB454"
RED = "#FF6B6B"
WHITE = "#FFFFFF"

APP_QSS = f"""
* {{
    font-family: "Microsoft YaHei UI", "Segoe UI", "PingFang SC", sans-serif;
}}

QWidget {{
    color: {TEXT};
}}

#Root {{
    background: {BG};
}}
#Sidebar {{
    background: {SIDEBAR};
    border-right: 1px solid {BORDER};
}}
#Content {{
    background: {BG};
}}

/* ---------- 卡片 ---------- */
QFrame[card="true"] {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 16px;
}}
QFrame[card="true"][accent="true"] {{
    border: 1px solid #2C4A42;
}}

/* ---------- 文字 ---------- */
QLabel[muted="true"] {{
    color: {MUTED};
}}
QLabel[section="true"] {{
    color: {MUTED};
    font-size: 12px;
    letter-spacing: 2px;
}}
QLabel[title="true"] {{
    color: {TEXT};
    font-size: 19px;
    font-weight: bold;
}}
QLabel[chip="true"] {{
    color: {MUTED};
    background: #0E141C;
    border: 1px solid {BORDER};
    border-radius: 9px;
    padding: 3px 9px;
    font-size: 11px;
}}

/* ---------- 输入控件 ---------- */
QDoubleSpinBox, QSpinBox {{
    background: #0D131A;
    border: 1px solid #263545;
    border-radius: 10px;
    padding: 7px 10px;
    color: {TEXT};
    selection-background-color: {ACCENT_DIM};
}}
QDoubleSpinBox:hover, QSpinBox:hover {{
    border: 1px solid #33506A;
}}
QDoubleSpinBox:focus, QSpinBox:focus {{
    border: 1px solid {ACCENT};
}}
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button,
QSpinBox::up-button, QSpinBox::down-button {{
    width: 0px;
    border: none;
}}

QComboBox {{
    background: #0D131A;
    border: 1px solid #263545;
    border-radius: 10px;
    padding: 7px 10px;
    color: {TEXT};
}}
QComboBox:hover {{
    border: 1px solid #33506A;
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox QAbstractItemView {{
    background: #101821;
    border: 1px solid {BORDER};
    border-radius: 8px;
    selection-background-color: #1C2B3B;
    color: {TEXT};
    outline: none;
    padding: 4px;
}}

/* ---------- 按钮 ---------- */
QPushButton {{
    background: #18212D;
    border: 1px solid #263545;
    border-radius: 11px;
    padding: 9px 16px;
    color: {TEXT};
    font-size: 13px;
}}
QPushButton:hover {{
    background: #1F2B39;
    border: 1px solid #33465C;
}}
QPushButton:pressed {{
    background: #141C26;
}}
QPushButton:disabled {{
    color: #55616F;
    background: #121920;
    border: 1px solid #1C2733;
}}

QPushButton[variant="primary"] {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
    color: #04130D;
    font-weight: bold;
}}
QPushButton[variant="primary"]:hover {{
    background: #47E8B6;
    border: 1px solid #47E8B6;
}}
QPushButton[variant="primary"]:pressed {{
    background: #2BB98C;
}}

QPushButton[variant="danger"] {{
    background: #3A1A1C;
    border: 1px solid #7A3034;
    color: {RED};
}}
QPushButton[variant="danger"]:hover {{
    background: #4A2023;
}}

QPushButton[variant="ghost"] {{
    background: transparent;
    border: 1px solid #263545;
    color: {MUTED};
}}
QPushButton[variant="ghost"]:hover {{
    color: {TEXT};
    border: 1px solid #33465C;
}}

/* 分段切换按钮（电压 / 电流） */
QPushButton[seg="true"] {{
    background: transparent;
    border: 1px solid #263545;
    border-radius: 9px;
    padding: 6px 14px;
    color: {MUTED};
    font-size: 12px;
}}
QPushButton[seg="true"]:hover {{
    color: {TEXT};
    border: 1px solid #3A4E64;
}}
QPushButton[seg="true"]:checked {{
    background: #16302A;
    border: 1px solid {ACCENT};
    color: {ACCENT};
    font-weight: bold;
}}

/* 输出开关大按钮 */
QPushButton#PowerButton {{
    border-radius: 14px;
    padding: 14px 18px;
    font-size: 15px;
    font-weight: bold;
    background: #18212D;
    border: 1px solid #263545;
    color: {TEXT};
}}
QPushButton#PowerButton[on="true"] {{
    background: #0E3A2B;
    border: 1px solid {ACCENT};
    color: {ACCENT};
}}
QPushButton#PowerButton[on="true"]:hover {{
    background: #124734;
}}

/* ---------- 表格 ---------- */
QTableWidget {{
    background: #0F151D;
    border: 1px solid {BORDER};
    border-radius: 12px;
    gridline-color: #1A2431;
    selection-background-color: #1C2B3B;
    alternate-background-color: #121A23;
}}
QTableWidget::item {{
    padding: 5px;
}}
QHeaderView::section {{
    background: #0F151D;
    color: {MUTED};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 8px 6px;
    font-size: 12px;
}}
QTableCornerButton::section {{
    background: #0F151D;
    border: none;
}}

/* ---------- 勾选框 ---------- */
QCheckBox {{
    spacing: 8px;
    color: {TEXT};
}}
QCheckBox::indicator {{
    width: 17px;
    height: 17px;
    border: 1px solid #2C3A4B;
    border-radius: 5px;
    background: #0D131A;
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
}}
QCheckBox::indicator:hover {{
    border: 1px solid #3E5670;
}}

/* ---------- 选项卡 ---------- */
QTabWidget::pane {{
    border: none;
    background: transparent;
}}
QTabBar::tab {{
    background: transparent;
    color: {MUTED};
    padding: 6px 16px;
    margin-right: 2px;
    border: none;
    border-bottom: 2px solid transparent;
    font-size: 12px;
}}
QTabBar::tab:selected {{
    color: {TEXT};
    border-bottom: 2px solid {ACCENT};
}}
QTabBar::tab:hover {{
    color: {TEXT};
}}
QTimeEdit {{
    background: #0D131A;
    border: 1px solid #263545;
    border-radius: 10px;
    padding: 7px 10px;
    color: {TEXT};
}}
QTimeEdit:focus {{
    border: 1px solid {ACCENT};
}}

/* ---------- 进度条 ---------- */
QProgressBar {{
    background: #0D131A;
    border: 1px solid #263545;
    border-radius: 8px;
    min-height: 14px;
    max-height: 14px;
    text-align: center;
    color: {TEXT};
    font-size: 10px;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 {ACCENT_DIM}, stop:1 {ACCENT});
    border-radius: 7px;
}}

/* ---------- 滚动条 ---------- */
QScrollBar:vertical {{
    background: transparent;
    width: 9px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: #25313F;
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: #33455A;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}

QToolTip {{
    background: #16202B;
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px;
}}
"""


def repolish(widget: QWidget) -> None:
    """动态属性变化后重刷样式。"""
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)
    widget.update()
