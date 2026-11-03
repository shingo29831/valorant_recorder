import time
import os
import json
import re
import threading
from datetime import datetime
from PyQt6.QtCore import QThread, pyqtSignal
from core.config import Config
from core.i18n import get_trans
from watcher.log_watcher import LogWatcher
from api.henrik_api import HenrikAPI
from storage.metadata_store import MetadataStore
from recorder.ffmpeg_recorder import FFmpegRecorder
from ui.watcher_bg_service import MatchMetadataService
from ui.watcher_supervisor import WatcherSupervisor

class WatcherThread(QThread):
    log_signal = pyqtSignal(str)
    match_saved_signal = pyqtSignal()
    recording_state_changed = pyqtSignal(bool)
    
    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.t = get_trans(self.config.LANGUAGE)
        self.store = MetadataStore(config=self.config)
        self.metadata_service = MatchMetadataService(
            config=self.config,
            store=self.store,
            t=self.t,
            emit_log=self.log_signal.emit,
            emit_match_saved=self.match_saved_signal.emit,
        )
        self.recorder = FFmpegRecorder(config=self.config)
        self.watcher = LogWatcher(
            on_match_start=self.handle_match_start,
            on_match_end=self.handle_match_end,
            on_real_match_end=self.handle_real_match_end,
            on_round_phase_changed=self.handle_round_phase_changed,
            on_ability_used=self.handle_ability_used,
            on_performance_drop=self.handle_performance_drop
        )
        self.current_video_path = None
        self.local_round_events = []
        self.local_ability_events = []
        self.recording_start_time = 0
        self.real_start_time = 0
        self._is_running = True
        self.current_riot_id = self.config.RIOT_ID if self.config.RIOT_ID else None
        self.current_tag_line = self.config.TAG_LINE if self.config.TAG_LINE else None
        self.current_region = self.config.REGION
        self._stop_timer = None
        self._is_manual_action = False
        self._is_manual_recording = False
        self._processing_video_paths = set()
        self.supervisor = WatcherSupervisor(self)

    def _update_current_player(self):
        from scripts.get_local_api_info import get_current_player, get_client_region
        name, tag = get_current_player()
        region = get_client_region()
        if name and tag:
            self.current_riot_id = name
            self.current_tag_line = tag
            self.current_region = region if region else self.config.REGION
            
            # UI側が自分を特定できるようにConfigを更新して保存する
            if self.config.RIOT_ID != name or self.config.TAG_LINE != tag or self.config.REGION != self.current_region:
                self.config.RIOT_ID = name
                self.config.TAG_LINE = tag
                self.config.REGION = self.current_region
                self.config.save()
                
            self.log_signal.emit(self.t.log_player_detected.format(name=name, tag=tag, region=self.current_region))
        else:
            if self.config.RIOT_ID and self.config.TAG_LINE:
                self.current_riot_id = self.config.RIOT_ID
                self.current_tag_line = self.config.TAG_LINE
                self.current_region = self.config.REGION
            self.log_signal.emit(self.t.log_player_detect_failed)

    def _cancel_stop_timer(self):
        if self._stop_timer is not None:
            try:
                self._stop_timer.cancel()
            except Exception:
                pass
            self._stop_timer = None

    def start_manual_recording(self):
        if self.current_video_path is not None:
            self.log_signal.emit(self.t.log_manual_already_recording)
            return
        self._cancel_stop_timer()
        self.recording_start_time = time.time()
        self.local_recording_start_time_ms = int(self.recording_start_time * 1000)
        self.real_match_end_time_ms = None
        self.local_round_events = []
        self.local_ability_events = []
        self._update_current_player()
        self.log_signal.emit(self.t.log_manual_starting)
        self._is_manual_recording = True
        try:
            self.current_video_path = self.recorder.start_recording()
            self.log_signal.emit(self.t.log_manual_recording_to.format(path=self.current_video_path))
            self._is_manual_action = True
            self.recording_state_changed.emit(True)
            self._is_manual_action = False
        except Exception as e:
            self._is_manual_recording = False
            self.current_video_path = None
            err_str = str(e)
            try:
                msg = self.t.log_manual_start_failed.format(error=err_str)
                if err_str not in msg:
                    msg = f"{msg}: {err_str}"
            except Exception:
                msg = f"録画の開始に失敗しました: {err_str}"
            self.log_signal.emit(msg)

    def stop_manual_recording(self):
        if self.current_video_path is None:
            return
        self._cancel_stop_timer()
        self.log_signal.emit(self.t.log_manual_stopping)
        self._is_manual_action = True
        self._stop_and_process_recording()
        self._is_manual_action = False

    def handle_real_match_end(self):
        if self._is_manual_recording:
            return
        self.log_signal.emit(self.t.log_real_match_end)
        if self.current_video_path is not None:
            self.log_signal.emit(self.t.log_stopping_real_match_end)
            self._stop_and_process_recording()

    def _is_range_match(self, is_range: bool = False) -> bool:
        if is_range or getattr(self.watcher, 'is_range', False):
            return True
        if hasattr(self.watcher, 'is_range_detected') and self.watcher.is_range_detected():
            return True
        map_name = getattr(self.watcher, 'current_map_name', '').lower()
        return any(k in map_name for k in ['the range', 'range', 'rangev2', 'poveglia', 'basictraining', 'shooting', 'tutorial'])

    def handle_match_start(self, is_range: bool, match_start_timestamp: float = None, is_recovery: bool = False):
        if self._is_range_match(is_range):
            self.log_signal.emit(self.t.log_range_detected_skip)
            return
            
        self.real_match_end_time_ms = None

        if self._is_manual_recording and self.current_video_path is not None:
            self._cancel_stop_timer()
            self.log_signal.emit(self.t.log_already_recording_cont)
            return

        # 遅延停止タイマーが動いている間に次の試合が始まった場合、タイマーをキャンセルして即座に前の録画を終了する
        if self._stop_timer is not None:
            self._cancel_stop_timer()
            if self.current_video_path is not None:
                self.log_signal.emit(self.t.log_new_match_during_delay)
                self._stop_and_process_recording()

        if self.current_video_path is not None:
            self.log_signal.emit(self.t.log_already_recording_cont)
            return

        self.recording_start_time = time.time()
        self.local_recording_start_time_ms = int(self.recording_start_time * 1000)
        self.local_round_events = []
        self.local_ability_events = []
        self._is_manual_recording = False
        self._update_current_player()
        
        if is_recovery:
            self.log_signal.emit(self.t.log_recovery_mode)
        else:
            self.log_signal.emit(self.t.log_match_started)
            
        try:
            import logging
            logging.info("[WatcherThread] Attempting to start FFmpeg recording...")
            self.current_video_path = self.recorder.start_recording()
            logging.info(f"[WatcherThread] Recording successfully started: {self.current_video_path}")
            self.log_signal.emit(self.t.log_recording_to.format(path=self.current_video_path))
            self.recording_state_changed.emit(True)
        except Exception as e:
            import logging
            err_str = str(e)
            logging.error(f"[WatcherThread] Failed to start recording: {err_str}")
            # 録画失敗時はログ監視側の状態をリセットし次マッチ検知不能を防ぐ
            self.current_video_path = None
            if hasattr(self, 'watcher'):
                self.watcher.is_in_match = False
                self.watcher.has_entered_in_progress = False
            try:
                msg = self.t.log_start_failed.format(error=err_str)
                if err_str not in msg:
                    msg = f"{msg}: {err_str}"
            except Exception:
                msg = f"録画の開始に失敗しました: {err_str}"
            self.log_signal.emit(msg)

    def handle_round_phase_changed(self, phase: str, phase_timestamp: float = None):
        if self.current_video_path is not None:
            # 絶対時刻(UNIXタイムスタンプミリ秒)で記録し、後でAPIデータと高精度に同期する
            ts_ms = int((phase_timestamp if phase_timestamp else time.time()) * 1000)
            self.local_round_events.append({"phase": phase, "time_ms": ts_ms})
            self.log_signal.emit(self.t.log_round_phase_changed.format(phase=phase, ts=ts_ms))

    def handle_ability_used(self, timestamp: float):
        if self.current_video_path is not None:
            ts_ms = int(timestamp * 1000)
            self.local_ability_events.append({"time_ms": ts_ms})
            
    def handle_performance_drop(self):
        if getattr(self.config, 'AUTO_PERFORMANCE_CONTROL', False) and self.current_video_path is not None:
            self.log_signal.emit(self.t.log_performance_drop)
            self.recorder.set_low_priority()

    def handle_match_end(self, is_range: bool):
        if self._is_manual_recording:
            return

        if self._is_range_match(is_range):
            return

        if self.current_video_path is None:
            return

        # 実際の試合終了時間を記録
        self.real_match_end_time_ms = int(time.time() * 1000)
        self.log_signal.emit(self.t.log_match_ended_wait)
        
        self._cancel_stop_timer()
            
        def delayed_stop(video_path_to_stop):
            self._stop_timer = None
            if not self._is_manual_recording and self.current_video_path == video_path_to_stop:
                self.log_signal.emit(self.t.log_stopping_after_delay)
                self._stop_and_process_recording()

        self._stop_timer = threading.Timer(15.0, delayed_stop, args=[self.current_video_path])
        self._stop_timer.start()

    def _stop_and_process_recording(self):
        self._cancel_stop_timer()
        self.recording_end_time = time.time()
        
        video_path = self.current_video_path
        start_time = self.recording_start_time
        end_time = self.recording_end_time
        events = list(self.local_round_events)
        ability_events = list(self.local_ability_events)
        start_time_ms = getattr(self, 'local_recording_start_time_ms', None)
        real_match_end_time_ms = getattr(self, 'real_match_end_time_ms', None)
        
        # UIフリーズを防ぐため、状態を即座にリセット
        if video_path:
            self._processing_video_paths.add(os.path.basename(video_path))
        self.current_video_path = None
        self._is_manual_recording = False
        self.recording_state_changed.emit(False)
        
        # 録画停止処理（FFmpegの終了待ちなど）を別スレッドで実行
        threading.Thread(
            target=self._async_stop_and_process,
            args=(video_path, start_time, end_time, events, ability_events, start_time_ms, real_match_end_time_ms),
            daemon=True
        ).start()

    def _async_stop_and_process(self, video_path, start_time, end_time, events, ability_events, start_time_ms, real_match_end_time_ms=None):
        try:
            try:
                self.recorder.stop_recording(target_filepath=video_path)
            except Exception as e:
                self.log_signal.emit(self.t.log_stop_failed.format(error=e))

            raw_map = getattr(self.watcher, 'current_map_name', 'Unknown')
            temp_filepath = self.metadata_service.save_temp_metadata(
                video_path=video_path,
                map_name="Fetching..." if raw_map == "Unknown" else raw_map,
                start_time=start_time,
                end_time=end_time,
                events=events,
                ability_events=ability_events,
                start_time_ms=start_time_ms,
                real_match_end_time_ms=real_match_end_time_ms,
            )
            self.metadata_service.fetch_api_and_save(
                video_path=video_path,
                start_time=start_time,
                end_time=end_time,
                events=events,
                riot_id=self.current_riot_id,
                tag_line=self.current_tag_line,
                region=self.current_region,
                map_name="Custom / Unknown" if raw_map == "Unknown" else raw_map,
                ability_events=ability_events,
                temp_filepath=temp_filepath,
                start_time_ms=start_time_ms,
                real_match_end_time_ms=real_match_end_time_ms,
            )
        finally:
            if video_path:
                self._processing_video_paths.discard(os.path.basename(video_path))


    def run(self):
        self.log_signal.emit(self.t.log_app_initialized)
        self.supervisor.start()
        
        try:
            self.watcher.start_watching()
        except Exception as e:
            self.log_signal.emit(self.t.log_fatal_error.format(error=e))
            self.recorder.stop_recording()

    def stop(self):
        self._is_running = False
        self.supervisor.stop()
        if self._stop_timer is not None:
            self._stop_timer.cancel()
            self._stop_timer = None
        if hasattr(self, 'watcher') and hasattr(self.watcher, 'stop'):
            self.watcher.stop()
        if self.current_video_path is not None:
            self.recorder.stop_recording()
        if not self.wait(2000):
            self.terminate()
