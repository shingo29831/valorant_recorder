import subprocess
import os
import threading
import numpy as np
import soundcard as sc
from datetime import datetime
from core.config import Config
from recorder.ffmpeg_downloader import ensure_ffmpeg_downloaded

class FFmpegRecorder:
    def __init__(self, config: Config):
        self.config = config
        self.process = None
        self.current_filepath = None
        self.log_file = None
        self.audio_thread = None
        self.stop_event = threading.Event()
        
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.ffmpeg_path = ensure_ffmpeg_downloaded(project_root)

    def _audio_capture_loop(self):
        samplerate = 48000
        frames_per_buffer = 1024
        
        try:
            speaker = sc.default_speaker()
            
            mic = None
            if self.config.RECORD_AUDIO_MIC:
                mics = sc.all_microphones()
                for m in mics:
                    if self.config.RECORD_AUDIO_MIC in m.name:
                        mic = m
                        break
                if not mic and mics:
                    mic = sc.default_microphone()

            if mic:
                with speaker.recorder(samplerate=samplerate, channels=2) as spk_rec, \
                     mic.recorder(samplerate=samplerate, channels=2) as mic_rec:
                    while not self.stop_event.is_set():
                        spk_data = spk_rec.record(numframes=frames_per_buffer)
                        mic_data = mic_rec.record(numframes=frames_per_buffer)
                        
                        mixed = spk_data + mic_data
                        mixed = np.clip(mixed, -1.0, 1.0)
                        
                        try:
                            if self.process and self.process.stdin and not self.process.stdin.closed:
                                self.process.stdin.write(mixed.astype(np.float32).tobytes())
                            else:
                                break
                        except (BrokenPipeError, OSError, ValueError):
                            break
            else:
                with speaker.recorder(samplerate=samplerate, channels=2) as spk_rec:
                    while not self.stop_event.is_set():
                        spk_data = spk_rec.record(numframes=frames_per_buffer)
                        
                        try:
                            if self.process and self.process.stdin and not self.process.stdin.closed:
                                self.process.stdin.write(spk_data.astype(np.float32).tobytes())
                            else:
                                break
                        except (BrokenPipeError, OSError, ValueError):
                            break
        except Exception as e:
            if self.log_file and not self.log_file.closed:
                self.log_file.write(f"Audio capture error: {e}\n")
                self.log_file.flush()

    def start_recording(self) -> str:
        if self.process is not None:
            return self.current_filepath

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"match_record_{timestamp}.mkv"
        self.current_filepath = os.path.join(self.config.SAVE_DIR, filename)
        os.makedirs(self.config.SAVE_DIR, exist_ok=True)

        cmd = [
            self.ffmpeg_path,
            "-y",
            "-f", self.config.RECORD_VIDEO_FORMAT,
            "-framerate", self.config.RECORD_FPS,
            "-video_size", self.config.RECORD_RESOLUTION,
            "-i", self.config.RECORD_INPUT_SOURCE,
            "-f", "f32le",
            "-ar", "48000",
            "-ac", "2",
            "-i", "pipe:0",
            "-map", "0:v",
            "-map", "1:a",
            "-c:v", self.config.RECORD_ENCODER,
            "-preset", "p4",
            "-tune", "hq",
            "-b:v", "10M",
            "-c:a", "aac",
            "-b:a", "192k",
            self.current_filepath
        ]

        error_log_path = os.path.join(self.config.SAVE_DIR, "ffmpeg_error.log")
        self.log_file = open(error_log_path, "w")
        
        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=self.log_file
        )
        
        self.stop_event.clear()
        self.audio_thread = threading.Thread(target=self._audio_capture_loop, daemon=True)
        self.audio_thread.start()
        
        return self.current_filepath

    def stop_recording(self):
        self.stop_event.set()
        
        if self.process:
            try:
                if self.process.stdin:
                    self.process.stdin.close()
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait()
            except Exception:
                pass
            self.process = None
            
        if self.audio_thread:
            self.audio_thread.join(timeout=5)
            self.audio_thread = None
            
        if self.log_file:
            self.log_file.close()
            self.log_file = None