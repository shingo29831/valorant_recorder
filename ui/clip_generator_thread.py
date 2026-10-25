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
        
    def _build_encoder_args(self, encoder: str) -> list[str]:
        enc_lower = (encoder or "").lower()
        if "nvenc" in enc_lower:
            return ["-c:v", encoder, "-preset", "p4", "-rc", "vbr", "-cq", "20", "-b:v", "10M", "-maxrate", "15M", "-bufsize", "30M"]
        if "amf" in enc_lower:
            # AMFは-presetオプションを持たないため専用パラメータを指定
            return ["-c:v", encoder, "-usage", "transcoding", "-quality", "balanced", "-rc", "vbr_peak", "-b:v", "10M", "-maxrate", "15M", "-bufsize", "30M"]
        if "qsv" in enc_lower:
            return ["-c:v", encoder, "-preset", "veryfast", "-b:v", "10M", "-maxrate", "15M", "-bufsize", "30M"]
        return ["-c:v", encoder or "libx264", "-preset", "veryfast", "-crf", "22", "-maxrate", "12M", "-bufsize", "24M"]

    def _build_command(self, encoder: str, start_sec: float, duration: float) -> list[str]:
        cmd = [
            self.ffmpeg_path,
            "-y",
            "-ss", f"{start_sec:.3f}",
            "-i", self.input_path,
            "-t", f"{duration:.3f}",
            "-map", "0:v:0",
        ]
        cmd.extend(self._build_encoder_args(encoder))
        cmd.extend(["-c:a", "aac", "-b:a", "192k"])

        # 2トラック構成: Track 0(システム音) と Track 1(マイク音) を指定比率で合成
        if self.audio_track_count >= 2:
            filter_complex = f"[0:a:0]volume={self.sys_volume}[a0];[0:a:1]volume={self.mic_volume}[a1];[a0][a1]amix=inputs=2:duration=longest[aout]"
            cmd.extend(["-filter_complex", filter_complex, "-map", "[aout]"])
        else:
            filter_complex = f"[0:a:0]volume={self.sys_volume}[aout]"
            cmd.extend(["-filter_complex", filter_complex, "-map", "[aout]"])

        cmd.append(self.output_path)
        return cmd

    def run(self):
        try:
            start_sec = self.start_ms / 1000.0
            end_sec = self.end_ms / 1000.0
            duration = end_sec - start_sec

            creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            cmd = self._build_command(self.encoder, start_sec, duration)
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace", creationflags=creationflags)

            if res.returncode != 0 and self.encoder != "libx264":
                cmd = self._build_command("libx264", start_sec, duration)
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace", creationflags=creationflags)

            if res.returncode == 0:
                self.finished.emit(True, self.output_path)
            else:
                self.finished.emit(False, res.stderr)
        except Exception as e:
            self.finished.emit(False, str(e))
