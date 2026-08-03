import time
from core.config import Config
from watcher.log_watcher import LogWatcher
from api.henrik_api import HenrikAPI
from storage.metadata_store import MetadataStore
from recorder.ffmpeg_recorder import FFmpegRecorder

class ValorantRecorderApp:
    def __init__(self):
        self.config = Config()
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

    def handle_match_start(self, is_range: bool):
        if is_range:
            print("[Recorder] 射撃訓練場(Range)を検知しました。録画とAPI取得をスキップします。")
            return

        print("[Recorder] Match started. Starting FFmpeg recording...")
        try:
            self.current_video_path = self.recorder.start_recording()
            print(f"[Recorder] Recording to: {self.current_video_path}")
        except Exception as e:
            print(f"[Error] Failed to start recording: {e}")

    def handle_match_end(self, is_range: bool):
        if is_range:
            return # 射撃訓練場の場合は何もしない

        print("[Recorder] Match ended. Stopping recording...")
        try:
            self.recorder.stop_recording()
        except Exception as e:
            print(f"[Error] Failed to stop recording: {e}")

        print("[API] Waiting for match data to be available on Riot servers...")
        time.sleep(30)

        try:
            match_data = self.api.fetch_latest_match()
            match_id = match_data['metadata']['matchid']
            mmr_change = self.api.fetch_mmr_change(match_id)
            
            if self.current_video_path:
                match_data['local_video_path'] = self.current_video_path

            filepath = self.store.save_match_metadata(match_data, mmr_change)
            print(f"[Storage] Metadata saved: {filepath} (RR Change: {mmr_change})")
        except Exception as e:
            print(f"[Error] Failed to process match metadata: {e}")
        finally:
            self.current_video_path = None

    def run(self):
        print("Valorant Recorder App initialized. Watching logs...")
        try:
            self.watcher.start_watching()
        except KeyboardInterrupt:
            print("Application terminated by user.")
            self.recorder.stop_recording()
        except Exception as e:
            print(f"Fatal error: {e}")
            self.recorder.stop_recording()

if __name__ == "__main__":
    app = ValorantRecorderApp()
    app.run()