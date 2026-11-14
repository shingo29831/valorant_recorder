"""Module: @role: 録画設定とエンコーダ特性に応じたFFmpegコマンドラインおよびフィルタグラフを構築するモジュール"""
import ctypes
from dataclasses import dataclass
import logging
import os
import re
import subprocess
from typing import List, Optional
from core.config import Config
from recorder.audio_processor_wrapper import get_short_path_name
from recorder.ffmpeg_downloader import ensure_rnnoise_model_downloaded

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
    res_str = str(getattr(config, "RECORD_RESOLUTION", "1920x1080"))
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


_THREAD_QUEUE_SIZE_SUPPORT_CACHE: dict[str, tuple[bool, bool]] = {}


def get_thread_queue_size_support(ffmpeg_path: str) -> tuple[bool, bool]:
    """FFmpegのバージョン差異によるthread_queue_sizeの入出力オプション仕様を動的判定"""
    if not ffmpeg_path or not os.path.exists(ffmpeg_path):
        return False, False
    if ffmpeg_path in _THREAD_QUEUE_SIZE_SUPPORT_CACHE:
        return _THREAD_QUEUE_SIZE_SUPPORT_CACHE[ffmpeg_path]

    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    # 旧仕様(demuxer入力)の受け付け可否を検査
    supports_input = False
    try:
        res_in = subprocess.run(
            [ffmpeg_path, "-v", "error", "-thread_queue_size", "16", "-f", "lavfi", "-i", "anullsrc", "-frames:a", "1", "-f", "null", "-"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
            timeout=3.0,
        )
        err_in = res_in.stderr.decode("utf-8", errors="replace").lower() if res_in.stderr else ""
        if res_in.returncode == 0 and "cannot be applied to input" not in err_in and "unrecognized option" not in err_in:
            supports_input = True
    except Exception:
        supports_input = False

    # 新仕様(muxer出力)の受け付け可否を検査
    supports_output = False
    try:
        res_out = subprocess.run(
            [ffmpeg_path, "-v", "error", "-f", "lavfi", "-i", "anullsrc", "-frames:a", "1", "-thread_queue_size", "16", "-f", "null", "-"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
            timeout=3.0,
        )
        err_out = res_out.stderr.decode("utf-8", errors="replace").lower() if res_out.stderr else ""
        if res_out.returncode == 0 and "cannot be applied to output" not in err_out and "unrecognized option" not in err_out:
            supports_output = True
    except Exception:
        supports_output = False

    _THREAD_QUEUE_SIZE_SUPPORT_CACHE[ffmpeg_path] = (supports_input, supports_output)
    return supports_input, supports_output


def resolve_rnnoise_filter(app_data_dir: str) -> str:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.path.join(project_root, "bin", "sh.rnnn"),
        os.path.join(app_data_dir, "bin", "sh.rnnn"),
    ]
    rnnn_path = None
    for c in candidates:
        if os.path.isfile(c) and os.path.getsize(c) > 100:
            rnnn_path = c
            break
    if not rnnn_path:
        try:
            rnnn_path = ensure_rnnoise_model_downloaded(app_data_dir)
        except Exception as e:
            logging.warning(f"[FFmpegCommandBuilder] Could not prepare RNNoise model: {e}")
            return ""
    if rnnn_path and os.path.exists(rnnn_path):
        short_p = get_short_path_name(os.path.abspath(rnnn_path)).replace("\\", "/").replace(":", "\\:")
        return f"arnndn=m='{short_p}',"
    return ""


def build_ffmpeg_command(
    config: Config,
    ffmpeg_path: str,
    actual_encoder: str,
    temp_filepath: str,
    force_sw_download: bool = False,
    force_gdigrab: bool = False,
) -> str:
    is_lightweight = bool(getattr(config, "LIGHTWEIGHT_MODE", False))
    enc_lower = actual_encoder.lower()
    is_nvenc = "nvenc" in enc_lower
    is_amf = "amf" in enc_lower
    is_qsv = "qsv" in enc_lower

    try:
        fps_int = max(1, int(getattr(config, "RECORD_FPS", "60")))
    except (ValueError, TypeError):
        fps_int = 60
    fps = str(fps_int)
    gop_size = fps
    target_mon = resolve_target_monitor(config)
    target_w, target_h = parse_target_resolution(config)
    screen_w, screen_h = target_mon.width, target_mon.height
    needs_scale = target_w != screen_w or target_h != screen_h

    # 高解像度(1440p以上)や高FPS(120以上)での画質破綻を防ぐためビットレート上限をスケーリング
    is_high_bandwidth = (target_w * target_h > 1920 * 1080) or (fps_int >= 120)

    video_fmt = str(getattr(config, "RECORD_VIDEO_FORMAT", "ddagrab")).strip().lower()
    use_gdigrab = force_gdigrab or (video_fmt == "gdigrab")
    input_source = str(getattr(config, "RECORD_INPUT_SOURCE", "primary")).strip() or "primary"
    output_idx = str(target_mon.output_idx)
    adapter_idx = target_mon.adapter_idx

    # Windowsのコロンを含むパスでFFmpegがクラッシュする原因となるFFREPORT環境変数を排除
    os.environ.pop("FFREPORT", None)

    cmd = [
        ffmpeg_path,
        "-y",
        "-hide_banner",
        "-loglevel",
        "info",
        "-fflags",
        "+genpts",
    ]
    if is_lightweight:
        # フィルタグラフの過剰スレッド生成を抑止してゲームのCPU競合を防止
        cmd.extend(["-filter_threads", "1", "-filter_complex_threads", "1"])

    out_pix_fmt = "nv12" if (is_nvenc or is_amf or is_qsv) else "yuv420p"
    sw_flag = "neighbor" if is_lightweight else "fast_bilinear"

    # NVENCとAMD AMFはD3D11テクスチャの直接入力に対応しているためゼロコピーで処理する
    use_d3d11_direct = (not use_gdigrab) and (is_nvenc or is_amf) and not force_sw_download
    # Intel QSVはGPU上のscale_d3d11で縮小後にnv12へ変換する
    use_qsv_gpu_scale = (not use_gdigrab) and is_qsv and not force_sw_download

    # ddagrabは全分岐でD3D11VAデバイス必須。対象モニターが属するGPUアダプタへ明示バインド
    if not use_gdigrab:
        d3d_dev_spec = f"d3d11va=d3d11:{adapter_idx}" if adapter_idx > 0 else "d3d11va=d3d11"
        cmd.extend(["-init_hw_device", d3d_dev_spec, "-filter_hw_device", "d3d11"])

    # 画面静止時のフレーム枯渇とPTS乖離による多重化キュー溢れを防ぐためdup_framesと0起点PTS正規化を適用
    dda_base = f"ddagrab=output_idx={output_idx}:framerate={fps}:draw_mouse=1:dup_frames=1"
    if use_gdigrab:
        scale_part = f"scale={target_w}:{target_h}:flags={sw_flag}," if needs_scale else ""
        video_filter = f"[1:v]setpts=PTS-STARTPTS,{scale_part}format={out_pix_fmt}[v_out];"
    elif use_d3d11_direct:
        d3d_scale = f",scale_d3d11=w={target_w}:h={target_h}" if needs_scale else ""
        video_filter = f"{dda_base},setpts=PTS-STARTPTS{d3d_scale}[v_out];"
    elif use_qsv_gpu_scale:
        d3d_scale = f",scale_d3d11=w={target_w}:h={target_h}" if needs_scale else ""
        video_filter = (
            f"{dda_base},setpts=PTS-STARTPTS{d3d_scale},"
            f"hwdownload,format=bgra,format=nv12[v_out];"
        )
    else:
        scale_part = f",scale={target_w}:{target_h}:flags={sw_flag}" if needs_scale else ""
        video_filter = (
            f"{dda_base},setpts=PTS-STARTPTS,"
            f"hwdownload,format=bgra{scale_part},format={out_pix_fmt}[v_out];"
        )

    if is_nvenc:
        nv_preset = "p1" if is_lightweight else "p3"
        nv_tune = "ull" if is_lightweight else "ll"
        nv_cq = "26" if is_lightweight else "20"
        nv_maxrate = "8M" if is_lightweight else ("30M" if is_high_bandwidth else "20M")
        nv_bufsize = "16M" if is_lightweight else ("60M" if is_high_bandwidth else "40M")
        encoder_opts = [
            "-c:v", actual_encoder,
            "-preset", nv_preset,
            "-tune", nv_tune,
            "-rc", "vbr",
            "-cq", nv_cq,
            "-b:v", "0",
            "-maxrate", nv_maxrate,
            "-bufsize", nv_bufsize,
            "-bf", "0",
            "-delay", "0",
            "-zerolatency", "1",
            "-g", gop_size,
            "-keyint_min", gop_size,
            "-forced-idr", "1",
        ]
        if is_lightweight:
            encoder_opts.extend(["-spatial-aq", "0", "-temporal-aq", "0", "-rc-lookahead", "0", "-no-scenecut", "1"])
    elif is_amf:
        amf_usage = "ultralowlatency" if is_lightweight else "lowlatency"
        amf_quality = "speed" if is_lightweight else "balanced"
        amf_rc = "vbr_latency" if is_lightweight else "vbr_peak"
        amf_br = "6M" if is_lightweight else ("20M" if is_high_bandwidth else "14M")
        amf_max = "8M" if is_lightweight else ("30M" if is_high_bandwidth else "20M")
        amf_buf = "16M" if is_lightweight else ("60M" if is_high_bandwidth else "40M")
        encoder_opts = [
            "-c:v", actual_encoder,
            "-usage", amf_usage,
            "-quality", amf_quality,
            "-rc", amf_rc,
            "-b:v", amf_br,
            "-maxrate", amf_max,
            "-bufsize", amf_buf,
            "-g", gop_size,
        ]
        if enc_lower == "h264_amf":
            encoder_opts.extend(["-bf", "0"])
    elif is_qsv:
        qsv_preset = "veryfast" if is_lightweight else "fast"
        qsv_br = "6M" if is_lightweight else ("20M" if is_high_bandwidth else "14M")
        qsv_max = "8M" if is_lightweight else ("30M" if is_high_bandwidth else "20M")
        qsv_buf = "16M" if is_lightweight else ("60M" if is_high_bandwidth else "40M")
        encoder_opts = [
            "-c:v", actual_encoder,
            "-preset", qsv_preset,
            "-async_depth", "1" if is_lightweight else "4",
            "-b:v", qsv_br,
            "-maxrate", qsv_max,
            "-bufsize", qsv_buf,
            "-bf", "0",
            "-g", gop_size,
        ]
    else:
        sw_max = "6M" if is_lightweight else ("22M" if is_high_bandwidth else "15M")
        sw_buf = "12M" if is_lightweight else ("44M" if is_high_bandwidth else "30M")
        encoder_opts = [
            "-c:v", actual_encoder,
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-crf", "28" if is_lightweight else "23",
            "-maxrate", sw_max,
            "-bufsize", sw_buf,
            "-g", gop_size,
            "-keyint_min", gop_size,
        ]
        if is_lightweight:
            encoder_opts.extend([
                "-threads", "2",
                "-x264-params", "sliced-threads=0:sync-lookahead=0:rc-lookahead=0:ref=1:subme=0:me=dia:trellis=0",
            ])
        else:
            encoder_opts.extend(["-threads", "4"])

    # panフィルタのチャンネル識別子混在エラー(-EINVAL)を防ぐため番号形式(c0,c1...)で統一
    audio_filter = (
        "[0:a]aresample=async=1000:first_pts=0,asetpts=N/SR/TB,asplit=2[sys_in][mic_in];"
        "[sys_in]pan=stereo|c0=c0|c1=c1[a_sys];"
        "[mic_in]pan=stereo|c0=c2|c1=c3[a_mic]"
    )
    filter_complex = video_filter + audio_filter

    supports_input_tqs, supports_output_tqs = get_thread_queue_size_support(ffmpeg_path)
    tqs_val = "4096" if is_lightweight else "8192"
    gdi_tqs_val = "1024" if is_lightweight else "4096"

    pipe_opts = []
    if supports_input_tqs:
        pipe_opts.extend(["-thread_queue_size", tqs_val])
    pipe_opts.extend([
        "-probesize", "32",
        "-analyzeduration", "0",
        "-f", "f32le",
        "-ar", "48000",
        "-ac", "4",
        "-channel_layout", "quad",
        "-i", "pipe:0",
    ])
    cmd.extend(pipe_opts)

    if use_gdigrab:
        gdi_opts = []
        if supports_input_tqs:
            gdi_opts.extend(["-thread_queue_size", gdi_tqs_val])
        gdi_opts.extend([
            "-use_wallclock_as_timestamps", "1",
            "-f", "gdigrab",
            "-framerate", fps,
            "-draw_mouse", "1",
        ])
        if input_source.lower().startswith("title="):
            gdi_opts.extend(["-i", input_source])
        else:
            # 仮想デスクトップ全体(全モニター)の結合録画を防ぎ、対象モニターの領域のみを切り出す
            crop_w = max(2, (target_mon.width // 2) * 2)
            crop_h = max(2, (target_mon.height // 2) * 2)
            gdi_opts.extend([
                "-offset_x", str(target_mon.x),
                "-offset_y", str(target_mon.y),
                "-video_size", f"{crop_w}x{crop_h}",
                "-i", "desktop",
            ])
        cmd.extend(gdi_opts)

    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", "[v_out]",
        "-map", "[a_sys]",
        "-map", "[a_mic]",
        "-metadata:s:a:0", "title=System Audio",
        "-metadata:s:a:1", "title=Microphone",
    ])
    cmd.extend(encoder_opts)
    cmd.extend([
        "-r", fps,
        "-fps_mode", "cfr",
        "-max_interleave_delta", "10000000",
        "-max_muxing_queue_size", "32768" if is_lightweight else "65536",
    ])
    if supports_output_tqs:
        cmd.extend(["-thread_queue_size", tqs_val])
    cmd.extend([
        "-c:a", "aac",
        "-b:a", "96k" if is_lightweight else "192k",
        "-ar", "48000",
        temp_filepath,
    ])
    return subprocess.list2cmdline(cmd)
