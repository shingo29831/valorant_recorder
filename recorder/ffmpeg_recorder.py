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
        import warnings
        from contextlib import ExitStack
        warnings.filterwarnings("ignore", category=sc.SoundcardRuntimeWarning)
        
        samplerate = 48000
        frames_per_buffer = 1024
        mic_gain = float(getattr(self.config, 'RECORD_AUDIO_MIC_GAIN', '1.0'))
        
        try:
            speaker = sc.default_speaker()
            spk_mic = sc.get_microphone(speaker.id, include_loopback=True)
            
            mic_device = None
            if self.config.RECORD_AUDIO_MIC and self.config.RECORD_AUDIO_MIC != "None":
                for m in sc.all_microphones(include_loopback=False):
                    if self.config.RECORD_AUDIO_MIC in m.name:
                        mic_device = m
                        break
                if mic_device is None:
                    mic_device = sc.default_microphone()

            with ExitStack() as stack:
                spk_rec = stack.enter_context(spk_mic.recorder(samplerate=samplerate, channels=2))
                
                mic_rec = None
                if mic_device is not None:
                    try:
                        mic_rec = stack.enter_context(mic_device.recorder(samplerate=samplerate, channels=2))
                        mic_channels = 2
                    except Exception:
                        mic_rec = stack.enter_context(mic_device.recorder(samplerate=samplerate, channels=1))
                        mic_channels = 1
                
                if hasattr(self, 'audio_ready_event'):
                    self.audio_ready_event.set()

                mic_queue = queue.Queue()
                spk_queue = queue.Queue()
                worker_stop = threading.Event()
                
                def mic_worker():
                    try:
                        while not worker_stop.is_set() and not self.stop_event.is_set():
                            data = mic_rec.record(numframes=frames_per_buffer)
                            if mic_channels == 1:
                                data = np.repeat(data, 2, axis=1)
                            try:
                                mic_queue.put_nowait(data)
                            except queue.Full:
                                pass
                    except Exception:
                        pass

                def spk_worker():
                    try:
                        while not worker_stop.is_set() and not self.stop_event.is_set():
                            data = spk_rec.record(numframes=frames_per_buffer)
                            try:
                                spk_queue.put_nowait(data)
                            except queue.Full:
                                pass
                    except Exception:
                        pass

                mic_thread = None
                if mic_rec is not None:
                    mic_thread = threading.Thread(target=mic_worker, daemon=True)
                    mic_thread.start()
                    
                spk_thread = threading.Thread(target=spk_worker, daemon=True)
                spk_thread.start()

                # 無音時にFFmpegが音声データ不足でハングアップしないよう、タイムアウトを短く設定して
                # 積極的に無音データを生成・供給する（FFmpegのstdinバッファが同期を取る）
                timeout_sec = 0.01

                while not self.stop_event.is_set():
                    # スピーカー音が鳴っていないとrecordがブロックするため、タイムアウト付きで取得し、
                    # 取得できない場合は無音データを生成してFFmpegのエンコード停止を防ぐ
                    try:
                        spk_data = spk_queue.get(timeout=timeout_sec)
                    except queue.Empty:
                        spk_data = np.zeros((frames_per_buffer, 2), dtype=np.float32)
                    
                    if mic_rec is not None:
                        try:
                            mic_data = mic_queue.get_nowait()
                            mic_data = mic_data * mic_gain
                        except queue.Empty:
                            mic_data = np.zeros((frames_per_buffer, 2), dtype=np.float32)
                    else:
                        mic_data = np.zeros_like(spk_data)
                    
                    # システム音とマイク音を結合して4chストリームにする (FL, FR, RL, RR)
                    combined = np.concatenate((spk_data, mic_data), axis=1)
                    combined = np.clip(combined, -1.0, 1.0)
                    
                    try:
                        self.audio_queue.put_nowait(combined.astype(np.float32).tobytes())
                    except queue.Full:
                        pass

                worker_stop.set()
                if mic_thread is not None:
                    mic_thread.join(timeout=1.0)
                if spk_thread is not None:
                    spk_thread.join(timeout=1.0)

        except Exception as e:
            if self.log_file and not self.log_file.closed:
                self.log_file.write(f"Audio capture error: {e}\n")
                self.log_file.flush()
            if hasattr(self, 'audio_ready_event'):
                self.audio_ready_event.set()
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
            "-thread_queue_size", "1024",
            "-f", self.config.RECORD_VIDEO_FORMAT,
            "-framerate", self.config.RECORD_FPS,
        ]
        
        if self.config.RECORD_VIDEO_FORMAT != "ddagrab":
            cmd.extend(["-video_size", self.config.RECORD_RESOLUTION])
            
        gate_level = float(getattr(self.config, 'RECORD_AUDIO_MIC_NOISE_GATE', '0')) / 100.0
        denoise = getattr(self.config, 'RECORD_AUDIO_MIC_DENOISE', 'False') == 'True'

        # a_resをasplit=2で2つのストリームに複製してから、それぞれをpanフィルタに渡す
        filter_complex = "[1:a]asplit=2[a_res1][a_res2];[a_res1]pan=stereo|c0=c0|c1=c1[a0];[a_res2]pan=stereo|c0=c2|c1=c3[a1]"
        
        mic_filters = []
        if denoise:
            mic_filters.append("afftdn=nf=-25")
            
        if gate_level > 0:
            # UI上のレベル(平方根スケール)を実際の振幅閾値に戻す
            amp_threshold = gate_level ** 2
            mic_filters.append(f"agate=threshold={amp_threshold:.4f}:ratio=10:attack=10:release=100")
            
        if mic_filters:
            # フィルタ適用後にチャンネルレイアウトやサンプリングレートが失われないようaformatで明示的に指定する
            filter_complex += f";[a1]{','.join(mic_filters)},aformat=channel_layouts=stereo:sample_rates=48000[a1_out]"
            mic_map = "[a1_out]"
        else:
            mic_map = "[a1]"

        cmd.extend([
            "-i", input_source,
            "-thread_queue_size", "1024",
            "-f", "f32le",
            "-ar", "48000",
            "-ac", "4",
            "-i", "pipe:0",
            "-filter_complex", filter_complex,
            "-map", "0:v",
            "-map", "[a0]",
            "-map", mic_map,
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
        
        self.stop_event.clear()
        self.audio_ready_event = threading.Event()
        
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break

        self.audio_record_thread = threading.Thread(target=self._audio_capture_loop, daemon=True)
        self.audio_record_thread.start()
        
        self.audio_ready_event.wait(timeout=5.0)
        
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
        
        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=self.log_file,
            creationflags=creationflags
        )
        
        self.audio_write_thread = threading.Thread(target=self._audio_write_loop, daemon=True)
        self.audio_write_thread.start()
        
        return self.current_filepath

    def stop_recording(self):
        self.stop_event.set()
        
        # 1. 音声キャプチャと書き込みスレッドを先に安全に終了させる
        if self.audio_record_thread:
            self.audio_record_thread.join(timeout=5)
            self.audio_record_thread = None
            
        if self.audio_write_thread:
            self.audio_write_thread.join(timeout=5)
            self.audio_write_thread = None
            
        if self.process:
            # 2. FFmpegに終了シグナルを送信して安全に終了させる
            # stdinは生データ(pipe:0)を受け取っているため、EOFだけでは映像入力(ddagrab等)が終了せずハングアップする。
            # そのため、明示的にシグナルを送って正常な終了処理(moovアトム書き込み)を開始させる。
            try:
                if os.name == 'nt':
                    os.kill(self.process.pid, signal.CTRL_BREAK_EVENT)
                else:
                    self.process.send_signal(signal.SIGINT)
            except Exception:
                pass

            # 3. stdinを閉じる
            if self.process.stdin:
                try:
                    self.process.stdin.close()
                except Exception:
                    pass
                
            # 4. FFmpegが正常終了(moovアトム書き込み等)するのを待機
            try:
                self.process.wait(timeout=15.0)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait()
            except Exception:
                pass
            
            returncode = self.process.poll()
            # WindowsでCTRL_BREAK_EVENTを送った場合、終了コードは255や3221225786になるため正常とみなす
            if returncode is not None and returncode != 0 and returncode not in (255, 3221225786):
                print(f"[FFmpegRecorder] FFmpeg exited abnormally with code {returncode}")
                # 異常終了時のみログを出力
                if self.current_filepath:
                    log_path = os.path.join(os.path.dirname(self.current_filepath), "ffmpeg_error.log")
                    if os.path.exists(log_path):
                        try:
                            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                                lines = f.readlines()
                                if lines:
                                    print("\n=== FFmpeg Error Log (Last 20 lines) ===")
                                    for line in lines[-20:]:
                                        print(line.strip())
                                    print("========================================\n")
                        except Exception as e:
                            print(f"[FFmpegRecorder] Could not read log file: {e}")
                            
            self.process = None
            
        if self.log_file:
            self.log_file.close()
            self.log_file = None