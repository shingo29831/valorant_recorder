import subprocess
import os
import ctypes
import logging
import re
import threading
import time
from datetime import datetime
from typing import Any, Optional
from core.config import Config, get_app_data_dir
from recorder.audio_processor_wrapper import AudioProcessorWrapper
from recorder.encoder_utils import get_available_encoders, test_encoder
from recorder.ffmpeg_command_builder import (
    build_ffmpeg_command,
    build_ffmpeg_mic_dsp_chain,
    parse_target_resolution,
)
from recorder.ffmpeg_downloader import (
    ensure_ffmpeg_downloaded,
    ensure_rnnoise_model_async,
    wait_for_rnnoise_model_ready,
)
from recorder.native_bridge import (
    RecorderConfig,
    collect_and_save_diagnostic,
    load_recorder_backend,
    remux_mkv_segments_to_mp4,
    resolve_deepfilter_model_path,
    try_rebuild_native_recorder,
)

class FFmpegRecorder:
    _GLOBAL_LOCK = threading.RLock()

    def __init__(self, config: Config):
        self.config = config
        self.current_filepath = None
        self.temp_filepath = None
        self._segment_records: list[dict[str, Any]] = []
        self._session_segment_index: int = 0
        self._session_actual_start_time_ms: Optional[int] = None
        self._session_initial_encoder: Optional[str] = None
        self._session_use_chat_track: Optional[bool] = None
        self._session_target_size: tuple[int, int] = (1920, 1080)
        self._session_fps: int = 60
        self._lock = self._GLOBAL_LOCK
        
        self.app_data_dir = get_app_data_dir(ensure_exists=True)
        self.ffmpeg_path = ensure_ffmpeg_downloaded(self.app_data_dir)
        self._sw_download_fallback_encoders: set[str] = set()
        self.actual_encoder = self._determine_encoder()
        self._last_configured_encoder = self.config.RECORD_ENCODER
        
        try_rebuild_native_recorder()
        self.native_lib, self.backend_mode = load_recorder_backend()
        self._disable_ffmpeg_arnndn = False
        if self._should_use_ffmpeg_mic_dsp() and self._wants_ffmpeg_arnndn():
            ensure_rnnoise_model_async(self.app_data_dir)

    def _determine_encoder(self) -> str:
        encoder = self.config.RECORD_ENCODER
        if encoder:
            success, _ = test_encoder(self.ffmpeg_path, encoder)
            if success:
                return encoder
        
        available, _ = get_available_encoders(self.ffmpeg_path)
        best_encoder = available[0] if available else "libx264"
        if self.config.RECORD_ENCODER != best_encoder:
            self.config.RECORD_ENCODER = best_encoder
            try:
                self.config.save()
            except Exception:
                pass
        return best_encoder

    def _should_use_ffmpeg_mic_dsp(self) -> bool:
        return self.backend_mode == "python" and not AudioProcessorWrapper.is_available()

    def _wants_ffmpeg_arnndn(self) -> bool:
        mic_dev = str(getattr(self.config, "RECORD_AUDIO_MIC", "")).strip().lower()
        if mic_dev == "none":
            return False
        denoise_str = str(getattr(self.config, "RECORD_AUDIO_MIC_DENOISE", "None")).strip().lower()
        return any(k in denoise_str for k in ("rnnoise", "speex", "deepfilter"))

    def _is_gate_delegated_to_ffmpeg(self, allow_arnndn: bool = True) -> bool:
        if not self._should_use_ffmpeg_mic_dsp():
            return False
        _, gate_handled = build_ffmpeg_mic_dsp_chain(
            self.config,
            self.ffmpeg_path,
            allow_arnndn=allow_arnndn,
            allow_agate=True,
        )
        return gate_handled

    def _build_ffmpeg_command(
        self,
        force_sw_download: bool = False,
        force_gdigrab: bool = False,
        enable_chat_track: Optional[bool] = None,
        allow_arnndn: Optional[bool] = None,
    ) -> str:
        effective_arnndn = (not self._disable_ffmpeg_arnndn) if allow_arnndn is None else bool(allow_arnndn)
        return build_ffmpeg_command(
            config=self.config,
            ffmpeg_path=self.ffmpeg_path,
            actual_encoder=self.actual_encoder,
            temp_filepath=self.temp_filepath or "",
            force_sw_download=force_sw_download,
            force_gdigrab=force_gdigrab,
            enable_chat_track=enable_chat_track,
            use_ffmpeg_mic_dsp=self._should_use_ffmpeg_mic_dsp(),
            allow_arnndn=effective_arnndn,
            allow_agate=True,
        )

    def _prepare_ffmpeg_log_for_attempt(self) -> float:
        """旧セッションのログ残存による誤判定を防ぐため試行開始前にnative_ffmpeg.logを消去し開始時刻を返す"""
        ffmpeg_log_file = os.path.join(self.config.SAVE_DIR, "native_ffmpeg.log")
        if os.path.exists(ffmpeg_log_file):
            try:
                os.remove(ffmpeg_log_file)
            except OSError:
                pass
        return time.time()

    def _did_last_ffmpeg_hit_arnndn_error(self, min_mtime: float = 0.0) -> bool:
        """今回試行で出力されたFFmpegログのエラー文脈行からarnndn起因失敗を厳格判定する"""
        ffmpeg_log_file = os.path.join(self.config.SAVE_DIR, "native_ffmpeg.log")
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
        error_markers = (
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
        for idx, line in enumerate(lines):
            if ("arnndn" not in line) and ("sh.rnnn" not in line):
                continue
            window = line
            if idx + 1 < len(lines):
                window = window + " " + lines[idx + 1]
            if any(m in window for m in error_markers):
                return True
        return False

    def _try_start_and_verify(self, config_struct: RecorderConfig) -> bool:
        if not self.native_lib.Recorder_Start(ctypes.byref(config_struct)):
            return False
        if hasattr(self.native_lib, "Recorder_GetStartupState"):
            # DeepFilterNet展開〜FFmpeg/D3D11起動成否が確定するまで待機し、フォールバック判定を正確に機能させる
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                state = int(self.native_lib.Recorder_GetStartupState())
                if state == 3 or not self.native_lib.Recorder_IsRunning():
                    return False
                if state == 2:
                    for _ in range(6):
                        time.sleep(0.05)
                        if not self.native_lib.Recorder_IsRunning():
                            return False
                    return True
                time.sleep(0.05)
            return False
        for _ in range(7):
            time.sleep(0.1)
            if not self.native_lib.Recorder_IsRunning():
                return False
        return bool(self.native_lib.Recorder_IsRunning())

    def _build_recorder_config(self, cmd_str: str, use_chat_track: bool) -> tuple[RecorderConfig, list[bytes]]:
        is_lightweight = bool(getattr(self.config, 'LIGHTWEIGHT_MODE', False))
        chat_device = str(getattr(self.config, 'RECORD_AUDIO_CHAT', 'None')).strip()

        def _parse_float(raw_val, default: float) -> float:
            try:
                return float(raw_val)
            except (ValueError, TypeError):
                return default

        # FFmpeg側agateへ委譲する場合はHPF->Denoise->NoiseGateの順序を守りPython側ゲートを二重適用しない
        if self._is_gate_delegated_to_ffmpeg(allow_arnndn=not self._disable_ffmpeg_arnndn):
            gate_level = 0.0
        else:
            gate_level = max(0.0, min(1.0, _parse_float(getattr(self.config, 'RECORD_AUDIO_MIC_NOISE_GATE', '0'), 0.0) / 100.0))
        denoise_str = str(getattr(self.config, 'RECORD_AUDIO_MIC_DENOISE', 'None')).lower()
        if "deepfilter" in denoise_str:
            denoise_mode = 2 if is_lightweight else 1
        elif "rnnoise" in denoise_str or "speex" in denoise_str:
            denoise_mode = 2
        else:
            denoise_mode = 0

        preprocess_str = str(getattr(self.config, 'RECORD_AUDIO_MIC_PREPROCESS', 'SpeexDSP'))
        preprocess_mode = 1 if preprocess_str == 'SpeexDSP' else 0

        raw_mic_gain = _parse_float(getattr(self.config, 'RECORD_AUDIO_MIC_GAIN', 1.0), 1.0)
        mic_gain = max(0.0, raw_mic_gain / 100.0 if raw_mic_gain > 10.0 else raw_mic_gain)
        raw_sys_gain = _parse_float(getattr(self.config, 'RECORD_AUDIO_SYSTEM_GAIN', getattr(self.config, 'RECORD_AUDIO_SYS_GAIN', 1.0)), 1.0)
        sys_gain = max(0.0, raw_sys_gain / 100.0 if raw_sys_gain > 10.0 else raw_sys_gain)
        raw_chat_gain = _parse_float(getattr(self.config, 'RECORD_AUDIO_CHAT_GAIN', 1.0), 1.0)
        chat_gain = max(0.0, raw_chat_gain / 100.0 if raw_chat_gain > 10.0 else raw_chat_gain)

        sys_device = str(getattr(self.config, 'RECORD_AUDIO_SYSTEM', 'default')).strip()
        if sys_device.lower() == 'none':
            os.environ["VALORECO_SYS_AUDIO_DEVICE"] = "__NONE__"
            sys_device_bytes = b"__NONE__"
            sys_gain = 0.0
        elif not sys_device or sys_device.lower() == 'default':
            os.environ["VALORECO_SYS_AUDIO_DEVICE"] = ""
            sys_device_bytes = b""
        else:
            os.environ["VALORECO_SYS_AUDIO_DEVICE"] = sys_device
            sys_device_bytes = sys_device.encode('utf-8')

        if not use_chat_track or chat_device.lower() == 'none':
            chat_device_bytes = b"__NONE__"
            chat_gain = 0.0
        elif not chat_device or chat_device.lower() == 'default':
            chat_device_bytes = b""
        else:
            chat_device_bytes = chat_device.encode('utf-8')

        mic_device = getattr(self.config, 'RECORD_AUDIO_MIC', getattr(self.config, 'RECORD_AUDIO_MIC_DEVICE', ''))
        if mic_device.strip().lower() == 'none':
            mic_device_bytes = b"__NONE__"
        elif not mic_device or mic_device.strip().lower() == 'default':
            mic_device_bytes = b""
        else:
            mic_device_bytes = mic_device.encode('utf-8')

        cmd_bytes = cmd_str.encode('utf-8')
        save_bytes = self.current_filepath.encode('utf-8')
        temp_bytes = self.temp_filepath.encode('utf-8')
        ffmpeg_bytes = self.ffmpeg_path.encode('utf-8')
        df_model_bytes = resolve_deepfilter_model_path(denoise_mode).encode('utf-8')

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

    def _launch_pipeline_with_fallbacks(
        self,
        use_chat_track: bool,
        allow_gdigrab_fallback: bool = True,
    ) -> tuple[bool, str, str]:
        """録画パイプラインを起動し、(成功可否, 使用エンコーダ, 使用キャプチャ方式) を返す"""
        is_lightweight = bool(getattr(self.config, 'LIGHTWEIGHT_MODE', False))
        force_sw = self.actual_encoder in self._sw_download_fallback_encoders
        video_fmt = str(getattr(self.config, 'RECORD_VIDEO_FORMAT', 'ddagrab')).strip().lower()
        use_gdi_initial = (video_fmt == "gdigrab")

        def _resolve_ddagrab_mode(sw_dl: bool, enc: str) -> str:
            if sw_dl or enc == "libx264":
                return "ddagrab_sw"
            enc_l = enc.lower()
            if "nvenc" in enc_l or "amf" in enc_l:
                return "ddagrab_d3d11"
            if "qsv" in enc_l:
                return "ddagrab_qsv"
            return "ddagrab_sw"

        initial_encoder = self.actual_encoder

        def _stop_and_flush() -> None:
            try:
                self.native_lib.Recorder_Stop()
            except Exception:
                pass

        def _run_attempt(
            config_struct: RecorderConfig,
            used_arnndn: bool,
        ) -> tuple[bool, bool]:
            attempt_ts = self._prepare_ffmpeg_log_for_attempt()
            ok = self._try_start_and_verify(config_struct)
            if ok:
                return True, False
            # native_ffmpeg.log書き出し完了を保証してからエラー判定を行う
            _stop_and_flush()
            if used_arnndn and self._did_last_ffmpeg_hit_arnndn_error(min_mtime=attempt_ts):
                logging.warning(
                    "[FFmpegRecorder] FFmpeg arnndn filter error detected. "
                    "Disabling arnndn and retrying primary pipeline..."
                )
                self._disable_ffmpeg_arnndn = True
                return False, True
            return False, False

        def _run_fallback_sequence(allow_arnndn_cur: bool) -> tuple[bool, str, str, bool]:
            self.actual_encoder = initial_encoder
            cmd_str = self._build_ffmpeg_command(
                force_sw_download=force_sw,
                force_gdigrab=use_gdi_initial,
                enable_chat_track=use_chat_track,
                allow_arnndn=allow_arnndn_cur,
            )
            used_arnndn = "arnndn=" in cmd_str
            config_struct, keepalive = self._build_recorder_config(cmd_str, use_chat_track)
            used_encoder = self.actual_encoder
            used_capture = "gdigrab" if use_gdi_initial else _resolve_ddagrab_mode(force_sw, used_encoder)

            logging.info(
                f"[FFmpegRecorder] Starting Recorder ({self.backend_mode}). Encoder: {used_encoder}, "
                f"Capture: {used_capture}, Lightweight: {is_lightweight}, ChatTrack: {use_chat_track}"
            )
            logging.info(f"[FFmpegRecorder] Command: {cmd_str}")

            started_ok, arnndn_err = _run_attempt(config_struct, used_arnndn)
            if started_ok or arnndn_err:
                return started_ok, used_encoder, used_capture, arnndn_err

            # マルチGPU(Optimus/SmartAccess Graphics)やHDR環境等でD3D11直結・GPUスケールが失敗した場合、互換モードで自動再試行
            if not use_gdi_initial and not force_sw and self.actual_encoder != "libx264":
                logging.warning(
                    f"[FFmpegRecorder] Direct D3D11 pipeline for {self.actual_encoder} failed. "
                    "Retrying with hwdownload compatibility mode..."
                )
                cmd_str = self._build_ffmpeg_command(
                    force_sw_download=True,
                    enable_chat_track=use_chat_track,
                    allow_arnndn=allow_arnndn_cur,
                )
                cmd_bytes = cmd_str.encode('utf-8')
                keepalive.append(cmd_bytes)
                config_struct.ffmpeg_command = cmd_bytes
                logging.info(f"[FFmpegRecorder] Fallback Command: {cmd_str}")
                started_ok, arnndn_err = _run_attempt(config_struct, "arnndn=" in cmd_str)
                if started_ok:
                    used_capture = "ddagrab_sw"
                    self._sw_download_fallback_encoders.add(self.actual_encoder)
                    return True, used_encoder, used_capture, False
                if arnndn_err:
                    return False, used_encoder, used_capture, True

            # ハイブリッドGPU環境等でddagrab自体がDXGI複製非対応の場合、gdigrab互換モードで再試行
            if not use_gdi_initial and allow_gdigrab_fallback:
                logging.warning(
                    f"[FFmpegRecorder] ddagrab failed with {self.actual_encoder}. "
                    "Retrying with gdigrab compatibility mode..."
                )
                cmd_str = self._build_ffmpeg_command(
                    force_sw_download=True,
                    force_gdigrab=True,
                    enable_chat_track=use_chat_track,
                    allow_arnndn=allow_arnndn_cur,
                )
                cmd_bytes = cmd_str.encode('utf-8')
                keepalive.append(cmd_bytes)
                config_struct.ffmpeg_command = cmd_bytes
                logging.info(f"[FFmpegRecorder] GDI Fallback Command: {cmd_str}")
                started_ok, arnndn_err = _run_attempt(config_struct, "arnndn=" in cmd_str)
                if started_ok:
                    return True, used_encoder, "gdigrab", False
                if arnndn_err:
                    return False, used_encoder, used_capture, True

            # ハードウェアエンコーダ自体が実録画解像度で初期化失敗した場合、ソフトウェアエンコーダ(libx264)で救済
            if self.actual_encoder != "libx264":
                logging.warning(f"[FFmpegRecorder] Hardware encoder {self.actual_encoder} failed. Falling back to libx264...")
                self.actual_encoder = "libx264"
                used_encoder = "libx264"
                cmd_str = self._build_ffmpeg_command(
                    force_sw_download=True,
                    force_gdigrab=use_gdi_initial,
                    enable_chat_track=use_chat_track,
                    allow_arnndn=allow_arnndn_cur,
                )
                cmd_bytes = cmd_str.encode('utf-8')
                keepalive.append(cmd_bytes)
                config_struct.ffmpeg_command = cmd_bytes
                logging.info(f"[FFmpegRecorder] Software Fallback Command: {cmd_str}")
                started_ok, arnndn_err = _run_attempt(config_struct, "arnndn=" in cmd_str)
                if started_ok:
                    used_capture = "gdigrab" if use_gdi_initial else "ddagrab_sw"
                    return True, used_encoder, used_capture, False
                if arnndn_err:
                    return False, used_encoder, used_capture, True

                if not use_gdi_initial and allow_gdigrab_fallback:
                    logging.warning("[FFmpegRecorder] libx264 with ddagrab failed. Retrying with libx264 + gdigrab...")
                    cmd_str = self._build_ffmpeg_command(
                        force_sw_download=True,
                        force_gdigrab=True,
                        enable_chat_track=use_chat_track,
                        allow_arnndn=allow_arnndn_cur,
                    )
                    cmd_bytes = cmd_str.encode('utf-8')
                    keepalive.append(cmd_bytes)
                    config_struct.ffmpeg_command = cmd_bytes
                    logging.info(f"[FFmpegRecorder] Software GDI Fallback Command: {cmd_str}")
                    started_ok, arnndn_err = _run_attempt(config_struct, "arnndn=" in cmd_str)
                    if started_ok:
                        return True, used_encoder, "gdigrab", False
                    if arnndn_err:
                        return False, used_encoder, used_capture, True

            return False, used_encoder, used_capture, False

        started_ok, used_encoder, used_capture, arnndn_err = _run_fallback_sequence(
            allow_arnndn_cur=not self._disable_ffmpeg_arnndn
        )
        # arnndn起因エラーが確認された場合のみ、元のエンコーダ・キャプチャ方式からarnndn無効で再試行し二重遅延を防ぐ
        if not started_ok and arnndn_err:
            self._disable_ffmpeg_arnndn = True
            started_ok, used_encoder, used_capture, _ = _run_fallback_sequence(allow_arnndn_cur=False)

        return started_ok, used_encoder, used_capture

    def _did_last_ffmpeg_hit_dxgi_loss(self) -> bool:
        """直前のFFmpeg終了がフルスクリーン裏画面切替等によるDXGIアクセス消失かをエラー固有語句で厳格判定する"""
        ffmpeg_log_file = os.path.join(self.config.SAVE_DIR, "native_ffmpeg.log")
        if not os.path.exists(ffmpeg_log_file):
            return False
        try:
            with open(ffmpeg_log_file, "r", encoding="utf-8", errors="replace") as lf:
                tail = lf.read()[-4096:].lower()
            exact_error_markers = (
                "887a0026",
                "dxgi_error_access_lost",
                "acquirenextframe failed",
                "failed to acquire next frame",
                "duplicateoutput failed",
            )
            if any(m in tail for m in exact_error_markers):
                return True
            if "output duplication" in tail and ("lost" in tail or "failed" in tail or "887a" in tail):
                return True
            return False
        except OSError:
            return False

    def start_recording(self) -> str:
        with self._lock:
            return self._start_recording_locked()

    def _start_recording_locked(self) -> str:
        if self.native_lib.Recorder_IsRunning():
            logging.info("[FFmpegRecorder] Already recording.")
            return self.current_filepath

        try:
            # ユーザーの設定変更や前回の一時フォールバックから復帰できるよう毎回エンコーダを確定
            self._last_configured_encoder = self.config.RECORD_ENCODER
            self.actual_encoder = self._determine_encoder()

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"match_record_{timestamp}.mp4"
            temp_filename = f"match_record_{timestamp}.mkv"
            self.current_filepath = os.path.join(self.config.SAVE_DIR, filename)
            self.temp_filepath = os.path.join(self.config.SAVE_DIR, temp_filename)
            self._segment_records = []
            self._session_segment_index = 0
            self._session_actual_start_time_ms = None
            self._session_initial_encoder = self.actual_encoder
            self._session_target_size = parse_target_resolution(self.config)
            try:
                self._session_fps = max(1, int(getattr(self.config, "RECORD_FPS", "60")))
            except (ValueError, TypeError):
                self._session_fps = 60
            os.makedirs(self.config.SAVE_DIR, exist_ok=True)

            chat_separate = bool(getattr(self.config, 'RECORD_AUDIO_CHAT_SEPARATE', False))
            chat_device = str(getattr(self.config, 'RECORD_AUDIO_CHAT', 'None')).strip()
            supports_chat = bool(
                hasattr(self.native_lib, "Recorder_SupportsChatTrack")
                and self.native_lib.Recorder_SupportsChatTrack()
            )
            if chat_separate and chat_device.lower() != 'none' and not supports_chat:
                # 未ビルドの旧DLLで6chパイプ不整合による音声破損を防ぐため4chに安全フォールバック
                logging.warning(
                    "[FFmpegRecorder] Separate chat track requested, but loaded native_recorder.dll does not support 6ch audio. Falling back to 4ch."
                )
            use_chat_track = chat_separate and supports_chat and (chat_device.lower() != 'none')
            self._session_use_chat_track = use_chat_track

            # 初回録画時に裏で進行中のRNNoiseモデルDL完了を上限2.5秒だけ待ち、初回からarnndnを有効化
            if self._should_use_ffmpeg_mic_dsp() and self._wants_ffmpeg_arnndn() and not self._disable_ffmpeg_arnndn:
                wait_for_rnnoise_model_ready(self.app_data_dir, timeout=2.5)

            started_ok, used_encoder, used_capture = self._launch_pipeline_with_fallbacks(
                use_chat_track=use_chat_track,
                allow_gdigrab_fallback=True,
            )

            if not started_ok:
                err = self.native_lib.Recorder_GetLastError().decode('utf-8', errors='replace')
                ffmpeg_log_tail = ""
                ffmpeg_log_file = os.path.join(self.config.SAVE_DIR, "native_ffmpeg.log")
                if os.path.exists(ffmpeg_log_file):
                    try:
                        with open(ffmpeg_log_file, "r", encoding="utf-8", errors="replace") as lf:
                            tail_lines = [l.strip() for l in lf.readlines()[-8:] if l.strip()]
                            if tail_lines:
                                ffmpeg_log_tail = " -> FFmpeg Log: " + " | ".join(tail_lines)
                    except Exception:
                        pass
                if not err:
                    err = "FFmpeg or audio capture pipeline terminated unexpectedly"
                full_err = f"Recorder initialization failed: {err}{ffmpeg_log_tail}"
                logging.error(f"[FFmpegRecorder] {full_err}")
                collect_and_save_diagnostic(self.native_lib, self.config.SAVE_DIR, full_err)
                raise RuntimeError(full_err)

            seg_start_ms = self._read_native_actual_start_time_ms() or int(time.time() * 1000)
            self._session_actual_start_time_ms = seg_start_ms
            self._session_initial_encoder = used_encoder
            self._segment_records.append({
                "path": self.temp_filepath,
                "encoder": used_encoder,
                "capture_mode": used_capture,
                "width": self._session_target_size[0],
                "height": self._session_target_size[1],
                "fps": self._session_fps,
                "audio_tracks": 3 if use_chat_track else 2,
                "start_time_ms": seg_start_ms,
                "end_time_ms": 0,
            })

            logging.info("[FFmpegRecorder] Recording started successfully.")
            return self.current_filepath
        except Exception as e:
            err_msg = str(e)
            logging.error(f"[FFmpegRecorder] Failed to start recording: {err_msg}", exc_info=True)
            collect_and_save_diagnostic(self.native_lib, self.config.SAVE_DIR, err_msg)
            self.current_filepath = None
            self.temp_filepath = None
            self._segment_records = []
            self._session_actual_start_time_ms = None
            # エラー時にネイティブスレッドとリソースを確実に後始末
            try:
                self.native_lib.Recorder_Stop()
            except Exception:
                pass
            raise

    def resume_recording_segment(
        self,
        expected_filepath: Optional[str] = None,
        allow_gdigrab_fallback: bool = False,
    ) -> bool:
        """試合中にDXGI消失(裏画面遷移等)でFFmpegが終了した際、同一セッションのまま次セグメントを継続録画する"""
        with self._lock:
            if not self.current_filepath:
                return False
            if expected_filepath and self.current_filepath != expected_filepath:
                return False
            if self.native_lib.Recorder_IsRunning():
                return True

            if self._session_actual_start_time_ms is None:
                native_start = self._read_native_actual_start_time_ms()
                if native_start:
                    self._session_actual_start_time_ms = native_start

            # 直前セグメントの終了時刻を確定させ、後続結合時のタイムラインギャップ補正に利用する
            prev_end_ms = self._read_native_actual_end_time_ms() or int(time.time() * 1000)
            if self._segment_records and not self._segment_records[-1].get("end_time_ms"):
                self._segment_records[-1]["end_time_ms"] = prev_end_ms

            dxgi_lost = self._did_last_ffmpeg_hit_dxgi_loss()
            try:
                self.native_lib.Recorder_Stop()
            except Exception as e:
                logging.warning(f"[FFmpegRecorder] Exception while cleaning up interrupted segment: {e}")

            # 排他フルスクリーン復帰時の黒画面化とストリーム不一致を防ぐため元エンコーダ＆ddagrabを優先復帰
            if self._session_initial_encoder:
                self.actual_encoder = self._session_initial_encoder

            base_no_ext = os.path.splitext(self.current_filepath)[0]
            candidate_index = self._session_segment_index + 1
            next_temp = f"{base_no_ext}_part{candidate_index}.mkv"
            prev_temp = self.temp_filepath
            self.temp_filepath = next_temp

            use_chat_track = bool(self._session_use_chat_track)
            # DXGI消失時は過渡期でのgdigrab即時固定を避け、連続失敗時のみgdigrabフォールバックを許可
            effective_gdi_fallback = allow_gdigrab_fallback or (not dxgi_lost)
            started_ok, used_encoder, used_capture = self._launch_pipeline_with_fallbacks(
                use_chat_track=use_chat_track,
                allow_gdigrab_fallback=effective_gdi_fallback,
            )
            if started_ok:
                self._session_segment_index = candidate_index
                seg_start_ms = self._read_native_actual_start_time_ms() or int(time.time() * 1000)
                self._segment_records.append({
                    "path": next_temp,
                    "encoder": used_encoder,
                    "capture_mode": used_capture,
                    "width": self._session_target_size[0],
                    "height": self._session_target_size[1],
                    "fps": self._session_fps,
                    "audio_tracks": 3 if use_chat_track else 2,
                    "start_time_ms": seg_start_ms,
                    "end_time_ms": 0,
                })
                logging.info(
                    f"[FFmpegRecorder] Seamlessly resumed recording session in segment #{candidate_index} "
                    f"(encoder={used_encoder}, capture={used_capture}): {next_temp}"
                )
                return True

            try:
                self.native_lib.Recorder_Stop()
            except Exception:
                pass
            if os.path.exists(next_temp):
                try:
                    if os.path.getsize(next_temp) == 0:
                        os.remove(next_temp)
                except OSError:
                    pass
            self.temp_filepath = prev_temp
            logging.warning("[FFmpegRecorder] Failed to resume recording segment; will retry while match is active.")
            return False

    def is_recording(self) -> bool:
        return self.native_lib.Recorder_IsRunning()

    def get_last_error(self) -> str:
        try:
            err = self.native_lib.Recorder_GetLastError()
            return err.decode("utf-8", errors="replace") if err else ""
        except Exception:
            return ""

    def _read_native_actual_start_time_ms(self) -> Optional[int]:
        if hasattr(self.native_lib, "Recorder_GetActualStartTimeMs"):
            try:
                val = int(self.native_lib.Recorder_GetActualStartTimeMs())
                return val if val > 0 else None
            except Exception:
                pass
        return None

    def _read_native_actual_end_time_ms(self) -> Optional[int]:
        if hasattr(self.native_lib, "Recorder_GetActualEndTimeMs"):
            try:
                val = int(self.native_lib.Recorder_GetActualEndTimeMs())
                return val if val > 0 else None
            except Exception:
                pass
        return None

    def get_actual_start_time_ms(self) -> Optional[int]:
        if self._session_actual_start_time_ms:
            return self._session_actual_start_time_ms
        return self._read_native_actual_start_time_ms()

    def cancel_recording(self):
        """Remuxを行わずに録画プロセスを即座に停止し、一時ファイルと出力ファイルを破棄する"""
        logging.info("[FFmpegRecorder] Cancelling NativeRecorder...")
        with self._lock:
            try:
                self.native_lib.Recorder_Stop()
            except Exception as e:
                logging.warning(f"[FFmpegRecorder] Exception while stopping native recorder on cancel: {e}")

            paths_to_clean = [seg["path"] for seg in self._segment_records if seg.get("path")]
            paths_to_clean.extend([
                self.temp_filepath,
                self.current_filepath,
                (self.current_filepath + ".tmp") if self.current_filepath else None,
                (self.current_filepath + ".concat.txt") if self.current_filepath else None,
            ])
            self.current_filepath = None
            self.temp_filepath = None
            self._segment_records = []
            self._session_segment_index = 0
            self._session_actual_start_time_ms = None
            self._session_initial_encoder = None
        for p in paths_to_clean:
            if not p:
                continue
            for _ in range(6):
                try:
                    if os.path.exists(p):
                        os.remove(p)
                    break
                except OSError:
                    time.sleep(0.3)

    def _collect_segments_for_filepath(self, target_filepath: str) -> list[dict[str, Any]]:
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

    def stop_recording(self, target_filepath: str = None) -> list[dict[str, int]]:
        logging.info("[FFmpegRecorder] Stopping NativeRecorder...")
        with self._lock:
            # 既に次の録画セッションへ切り替わっている場合は新セッションを誤停止しない
            if target_filepath and self.current_filepath and self.current_filepath != target_filepath:
                logging.info(
                    f"[FFmpegRecorder] Session already switched ({self.current_filepath}), skipping stop for {target_filepath}."
                )
                segment_entries = self._collect_segments_for_filepath(target_filepath)
                current_filepath = target_filepath
                fallback_encoder = self.actual_encoder
            else:
                self.native_lib.Recorder_Stop()
                final_end_ms = self._read_native_actual_end_time_ms() or int(time.time() * 1000)
                if self._segment_records and not self._segment_records[-1].get("end_time_ms"):
                    self._segment_records[-1]["end_time_ms"] = final_end_ms
                segment_entries = [dict(seg) for seg in self._segment_records]
                recorded_paths = {seg["path"] for seg in segment_entries if seg.get("path")}
                if self.temp_filepath and self.temp_filepath not in recorded_paths:
                    segment_entries.append({"path": self.temp_filepath})
                current_filepath = self.current_filepath or target_filepath
                if not segment_entries and current_filepath:
                    segment_entries = self._collect_segments_for_filepath(current_filepath)
                fallback_encoder = self._session_initial_encoder or self.actual_encoder
                self.current_filepath = None
                self.temp_filepath = None
                self._segment_records = []
                self._session_segment_index = 0
                self._session_actual_start_time_ms = None
                self._session_initial_encoder = None
        
        if segment_entries and current_filepath:
            return remux_mkv_segments_to_mp4(
                self.ffmpeg_path,
                segment_entries,
                current_filepath,
                fallback_encoder,
            )
        return []

    def set_low_priority(self):
        if hasattr(self.native_lib, 'Recorder_SetLowPriority'):
            try:
                self.native_lib.Recorder_SetLowPriority(True)
            except Exception as e:
                logging.warning(f"[FFmpegRecorder] Failed to set low priority: {e}")
