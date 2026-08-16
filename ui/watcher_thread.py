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
    
    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.api = HenrikAPI(
            api_key=self.config.API_KEY,
            region=self.config.REGION,
            name=self.config.RIOT_ID,
            tag=self.config.TAG_LINE
        )
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

    def handle_real_match_end(self):
        self.log_signal.emit("[Recorder] Real match end verified in logs.")

    def handle_match_start(self, is_range: bool):
        if is_range:
            self.log_signal.emit("[Recorder] 射撃訓練場(Range)を検知しました。録画とAPI取得をスキップします。")
            return

        self.recording_start_time = time.time()
        self.local_round_events = []
        self.log_signal.emit("[Recorder] Match started. Starting FFmpeg recording...")
        try:
            self.current_video_path = self.recorder.start_recording()
            self.log_signal.emit(f"[Recorder] Recording to: {self.current_video_path}")
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

        self.recording_end_time = time.time()
        self.log_signal.emit("[Recorder] Match ended. Stopping recording...")
        try:
            self.recorder.stop_recording()
        except Exception as e:
            self.log_signal.emit(f"[Error] Failed to stop recording: {e}")

        self.log_signal.emit("[API] Checking for match data...")
        
        match_data = None
        mmr_change = 0

        for attempt in range(3):
            time.sleep(20)
            try:
                api_match_data = self.api.fetch_latest_match()
                game_start = api_match_data.get('metadata', {}).get('game_start', 0)
                
                if abs(game_start - self.recording_start_time) < 3600:
                    match_data = api_match_data
                    match_id = match_data['metadata']['matchid']
                    mmr_change = self.api.fetch_mmr_change(match_id)
                    self.log_signal.emit("[API] Successfully fetched current match data.")
                    break
            except Exception:
                pass

        if match_data:
            try:
                if self.current_video_path:
                    match_data['local_video_path'] = self.current_video_path
                    match_data['local_match_start_time'] = self.recording_start_time
                    match_data['local_match_end_time'] = getattr(self, 'recording_end_time', time.time())
                    match_data['local_round_events'] = self.local_round_events
                        
                filepath = self.store.save_match_metadata(match_data, mmr_change)
                self.log_signal.emit(f"[Storage] Metadata saved: {filepath}")
                self.match_saved_signal.emit()
            except Exception as e:
                self.log_signal.emit(f"[Error] Failed to process match metadata: {e}")
        else:
            self.log_signal.emit("[API] Match data not available yet. Will retry in background.")
            
        self.current_video_path = None

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

    def _create_dummy_metadata(self, video_path, vid_time):
        match_data = {
            "metadata": {
                "matchid": f"custom_{int(vid_time)}",
                "map": "Custom / Unknown",
                "game_start": int(vid_time),
                "game_length": 0,
                "mode": "Custom"
            },
            "players": {"all_players": []},
            "kills": [],
            "rounds": [],
            "local_video_path": video_path
        }
        self.store.save_match_metadata(match_data, 0)
        self.match_saved_signal.emit()

    def _background_worker(self):
        while self._is_running:
            time.sleep(60)
            
            if self.watcher.is_in_match:
                continue
                
            pending_videos = self._get_pending_videos()
            if not pending_videos:
                continue
                
            self.log_signal.emit(f"[Background] Found {len(pending_videos)} pending video(s). Checking API...")
            
            try:
                api_match_data = self.api.fetch_latest_match(retries=1, delay=2)
                if not api_match_data:
                    continue
                    
                game_start = api_match_data.get('metadata', {}).get('game_start', 0)
                
                for video_path, vid_time in pending_videos:
                    diff = abs(game_start - vid_time)
                    
                    if diff < 3600:
                        self.log_signal.emit(f"[Background] Match found for {os.path.basename(video_path)}.")
                        match_id = api_match_data['metadata']['matchid']
                        mmr_change = self.api.fetch_mmr_change(match_id, retries=1, delay=2)
                        
                        api_match_data['local_video_path'] = video_path
                        filepath = self.store.save_match_metadata(api_match_data, mmr_change)
                        self.log_signal.emit(f"[Background] Saved metadata: {filepath}")
                        self.match_saved_signal.emit()
                        
                    elif game_start > vid_time + 3600:
                        self.log_signal.emit(f"[Background] Video {os.path.basename(video_path)} is likely a custom match. Skipping.")
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
        self.recorder.stop_recording()
        self.terminate()