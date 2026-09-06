import subprocess
import os
import threading
import queue
import signal
from datetime import datetime
from core.config import Config
from recorder.ffmpeg_downloader import ensure_ffmpeg_downloaded, ensure_rnnoise_model_downloaded
from recorder.encoder_utils import test_encoder, get_available_encoders
from recorder.audio_capture_thread import AudioCaptureThread

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
        
        # Nuitkaの実行時一時ディレクトリではなく、永続的なディレクトリにダウンロードする
        app_data_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'ValoReco')
        os.makedirs(app_data_dir, exist_ok=True)
        self.ffmpeg_path = ensure_ffmpeg_downloaded(app_data_dir)
        self.rnnoise_model_path = ensure_rnnoise_model_downloaded(app_data_dir)
        self.actual_encoder = self._determine_encoder()

    def _determine_encoder(self) -> str:
        encoder = self.config.RECORD_ENCODER
        success, _ = test_encoder(self.ffmpeg_path, encoder)
        if success:
            return encoder
        
        # 設定されたエンコーダが使えない場合（グラボ変更など）、利用可能な最適なものを返す
        available, _ = get_available_encoders(self.ffmpeg_path)
        return available[0]

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

        cmd = [
            self.ffmpeg_path,
            "-y",
        ]
        
        filter_complex = ""
        video_map = "0:v"
        
        if self.config.RECORD_VIDEO_FORMAT == "ddagrab":
            cmd.extend([
                "-thread_queue_size", "1024",
                "-f", "lavfi",
                "-i", f"ddagrab=framerate={self.config.RECORD_FPS}"
            ])
            filter_complex += "[0:v]hwdownload,format=bgra[v_out];"
            video_map = "[v_out]"
        else:
            cmd.extend([
                "-thread_queue_size", "1024",
                "-use_wallclock_as_timestamps", "1",
                "-f", self.config.RECORD_VIDEO_FORMAT,
                "-framerate", self.config.RECORD_FPS,
                "-video_size", self.config.RECORD_RESOLUTION,
                "-i", self.config.RECORD_INPUT_SOURCE
            ])
            
        gate_level = float(getattr(self.config, 'RECORD_AUDIO_MIC_NOISE_GATE', '0')) / 100.0
        denoise_mode = str(getattr(self.config, 'RECORD_AUDIO_MIC_DENOISE', 'None'))

        # a_resをasplit=2で2つのストリームに複製してから、それぞれをpanフィルタに渡す
        # FFmpegの自動ダウンミックスによる音量減衰を防ぐため、ゲイン(1.0*)を明示的に指定
        filter_complex += "[1:a]asplit=2[a_res1][a_res2];[a_res1]pan=stereo|c0=1.0*c0|c1=1.0*c1[a0];[a_res2]pan=stereo|c0=1.0*c2|c1=1.0*c3[a1]"
        
        mic_filters = []
        
        # RNNoiseの場合はFFmpegのarnndnフィルタを使用する
        if denoise_mode in ('True', 'Standard (FFmpeg)', 'AI (RNNoise)'):
            try:
                rel_model_path = os.path.relpath(self.rnnoise_model_path, start=os.getcwd())
                model_path_str = rel_model_path.replace('\\', '/')
                mic_filters.append(f"arnndn=m='{model_path_str}'")
            except ValueError:
                model_path_str = self.rnnoise_model_path.replace('\\', '/').replace(':', '\\:').replace(' ', '\\ ')
                mic_filters.append(f"arnndn=m={model_path_str}")
            
        # ノイズゲートはPython側でモニターと全く同じ処理を適用するため、FFmpegのagateフィルタは使用しない
            
        if mic_filters:
            # フィルタ適用後にチャンネルレイアウトやサンプリングレートが失われないようaformatで明示的に指定する
            filter_complex += f";[a1]{','.join(mic_filters)},aformat=channel_layouts=stereo:sample_rates=48000[a1_out]"
            mic_map = "[a1_out]"
        else:
            mic_map = "[a1]"

        # システム音とマイク音をそれぞれ asplit で複製し、
        # 1. ミックス用 (通常再生用)
        # 2. システム音単独 (編集用)
        # 3. マイク音単独 (編集用)
        # の3つのオーディオトラックを生成する
        filter_complex += f";[a0]asplit=2[a0_mix][a0_out];{mic_map}asplit=2[a1_mix][a1_out];[a0_mix][a1_mix]amix=inputs=2:duration=longest:normalize=0[a_mixed]"

        cmd.extend([
            "-thread_queue_size", "1024",
            "-use_wallclock_as_timestamps", "1",
            "-f", "f32le",
            "-ar", "48000",
            "-ac", "4",
            "-i", "pipe:0",
            "-filter_complex", filter_complex,
            "-map", video_map,
            "-map", "[a_mixed]",
            "-map", "[a0_out]",
            "-map", "[a1_out]",
            "-c:v", self.actual_encoder,
            "-preset", preset,
            "-tune", tune,
            "-b:v", "10M",
            "-pix_fmt", "yuv420p",
            "-r", self.config.RECORD_FPS,
            "-fps_mode", "cfr",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "frag_keyframe+empty_moov",
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

        self.audio_record_thread = AudioCaptureThread(
            self.config,
            self.audio_queue,
            self.log_file,
            self.stop_event,
            self.audio_ready_event
        )
        self.audio_record_thread.start()
        
        self.audio_ready_event.wait(timeout=5.0)
        
        # CREATE_NO_WINDOW に加え DETACHED_PROCESS (0x00000008) を指定し、
        # プロセス起動時にOSがコンソール用に一瞬フォーカスを奪う現象を完全に防ぐ
        creationflags = (subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW | 0x00000008) if os.name == 'nt' else 0
        
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
            # WindowsでCTRL_BREAK_EVENTを送った場合、終了コードは255や3221225786、3221225477(0xC0000005)になるため正常とみなす
            if returncode is not None and returncode != 0 and returncode not in (255, 3221225786, 3221225477):
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

    def set_low_priority(self):
        """FFmpegプロセスの優先度を下げてゲームのFPS低下を防ぐ"""
        if self.process and self.process.poll() is None:
            try:
                import ctypes
                import sys
                if sys.platform == "win32":
                    # BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
                    ctypes.windll.kernel32.SetPriorityClass(int(self.process._handle), 0x00004000)
            except Exception as e:
                warnings.warn(f"Failed to set low priority: {e}")