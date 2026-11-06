"""Module: @role: システムスペック（CPU・RAM・GPU・解像度・エンコーダ）の検出と推奨録画設定の判定を担うモジュール"""
import os
import sys
import shutil
from dataclasses import dataclass
from typing import Optional, Tuple, List

@dataclass
class SpecInfo:
    cpu_name: str
    cpu_cores: int
    ram_gb: float
    gpu_names: List[str]
    screen_width: int
    screen_height: int
    available_encoders: List[str]

@dataclass
class RecommendedSettings:
    preset_type: str        # 'high', 'balanced', 'lightweight'
    preset_title: str
    encoder: str
    resolution: str
    fps: str
    lightweight_mode: bool
    reason_key: str
    reason_text: str

def get_cpu_info() -> Tuple[str, int]:
    cpu_count = os.cpu_count() or 4
    cpu_name = "CPU"
    if sys.platform == "win32":
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            val, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            winreg.CloseKey(key)
            if val:
                cpu_name = str(val).strip()
        except Exception:
            pass
    return cpu_name, cpu_count

def get_ram_gb() -> float:
    if sys.platform == "win32":
        try:
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return round(stat.ullTotalPhys / (1024 ** 3), 1)
        except Exception:
            pass
    return 16.0

def get_gpu_names() -> List[str]:
    gpus: List[str] = []
    if sys.platform == "win32":
        try:
            import winreg
            base_path = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base_path)
            num_subkeys, _, _ = winreg.QueryInfoKey(key)
            for i in range(num_subkeys):
                sub_name = winreg.EnumKey(key, i)
                if sub_name.isdigit():
                    try:
                        sub_key = winreg.OpenKey(key, sub_name)
                        driver_desc, _ = winreg.QueryValueEx(sub_key, "DriverDesc")
                        winreg.CloseKey(sub_key)
                        if driver_desc and str(driver_desc) not in gpus:
                            desc_lower = str(driver_desc).lower()
                            if not any(k in desc_lower for k in ("basic render", "remote desktop", "virtual")):
                                gpus.append(str(driver_desc).strip())
                    except Exception:
                        pass
            winreg.CloseKey(key)
        except Exception:
            pass
    return gpus if gpus else ["Standard Graphics"]

def get_screen_resolution() -> Tuple[int, int]:
    if sys.platform == "win32":
        try:
            import ctypes
            user32 = ctypes.windll.user32
            w = user32.GetSystemMetrics(0)
            h = user32.GetSystemMetrics(1)
            if w > 0 and h > 0:
                return w, h
        except Exception:
            pass
    return 1920, 1080

def detect_ffmpeg_path() -> Optional[str]:
    ffmpeg_in_path = shutil.which("ffmpeg")
    if ffmpeg_in_path:
        return ffmpeg_in_path
    
    search_dirs = [
        os.path.abspath("."),
        os.path.dirname(os.path.abspath(__file__)),
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ]
    for d in search_dirs:
        candidate = os.path.join(d, "ffmpeg", "bin", "ffmpeg.exe")
        if os.path.exists(candidate):
            return candidate
        candidate2 = os.path.join(d, "ffmpeg.exe")
        if os.path.exists(candidate2):
            return candidate2
    return None

def detect_specs() -> SpecInfo:
    cpu_name, cpu_cores = get_cpu_info()
    ram_gb = get_ram_gb()
    gpu_names = get_gpu_names()
    width, height = get_screen_resolution()

    ffmpeg_path = detect_ffmpeg_path()
    available_encoders: List[str] = []
    if ffmpeg_path:
        try:
            from recorder.encoder_utils import get_available_encoders
            avail, _ = get_available_encoders(ffmpeg_path)
            available_encoders = avail
        except Exception:
            pass

    # FFmpegが未検出時のフォールバック推定
    if not available_encoders:
        all_gpus_str = " ".join(gpu_names).lower()
        if any(k in all_gpus_str for k in ("nvidia", "geforce", "rtx", "gtx")):
            available_encoders.append("h264_nvenc")
        if any(k in all_gpus_str for k in ("radeon", "amd")):
            available_encoders.append("h264_amf")
        if any(k in all_gpus_str for k in ("intel", "arc", "uhd", "iris")):
            available_encoders.append("h264_qsv")
        available_encoders.append("libx264")

    return SpecInfo(
        cpu_name=cpu_name,
        cpu_cores=cpu_cores,
        ram_gb=ram_gb,
        gpu_names=gpu_names,
        screen_width=width,
        screen_height=height,
        available_encoders=available_encoders,
    )

def evaluate_recommended_settings(specs: SpecInfo, lang: str = "ja") -> RecommendedSettings:
    is_ja = lang == "ja"

    chosen_encoder = "libx264"
    has_hw_encoder = False
    for candidate in ["h264_nvenc", "h264_amf", "h264_qsv"]:
        if candidate in specs.available_encoders:
            chosen_encoder = candidate
            has_hw_encoder = True
            break

    if specs.screen_height >= 1080 and specs.screen_width >= 1920:
        base_res = "1920x1080"
    elif specs.screen_width > 0 and specs.screen_height > 0:
        base_res = f"{specs.screen_width}x{specs.screen_height}"
    else:
        base_res = "1920x1080"

    if has_hw_encoder and specs.ram_gb >= 14.0 and specs.cpu_cores >= 6:
        preset_type = "high"
        preset_title = "高画質・滑らか (最高設定)" if is_ja else "High Quality & Smooth (Best)"
        encoder = chosen_encoder
        resolution = base_res
        fps = "60"
        lightweight = False
        if "nvenc" in encoder:
            reason_key = "spec_reason_nvenc"
            reason_text = "NVIDIA NVENCによる低負荷・高画質なGPU録画が可能です。VALORANTの動作に影響を与えず滑らかに録画できます。" if is_ja else "Hardware encoding via NVIDIA NVENC is available for low-overhead, high-quality recording without impacting game performance."
        elif "amf" in encoder:
            reason_key = "spec_reason_amf"
            reason_text = "AMD AMFハードウェアエンコーダにより、高画質を保ちながらGPUで低負荷に録画できます。" if is_ja else "AMD AMF hardware encoder enables smooth, low-overhead recording on your GPU."
        else:
            reason_key = "spec_reason_qsv"
            reason_text = "Intel QSVハードウェアアクセラレーションにより、効率的に録画できます。" if is_ja else "Intel QSV hardware acceleration provides efficient video encoding."
    elif has_hw_encoder:
        preset_type = "balanced"
        preset_title = "標準バランス" if is_ja else "Balanced"
        encoder = chosen_encoder
        resolution = base_res
        fps = "60"
        lightweight = False
        reason_key = "spec_reason_balanced"
        reason_text = "ハードウェアアクセラレーションを活用し、画質と動作安定性のバランスを最適化しました。" if is_ja else "Optimized for a great balance between video quality and system stability using hardware acceleration."
    else:
        preset_type = "lightweight"
        preset_title = "パフォーマンス優先 (軽量)" if is_ja else "Performance (Lightweight)"
        encoder = "libx264"
        resolution = "1280x720"
        fps = "30" if specs.cpu_cores <= 4 else "60"
        lightweight = True
        reason_key = "spec_reason_cpu"
        reason_text = "ハードウェアエンコーダが検出されなかったため、ゲーム動作への負荷を抑える720p・軽量モードを推奨します。" if is_ja else "No hardware encoder was detected. 720p with lightweight mode is recommended to minimize game FPS drops."

    return RecommendedSettings(
        preset_type=preset_type,
        preset_title=preset_title,
        encoder=encoder,
        resolution=resolution,
        fps=fps,
        lightweight_mode=lightweight,
        reason_key=reason_key,
        reason_text=reason_text,
    )
