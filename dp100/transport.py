"""Windows HID 传输层：零依赖（纯 ctypes），用于 ATK-DP100。

特点：
  * SetupAPI 枚举 HID 设备，按 VID/PID 过滤
  * CreateFile / WriteFile / ReadFile 直接收发 64 字节报文
  * 读操作带超时（内部线程 + join），避免界面卡死
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
import uuid
from ctypes import wintypes

VID = 0x2E3C
PID = 0xAF01
GUID_HID = uuid.UUID("4D1E55B2-F16F-11CF-88CB-001111000030")

DIGCF_PRESENT = 0x00000002
DIGCF_DEVICEINTERFACE = 0x00000010
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

ERROR_IO_PENDING = 997
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_uuid(cls, u: uuid.UUID) -> "GUID":
        return cls(u.fields[0], u.fields[1], u.fields[2],
                   (ctypes.c_ubyte * 8)(*u.bytes[8:]))


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", ctypes.c_size_t),
    ]


class SP_DEVICE_INTERFACE_DETAIL_DATA_W(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("DevicePath", ctypes.c_wchar * 1),
    ]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Size", wintypes.DWORD),
        ("VendorID", wintypes.WORD),
        ("ProductID", wintypes.WORD),
        ("VersionNumber", wintypes.WORD),
    ]


class OVERLAPPED(ctypes.Structure):
    """Windows OVERLAPPED（64 位下 32 字节）。"""

    _fields_ = [
        ("Internal", ctypes.c_void_p),
        ("InternalHigh", ctypes.c_void_p),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", ctypes.c_void_p),
    ]


if sys.platform.startswith("win"):
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _hid = ctypes.WinDLL("hid", use_last_error=True)
    _setup = ctypes.WinDLL("setupapi", use_last_error=True)

    _k32.CreateFileW.restype = ctypes.c_void_p
    _k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                 ctypes.c_void_p]
    _k32.WriteFile.restype = wintypes.BOOL
    _k32.WriteFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                               ctypes.POINTER(wintypes.DWORD),
                               ctypes.POINTER(OVERLAPPED)]
    _k32.ReadFile.restype = wintypes.BOOL
    _k32.ReadFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                              ctypes.POINTER(wintypes.DWORD),
                              ctypes.POINTER(OVERLAPPED)]
    _k32.CloseHandle.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [ctypes.c_void_p]
    _k32.CreateEventW.restype = ctypes.c_void_p
    _k32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL,
                                  wintypes.LPCWSTR]
    _k32.WaitForSingleObject.restype = wintypes.DWORD
    _k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    _k32.GetOverlappedResult.restype = wintypes.BOOL
    _k32.GetOverlappedResult.argtypes = [ctypes.c_void_p, ctypes.POINTER(OVERLAPPED),
                                         ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]
    _k32.CancelIoEx.restype = wintypes.BOOL
    _k32.CancelIoEx.argtypes = [ctypes.c_void_p, ctypes.POINTER(OVERLAPPED)]

    _setup.SetupDiGetClassDevsW.restype = ctypes.c_void_p
    _setup.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wintypes.LPCWSTR,
                                            ctypes.c_void_p, wintypes.DWORD]
    _setup.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
    _setup.SetupDiEnumDeviceInterfaces.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(GUID), wintypes.DWORD,
        ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
    _setup.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
    _setup.SetupDiGetDeviceInterfaceDetailW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA), ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    _setup.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL
    _setup.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]

    _hid.HidD_GetAttributes.restype = wintypes.BOOL
    _hid.HidD_GetAttributes.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDD_ATTRIBUTES)]
    _hid.HidD_GetProductString.restype = wintypes.BOOL
    _hid.HidD_GetProductString.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD]


def _detail_cb_size() -> int:
    return ctypes.sizeof(SP_DEVICE_INTERFACE_DETAIL_DATA_W)


def enumerate_hid_paths() -> list[str]:
    """返回系统中所有 HID 设备接口路径。"""
    if not sys.platform.startswith("win"):
        return []
    guid = GUID.from_uuid(GUID_HID)
    hdev = _setup.SetupDiGetClassDevsW(ctypes.byref(guid), None, None,
                                       DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if not hdev:
        return []
    paths: list[str] = []
    try:
        ifd = SP_DEVICE_INTERFACE_DATA()
        ifd.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
        idx = 0
        while _setup.SetupDiEnumDeviceInterfaces(hdev, None, ctypes.byref(guid), idx,
                                                 ctypes.byref(ifd)):
            idx += 1
            required = wintypes.DWORD(0)
            _setup.SetupDiGetDeviceInterfaceDetailW(hdev, ctypes.byref(ifd), None, 0,
                                                    ctypes.byref(required), None)
            if required.value <= 0:
                continue
            raw = (ctypes.c_byte * required.value)()
            detail = ctypes.cast(raw, ctypes.POINTER(SP_DEVICE_INTERFACE_DETAIL_DATA_W))
            detail.contents.cbSize = _detail_cb_size()
            if _setup.SetupDiGetDeviceInterfaceDetailW(
                    hdev, ctypes.byref(ifd), ctypes.cast(detail, ctypes.c_void_p),
                    required.value, ctypes.byref(required), None):
                data = bytes(raw)
                text = data[4:].decode("utf-16-le", "ignore")
                paths.append(text.split("\x00", 1)[0])
    finally:
        _setup.SetupDiDestroyDeviceInfoList(hdev)
    return paths


def _product_string(path: str) -> str:
    """尽力读取 HID 产品名（失败返回空串）。"""
    handle = _k32.CreateFileW(path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                              OPEN_EXISTING, 0, None)
    if not handle or handle == INVALID_HANDLE_VALUE:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(256)
        if _hid.HidD_GetProductString(handle, ctypes.cast(buf, ctypes.c_void_p), 512):
            return buf.value
    finally:
        _k32.CloseHandle(handle)
    return ""


def list_dp100() -> list[dict]:
    """枚举已插入的 ATK-DP100 设备。"""
    found: list[dict] = []
    for path in enumerate_hid_paths():
        handle = _k32.CreateFileW(path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                                  OPEN_EXISTING, 0, None)
        if not handle or handle == INVALID_HANDLE_VALUE:
            continue
        try:
            attrs = HIDD_ATTRIBUTES()
            attrs.Size = ctypes.sizeof(HIDD_ATTRIBUTES)
            if not _hid.HidD_GetAttributes(handle, ctypes.byref(attrs)):
                continue
            if attrs.VendorID != VID or attrs.ProductID != PID:
                continue
            found.append({
                "path": path,
                "vid": attrs.VendorID,
                "pid": attrs.ProductID,
                "name": _product_string(path) or "ATK-DP100",
            })
        finally:
            _k32.CloseHandle(handle)
    return found


class HidTransport:
    """DP100 的单条连接。

    使用 Windows 重叠 I/O：每次读写都带事件对象 + 超时，超时即 CancelIoEx，
    不会留下卡死的后台线程，也不会让调用方永久阻塞。
    """

    def __init__(self, path: str):
        self.path = path
        self._handle: int | None = None
        self._lock = threading.RLock()
        self._closed = False

    # -- 生命周期 ---------------------------------------------------------- #
    def open(self) -> None:
        if self._handle:
            return
        handle = _k32.CreateFileW(self.path,
                                  GENERIC_READ | GENERIC_WRITE,
                                  FILE_SHARE_READ | FILE_SHARE_WRITE,
                                  None, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None)
        if not handle or handle == INVALID_HANDLE_VALUE:
            err = ctypes.get_last_error()
            raise OSError(f"无法打开 HID 设备 (WinError {err})")
        self._handle = handle
        self._closed = False

    def close(self) -> None:
        with self._lock:
            handle, self._handle = self._handle, None
            self._closed = True
            if handle:
                # 先撤掉所有挂起的重叠操作，再关句柄，避免 CloseHandle 阻塞
                try:
                    _k32.CancelIoEx(handle, None)
                except Exception:
                    pass
                try:
                    _k32.CloseHandle(handle)
                except Exception:
                    pass

    @property
    def is_open(self) -> bool:
        return self._handle is not None and not self._closed

    # -- 重叠调用 ---------------------------------------------------------- #
    def _overlapped_call(self, func, buffer, size: int, timeout: float) -> int:
        """执行一次重叠读写，返回传输字节数；超时抛 TimeoutError。"""
        with self._lock:
            handle = self._handle
        if not handle or self._closed:
            raise OSError("设备已关闭")

        event = _k32.CreateEventW(None, True, False, None)
        if not event:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            overlapped = OVERLAPPED()
            overlapped.hEvent = event
            transferred = wintypes.DWORD(0)
            ok = func(handle, ctypes.cast(buffer, ctypes.c_void_p), size,
                      ctypes.byref(transferred), ctypes.byref(overlapped))
            if not ok:
                err = ctypes.get_last_error()
                if err != ERROR_IO_PENDING:
                    raise ctypes.WinError(err)

            wait_ms = int(max(1.0, timeout * 1000.0))
            rc = _k32.WaitForSingleObject(event, wait_ms)
            if rc == WAIT_TIMEOUT:
                _k32.CancelIoEx(handle, ctypes.byref(overlapped))
                # 回收被取消的操作，保证句柄安全
                _k32.GetOverlappedResult(handle, ctypes.byref(overlapped),
                                         ctypes.byref(transferred), True)
                raise TimeoutError("等待设备响应超时")
            if rc != WAIT_OBJECT_0:
                raise OSError(f"等待设备响应失败 (wait={rc})")

            done = wintypes.DWORD(0)
            if not _k32.GetOverlappedResult(handle, ctypes.byref(overlapped),
                                            ctypes.byref(done), False):
                err = ctypes.get_last_error()
                raise ctypes.WinError(err)
            return int(done.value)
        finally:
            _k32.CloseHandle(event)

    # -- 收发 -------------------------------------------------------------- #
    def write(self, payload: bytes, timeout: float = 1.0) -> None:
        """发送一帧 64 字节数据（Windows 需要前置 report id 0x00）。"""
        buf = (ctypes.c_ubyte * 65)()
        buf[0] = 0x00
        for i, b in enumerate(payload[:64]):
            buf[i + 1] = b
        try:
            self._overlapped_call(_k32.WriteFile, buf, 65, timeout)
        except OSError as exc:
            # 少数固件按裸 64 字节收报文（WinError 87 = 参数错误）
            if getattr(exc, "winerror", None) != 87:
                raise
            buf64 = (ctypes.c_ubyte * 64)(*payload[:64])
            self._overlapped_call(_k32.WriteFile, buf64, 64, timeout)

    def read(self, timeout: float = 1.0) -> bytes:
        """读一帧，超时抛 TimeoutError。"""
        buf = (ctypes.c_ubyte * 65)()
        got = self._overlapped_call(_k32.ReadFile, buf, 65, timeout)
        return bytes(buf[:got])

    def __enter__(self) -> "HidTransport":
        self.open()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
