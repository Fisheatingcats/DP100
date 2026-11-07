"""ATK-DP100 数控电源私有 USB-HID 协议。

协议要点（逆向自设备实测 + 社区文档）：
  * USB HID，VID=0x2E3C PID=0xAF01，固定 64 字节报文（Windows 下需前置 1 字节 report id）
  * 帧结构: [方向][OpCode][serial][Len][Data...][CRC16 lo][CRC16 hi]
      方向: 0xFB = 主机 -> 设备,  0xFA = 设备 -> 主机
  * CRC: CRC-16/MODBUS，校验范围 = 帧头到数据末字节（含 Len），小端存放
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

HOST_TO_DEV = 0xFB
DEV_TO_HOST = 0xFA
PACKET_SIZE = 64
MAX_PAYLOAD = PACKET_SIZE - 6

# OpCode
OP_DEVICE_INFO = 0x10
OP_START_TRANS = 0x12
OP_DATA_TRANS = 0x13
OP_END_TRANS = 0x14
OP_DEV_UPGRADE = 0x15
OP_BASIC_INFO = 0x30
OP_BASIC_SET = 0x35
OP_SYSTEM_INFO = 0x40
OP_SYSTEM_SET = 0x45
OP_SCAN_OUT = 0x50
OP_SERIAL_OUT = 0x55
OP_DISCONNECT = 0x80

# BASIC_SET 的 index 高位标志
#
# 实测（HW 1.4 / APP 1.4）：只有 0x20 能可靠地修改设定并立即生效；
# 0xA0 / 0x80 在本固件上不会改变输出状态（0x80 仅做“激活”读取）。
# 社区实现 pydp100 同样使用 0x20 完成开关与设值。
IDX_MODIFY = 0x20        # 修改当前预设（含 state 开关），立即生效 —— 唯一可用
IDX_SET = 0x20           # 别名
IDX_ACTIVATE = 0x80      # 激活/查询当前预设
IDX_SET_ACTIVE = 0xA0    # 文档中的“修改并激活”，实测无效，勿用于写入
IDX_QUERY_ACTIVE = 0x80  # 查询：0x80 = 查询当前生效的那一组


class FrameError(Exception):
    """帧解析失败（方向 / CRC / 长度）。"""


class DeviceTimeoutError(Exception):
    """设备无响应。"""


def crc16_modbus(data: bytes) -> int:
    """CRC-16/MODBUS：初值 0xFFFF，多项式 0xA001。"""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def build_frame(opcode: int, payload: bytes = b"") -> bytes:
    """构造一帧 64 字节的主机->设备报文。"""
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"payload 过长: {len(payload)} > {MAX_PAYLOAD}")
    buf = bytearray(PACKET_SIZE)
    buf[0] = HOST_TO_DEV
    buf[1] = opcode
    buf[2] = 0x00
    buf[3] = len(payload)
    buf[4:4 + len(payload)] = payload
    crc = crc16_modbus(bytes(buf[:4 + len(payload)]))
    buf[4 + len(payload)] = crc & 0xFF
    buf[5 + len(payload)] = (crc >> 8) & 0xFF
    return bytes(buf)


def parse_frame(raw: bytes) -> tuple[int, bytes]:
    """解析设备->主机的 64 字节报文，返回 (opcode, payload)。

    Windows 读回来的数据可能带一个前导 report id(0x00)，这里自动兼容。
    """
    if len(raw) and raw[0] == 0x00 and len(raw) > 1:
        raw = raw[1:]
    if len(raw) < 6:
        raise FrameError(f"帧过短: {len(raw)}")
    if raw[0] != DEV_TO_HOST:
        raise FrameError(f"方向字节异常: 0x{raw[0]:02X}")
    opcode = raw[1]
    length = raw[3]
    if 4 + length + 2 > len(raw):
        raise FrameError("长度字段越界")
    expect = crc16_modbus(raw[:4 + length])
    actual = raw[4 + length] | (raw[5 + length] << 8)
    if expect != actual:
        raise FrameError(f"CRC 不符: 期望 0x{expect:04X} 实际 0x{actual:04X}")
    return opcode, bytes(raw[4:4 + length])


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #

@dataclass
class BasicInfo:
    """0x30 实时状态，16 字节。"""

    vin_mv: int          # 输入电压
    vout_mv: int         # 输出电压
    iout_ma: int         # 输出电流
    vmax_mv: int         # 当前允许的最大输出电压
    temp1: int           # 温度（0.1℃）
    temp2: int           # 第二路温度（0.1℃，带符号）
    aux5v_mv: int        # 内部 5V 轨
    out_mode: int        # 输出模式原始枚举
    work_state: int      # 工作状态原始枚举（0 = 正常）

    PAYLOAD_SIZE = 16

    @classmethod
    def from_payload(cls, payload: bytes) -> "BasicInfo":
        if len(payload) != cls.PAYLOAD_SIZE:
            raise FrameError(f"BasicInfo 长度异常: {len(payload)}")
        vin, vout, iout, vmax, t1, t2, aux5 = struct.unpack_from("<7H", payload, 0)
        temp2 = t2 if t2 < 0x8000 else t2 - 0x10000
        return cls(vin, vout, iout, vmax, t1, temp2, aux5, payload[14], payload[15])

    # 便捷属性
    @property
    def vin(self) -> float:
        return self.vin_mv / 1000.0

    @property
    def vout(self) -> float:
        return self.vout_mv / 1000.0

    @property
    def iout(self) -> float:
        return self.iout_ma / 1000.0

    @property
    def power(self) -> float:
        return self.vout_mv * self.iout_ma / 1e6

    @property
    def temperature(self) -> float:
        return self.temp1 / 10.0

    # 输出模式（实测）：0 = 恒流 CC，1 = 恒压 CV，2 = 输出关闭
    OUT_MODE_CC = 0
    OUT_MODE_CV = 1
    OUT_MODE_OFF = 2

    @property
    def output_on(self) -> bool:
        """设备是否处于输出开启状态（依据 out_mode 实测语义）。"""
        if self.out_mode == self.OUT_MODE_OFF:
            return False
        if self.out_mode in (self.OUT_MODE_CC, self.OUT_MODE_CV):
            return True
        # 未知枚举值：退回电流判断
        return self.iout_ma >= 5

    def guessed_mode(self, vo_set_mv: int | None = None) -> str:
        """返回工作模式文字。out_mode 语义已由实测确认。"""
        if self.out_mode == self.OUT_MODE_OFF:
            return "输出关闭"
        if self.out_mode == self.OUT_MODE_CV:
            return "恒压 CV"
        if self.out_mode == self.OUT_MODE_CC:
            return "恒流 CC" if self.iout_ma >= 5 else "输出开启"
        # 未知枚举：用电流与设定电压推断
        if self.iout_ma < 5:
            return "待机"
        if vo_set_mv is not None and self.vout_mv >= vo_set_mv - 80:
            return "恒压 CV"
        return "恒流 CC"


@dataclass
class BasicSet:
    """0x35 预设组，10 字节。"""

    index: int
    state: int       # 0=关闭 1=开启
    vo_mv: int       # 设定电压
    io_ma: int       # 设定电流
    ovp_mv: int      # 过压保护
    ocp_ma: int      # 过流保护

    PAYLOAD_SIZE = 10

    @classmethod
    def from_payload(cls, payload: bytes) -> "BasicSet":
        if len(payload) != cls.PAYLOAD_SIZE:
            raise FrameError(f"BasicSet 长度异常: {len(payload)}")
        index, state, vo, io, ovp, ocp = struct.unpack_from("<BBHHHh", payload, 0)
        return cls(index, state, vo, io, ovp, max(ocp, 0))


def build_set_payload(index: int, state: int, vo_mv: int, io_ma: int,
                      ovp_mv: int, ocp_ma: int) -> bytes:
    """构造 0x35 的设置载荷。"""
    return struct.pack("<BBHHHh", index & 0xFF, 1 if state else 0,
                       int(vo_mv), int(io_ma), int(ovp_mv), int(ocp_ma))


@dataclass
class DeviceInfo:
    """0x10 设备信息，40 字节。"""

    dev_type: str
    hw_ver: int
    app_ver: int
    boot_ver: int
    run_area: int
    serial: str
    year: int
    month: int
    day: int

    PAYLOAD_SIZE = 40

    @classmethod
    def from_payload(cls, payload: bytes) -> "DeviceInfo":
        if len(payload) != cls.PAYLOAD_SIZE:
            raise FrameError(f"DeviceInfo 长度异常: {len(payload)}")
        text = bytes(payload[0:16]).decode("latin-1", "ignore")
        dev_type = "".join(ch for ch in text if 32 <= ord(ch) < 127).strip()
        hw, app, boot, area = struct.unpack_from("<4H", payload, 16)
        sn = bytes(payload[24:36]).decode("latin-1", "ignore")
        sn = "".join(ch for ch in sn if 32 <= ord(ch) < 127).strip()
        year, = struct.unpack_from("<H", payload, 36)
        return cls(dev_type, hw, app, boot, area, sn, year, payload[38], payload[39])

    @staticmethod
    def _ver(v: int) -> str:
        return f"{v // 10}.{v % 10}"

    def describe(self) -> str:
        return (f"{self.dev_type or 'ATK-DP100'}  |  固件 {self._ver(self.app_ver)}"
                f"  硬件 {self._ver(self.hw_ver)}  Boot {self._ver(self.boot_ver)}")
