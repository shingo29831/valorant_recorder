"""Module: @role: C++ネイティブ録音モジュール(native_recorder.dll)の自動ビルド・ロード・診断ログ収集・Remuxを担うモジュール"""
import ctypes
from datetime import datetime
import logging
import os
import subprocess
import threading
import time
from core.config import is_compiled_binary
from recorder.audio_processor_wrapper import (
    ensure_deepfilter_model_async,
    find_or_download_deepfilter_model,
    is_audio_processor_dll_loaded,
)

_REBUILD_LOCK = threading.Lock()
_REBUILD_CHECKED = False
_NATIVE_DLL_LOADED = False


class RecorderConfig(ctypes.Structure):
    _fields_ = [
        ("mic_gain", ctypes.c_float),
        ("system_gain", ctypes.c_float),
        ("noise_gate_threshold", ctypes.c_float),
        ("mic_device_name", ctypes.c_char_p),
        ("ffmpeg_command", ctypes.c_char_p),
        ("save_file_path", ctypes.c_char_p),
        ("temp_file_path", ctypes.c_char_p),
        ("ffmpeg_path", ctypes.c_char_p),
        ("df_model_path", ctypes.c_char_p),
        ("denoise_mode", ctypes.c_int32),
        ("preprocess_mode", ctypes.c_int32),
        ("low_priority", ctypes.c_int32),
        ("system_device_name", ctypes.c_char_p),
        ("chat_device_name", ctypes.c_char_p),
        ("chat_gain", ctypes.c_float),
        ("chat_enabled", ctypes.c_int32),
    ]


def try_rebuild_native_recorder() -> None:
    """C++のソースファイルがDLLより新しい場合、全DLL未ロード時に1回限りCMakeビルドを実行する。"""
    global _REBUILD_CHECKED
    if is_compiled_binary():
        return
    # ビルド完了までロックを保持し、DLL既ロード時はファイルロック競合・シンボル不整合を防ぐためスキップ
    with _REBUILD_LOCK:
        if _REBUILD_CHECKED or _NATIVE_DLL_LOADED or is_audio_processor_dll_loaded():
            return
        _REBUILD_CHECKED = True

        try:
            base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "cpp", "audio_processor"))
            cpp_sources = [
                os.path.join(base_dir, "NativeRecorder.cpp"),
                os.path.join(base_dir, "NativeRecorder.h"),
                os.path.join(base_dir, "NativeAudioUtils.h"),
                os.path.join(base_dir, "FFmpegProcess.cpp"),
                os.path.join(base_dir, "FFmpegProcess.h"),
                os.path.join(base_dir, "WasapiCapture.cpp"),
                os.path.join(base_dir, "WasapiCapture.h"),
                os.path.join(base_dir, "AudioProcessor.cpp"),
                os.path.join(base_dir, "AudioProcessor.h"),
            ]
            build_dir = os.path.join(base_dir, "build")
            dll_paths = [
                os.path.join(build_dir, "Release", "native_recorder.dll"),
                os.path.join(build_dir, "Release", "audio_processor.dll"),
            ]

            existing_sources = [p for p in cpp_sources if os.path.exists(p)]
            if not existing_sources:
                return

            max_src_mtime = max(os.path.getmtime(p) for p in existing_sources)
            needs_build = any(
                (not os.path.exists(dll_p) or max_src_mtime > os.path.getmtime(dll_p)) for dll_p in dll_paths
            )

            if needs_build:
                logging.info("[FFmpegRecorder] Newer C++ source detected. Rebuilding native_recorder.dll...")
                os.makedirs(build_dir, exist_ok=True)
                res = subprocess.run(
                    ["cmake", "--build", ".", "--config", "Release"],
                    cwd=build_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    timeout=60,
                )
                if res.returncode == 0:
                    logging.info("[FFmpegRecorder] C++ module successfully rebuilt.")
                else:
                    logging.warning(
                        f"[FFmpegRecorder] Auto-rebuild skipped: {res.stderr.decode('utf-8', errors='replace')}"
                    )
        except Exception as e:
            logging.warning(f"[FFmpegRecorder] Auto-rebuild check skipped: {e}")


def load_native_recorder_dll() -> ctypes.CDLL:
    base_build_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "cpp", "audio_processor", "build"))
    possible_paths = [
        os.path.join(base_build_dir, "Release", "native_recorder.dll"),
        os.path.join(base_build_dir, "native_recorder.dll"),
    ]

    dll_path = next((p for p in possible_paths if os.path.exists(p)), None)
    if not dll_path:
        raise FileNotFoundError(
            f"native_recorder.dll not found in {base_build_dir}. Please ensure C++ build was successful."
        )

    dll_dir = os.path.dirname(dll_path)
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(dll_dir)

    load_with_altered_search_path = 8
    for dep_dll in ("deep_filter.dll", "libspeexdsp.dll", "audio_processor.dll"):
        dep_path = os.path.join(dll_dir, dep_dll)
        if os.path.exists(dep_path):
            try:
                ctypes.CDLL(dep_path, winmode=load_with_altered_search_path)
            except Exception as e:
                logging.warning(f"[FFmpegRecorder] Failed to preload {dep_dll}: {e}")
        else:
            logging.warning(f"[FFmpegRecorder] Dependency not found: {dep_path}")

    global _NATIVE_DLL_LOADED
    with _REBUILD_LOCK:
        try:
            native_lib = ctypes.CDLL(dll_path, winmode=load_with_altered_search_path)
            _NATIVE_DLL_LOADED = True
        except Exception as e:
            logging.error(f"[FFmpegRecorder] Failed to load native_recorder.dll: {e}")
            raise RuntimeError(f"Failed to load native recorder module: {e}")

    native_lib.Recorder_Start.argtypes = [ctypes.POINTER(RecorderConfig)]
    native_lib.Recorder_Start.restype = ctypes.c_bool
    native_lib.Recorder_Stop.argtypes = []
    native_lib.Recorder_IsRunning.restype = ctypes.c_bool
    if hasattr(native_lib, "Recorder_GetStartupState"):
        native_lib.Recorder_GetStartupState.argtypes = []
        native_lib.Recorder_GetStartupState.restype = ctypes.c_int32
    native_lib.Recorder_GetLastError.restype = ctypes.c_char_p
    if hasattr(native_lib, "Recorder_SetLowPriority"):
        native_lib.Recorder_SetLowPriority.argtypes = [ctypes.c_bool]
        native_lib.Recorder_SetLowPriority.restype = None
    if hasattr(native_lib, "Recorder_GetActualStartTimeMs"):
        native_lib.Recorder_GetActualStartTimeMs.argtypes = []
        native_lib.Recorder_GetActualStartTimeMs.restype = ctypes.c_int64
    if hasattr(native_lib, "Recorder_GetActualEndTimeMs"):
        native_lib.Recorder_GetActualEndTimeMs.argtypes = []
        native_lib.Recorder_GetActualEndTimeMs.restype = ctypes.c_int64
    if hasattr(native_lib, "Recorder_SupportsChatTrack"):
        native_lib.Recorder_SupportsChatTrack.argtypes = []
        native_lib.Recorder_SupportsChatTrack.restype = ctypes.c_bool

    return native_lib


def resolve_deepfilter_model_path(denoise_mode: int) -> str:
    if denoise_mode != 1:
        return ""
    # 手動録画(UIスレッド)等のホットパスをブロックしないためローカルのみ即時解決し、不在時は裏で取得
    model_path = find_or_download_deepfilter_model(allow_download=False)
    if not model_path:
        ensure_deepfilter_model_async()
    return model_path


def collect_and_save_diagnostic(native_lib: ctypes.CDLL, save_dir: str, err_msg: str) -> None:
    # PyInstaller環境の一時フォルダ消失を防ぎ永続ログディレクトリへ保存
    app_data_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ValorantRecorder")
    os.makedirs(app_data_dir, exist_ok=True)
    diagnostic_path = os.path.join(app_data_dir, "recording_diagnostic.log")
    try:
        with open(diagnostic_path, "w", encoding="utf-8") as f:
            f.write("=== Recording Error Diagnostic ===\n")
            f.write(f"Timestamp: {datetime.now()}\n")
            f.write(f"Error Message: {err_msg}\n\n")

            try:
                last_err = native_lib.Recorder_GetLastError().decode("utf-8", errors="replace")
                f.write(f"[NativeRecorder Last Error]: {last_err}\n\n")
            except Exception as e:
                f.write(f"[NativeRecorder Last Error]: (Failed to read: {e})\n\n")

            log_targets = [
                ("native_recorder_debug.log", os.path.join(save_dir, "native_recorder_debug.log")),
                ("native_ffmpeg.log", os.path.join(save_dir, "native_ffmpeg.log")),
                (
                    "app.log",
                    os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ValorantRecorder", "app.log"),
                ),
            ]
            for label, path in log_targets:
                f.write(f"--- {label} ({path}) ---\n")
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8", errors="replace") as lf:
                        f.write(lf.read()[-3000:])
                else:
                    f.write("File not found.\n")
                f.write("\n\n")
        logging.info(f"[FFmpegRecorder] Diagnostic log saved to: {diagnostic_path}")
    except Exception as write_err:
        logging.error(f"[FFmpegRecorder] Failed to write diagnostic log: {write_err}")


def remux_mkv_to_mp4(ffmpeg_path: str, temp_filepath: str, current_filepath: str, actual_encoder: str) -> None:
    if not (temp_filepath and current_filepath and os.path.exists(temp_filepath)):
        return

    for _ in range(6):
        try:
            with open(temp_filepath, "r+b"):
                break
        except IOError:
            time.sleep(0.5)

    remux_temp_path = current_filepath + ".tmp"
    try:
        logging.info(f"[FFmpegRecorder] Remuxing MKV to MP4: {temp_filepath} -> {current_filepath}")
        remux_cmd = [
            ffmpeg_path,
            "-y",
            "-i", temp_filepath,
            "-map", "0",
            "-c", "copy",
        ]
        if "hevc" in actual_encoder.lower():
            remux_cmd.extend(["-tag:v", "hvc1"])
        remux_cmd.extend(["-movflags", "+faststart", "-f", "mp4", remux_temp_path])

        creationflags = 0
        startupinfo = None
        if os.name == "nt":
            below_normal_priority_class = 0x00004000
            creationflags = subprocess.CREATE_NO_WINDOW | below_normal_priority_class
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE

        remux_process = subprocess.run(
            remux_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            startupinfo=startupinfo,
        )
        if remux_process.returncode == 0 and os.path.exists(remux_temp_path):
            os.replace(remux_temp_path, current_filepath)
            try:
                os.remove(temp_filepath)
            except Exception as rm_err:
                logging.warning(f"[FFmpegRecorder] Could not remove temp MKV: {rm_err}")
            logging.info("[FFmpegRecorder] Remux completed successfully.")
        else:
            if os.path.exists(remux_temp_path):
                try:
                    os.remove(remux_temp_path)
                except OSError:
                    pass
            logging.error(f"[FFmpegRecorder] Remux failed with code {remux_process.returncode}")
    except Exception as e:
        if os.path.exists(remux_temp_path):
            try:
                os.remove(remux_temp_path)
            except OSError:
                pass
        logging.error(f"[FFmpegRecorder] Remux exception: {e}")
