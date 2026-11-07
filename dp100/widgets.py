"""自定义控件：指标卡片、实时曲线、状态指示。"""

from __future__ import annotations

import math
import time

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from . import theme


class MetricCard(QFrame):
    """一个数值指标卡：标题 + 大数字 + 单位 + 副标题。"""

    def __init__(self, title: str, unit: str, accent: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setProperty("card", "true")
        self.setMinimumHeight(116)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(7)
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {accent}; font-size: 11px;")
        title_label = QLabel(title)
        title_label.setProperty("muted", "true")
        head.addWidget(dot)
        head.addWidget(title_label)
        head.addStretch(1)
        self._badge = QLabel("")
        self._badge.setProperty("chip", "true")
        self._badge.hide()
        head.addWidget(self._badge)
        root.addLayout(head)

        value_row = QHBoxLayout()
        value_row.setSpacing(6)
        value_row.setContentsMargins(0, 0, 0, 0)
        self._value = QLabel("--")
        font = QFont("DIN Alternate", 22)
        font.setStyleHint(QFont.StyleHint.SansSerif)
        font.setWeight(QFont.Weight.DemiBold)
        font.setFamilies(["Consolas", "Bahnschrift", "Microsoft YaHei UI"])
        self._value.setFont(font)
        self._value.setStyleSheet(f"color: {theme.TEXT};")
        self._unit = QLabel(unit)
        self._unit.setProperty("muted", "true")
        self._unit.setStyleSheet(f"color: {theme.MUTED}; font-size: 12px; padding-top: 10px;")
        value_row.addWidget(self._value, 0, Qt.AlignmentFlag.AlignBottom)
        value_row.addWidget(self._unit, 0, Qt.AlignmentFlag.AlignBottom)
        value_row.addStretch(1)
        root.addLayout(value_row)

        self._hint = QLabel("")
        self._hint.setStyleSheet(f"color: {theme.MUTED}; font-size: 11px;")
        root.addWidget(self._hint)

    def set_value(self, text: str, hint: str = "") -> None:
        self._value.setText(text)
        self._hint.setText(hint)

    def set_badge(self, text: str, color: str = theme.MUTED) -> None:
        if not text:
            self._badge.hide()
            return
        self._badge.setText(text)
        self._badge.setStyleSheet(
            f"color: {color}; background: #0E141C; border: 1px solid {theme.BORDER};"
            "border-radius: 9px; padding: 2px 8px; font-size: 11px;")
        self._badge.show()


class TrendChart(QWidget):
    """单指标实时曲线：电压与电流分开显示，各自独立纵轴自适应。

    电流往往只有几十 mA，和 8 V 电压画在一张图里会压成一条平线，
    所以这里只画一个物理量，纵轴按该量的近期最大值自动缩放。
    """

    WINDOW_S = 60.0

    METRICS = {
        "voltage": {"index": 1, "unit": "V", "color": theme.ACCENT,
                    "name": "电压", "steps": (1.0, 2.0, 5.0, 10.0), "floor": 5.0,
                    "decimals": 2},
        "current": {"index": 2, "unit": "A", "color": theme.BLUE,
                    "name": "电流", "steps": (0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0),
                    "floor": 0.02, "decimals": 3},
    }

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._samples: list[tuple[float, float, float]] = []
        self._metric = "voltage"

    # -- 数据 ------------------------------------------------------------- #
    # 注意：这里不能用 property 名 `metric`，它会覆盖 QPaintDevice.metric()
    # 虚函数，导致 Qt 绘图层调用时崩溃（'str' object is not callable）。
    @property
    def metric_name(self) -> str:
        return self._metric

    def set_metric(self, name: str) -> None:
        if name in self.METRICS and name != self._metric:
            self._metric = name
            self.update()

    def add_sample(self, seconds: float, voltage: float, current: float) -> None:
        self._samples.append((seconds, voltage, current))
        cutoff = seconds - self.WINDOW_S * 2
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.pop(0)
        self.update()

    def clear(self) -> None:
        self._samples.clear()
        self.update()

    @property
    def has_data(self) -> bool:
        return bool(self._samples)

    # -- 绘制 ------------------------------------------------------------- #
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.CARD))
        painter.drawRoundedRect(rect, 16, 16)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(theme.BORDER), 1))
        painter.drawRoundedRect(rect, 16, 16)

        cfg = self.METRICS[self._metric]
        index = cfg["index"]
        line_color = QColor(cfg["color"])
        unit = cfg["unit"]
        decimals = cfg["decimals"]

        left, right, top, bottom = 58, 20, 42, 30
        plot = QRectF(rect.left() + left, rect.top() + top,
                      rect.width() - left - right, rect.height() - top - bottom)
        if plot.width() <= 10 or plot.height() <= 10:
            return

        values = [s[index] for s in self._samples]
        y_max = self._nice_ceil(max(values + [cfg["floor"]]) * 1.08, cfg["steps"])
        y_max = max(y_max, cfg["floor"])

        if self._samples:
            now = self._samples[-1][0]
            t0 = max(self._samples[0][0], now - self.WINDOW_S)
            span = max(now - t0, 0.001)
        else:
            now = 0.0
            t0 = -self.WINDOW_S
            span = self.WINDOW_S

        # 网格
        grid_pen = QPen(QColor(34, 48, 63, 150))
        grid_pen.setWidthF(1.0)
        painter.setPen(grid_pen)
        for k in range(5):
            y = plot.top() + plot.height() * k / 4
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        for k in range(7):
            x = plot.left() + plot.width() * k / 6
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))

        # 纵轴刻度（只有左侧一套，因为只有一个物理量）
        painter.setFont(QFont("Microsoft YaHei UI", 8))
        painter.setPen(QColor(theme.MUTED))
        for k in range(5):
            y = plot.top() + plot.height() * k / 4
            value = y_max * (4 - k) / 4
            painter.drawText(QRectF(rect.left() + 4, y - 8, left - 10, 16),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             f"{value:.{decimals}f}")
        for k in range(7):
            x = plot.left() + plot.width() * k / 6
            secs = -span + span * k / 6
            text = "0 s" if k == 6 else f"{secs:.0f}"
            painter.drawText(QRectF(x - 24, plot.bottom() + 6, 48, 16),
                             Qt.AlignmentFlag.AlignCenter, text)

        # 标题（左上）：当前显示的量 + 单位
        painter.setFont(QFont("Microsoft YaHei UI", 9, QFont.Weight.DemiBold))
        painter.setPen(QColor(theme.TEXT))
        painter.drawText(QRectF(plot.left(), rect.top() + 12, 200, 20),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         f"{cfg['name']} ({unit})")

        if not self._samples:
            painter.setPen(QColor(theme.MUTED))
            painter.setFont(QFont("Microsoft YaHei UI", 11))
            painter.drawText(plot, Qt.AlignmentFlag.AlignCenter, "等待设备数据…")
            return

        def to_px(t: float, value: float) -> QPointF:
            x = plot.left() + (t - t0) / span * plot.width()
            y = plot.bottom() - (value / y_max) * plot.height()
            return QPointF(x, y)

        visible = [s for s in self._samples if s[0] >= t0 - 1]
        if not visible:
            visible = self._samples[-1:]

        points = [to_px(s[0], s[index]) for s in visible]
        path = QPainterPath(points[0])
        for p in points[1:]:
            path.lineTo(p)
        fill = QPainterPath(path)
        fill.lineTo(points[-1].x(), plot.bottom())
        fill.lineTo(points[0].x(), plot.bottom())

        fill_color = QColor(line_color)
        fill_color.setAlpha(38)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill_color)
        painter.drawPath(fill)

        pen = QPen(line_color)
        pen.setWidthF(2.0)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(line_color)
        painter.drawEllipse(points[-1], 3.2, 3.2)

        # 右上角当前值
        painter.setFont(QFont("Microsoft YaHei UI", 11, QFont.Weight.DemiBold))
        painter.setPen(line_color)
        painter.drawText(QRectF(plot.right() - 160, rect.top() + 10, 160, 22),
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                         f"{visible[-1][index]:.{decimals}f} {unit}")

    # -- 工具 ------------------------------------------------------------- #
    @staticmethod
    def _nice_ceil(value: float, steps: tuple[float, ...]) -> float:
        for step in steps:
            candidate = math.ceil(value / step) * step
            if candidate >= value:
                return candidate
        return value
