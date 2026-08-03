import subprocess
import os
import threading
import queue
import warnings
import signal
import numpy as np
from datetime import datetime
from core.config import Config
from recorder.ffmpeg_downloader import ensure_ffmpeg_downloaded

warnings.filterwarnings("ignore", message="data discontinuity in recording")

class FFmpegRecorder:
    def __init__(self, config: Config):
        self.config = config
        self.process = None
        self.current_filepath = None
        self.log_file = None
        self.audio_record_thread = None
        self.audio_write_thread = None
        self.stop_event = threading.Event()
        self.audio_queue = queue.Queue(maxsize=200)
        
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.ffmpeg_path = ensure_ffmpeg_downloaded(project_root)
        self.actual_encoder = self._determine_encoder()

    def _determine_encoder(self) -> str:
        encoder = self.config.RECORD_ENCODER
        if "nvenc" in encoder:
            try:
                test_cmd = [
                    self.ffmpeg_path,
                    "-f", "lavfi", "-i", "color=black:s=128x128:r=1",
                    "-c:v", encoder,
                    "-frames:v", "1",
                    "-f", "null", "-"
                ]
                res = subprocess.run(test_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
                if res.returncode != 0:
                    return "libx264"
            except Exception:
                return "libx264"
        return encoder

    def _audio_capture_loop(self):
        # COM競合を防ぐため、別スレッド内でインポートを遅延させる
        import soundcard as sc
        
        samplerate = 48000
        frames_per_buffer = 1024
        
        try:
            speaker = sc.default_speaker()
            spk_mic = sc.get_microphone(speaker.id, include_loopback=True)
            
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
                with spk_mic.recorder(samplerate=samplerate, channels=2) as spk_rec, \
                     mic.recorder(samplerate=samplerate, channels=2) as mic_rec:
                    while not self.stop_event.is_set():
                        spk_data = spk_rec.record(numframes=frames_per_buffer)
                        mic_data = mic_rec.record(numframes=frames_per_buffer)
                        
                        mixed = spk_data + mic_data
                        mixed = np.clip(mixed, -1.0, 1.0)
                        
                        try:
                            self.audio_queue.put_nowait(mixed.astype(np.float32).tobytes())
                        except queue.Full:
                            pass
            else:
                with spk_mic.recorder(samplerate=samplerate, channels=2) as spk_rec:
                    while not self.stop_event.is_set():
                        spk_data = spk_rec.record(numframes=frames_per_buffer)
                        
                        try:
                            self.audio_queue.put_nowait(spk_data.astype(np.float32).tobytes())
                        except queue.Full:
                            pass
        except Exception as e:
            if self.log_file and not self.log_file.closed:
                self.log_file.write(f"Audio capture error: {e}\n")
                self.log_file.flush()
        finally:
            try:
                self.audio_queue.put_nowait(None)
            except queue.Full:
                pass

    def _audio_write_loop(self):
        while not self.stop_event.is_set():
            try:
                data = self.audio_queue.get(timeout=0.5)
                if data is None:
                    break
                if self.process and self.process.stdin and not self.process.stdin.closed:
                    self.process.stdin.write(data)
                else:
                    break
            except queue.Empty:
                continue
            except (BrokenPipeError, OSError, ValueError):
                break

    def start_recording(self) -> str:
        if self.process is not None:
            return self.current_filepath

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"match_record_{timestamp}.mp4"
        self.current_filepath = os.path.join(self.config.SAVE_DIR, filename)
        os.makedirs(self.config.SAVE_DIR, exist_ok=True)

        preset = "p4" if "nvenc" in self.actual_encoder else "veryfast"
        tune = "hq" if "nvenc" in self.actual_encoder else "zerolatency"

        input_source = "" if self.config.RECORD_VIDEO_FORMAT == "ddagrab" else self.config.RECORD_INPUT_SOURCE

        cmd = [
            self.ffmpeg_path,
            "-y",
            "-f", self.config.RECORD_VIDEO_FORMAT,
            "-framerate", self.config.RECORD_FPS,
        ]
        
        if self.config.RECORD_VIDEO_FORMAT != "ddagrab":
            cmd.extend(["-video_size", self.config.RECORD_RESOLUTION])
            
        cmd.extend([
            "-i", input_source,
            "-f", "f32le",
            "-ar", "48000",
            "-ac", "2",
            "-i", "pipe:0",
            "-map", "0:v",
            "-map", "1:a",
            "-c:v", self.actual_encoder,
            "-preset", preset,
            "-tune", tune,
            "-b:v", "10M",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            self.current_filepath
        ])

        error_log_path = os.path.join(self.config.SAVE_DIR, "ffmpeg_error.log")
        self.log_file = open(error_log_path, "w")
        
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
        
        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=self.log_file,
            creationflags=creationflags
        )
        
        self.stop_event.clear()
        
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break

        self.audio_record_thread = threading.Thread(target=self._audio_capture_loop, daemon=True)
        self.audio_write_thread = threading.Thread(target=self._audio_write_loop, daemon=True)
        self.audio_record_thread.start()
        self.audio_write_thread.start()
        
        return self.current_filepath

    def stop_recording(self):
        self.stop_event.set()
        
        if self.process:
            try:
                if self.process.stdin:
                    self.process.stdin.close()
                
                if os.name == 'nt':
                    os.kill(self.process.pid, signal.CTRL_BREAK_EVENT)
                else:
                    self.process.terminate()
                    
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait()
            except Exception:
                pass
            self.process = None
            
        if self.audio_record_thread:
            self.audio_record_thread.join(timeout=5)
            self.audio_record_thread = None
            
        if self.audio_write_thread:
            self.audio_write_thread.join(timeout=5)
            self.audio_write_thread = None
            
        if self.log_file:
            self.log_file.close()
            self.log_file = None