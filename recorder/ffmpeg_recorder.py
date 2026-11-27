"""Module: @role: 録画セッションのライフサイクル管理・セグメント継続・停止/キャンセル/結合を統括するファサードモジュール"""

import logging
import os
import threading
import time
from datetime import datetime
from typing import Any, Optional

from core.config import Config, get_app_data_dir
from recorder.ffmpeg_command_builder import parse_target_resolution
from recorder.ffmpeg_downloader import (
    ensure_ffmpeg_downloaded,
    ensure_rnnoise_model_async,
    wait_for_rnnoise_model_ready,
)
from recorder.ffmpeg_pipeline_launcher import launch_pipeline_with_fallbacks
from recorder.ffmpeg_recorder_config import (
    collect_segments_for_filepath,
    determine_encoder,
    did_last_ffmpeg_hit_dxgi_loss,
    should_use_ffmpeg_mic_dsp,
    wants_ffmpeg_arnndn,
)
from recorder.native_bridge import (
    collect_and_save_diagnostic,
    load_recorder_backend,
    remux_mkv_segments_to_mp4,
    try_rebuild_native_recorder,
)

class FFmpegRecorder:
    _GLOBAL_LOCK = threading.RLock()

    def __init__(self, config: Config) -> None:
        self.config = config
        self.current_filepath: Optional[str] = None
        self.temp_filepath: Optional[str] = None
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
        self.actual_encoder = determine_encoder(self.config, self.ffmpeg_path)
        self._last_configured_encoder = self.config.RECORD_ENCODER

        try_rebuild_native_recorder()
        self.native_lib, self.backend_mode = load_recorder_backend()
        self._disable_ffmpeg_arnndn = False
        if should_use_ffmpeg_mic_dsp(self.backend_mode) and wants_ffmpeg_arnndn(self.config):
            ensure_rnnoise_model_async(self.app_data_dir)

    def _launch_pipeline(
        self,
        use_chat_track: bool,
        allow_gdigrab_fallback: bool = True,
    ) -> tuple[bool, str, str]:
        result = launch_pipeline_with_fallbacks(
            config=self.config,
            native_lib=self.native_lib,
            backend_mode=self.backend_mode,
            ffmpeg_path=self.ffmpeg_path,
            initial_encoder=self.actual_encoder,
            current_filepath=self.current_filepath or "",
            temp_filepath=self.temp_filepath or "",
            use_chat_track=use_chat_track,
            sw_download_fallback_encoders=self._sw_download_fallback_encoders,
            disable_ffmpeg_arnndn=self._disable_ffmpeg_arnndn,
            allow_gdigrab_fallback=allow_gdigrab_fallback,
        )
        self.actual_encoder = result.final_encoder
        self._disable_ffmpeg_arnndn = result.disable_ffmpeg_arnndn
        return result.started_ok, result.used_encoder, result.used_capture

    def start_recording(self) -> str:
        with self._lock:
            return self._start_recording_locked()

    def _start_recording_locked(self) -> str:
        if self.native_lib.Recorder_IsRunning():
            logging.info("[FFmpegRecorder] Already recording.")
            return self.current_filepath or ""

        try:
            # ユーザーの設定変更や前回の一時フォールバックから復帰できるよう毎回エンコーダを確定
            self._last_configured_encoder = self.config.RECORD_ENCODER
            self.actual_encoder = determine_encoder(self.config, self.ffmpeg_path)

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

            chat_separate = bool(getattr(self.config, "RECORD_AUDIO_CHAT_SEPARATE", False))
            chat_device = str(getattr(self.config, "RECORD_AUDIO_CHAT", "None") or "None").strip()
            supports_chat = bool(
                hasattr(self.native_lib, "Recorder_SupportsChatTrack")
                and self.native_lib.Recorder_SupportsChatTrack()
            )
            if chat_separate and chat_device.lower() != "none" and not supports_chat:
                # 未ビルドの旧DLLで6chパイプ不整合による音声破損を防ぐため4chに安全フォールバック
                logging.warning(
                    "[FFmpegRecorder] Separate chat track requested, but loaded native_recorder.dll does not support 6ch audio. Falling back to 4ch."
                )
            use_chat_track = chat_separate and supports_chat and (chat_device.lower() != "none")
            self._session_use_chat_track = use_chat_track

            # 初回録画時に裏で進行中のRNNoiseモデルDL完了を上限2.5秒だけ待ち、初回からarnndnを有効化
            if (
                should_use_ffmpeg_mic_dsp(self.backend_mode)
                and wants_ffmpeg_arnndn(self.config)
                and not self._disable_ffmpeg_arnndn
            ):
                wait_for_rnnoise_model_ready(self.app_data_dir, timeout=2.5)

            started_ok, used_encoder, used_capture = self._launch_pipeline(
                use_chat_track=use_chat_track,
                allow_gdigrab_fallback=True,
            )

            if not started_ok:
                err = self.native_lib.Recorder_GetLastError().decode("utf-8", errors="replace")
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

            dxgi_lost = did_last_ffmpeg_hit_dxgi_loss(self.config.SAVE_DIR)
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
            started_ok, used_encoder, used_capture = self._launch_pipeline(
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
        return bool(self.native_lib.Recorder_IsRunning())

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

    def cancel_recording(self) -> None:
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

    def stop_recording(self, target_filepath: Optional[str] = None) -> list[dict[str, int]]:
        logging.info("[FFmpegRecorder] Stopping NativeRecorder...")
        with self._lock:
            # 既に次の録画セッションへ切り替わっている場合は新セッションを誤停止しない
            if target_filepath and self.current_filepath and self.current_filepath != target_filepath:
                logging.info(
                    f"[FFmpegRecorder] Session already switched ({self.current_filepath}), skipping stop for {target_filepath}."
                )
                segment_entries = collect_segments_for_filepath(target_filepath)
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
                    segment_entries = collect_segments_for_filepath(current_filepath)
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

    def set_low_priority(self) -> None:
        if hasattr(self.native_lib, "Recorder_SetLowPriority"):
            try:
                self.native_lib.Recorder_SetLowPriority(True)
            except Exception as e:
                logging.warning(f"[FFmpegRecorder] Failed to set low priority: {e}")
