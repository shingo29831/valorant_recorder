import time
import os
import json
import re
import threading
from datetime import datetime
from PyQt6.QtCore import QThread, pyqtSignal
from core.config import Config
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
        self.store = MetadataStore(save_dir=self.config.SAVE_DIR)
        self.recorder = FFmpegRecorder(config=self.config)
        self.watcher = LogWatcher(
            on_match_start=self.handle_match_start,
            on_match_end=self.handle_match_end,
            on_real_match_end=self.handle_real_match_end,
            on_round_phase_changed=self.handle_round_phase_changed
        )
        self.current_video_path = None
        self.local_round_events = []
        self.recording_start_time = 0
        self.real_start_time = 0
        self._is_running = True
        self.current_riot_id = None
        self.current_tag_line = None
        self.current_region = self.config.REGION

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
                
            self.log_signal.emit(f"[Watcher] Player detected: {name}#{tag} (Region: {self.current_region})")
        else:
            self.log_signal.emit("[Watcher] Failed to detect player from local API.")

    def start_manual_recording(self):
        if self.current_video_path is not None:
            self.log_signal.emit("[Manual] Already recording.")
            return
        self.recording_start_time = time.time()
        self.local_round_events = []
        self._update_current_player()
        self.log_signal.emit("[Manual] Starting manual recording...")
        try:
            self.current_video_path = self.recorder.start_recording()
            self.log_signal.emit(f"[Manual] Recording to: {self.current_video_path}")
            self.recording_state_changed.emit(True)
        except Exception as e:
            self.log_signal.emit(f"[Error] Failed to start manual recording: {e}")

    def stop_manual_recording(self):
        if self.current_video_path is None:
            return
        self.log_signal.emit("[Manual] Stopping manual recording...")
        self._stop_and_process_recording()

    def handle_real_match_end(self):
        self.log_signal.emit("[Recorder] Real match end verified in logs.")
        if self.current_video_path is not None:
            self.log_signal.emit("[Recorder] Stopping recording on real match end...")
            self._stop_and_process_recording()

    def handle_match_start(self, is_range: bool):
        if is_range:
            self.log_signal.emit("[Recorder] 射撃訓練場(Range)を検知しました。録画とAPI取得をスキップします。")
            return

        if self.current_video_path is not None:
            self.log_signal.emit("[Recorder] Already recording. Continuing...")
            return

        self.recording_start_time = time.time()
        self.local_round_events = []
        self._update_current_player()
        self.log_signal.emit("[Recorder] Match started. Starting FFmpeg recording...")
        try:
            self.current_video_path = self.recorder.start_recording()
            self.log_signal.emit(f"[Recorder] Recording to: {self.current_video_path}")
            self.recording_state_changed.emit(True)
        except Exception as e:
            self.log_signal.emit(f"[Error] Failed to start recording: {e}")

    def handle_round_phase_changed(self, phase: str):
        if self.recording_start_time > 0:
            time_ms = int((time.time() - self.recording_start_time) * 1000)
            self.local_round_events.append({"phase": phase, "time_ms": time_ms})
            self.log_signal.emit(f"[Recorder] Round phase changed: {phase} at {time_ms}ms")

    def handle_match_end(self, is_range: bool):
        if is_range:
            return

        if self.current_video_path is None:
            return

        self.log_signal.emit("[Recorder] Match ended. Stopping recording...")
        self._stop_and_process_recording()

    def _stop_and_process_recording(self):
        self.recording_end_time = time.time()
        try:
            self.recorder.stop_recording()
        except Exception as e:
            self.log_signal.emit(f"[Error] Failed to stop recording: {e}")

        self.recording_state_changed.emit(False)
        
        video_path = self.current_video_path
        start_time = self.recording_start_time
        end_time = self.recording_end_time
        events = list(self.local_round_events)
        self.current_video_path = None
        
        threading.Thread(
            target=self._fetch_api_and_save,
            args=(video_path, start_time, end_time, events),
            daemon=True
        ).start()

    def _fetch_api_and_save(self, video_path, start_time, end_time, events):
        self.log_signal.emit("[API] Checking for match data...")
        
        if not self.current_riot_id or not self.current_tag_line:
            self.log_signal.emit("[API] No player ID detected. Saving as local-only match.")
            self._create_dummy_metadata(video_path, start_time, end_time, events)
            return
            
        self.log_signal.emit(f"[API] Fetching match data for {self.current_riot_id}#{self.current_tag_line} (Region: {self.current_region})...")
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
                    try:
                        mmr_change = api.fetch_mmr_change(match_id)
                    except Exception as e:
                        self.log_signal.emit(f"[API] MMR fetch skipped (likely not competitive): {e}")
                        mmr_change = 0
                    self.log_signal.emit("[API] Successfully fetched current match data.")
                    break
            except Exception as e:
                self.log_signal.emit(f"[API] Error fetching match data (attempt {attempt+1}/3): {e}")

        if match_data:
            try:
                match_data['local_video_path'] = video_path
                match_data['local_match_start_time'] = start_time
                match_data['local_match_end_time'] = end_time
                match_data['local_round_events'] = events
                        
                filepath = self.store.save_match_metadata(match_data, mmr_change)
                self.log_signal.emit(f"[Storage] Metadata saved: {filepath}")
                self.match_saved_signal.emit()
            except Exception as e:
                self.log_signal.emit(f"[Error] Failed to process match metadata: {e}")
        else:
            self.log_signal.emit("[API] Match data not found after retries. Saving as local-only match.")
            self._create_dummy_metadata(video_path, start_time, end_time, events)

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

    def _create_dummy_metadata(self, video_path, vid_time, end_time=0, events=None):
        if events is None:
            events = []
        match_data = {
            "metadata": {
                "matchid": f"custom_{int(vid_time)}",
                "map": "Custom / Unknown",
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
            "local_round_events": events
        }
        self.store.save_match_metadata(match_data, 0)
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
                    if data.get("is_favorite", False):
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
                        self.log_signal.emit(f"[Background] Failed to delete old file {filepath}: {e}")
                        
        if deleted_count > 0:
            self.log_signal.emit(f"[Background] Auto-deleted {deleted_count} old file(s).")
            self.match_saved_signal.emit()

    def _background_worker(self):
        self._cleanup_old_records()
        last_cleanup_time = time.time()
        
        while self._is_running:
            time.sleep(60)
            
            now = time.time()
            if now - last_cleanup_time > 3600:
                self._cleanup_old_records()
                last_cleanup_time = now
            
            if self.watcher.is_in_match:
                continue
                
            pending_videos = self._get_pending_videos()
            if not pending_videos:
                continue
                
            self.log_signal.emit(f"[Background] Found {len(pending_videos)} pending video(s). Checking API...")
            
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
                            
                        self.log_signal.emit(f"[Background] Player detected: {name}#{tag} (Region: {self.current_region})")
                    else:
                        continue
                        
                self.log_signal.emit(f"[Background] Fetching match data for {self.current_riot_id}#{self.current_tag_line} (Region: {self.current_region})...")
                api = HenrikAPI(self.current_region, self.current_riot_id, self.current_tag_line)
                
                try:
                    api_match_data = api.fetch_latest_match(retries=1, delay=2)
                except Exception as e:
                    self.log_signal.emit(f"[Background] API fetch error: {e}")
                    api_match_data = None
                    
                if not api_match_data:
                    for video_path, vid_time in pending_videos:
                        if time.time() - vid_time > 3600:
                            self.log_signal.emit(f"[Background] Video {os.path.basename(video_path)} API fetch failed permanently. Saving as local-only.")
                            self._create_dummy_metadata(video_path, vid_time)
                    continue
                    
                game_start = api_match_data.get('metadata', {}).get('game_start', 0)
                
                for video_path, vid_time in pending_videos:
                    diff = abs(game_start - vid_time)
                    
                    if diff < 3600:
                        self.log_signal.emit(f"[Background] Match found for {os.path.basename(video_path)}.")
                        match_id = api_match_data['metadata']['matchid']
                        try:
                            mmr_change = api.fetch_mmr_change(match_id, retries=1, delay=2)
                        except Exception as e:
                            self.log_signal.emit(f"[Background] MMR fetch skipped (likely not competitive): {e}")
                            mmr_change = 0
                        
                        api_match_data['local_video_path'] = video_path
                        filepath = self.store.save_match_metadata(api_match_data, mmr_change)
                        self.log_signal.emit(f"[Background] Saved metadata: {filepath}")
                        self.match_saved_signal.emit()
                        
                    elif game_start > vid_time + 3600 or time.time() - vid_time > 3600:
                        self.log_signal.emit(f"[Background] Video {os.path.basename(video_path)} is likely a custom match or API not available. Skipping.")
                        self._create_dummy_metadata(video_path, vid_time)
                        
            except Exception as e:
                self.log_signal.emit(f"[Background] Error: {e}")

    def run(self):
        self.log_signal.emit("Valorant Recorder App initialized. Watching logs...")
        
        self.bg_thread = threading.Thread(target=self._background_worker, daemon=True)
        self.bg_thread.start()
        
        try:
            self.watcher.start_watching()
        except Exception as e:
            self.log_signal.emit(f"Fatal error: {e}")
            self.recorder.stop_recording()

    def stop(self):
        self._is_running = False
        if self.current_video_path is not None:
            self.recorder.stop_recording()
        self.terminate()