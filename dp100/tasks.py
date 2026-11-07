"""自动化任务引擎：定时任务 + 事件任务。

任务模型：
  * 定时任务：延时 N 秒后执行，或在每天指定时刻执行
  * 事件任务：电压/电流/功率/温度达到阈值时执行（上升沿触发）

执行动作：关闭输出、打开输出、设定电压、降低电压、升高电压、设定电流。
任务真正下发前会先判断：自动化总开关是否启用、设备是否在线。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .protocol import BasicInfo

KIND_LABELS = {"timed": "定时", "event": "事件"}

ACTION_LABELS = {
    "output_off": "关闭输出",
    "output_on": "打开输出",
    "set_voltage": "设定电压",
    "lower_voltage": "降低电压",
    "raise_voltage": "升高电压",
    "set_current": "设定电流",
}

ACTION_UNITS = {
    "set_voltage": "V",
    "lower_voltage": "V",
    "raise_voltage": "V",
    "set_current": "A",
}

SOURCE_LABELS = {
    "voltage": "输出电压",
    "current": "输出电流",
    "power": "输出功率",
    "temp": "设备温度",
}

COMPARATOR_LABELS = {">=": "≥", "<=": "≤"}

TIMING_LABELS = {"delay": "延时执行", "at": "每天定时"}


@dataclass
class Task:
    kind: str = "event"                 # timed / event
    action: str = "output_off"
    value: float = 0.0                  # 动作参数（V / A）
    source: str = "current"             # 事件源
    comparator: str = ">="
    threshold: float = 0.0
    mode: str = "delay"                 # 定时方式：delay / at
    at_time: str = "12:00:00"
    delay_s: float = 60.0
    enabled: bool = True
    once: bool = False                  # 触发一次后自动停用
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    last_fired: str = ""
    next_run: float = 0.0

    # -- 展示 ---------------------------------------------------------- #
    def describe_condition(self) -> str:
        if self.kind == "timed":
            if self.mode == "delay":
                return f"{TIMING_LABELS['delay']} {self.delay_s / 60:.1f} 分钟"
            return f"{TIMING_LABELS['at']} {self.at_time}"
        return (f"{SOURCE_LABELS.get(self.source, self.source)} "
                f"{COMPARATOR_LABELS.get(self.comparator, self.comparator)} "
                f"{self.threshold:.2f}")

    def describe_action(self) -> str:
        label = ACTION_LABELS.get(self.action, self.action)
        unit = ACTION_UNITS.get(self.action)
        if unit:
            return f"{label} {self.value:.2f} {unit}"
        return label

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in allowed})


class TaskEngine(QObject):
    """任务调度与判定。执行动作由外部通过 fired 信号处理。"""

    fired = pyqtSignal(object)     # Task
    tasks_changed = pyqtSignal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.tasks: list[Task] = []
        self.enabled = False
        self._status: BasicInfo | None = None
        self._cond_state: dict[str, bool] = {}
        self._last_tick = time.monotonic()

        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # -- 状态 ---------------------------------------------------------- #
    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        if enabled:
            self.reschedule_all()

    def set_status(self, info: BasicInfo) -> None:
        self._status = info
        if not self.enabled:
            return
        for task in self.tasks:
            if task.kind != "event" or not task.enabled:
                continue
            value = self._probe(task.source)
            cond = value >= task.threshold if task.comparator == ">=" else value <= task.threshold
            previous = self._cond_state.get(task.id, False)
            self._cond_state[task.id] = cond
            if cond and not previous:
                self._fire(task)

    def _probe(self, source: str) -> float:
        info = self._status
        if info is None:
            return 0.0
        return {
            "voltage": info.vout,
            "current": info.iout,
            "power": info.power,
            "temp": info.temperature,
        }.get(source, 0.0)

    # -- 任务管理 ------------------------------------------------------ #
    def add_task(self, task: Task) -> None:
        self.tasks.append(task)
        if task.kind == "timed":
            self._schedule(task)
        self.tasks_changed.emit()

    def remove_task(self, task_id: str) -> None:
        self.tasks = [t for t in self.tasks if t.id != task_id]
        self._cond_state.pop(task_id, None)
        self.tasks_changed.emit()

    def clear(self) -> None:
        self.tasks.clear()
        self._cond_state.clear()
        self.tasks_changed.emit()

    def reschedule_all(self) -> None:
        for task in self.tasks:
            if task.kind == "timed" and task.enabled:
                self._schedule(task)

    def reset_condition_state(self) -> None:
        self._cond_state.clear()

    # -- 调度 ---------------------------------------------------------- #
    def _schedule(self, task: Task) -> None:
        now = time.time()
        if task.mode == "delay":
            task.next_run = now + max(1.0, float(task.delay_s))
            return
        try:
            hh, mm, ss = (int(part) for part in task.at_time.split(":"))
        except ValueError:
            hh, mm, ss = 12, 0, 0
        target = datetime.now().replace(hour=hh, minute=mm, second=ss, microsecond=0)
        if target <= datetime.now():
            target += timedelta(days=1)
        task.next_run = target.timestamp()

    def _tick(self) -> None:
        if not self.enabled:
            return
        now = time.time()
        for task in list(self.tasks):
            if task.kind != "timed" or not task.enabled:
                continue
            if task.next_run <= 0:
                self._schedule(task)
                continue
            if task.next_run > now:
                continue
            if task.mode == "delay":
                task.enabled = False          # 延时任务只执行一次
            else:
                self._schedule(task)          # 每天定时，排下一次
            self._fire(task)

    def _fire(self, task: Task) -> None:
        task.last_fired = datetime.now().strftime("%H:%M:%S")
        if task.kind == "event" and task.once:
            task.enabled = False
        self.fired.emit(task)

    # -- 存取 ---------------------------------------------------------- #
    def to_json(self) -> dict:
        return {"enabled": self.enabled, "tasks": [t.to_dict() for t in self.tasks]}

    def load_json(self, data: dict | None) -> None:
        self.tasks.clear()
        self._cond_state.clear()
        if not data:
            return
        self.enabled = bool(data.get("enabled", False))
        for item in data.get("tasks", []):
            try:
                self.tasks.append(Task.from_dict(item))
            except TypeError:
                continue
        self.reschedule_all()
        self.tasks_changed.emit()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_json(), ensure_ascii=False, indent=2),
                        encoding="utf-8")

    def load(self, path: Path) -> None:
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self.load_json(data)
