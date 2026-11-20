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
            on_performance_drop=self.handle_performance_drop,
            on_client_closed=self.handle_client_closed,
        )
        self.current_video_path = None
        self.local_round_events = []
        self.local_ability_events = []
        self.recording_start_time = 0
        self.real_start_time = 0
        self._is_running = True
        self.current_riot_id = self.config.RIOT_ID.strip() if self.config.RIOT_ID and self.config.RIOT_ID.strip() else None
        self.current_tag_line = self.config.TAG_LINE.strip() if self.config.TAG_LINE and self.config.TAG_LINE.strip() else None
        self.current_region = (self.config.REGION or "ap").strip() or "ap"
        self._match_riot_id = None
        self._match_tag_line = None
        self._match_region = None
        self._match_player_verified = False
        self._match_map_name = "Unknown"
        self._stop_timer = None
        self._is_manual_action = False
        self._is_manual_recording = False
        # I/OやFFmpeg待機では絶対に保持しない軽量なメモリ状態専用ロック
        self._state_lock = threading.Lock()
        self._recording_session_id = 0
        self._is_starting_recording = False
        self._processing_video_paths = set()
        self.supervisor = WatcherSupervisor(self)

    def _update_current_player(self) -> bool:
        from scripts.get_local_api_info import get_current_player, get_client_region
        name, tag = get_current_player()
        region = get_client_region()
        if name and tag:
            self.current_riot_id = name
            self.current_tag_line = tag
            self.current_region = region if region else (self.config.REGION or "ap")

            changed = False
            if hasattr(self.config, "register_known_account") and self.config.register_known_account(name, tag):
                changed = True

            # UI側が自分を特定できるようにConfigを更新して保存する
            if self.config.RIOT_ID != name or self.config.TAG_LINE != tag or self.config.REGION != self.current_region:
                self.config.RIOT_ID = name
                self.config.TAG_LINE = tag
                self.config.REGION = self.current_region
                changed = True
            if changed:
                self.config.save()

            self.log_signal.emit(self.t.log_player_detected.format(name=name, tag=tag, region=self.current_region))
            return True
        else:
            if region and self.config.REGION != region:
                self.current_region = region
                self.config.REGION = region
                self.config.save()
            if self.config.RIOT_ID and self.config.TAG_LINE:
                self.current_riot_id = self.config.RIOT_ID.strip()
                self.current_tag_line = self.config.TAG_LINE.strip()
                self.current_region = self.config.REGION or "ap"
            self.log_signal.emit(self.t.log_player_detect_failed)
            return False

    def _cancel_stop_timer(self):
        if self._stop_timer is not None:
            try:
                self._stop_timer.cancel()
            except Exception:
                pass
            self._stop_timer = None

    def _is_range_map_name(self, map_name: str) -> bool:
        if not map_name:
            return False
        lower_name = map_name.lower()
        return any(
            k in lower_name
            for k in ("the range", "range", "rangev2", "poveglia", "basictraining", "shooting", "tutorial", "pingo")
        )

    def _resolve_valid_watcher_map(self) -> str:
        cur_map = getattr(self.watcher, "current_map_name", "Unknown") or "Unknown"
        if cur_map != "Unknown" and not self._is_range_map_name(cur_map):
            return cur_map
        last_map = getattr(self.watcher, "last_valid_map_name", "Unknown") or "Unknown"
        if last_map != "Unknown" and not self._is_range_map_name(last_map):
            return last_map
        return "Unknown"

    def resume_active_recording_segment(self, allow_gdigrab_fallback: bool = False) -> bool:
        """試合中・手動録画中に中断されたパイプラインをUIロックを阻害せず同一セッションのまま復旧する"""
        with self._state_lock:
            if self._is_starting_recording or self.current_video_path is None:
                return False
            if not self._is_manual_recording:
                if not getattr(self.watcher, "is_in_match", False) or self._is_range_match():
                    return False
            session_id = self._recording_session_id
            expected_path = self.current_video_path

        if not hasattr(self.recorder, "resume_recording_segment"):
            return False

        # ロック外でFFmpeg起動検証を実行し、手動停止などUIスレッド操作のフリーズを防止
        resumed = bool(
            self.recorder.resume_recording_segment(
                expected_filepath=expected_path,
                allow_gdigrab_fallback=allow_gdigrab_fallback,
            )
        )
        if not resumed:
            return False

        # 復旧処理中に別スレッドから録画終了された場合は新セグメントを安全に停止してセッション整合を保つ
        with self._state_lock:
            still_active = (
                self._recording_session_id == session_id
                and self.current_video_path == expected_path
            )
        if not still_active:
            try:
                self.recorder.stop_recording(target_filepath=expected_path)
            except Exception:
                pass
            return False
        return True

    def handle_client_closed(self) -> bool:
        # ウィンドウ終了に伴うメニュー遷移で停止待機タイマーが動いていた場合はキャンセルして録画を継続する
        with self._state_lock:
            if self._is_manual_recording or self.current_video_path is None:
                return False
            if self._stop_timer is not None and not getattr(self.watcher, "_last_end_was_explicit", False):
                self._cancel_stop_timer()
                self.real_match_end_time_ms = None
                cancelled = True
            else:
                cancelled = False
        if cancelled:
            default_msg = (
                "VALORANTの終了を検知したため停止待機をキャンセルし、再接続に備えて録画を継続します。"
                if getattr(self.config, "LANGUAGE", "ja") == "ja"
                else "VALORANT closed; cancelled stop timer and continuing recording for reconnect."
            )
            self.log_signal.emit(getattr(self.t, "log_client_closed_continue", default_msg))
            return True
        return False

    def start_manual_recording(self):
        with self._state_lock:
            if self.current_video_path is not None or self._is_starting_recording:
                already_active = True
            else:
                already_active = False
                self._is_starting_recording = True
                self._cancel_stop_timer()
                self.recording_start_time = time.time()
                self.local_recording_start_time_ms = int(self.recording_start_time * 1000)
                self.real_match_end_time_ms = None
                self.local_round_events = []
                self.local_ability_events = []
                self._is_manual_recording = True
                self._recording_session_id += 1
        if already_active:
            self.log_signal.emit(self.t.log_manual_already_recording)
            return

        verified = self._update_current_player()
        with self._state_lock:
            self._match_riot_id = self.current_riot_id
            self._match_tag_line = self.current_tag_line
            self._match_region = self.current_region
            self._match_player_verified = verified
            self._match_map_name = self._resolve_valid_watcher_map()
        self.log_signal.emit(self.t.log_manual_starting)
        try:
            started_path = self.recorder.start_recording()
            with self._state_lock:
                self.current_video_path = started_path
                self._is_starting_recording = False
            self.log_signal.emit(self.t.log_manual_recording_to.format(path=started_path))
            self._is_manual_action = True
            self.recording_state_changed.emit(True)
            self._is_manual_action = False
        except Exception as e:
            with self._state_lock:
                self._is_manual_recording = False
                self._is_starting_recording = False
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
        with self._state_lock:
            has_video = self.current_video_path is not None
            if has_video:
                self._cancel_stop_timer()
        if not has_video:
            return
        self.log_signal.emit(self.t.log_manual_stopping)
        self._is_manual_action = True
        self._stop_and_process_recording()
        self._is_manual_action = False

    def handle_real_match_end(self):
        with self._state_lock:
            if self._is_manual_recording:
                return
            has_video = self.current_video_path is not None
        self.log_signal.emit(self.t.log_real_match_end)
        if has_video:
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
            # 前試合の終了待機タイマー中に射撃場へ入った場合は前試合の録画を即座に保存・終了する
            with self._state_lock:
                should_finalize = (
                    self._stop_timer is not None
                    and not self._is_manual_recording
                    and self.current_video_path is not None
                )
                if should_finalize:
                    self._cancel_stop_timer()
            if should_finalize:
                self._stop_and_process_recording()
            self.log_signal.emit(self.t.log_range_detected_skip)
            return

        need_stop_prev = False
        with self._state_lock:
            self.real_match_end_time_ms = None
            if self._is_manual_recording and self.current_video_path is not None:
                self._cancel_stop_timer()
                manual_cont = True
            else:
                manual_cont = False
                if self._stop_timer is not None:
                    self._cancel_stop_timer()
                    if self.current_video_path is not None:
                        need_stop_prev = True
        if manual_cont:
            self.log_signal.emit(self.t.log_already_recording_cont)
            return

        # 遅延停止タイマーが動いている間に次の試合が始まった場合、タイマーをキャンセルして即座に前の録画を終了する
        if need_stop_prev:
            self.log_signal.emit(self.t.log_new_match_during_delay)
            self._stop_and_process_recording()

        with self._state_lock:
            if self.current_video_path is not None or self._is_starting_recording:
                already_rec = True
            else:
                already_rec = False
                self._is_starting_recording = True
                self.recording_start_time = time.time()
                self.local_recording_start_time_ms = int(self.recording_start_time * 1000)
                self.local_round_events = []
                self.local_ability_events = []
                self._is_manual_recording = False
                self._recording_session_id += 1
        if already_rec:
            self.log_signal.emit(self.t.log_already_recording_cont)
            return

        verified = self._update_current_player()
        with self._state_lock:
            self._match_riot_id = self.current_riot_id
            self._match_tag_line = self.current_tag_line
            self._match_region = self.current_region
            self._match_player_verified = verified
            self._match_map_name = self._resolve_valid_watcher_map()
        
        if is_recovery:
            self.log_signal.emit(self.t.log_recovery_mode)
        else:
            self.log_signal.emit(self.t.log_match_started)
            
        try:
            import logging
            logging.info("[WatcherThread] Attempting to start FFmpeg recording...")
            started_path = self.recorder.start_recording()
            with self._state_lock:
                self.current_video_path = started_path
                self._is_starting_recording = False
            logging.info(f"[WatcherThread] Recording successfully started: {started_path}")
            self.log_signal.emit(self.t.log_recording_to.format(path=started_path))
            self.recording_state_changed.emit(True)
        except Exception as e:
            import logging
            err_str = str(e)
            logging.error(f"[WatcherThread] Failed to start recording: {err_str}")
            # 録画失敗時はログ監視側の状態をリセットし次マッチ検知不能を防ぐ
            with self._state_lock:
                self.current_video_path = None
                self._is_starting_recording = False
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
            if getattr(self, "_match_map_name", "Unknown") == "Unknown":
                self._match_map_name = self._resolve_valid_watcher_map()
            # 絶対時刻(UNIXタイムスタンプミリ秒)で記録し、後でAPIデータと高精度に同期する
            ts_ms = int((phase_timestamp if phase_timestamp else time.time()) * 1000)
            self.local_round_events.append({"phase": phase, "time_ms": ts_ms})
            self.log_signal.emit(self.t.log_round_phase_changed.format(phase=phase, ts=ts_ms))

    def handle_ability_used(self, timestamp: float):
        if self.current_video_path is not None:
            if getattr(self, "_match_map_name", "Unknown") == "Unknown":
                self._match_map_name = self._resolve_valid_watcher_map()
            ts_ms = int(timestamp * 1000)
            self.local_ability_events.append({"time_ms": ts_ms})
            
    def handle_performance_drop(self):
        if getattr(self.config, 'AUTO_PERFORMANCE_CONTROL', False) and self.current_video_path is not None:
            self.log_signal.emit(self.t.log_performance_drop)
            self.recorder.set_low_priority()

    def handle_match_end(self, is_range: bool):
        if self._is_range_match(is_range):
            return

        with self._state_lock:
            if self._is_manual_recording or self.current_video_path is None:
                return
            if getattr(self, "_match_map_name", "Unknown") == "Unknown":
                self._match_map_name = self._resolve_valid_watcher_map()
            # 実際の試合終了時間を記録
            self.real_match_end_time_ms = int(time.time() * 1000)
            target_video_path = self.current_video_path
            self._cancel_stop_timer()

            def delayed_stop(video_path_to_stop):
                with self._state_lock:
                    self._stop_timer = None
                    should_stop = (
                        not self._is_manual_recording
                        and self.current_video_path == video_path_to_stop
                    )
                if should_stop:
                    self.log_signal.emit(self.t.log_stopping_after_delay)
                    self._stop_and_process_recording()

            self._stop_timer = threading.Timer(15.0, delayed_stop, args=[target_video_path])
            self._stop_timer.start()

        self.log_signal.emit(self.t.log_match_ended_wait)

    def _stop_and_process_recording(self):
        with self._state_lock:
            self._cancel_stop_timer()
            video_path = self.current_video_path
            if video_path is None:
                return
            self.recording_end_time = time.time()
            start_time = self.recording_start_time
            end_time = self.recording_end_time
            events = list(self.local_round_events)
            ability_events = list(self.local_ability_events)
            start_time_ms = getattr(self, 'local_recording_start_time_ms', None)
            real_match_end_time_ms = getattr(self, 'real_match_end_time_ms', None)
            match_riot_id = getattr(self, '_match_riot_id', None) or self.current_riot_id
            match_tag_line = getattr(self, '_match_tag_line', None) or self.current_tag_line
            match_region = getattr(self, '_match_region', None) or self.current_region
            match_player_verified = bool(getattr(self, '_match_player_verified', False))
            # 射撃場や次マップへの遷移でwatcher.current_map_nameが上書きされても前試合の退避マップ名を最優先する
            match_map_name = getattr(self, '_match_map_name', 'Unknown') or 'Unknown'
            if match_map_name == "Unknown" or self._is_range_map_name(match_map_name):
                match_map_name = self._resolve_valid_watcher_map()

            # UIフリーズを防ぐため、軽量ロック内で状態を即座にリセットし世代IDを更新
            self._processing_video_paths.add(os.path.basename(video_path))
            self.current_video_path = None
            self._is_manual_recording = False
            self._recording_session_id += 1
            self.local_recording_start_time_ms = None
            self.real_match_end_time_ms = None
            self._match_riot_id = None
            self._match_tag_line = None
            self._match_region = None
            self._match_player_verified = False
            self._match_map_name = "Unknown"
            self.local_round_events = []
            self.local_ability_events = []

        # C++側が計測した映像・音声パイプラインの実開始時刻を優先して起動遅延ズレを防ぐ
        if hasattr(self.recorder, 'get_actual_start_time_ms'):
            native_start_ms = self.recorder.get_actual_start_time_ms()
            if native_start_ms:
                start_time_ms = native_start_ms
                start_time = native_start_ms / 1000.0

        # 再入デッドロック防止のためシグナルはロック外で発火
        self.recording_state_changed.emit(False)
        
        # 録画停止処理（FFmpegの終了待ちなど）を別スレッドで実行
        threading.Thread(
            target=self._async_stop_and_process,
            args=(
                video_path,
                start_time,
                end_time,
                events,
                ability_events,
                start_time_ms,
                real_match_end_time_ms,
                match_riot_id,
                match_tag_line,
                match_region,
                match_player_verified,
                match_map_name,
            ),
            daemon=True
        ).start()

    def _async_stop_and_process(
        self,
        video_path,
        start_time,
        end_time,
        events,
        ability_events,
        start_time_ms,
        real_match_end_time_ms=None,
        match_riot_id=None,
        match_tag_line=None,
        match_region=None,
        match_player_verified=False,
        match_map_name="Unknown",
    ):
        recording_segments = []
        try:
            try:
                stopped_segments = self.recorder.stop_recording(target_filepath=video_path)
                if isinstance(stopped_segments, list):
                    recording_segments = stopped_segments
            except Exception as e:
                self.log_signal.emit(self.t.log_stop_failed.format(error=e))

            # 録画開始時に未検証だった場合は終了時点の最新ログインアカウントを再取得する
            if not match_player_verified or not match_riot_id or not match_tag_line:
                if self._update_current_player():
                    match_riot_id = self.current_riot_id
                    match_tag_line = self.current_tag_line
                    match_region = self.current_region
                elif not match_riot_id or not match_tag_line:
                    match_riot_id = self.current_riot_id
                    match_tag_line = self.current_tag_line
                    match_region = self.current_region

            raw_map = match_map_name or "Unknown"
            temp_filepath = self.metadata_service.save_temp_metadata(
                video_path=video_path,
                map_name="Fetching..." if raw_map == "Unknown" else raw_map,
                start_time=start_time,
                end_time=end_time,
                events=events,
                ability_events=ability_events,
                start_time_ms=start_time_ms,
                real_match_end_time_ms=real_match_end_time_ms,
                recording_segments=recording_segments,
            )
            self.metadata_service.fetch_api_and_save(
                video_path=video_path,
                start_time=start_time,
                end_time=end_time,
                events=events,
                riot_id=match_riot_id,
                tag_line=match_tag_line,
                region=match_region or self.current_region,
                map_name="Custom / Unknown" if raw_map == "Unknown" else raw_map,
                ability_events=ability_events,
                temp_filepath=temp_filepath,
                start_time_ms=start_time_ms,
                real_match_end_time_ms=real_match_end_time_ms,
                recording_segments=recording_segments,
            )
        finally:
            if video_path:
                self._processing_video_paths.discard(os.path.basename(video_path))


    def run(self):
        self.log_signal.emit(self.t.log_app_initialized)
        if not self.current_riot_id or not self.current_tag_line:
            self._update_current_player()
        self.supervisor.start()
        
        try:
            self.watcher.start_watching()
        except Exception as e:
            self.log_signal.emit(self.t.log_fatal_error.format(error=e))
            self.recorder.stop_recording()

    def stop(self):
        self._is_running = False
        self.supervisor.stop()
        with self._state_lock:
            self._cancel_stop_timer()
            active_path = self.current_video_path
            self.current_video_path = None
            self._is_manual_recording = False
            self._recording_session_id += 1
        if hasattr(self, 'watcher') and hasattr(self.watcher, 'stop'):
            self.watcher.stop()
        if active_path is not None:
            try:
                self.recorder.stop_recording(target_filepath=active_path)
            except Exception:
                pass
        if self.isRunning() and not self.wait(1500):
            self.terminate()
            self.wait(500)
