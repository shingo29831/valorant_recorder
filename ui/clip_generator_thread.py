import os
import subprocess
from PyQt6.QtCore import QThread, pyqtSignal

class ClipGeneratorThread(QThread):
    finished = pyqtSignal(bool, str)
    
    def __init__(self, ffmpeg_path, input_path, output_path, start_ms, end_ms, encoder, sys_volume, mic_volume, audio_track_count):
        super().__init__()
        self.ffmpeg_path = ffmpeg_path
        self.input_path = input_path
        self.output_path = output_path
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.encoder = encoder
        self.sys_volume = sys_volume
        self.mic_volume = mic_volume
        self.audio_track_count = audio_track_count
        
    def run(self):
        try:
            start_sec = self.start_ms / 1000.0
            end_sec = self.end_ms / 1000.0
            duration = end_sec - start_sec
            
            preset = "p4" if "nvenc" in self.encoder else "veryfast"
            
            cmd = [
                self.ffmpeg_path,
                "-y",
                "-ss", f"{start_sec:.3f}",
                "-i", self.input_path,
                "-t", f"{duration:.3f}",
                "-map", "0:v:0",
                "-c:v", self.encoder,
                "-preset", preset,
                "-b:v", "10M",
                "-c:a", "aac",
                "-b:a", "192k"
            ]
            
            if self.audio_track_count >= 3:
                filter_complex = f"[0:a:1]volume={self.sys_volume}[a0];[0:a:2]volume={self.mic_volume}[a1];[a0][a1]amix=inputs=2:duration=longest[aout]"
                cmd.extend(["-filter_complex", filter_complex, "-map", "[aout]"])
            elif self.audio_track_count == 2:
                filter_complex = f"[0:a:0]volume={self.sys_volume}[a0];[0:a:1]volume={self.mic_volume}[a1];[a0][a1]amix=inputs=2:duration=longest[aout]"
                cmd.extend(["-filter_complex", filter_complex, "-map", "[aout]"])
            else:
                filter_complex = f"[0:a:0]volume={self.sys_volume}[aout]"
                cmd.extend(["-filter_complex", filter_complex, "-map", "[aout]"])
                
            cmd.append(self.output_path)
            
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=creationflags)
            
            if res.returncode == 0:
                self.finished.emit(True, self.output_path)
            else:
                self.finished.emit(False, res.stderr)
        except Exception as e:
            self.finished.emit(False, str(e))
