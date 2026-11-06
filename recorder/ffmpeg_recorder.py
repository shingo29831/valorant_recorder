import subprocess
import os
import ctypes
import logging
import threading
import time
from datetime import datetime
from typing import Optional
from core.config import Config
from recorder.encoder_utils import get_available_encoders, test_encoder
from recorder.ffmpeg_command_builder import build_ffmpeg_command
from recorder.ffmpeg_downloader import ensure_ffmpeg_downloaded
from recorder.native_bridge import (
    RecorderConfig,
    collect_and_save_diagnostic,
    load_native_recorder_dll,
    remux_mkv_to_mp4,
    resolve_deepfilter_model_path,
    try_rebuild_native_recorder,
)

class FFmpegRecorder:
    _GLOBAL_LOCK = threading.RLock()

    def __init__(self, config: Config):
        self.config = config
        self.current_filepath = None
        self.temp_filepath = None
        self._lock = self._GLOBAL_LOCK
        
        self.app_data_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'ValoReco')
        os.makedirs(self.app_data_dir, exist_ok=True)
        self.ffmpeg_path = ensure_ffmpeg_downloaded(self.app_data_dir)
        self._sw_download_fallback_encoders: set[str] = set()
        self.actual_encoder = self._determine_encoder()
        self._last_configured_encoder = self.config.RECORD_ENCODER
        
        try_rebuild_native_recorder()
        self.native_lib = load_native_recorder_dll()

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

    def _build_ffmpeg_command(self, force_sw_download: bool = False, force_gdigrab: bool = False) -> str:
        return build_ffmpeg_command(
            config=self.config,
            ffmpeg_path=self.ffmpeg_path,
            actual_encoder=self.actual_encoder,
            temp_filepath=self.temp_filepath or "",
            force_sw_download=force_sw_download,
            force_gdigrab=force_gdigrab,
        )

    def _try_start_and_verify(self, config_struct: RecorderConfig) -> bool:
        if not self.native_lib.Recorder_Start(ctypes.byref(config_struct)):
            return False
        for _ in range(7):
            time.sleep(0.1)
            if not self.native_lib.Recorder_IsRunning():
                return False
        return bool(self.native_lib.Recorder_IsRunning())

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
            os.makedirs(self.config.SAVE_DIR, exist_ok=True)

            is_lightweight = bool(getattr(self.config, 'LIGHTWEIGHT_MODE', False))
            force_sw = self.actual_encoder in self._sw_download_fallback_encoders
            cmd_str = self._build_ffmpeg_command(force_sw_download=force_sw)

            gate_level = float(getattr(self.config, 'RECORD_AUDIO_MIC_NOISE_GATE', '0')) / 100.0
            denoise_str = str(getattr(self.config, 'RECORD_AUDIO_MIC_DENOISE', 'None')).lower()
            if "deepfilter" in denoise_str:
                denoise_mode = 2 if is_lightweight else 1
            elif "rnnoise" in denoise_str or "speex" in denoise_str:
                denoise_mode = 2
            else:
                denoise_mode = 0

            preprocess_str = str(getattr(self.config, 'RECORD_AUDIO_MIC_PREPROCESS', 'SpeexDSP'))
            preprocess_mode = 1 if preprocess_str == 'SpeexDSP' else 0

            raw_mic_gain = float(getattr(self.config, 'RECORD_AUDIO_MIC_GAIN', 1.0))
            mic_gain = raw_mic_gain / 100.0 if raw_mic_gain > 10.0 else raw_mic_gain
            raw_sys_gain = float(getattr(self.config, 'RECORD_AUDIO_SYSTEM_GAIN', getattr(self.config, 'RECORD_AUDIO_SYS_GAIN', 1.0)))
            sys_gain = raw_sys_gain / 100.0 if raw_sys_gain > 10.0 else raw_sys_gain
            sys_device = str(getattr(self.config, 'RECORD_AUDIO_SYSTEM', 'default')).strip()
            if sys_device.lower() == 'none':
                os.environ["VALORECO_SYS_AUDIO_DEVICE"] = "__NONE__"
                sys_gain = 0.0
            elif not sys_device or sys_device.lower() == 'default':
                os.environ["VALORECO_SYS_AUDIO_DEVICE"] = ""
            else:
                os.environ["VALORECO_SYS_AUDIO_DEVICE"] = sys_device

            config_struct = RecorderConfig()
            config_struct.mic_gain = mic_gain
            config_struct.system_gain = sys_gain
            config_struct.noise_gate_threshold = gate_level
            config_struct.denoise_mode = denoise_mode
            config_struct.preprocess_mode = preprocess_mode
            config_struct.low_priority = 1 if is_lightweight else 0

            mic_device = getattr(self.config, 'RECORD_AUDIO_MIC', getattr(self.config, 'RECORD_AUDIO_MIC_DEVICE', ''))
            if mic_device.strip().lower() == 'none':
                config_struct.mic_device_name = b"__NONE__"
            elif not mic_device or mic_device.strip().lower() == 'default':
                config_struct.mic_device_name = b""
            else:
                config_struct.mic_device_name = mic_device.encode('utf-8')
            config_struct.ffmpeg_command = cmd_str.encode('utf-8')
            config_struct.save_file_path = self.current_filepath.encode('utf-8')
            config_struct.temp_file_path = self.temp_filepath.encode('utf-8')
            config_struct.ffmpeg_path = self.ffmpeg_path.encode('utf-8')

            config_struct.df_model_path = resolve_deepfilter_model_path(denoise_mode).encode('utf-8')

            logging.info(f"[FFmpegRecorder] Starting NativeRecorder. Encoder: {self.actual_encoder}, Lightweight: {is_lightweight}")
            logging.info(f"[FFmpegRecorder] Command: {cmd_str}")

            started_ok = self._try_start_and_verify(config_struct)

            # マルチGPU(Optimus/SmartAccess Graphics)やHDR環境等でD3D11直結・GPUスケールが失敗した場合、互換モードで自動再試行
            if not started_ok and not force_sw and self.actual_encoder != "libx264":
                logging.warning(f"[FFmpegRecorder] Direct D3D11 pipeline for {self.actual_encoder} failed. Retrying with hwdownload compatibility mode...")
                try:
                    self.native_lib.Recorder_Stop()
                except Exception:
                    pass
                cmd_str = self._build_ffmpeg_command(force_sw_download=True)
                config_struct.ffmpeg_command = cmd_str.encode('utf-8')
                logging.info(f"[FFmpegRecorder] Fallback Command: {cmd_str}")
                started_ok = self._try_start_and_verify(config_struct)
                if started_ok:
                    self._sw_download_fallback_encoders.add(self.actual_encoder)

            # ハイブリッドGPU環境等でddagrab自体がDXGI複製非対応の場合、gdigrab互換モードで再試行
            video_fmt = str(getattr(self.config, 'RECORD_VIDEO_FORMAT', 'ddagrab')).strip().lower()
            if not started_ok and video_fmt != "gdigrab":
                logging.warning(f"[FFmpegRecorder] ddagrab failed with {self.actual_encoder}. Retrying with gdigrab compatibility mode...")
                try:
                    self.native_lib.Recorder_Stop()
                except Exception:
                    pass
                cmd_str = self._build_ffmpeg_command(force_sw_download=True, force_gdigrab=True)
                config_struct.ffmpeg_command = cmd_str.encode('utf-8')
                logging.info(f"[FFmpegRecorder] GDI Fallback Command: {cmd_str}")
                started_ok = self._try_start_and_verify(config_struct)

            # ハードウェアエンコーダ自体が実録画解像度で初期化失敗した場合、ソフトウェアエンコーダ(libx264)で救済
            if not started_ok and self.actual_encoder != "libx264":
                logging.warning(f"[FFmpegRecorder] Hardware encoder {self.actual_encoder} failed. Falling back to libx264...")
                try:
                    self.native_lib.Recorder_Stop()
                except Exception:
                    pass
                self.actual_encoder = "libx264"
                cmd_str = self._build_ffmpeg_command(force_sw_download=True)
                config_struct.ffmpeg_command = cmd_str.encode('utf-8')
                logging.info(f"[FFmpegRecorder] Software Fallback Command: {cmd_str}")
                started_ok = self._try_start_and_verify(config_struct)

                if not started_ok and video_fmt != "gdigrab":
                    logging.warning("[FFmpegRecorder] libx264 with ddagrab failed. Retrying with libx264 + gdigrab...")
                    try:
                        self.native_lib.Recorder_Stop()
                    except Exception:
                        pass
                    cmd_str = self._build_ffmpeg_command(force_sw_download=True, force_gdigrab=True)
                    config_struct.ffmpeg_command = cmd_str.encode('utf-8')
                    logging.info(f"[FFmpegRecorder] Software GDI Fallback Command: {cmd_str}")
                    started_ok = self._try_start_and_verify(config_struct)

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

            logging.info("[FFmpegRecorder] Recording started successfully.")
            return self.current_filepath
        except Exception as e:
            err_msg = str(e)
            logging.error(f"[FFmpegRecorder] Failed to start recording: {err_msg}", exc_info=True)
            collect_and_save_diagnostic(self.native_lib, self.config.SAVE_DIR, err_msg)
            # エラー時にネイティブスレッドとリソースを確実に後始末
            try:
                self.native_lib.Recorder_Stop()
            except Exception:
                pass
            raise

    def is_recording(self) -> bool:
        return self.native_lib.Recorder_IsRunning()

    def get_last_error(self) -> str:
        try:
            err = self.native_lib.Recorder_GetLastError()
            return err.decode("utf-8", errors="replace") if err else ""
        except Exception:
            return ""

    def cancel_recording(self):
        """Remuxを行わずに録画プロセスを即座に停止し、一時ファイルと出力ファイルを破棄する"""
        logging.info("[FFmpegRecorder] Cancelling NativeRecorder...")
        with self._lock:
            try:
                self.native_lib.Recorder_Stop()
            except Exception as e:
                logging.warning(f"[FFmpegRecorder] Exception while stopping native recorder on cancel: {e}")

            paths_to_clean = [
                self.temp_filepath,
                self.current_filepath,
                (self.current_filepath + ".tmp") if self.current_filepath else None,
            ]
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

    def stop_recording(self, target_filepath: str = None):
        logging.info("[FFmpegRecorder] Stopping NativeRecorder...")
        with self._lock:
            # 既に次の録画セッションへ切り替わっている場合は新セッションを誤停止しない
            if target_filepath and self.current_filepath and self.current_filepath != target_filepath:
                logging.info(
                    f"[FFmpegRecorder] Session already switched ({self.current_filepath}), skipping stop for {target_filepath}."
                )
                temp_filepath = os.path.splitext(target_filepath)[0] + ".mkv"
                current_filepath = target_filepath
                actual_encoder = self.actual_encoder
            else:
                self.native_lib.Recorder_Stop()
                temp_filepath = self.temp_filepath
                current_filepath = self.current_filepath
                actual_encoder = self.actual_encoder
        
        if temp_filepath and current_filepath:
            remux_mkv_to_mp4(self.ffmpeg_path, temp_filepath, current_filepath, actual_encoder)

    def set_low_priority(self):
        if hasattr(self.native_lib, 'Recorder_SetLowPriority'):
            try:
                self.native_lib.Recorder_SetLowPriority(True)
            except Exception as e:
                logging.warning(f"[FFmpegRecorder] Failed to set low priority: {e}")
