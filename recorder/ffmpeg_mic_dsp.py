"""Module: @role: FFmpeg内蔵マイクDSPフィルタチェーン(HPF・RNNoise・NoiseGate)とパスエスケープを担うモジュール"""
import logging
import os
from typing import Optional

from core.config import Config, get_app_base_dir, get_app_data_dir
from recorder.audio_processor_wrapper import get_short_path_name
from recorder.ffmpeg_downloader import (
    ensure_rnnoise_model_async,
    ensure_rnnoise_model_downloaded,
    is_valid_rnnoise_model,
)
from recorder.ffmpeg_probe import supports_arnndn_filter, supports_ffmpeg_filter

def _escape_ffmpeg_filter_option_path(path: str) -> str:
    """FFmpegフィルタグラフの2段階解析(avfilter_graph_parse2 -> av_set_options_string)向けにパスをエスケープする"""
    norm_p = path.replace("\\", "/")
    # 第1段で外側の'...'が剥がれた後に第2段が:と=を再解釈するため、クォート内でも第2段用エスケープが必須
    stage2 = (
        norm_p.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("=", "\\=")
    )
    if "'" not in norm_p:
        return f"'{stage2}'"
    # パスに'を含む場合はクォート外とし、第2段用('含む)の後に第1段用エスケープを重ねる
    stage2_q = stage2.replace("'", "\\'")
    stage1 = (
        stage2_q.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace(",", "\\,")
        .replace(";", "\\;")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace(" ", "\\ ")
    )
    return stage1

def resolve_rnnoise_filter(app_data_dir: Optional[str] = None, allow_download: bool = False) -> str:
    effective_app_data = app_data_dir or get_app_data_dir()
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.path.join(project_root, "bin", "sh.rnnn"),
        os.path.join(get_app_base_dir(), "bin", "sh.rnnn"),
        os.path.join(effective_app_data, "bin", "sh.rnnn"),
    ]
    rnnn_path = None
    for c in candidates:
        if is_valid_rnnoise_model(c):
            rnnn_path = c
            break
    if not rnnn_path:
        if allow_download:
            try:
                rnnn_path = ensure_rnnoise_model_downloaded(effective_app_data)
            except Exception as e:
                logging.warning(f"[FFmpegCommandBuilder] Could not prepare RNNoise model: {e}")
                return ""
        else:
            ensure_rnnoise_model_async(effective_app_data)
            return ""
    if rnnn_path and is_valid_rnnoise_model(rnnn_path):
        short_p = get_short_path_name(os.path.abspath(rnnn_path))
        escaped_val = _escape_ffmpeg_filter_option_path(short_p)
        return f"arnndn=m={escaped_val},"
    return ""

def build_ffmpeg_mic_dsp_chain(
    config: Config,
    ffmpeg_path: str,
    allow_arnndn: bool = True,
    allow_agate: bool = True,
) -> tuple[str, bool]:
    """C++音声DLL不可時にFFmpeg内蔵フィルタで HPF -> RNNoise -> RMS NoiseGate の正順処理を構成する"""
    mic_dev = str(getattr(config, "RECORD_AUDIO_MIC", "") or "").strip().lower()
    if mic_dev == "none":
        return "", False

    filters: list[str] = []
    preprocess_str = str(getattr(config, "RECORD_AUDIO_MIC_PREPROCESS", "SpeexDSP") or "SpeexDSP").strip()
    if preprocess_str and preprocess_str.lower() != "none":
        filters.append("highpass=f=80")

    denoise_str = str(getattr(config, "RECORD_AUDIO_MIC_DENOISE", "None") or "").strip().lower()
    if allow_arnndn and any(k in denoise_str for k in ("rnnoise", "speex", "deepfilter")):
        if supports_arnndn_filter(ffmpeg_path):
            rnnoise_part = resolve_rnnoise_filter(get_app_data_dir(), allow_download=False).rstrip(",")
            if rnnoise_part:
                filters.append(rnnoise_part)

    gate_handled_by_ffmpeg = False
    if allow_agate:
        try:
            gate_pct = float(getattr(config, "RECORD_AUDIO_MIC_NOISE_GATE", "0") or 0)
        except (ValueError, TypeError):
            gate_pct = 0.0
        gate_norm = max(0.0, min(1.0, gate_pct / 100.0))
        if gate_norm > 0.001 and supports_ffmpeg_filter(ffmpeg_path, "agate"):
            # MicMonitorThreadと同一のRMS閾値((threshold/2)^2)・0.01フロアで一致させる
            amp_thresh = max(0.000125, min(1.0, (gate_norm / 2.0) ** 2))
            filters.append(
                f"agate=threshold={amp_thresh:.6f}:ratio=20:range=0.01:attack=10:release=300:detection=rms"
            )
            gate_handled_by_ffmpeg = True

    return (("," + ",".join(filters)) if filters else ""), gate_handled_by_ffmpeg
