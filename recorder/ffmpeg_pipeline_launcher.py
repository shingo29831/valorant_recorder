"""Module: @role: FFmpeg録画パイプラインの起動検証とD3D11/GDI/ソフトウェアエンコーダへの段階的フォールバック制御を担うモジュール"""

import ctypes
import logging
import time
from dataclasses import dataclass
from typing import Any

from core.config import Config
from recorder.ffmpeg_command_builder import build_ffmpeg_command
from recorder.ffmpeg_recorder_config import (
    build_recorder_config,
    did_last_ffmpeg_hit_arnndn_error,
    prepare_ffmpeg_log_for_attempt,
    should_use_ffmpeg_mic_dsp,
)
from recorder.native_bridge import RecorderConfig

STARTUP_STATE_RUNNING = 2
STARTUP_STATE_FAILED = 3
STARTUP_VERIFY_TIMEOUT_SEC = 10.0

@dataclass
class PipelineLaunchResult:
    started_ok: bool
    used_encoder: str
    used_capture: str
    final_encoder: str
    disable_ffmpeg_arnndn: bool

def try_start_and_verify(native_lib: Any, config_struct: RecorderConfig) -> bool:
    if not native_lib.Recorder_Start(ctypes.byref(config_struct)):
        return False
    if hasattr(native_lib, "Recorder_GetStartupState"):
        # DeepFilterNet展開〜FFmpeg/D3D11起動成否が確定するまで待機し、フォールバック判定を正確に機能させる
        deadline = time.monotonic() + STARTUP_VERIFY_TIMEOUT_SEC
        while time.monotonic() < deadline:
            state = int(native_lib.Recorder_GetStartupState())
            if state == STARTUP_STATE_FAILED or not native_lib.Recorder_IsRunning():
                return False
            if state == STARTUP_STATE_RUNNING:
                for _ in range(6):
                    time.sleep(0.05)
                    if not native_lib.Recorder_IsRunning():
                        return False
                return True
            time.sleep(0.05)
        return False
    for _ in range(7):
        time.sleep(0.1)
        if not native_lib.Recorder_IsRunning():
            return False
    return bool(native_lib.Recorder_IsRunning())

def _resolve_ddagrab_mode(sw_dl: bool, enc: str) -> str:
    if sw_dl or enc == "libx264":
        return "ddagrab_sw"
    enc_l = enc.lower()
    if "nvenc" in enc_l or "amf" in enc_l:
        return "ddagrab_d3d11"
    if "qsv" in enc_l:
        return "ddagrab_qsv"
    return "ddagrab_sw"

def launch_pipeline_with_fallbacks(
    config: Config,
    native_lib: Any,
    backend_mode: str,
    ffmpeg_path: str,
    initial_encoder: str,
    current_filepath: str,
    temp_filepath: str,
    use_chat_track: bool,
    sw_download_fallback_encoders: set[str],
    disable_ffmpeg_arnndn: bool,
    allow_gdigrab_fallback: bool = True,
) -> PipelineLaunchResult:
    """録画パイプラインを起動し、起動結果と最終エンコーダ/キャプチャ方式を返す"""
    save_dir = config.SAVE_DIR
    is_lightweight = bool(getattr(config, "LIGHTWEIGHT_MODE", False))
    force_sw = initial_encoder in sw_download_fallback_encoders
    video_fmt = str(getattr(config, "RECORD_VIDEO_FORMAT", "ddagrab") or "ddagrab").strip().lower()
    use_gdi_initial = video_fmt == "gdigrab"
    use_ffmpeg_mic_dsp = should_use_ffmpeg_mic_dsp(backend_mode)

    current_encoder = initial_encoder
    arnndn_disabled = disable_ffmpeg_arnndn

    def _build_cmd(enc: str, force_sw_dl: bool, force_gdi: bool, allow_arnndn_cur: bool) -> str:
        return build_ffmpeg_command(
            config=config,
            ffmpeg_path=ffmpeg_path,
            actual_encoder=enc,
            temp_filepath=temp_filepath or "",
            force_sw_download=force_sw_dl,
            force_gdigrab=force_gdi,
            enable_chat_track=use_chat_track,
            use_ffmpeg_mic_dsp=use_ffmpeg_mic_dsp,
            allow_arnndn=allow_arnndn_cur,
            allow_agate=True,
        )

    def _stop_and_flush() -> None:
        try:
            native_lib.Recorder_Stop()
        except Exception:
            pass

    def _run_attempt(config_struct: RecorderConfig, used_arnndn: bool) -> tuple[bool, bool]:
        nonlocal arnndn_disabled
        attempt_ts = prepare_ffmpeg_log_for_attempt(save_dir)
        if try_start_and_verify(native_lib, config_struct):
            return True, False
        # native_ffmpeg.log書き出し完了を保証してからエラー判定を行う
        _stop_and_flush()
        if used_arnndn and did_last_ffmpeg_hit_arnndn_error(save_dir, min_mtime=attempt_ts):
            logging.warning(
                "[FFmpegRecorder] FFmpeg arnndn filter error detected. "
                "Disabling arnndn and retrying primary pipeline..."
            )
            arnndn_disabled = True
            return False, True
        return False, False

    def _run_fallback_sequence(allow_arnndn_cur: bool) -> tuple[bool, str, str, bool]:
        nonlocal current_encoder
        current_encoder = initial_encoder
        cmd_str = _build_cmd(current_encoder, force_sw, use_gdi_initial, allow_arnndn_cur)
        used_arnndn = "arnndn=" in cmd_str
        config_struct, keepalive = build_recorder_config(
            config=config,
            cmd_str=cmd_str,
            current_filepath=current_filepath,
            temp_filepath=temp_filepath,
            ffmpeg_path=ffmpeg_path,
            backend_mode=backend_mode,
            use_chat_track=use_chat_track,
            allow_arnndn=allow_arnndn_cur,
        )
        used_encoder = current_encoder
        used_capture = "gdigrab" if use_gdi_initial else _resolve_ddagrab_mode(force_sw, used_encoder)

        logging.info(
            f"[FFmpegRecorder] Starting Recorder ({backend_mode}). Encoder: {used_encoder}, "
            f"Capture: {used_capture}, Lightweight: {is_lightweight}, ChatTrack: {use_chat_track}"
        )
        logging.info(f"[FFmpegRecorder] Command: {cmd_str}")

        started_ok, arnndn_err = _run_attempt(config_struct, used_arnndn)
        if started_ok or arnndn_err:
            return started_ok, used_encoder, used_capture, arnndn_err

        def _retry_with_command(
            label: str,
            enc: str,
            force_sw_dl: bool,
            force_gdi: bool,
        ) -> tuple[bool, bool]:
            retry_cmd = _build_cmd(enc, force_sw_dl, force_gdi, allow_arnndn_cur)
            cmd_bytes = retry_cmd.encode("utf-8")
            keepalive.append(cmd_bytes)
            config_struct.ffmpeg_command = cmd_bytes
            logging.info(f"[FFmpegRecorder] {label}: {retry_cmd}")
            return _run_attempt(config_struct, "arnndn=" in retry_cmd)

        # マルチGPU(Optimus/SmartAccess Graphics)やHDR環境等でD3D11直結・GPUスケールが失敗した場合、互換モードで自動再試行
        if not use_gdi_initial and not force_sw and current_encoder != "libx264":
            logging.warning(
                f"[FFmpegRecorder] Direct D3D11 pipeline for {current_encoder} failed. "
                "Retrying with hwdownload compatibility mode..."
            )
            started_ok, arnndn_err = _retry_with_command("Fallback Command", current_encoder, True, False)
            if started_ok:
                sw_download_fallback_encoders.add(current_encoder)
                return True, used_encoder, "ddagrab_sw", False
            if arnndn_err:
                return False, used_encoder, used_capture, True

        # ハイブリッドGPU環境等でddagrab自体がDXGI複製非対応の場合、gdigrab互換モードで再試行
        if not use_gdi_initial and allow_gdigrab_fallback:
            logging.warning(
                f"[FFmpegRecorder] ddagrab failed with {current_encoder}. "
                "Retrying with gdigrab compatibility mode..."
            )
            started_ok, arnndn_err = _retry_with_command("GDI Fallback Command", current_encoder, True, True)
            if started_ok:
                return True, used_encoder, "gdigrab", False
            if arnndn_err:
                return False, used_encoder, used_capture, True

        # ハードウェアエンコーダ自体が実録画解像度で初期化失敗した場合、ソフトウェアエンコーダ(libx264)で救済
        if current_encoder != "libx264":
            logging.warning(f"[FFmpegRecorder] Hardware encoder {current_encoder} failed. Falling back to libx264...")
            current_encoder = "libx264"
            used_encoder = "libx264"
            started_ok, arnndn_err = _retry_with_command("Software Fallback Command", "libx264", True, use_gdi_initial)
            if started_ok:
                used_capture = "gdigrab" if use_gdi_initial else "ddagrab_sw"
                return True, used_encoder, used_capture, False
            if arnndn_err:
                return False, used_encoder, used_capture, True

            if not use_gdi_initial and allow_gdigrab_fallback:
                logging.warning("[FFmpegRecorder] libx264 with ddagrab failed. Retrying with libx264 + gdigrab...")
                started_ok, arnndn_err = _retry_with_command("Software GDI Fallback Command", "libx264", True, True)
                if started_ok:
                    return True, used_encoder, "gdigrab", False
                if arnndn_err:
                    return False, used_encoder, used_capture, True

        return False, used_encoder, used_capture, False

    started_ok, used_encoder, used_capture, arnndn_err = _run_fallback_sequence(
        allow_arnndn_cur=not arnndn_disabled
    )
    # arnndn起因エラーが確認された場合のみ、元のエンコーダ・キャプチャ方式からarnndn無効で再試行し二重遅延を防ぐ
    if not started_ok and arnndn_err:
        arnndn_disabled = True
        started_ok, used_encoder, used_capture, _ = _run_fallback_sequence(allow_arnndn_cur=False)

    return PipelineLaunchResult(
        started_ok=started_ok,
        used_encoder=used_encoder,
        used_capture=used_capture,
        final_encoder=current_encoder,
        disable_ffmpeg_arnndn=arnndn_disabled,
    )
