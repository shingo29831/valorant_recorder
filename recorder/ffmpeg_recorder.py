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
        self.temp_filepath = None
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
                    self.process.stdin.flush()
                else:
                    break
            except queue.Empty:
                continue
            except (BrokenPipeError, OSError, ValueError):
                break
                
        # 同一スレッド内で安全に stdin を閉じる (別スレッドからの close によるデッドロック防止)
        if self.process and self.process.stdin and not self.process.stdin.closed:
            try:
                self.process.stdin.close()
            except Exception:
                pass

    def start_recording(self) -> str:
        if self.process is not None:
            return self.current_filepath

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"match_record_{timestamp}.mp4"
        temp_filename = f"match_record_{timestamp}.mkv"
        self.current_filepath = os.path.join(self.config.SAVE_DIR, filename)
        self.temp_filepath = os.path.join(self.config.SAVE_DIR, temp_filename)
        os.makedirs(self.config.SAVE_DIR, exist_ok=True)

        # リアルタイムエンコード向けに低遅延・低負荷なプリセットとチューニングを適用
        preset = "p2" if "nvenc" in self.actual_encoder else "veryfast"
        tune = "ull" if "nvenc" in self.actual_encoder else "zerolatency"

        cmd = [
            self.ffmpeg_path,
            "-y",
        ]
        
        filter_complex = ""
        video_map = "0:v"
        
        if self.config.RECORD_VIDEO_FORMAT == "ddagrab":
            cmd.extend([
                "-thread_queue_size", "4096",
                "-f", "lavfi",
                "-i", f"ddagrab=framerate={self.config.RECORD_FPS}"
            ])
            # ddagrabのタイムスタンプを0から開始させる
            filter_complex += "[0:v]hwdownload,format=bgra,setpts=PTS-STARTPTS[v_out];"
            video_map = "[v_out]"
        else:
            cmd.extend([
                "-thread_queue_size", "4096",
                "-rtbufsize", "1024M",  # キャプチャバッファを増やしてドロップを防ぐ
                "-use_wallclock_as_timestamps", "1",
                "-f", self.config.RECORD_VIDEO_FORMAT,
                "-framerate", self.config.RECORD_FPS,
                "-video_size", self.config.RECORD_RESOLUTION,
                "-i", self.config.RECORD_INPUT_SOURCE
            ])
            # gdigrab等の場合もタイムスタンプを0から開始させる
            filter_complex += "[0:v]setpts=PTS-STARTPTS[v_out];"
            video_map = "[v_out]"
            
        gate_level = float(getattr(self.config, 'RECORD_AUDIO_MIC_NOISE_GATE', '0')) / 100.0
        denoise_mode = str(getattr(self.config, 'RECORD_AUDIO_MIC_DENOISE', 'None'))

        # 音声のタイムスタンプも0から開始させ、映像と同期させる
        # a_resをasplit=2で2つのストリームに複製してから、それぞれをpanフィルタに渡す
        # FFmpegの自動ダウンミックスによる音量減衰を防ぐため、ゲイン(1.0*)を明示的に指定
        filter_complex += "[1:a]asetpts=PTS-STARTPTS,asplit=2[a_res1][a_res2];[a_res1]pan=stereo|c0=1.0*c0|c1=1.0*c1[a0];[a_res2]pan=stereo|c0=1.0*c2|c1=1.0*c3[a1]"
        
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
        # 録音時に2倍に増幅しているため、ミックス時にクリップしないよう alimiter を適用する
        filter_complex += f";[a0]asplit=2[a0_mix][a0_out];{mic_map}asplit=2[a1_mix][a1_out];[a0_mix][a1_mix]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.99[a_mixed]"

        cmd.extend([
            "-thread_queue_size", "4096",
            # 音声(pipe:0)に対する wallclock タイムスタンプは、映像との激しい非同期(dup大量発生)を引き起こすため削除
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
            "-r", str(self.config.RECORD_FPS),
            "-g", str(self.config.RECORD_FPS),
            "-keyint_min", str(self.config.RECORD_FPS),
            "-fps_mode", "cfr",
            "-c:a", "aac",
            "-b:a", "192k",
            "-max_muxing_queue_size", "9999",
            "-shortest",
            self.temp_filepath
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
        
        # CREATE_NO_WINDOW のみを指定し、CREATE_NEW_PROCESS_GROUP や DETACHED_PROCESS は
        # 逆にフォーカスを奪う原因になるため除外する。
        # さらに startupinfo で明示的にウィンドウを非表示(SW_HIDE)に設定する。
        creationflags = 0
        startupinfo = None
        if os.name == 'nt':
            creationflags = subprocess.CREATE_NO_WINDOW
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
        
        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=self.log_file,
            creationflags=creationflags,
            startupinfo=startupinfo
        )
        
        # FFmpegプロセス起動完了までの間にキャプチャされた古い音声データを破棄する。
        # これを行わないと、映像キャプチャ開始前の音声が先頭に挿入され、音声が先行する音ズレが発生する。
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break
        
        self.audio_write_thread = threading.Thread(target=self._audio_write_loop, daemon=True)
        self.audio_write_thread.start()
        
        return self.current_filepath

    def stop_recording(self):
        self.stop_event.set()
        
        if self.process:
            # 1. FFmpegに終了シグナルを送信して安全に終了させる
            # stdinは生データ(pipe:0)を受け取っているため、EOFだけでは映像入力(ddagrab等)が終了せずハングアップする。
            # そのため、明示的にシグナルを送って正常な終了処理(moovアトム書き込み)を開始させる。
            # スレッドのjoin前にシグナルを送ることで、write()でブロックしているスレッドを解放する。
            try:
                if os.name == 'nt':
                    os.kill(self.process.pid, signal.CTRL_BREAK_EVENT)
                else:
                    self.process.send_signal(signal.SIGINT)
            except Exception:
                pass

        # 2. 音声キャプチャと書き込みスレッドを安全に終了させる
        if self.audio_record_thread:
            self.audio_record_thread.join(timeout=5)
            self.audio_record_thread = None
            
        if self.audio_write_thread:
            self.audio_write_thread.join(timeout=5)
            self.audio_write_thread = None
            
        if self.process:
            # stdinのcloseはaudio_write_thread内で安全に行われるため、ここでは行わない
                
            # 3. FFmpegが正常終了(moovアトム書き込み等)するのを待機
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

        # MKVからMP4への再多重化 (Remux)
        # クラッシュ時でもMKVは正常な状態が保たれるため、ここでMP4に変換することで破損を防ぐ
        if self.temp_filepath and os.path.exists(self.temp_filepath):
            try:
                print(f"[FFmpegRecorder] Remuxing MKV to MP4: {self.temp_filepath} -> {self.current_filepath}")
                remux_cmd = [
                    self.ffmpeg_path,
                    "-y",
                    "-i", self.temp_filepath,
                    "-c", "copy",
                    "-movflags", "+faststart",
                    self.current_filepath
                ]
                creationflags = 0
                startupinfo = None
                if os.name == 'nt':
                    creationflags = subprocess.CREATE_NO_WINDOW
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startupinfo.wShowWindow = subprocess.SW_HIDE

                remux_process = subprocess.run(
                    remux_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creationflags,
                    startupinfo=startupinfo
                )
                if remux_process.returncode == 0:
                    os.remove(self.temp_filepath)
                    print("[FFmpegRecorder] Remux completed successfully.")
                else:
                    print(f"[FFmpegRecorder] Remux failed with code {remux_process.returncode}")
            except Exception as e:
                print(f"[FFmpegRecorder] Remux exception: {e}")

    def set_low_priority(self):
        """
        FFmpegプロセスの優先度を設定する。
        ※以前は BELOW_NORMAL に下げていたが、OSのスケジューラによって
        FFmpegにリソースが割り当てられず動画がカクつく原因となっていたため、
        NORMAL_PRIORITY_CLASS (0x00000020) を維持するように修正。
        （メソッド名は他ファイルからの呼び出し互換性のために維持）
        """
        if self.process and self.process.poll() is None:
            try:
                import ctypes
                import sys
                if sys.platform == "win32":
                    # NORMAL_PRIORITY_CLASS = 0x00000020
                    # 録画のフレームドロップを防ぐため NORMAL を明示的に設定する
                    ctypes.windll.kernel32.SetPriorityClass(int(self.process._handle), 0x00000020)
            except Exception as e:
                import warnings
                warnings.warn(f"Failed to set process priority: {e}")