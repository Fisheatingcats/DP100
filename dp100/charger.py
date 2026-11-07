"""电池充电模式：涓流 / 恒流 / 恒压 三段式 + 库仑计 + 剩余时间估算。

设备侧只需要两种设置：
  * TRAICKLE 阶段：CV 目标电压 + 小电流限流
  * CC / CV 阶段：CV 目标电压 + 恒流限流
设备本身就会自动在 CC 与 CV 之间切换，软件负责阶段管理、保护与收尾。
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum

from .protocol import BasicInfo

# 化学体系：每节恒压值
CHEMISTRIES: dict[str, dict] = {
    "li_ion": {"label": "锂离子 / 锂聚合物 (4.20 V)", "cv": 4.20},
    "li_ion_hv": {"label": "高压锂电 (4.35 V)", "cv": 4.35},
    "lifepo4": {"label": "磷酸铁锂 (3.65 V)", "cv": 3.65},
}

# 静置开路电压 -> 电量（每节）。典型三元/钴酸锂电芯的 OCV 曲线，
# 中段很平，所以只能给出近似值（±10~15%），真正的进度靠库仑计。
_OCV_LI_ION = [
    (0.0, 3.00), (3.0, 3.30), (5.0, 3.40), (10.0, 3.50), (20.0, 3.58),
    (30.0, 3.65), (40.0, 3.72), (50.0, 3.78), (60.0, 3.84), (70.0, 3.90),
    (80.0, 3.96), (90.0, 4.05), (95.0, 4.12), (100.0, 4.20),
]
_OCV_LIFEPO4 = [
    (0.0, 2.80), (10.0, 3.10), (20.0, 3.20), (30.0, 3.25), (40.0, 3.28),
    (50.0, 3.30), (60.0, 3.32), (70.0, 3.34), (80.0, 3.36), (90.0, 3.40),
    (100.0, 3.65),
]


def _table(chemistry: str) -> list[tuple[float, float]]:
    if chemistry == "lifepo4":
        return _OCV_LIFEPO4
    if chemistry == "li_ion_hv":
        scale = 4.35 / 4.20
        return [(soc, v * scale) for soc, v in _OCV_LI_ION]
    return _OCV_LI_ION


def soc_from_voltage(chemistry: str, per_cell_v: float) -> float:
    """由静置电压估算电量百分比。"""
    table = _table(chemistry)
    if per_cell_v <= table[0][1]:
        return 0.0
    if per_cell_v >= table[-1][1]:
        return 100.0
    for (s0, v0), (s1, v1) in zip(table, table[1:]):
        if v0 <= per_cell_v <= v1:
            if v1 == v0:
                return s1
            return s0 + (s1 - s0) * (per_cell_v - v0) / (v1 - v0)
    return 0.0


def voltage_from_soc(chemistry: str, soc: float) -> float:
    """由电量百分比反查单节静置电压（SOC→OCV 曲线的逆）。"""
    table = _table(chemistry)
    soc = max(0.0, min(100.0, soc))
    if soc <= table[0][0]:
        return table[0][1]
    if soc >= table[-1][0]:
        return table[-1][1]
    for (s0, v0), (s1, v1) in zip(table, table[1:]):
        if s0 <= soc <= s1:
            if s1 == s0:
                return v1
            return v0 + (v1 - v0) * (soc - s0) / (s1 - s0)
    return table[-1][1]


@dataclass
class ChargeProfile:
    """充电参数。"""

    cells: int = 2
    chemistry: str = "li_ion"
    capacity_mah: float = 3600.0

    cc_rate_c: float = 0.5            # 恒流倍率
    trickle_rate_c: float = 0.1       # 涓流倍率
    trickle_exit_v: float = 3.00      # 每节电压低于此值时先涓流
    cv_terminal_c: float = 0.05       # 恒压阶段终止电流倍率
    target_soc: float = 100.0

    max_minutes: float = 480.0        # 最长充电时间
    temp_limit: float = 45.0          # 温度上限
    ovp_margin_mv: int = 200          # OVP = CV + margin

    # -- 派生参数 ------------------------------------------------------ #
    @property
    def cv_per_cell(self) -> float:
        return CHEMISTRIES[self.chemistry]["cv"]

    @property
    def cv_voltage_mv(self) -> int:
        return int(round(self.cells * self.cv_per_cell * 1000))

    @property
    def cc_current_ma(self) -> int:
        return max(1, int(round(self.capacity_mah * self.cc_rate_c)))

    @property
    def trickle_current_ma(self) -> int:
        return max(1, int(round(self.capacity_mah * self.trickle_rate_c)))

    @property
    def terminal_current_ma(self) -> int:
        return max(1, int(round(self.capacity_mah * self.cv_terminal_c)))

    @property
    def trickle_exit_mv(self) -> int:
        return int(round(self.cells * self.trickle_exit_v * 1000))

    @property
    def ovp_mv(self) -> int:
        return self.cv_voltage_mv + self.ovp_margin_mv

    @property
    def ocp_ma(self) -> int:
        return int(round(self.cc_current_ma * 1.3)) + 50

    def summary(self) -> str:
        return (f"CV {self.cv_voltage_mv / 1000:.2f} V  ·  "
                f"CC {self.cc_current_ma / 1000:.3f} A  ·  "
                f"涓流 {self.trickle_current_ma / 1000:.3f} A  ·  "
                f"终止 {self.terminal_current_ma / 1000:.3f} A")

    # -- 电压 / 电量换算 ------------------------------------------------ #
    def soc_to_voltage(self, soc: float) -> float:
        """电量百分比 → 整包静置电压（V）。"""
        return self.cells * voltage_from_soc(self.chemistry, soc)

    def voltage_to_soc(self, voltage: float) -> float:
        """整包静置电压（V）→ 电量百分比。"""
        return soc_from_voltage(self.chemistry, voltage / max(1, self.cells))

    @property
    def target_voltage(self) -> float:
        return self.soc_to_voltage(self.target_soc)


class Phase(str, Enum):
    IDLE = "idle"
    TRICKLE = "trickle"
    CC = "cc"
    CV = "cv"
    DONE = "done"
    FAULT = "fault"


PHASE_LABELS = {
    Phase.IDLE: "未充电",
    Phase.TRICKLE: "涓流预充",
    Phase.CC: "恒流充电",
    Phase.CV: "恒压充电",
    Phase.DONE: "充电完成",
    Phase.FAULT: "保护停止",
}


@dataclass
class Command:
    """给设备的动作指令。"""

    kind: str                       # apply / output
    vo_mv: int = 0
    io_ma: int = 0
    ovp_mv: int = 0
    ocp_ma: int = 0
    state: int = 0


@dataclass
class ChargeReadout:
    phase: Phase = Phase.IDLE
    soc: float = 0.0                 # 当前电量（库仑计，起点由实测 OCV 决定）
    start_soc: float = 0.0
    charged_mah: float = 0.0         # 本次已充入
    required_mah: float = 0.0        # 本次需要充入（容量 × 目标-起点）
    progress: float = 0.0            # 本次充电进度 0-100
    elapsed_s: float = 0.0
    eta_s: float | None = None
    eta_model: str = ""              # 估算依据：恒流直算 / 恒压指数拟合
    avg_current: float = 0.0
    charged_wh: float = 0.0
    ocv_v: float = 0.0               # 开充时实测的开路电压
    message: str = ""
    voltage: float = 0.0
    current: float = 0.0
    temperature: float = 0.0
    learned_capacity_mah: float | None = None


class ChargeSession:
    """一次充电过程的状态机。

    电量估计策略：
      * 起点：开充瞬间把电流降到 0 测开路电压（OCV），比带载端电压准得多
      * 过程：库仑计（积分电流）推进，不依赖电压
      * 终点：恒压段电流降到终止值即视为满充，校准库仑计与真实容量
      * 剩余时间：恒流段直接用剩余电量/电流；恒压段对 I(t) 做指数拟合
    """

    def __init__(self) -> None:
        self.profile = ChargeProfile()
        self.active = False
        self.phase = Phase.IDLE
        self.soc = 0.0
        self.start_soc = 0.0
        self.charged_ah = 0.0
        self.charged_wh = 0.0
        self.elapsed = 0.0
        self.message = ""
        self.eta_s: float | None = None
        self.eta_model = ""
        self.ocv_v = 0.0
        self.learned_capacity_mah: float | None = None
        self.ocv_drift_mv = 0.0          # 探测期间电压仍在回落的幅度（越大越不准）
        self._terminal_hits = 0
        self._cv_enter_elapsed: float | None = None
        self._i_history: deque[tuple[float, float]] = deque(maxlen=600)
        self._last_info: BasicInfo | None = None

    # -- 生命周期 ------------------------------------------------------ #
    @property
    def required_ah(self) -> float:
        capacity_ah = self.profile.capacity_mah / 1000.0
        return max(1e-6, capacity_ah * (self.profile.target_soc - self.start_soc) / 100.0)

    def start(self, info: BasicInfo | None,
              initial_soc: float | None = None) -> list[Command]:
        self.active = True
        self.phase = Phase.IDLE
        self.charged_ah = 0.0
        self.charged_wh = 0.0
        self.elapsed = 0.0
        self._terminal_hits = 0
        self._cv_enter_elapsed = None
        self._i_history.clear()
        self._last_info = info
        self.eta_s = None
        self.eta_model = ""
        self.learned_capacity_mah = None
        self.message = ""

        if info is not None:
            self.ocv_v = info.vout
        if initial_soc is None:
            initial_soc = self.estimate_soc(info)
        self.start_soc = max(0.0, min(100.0, initial_soc))
        self.soc = self.start_soc

        commands: list[Command] = []
        per_cell = self.ocv_v / max(1, self.profile.cells)
        warning = ""
        if self.ocv_v >= self.profile.cv_voltage_mv / 1000.0 - 0.10:
            warning = "（OCV 已接近 CV，可能未接电池或电池已满，请核对）"
        if info is not None and info.vout_mv < self.profile.trickle_exit_mv:
            self.phase = Phase.TRICKLE
            self.message = (f"实测开路电压 {self.ocv_v:.3f} V（{per_cell:.3f} V/节 → "
                            f"起始 {self.start_soc:.0f}%）{warning}，电压偏低先涓流预充")
            commands.append(self._apply(self.profile.trickle_current_ma))
        else:
            self.phase = Phase.CC
            self.message = (f"实测开路电压 {self.ocv_v:.3f} V（{per_cell:.3f} V/节 → "
                            f"起始 {self.start_soc:.0f}%）{warning}，进入恒流充电")
            commands.append(self._apply(self.profile.cc_current_ma))
        return commands

    def stop(self, message: str = "已停止") -> None:
        self.active = False
        self.phase = Phase.IDLE
        self.message = message

    # -- 更新 ---------------------------------------------------------- #
    def update(self, info: BasicInfo, dt: float) -> list[Command]:
        self._last_info = info
        commands: list[Command] = []
        if not self.active:
            return commands

        dt = max(0.0, min(dt, 5.0))
        self.elapsed += dt
        self.charged_ah += info.iout * dt / 3600.0
        self.charged_wh += info.power * dt / 3600.0
        capacity_ah = self.profile.capacity_mah / 1000.0
        if capacity_ah > 0:
            self.soc = min(100.0, self.start_soc + self.charged_ah / capacity_ah * 100.0)

        # 保护判定
        if info.temperature > self.profile.temp_limit:
            return self._fail(f"温度 {info.temperature:.1f}℃ 超过上限")
        if info.vout_mv > self.profile.cv_voltage_mv + 150:
            return self._fail(f"电压 {info.vout:.2f} V 超过 CV 设定")
        if self.elapsed > self.profile.max_minutes * 60:
            return self._fail("超过最长充电时间")

        v_per_cell = info.vout / max(1, self.profile.cells)

        if self.phase == Phase.TRICKLE:
            if v_per_cell >= self.profile.trickle_exit_v or \
                    info.vout_mv >= self.profile.trickle_exit_mv:
                self.phase = Phase.CC
                self.message = "涓流结束，切换恒流充电"
                commands.append(self._apply(self.profile.cc_current_ma))

        elif self.phase == Phase.CC:
            if info.vout_mv >= self.profile.cv_voltage_mv - 60:
                self.phase = Phase.CV
                self._cv_enter_elapsed = self.elapsed
                self._i_history.clear()
                self.message = "到达恒压点，进入恒压阶段（电流开始自然衰减）"

        elif self.phase == Phase.CV:
            # 必须"确认真的进入恒压并有实际充入"后，才允许用终止电流判满，
            # 否则探测/切换瞬间的 0 电流会被误判成充满。
            settled = (self._cv_enter_elapsed is not None
                       and self.elapsed - self._cv_enter_elapsed >= 60.0
                       and self.charged_ah >= 0.02 * self.required_ah)
            if settled and info.iout_ma <= self.profile.terminal_current_ma:
                self._terminal_hits += 1
                if self._terminal_hits >= 3:
                    self._finish(commands)
                    return commands
            else:
                self._terminal_hits = 0

        self._i_history.append((self.elapsed, info.iout))
        self.eta_s, self.eta_model = self._estimate_eta(info)
        return commands

    def _finish(self, commands: list[Command]) -> None:
        """正常充满：校准库仑计与容量。"""
        self.phase = Phase.DONE
        self.soc = min(100.0, max(self.soc, self.profile.target_soc))
        # 用本次实际充入量反推真实容量，供下次使用
        gained = self.profile.target_soc - self.start_soc
        charged_ok = self.charged_ah >= 0.85 * self.required_ah
        if charged_ok and gained > 5 and self.charged_ah > 0:
            learned = self.charged_ah * 1000.0 / (gained / 100.0)
            configured = self.profile.capacity_mah
            if 0.5 * configured <= learned <= 2.0 * configured:
                self.learned_capacity_mah = learned
        extra = ""
        if self.learned_capacity_mah:
            extra = f"，实测容量约 {self.learned_capacity_mah:.0f} mAh"
        self.message = f"电流降至终止值，充电完成（充入 {self.charged_ah * 1000:.0f} mAh{extra}）"
        commands.append(Command(kind="output", state=0))
        self.active = False

    def _fail(self, message: str) -> list[Command]:
        self.phase = Phase.FAULT
        self.message = message
        self.active = False
        return [Command(kind="output", state=0)]

    # -- 估算 ---------------------------------------------------------- #
    def estimate_soc(self, info: BasicInfo | None,
                     zero_current: bool = False) -> float:
        """由开路电压估算电量。

        zero_current=True 表示刚做过 0 电流探测，端电压即近似 OCV；
        否则按电流扣除粗略内阻压降（误差较大，仅作兜底）。
        """
        if info is None:
            return 30.0
        per_cell = info.vout / max(1, self.profile.cells)
        if not zero_current and info.iout > 0.01:
            # 每节内阻约 60 mΩ，粗略补偿 IR 压降
            ir_drop = info.iout * 0.06 * 1.0
            per_cell -= min(0.20, ir_drop)
        return soc_from_voltage(self.profile.chemistry, per_cell)

    @property
    def avg_current(self) -> float:
        if self.elapsed <= 0:
            return 0.0
        return self.charged_ah * 3600.0 / self.elapsed

    def _estimate_eta(self, info: BasicInfo) -> tuple[float | None, str]:
        remaining_ah = max(0.0, self.required_ah - self.charged_ah)

        if self.phase == Phase.CC:
            current = max(info.iout, 0.001)
            return remaining_ah / current * 3600.0, "恒流直算"

        if self.phase == Phase.CV:
            terminal = self.profile.terminal_current_ma / 1000.0
            fit = self._fit_cv_decay(info.iout, terminal)
            if fit is not None:
                return fit, "恒压指数拟合"
            if info.iout > terminal:
                return remaining_ah / info.iout * 3600.0, "按当前电流估算"
            return 0.0, "即将完成"

        if self.phase == Phase.TRICKLE:
            return None, "涓流预充中，暂不估算"
        return None, ""

    def _fit_cv_decay(self, i_now: float, i_terminal: float) -> float | None:
        """恒压段电流近似指数衰减：I(t)=I0·e^(-(t-t0)/τ)，用 ln I 对 t 线性拟合。"""
        points = [(t, i) for t, i in self._i_history
                  if i > i_terminal * 1.15 and i > 0.002]
        if len(points) < 8:
            return None
        span = points[-1][0] - points[0][0]
        if span < 15.0:               # 样本时间跨度太短，拟合不可靠
            return None
        n = len(points)
        sx = sum(t for t, _ in points)
        sy = sum(math.log(i) for _, i in points)
        sxx = sum(t * t for t, _ in points)
        sxy = sum(t * math.log(i) for t, i in points)
        denom = n * sxx - sx * sx
        if abs(denom) < 1e-9:
            return None
        slope = (n * sxy - sx * sy) / denom
        if slope >= -1e-6:            # 电流没有明显下降，不适用
            return None
        tau = -1.0 / slope
        if tau <= 5 or tau > 7200:
            return None
        if i_now <= i_terminal:
            return 0.0
        eta = tau * math.log(i_now / i_terminal)
        return max(0.0, min(eta, 6 * 3600.0))

    def _apply(self, current_ma: int) -> Command:
        return Command(kind="apply",
                       vo_mv=self.profile.cv_voltage_mv,
                       io_ma=current_ma,
                       ovp_mv=self.profile.ovp_mv,
                       ocp_ma=self.profile.ocp_ma,
                       state=1)

    # -- 展示 ---------------------------------------------------------- #
    def readout(self) -> ChargeReadout:
        info = self._last_info
        required_mah = self.required_ah * 1000.0
        progress = 0.0
        if required_mah > 0:
            progress = max(0.0, min(100.0, self.charged_ah * 1000.0 / required_mah * 100.0))
        return ChargeReadout(
            phase=self.phase,
            soc=self.soc,
            start_soc=self.start_soc,
            charged_mah=self.charged_ah * 1000.0,
            required_mah=required_mah,
            progress=progress,
            elapsed_s=self.elapsed,
            eta_s=self.eta_s,
            eta_model=self.eta_model,
            avg_current=self.avg_current,
            charged_wh=self.charged_wh,
            ocv_v=self.ocv_v,
            message=self.message,
            voltage=info.vout if info else 0.0,
            current=info.iout if info else 0.0,
            temperature=info.temperature if info else 0.0,
            learned_capacity_mah=self.learned_capacity_mah,
        )
