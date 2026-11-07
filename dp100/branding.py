"""品牌资源：Logo 路径与窗口图标加载。"""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon, QPixmap

PACKAGE_DIR = Path(__file__).resolve().parent
LOGO_PATH = PACKAGE_DIR / "img" / "logo.png"


def logo_exists() -> bool:
    return LOGO_PATH.is_file()


def app_icon() -> QIcon:
    """窗口 / 任务栏图标。Logo 缺失时返回空 QIcon，不影响程序运行。"""
    icon = QIcon()
    if LOGO_PATH.is_file():
        pixmap = QPixmap(str(LOGO_PATH))
        if not pixmap.isNull():
            icon.addPixmap(pixmap)
    return icon


def logo_pixmap(size: int) -> QPixmap:
    """取指定边长的 Logo 缩略图（保持宽高比、平滑缩放）。"""
    if not LOGO_PATH.is_file():
        return QPixmap()
    pixmap = QPixmap(str(LOGO_PATH))
    if pixmap.isNull():
        return QPixmap()
    return pixmap.scaled(size, size,
                         Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)


def apply_windows_app_id(app_id: str = "Alientek.DP100.PowerGUI") -> None:
    """设置 AppUserModelID，让任务栏显示本程序自己的图标而不是 python 的图标。"""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass
