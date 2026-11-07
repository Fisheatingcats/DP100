#!/usr/bin/env python3
"""ATK-DP100 数控电源上位机 —— 程序入口。

用法:
    python main.py                # 正常启动
    python main.py --demo         # 无设备时用模拟数据预览界面
    python main.py --auto-connect # 启动后自动连接第一台设备
"""

from __future__ import annotations

import argparse
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication

from dp100.branding import app_icon, apply_windows_app_id, logo_exists
from dp100.main_window import MainWindow
from dp100.theme import APP_QSS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DP100 数控电源上位机")
    parser.add_argument("--demo", action="store_true",
                        help="使用模拟数据预览界面（不需要设备）")
    parser.add_argument("--auto-connect", action="store_true",
                        help="启动后自动连接第一台检测到的设备")
    parser.add_argument("--screenshot", metavar="PNG", default=None,
                        help="启动后截图保存并退出（开发调试用）")
    parser.add_argument("--screenshot-delay", type=int, default=1600,
                        help="截图前等待的毫秒数")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    apply_windows_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName("DP100 数控电源")
    app.setWindowIcon(app_icon())
    app.setStyle("Fusion")

    font = QFont("Microsoft YaHei UI", 10)
    font.setFamilies(["Microsoft YaHei UI", "Segoe UI", "PingFang SC"])
    app.setFont(font)
    app.setStyleSheet(APP_QSS)

    if not logo_exists():
        print("[提示] 未找到 dp100/img/logo.png，窗口将使用默认图标", file=sys.stderr)

    window = MainWindow(demo=args.demo, auto_connect=args.auto_connect)
    window.setWindowIcon(app_icon())
    window.show()

    if args.screenshot:
        def snap() -> None:
            pixmap = window.grab()
            if pixmap.save(args.screenshot):
                print(f"screenshot saved: {args.screenshot}")
            app.quit()

        QTimer.singleShot(args.screenshot_delay, snap)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
