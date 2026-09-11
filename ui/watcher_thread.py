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

class WatcherThread(QThread):
    log_signal = pyqtSignal(str)
    match_saved_signal = pyqtSignal()
    recording_state_changed = pyqtSignal(bool)
    
    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.t = get_trans(self.config.LANGUAGE)
        self.store = MetadataStore(save_dir=self.config.SAVE_DIR)
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
        self.current_riot_id = None
        self.current_tag_line = None
        self.current_region = self.config.REGION
        self._stop_timer = None
        self._is_manual_action = False

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
            self.log_signal.emit(self.t.log_player_detect_failed)

    def start_manual_recording(self):
        if self.current_video_path is not None:
            self.log_signal.emit(self.t.log_manual_already_recording)
            return
        self.recording_start_time = time.time()
        self.local_round_events = []
        self._update_current_player()
        self.log_signal.emit(self.t.log_manual_starting)
        try:
            self.current_video_path = self.recorder.start_recording()
            self.log_signal.emit(self.t.log_manual_recording_to.format(path=self.current_video_path))
            self._is_manual_action = True
            self.recording_state_changed.emit(True)
            self._is_manual_action = False
        except Exception as e:
            self.log_signal.emit(self.t.log_manual_start_failed.format(error=e))

    def stop_manual_recording(self):
        if self.current_video_path is None:
            return
        self.log_signal.emit(self.t.log_manual_stopping)
        self._is_manual_action = True
        self._stop_and_process_recording()
        self._is_manual_action = False

    def handle_real_match_end(self):
        self.log_signal.emit(self.t.log_real_match_end)
        if self.current_video_path is not None:
            self.log_signal.emit(self.t.log_stopping_real_match_end)
            self._stop_and_process_recording()

    def handle_match_start(self, is_range: bool, match_start_timestamp: float = None, is_recovery: bool = False):
        map_name = getattr(self.watcher, 'current_map_name', '').lower()
        if is_range or map_name in ['the range', 'range', 'rangev2', 'poveglia', 'basictraining', 'shooting', 'tutorial']:
            self.log_signal.emit(self.t.log_range_detected_skip)
            return
            
        self.real_match_end_time_ms = None

        # 遅延停止タイマーが動いている間に次の試合が始まった場合、タイマーをキャンセルして即座に前の録画を終了する
        if self._stop_timer is not None:
            self._stop_timer.cancel()
            self._stop_timer = None
            if self.current_video_path is not None:
                self.log_signal.emit(self.t.log_new_match_during_delay)
                self._stop_and_process_recording()

        if self.current_video_path is not None:
            self.log_signal.emit(self.t.log_already_recording_cont)
            return

        self.recording_start_time = time.time()
        self.local_recording_start_time_ms = int(self.recording_start_time * 1000)
        self.local_round_events = []
        self._update_current_player()
        
        if is_recovery:
            self.log_signal.emit(self.t.log_recovery_mode)
        else:
            self.log_signal.emit(self.t.log_match_started)
            
        try:
            self.current_video_path = self.recorder.start_recording()
            self.log_signal.emit(self.t.log_recording_to.format(path=self.current_video_path))
            self.recording_state_changed.emit(True)
        except Exception as e:
            self.log_signal.emit(self.t.log_start_failed.format(error=e))

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
        map_name = getattr(self.watcher, 'current_map_name', '').lower()
        if is_range or map_name in ['the range', 'range', 'poveglia']:
            return

        if self.current_video_path is None:
            return

        # 実際の試合終了時間を記録
        self.real_match_end_time_ms = int(time.time() * 1000)
        self.log_signal.emit(self.t.log_match_ended_wait)
        
        if self._stop_timer is not None:
            self._stop_timer.cancel()
            
        def delayed_stop(video_path_to_stop):
            if self.current_video_path == video_path_to_stop:
                self.log_signal.emit(self.t.log_stopping_after_delay)
                self._stop_and_process_recording()

        self._stop_timer = threading.Timer(15.0, delayed_stop, args=[self.current_video_path])
        self._stop_timer.start()

    def _stop_and_process_recording(self):
        self.recording_end_time = time.time()
        
        video_path = self.current_video_path
        start_time = self.recording_start_time
        end_time = self.recording_end_time
        events = list(self.local_round_events)
        ability_events = list(self.local_ability_events)
        start_time_ms = getattr(self, 'local_recording_start_time_ms', None)
        real_match_end_time_ms = getattr(self, 'real_match_end_time_ms', None)
        
        # UIフリーズを防ぐため、状態を即座にリセット
        self.current_video_path = None
        self.recording_state_changed.emit(False)
        
        # 録画停止処理（FFmpegの終了待ちなど）を別スレッドで実行
        threading.Thread(
            target=self._async_stop_and_process,
            args=(video_path, start_time, end_time, events, ability_events, start_time_ms, real_match_end_time_ms),
            daemon=True
        ).start()

    def _async_stop_and_process(self, video_path, start_time, end_time, events, ability_events, start_time_ms, real_match_end_time_ms=None):
        try:
            self.recorder.stop_recording()
        except Exception as e:
            self.log_signal.emit(self.t.log_stop_failed.format(error=e))

        # ローカルログから取得したマップ名を使用
        map_name = getattr(self.watcher, 'current_map_name', 'Fetching...')
        if map_name == "Unknown":
            map_name = "Fetching..."

        # API取得前に仮のメタデータを保存し、UIに即時表示させる
        temp_match_data = {
            "metadata": {
                "matchid": f"pending_{int(start_time)}",
                "map": map_name,
                "game_start": int(start_time),
                "game_length": int(end_time - start_time) if end_time > start_time else 0,
                "mode": "Unknown"
            },
            "players": {"all_players": []},
            "kills": [],
            "rounds": [],
            "local_video_path": video_path,
            "local_match_start_time": start_time,
            "local_match_end_time": end_time,
            "local_round_events": events,
            "local_ability_events": ability_events,
            "is_fetching_api": True
        }
        if start_time_ms is not None:
            temp_match_data['local_recording_start_time_ms'] = start_time_ms
        if real_match_end_time_ms is not None:
            temp_match_data['local_real_match_end_time_ms'] = real_match_end_time_ms
            
        temp_filepath = self.store.save_match_metadata(temp_match_data, 0)
        self.match_saved_signal.emit()
        
        # API取得処理へ移行（すでに別スレッド内なので直接呼び出し）
        self._fetch_api_and_save(video_path, start_time, end_time, events, ability_events, temp_filepath, start_time_ms, real_match_end_time_ms)

    def _fetch_api_and_save(self, video_path, start_time, end_time, events, ability_events=None, temp_filepath=None, start_time_ms=None, real_match_end_time_ms=None):
        if ability_events is None:
            ability_events = []
        self.log_signal.emit(self.t.log_api_checking)
        
        if not self.current_riot_id or not self.current_tag_line:
            self.log_signal.emit(self.t.log_api_no_player_id)
            self._create_dummy_metadata(video_path, start_time, end_time, events, temp_filepath, start_time_ms, real_match_end_time_ms)
            return
            
        self.log_signal.emit(self.t.log_api_fetching.format(name=self.current_riot_id, tag=self.current_tag_line, region=self.current_region))
        api = HenrikAPI(self.current_region, self.current_riot_id, self.current_tag_line)
        
        match_data = None
        mmr_change = 0

        for attempt in range(3):
            time.sleep(20)
            try:
                api_match_data = api.fetch_latest_match()
                game_start = api_match_data.get('metadata', {}).get('game_start', 0)
                
                if abs(game_start - start_time) < 3600:
                    match_data = api_match_data
                    match_id = match_data['metadata']['matchid']
                    mode = match_data.get('metadata', {}).get('mode', '')
                    mmr_change = 0
                    
                    # デスマッチなどMMRが変動しないモードはスキップして高速化
                    if mode.lower() not in ['deathmatch', 'custom game', 'escalation', 'snowball fight', 'replication']:
                        try:
                            mmr_change = api.fetch_mmr_change(match_id)
                        except Exception as e:
                            self.log_signal.emit(self.t.log_api_mmr_skipped.format(error=e))
                            
                    self.log_signal.emit(self.t.log_api_fetch_success)
                    break
            except Exception as e:
                self.log_signal.emit(self.t.log_api_fetch_error.format(attempt=attempt+1, error=e))

        if match_data:
            try:
                # パーティメンバーの抽出
                party_members = []
                target_party_id = None
                players = match_data.get("players", {}).get("all_players", [])
                for p in players:
                    if p.get("name", "").lower() == self.current_riot_id.lower() and p.get("tag", "").lower() == self.current_tag_line.lower():
                        target_party_id = p.get("party_id")
                        break
                if target_party_id:
                    for p in players:
                        if p.get("party_id") == target_party_id:
                            party_members.append(f"{p.get('name')}#{p.get('tag')}")
                
                match_data['party_members'] = party_members
                match_data['local_video_path'] = video_path
                match_data['local_match_start_time'] = start_time
                match_data['local_match_end_time'] = end_time
                match_data['local_round_events'] = events
                match_data['local_ability_events'] = ability_events
                match_data['is_fetching_api'] = False
                if start_time_ms is not None:
                    match_data['local_recording_start_time_ms'] = start_time_ms
                elif hasattr(self, 'local_recording_start_time_ms'):
                    match_data['local_recording_start_time_ms'] = self.local_recording_start_time_ms
                if real_match_end_time_ms is not None:
                    match_data['local_real_match_end_time_ms'] = real_match_end_time_ms
                        
                filepath = self.store.save_match_metadata(match_data, mmr_change)
                
                # 仮のメタデータファイルを削除
                if temp_filepath and os.path.exists(temp_filepath) and temp_filepath != filepath:
                    try:
                        os.remove(temp_filepath)
                    except Exception:
                        pass
                        
                self.log_signal.emit(self.t.log_storage_saved.format(path=filepath))
                self.match_saved_signal.emit()
            except Exception as e:
                self.log_signal.emit(self.t.log_process_metadata_failed.format(error=e))
        else:
            self.log_signal.emit(self.t.log_api_not_found)
            self._create_dummy_metadata(video_path, start_time, end_time, events, temp_filepath, start_time_ms, real_match_end_time_ms)

    def _get_pending_videos(self):
        if not os.path.exists(self.config.SAVE_DIR):
            return []
            
        videos = []
        jsons = []
        
        for f in os.listdir(self.config.SAVE_DIR):
            if f.endswith('.mp4'):
                videos.append(f)
            elif f.endswith('.json'):
                jsons.append(f)
                
        handled_videos = set()
        for jf in jsons:
            try:
                with open(os.path.join(self.config.SAVE_DIR, jf), 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    match_info = data.get("match_info", data)
                    vpath = match_info.get("local_video_path", "")
                    if vpath:
                        handled_videos.add(os.path.basename(vpath.replace("\\", "/")))
            except Exception:
                pass
                
        pending = []
        date_pattern = re.compile(r"(\d{8}_\d{6})")
        
        for v in videos:
            if v not in handled_videos:
                v_path = os.path.join(self.config.SAVE_DIR, v)
                
                # 0バイトのファイルは録画失敗なので削除してスキップ
                if os.path.getsize(v_path) == 0:
                    try:
                        os.remove(v_path)
                    except Exception:
                        pass
                    continue
                    
                if time.time() - os.path.getmtime(v_path) < 60:
                    continue
                    
                match = date_pattern.search(v)
                if match:
                    try:
                        dt = datetime.strptime(match.group(1), "%Y%m%d_%H%M%S")
                        pending.append((v_path, dt.timestamp()))
                    except Exception:
                        pass
        return pending

    def _create_dummy_metadata(self, video_path, vid_time, end_time=0, events=None, temp_filepath=None, start_time_ms=None, real_match_end_time_ms=None):
        if events is None:
            events = []
            
        map_name = getattr(self.watcher, 'current_map_name', 'Custom / Unknown')
        if map_name == "Unknown":
            map_name = "Custom / Unknown"
            
        match_data = {
            "metadata": {
                "matchid": f"custom_{int(vid_time)}",
                "map": map_name,
                "game_start": int(vid_time),
                "game_length": int(end_time - vid_time) if end_time > vid_time else 0,
                "mode": "Custom"
            },
            "players": {"all_players": []},
            "kills": [],
            "rounds": [],
            "local_video_path": video_path,
            "local_match_start_time": vid_time,
            "local_match_end_time": end_time,
            "local_round_events": events,
            "local_ability_events": [],
            "is_fetching_api": False
        }
        if start_time_ms is not None:
            match_data['local_recording_start_time_ms'] = start_time_ms
        elif hasattr(self, 'local_recording_start_time_ms'):
            match_data['local_recording_start_time_ms'] = self.local_recording_start_time_ms
        if real_match_end_time_ms is not None:
            match_data['local_real_match_end_time_ms'] = real_match_end_time_ms
            
        filepath = self.store.save_match_metadata(match_data, 0)
        
        if temp_filepath and os.path.exists(temp_filepath) and temp_filepath != filepath:
            try:
                os.remove(temp_filepath)
            except Exception:
                pass
                
        self.match_saved_signal.emit()

    def _cleanup_old_records(self):
        if self.config.AUTO_DELETE_DAYS <= 0:
            return
            
        save_dir = self.config.SAVE_DIR
        if not os.path.exists(save_dir):
            return
            
        now = time.time()
        cutoff = now - (self.config.AUTO_DELETE_DAYS * 86400)
        
        bases_to_delete = set()
        bases_to_keep = set()
        
        for f in os.listdir(save_dir):
            if f.endswith('.json'):
                filepath = os.path.join(save_dir, f)
                try:
                    with open(filepath, 'r', encoding='utf-8') as jf:
                        data = json.load(jf)
                    
                    # 動画ファイルが存在しない、または0バイトの古いJSONは削除対象にする
                    video_path = data.get("match_info", data).get("local_video_path", "")
                    is_broken = False
                    if not video_path or not os.path.exists(video_path) or os.path.getsize(video_path) == 0:
                        if time.time() - os.path.getmtime(filepath) > 300: # 5分以上経過していれば壊れていると判定
                            is_broken = True
                            
                    if is_broken:
                        bases_to_delete.add(f[:-5])
                    elif data.get("is_favorite", False):
                        bases_to_keep.add(f[:-5])
                    elif os.path.getmtime(filepath) < cutoff:
                        bases_to_delete.add(f[:-5])
                except Exception:
                    if os.path.getmtime(filepath) < cutoff:
                        bases_to_delete.add(f[:-5])
                        
        for f in os.listdir(save_dir):
            if f.endswith(('.mp4', '.jpg')):
                base = os.path.splitext(f)[0]
                if base not in bases_to_keep and base not in bases_to_delete:
                    filepath = os.path.join(save_dir, f)
                    try:
                        if os.path.getmtime(filepath) < cutoff:
                            bases_to_delete.add(base)
                    except Exception:
                        pass

        deleted_count = 0
        for base in bases_to_delete:
            for ext in ['.json', '.mp4', '.jpg']:
                filepath = os.path.join(save_dir, base + ext)
                if os.path.exists(filepath):
                    try:
                        os.remove(filepath)
                        deleted_count += 1
                    except Exception as e:
                        self.log_signal.emit(self.t.log_bg_delete_failed.format(path=filepath, error=e))
                        
        if deleted_count > 0:
            self.log_signal.emit(self.t.log_bg_auto_deleted.format(count=deleted_count))
            self.match_saved_signal.emit()

    def _background_worker(self):
        self._cleanup_old_records()
        last_cleanup_time = time.time()
        last_api_check_time = time.time()
        
        while self._is_running:
            time.sleep(1)
            now = time.time()
            
            # --- 1. 録画プロセスの死活監視と自動再開 (クラッシュ対策) ---
            if self.current_video_path is not None and getattr(self.watcher, 'is_in_match', False):
                # 録画開始後にマップ名が遅れて判明し、射撃場だった場合のキャンセル処理
                map_name = getattr(self.watcher, 'current_map_name', '').lower()
                is_range = getattr(self.watcher, 'is_range', False)
                if is_range or map_name in ['the range', 'range', 'rangev2', 'poveglia', 'basictraining', 'shooting', 'tutorial']:
                    self.log_signal.emit(self.t.log_range_transition_cancel)
                    video_to_delete = self.current_video_path
                    self.current_video_path = None
                    self.recording_state_changed.emit(False)
                    self.watcher.is_in_match = False
                    
                    try:
                        self.recorder.stop_recording()
                    except Exception as e:
                        self.log_signal.emit(self.t.log_cancel_stop_failed.format(error=e))
                        
                    # 少し待ってからファイルを削除
                    def delete_cancelled_video(path):
                        time.sleep(2)
                        try:
                            if os.path.exists(path):
                                os.remove(path)
                                self.log_signal.emit(self.t.log_cancelled_video_deleted.format(path=path))
                        except Exception as e:
                            self.log_signal.emit(self.t.log_cancel_delete_failed.format(error=e))
                            
                    threading.Thread(target=delete_cancelled_video, args=(video_to_delete,), daemon=True).start()
                    continue

                if self.recorder.process is not None:
                    returncode = self.recorder.process.poll()
                    if returncode is not None:
                        self.log_signal.emit(self.t.log_ffmpeg_crashed.format(code=returncode))
                        # 現在の録画を保存処理に回す
                        self._stop_and_process_recording()
                        # 少し待機してから再開
                        time.sleep(2)
                        # リカバリーモードとして再開
                        self.handle_match_start(is_range=False, is_recovery=True)
                        continue

            # --- 2. 定期クリーンアップ (1時間ごと) ---
            if now - last_cleanup_time > 3600:
                self._cleanup_old_records()
                last_cleanup_time = now
            
            # --- 3. 未処理動画のAPIチェック (60秒ごと) ---
            if now - last_api_check_time > 60:
                last_api_check_time = now
                
                if getattr(self.watcher, 'is_in_match', False):
                    continue
                    
                pending_videos = self._get_pending_videos()
                if not pending_videos:
                    continue
                    
                self.log_signal.emit(self.t.log_bg_pending_videos.format(count=len(pending_videos)))
                
                try:
                    if not self.current_riot_id or not self.current_tag_line:
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
                                
                            self.log_signal.emit(self.t.log_bg_player_detected.format(name=name, tag=tag, region=self.current_region))
                        else:
                            continue
                            
                    self.log_signal.emit(self.t.log_bg_fetching.format(name=self.current_riot_id, tag=self.current_tag_line, region=self.current_region))
                    api = HenrikAPI(self.current_region, self.current_riot_id, self.current_tag_line)
                    
                    try:
                        api_match_data = api.fetch_latest_match(retries=1, delay=2)
                    except Exception as e:
                        self.log_signal.emit(self.t.log_bg_api_error.format(error=e))
                        api_match_data = None
                        
                    if not api_match_data:
                        for video_path, vid_time in pending_videos:
                            if time.time() - vid_time > 3600:
                                self.log_signal.emit(self.t.log_bg_fetch_failed_perm.format(file=os.path.basename(video_path)))
                                self._create_dummy_metadata(video_path, vid_time)
                        continue
                        
                    game_start = api_match_data.get('metadata', {}).get('game_start', 0)
                    
                    for video_path, vid_time in pending_videos:
                        diff = abs(game_start - vid_time)
                        
                        if diff < 3600:
                            self.log_signal.emit(self.t.log_bg_match_found.format(file=os.path.basename(video_path)))
                            match_id = api_match_data['metadata']['matchid']
                            mode = api_match_data.get('metadata', {}).get('mode', '')
                            mmr_change = 0
                            
                            if mode.lower() not in ['deathmatch', 'custom game', 'escalation', 'snowball fight', 'replication']:
                                try:
                                    mmr_change = api.fetch_mmr_change(match_id, retries=1, delay=2)
                                except Exception as e:
                                    self.log_signal.emit(self.t.log_bg_mmr_skipped.format(error=e))
                            
                            api_match_data['local_video_path'] = video_path
                            filepath = self.store.save_match_metadata(api_match_data, mmr_change)
                            self.log_signal.emit(self.t.log_bg_saved.format(path=filepath))
                            self.match_saved_signal.emit()
                            
                        elif game_start > vid_time + 3600 or time.time() - vid_time > 3600:
                            self.log_signal.emit(self.t.log_bg_custom_or_unavailable.format(file=os.path.basename(video_path)))
                            self._create_dummy_metadata(video_path, vid_time)
                            
                except Exception as e:
                    self.log_signal.emit(self.t.log_bg_error.format(error=e))

    def run(self):
        self.log_signal.emit(self.t.log_app_initialized)
        
        self.bg_thread = threading.Thread(target=self._background_worker, daemon=True)
        self.bg_thread.start()
        
        try:
            self.watcher.start_watching()
        except Exception as e:
            self.log_signal.emit(self.t.log_fatal_error.format(error=e))
            self.recorder.stop_recording()

    def stop(self):
        self._is_running = False
        if self.current_video_path is not None:
            self.recorder.stop_recording()
        self.terminate()