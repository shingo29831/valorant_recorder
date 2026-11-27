"""Module: @role: 録画セッションのエンコーダ決定・音声DSP設定解決・RecorderConfig構造体構築・FFmpegログ解析を担うモジュール"""

import os
import re
import time
from typing import Any

from core.config import Config
from recorder.audio_processor_wrapper import AudioProcessorWrapper
from recorder.encoder_utils import get_available_encoders, test_encoder
from recorder.ffmpeg_command_builder import build_ffmpeg_mic_dsp_chain
from recorder.native_bridge import RecorderConfig, resolve_deepfilter_model_path

_ARNNDN_ERROR_MARKERS: tuple[str, ...] = (
    "error",
    "failed",
    "invalid",
    "no such filter",
    "cannot",
    "unable",
    "could not",
    "unrecognized",
    "not found",
    "missing",
    "permission denied",
    "corrupt",
    "not a valid",
    "option",
)

_DXGI_LOSS_MARKERS: tuple[str, ...] = (
    "887a0026",
    "dxgi_error_access_lost",
    "acquirenextframe failed",
    "failed to acquire next frame",
    "duplicateoutput failed",
)

def _parse_float(raw_val: Any, default: float) -> float:
    try:
        return float(raw_val)
    except (ValueError, TypeError):
        return default

def determine_encoder(config: Config, ffmpeg_path: str) -> str:
    encoder = config.RECORD_ENCODER
    if encoder:
        success, _ = test_encoder(ffmpeg_path, encoder)
        if success:
            return encoder

    available, _ = get_available_encoders(ffmpeg_path)
    best_encoder = available[0] if available else "libx264"
    if config.RECORD_ENCODER != best_encoder:
        config.RECORD_ENCODER = best_encoder
        try:
            config.save()
        except Exception:
            pass
    return best_encoder

def should_use_ffmpeg_mic_dsp(backend_mode: str) -> bool:
    return backend_mode == "python" and not AudioProcessorWrapper.is_available()

def wants_ffmpeg_arnndn(config: Config) -> bool:
    mic_dev = str(getattr(config, "RECORD_AUDIO_MIC", "") or "").strip().lower()
    if mic_dev == "none":
        return False
    denoise_str = str(getattr(config, "RECORD_AUDIO_MIC_DENOISE", "None") or "None").strip().lower()
    return any(k in denoise_str for k in ("rnnoise", "speex", "deepfilter"))

def is_gate_delegated_to_ffmpeg(
    config: Config,
    ffmpeg_path: str,
    backend_mode: str,
    allow_arnndn: bool = True,
) -> bool:
    if not should_use_ffmpeg_mic_dsp(backend_mode):
        return False
    _, gate_handled = build_ffmpeg_mic_dsp_chain(
        config,
        ffmpeg_path,
        allow_arnndn=allow_arnndn,
        allow_agate=True,
    )
    return gate_handled

def prepare_ffmpeg_log_for_attempt(save_dir: str) -> float:
    """旧セッションのログ残存による誤判定を防ぐため試行開始前にnative_ffmpeg.logを消去し開始時刻を返す"""
    ffmpeg_log_file = os.path.join(save_dir, "native_ffmpeg.log")
    if os.path.exists(ffmpeg_log_file):
        try:
            os.remove(ffmpeg_log_file)
        except OSError:
            pass
    return time.time()

def did_last_ffmpeg_hit_arnndn_error(save_dir: str, min_mtime: float = 0.0) -> bool:
    """今回試行で出力されたFFmpegログのエラー文脈行からarnndn起因失敗を厳格判定する"""
    ffmpeg_log_file = os.path.join(save_dir, "native_ffmpeg.log")
    try:
        if not os.path.isfile(ffmpeg_log_file) or os.path.getsize(ffmpeg_log_file) == 0:
            return False
        if min_mtime > 0.0 and os.path.getmtime(ffmpeg_log_file) < (min_mtime - 0.5):
            return False
        with open(ffmpeg_log_file, "r", encoding="utf-8", errors="replace") as lf:
            tail = lf.read()[-6144:].lower()
    except OSError:
        return False

    lines = [line.strip() for line in tail.splitlines() if line.strip()]
    for idx, line in enumerate(lines):
        if ("arnndn" not in line) and ("sh.rnnn" not in line):
            continue
        window = line
        if idx + 1 < len(lines):
            window = window + " " + lines[idx + 1]
        if any(m in window for m in _ARNNDN_ERROR_MARKERS):
            return True
    return False

def did_last_ffmpeg_hit_dxgi_loss(save_dir: str) -> bool:
    """直前のFFmpeg終了がフルスクリーン裏画面切替等によるDXGIアクセス消失かをエラー固有語句で厳格判定する"""
    ffmpeg_log_file = os.path.join(save_dir, "native_ffmpeg.log")
    if not os.path.exists(ffmpeg_log_file):
        return False
    try:
        with open(ffmpeg_log_file, "r", encoding="utf-8", errors="replace") as lf:
            tail = lf.read()[-4096:].lower()
        if any(m in tail for m in _DXGI_LOSS_MARKERS):
            return True
        if "output duplication" in tail and ("lost" in tail or "failed" in tail or "887a" in tail):
            return True
        return False
    except OSError:
        return False

def build_recorder_config(
    config: Config,
    cmd_str: str,
    current_filepath: str,
    temp_filepath: str,
    ffmpeg_path: str,
    backend_mode: str,
    use_chat_track: bool,
    allow_arnndn: bool,
) -> tuple[RecorderConfig, list[bytes]]:
    is_lightweight = bool(getattr(config, "LIGHTWEIGHT_MODE", False))
    chat_device = str(getattr(config, "RECORD_AUDIO_CHAT", "None") or "None").strip()

    # FFmpeg側agateへ委譲する場合はHPF->Denoise->NoiseGateの順序を守りPython側ゲートを二重適用しない
    if is_gate_delegated_to_ffmpeg(config, ffmpeg_path, backend_mode, allow_arnndn=allow_arnndn):
        gate_level = 0.0
    else:
        gate_level = max(0.0, min(1.0, _parse_float(getattr(config, "RECORD_AUDIO_MIC_NOISE_GATE", "0"), 0.0) / 100.0))

    denoise_str = str(getattr(config, "RECORD_AUDIO_MIC_DENOISE", "None") or "None").lower()
    if "deepfilter" in denoise_str:
        denoise_mode = 2 if is_lightweight else 1
    elif "rnnoise" in denoise_str or "speex" in denoise_str:
        denoise_mode = 2
    else:
        denoise_mode = 0

    preprocess_str = str(getattr(config, "RECORD_AUDIO_MIC_PREPROCESS", "SpeexDSP") or "SpeexDSP")
    preprocess_mode = 1 if preprocess_str == "SpeexDSP" else 0

    raw_mic_gain = _parse_float(getattr(config, "RECORD_AUDIO_MIC_GAIN", 1.0), 1.0)
    mic_gain = max(0.0, raw_mic_gain / 100.0 if raw_mic_gain > 10.0 else raw_mic_gain)
    raw_sys_gain = _parse_float(
        getattr(config, "RECORD_AUDIO_SYSTEM_GAIN", getattr(config, "RECORD_AUDIO_SYS_GAIN", 1.0)),
        1.0,
    )
    sys_gain = max(0.0, raw_sys_gain / 100.0 if raw_sys_gain > 10.0 else raw_sys_gain)
    raw_chat_gain = _parse_float(getattr(config, "RECORD_AUDIO_CHAT_GAIN", 1.0), 1.0)
    chat_gain = max(0.0, raw_chat_gain / 100.0 if raw_chat_gain > 10.0 else raw_chat_gain)

    sys_device = str(getattr(config, "RECORD_AUDIO_SYSTEM", "default") or "default").strip()
    if sys_device.lower() == "none":
        os.environ["VALORECO_SYS_AUDIO_DEVICE"] = "__NONE__"
        sys_device_bytes = b"__NONE__"
        sys_gain = 0.0
    elif not sys_device or sys_device.lower() == "default":
        os.environ["VALORECO_SYS_AUDIO_DEVICE"] = ""
        sys_device_bytes = b""
    else:
        os.environ["VALORECO_SYS_AUDIO_DEVICE"] = sys_device
        sys_device_bytes = sys_device.encode("utf-8")

    if not use_chat_track or chat_device.lower() == "none":
        chat_device_bytes = b"__NONE__"
        chat_gain = 0.0
    elif not chat_device or chat_device.lower() == "default":
        chat_device_bytes = b""
    else:
        chat_device_bytes = chat_device.encode("utf-8")

    mic_device_raw = str(getattr(config, "RECORD_AUDIO_MIC", getattr(config, "RECORD_AUDIO_MIC_DEVICE", "")) or "")
    mic_device_stripped = mic_device_raw.strip()
    if mic_device_stripped.lower() == "none":
        mic_device_bytes = b"__NONE__"
    elif not mic_device_stripped or mic_device_stripped.lower() == "default":
        mic_device_bytes = b""
    else:
        mic_device_bytes = mic_device_raw.encode("utf-8")

    cmd_bytes = cmd_str.encode("utf-8")
    save_bytes = (current_filepath or "").encode("utf-8")
    temp_bytes = (temp_filepath or "").encode("utf-8")
    ffmpeg_bytes = ffmpeg_path.encode("utf-8")
    df_model_bytes = resolve_deepfilter_model_path(denoise_mode).encode("utf-8")

    # ctypes.c_char_p 参照先バイト列のGC解放を防ぐため生存リストを返却
    keepalive = [
        sys_device_bytes,
        chat_device_bytes,
        mic_device_bytes,
        cmd_bytes,
        save_bytes,
        temp_bytes,
        ffmpeg_bytes,
        df_model_bytes,
    ]

    config_struct = RecorderConfig()
    config_struct.mic_gain = mic_gain
    config_struct.system_gain = sys_gain
    config_struct.noise_gate_threshold = gate_level
    config_struct.denoise_mode = denoise_mode
    config_struct.preprocess_mode = preprocess_mode
    config_struct.low_priority = 1 if is_lightweight else 0
    config_struct.system_device_name = sys_device_bytes
    config_struct.chat_device_name = chat_device_bytes
    config_struct.chat_gain = chat_gain
    config_struct.chat_enabled = 1 if use_chat_track else 0
    config_struct.mic_device_name = mic_device_bytes
    config_struct.ffmpeg_command = cmd_bytes
    config_struct.save_file_path = save_bytes
    config_struct.temp_file_path = temp_bytes
    config_struct.ffmpeg_path = ffmpeg_bytes
    config_struct.df_model_path = df_model_bytes
    return config_struct, keepalive

def collect_segments_for_filepath(target_filepath: str) -> list[dict[str, Any]]:
    parent_dir = os.path.dirname(target_filepath) or "."
    base_name = os.path.splitext(os.path.basename(target_filepath))[0]
    segments: list[dict[str, Any]] = []
    primary_mkv = os.path.join(parent_dir, base_name + ".mkv")
    if os.path.exists(primary_mkv):
        segments.append({"path": primary_mkv})
    part_re = re.compile(rf"^{re.escape(base_name)}_part(\d+)\.mkv$", re.IGNORECASE)
    part_files: list[tuple[int, str]] = []
    try:
        for fname in os.listdir(parent_dir):
            m = part_re.match(fname)
            if m:
                part_files.append((int(m.group(1)), os.path.join(parent_dir, fname)))
    except OSError:
        pass
    for _, full_p in sorted(part_files, key=lambda x: x[0]):
        segments.append({"path": full_p})
    return segments
