import time
from PyQt6.QtCore import QThread, pyqtSignal
from core.config import Config
from watcher.log_watcher import LogWatcher
from api.henrik_api import HenrikAPI
from storage.metadata_store import MetadataStore
from recorder.ffmpeg_recorder import FFmpegRecorder

class WatcherThread(QThread):
    log_signal = pyqtSignal(str)
    
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
            on_match_end=self.handle_match_end
        )
        self.current_video_path = None
        self.match_start_time = 0
        self._is_running = True

    def handle_match_start(self, is_range: bool):
        if is_range:
            self.log_signal.emit("[Recorder] 射撃訓練場(Range)を検知しました。録画とAPI取得をスキップします。")
            return

        self.match_start_time = time.time()
        self.log_signal.emit("[Recorder] Match started. Starting FFmpeg recording...")
        try:
            self.current_video_path = self.recorder.start_recording()
            self.log_signal.emit(f"[Recorder] Recording to: {self.current_video_path}")
        except Exception as e:
            self.log_signal.emit(f"[Error] Failed to start recording: {e}")

    def handle_match_end(self, is_range: bool):
        if is_range:
            return

        self.log_signal.emit("[Recorder] Match ended. Stopping recording...")
        try:
            self.recorder.stop_recording()
        except Exception as e:
            self.log_signal.emit(f"[Error] Failed to stop recording: {e}")

        self.log_signal.emit("[API] Waiting for match data to be available on Riot servers...")
        
        match_data = None
        mmr_change = 0

        for attempt in range(6):
            time.sleep(30)
            try:
                api_match_data = self.api.fetch_latest_match()
                game_start = api_match_data.get('metadata', {}).get('game_start', 0)
                
                if abs(game_start - self.match_start_time) < 3600:
                    match_data = api_match_data
                    match_id = match_data['metadata']['matchid']
                    mmr_change = self.api.fetch_mmr_change(match_id)
                    self.log_signal.emit("[API] Successfully fetched current match data.")
                    break
                else:
                    self.log_signal.emit(f"[API] Fetched match is old (diff: {abs(game_start - self.match_start_time)}s). Retrying...")
            except Exception as e:
                self.log_signal.emit(f"[API] Attempt {attempt+1} failed: {e}")

        if not match_data:
            self.log_signal.emit("[Storage] Creating local fallback metadata for custom/untracked match.")
            match_data = {
                "metadata": {
                    "matchid": f"custom_{int(time.time())}",
                    "map": "Custom / Unknown",
                    "game_start": int(self.match_start_time),
                    "game_length": int(time.time() - self.match_start_time),
                    "mode": "Custom"
                },
                "players": {"all_players": []},
                "kills": [],
                "rounds": []
            }

        try:
            if self.current_video_path:
                match_data['local_video_path'] = self.current_video_path

            filepath = self.store.save_match_metadata(match_data, mmr_change)
            self.log_signal.emit(f"[Storage] Metadata saved: {filepath} (RR Change: {mmr_change})")
        except Exception as e:
            self.log_signal.emit(f"[Error] Failed to process match metadata: {e}")
        finally:
            self.current_video_path = None

    def run(self):
        self.log_signal.emit("Valorant Recorder App initialized. Watching logs...")
        try:
            self.watcher.start_watching()
        except Exception as e:
            self.log_signal.emit(f"Fatal error: {e}")
            self.recorder.stop_recording()

    def stop(self):
        self._is_running = False
        self.recorder.stop_recording()
        self.terminate()