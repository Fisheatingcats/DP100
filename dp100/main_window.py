"""主窗口。"""

from __future__ import annotations

import csv
import json
import os
import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QMetaObject, QThread, QTime, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .branding import app_icon, logo_pixmap
from .charger import (CHEMISTRIES, PHASE_LABELS, ChargeProfile, ChargeSession,
                      Phase, Command, soc_from_voltage)
from .protocol import BasicInfo, BasicSet, DeviceInfo
from .tasks import (
    ACTION_LABELS,
    ACTION_UNITS,
    COMPARATOR_LABELS,
    KIND_LABELS,
    SOURCE_LABELS,
    Task,
    TaskEngine,
)
from .transport import list_dp100
from .widgets import MetricCard, TrendChart
from .worker import DeviceWorker

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
TASKS_FILE = Path(__file__).resolve().parents[1] / "tasks.json"
CHARGE_FILE = Path(__file__).resolve().parents[1] / "charge_settings.json"


class MainWindow(QMainWindow):
    # 发往 worker 线程的命令
    cmd_open = pyqtSignal(str)
    cmd_close = pyqtSignal()
    cmd_apply = pyqtSignal(int, int, int, int, int)
    cmd_output = pyqtSignal(int)
    cmd_query_presets = pyqtSignal()

    def __init__(self, demo: bool = False, auto_connect: bool = False,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("DP100 数控电源上位机")
        self.setWindowIcon(app_icon())
        self.resize(1260, 840)
        self.setMinimumSize(1080, 720)

        self._demo = demo
        self._connected = False
        self._output_on = False
        self._preset: BasicSet | None = None
        self._last_info: BasicInfo | None = None
        self._monitor_hits = 0
        self._csv_file = None
        self._csv_writer = None
        self._t0 = time.monotonic()
        self._demo_t = 0.0
        self._last_status_time = time.monotonic()
        self._closing = False
        self._charge_probing = False
        self._probe_v1_mv = 0
        self.charger = ChargeSession()

        self._build_ui()
        self._set_controls_enabled(False)

        # 自动化任务引擎
        self.engine = TaskEngine(self)
        self.engine.fired.connect(self._on_task_fired)
        self.engine.tasks_changed.connect(self._refresh_task_table)
        self.engine.load(TASKS_FILE)
        self.chk_task_enable.blockSignals(True)
        self.chk_task_enable.setChecked(self.engine.enabled)
        self.chk_task_enable.blockSignals(False)
        self.engine.set_enabled(self.engine.enabled)
        self._refresh_task_table()
        self._load_charge_settings()

        if demo:
            self._start_demo()
        else:
            self._setup_worker()
            self._refresh_devices()
            if auto_connect:
                QTimer.singleShot(300, self._try_auto_connect)

    # ------------------------------------------------------------------ #
    # 界面搭建
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_sidebar())
        outer.addWidget(self._build_content(), 1)

        self.statusBar().setStyleSheet(
            f"QStatusBar{{background:{theme.SIDEBAR};color:{theme.MUTED};"
            f"border-top:1px solid {theme.BORDER};font-size:11px;}}")
        self.statusBar().showMessage("就绪")

    # -- 侧边栏 ---------------------------------------------------------- #
    def _build_sidebar(self) -> QWidget:
        side = QWidget()
        side.setObjectName("Sidebar")
        side.setFixedWidth(396)
        col = QVBoxLayout(side)
        col.setContentsMargins(18, 16, 18, 14)
        col.setSpacing(11)

        # 品牌 + 连接状态
        brand = QHBoxLayout()
        brand.setSpacing(10)
        logo_label = QLabel()
        logo_label.setFixedSize(44, 44)
        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = logo_pixmap(40)
        if not pixmap.isNull():
            logo_label.setPixmap(pixmap)
            # Logo 本身是深色，衬一层浅底保证在深色主题下清晰
            logo_label.setStyleSheet(
                "background:#F3F6FA;border:1px solid #2A3644;border-radius:11px;")
        else:
            logo_label.hide()
        brand.addWidget(logo_label, 0, Qt.AlignmentFlag.AlignVCenter)

        name_box = QVBoxLayout()
        name_box.setSpacing(1)
        title = QLabel("DP100")
        title.setFont(self._font(24, QFont.Weight.Bold))
        title.setStyleSheet(f"color:{theme.TEXT};")
        subtitle = QLabel("数控电源上位机 · ATK-DP100")
        subtitle.setStyleSheet(f"color:{theme.MUTED};font-size:11px;")
        name_box.addWidget(title)
        name_box.addWidget(subtitle)
        brand.addLayout(name_box)
        brand.addStretch(1)
        self.chip_state = QLabel("未连接")
        self.chip_state.setProperty("chip", "true")
        brand.addWidget(self.chip_state, 0, Qt.AlignmentFlag.AlignTop)
        col.addLayout(brand)

        # 设备连接
        card = self._card("设备连接")
        row = QHBoxLayout()
        row.setSpacing(8)
        self.combo_devices = QComboBox()
        self.combo_devices.setMinimumWidth(230)
        self.btn_refresh = QPushButton("⟳")
        self.btn_refresh.setFixedWidth(46)
        self.btn_refresh.setToolTip("重新扫描 USB 设备")
        self.btn_refresh.clicked.connect(self._refresh_devices)
        row.addWidget(self.combo_devices, 1)
        row.addWidget(self.btn_refresh)
        card.layout().addLayout(row)

        self.btn_connect = QPushButton("连接设备")
        self.btn_connect.setProperty("variant", "primary")
        self.btn_connect.clicked.connect(self._on_connect_clicked)
        card.layout().addWidget(self.btn_connect)

        self.btn_connect.setToolTip("USB-A 需切到 USBD 模式；Type-C 仅负责供电")
        col.addWidget(card)

        # 输出设定（电压/电流/保护限值合并为一张卡）
        card = self._card("输出设定")
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(5)
        grid.addLayout(self._spin_block("电压", "V"), 0, 0)
        grid.addLayout(self._spin_block("电流", "A"), 0, 1)
        grid.addLayout(self._spin_block("过压 OVP", "V", kind="prot"), 1, 0)
        grid.addLayout(self._spin_block("过流 OCP", "A", kind="prot"), 1, 1)
        card.layout().addLayout(grid)

        self.btn_apply = QPushButton("应用设定（含保护限值）")
        self.btn_apply.setProperty("variant", "primary")
        self.btn_apply.clicked.connect(self._on_apply_clicked)
        card.layout().addWidget(self.btn_apply)
        self.btn_prot = self.btn_apply
        col.addWidget(card)

        # 输出开关
        self.btn_power = QPushButton("⏻  输出已关闭")
        self.btn_power.setObjectName("PowerButton")
        self.btn_power.setProperty("on", "false")
        self.btn_power.clicked.connect(self._on_power_clicked)
        col.addWidget(self.btn_power)

        # 充电监控
        card = self._card("充电自动关断")
        self.chk_auto = QCheckBox("电流回落后自动关闭输出")
        self.chk_auto.toggled.connect(self._on_auto_toggled)
        card.layout().addWidget(self.chk_auto)
        monitor = QHBoxLayout()
        monitor.setSpacing(10)
        monitor.addLayout(self._spin_block("目标电压", "V", kind="cut"))
        monitor.addLayout(self._spin_block("截止电流", "A", kind="cut"))
        card.layout().addLayout(monitor)
        self.lbl_auto = QLabel("未启用")
        self.lbl_auto.setStyleSheet(f"color:{theme.MUTED};font-size:11px;")
        self.lbl_auto.setWordWrap(True)
        card.layout().addWidget(self.lbl_auto)
        col.addWidget(card)

        col.addStretch(1)

        return side

    def _spin_block(self, label: str, unit: str, kind: str = "out") -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(4)
        caption = QLabel(f"{label} ({unit})")
        caption.setStyleSheet(f"color:{theme.MUTED};font-size:11px;")
        spin = QDoubleSpinBox()
        spin.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        spin.setStyleSheet("font-size: 19px; padding: 7px 10px;")
        if kind == "out" and label == "电压":
            spin.setRange(0.0, 30.0)
            spin.setDecimals(2)
            spin.setSingleStep(0.01)
            spin.setValue(8.40)
            self.spin_voltage = spin
        elif kind == "out":
            spin.setRange(0.0, 5.0)
            spin.setDecimals(3)
            spin.setSingleStep(0.001)
            spin.setValue(1.000)
            self.spin_current = spin
        elif kind == "prot" and "过压" in label:
            spin.setRange(0.1, 30.5)
            spin.setDecimals(2)
            spin.setSingleStep(0.1)
            spin.setValue(8.70)
            self.spin_ovp = spin
        elif kind == "prot":
            spin.setRange(0.1, 5.05)
            spin.setDecimals(2)
            spin.setSingleStep(0.05)
            spin.setValue(1.20)
            self.spin_ocp = spin
        elif "目标" in label:
            spin.setRange(0.0, 30.0)
            spin.setDecimals(2)
            spin.setSingleStep(0.01)
            spin.setValue(8.40)
            self.spin_cut_v = spin
        else:
            spin.setRange(0.01, 5.0)
            spin.setDecimals(3)
            spin.setSingleStep(0.005)
            spin.setValue(0.050)
            self.spin_cut_i = spin
        box.addWidget(caption)
        box.addWidget(spin)
        return box

    # -- 主内容区 -------------------------------------------------------- #
    def _build_content(self) -> QWidget:
        content = QWidget()
        content.setObjectName("Content")
        col = QVBoxLayout(content)
        col.setContentsMargins(18, 16, 18, 14)
        col.setSpacing(13)

        header = QHBoxLayout()
        hb = QVBoxLayout()
        hb.setSpacing(2)
        page_title = QLabel("实时监测")
        page_title.setProperty("title", "true")
        page_sub = QLabel("每 200 ms 从设备读取一次实时状态")
        page_sub.setStyleSheet(f"color:{theme.MUTED};font-size:11px;")
        hb.addWidget(page_title)
        hb.addWidget(page_sub)
        header.addLayout(hb)
        header.addStretch(1)

        # 图表视图切换：电压 / 电流（分开画，各自纵轴自适应）
        self.btn_metric_v = QPushButton("电压 V")
        self.btn_metric_i = QPushButton("电流 A")
        self.metric_group = QButtonGroup(self)
        self.metric_group.setExclusive(True)
        for index, btn in enumerate((self.btn_metric_v, self.btn_metric_i)):
            btn.setCheckable(True)
            btn.setProperty("seg", "true")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.metric_group.addButton(btn, index)
            header.addWidget(btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.btn_metric_v.setChecked(True)
        self.metric_group.idClicked.connect(self._on_metric_changed)
        header.addSpacing(6)

        self.chip_path = QLabel("USB 未连接")
        self.chip_path.setProperty("chip", "true")
        header.addWidget(self.chip_path, 0, Qt.AlignmentFlag.AlignVCenter)
        self.btn_record = QPushButton("开始记录 CSV")
        self.btn_record.setProperty("variant", "ghost")
        self.btn_record.clicked.connect(self._toggle_record)
        header.addWidget(self.btn_record, 0, Qt.AlignmentFlag.AlignVCenter)
        self.btn_folder = QPushButton("数据目录")
        self.btn_folder.setProperty("variant", "ghost")
        self.btn_folder.clicked.connect(self._open_data_dir)
        header.addWidget(self.btn_folder, 0, Qt.AlignmentFlag.AlignVCenter)
        col.addLayout(header)

        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.card_v = MetricCard("输出电压", "V", theme.ACCENT)
        self.card_i = MetricCard("输出电流", "A", theme.BLUE)
        self.card_p = MetricCard("输出功率", "W", theme.AMBER)
        self.card_t = MetricCard("设备温度", "℃", theme.RED)
        for card in (self.card_v, self.card_i, self.card_p, self.card_t):
            cards.addWidget(card)
        col.addLayout(cards)

        self.chart = TrendChart()
        col.addWidget(self.chart, 3)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("BottomTabs")
        self.tabs.addTab(self._scrollable(self._build_charge_panel()), "充电模式")
        self.tabs.addTab(self._build_preset_card(), "预设组")
        self.tabs.addTab(self._scrollable(self._build_task_panel()), "自动化任务")
        self.tabs.addTab(self._build_log_card(), "运行日志")
        col.addWidget(self.tabs, 4)
        return content

    def _build_preset_card(self) -> QFrame:
        card = self._card("预设组 P0 – P9")
        self.table = QTableWidget(10, 5)
        self.table.setHorizontalHeaderLabels(["组", "电压 (V)", "电流 (A)", "OVP (V)", "OCP (A)"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(22)
        self.table.setStyleSheet("font-size: 11px;")
        for row in range(10):
            self.table.setItem(row, 0, QTableWidgetItem(f"P{row}"))
            for col in range(1, 5):
                item = QTableWidgetItem("--")
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, col, item)
        self.table.cellDoubleClicked.connect(self._on_preset_row_activated)
        card.layout().addWidget(self.table)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_read_presets = QPushButton("读取设备预设")
        self.btn_read_presets.clicked.connect(lambda: self.cmd_query_presets.emit())
        self.btn_load_preset = QPushButton("载入选中行")
        self.btn_load_preset.setProperty("variant", "ghost")
        self.btn_load_preset.clicked.connect(self._on_load_preset)
        row.addWidget(self.btn_read_presets)
        row.addWidget(self.btn_load_preset)
        row.addStretch(1)
        card.layout().addLayout(row)
        return card

    def _build_log_card(self) -> QFrame:
        card = self._card("运行日志")
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(300)
        self.log_view.setStyleSheet(
            "QPlainTextEdit{background:#0F151D;border:1px solid %s;"
            "border-radius:12px;padding:8px;color:#AEBCCC;"
            "font-family:Consolas,'Microsoft YaHei UI';font-size:11px;}" % theme.BORDER)
        card.layout().addWidget(self.log_view, 1)
        return card

    # ------------------------------------------------------------------ #
    # 充电模式
    # ------------------------------------------------------------------ #
    CHEM_SHORT = {"li_ion": "锂离子 4.20V", "li_ion_hv": "高压锂电 4.35V",
                  "lifepo4": "磷酸铁锂 3.65V"}

    @staticmethod
    def _field(label: str, widget: QWidget) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(3)
        caption = QLabel(label)
        caption.setStyleSheet(f"color:{theme.MUTED};font-size:10px;")
        box.addWidget(caption)
        box.addWidget(widget)
        return box

    @staticmethod
    def _spin(lo: float, hi: float, value: float, decimals: int,
              suffix: str, step: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(lo, hi)
        spin.setDecimals(decimals)
        spin.setSingleStep(step)
        spin.setValue(value)
        spin.setSuffix(suffix)
        spin.setStyleSheet("font-size: 11px; padding: 4px 8px;")
        spin.setMinimumWidth(104)
        return spin

    def _build_charge_panel(self) -> QWidget:
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(2, 8, 2, 2)
        lay.setSpacing(10)

        # ------------- 参数区 ------------- #
        form_card = QFrame()
        form_card.setProperty("card", "true")
        fl = QVBoxLayout(form_card)
        fl.setContentsMargins(14, 8, 14, 8)
        fl.setSpacing(5)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)

        self.spin_cells = QSpinBox()
        self.spin_cells.setRange(1, 8)
        self.spin_cells.setValue(2)
        self.spin_cells.setSuffix(" S")
        self.spin_cells.setStyleSheet("font-size: 12px; padding: 4px 8px;")

        self.cb_chem = QComboBox()
        for key, label in self.CHEM_SHORT.items():
            self.cb_chem.addItem(label, key)
        self.cb_chem.setStyleSheet("font-size: 11px;")

        self.spin_capacity = self._spin(100, 20000, 3600, 0, " mAh", 100)
        self.spin_cc_rate = self._spin(0.05, 1.0, 0.5, 2, " C", 0.05)
        self.spin_trickle_rate = self._spin(0.01, 0.5, 0.10, 2, " C", 0.01)
        self.spin_trickle_exit = self._spin(2.50, 3.40, 3.00, 2, " V/节", 0.05)
        self.spin_cv_term = self._spin(0.01, 0.20, 0.05, 2, " C", 0.01)
        self.spin_target_v = self._spin(2.5, 30.0, 8.40, 2, " V", 0.05)
        self.spin_target_v.setToolTip("充到该电压（整包静置电压）视为达到目标；"
                                      "默认 100% 对应 CV 电压")
        self.spin_max_min = self._spin(10, 1440, 480, 0, " min", 10)
        self.spin_temp_limit = self._spin(20, 70, 45, 0, " ℃", 1)

        # 起始电压：默认全自动（零电流测 OCV），不放在参数区打扰用户；
        # 只有遇到特殊情况才在“高级”里手动指定。
        self.spin_start_v = self._spin(2.0, 30.0, 8.20, 2, " V", 0.01)
        self.spin_start_v.setMaximumWidth(120)
        self.chk_manual_soc = QCheckBox("高级：手动指定起始电压")
        self.chk_manual_soc.setToolTip(
            "默认自动测量：输出关着时直接用静置电压；输出开着时先把电流降到 0 "
            "约 2 秒，用开路电压估算。\n"
            "只有在你确切知道电池当前电压、或电池特殊时才需要手动填。")
        self.chk_manual_soc.toggled.connect(self._on_manual_soc_toggled)
        self.spin_start_v.setVisible(False)

        grid.addLayout(self._field("电芯数", self.spin_cells), 0, 0)
        grid.addLayout(self._field("化学体系", self.cb_chem), 0, 1)
        grid.addLayout(self._field("容量", self.spin_capacity), 0, 2)
        grid.addLayout(self._field("恒流倍率", self.spin_cc_rate), 1, 0)
        grid.addLayout(self._field("涓流倍率", self.spin_trickle_rate), 1, 1)
        grid.addLayout(self._field("涓流退出电压", self.spin_trickle_exit), 1, 2)
        grid.addLayout(self._field("CV 终止电流", self.spin_cv_term), 2, 0)
        grid.addLayout(self._field("目标电压", self.spin_target_v), 2, 1)
        grid.addLayout(self._field("最长充电时间", self.spin_max_min), 2, 2)
        grid.addLayout(self._field("温度上限", self.spin_temp_limit), 3, 0)

        manual_row = QHBoxLayout()
        manual_row.setSpacing(8)
        manual_row.addWidget(self.chk_manual_soc)
        manual_row.addWidget(self.spin_start_v)
        manual_row.addStretch(1)
        grid.addLayout(manual_row, 3, 1, 1, 2)

        self.btn_charge_start = QPushButton("开始充电")
        self.btn_charge_start.setProperty("variant", "primary")
        self.btn_charge_start.clicked.connect(self._on_charge_start)
        self.btn_charge_stop = QPushButton("停止充电")
        self.btn_charge_stop.setProperty("variant", "danger")
        self.btn_charge_stop.setEnabled(False)
        self.btn_charge_stop.clicked.connect(self._on_charge_stop)

        for col in range(3):
            grid.setColumnStretch(col, 1)
        fl.addLayout(grid)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        action_row.addWidget(self.btn_charge_start)
        action_row.addWidget(self.btn_charge_stop)
        action_row.addStretch(1)
        fl.addLayout(action_row)

        self.lbl_charge_summary = QLabel("")
        self.lbl_charge_summary.setStyleSheet(f"color:{theme.MUTED};font-size:10px;")
        fl.addWidget(self.lbl_charge_summary)
        lay.addWidget(form_card, 3)

        # ------------- 状态区 ------------- #
        status_card = QFrame()
        status_card.setProperty("card", "true")
        sl = QVBoxLayout(status_card)
        sl.setContentsMargins(16, 10, 16, 10)
        sl.setSpacing(6)

        self.lbl_charge_phase = QLabel("未充电")
        self.lbl_charge_phase.setFont(self._font(18, QFont.Weight.Bold))
        self.lbl_charge_phase.setStyleSheet(f"color:{theme.MUTED};")
        sl.addWidget(self.lbl_charge_phase)

        # 起始电压来源：明确写出是按“实测开路电压”算的，而不是充电时的端电压
        self.lbl_charge_origin = QLabel("起始电压：—")
        self.lbl_charge_origin.setWordWrap(True)
        self.lbl_charge_origin.setStyleSheet(f"color:{theme.MUTED};font-size:10px;")
        self.lbl_charge_origin.setToolTip(
            "开充瞬间把电流降到 0 保持约 2 秒，测得电池开路电压（OCV），\n"
            "它就是起始电压；内部再按 OCV 曲线换算电量用于计算。\n"
            "充电过程中只用库仑计积分，不再使用端电压。")
        sl.addWidget(self.lbl_charge_origin)

        self.bar_soc = QProgressBar()
        self.bar_soc.setRange(0, 100)
        self.bar_soc.setValue(0)
        self.bar_soc.setTextVisible(False)
        sl.addWidget(self.bar_soc)

        soc_row = QHBoxLayout()
        self.lbl_soc_value = QLabel("0.0 %")
        self.lbl_soc_value.setStyleSheet(f"color:{theme.TEXT};font-size:13px;")
        self.lbl_charged = QLabel("已充入 0 mAh")
        self.lbl_charged.setStyleSheet(f"color:{theme.MUTED};font-size:11px;")
        soc_row.addWidget(self.lbl_soc_value)
        soc_row.addStretch(1)
        soc_row.addWidget(self.lbl_charged)
        sl.addLayout(soc_row)

        time_row = QHBoxLayout()
        self.lbl_elapsed = QLabel("已用 --")
        self.lbl_eta = QLabel("预计剩余 --")
        for lbl in (self.lbl_elapsed, self.lbl_eta):
            lbl.setStyleSheet(f"color:{theme.MUTED};font-size:11px;")
        time_row.addWidget(self.lbl_elapsed)
        time_row.addStretch(1)
        time_row.addWidget(self.lbl_eta)
        sl.addLayout(time_row)

        self.lbl_live = QLabel("—")
        self.lbl_live.setStyleSheet(f"color:{theme.BLUE};font-size:11px;")
        sl.addWidget(self.lbl_live)

        self.lbl_charge_msg = QLabel("设置参数后点击“开始充电”")
        self.lbl_charge_msg.setWordWrap(True)
        self.lbl_charge_msg.setStyleSheet(f"color:{theme.MUTED};font-size:10px;")
        sl.addWidget(self.lbl_charge_msg)
        sl.addStretch(1)
        lay.addWidget(status_card, 2)

        # 绑定刷新
        for widget in (self.spin_cells, self.spin_capacity, self.spin_cc_rate,
                       self.spin_trickle_rate, self.spin_trickle_exit,
                       self.spin_cv_term, self.spin_target_v, self.spin_max_min,
                       self.spin_temp_limit, self.spin_start_v):
            widget.valueChanged.connect(self._on_charge_summary_changed)
        self.cb_chem.currentIndexChanged.connect(self._on_charge_summary_changed)
        self.spin_cells.valueChanged.connect(self._on_charge_summary_changed)
        self._on_charge_summary_changed()

        return page

    def _charge_profile(self) -> ChargeProfile:
        cells = self.spin_cells.value()
        chemistry = self.cb_chem.currentData() or "li_ion"
        target_v = self.spin_target_v.value()
        target_soc = soc_from_voltage(chemistry, target_v / max(1, cells))
        return ChargeProfile(
            cells=cells,
            chemistry=chemistry,
            capacity_mah=self.spin_capacity.value(),
            cc_rate_c=self.spin_cc_rate.value(),
            trickle_rate_c=self.spin_trickle_rate.value(),
            trickle_exit_v=self.spin_trickle_exit.value(),
            cv_terminal_c=self.spin_cv_term.value(),
            target_soc=target_soc,
            max_minutes=self.spin_max_min.value(),
            temp_limit=self.spin_temp_limit.value(),
        )

    def _on_charge_summary_changed(self, *_args) -> None:
        profile = self._charge_profile()
        self.lbl_charge_summary.setText(
            f"{profile.summary()}  ·  目标 {profile.target_voltage:.2f} V")
        self.spin_start_v.setVisible(self.chk_manual_soc.isChecked())
        self.spin_start_v.setEnabled(self.chk_manual_soc.isChecked())
        # 目标电压不能超过 CV（否则永远充不到），电芯数变化时同步上限
        self.spin_target_v.setMaximum(profile.cv_voltage_mv / 1000.0)
        if self.spin_target_v.value() > profile.cv_voltage_mv / 1000.0:
            self.spin_target_v.setValue(profile.cv_voltage_mv / 1000.0)

    def _on_manual_soc_toggled(self, checked: bool) -> None:
        self.spin_start_v.setVisible(checked)
        self.spin_start_v.setEnabled(checked)

    def _set_charge_form_enabled(self, enabled: bool) -> None:
        for widget in (self.spin_cells, self.cb_chem, self.spin_capacity,
                       self.spin_cc_rate, self.spin_trickle_rate,
                       self.spin_trickle_exit, self.spin_cv_term,
                       self.spin_target_v, self.spin_max_min,
                       self.spin_temp_limit, self.spin_start_v,
                       self.chk_manual_soc):
            widget.setEnabled(enabled)

    def _on_charge_start(self) -> None:
        if not self._connected:
            self.statusBar().showMessage("请先连接设备", 3000)
            return
        profile = self._charge_profile()
        self.charger.profile = profile
        self.btn_charge_start.setEnabled(False)
        self.btn_charge_stop.setEnabled(True)
        self._set_charge_form_enabled(False)

        if not self.chk_manual_soc.isChecked():
            # 输出本来就是关的 → 此刻端电压已经是静置 OCV，无需再探测
            if not self._output_on and self._last_info is not None:
                soc0 = self.charger.estimate_soc(self._last_info, zero_current=True)
                self.charger.ocv_drift_mv = 0
                self._log(f"输出未开启，直接用静置电压 {self._last_info.vout:.3f} V "
                          f"作为起始电压")
                self._begin_charge(soc0)
                return

            # 输出已经开着（带载），先把电流降到 0，让表面电荷回落
            self._charge_probing = True
            self._probe_v1_mv = 0
            self.lbl_charge_msg.setText("正在测量开路电压（电流降为 0，约 2 秒）…")
            self.statusBar().showMessage("测量电池开路电压中…", 5000)
            self.cmd_apply.emit(profile.cv_voltage_mv, 0, profile.ovp_mv,
                                profile.ocp_ma, 1)
            QTimer.singleShot(1000, self._charge_probe_mid)
            QTimer.singleShot(2400, self._charge_probe_done)
            return

        # 手动指定起始电压 → 换算成起始电量
        manual_v = self.spin_start_v.value()
        self._begin_charge(self.charger.profile.voltage_to_soc(manual_v))

    def _charge_probe_mid(self) -> None:
        if self._last_info is not None:
            self._probe_v1_mv = self._last_info.vout_mv

    def _charge_probe_done(self) -> None:
        """0 电流维持期结束：用此时端电压当作 OCV 估算起始电量。"""
        self._charge_probing = False
        info = self._last_info
        if info is None:
            self._begin_charge(None)
            return
        drift_mv = max(0, self._probe_v1_mv - info.vout_mv)
        soc0 = self.charger.estimate_soc(info, zero_current=True)
        self.charger.ocv_drift_mv = drift_mv
        note = ""
        if drift_mv > 15:
            note = f"，0.9 秒内仍回落 {drift_mv} mV（起始电压可能偏高）"
        self._log(f"开路电压实测 {info.vout:.3f} V "
                  f"（{info.vout / max(1, self.charger.profile.cells):.3f} V/节），"
                  f"作为起始电压{note}")
        self._begin_charge(soc0)

    def _begin_charge(self, initial_soc: float | None) -> None:
        profile = self.charger.profile
        # 目标电压必须明显高于起始电压，否则没有充电意义
        if initial_soc is not None and initial_soc >= profile.target_soc - 1.0:
            target_v = profile.target_voltage
            start_v = profile.soc_to_voltage(initial_soc)
            self.btn_charge_start.setEnabled(True)
            self.btn_charge_stop.setEnabled(False)
            self._set_charge_form_enabled(True)
            msg = (f"起始电压 {start_v:.2f} V 已达目标 {target_v:.2f} V，无需充电"
                   if start_v >= target_v - 0.02 else
                   f"起始电压 {start_v:.2f} V 已接近目标 {target_v:.2f} V，"
                   "请调高目标电压或确认电池状态")
            self.statusBar().showMessage(msg, 6000)
            self._log(msg)
            self.lbl_charge_msg.setText(msg)
            return

        info = self._last_info
        commands = self.charger.start(info, initial_soc)
        for command in commands:
            self._exec_charge_command(command)
        self._save_charge_settings()
        self._log(f"开始充电：{profile.summary()}，起始电压 {self.charger.ocv_v:.3f} V，"
                  f"目标 {profile.target_voltage:.2f} V，"
                  f"需充入约 {self.charger.required_ah * 1000:.0f} mAh")
        self._update_charge_status()

    def _on_charge_stop(self) -> None:
        self._charge_probing = False
        self.charger.stop()
        self.cmd_output.emit(0)
        self.btn_charge_start.setEnabled(True)
        self.btn_charge_stop.setEnabled(False)
        self._set_charge_form_enabled(True)
        self._log("已停止充电")
        self._update_charge_status()

    def _exec_charge_command(self, command: Command) -> None:
        if command.kind == "apply":
            self.cmd_apply.emit(command.vo_mv, command.io_ma,
                                command.ovp_mv, command.ocp_ma, command.state)
        elif command.kind == "output":
            self.cmd_output.emit(command.state)

    def _update_charge_status(self) -> None:
        readout = self.charger.readout()
        color = {
            Phase.TRICKLE: theme.BLUE,
            Phase.CC: theme.ACCENT,
            Phase.CV: theme.AMBER,
            Phase.DONE: theme.ACCENT,
            Phase.FAULT: theme.RED,
        }.get(readout.phase, theme.MUTED)
        self.lbl_charge_phase.setText(PHASE_LABELS[readout.phase])
        self.lbl_charge_phase.setStyleSheet(f"color:{color};")

        profile = self.charger.profile
        if readout.phase == Phase.IDLE and readout.ocv_v <= 0:
            self.lbl_charge_origin.setText("起始电压：—")
        else:
            per_cell = readout.ocv_v / max(1, profile.cells)
            drift = self.charger.ocv_drift_mv
            extra = f"，仍在回落 {drift:.0f} mV（可能偏高）" if drift > 15 else ""
            self.lbl_charge_origin.setText(
                f"起始电压 {readout.ocv_v:.3f} V（{per_cell:.3f} V/节，零电流实测{extra}）"
                f" → 目标 {profile.target_voltage:.2f} V")

        # 进度条 = 本次充电进度（已充入 / 需充入），不依赖电压
        self.bar_soc.setValue(int(round(readout.progress)))
        est_v = profile.soc_to_voltage(readout.soc)
        self.lbl_soc_value.setText(f"{est_v:.2f} V")
        self.lbl_soc_value.setToolTip("按已充入电量换算的电池静置电压（估算值）")
        self.lbl_charged.setText(
            f"已充入 {readout.charged_mah:.0f} / {readout.required_mah:.0f} mAh"
            f"（进度 {readout.progress:.0f}%）")
        self.lbl_elapsed.setText(f"已用 {self._fmt_duration(readout.elapsed_s)}")
        if readout.eta_s is not None and readout.eta_s > 0:
            hint = f"（{readout.eta_model}）" if readout.eta_model else ""
            self.lbl_eta.setText(f"预计剩余 {self._fmt_duration(readout.eta_s)}{hint}")
        elif readout.phase == Phase.DONE:
            self.lbl_eta.setText("已完成")
        else:
            self.lbl_eta.setText("预计剩余 --")
        self.lbl_live.setText(
            f"{readout.voltage:.2f} V · {readout.current:.3f} A · "
            f"{readout.temperature:.1f} ℃ · 均流 {readout.avg_current:.3f} A · "
            f"{readout.charged_wh:.2f} Wh")
        self.lbl_charge_msg.setText(readout.message or "")

    @staticmethod
    def _fmt_duration(seconds: float) -> str:
        seconds = max(0.0, seconds)
        if seconds < 60:
            return f"{seconds:.0f} 秒"
        minutes = seconds / 60
        if minutes < 60:
            return f"{minutes:.0f} 分钟"
        return f"{minutes // 60:.0f} 小时 {minutes % 60:.0f} 分"

    def _on_charge_finished(self) -> None:
        self._charge_probing = False
        self.btn_charge_start.setEnabled(True)
        self.btn_charge_stop.setEnabled(False)
        self._set_charge_form_enabled(True)
        readout = self.charger.readout()
        self._log(f"充电结束：{readout.message}")
        self.statusBar().showMessage(readout.message, 8000)
        if readout.learned_capacity_mah:
            # 用本次实测容量做半权重平滑，避免单次误差导致容量跳变
            old = self.spin_capacity.value()
            blended = 0.5 * old + 0.5 * readout.learned_capacity_mah
            if abs(blended - old) > 0.02 * old:
                self.spin_capacity.setValue(
                    min(self.spin_capacity.maximum(), blended))
                self._save_charge_settings()
                self._log(f"容量已校准：{old:.0f} → {blended:.0f} mAh"
                          f"（本次实测 {readout.learned_capacity_mah:.0f} mAh）")
        self._update_charge_status()

    # -- 充电参数持久化 -------------------------------------------------- #
    def _save_charge_settings(self) -> None:
        profile = self._charge_profile()
        data = {
            "cells": profile.cells, "chemistry": profile.chemistry,
            "capacity_mah": profile.capacity_mah,
            "cc_rate_c": profile.cc_rate_c,
            "trickle_rate_c": profile.trickle_rate_c,
            "trickle_exit_v": profile.trickle_exit_v,
            "cv_terminal_c": profile.cv_terminal_c,
            "target_v": profile.target_voltage,
            "max_minutes": profile.max_minutes,
            "temp_limit": profile.temp_limit,
            "manual_soc": self.chk_manual_soc.isChecked(),
            "start_v": self.spin_start_v.value(),
        }
        try:
            CHARGE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        except OSError as exc:
            self._log(f"充电参数保存失败：{exc}")

    def _load_charge_settings(self) -> None:
        if not CHARGE_FILE.exists():
            return
        try:
            data = json.loads(CHARGE_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        mapping = {
            "cells": self.spin_cells, "capacity_mah": self.spin_capacity,
            "cc_rate_c": self.spin_cc_rate, "trickle_rate_c": self.spin_trickle_rate,
            "trickle_exit_v": self.spin_trickle_exit,
            "cv_terminal_c": self.spin_cv_term, "target_v": self.spin_target_v,
            "max_minutes": self.spin_max_min, "temp_limit": self.spin_temp_limit,
            "start_v": self.spin_start_v,
        }
        for key, widget in mapping.items():
            if key in data:
                widget.setValue(data[key])
        if data.get("chemistry"):
            index = self.cb_chem.findData(data["chemistry"])
            if index >= 0:
                self.cb_chem.setCurrentIndex(index)
        if "manual_soc" in data:
            self.chk_manual_soc.setChecked(bool(data["manual_soc"]))
        elif "auto_soc" in data:          # 兼容旧配置
            self.chk_manual_soc.setChecked(not bool(data["auto_soc"]))
        # 读入的旧值可能不适用于当前电芯数/化学体系，做一次范围修正
        self._on_charge_summary_changed()
        cv_v = self._charge_profile().cv_voltage_mv / 1000.0
        if self.spin_target_v.value() > cv_v:
            self.spin_target_v.setValue(cv_v)

    # ------------------------------------------------------------------ #
    # 自动化任务
    # ------------------------------------------------------------------ #
    def _build_task_panel(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(2, 8, 2, 2)
        lay.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(10)
        self.chk_task_enable = QCheckBox("启用自动化任务")
        self.chk_task_enable.toggled.connect(self._on_task_enable_toggled)
        hint = QLabel("事件任务：实时数据满足条件时执行动作；定时任务：按系统时间调度")
        hint.setStyleSheet(f"color:{theme.MUTED};font-size:11px;")
        head.addWidget(self.chk_task_enable)
        head.addWidget(hint, 1)
        lay.addLayout(head)

        form = QFrame()
        form.setProperty("card", "true")
        fl = QVBoxLayout(form)
        fl.setContentsMargins(12, 10, 12, 10)
        fl.setSpacing(6)

        row1 = QHBoxLayout()
        row1.setSpacing(10)
        self.cb_task_kind = QComboBox()
        self.cb_task_kind.addItem("事件任务", "event")
        self.cb_task_kind.addItem("定时任务", "timed")
        self.cb_task_kind.currentIndexChanged.connect(self._on_task_kind_changed)
        self.cb_task_action = QComboBox()
        for key, label in ACTION_LABELS.items():
            self.cb_task_action.addItem(label, key)
        self.cb_task_action.currentIndexChanged.connect(self._on_task_action_changed)
        row1.addWidget(self.cb_task_kind, 1)
        row1.addWidget(self.cb_task_action, 1)
        fl.addLayout(row1)

        self.task_stack = QStackedWidget()

        # 事件页
        event_page = QWidget()
        el = QHBoxLayout(event_page)
        el.setContentsMargins(0, 0, 0, 0)
        el.setSpacing(10)
        self.cb_task_source = QComboBox()
        for key, label in SOURCE_LABELS.items():
            self.cb_task_source.addItem(label, key)
        self.cb_task_source.currentIndexChanged.connect(self._on_task_source_changed)
        self.cb_task_cmp = QComboBox()
        for key, label in COMPARATOR_LABELS.items():
            self.cb_task_cmp.addItem(label, key)
        self.spin_task_threshold = QDoubleSpinBox()
        self.spin_task_threshold.setRange(0.0, 300.0)
        self.spin_task_threshold.setDecimals(3)
        self.spin_task_threshold.setValue(0.05)
        self.spin_task_threshold.setSuffix(" A")
        el.addWidget(self.cb_task_source)
        el.addWidget(self.cb_task_cmp)
        el.addWidget(self.spin_task_threshold, 1)
        self.task_stack.addWidget(event_page)

        # 定时页
        timed_page = QWidget()
        tl = QHBoxLayout(timed_page)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(10)
        self.cb_task_mode = QComboBox()
        self.cb_task_mode.addItem("延时执行", "delay")
        self.cb_task_mode.addItem("每天定时", "at")
        self.cb_task_mode.currentIndexChanged.connect(self._on_task_mode_changed)
        self.spin_task_delay = QDoubleSpinBox()
        self.spin_task_delay.setRange(1.0, 86400.0)
        self.spin_task_delay.setDecimals(0)
        self.spin_task_delay.setValue(60.0)
        self.spin_task_delay.setSuffix(" 秒")
        self.spin_task_delay.setStyleSheet("min-width: 110px;")
        self.time_task = QTimeEdit(QTime(12, 0))
        self.time_task.setDisplayFormat("HH:mm:ss")
        self.time_task.setStyleSheet("min-width: 100px;")
        tl.addWidget(self.cb_task_mode)
        tl.addWidget(self.spin_task_delay, 1)
        tl.addWidget(self.time_task, 1)
        self.task_stack.addWidget(timed_page)
        self.task_stack.setCurrentIndex(0)
        fl.addWidget(self.task_stack)

        row3 = QHBoxLayout()
        row3.setSpacing(10)
        self.lbl_task_value = QLabel("动作参数")
        self.lbl_task_value.setStyleSheet(f"color:{theme.MUTED};font-size:11px;")
        self.spin_task_value = QDoubleSpinBox()
        self.spin_task_value.setRange(0.0, 30.0)
        self.spin_task_value.setDecimals(2)
        self.spin_task_value.setValue(0.50)
        self.spin_task_value.setStyleSheet("min-width: 96px;")
        self.chk_task_once = QCheckBox("触发一次后停用")
        self.chk_task_once.setChecked(True)
        btn_add = QPushButton("添加任务")
        btn_add.setProperty("variant", "primary")
        btn_add.clicked.connect(self._on_add_task)
        row3.addWidget(self.lbl_task_value)
        row3.addWidget(self.spin_task_value)
        row3.addWidget(self.chk_task_once)
        row3.addStretch(1)
        row3.addWidget(btn_add)
        fl.addLayout(row3)
        lay.addWidget(form)

        self.task_table = QTableWidget(0, 5)
        self.task_table.setHorizontalHeaderLabels(
            ["启用", "类型", "触发条件", "执行动作", "最近触发"])
        self.task_table.verticalHeader().setVisible(False)
        header = self.task_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        for col in (2, 3, 4):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        self.task_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.task_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.task_table.setAlternatingRowColors(True)
        self.task_table.setShowGrid(False)
        self.task_table.verticalHeader().setDefaultSectionSize(22)
        self.task_table.setStyleSheet("font-size: 11px;")
        self.task_table.itemChanged.connect(self._on_task_item_changed)
        lay.addWidget(self.task_table, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_del = QPushButton("删除选中")
        btn_del.setProperty("variant", "ghost")
        btn_del.clicked.connect(self._on_delete_tasks)
        btn_clear = QPushButton("清空全部")
        btn_clear.setProperty("variant", "ghost")
        btn_clear.clicked.connect(self._on_clear_tasks)
        btn_row.addWidget(btn_del)
        btn_row.addWidget(btn_clear)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)

        self._on_task_action_changed(0)
        self._on_task_mode_changed(0)
        return page

    def _on_task_kind_changed(self, _index: int) -> None:
        is_timed = self.cb_task_kind.currentData() == "timed"
        self.task_stack.setCurrentIndex(1 if is_timed else 0)

    def _on_task_mode_changed(self, _index: int) -> None:
        is_delay = self.cb_task_mode.currentData() == "delay"
        self.spin_task_delay.setVisible(is_delay)
        self.time_task.setVisible(not is_delay)

    def _on_task_source_changed(self, _index: int) -> None:
        source = self.cb_task_source.currentData()
        if source == "current":
            self.spin_task_threshold.setRange(0.0, 5.0)
            self.spin_task_threshold.setSuffix(" A")
            self.spin_task_threshold.setValue(0.05)
        elif source == "voltage":
            self.spin_task_threshold.setRange(0.0, 30.0)
            self.spin_task_threshold.setSuffix(" V")
            self.spin_task_threshold.setValue(8.30)
        elif source == "power":
            self.spin_task_threshold.setRange(0.0, 100.0)
            self.spin_task_threshold.setSuffix(" W")
            self.spin_task_threshold.setValue(5.0)
        else:
            self.spin_task_threshold.setRange(0.0, 120.0)
            self.spin_task_threshold.setSuffix(" ℃")
            self.spin_task_threshold.setValue(60.0)

    def _on_task_action_changed(self, _index: int) -> None:
        action = self.cb_task_action.currentData()
        unit = ACTION_UNITS.get(action)
        visible = unit is not None
        self.lbl_task_value.setVisible(visible)
        self.spin_task_value.setVisible(visible)
        if unit == "A":
            self.spin_task_value.setRange(0.0, 5.0)
            self.spin_task_value.setDecimals(3)
            self.spin_task_value.setSuffix(" A")
            self.spin_task_value.setValue(0.5)
        elif unit == "V":
            self.spin_task_value.setRange(0.0, 30.0)
            self.spin_task_value.setDecimals(2)
            self.spin_task_value.setSuffix(" V")
            self.spin_task_value.setValue(0.5)

    def _on_task_enable_toggled(self, checked: bool) -> None:
        self.engine.set_enabled(checked)
        self._save_tasks()
        self._log("自动化任务已启用" if checked else "自动化任务已停用")

    def _on_add_task(self) -> None:
        kind = self.cb_task_kind.currentData()
        task = Task(
            kind=kind,
            action=self.cb_task_action.currentData(),
            value=self.spin_task_value.value(),
            source=self.cb_task_source.currentData(),
            comparator=self.cb_task_cmp.currentData(),
            threshold=self.spin_task_threshold.value(),
            mode=self.cb_task_mode.currentData(),
            at_time=self.time_task.time().toString("HH:mm:ss"),
            delay_s=self.spin_task_delay.value(),
            once=self.chk_task_once.isChecked(),
            enabled=True,
        )
        self.engine.add_task(task)
        self._log(f"新增任务：{task.describe_condition()} → {task.describe_action()}")
        self._save_tasks()

    def _refresh_task_table(self) -> None:
        table = self.task_table
        table.blockSignals(True)
        table.setRowCount(len(self.engine.tasks))
        for row, task in enumerate(self.engine.tasks):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
                           | Qt.ItemFlag.ItemIsSelectable)
            check.setCheckState(Qt.CheckState.Checked if task.enabled
                                else Qt.CheckState.Unchecked)
            check.setData(Qt.ItemDataRole.UserRole, task.id)
            table.setItem(row, 0, check)
            texts = [KIND_LABELS.get(task.kind, task.kind),
                     task.describe_condition(), task.describe_action(),
                     task.last_fired or "—"]
            for col, text in enumerate(texts, start=1):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                table.setItem(row, col, item)
        table.blockSignals(False)

    def _on_task_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        task_id = item.data(Qt.ItemDataRole.UserRole)
        for task in self.engine.tasks:
            if task.id == task_id:
                task.enabled = item.checkState() == Qt.CheckState.Checked
                if task.enabled and task.kind == "timed":
                    self.engine._schedule(task)
                break
        self._save_tasks()

    def _on_delete_tasks(self) -> None:
        rows = {index.row() for index in self.task_table.selectedIndexes()}
        for row in rows:
            item = self.task_table.item(row, 0)
            if item is not None:
                self.engine.remove_task(item.data(Qt.ItemDataRole.UserRole))
        self._save_tasks()
        if rows:
            self._log(f"已删除 {len(rows)} 个任务")

    def _on_clear_tasks(self) -> None:
        self.engine.clear()
        self._save_tasks()
        self._log("已清空全部任务")

    def _save_tasks(self) -> None:
        try:
            self.engine.save(TASKS_FILE)
        except OSError as exc:
            self._log(f"任务保存失败：{exc}")

    # -- 任务执行 -------------------------------------------------------- #
    def _on_task_fired(self, task: Task) -> None:
        self._log(f"任务触发：{task.describe_condition()} → {task.describe_action()}")
        if not self._connected:
            self._log("  设备未连接，动作未执行")
        else:
            self._execute_task(task)
        self._save_tasks()
        self._refresh_task_table()

    def _execute_task(self, task: Task) -> None:
        preset = self._preset
        cur_vo = preset.vo_mv if preset else int(round(self.spin_voltage.value() * 1000))
        cur_io = preset.io_ma if preset else int(round(self.spin_current.value() * 1000))
        ovp = preset.ovp_mv if preset else int(round(self.spin_ovp.value() * 1000))
        ocp = preset.ocp_ma if preset else int(round(self.spin_ocp.value() * 1000))
        state = 1 if self._output_on else 0

        if task.action == "output_off":
            self.cmd_output.emit(0)
            return
        if task.action == "output_on":
            self.cmd_output.emit(1)
            return

        if task.action == "set_voltage":
            new_vo = int(round(task.value * 1000))
        elif task.action == "lower_voltage":
            new_vo = cur_vo - int(round(task.value * 1000))
        elif task.action == "raise_voltage":
            new_vo = cur_vo + int(round(task.value * 1000))
        else:  # set_current
            new_io = max(0, min(5000, int(round(task.value * 1000))))
            self.cmd_apply.emit(cur_vo, new_io, ovp, ocp, state)
            return

        new_vo = max(0, min(30000, new_vo))
        self.cmd_apply.emit(new_vo, cur_io, ovp, ocp, state)


    @staticmethod
    def _font(size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
        font = QFont("Microsoft YaHei UI", size)
        font.setWeight(weight)
        return font

    def _on_metric_changed(self, index: int) -> None:
        self.chart.set_metric("voltage" if index == 0 else "current")

    @staticmethod
    def _scrollable(inner: QWidget) -> QScrollArea:
        """窄窗口下自动出现滚动条，避免控件被压缩。"""
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
            "QScrollArea > QWidget > QWidget{background:transparent;}")
        area.setWidget(inner)
        return area

    def _card(self, title: str) -> QFrame:
        frame = QFrame()
        frame.setProperty("card", "true")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(15, 11, 15, 11)
        lay.setSpacing(7)
        caption = QLabel(title)
        caption.setProperty("section", "true")
        lay.addWidget(caption)
        return frame

    def _log(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{stamp}] {text}")

    # ------------------------------------------------------------------ #
    # 设备枚举 / 连接
    # ------------------------------------------------------------------ #
    def _refresh_devices(self) -> None:
        self.combo_devices.clear()
        try:
            devices = list_dp100()
        except Exception as exc:  # pragma: no cover
            devices = []
            self._log(f"扫描设备失败：{exc}")
        for dev in devices:
            self.combo_devices.addItem(f"{dev['name']}  ·  USBD", dev["path"])
        if not devices:
            self.combo_devices.addItem("未检测到 DP100（检查 USBD 模式）", None)

    def _try_auto_connect(self) -> None:
        if self.combo_devices.currentData():
            self._on_connect_clicked()

    def _on_connect_clicked(self) -> None:
        if self._connected:
            self.cmd_close.emit()
            return
        path = self.combo_devices.currentData()
        if not path:
            self.statusBar().showMessage("没有可连接的设备")
            self._log("没有可连接的设备")
            return
        self.btn_connect.setEnabled(False)
        self.btn_connect.setText("连接中…")
        self.cmd_open.emit(path)

    def _on_connected(self, data: dict) -> None:
        self._connected = True
        info: DeviceInfo = data["info"]
        self.chip_state.setText("已连接")
        self.chip_state.setStyleSheet(
            f"color:{theme.ACCENT};background:#0E2A21;border:1px solid #235D4A;"
            "border-radius:9px;padding:3px 9px;font-size:11px;")
        self.chip_path.setText(info.dev_type or "ATK-DP100")
        self.chip_path.setToolTip(
            f"{info.describe()}\nSN {info.serial or '—'}\n{data['path']}")
        self.btn_connect.setToolTip(f"{info.describe()}\nSN {info.serial or '—'}")
        self.btn_connect.setText("断开连接")
        self.btn_connect.setEnabled(True)
        self.btn_connect.setProperty("variant", "danger")
        theme.repolish(self.btn_connect)
        self.combo_devices.setEnabled(False)
        self._set_controls_enabled(True)
        self.statusBar().showMessage("已连接")
        self._log(f"连接成功：{data['path']}")
        self.chart.clear()

    def _on_disconnected(self) -> None:
        self._connected = False
        self.chip_state.setText("未连接")
        self.chip_state.setStyleSheet("")
        self.chip_state.setProperty("chip", "true")
        theme.repolish(self.chip_state)
        self.chip_path.setText("USB 未连接")
        self.btn_connect.setText("连接设备")
        self.btn_connect.setEnabled(True)
        self.btn_connect.setProperty("variant", "primary")
        theme.repolish(self.btn_connect)
        self.combo_devices.setEnabled(True)
        self._set_controls_enabled(False)
        self.btn_power.setText("⏻  输出已关闭")
        self.btn_power.setProperty("on", "false")
        theme.repolish(self.btn_power)
        self._preset = None
        self._output_on = False
        self.statusBar().showMessage("已断开")
        self._log("连接已断开")
        self._charge_probing = False
        if self.charger.active:
            self.charger.stop("设备断开，充电中止")
            self._on_charge_finished()
        self._refresh_devices()

    def _set_controls_enabled(self, enabled: bool) -> None:
        for widget in (self.btn_apply, self.btn_prot, self.btn_power,
                       self.spin_voltage, self.spin_current,
                       self.spin_ovp, self.spin_ocp):
            widget.setEnabled(enabled)
        self.btn_read_presets.setEnabled(enabled)
        self.btn_load_preset.setEnabled(enabled)

    # ------------------------------------------------------------------ #
    # 实时数据
    # ------------------------------------------------------------------ #
    def _on_status(self, info: BasicInfo) -> None:
        self._last_info = info
        vo_set = self._preset.vo_mv if self._preset else None
        mode = info.guessed_mode(vo_set)
        self.card_v.set_value(f"{info.vout:.2f}", f"设定 "
                              f"{self.spin_voltage.value():.2f} V  ·  {mode}")
        self.card_i.set_value(f"{info.iout:.3f}", f"设定 {self.spin_current.value():.3f} A")
        self.card_p.set_value(f"{info.power:.1f}", f"输入 {info.vin:.1f} V")
        self.card_t.set_value(f"{info.temperature:.1f}",
                              f"内部 5V {info.aux5v_mv / 1000:.2f} V")
        self.card_v.set_badge("" if mode == "输出关闭" else mode, theme.ACCENT)
        self.chart.add_sample(time.monotonic() - self._t0, info.vout, info.iout)
        self._sync_output_state(info.output_on, mode)

        now = time.monotonic()
        dt = now - self._last_status_time
        self._last_status_time = now
        self.engine.set_status(info)
        self._tick_charger(info, dt)
        self._check_auto_cutoff(info)
        self._write_csv_row(info)

    def _tick_charger(self, info: BasicInfo, dt: float) -> None:
        if not self.charger.active:
            return
        for command in self.charger.update(info, dt):
            self._exec_charge_command(command)
        self._update_charge_status()
        if not self.charger.active:
            self._on_charge_finished()

    def _sync_output_state(self, is_on: bool, mode: str) -> None:
        """让按钮状态跟随设备真实输出状态（每 200ms 校准一次，防止界面与实际不一致）。"""
        if is_on == self._output_on:
            return
        self._output_on = is_on
        self.btn_power.setText("⏻  输出已开启" if is_on else "⏻  输出已关闭")
        self.btn_power.setProperty("on", "true" if is_on else "false")
        theme.repolish(self.btn_power)
        self._log(f"输出状态变为：{mode}")

    def _on_preset(self, preset: BasicSet) -> None:
        self._preset = preset
        self._output_on = bool(preset.state)
        self.btn_power.setText("⏻  输出已开启" if preset.state else "⏻  输出已关闭")
        self.btn_power.setProperty("on", "true" if preset.state else "false")
        theme.repolish(self.btn_power)
        # 反映设备真实设定值（不触发任何写入）
        for spin, value in ((self.spin_voltage, preset.vo_mv / 1000.0),
                            (self.spin_current, preset.io_ma / 1000.0),
                            (self.spin_ovp, preset.ovp_mv / 1000.0),
                            (self.spin_ocp, preset.ocp_ma / 1000.0)):
            if abs(spin.value() - value) > 1e-6 and not spin.hasFocus():
                spin.setValue(value)

    def _on_presets(self, presets: list) -> None:
        for row, preset in enumerate(presets):
            if preset is None:
                for col in range(1, 5):
                    self.table.item(row, col).setText("--")
                continue
            self.table.item(row, 1).setText(f"{preset.vo_mv / 1000:.2f}")
            self.table.item(row, 2).setText(f"{preset.io_ma / 1000:.3f}")
            self.table.item(row, 3).setText(f"{preset.ovp_mv / 1000:.2f}")
            self.table.item(row, 4).setText(f"{preset.ocp_ma / 1000:.2f}")
        self._log("已读取设备预设组")

    def _on_error(self, message: str) -> None:
        self.statusBar().showMessage(message, 5000)
        self._log(message)
        if "连接失败" in message or "设备已断开" in message:
            if self._connected:
                self._on_disconnected()

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #
    def _on_power_clicked(self) -> None:
        if not self._connected:
            return
        self.cmd_output.emit(0 if self._output_on else 1)

    def _on_apply_clicked(self) -> None:
        if not self._connected:
            return
        self.cmd_apply.emit(
            int(round(self.spin_voltage.value() * 1000)),
            int(round(self.spin_current.value() * 1000)),
            int(round(self.spin_ovp.value() * 1000)),
            int(round(self.spin_ocp.value() * 1000)),
            1 if self._output_on else 0,
        )

    def _on_preset_row_activated(self, row: int, _col: int) -> None:
        self.table.selectRow(row)
        self._on_load_preset()

    def _on_load_preset(self) -> None:
        rows = {idx.row() for idx in self.table.selectedIndexes()}
        if not rows:
            self.statusBar().showMessage("请先选择一行预设", 3000)
            return
        row = sorted(rows)[0]
        values = [self.table.item(row, col).text() for col in range(1, 5)]
        if values[0] == "--":
            self.statusBar().showMessage("该预设为空", 3000)
            return
        self.spin_voltage.setValue(float(values[0]))
        self.spin_current.setValue(float(values[1]))
        self.spin_ovp.setValue(float(values[2]))
        self.spin_ocp.setValue(float(values[3]))
        self.statusBar().showMessage(f"已载入 P{row} 的参数", 3000)

    def _on_auto_toggled(self, checked: bool) -> None:
        if checked:
            self.lbl_auto.setText(
                f"启用：当电压 ≥ {self.spin_cut_v.value():.2f} V 且电流 ≤ "
                f"{self.spin_cut_i.value():.3f} A（连续 3 次）时关闭输出")
        else:
            self.lbl_auto.setText("未启用")
            self._monitor_hits = 0

    def _check_auto_cutoff(self, info: BasicInfo) -> None:
        if self.charger.active:
            return
        if not self.chk_auto.isChecked() or not self._connected:
            return
        target = int(round(self.spin_cut_v.value() * 1000))
        cutoff = int(round(self.spin_cut_i.value() * 1000))
        if info.vout_mv >= target - 100 and info.iout_ma <= cutoff and info.iout_ma > 0:
            self._monitor_hits += 1
            if self._monitor_hits >= 3:
                self._monitor_hits = 0
                self._log("达到截止条件，自动关闭输出")
                self.statusBar().showMessage("自动关断已触发，输出已关闭", 5000)
                self.chk_auto.setChecked(False)
                self.cmd_output.emit(0)
        else:
            self._monitor_hits = 0

    # ------------------------------------------------------------------ #
    # CSV 记录
    # ------------------------------------------------------------------ #
    def _toggle_record(self) -> None:
        if self._csv_file is not None:
            self._csv_file.close()
            self._csv_file = None
            self._csv_writer = None
            self.btn_record.setText("开始记录 CSV")
            self.btn_record.setProperty("variant", "ghost")
            theme.repolish(self.btn_record)
            self._log("CSV 记录已停止")
            return
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        name = datetime.now().strftime("dp100_%Y%m%d_%H%M%S.csv")
        path = DATA_DIR / name
        self._csv_file = open(path, "w", newline="", encoding="utf-8-sig")
        self._csv_writer = csv.writer(self._csv_file)
        self._csv_writer.writerow(["time", "vin_V", "vout_V", "iout_A",
                                   "power_W", "temp_C", "mode"])
        self.btn_record.setText("■ 停止记录")
        self.btn_record.setProperty("variant", "primary")
        theme.repolish(self.btn_record)
        self._log(f"开始记录：{path}")

    def _write_csv_row(self, info: BasicInfo) -> None:
        if self._csv_writer is None:
            return
        self._csv_writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            f"{info.vin:.2f}", f"{info.vout:.3f}", f"{info.iout:.3f}",
            f"{info.power:.3f}", f"{info.temperature:.1f}",
            info.guessed_mode(self._preset.vo_mv if self._preset else None),
        ])
        self._csv_file.flush()

    def _open_data_dir(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(str(DATA_DIR))  # noqa: S606

    # ------------------------------------------------------------------ #
    # worker 线程
    # ------------------------------------------------------------------ #
    def _setup_worker(self) -> None:
        self._thread = QThread(self)
        self._worker = DeviceWorker()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.init)

        self._worker.sig_connected.connect(self._on_connected)
        self._worker.sig_disconnected.connect(self._on_disconnected)
        self._worker.sig_status.connect(self._on_status)
        self._worker.sig_preset.connect(self._on_preset)
        self._worker.sig_presets.connect(self._on_presets)
        self._worker.sig_error.connect(self._on_error)
        self._worker.sig_log.connect(self._log)

        self.cmd_open.connect(self._worker.open_device)
        self.cmd_close.connect(self._worker.close_device)
        self.cmd_apply.connect(self._worker.apply_settings)
        self.cmd_output.connect(self._worker.set_output)
        self.cmd_query_presets.connect(self._worker.query_all_presets)
        self._thread.start()

    # ------------------------------------------------------------------ #
    # 演示模式
    # ------------------------------------------------------------------ #
    def _start_demo(self) -> None:
        self.chip_state.setText("演示模式")
        self.chip_path.setText("DEMO")
        self._connected = True
        self._set_controls_enabled(True)
        self.btn_power.setText("⏻  输出已开启")
        self.btn_power.setProperty("on", "true")
        theme.repolish(self.btn_power)
        self._output_on = True
        self._preset = BasicSet(0, 1, 8400, 1000, 30500, 5050)
        self._log("演示模式：数据为模拟值")
        demo_presets = [
            (8.40, 1.000, 8.70, 1.20),
            (5.00, 1.000, 5.30, 1.20),
            (12.00, 0.500, 12.50, 0.60),
            (3.30, 1.000, 3.60, 1.20),
            (7.40, 0.500, 7.80, 0.60),
            (9.00, 0.250, 9.30, 0.30),
            (15.00, 2.000, 15.50, 2.20),
            (20.00, 3.000, 20.50, 3.20),
            (6.00, 1.500, 6.40, 1.80),
            (24.00, 1.000, 24.50, 1.20),
        ]
        presets = [BasicSet(i, 1, int(v * 1000), int(a * 1000),
                            int(ovp * 1000), int(ocp * 1000))
                   for i, (v, a, ovp, ocp) in enumerate(demo_presets)]
        self._on_presets(presets)
        self._demo_timer = QTimer(self)
        self._demo_timer.setInterval(200)
        self._demo_timer.timeout.connect(self._demo_tick)
        self._demo_timer.start()

    def _demo_tick(self) -> None:
        import math

        t = self._demo_t % 90.0
        self._demo_t += 0.2
        vcell = 3.62 + (4.17 - 3.62) * (1 - math.exp(-t / 45.0))
        v = vcell * 2
        i = 0.98 if v < 8.28 else max(0.05, 0.98 * (8.42 - v) / 0.14)
        i += math.sin(t * 3.0) * 0.004
        v += math.sin(t * 1.7) * 0.004
        info = BasicInfo(int(19.9 * 1000), int(v * 1000), int(max(i, 0) * 1000),
                         int(19.33 * 1000), int(28.6 * 10), int(29.4 * 10),
                         5077, 0, 0)
        self._on_status(info)

    # ------------------------------------------------------------------ #
    # 收尾
    # ------------------------------------------------------------------ #
    def closeEvent(self, event) -> None:  # noqa: N802
        # 关闭流程绝不能阻塞 UI：任何一步失败都要继续走完并让窗口关掉
        self._closing = True
        try:
            if self._csv_file is not None:
                self._csv_file.close()
                self._csv_file = None
        except Exception:
            pass
        try:
            self._save_charge_settings()
        except Exception:
            pass
        try:
            if self.charger.active:
                self.charger.stop("程序退出")
                self.cmd_output.emit(0)
        except Exception:
            pass

        thread = getattr(self, "_thread", None)
        worker = getattr(self, "_worker", None)
        if thread is not None:
            # 先让 worker 自己收尾（非阻塞投递，不等待）
            if worker is not None:
                try:
                    QMetaObject.invokeMethod(worker, "close_device",
                                             Qt.ConnectionType.QueuedConnection)
                except Exception:
                    pass
            thread.quit()
            if not thread.wait(1200):
                # 兜底：仍卡在设备 I/O 上就强制结束该线程
                try:
                    thread.terminate()
                except Exception:
                    pass
                thread.wait(600)
            if worker is not None:
                try:
                    worker.deleteLater()
                except Exception:
                    pass
            self._worker = None
            self._thread = None

        event.accept()
        super().closeEvent(event)
