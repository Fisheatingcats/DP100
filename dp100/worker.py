"""后台设备工作线程：负责 HID 连接、轮询与参数写入。

所有 HID 操作都发生在这个 QThread 里，主线程只接收信号，保证界面不卡顿。
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot

from .protocol import (
    BasicInfo,
    BasicSet,
    DeviceInfo,
    DeviceTimeoutError,
    FrameError,
    IDX_MODIFY,
    IDX_QUERY_ACTIVE,
    OP_BASIC_INFO,
    OP_BASIC_SET,
    OP_DEVICE_INFO,
    build_frame,
    build_set_payload,
    parse_frame,
)
from .transport import HidTransport

FAST_INTERVAL_MS = 200     # 实时状态刷新周期
SLOW_INTERVAL_MS = 1000    # 预设/设定值刷新周期
MAX_LINK_ERRORS = 5        # 连续通信失败多少次判定掉线


class DeviceWorker(QObject):
    sig_connected = pyqtSignal(dict)        # {"path": str, "info": DeviceInfo}
    sig_disconnected = pyqtSignal()
    sig_status = pyqtSignal(object)         # BasicInfo
    sig_preset = pyqtSignal(object)         # BasicSet（当前生效组）
    sig_presets = pyqtSignal(object)        # list[BasicSet | None]
    sig_error = pyqtSignal(str)
    sig_log = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self._transport: HidTransport | None = None
        self._preset: BasicSet | None = None
        self._path = ""
        self._fast: QTimer | None = None
        self._slow: QTimer | None = None
        self._link_errors = 0
        self._closing = False

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    @pyqtSlot()
    def init(self) -> None:
        """在 worker 线程内创建定时器。"""
        self._fast = QTimer(self)
        self._fast.setInterval(FAST_INTERVAL_MS)
        self._fast.timeout.connect(self._poll_fast)

        self._slow = QTimer(self)
        self._slow.setInterval(SLOW_INTERVAL_MS)
        self._slow.timeout.connect(self._poll_slow)

    @pyqtSlot(str)
    def open_device(self, path: str) -> None:
        self._shutdown(silent=True)
        self._closing = False          # 重新连接时恢复轮询
        transport = HidTransport(path)
        try:
            transport.open()
            info = self._query_device_info(transport)
            self._transport = transport
            self._path = path
            self._link_errors = 0
            self._preset = None
            self.sig_connected.emit({"path": path, "info": info})
            self._poll_slow()
            if self._fast and self._slow:
                self._fast.start()
                self._slow.start()
            self.sig_log.emit(f"已连接 {info.dev_type or 'ATK-DP100'}")
        except Exception as exc:
            try:
                transport.close()
            except Exception:
                pass
            self._transport = None
            self.sig_error.emit(f"连接失败：{exc}")

    @pyqtSlot()
    def close_device(self) -> None:
        self._shutdown(silent=False)

    def _shutdown(self, silent: bool) -> None:
        self._closing = True
        if self._fast:
            self._fast.stop()
        if self._slow:
            self._slow.stop()
        transport, self._transport = self._transport, None
        self._preset = None
        if transport is not None:
            try:
                transport.close()
            except Exception:
                pass
            if not silent:
                self.sig_log.emit("已断开连接")
        if not silent:
            self.sig_disconnected.emit()

    # ------------------------------------------------------------------ #
    # 轮询
    # ------------------------------------------------------------------ #
    @pyqtSlot()
    def _poll_fast(self) -> None:
        if self._closing or not self._transport:
            return
        try:
            payload = self._xfer(OP_BASIC_INFO, b"", timeout=0.7)
            info = BasicInfo.from_payload(payload)
            self._link_errors = 0
            self.sig_status.emit(info)
        except Exception as exc:
            self._on_link_error(exc)

    def _query_preset(self) -> BasicSet:
        """向设备读取当前生效预设（0x80 查询，10 字节）。"""
        payload = self._xfer(OP_BASIC_SET, bytes([IDX_QUERY_ACTIVE]),
                             timeout=0.7, expect_len=BasicSet.PAYLOAD_SIZE)
        preset = BasicSet.from_payload(payload)
        self._preset = preset
        return preset

    def _poll_slow(self) -> None:
        if self._closing or not self._transport:
            return
        try:
            preset = self._query_preset()
            self.sig_preset.emit(preset)
        except Exception as exc:
            self._on_link_error(exc)

    def _on_link_error(self, exc: Exception) -> None:
        self._link_errors += 1
        if self._link_errors == 1:
            self.sig_error.emit(f"通信异常：{exc}")
        if self._link_errors >= MAX_LINK_ERRORS:
            self._shutdown(silent=True)
            self.sig_error.emit("设备已断开，请检查 USB 连接")

    # ------------------------------------------------------------------ #
    # 命令
    # ------------------------------------------------------------------ #
    @pyqtSlot(int, int, int, int, int)
    def apply_settings(self, vo_mv: int, io_ma: int, ovp_mv: int, ocp_ma: int,
                       state: int) -> None:
        """写入电压/电流/保护限值并立即生效。"""
        if not self._transport:
            self.sig_error.emit("尚未连接设备")
            return
        try:
            payload = build_set_payload(IDX_MODIFY, state, vo_mv, io_ma,
                                        ovp_mv, ocp_ma)
            self._xfer(OP_BASIC_SET, payload, timeout=0.8, expect_len=1)
            self.sig_log.emit(
                f"已应用：{vo_mv / 1000:.2f} V / {io_ma / 1000:.3f} A，"
                f"OVP {ovp_mv / 1000:.2f} V，OCP {ocp_ma / 1000:.3f} A，"
                f"输出{'开' if state else '关'}"
            )
            time.sleep(0.05)
            self._poll_slow()
        except Exception as exc:
            self.sig_error.emit(f"设置失败：{exc}")

    @pyqtSlot(int)
    def set_output(self, state: int) -> None:
        """开/关输出，保留现有设定值。"""
        if not self._transport:
            self.sig_error.emit("尚未连接设备")
            return
        try:
            preset = self._preset
            if preset is None:                      # 兜底：先向设备取一次当前值
                preset = self._query_preset()
            payload = build_set_payload(IDX_MODIFY, 1 if state else 0,
                                        preset.vo_mv, preset.io_ma,
                                        preset.ovp_mv, preset.ocp_ma)
            ack = self._xfer(OP_BASIC_SET, payload, timeout=0.8, expect_len=1)
            if ack and ack[0] != 1:
                self.sig_error.emit(f"设备拒绝了输出切换（result={ack[0]}）")
                return
            self.sig_log.emit("输出已开启" if state else "输出已关闭")
            time.sleep(0.05)
            self._poll_slow()
        except Exception as exc:
            self.sig_error.emit(f"切换输出失败：{exc}")

    @pyqtSlot()
    def query_all_presets(self) -> None:
        """读取 P0~P9 十组预设。"""
        if not self._transport:
            self.sig_error.emit("尚未连接设备")
            return
        presets: list[BasicSet | None] = []
        for index in range(10):
            try:
                payload = self._xfer(OP_BASIC_SET, bytes([index]), timeout=0.5,
                                     expect_len=BasicSet.PAYLOAD_SIZE)
                presets.append(BasicSet.from_payload(payload))
            except Exception:
                presets.append(None)
        self.sig_presets.emit(presets)

    # ------------------------------------------------------------------ #
    # 底层收发
    # ------------------------------------------------------------------ #
    def _query_device_info(self, transport: HidTransport) -> DeviceInfo:
        transport.write(build_frame(OP_DEVICE_INFO))
        deadline = time.monotonic() + 1.2
        while time.monotonic() < deadline:
            raw = transport.read(max(0.05, deadline - time.monotonic()))
            try:
                opcode, payload = parse_frame(raw)
            except FrameError:
                continue
            if opcode == OP_DEVICE_INFO:
                return DeviceInfo.from_payload(payload)
        raise DeviceTimeoutError("读取设备信息超时")

    def _xfer(self, opcode: int, payload: bytes, timeout: float = 1.0,
              expect_len: int | None = None) -> bytes:
        """发送并等待指定 OpCode 的应答。

        expect_len 用于区分同一 OpCode 的不同应答：0x35 的写操作只回 1 字节
        result（0x01=成功），而查询会回 10 字节预设内容，混用会把 ACK 当成预设解析。
        """
        if not self._transport:
            raise DeviceTimeoutError("设备未连接")
        if self._closing:
            raise DeviceTimeoutError("正在关闭")
        self._transport.write(build_frame(opcode, payload))
        deadline = time.monotonic() + timeout
        fallback: bytes | None = None
        while True:
            if self._closing:
                raise DeviceTimeoutError("正在关闭")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if fallback is not None:
                    return fallback
                raise DeviceTimeoutError("设备无响应")
            try:
                raw = self._transport.read(remaining)
            except TimeoutError as exc:
                if fallback is not None:
                    return fallback
                raise DeviceTimeoutError("设备无响应") from exc
            try:
                rx_op, rx_payload = parse_frame(raw)
            except FrameError:
                continue
            if rx_op != opcode:
                continue
            if expect_len is None or len(rx_payload) == expect_len:
                return rx_payload
            fallback = rx_payload      # 长度不符，先记下，继续等正确的那一帧

