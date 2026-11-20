"""Module: @role: C++ネイティブ録音モジュール(native_recorder.dll)の自動ビルド・ロード・診断ログ収集・Remuxを担うモジュール"""
import ctypes
from datetime import datetime
import logging
import os
import re
import subprocess
import threading
import time
from typing import Any, Optional
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


_FFMPEG_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def _probe_media_duration_ms(ffmpeg_path: str, media_path: str) -> Optional[int]:
    """FFmpegでセグメントの正確な再生長(ms)を計測する"""
    if not (ffmpeg_path and media_path and os.path.exists(media_path)):
        return None
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        res = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-i", media_path],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
            timeout=10.0,
        )
        stderr_text = res.stderr.decode("utf-8", errors="replace") if res.stderr else ""
        m = _FFMPEG_DURATION_RE.search(stderr_text)
        if m:
            hours = int(m.group(1))
            minutes = int(m.group(2))
            seconds = float(m.group(3))
            dur_ms = int(round((hours * 3600 + minutes * 60 + seconds) * 1000.0))
            if dur_ms > 0:
                return dur_ms
    except Exception as e:
        logging.debug(f"[FFmpegRecorder] Failed to probe duration for {media_path}: {e}")
    return None


def _build_subprocess_window_opts() -> tuple[int, Any]:
    if os.name != "nt":
        return 0, None
    below_normal_priority_class = 0x00004000
    creationflags = subprocess.CREATE_NO_WINDOW | below_normal_priority_class
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return creationflags, startupinfo


def _build_timeline_segments_map(
    ffmpeg_path: str,
    valid_segments: list[dict[str, Any]],
) -> list[dict[str, int]]:
    """複数セグメント結合時の実時刻→動画再生時刻マッピング用メタデータを構築する"""
    if len(valid_segments) < 2:
        return []
    mapped: list[dict[str, int]] = []
    video_cursor_ms = 0
    for seg in valid_segments:
        wall_start = int(seg.get("start_time_ms") or 0)
        wall_end = int(seg.get("end_time_ms") or 0)
        probed_ms = _probe_media_duration_ms(ffmpeg_path, seg["path"])
        fallback_ms = max(0, wall_end - wall_start) if (wall_start > 0 and wall_end > wall_start) else 0
        dur_ms = probed_ms if (probed_ms and probed_ms > 0) else fallback_ms
        if wall_start > 0 and wall_end <= wall_start and dur_ms > 0:
            wall_end = wall_start + dur_ms
        mapped.append(
            {
                "wall_start_ms": wall_start,
                "wall_end_ms": wall_end,
                "video_start_ms": video_cursor_ms,
                "duration_ms": dur_ms,
            }
        )
        video_cursor_ms += dur_ms
    return mapped


def _are_segments_stream_compatible(segments: list[dict[str, Any]]) -> bool:
    """無劣化concat(-c copy)でデコード破綻しない同質ストリーム群かを厳格判定する"""
    if len(segments) < 2:
        return True
    first = segments[0]
    keys = ("encoder", "capture_mode", "width", "height", "fps", "audio_tracks")
    for k in keys:
        if first.get(k) in (None, "", 0):
            return False
    ref = tuple(first[k] for k in keys)
    return all(tuple(seg.get(k) for k in keys) == ref for seg in segments[1:])


def remux_mkv_segments_to_mp4(
    ffmpeg_path: str,
    segment_entries: list[Any],
    current_filepath: str,
    actual_encoder: str,
) -> list[dict[str, int]]:
    """同一録画セッション内のMKVセグメント群を1本のMP4へ安全に結合し、タイムライン補正用セグメント情報を返す"""
    if not (segment_entries and current_filepath):
        return []

    normalized_entries: list[dict[str, Any]] = []
    all_raw_paths: list[str] = []
    seen: set[str] = set()
    for item in segment_entries:
        if isinstance(item, str):
            entry: dict[str, Any] = {"path": item}
        elif isinstance(item, dict):
            entry = dict(item)
        else:
            continue
        path = str(entry.get("path") or "")
        if not path:
            continue
        norm = os.path.normcase(os.path.abspath(path))
        if norm in seen:
            continue
        seen.add(norm)
        all_raw_paths.append(path)
        for _ in range(4):
            if os.path.exists(path):
                try:
                    with open(path, "r+b"):
                        break
                except IOError:
                    time.sleep(0.3)
            else:
                break
        try:
            if os.path.exists(path) and os.path.getsize(path) > 0:
                normalized_entries.append(entry)
        except OSError:
            pass

    if not normalized_entries:
        return []

    if len(normalized_entries) == 1:
        only_seg = normalized_entries[0]
        seg_encoder = str(only_seg.get("encoder") or actual_encoder or "")
        remux_mkv_to_mp4(ffmpeg_path, only_seg["path"], current_filepath, seg_encoder)
        for p in all_raw_paths:
            if p != only_seg["path"] and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        return []

    timeline_segments = _build_timeline_segments_map(ffmpeg_path, normalized_entries)
    concat_list_path = current_filepath + ".concat.txt"
    remux_temp_path = current_filepath + ".tmp"
    creationflags, startupinfo = _build_subprocess_window_opts()
    merged_ok = False

    try:
        # 完全に同一のエンコーダ・キャプチャ方式・解像度・FPS・音声構成のときのみ無劣化concatを実行
        if _are_segments_stream_compatible(normalized_entries):
            with open(concat_list_path, "w", encoding="utf-8") as cf:
                for seg in normalized_entries:
                    escaped = os.path.abspath(seg["path"]).replace("\\", "/").replace("'", "'\\''")
                    cf.write(f"file '{escaped}'\n")

            common_encoder = str(normalized_entries[0].get("encoder") or actual_encoder or "")
            logging.info(
                f"[FFmpegRecorder] Stream-copy concatenating {len(normalized_entries)} compatible MKV segments: {current_filepath}"
            )
            concat_cmd = [
                ffmpeg_path,
                "-y",
                "-fflags", "+genpts",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_list_path,
                "-map", "0",
                "-c", "copy",
            ]
            if "hevc" in common_encoder.lower():
                concat_cmd.extend(["-tag:v", "hvc1"])
            concat_cmd.extend(["-movflags", "+faststart", "-f", "mp4", remux_temp_path])

            res = subprocess.run(
                concat_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=creationflags,
                startupinfo=startupinfo,
            )
            if res.returncode == 0 and os.path.exists(remux_temp_path) and os.path.getsize(remux_temp_path) > 0:
                merged_ok = True
            else:
                err_tail = res.stderr.decode("utf-8", errors="replace")[-500:] if res.stderr else ""
                logging.warning(
                    f"[FFmpegRecorder] Stream-copy concat failed (code={res.returncode}): {err_tail}"
                )

        # 異種ストリーム混在時・コピー失敗時は個別入力＋concatフィルタで正規化・再エンコード結合し黒画面/音ズレを防止
        if not merged_ok:
            if os.path.exists(remux_temp_path):
                try:
                    os.remove(remux_temp_path)
                except OSError:
                    pass

            target_w = int(normalized_entries[0].get("width") or 1920)
            target_h = int(normalized_entries[0].get("height") or 1080)
            target_fps = max(1, int(normalized_entries[0].get("fps") or 60))
            audio_tracks = min(
                (int(seg.get("audio_tracks") or 2) for seg in normalized_entries),
                default=2,
            )
            audio_tracks = max(1, audio_tracks)

            filter_parts: list[str] = []
            concat_inputs: list[str] = []
            reencode_cmd: list[str] = [ffmpeg_path, "-y", "-hide_banner", "-loglevel", "error"]
            for idx, seg in enumerate(normalized_entries):
                reencode_cmd.extend(["-i", seg["path"]])
                filter_parts.append(
                    f"[{idx}:v:0]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
                    f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
                    f"fps={target_fps},format=yuv420p[v{idx}]"
                )
                concat_inputs.append(f"[v{idx}]")
                for a_idx in range(audio_tracks):
                    filter_parts.append(
                        f"[{idx}:a:{a_idx}]aresample=48000:async=1,"
                        f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{idx}_{a_idx}]"
                    )
                    concat_inputs.append(f"[a{idx}_{a_idx}]")

            out_audio_labels = [f"[aout{a_idx}]" for a_idx in range(audio_tracks)]
            filter_parts.append(
                f"{''.join(concat_inputs)}concat=n={len(normalized_entries)}:v=1:a={audio_tracks}"
                f"[vout]{''.join(out_audio_labels)}"
            )
            filter_complex = ";".join(filter_parts)

            reencode_cmd.extend(["-filter_complex", filter_complex, "-map", "[vout]"])
            for label in out_audio_labels:
                reencode_cmd.extend(["-map", label])

            track_titles = ("System Audio", "Microphone", "Voice Chat")
            for a_idx in range(audio_tracks):
                if a_idx < len(track_titles):
                    reencode_cmd.extend([f"-metadata:s:a:{a_idx}", f"title={track_titles[a_idx]}"])

            reencode_cmd.extend([
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "21",
                "-r", str(target_fps),
                "-fps_mode", "cfr",
                "-c:a", "aac",
                "-b:a", "192k",
                "-ar", "48000",
                "-movflags", "+faststart",
                "-f", "mp4",
                remux_temp_path,
            ])

            logging.info(
                f"[FFmpegRecorder] Filter-concat re-encoding {len(normalized_entries)} mixed MKV segments: {current_filepath}"
            )
            res = subprocess.run(
                reencode_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=creationflags,
                startupinfo=startupinfo,
            )
            if res.returncode == 0 and os.path.exists(remux_temp_path) and os.path.getsize(remux_temp_path) > 0:
                merged_ok = True
            else:
                err_tail = res.stderr.decode("utf-8", errors="replace")[-800:] if res.stderr else ""
                logging.error(
                    f"[FFmpegRecorder] Filter-concat re-encode failed (code={res.returncode}): {err_tail}"
                )

        if merged_ok:
            os.replace(remux_temp_path, current_filepath)
            for p in all_raw_paths:
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError as rm_err:
                        logging.warning(f"[FFmpegRecorder] Could not remove temp segment MKV: {rm_err}")
            logging.info("[FFmpegRecorder] Segment concat & remux completed successfully.")
            return timeline_segments

        logging.error("[FFmpegRecorder] All concat strategies failed; falling back to largest valid segment.")
        if os.path.exists(remux_temp_path):
            try:
                os.remove(remux_temp_path)
            except OSError:
                pass
        largest_seg = max(normalized_entries, key=lambda s: os.path.getsize(s["path"]))
        remux_mkv_to_mp4(
            ffmpeg_path,
            largest_seg["path"],
            current_filepath,
            str(largest_seg.get("encoder") or actual_encoder or ""),
        )
        return []
    except Exception as e:
        if os.path.exists(remux_temp_path):
            try:
                os.remove(remux_temp_path)
            except OSError:
                pass
        logging.error(f"[FFmpegRecorder] Segment concat exception: {e}")
        return []
    finally:
        if os.path.exists(concat_list_path):
            try:
                os.remove(concat_list_path)
            except OSError:
                pass
