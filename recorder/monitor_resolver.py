"""Module: @role: Windowsモニター列挙(DXGI/Win32)と録画対象モニター・目標解像度の解決を担うモジュール"""
import ctypes
from dataclasses import dataclass
import logging
import os
from typing import List, Optional

from core.config import Config

_DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x00000001
_DISPLAY_DEVICE_PRIMARY_DEVICE = 0x00000004
_ENUM_CURRENT_SETTINGS = 0xFFFFFFFF

@dataclass(frozen=True)
class MonitorInfo:
    index: int
    device_name: str
    friendly_name: str
    adapter_idx: int
    output_idx: int
    x: int
    y: int
    width: int
    height: int
    is_primary: bool

if os.name == "nt":
    class _DISPLAY_DEVICEW(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("DeviceName", ctypes.c_wchar * 32),
            ("DeviceString", ctypes.c_wchar * 128),
            ("StateFlags", ctypes.c_ulong),
            ("DeviceID", ctypes.c_wchar * 128),
            ("DeviceKey", ctypes.c_wchar * 128),
        ]

    class _DEVMODEW(ctypes.Structure):
        _fields_ = [
            ("dmDeviceName", ctypes.c_wchar * 32),
            ("dmSpecVersion", ctypes.c_ushort),
            ("dmDriverVersion", ctypes.c_ushort),
            ("dmSize", ctypes.c_ushort),
            ("dmDriverExtra", ctypes.c_ushort),
            ("dmFields", ctypes.c_ulong),
            ("dmPosition_x", ctypes.c_long),
            ("dmPosition_y", ctypes.c_long),
            ("dmDisplayOrientation", ctypes.c_ulong),
            ("dmDisplayFixedOutput", ctypes.c_ulong),
            ("dmColor", ctypes.c_short),
            ("dmDuplex", ctypes.c_short),
            ("dmYResolution", ctypes.c_short),
            ("dmTTOption", ctypes.c_short),
            ("dmCollate", ctypes.c_short),
            ("dmFormName", ctypes.c_wchar * 32),
            ("dmLogPixels", ctypes.c_ushort),
            ("dmBitsPerPel", ctypes.c_ulong),
            ("dmPelsWidth", ctypes.c_ulong),
            ("dmPelsHeight", ctypes.c_ulong),
            ("dmDisplayFlags", ctypes.c_ulong),
            ("dmDisplayFrequency", ctypes.c_ulong),
            ("dmICMMethod", ctypes.c_ulong),
            ("dmICMIntent", ctypes.c_ulong),
            ("dmMediaType", ctypes.c_ulong),
            ("dmDitherType", ctypes.c_ulong),
            ("dmReserved1", ctypes.c_ulong),
            ("dmReserved2", ctypes.c_ulong),
            ("dmPanningWidth", ctypes.c_ulong),
            ("dmPanningHeight", ctypes.c_ulong),
        ]

    class _RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    class _DXGI_OUTPUT_DESC(ctypes.Structure):
        _fields_ = [
            ("DeviceName", ctypes.c_wchar * 32),
            ("DesktopCoordinates", _RECT),
            ("AttachedToDesktop", ctypes.c_int),
            ("Rotation", ctypes.c_int),
            ("Monitor", ctypes.c_void_p),
        ]

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_ulong),
            ("Data2", ctypes.c_ushort),
            ("Data3", ctypes.c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        ]

def _query_dxgi_output_map() -> dict[str, tuple[int, int, int, int, int, int]]:
    """DXGIを走査し DeviceName -> (adapter_idx, output_idx, x, y, w, h) の対応表を返す"""
    if os.name != "nt":
        return {}
    result: dict[str, tuple[int, int, int, int, int, int]] = {}
    try:
        dxgi = ctypes.windll.LoadLibrary("dxgi.dll")
        create_factory = dxgi.CreateDXGIFactory1
        create_factory.argtypes = [ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
        create_factory.restype = ctypes.c_long

        iid_factory1 = _GUID(
            0x770AAE78,
            0xF26F,
            0x4DBA,
            (ctypes.c_ubyte * 8)(0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87),
        )
        factory_ptr = ctypes.c_void_p()
        if create_factory(ctypes.byref(iid_factory1), ctypes.byref(factory_ptr)) < 0 or not factory_ptr.value:
            return {}

        ptr_size = ctypes.sizeof(ctypes.c_void_p)
        release_proto = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
        enum_adapters_proto = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)
        )
        enum_outputs_proto = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)
        )
        get_desc_proto = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(_DXGI_OUTPUT_DESC)
        )

        def _get_vfunc(com_ptr: ctypes.c_void_p, vtbl_idx: int, proto):
            vtbl = ctypes.cast(com_ptr, ctypes.POINTER(ctypes.c_void_p)).contents.value
            func_addr = ctypes.cast(vtbl + vtbl_idx * ptr_size, ctypes.POINTER(ctypes.c_void_p)).contents.value
            return proto(func_addr)

        try:
            enum_adapters1 = _get_vfunc(factory_ptr, 12, enum_adapters_proto)
            adapter_idx = 0
            while True:
                adapter_ptr = ctypes.c_void_p()
                if enum_adapters1(factory_ptr, adapter_idx, ctypes.byref(adapter_ptr)) < 0 or not adapter_ptr.value:
                    break
                try:
                    enum_outputs = _get_vfunc(adapter_ptr, 7, enum_outputs_proto)
                    output_idx = 0
                    while True:
                        output_ptr = ctypes.c_void_p()
                        if enum_outputs(adapter_ptr, output_idx, ctypes.byref(output_ptr)) < 0 or not output_ptr.value:
                            break
                        try:
                            get_desc = _get_vfunc(output_ptr, 7, get_desc_proto)
                            desc = _DXGI_OUTPUT_DESC()
                            if get_desc(output_ptr, ctypes.byref(desc)) >= 0 and desc.DeviceName:
                                r = desc.DesktopCoordinates
                                w = max(0, int(r.right - r.left))
                                h = max(0, int(r.bottom - r.top))
                                result[desc.DeviceName.upper()] = (
                                    adapter_idx,
                                    output_idx,
                                    int(r.left),
                                    int(r.top),
                                    w,
                                    h,
                                )
                        finally:
                            _get_vfunc(output_ptr, 2, release_proto)(output_ptr)
                        output_idx += 1
                finally:
                    _get_vfunc(adapter_ptr, 2, release_proto)(adapter_ptr)
                adapter_idx += 1
        finally:
            _get_vfunc(factory_ptr, 2, release_proto)(factory_ptr)
    except Exception as e:
        logging.debug(f"[FFmpegCommandBuilder] DXGI output enumeration fallback: {e}")
    return result

def get_monitors() -> List[MonitorInfo]:
    """システムに接続されている有効なモニター一覧を返す"""
    if os.name != "nt":
        return [
            MonitorInfo(
                index=0,
                device_name=r"\\.\DISPLAY1",
                friendly_name="Primary Monitor",
                adapter_idx=0,
                output_idx=0,
                x=0,
                y=0,
                width=1920,
                height=1080,
                is_primary=True,
            )
        ]

    dxgi_map = _query_dxgi_output_map()
    monitors: List[MonitorInfo] = []
    try:
        user32 = ctypes.windll.user32
        enum_devices = user32.EnumDisplayDevicesW
        enum_devices.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.POINTER(_DISPLAY_DEVICEW), ctypes.c_ulong]
        enum_devices.restype = ctypes.c_int

        enum_settings = user32.EnumDisplaySettingsExW
        enum_settings.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.POINTER(_DEVMODEW), ctypes.c_ulong]
        enum_settings.restype = ctypes.c_int

        adapter_output_counters: dict[str, int] = {}
        dev_idx = 0
        while True:
            dd = _DISPLAY_DEVICEW()
            dd.cb = ctypes.sizeof(_DISPLAY_DEVICEW)
            if not enum_devices(None, dev_idx, ctypes.byref(dd), 0):
                break
            dev_idx += 1

            if not (dd.StateFlags & _DISPLAY_DEVICE_ATTACHED_TO_DESKTOP):
                continue

            dev_name = dd.DeviceName.strip()
            is_primary = bool(dd.StateFlags & _DISPLAY_DEVICE_PRIMARY_DEVICE)

            mon_dd = _DISPLAY_DEVICEW()
            mon_dd.cb = ctypes.sizeof(_DISPLAY_DEVICEW)
            friendly = ""
            if enum_devices(dev_name, 0, ctypes.byref(mon_dd), 0):
                friendly = mon_dd.DeviceString.strip()
            if not friendly:
                friendly = dd.DeviceString.strip() or f"Display {len(monitors) + 1}"

            dxgi_entry = dxgi_map.get(dev_name.upper())
            if dxgi_entry is not None:
                adapter_idx, output_idx, dx_x, dx_y, dx_w, dx_h = dxgi_entry
            else:
                gpu_key = dd.DeviceString.strip() or "default_adapter"
                output_idx = adapter_output_counters.get(gpu_key, 0)
                adapter_output_counters[gpu_key] = output_idx + 1
                adapter_idx = 0
                dx_x, dx_y, dx_w, dx_h = 0, 0, 0, 0

            dm = _DEVMODEW()
            dm.dmSize = ctypes.sizeof(_DEVMODEW)
            if enum_settings(dev_name, _ENUM_CURRENT_SETTINGS, ctypes.byref(dm), 0) and dm.dmPelsWidth > 0 and dm.dmPelsHeight > 0:
                x = int(dm.dmPosition_x)
                y = int(dm.dmPosition_y)
                width = int(dm.dmPelsWidth)
                height = int(dm.dmPelsHeight)
            elif dx_w > 0 and dx_h > 0:
                x, y, width, height = dx_x, dx_y, dx_w, dx_h
            else:
                x, y = 0, 0
                width = int(user32.GetSystemMetrics(0)) or 1920
                height = int(user32.GetSystemMetrics(1)) or 1080

            monitors.append(
                MonitorInfo(
                    index=len(monitors),
                    device_name=dev_name,
                    friendly_name=friendly,
                    adapter_idx=adapter_idx,
                    output_idx=output_idx,
                    x=x,
                    y=y,
                    width=width,
                    height=height,
                    is_primary=is_primary,
                )
            )
    except Exception as e:
        logging.warning(f"[FFmpegCommandBuilder] Failed to enumerate monitors: {e}")

    if not monitors:
        w, h = 1920, 1080
        try:
            user32 = ctypes.windll.user32
            w = int(user32.GetSystemMetrics(0)) or 1920
            h = int(user32.GetSystemMetrics(1)) or 1080
        except Exception:
            pass
        monitors.append(
            MonitorInfo(
                index=0,
                device_name=r"\\.\DISPLAY1",
                friendly_name="Primary Monitor",
                adapter_idx=0,
                output_idx=0,
                x=0,
                y=0,
                width=w,
                height=h,
                is_primary=True,
            )
        )
    return monitors

def resolve_target_monitor(config: Optional[Config] = None) -> MonitorInfo:
    """設定値(RECORD_INPUT_SOURCE)から録画対象の単一モニターを解決する（デフォルトはメインモニター）"""
    monitors = get_monitors()
    primary_mon = next((m for m in monitors if m.is_primary), monitors[0])
    if config is None:
        return primary_mon

    raw_src = str(getattr(config, "RECORD_INPUT_SOURCE", "primary") or "primary").strip()
    src_lower = raw_src.lower()

    if src_lower in ("", "primary", "desktop", "default"):
        return primary_mon

    for m in monitors:
        if m.device_name.lower() == src_lower:
            return m
        short_dev = m.device_name.split("\\")[-1].lower()
        if short_dev == src_lower:
            return m

    idx_str = ""
    if raw_src.isdigit():
        idx_str = raw_src
    elif src_lower.startswith("output_idx="):
        idx_str = raw_src.split("=", 1)[1].strip()
    elif src_lower.startswith("monitor:"):
        idx_str = raw_src.split(":", 1)[1].strip()

    if idx_str.isdigit():
        target_idx = int(idx_str)
        for m in monitors:
            if m.index == target_idx:
                return m
        for m in monitors:
            if m.output_idx == target_idx:
                return m

    return primary_mon

def get_desktop_resolution(config: Optional[Config] = None) -> tuple[int, int]:
    mon = resolve_target_monitor(config)
    if mon.width > 0 and mon.height > 0:
        return mon.width, mon.height
    return 1920, 1080

def parse_target_resolution(config: Config) -> tuple[int, int]:
    res_str = str(getattr(config, "RECORD_RESOLUTION", "1920x1080") or "1920x1080")
    try:
        parts = res_str.lower().split("x")
        if len(parts) == 2:
            w = (int(parts[0]) // 2) * 2
            h = (int(parts[1]) // 2) * 2
            if w > 0 and h > 0:
                return w, h
    except Exception:
        pass
    return 1920, 1080
