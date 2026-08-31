import subprocess
import os
import threading
import queue
import warnings
import signal
import numpy as np
from datetime import datetime
from core.config import Config
from recorder.ffmpeg_downloader import ensure_ffmpeg_downloaded, ensure_rnnoise_model_downloaded

warnings.filterwarnings("ignore", message=".*data discontinuity.*")
warnings.filterwarnings("ignore", module=".*soundcard.*")

def test_encoder(ffmpeg_path: str, encoder: str) -> tuple[bool, str]:
    """指定されたエンコーダが現在の環境で利用可能かテストし、結果とエラーメッセージを返す"""
    cmd = [
        ffmpeg_path,
        "-v", "error",
        # NVENCの最小解像度制限(144x144等)を回避するため、256x256でテストする
        "-f", "lavfi", "-i", "color=black:s=256x256:r=1",
        "-pix_fmt", "yuv420p",
        "-c:v", encoder,
        "-frames:v", "1",
        "-f", "null", "-"
    ]
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        # text=Trueによるエンコーディングエラー(UnicodeDecodeError等)を防ぐためバイナリで取得
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=creationflags)
        if res.returncode != 0:
            err_msg = res.stderr.decode('utf-8', errors='replace') if res.stderr else ""
            return False, err_msg.strip()
        return True, ""
    except Exception as e:
        return False, str(e)

def get_available_encoders(ffmpeg_path: str) -> tuple[list, list]:
    """
    利用可能なハードウェアエンコーダと、発生した警告メッセージキーのリストを返す。
    """
    hw_encoders = [
        "hevc_nvenc", "h264_nvenc",  # NVIDIA
        "hevc_amf", "h264_amf",      # AMD
        "hevc_qsv", "h264_qsv"       # Intel
    ]
    
    available = []
    warning_keys = []
    
    # 失敗原因特定のため、永続的なデータディレクトリにログを出力する
    app_data_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'ValoReco')
    os.makedirs(app_data_dir, exist_ok=True)
    log_path = os.path.join(app_data_dir, "encoder_test.log")
    
    try:
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(f"--- Encoder Test Log ({datetime.now()}) ---\n")
            
            for enc in hw_encoders:
                success, err_msg = test_encoder(ffmpeg_path, enc)
                if success:
                    available.append(enc)
                    f.write(f"[{enc}] Success\n")
                else:
                    f.write(f"[{enc}] Failed:\n{err_msg}\n\n")
                    # NVIDIAドライバが古い場合のエラーを検知
                    if "nvenc" in enc and "minimum required Nvidia driver" in err_msg:
                        if "nvenc_driver_old" not in warning_keys:
                            warning_keys.append("nvenc_driver_old")
    except Exception as e:
        print(f"Failed to write encoder_test.log: {e}")
        # ログ書き込みに失敗してもテスト自体は続行する
        for enc in hw_encoders:
            if enc not in available:
                success, err_msg = test_encoder(ffmpeg_path, enc)
                if success:
                    available.append(enc)
            
    if available:
        return available, warning_keys
        
    return ["libx264"], warning_keys

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

    def _audio_capture_loop(self):
        # COM競合を防ぐため、別スレッド内でインポートを遅延させる
        import soundcard as sc
        import warnings
        from contextlib import ExitStack
        warnings.simplefilter("ignore", category=sc.SoundcardRuntimeWarning)
        warnings.filterwarnings("ignore", message=".*data discontinuity.*")
        
        samplerate = 48000
        # AIノイズキャンセル(DeepFilterNet)やSpeexDSPは10ms(480サンプル)単位での処理を要求するため、
        # 480の倍数であり、かつモニター時と同じ安定したバッファサイズである2400(50ms)に設定する。
        # バッファが小さすぎると処理落ち(ドロップアウト)が発生しプツプツ音の原因になる。
        frames_per_buffer = 2400
        mic_gain = float(getattr(self.config, 'RECORD_AUDIO_MIC_GAIN', '1.0'))
        system_gain = float(getattr(self.config, 'RECORD_AUDIO_SYSTEM_GAIN', '1.0'))
        
        try:
            speaker = sc.default_speaker()
            spk_mic = sc.get_microphone(speaker.id, include_loopback=True)
            
            mic_device = None
            if self.config.RECORD_AUDIO_MIC and self.config.RECORD_AUDIO_MIC != "None":
                # 完全一致を優先
                for m in sc.all_microphones(include_loopback=False):
                    if self.config.RECORD_AUDIO_MIC == m.name:
                        mic_device = m
                        break
                # 見つからなければ部分一致
                if mic_device is None:
                    for m in sc.all_microphones(include_loopback=False):
                        if self.config.RECORD_AUDIO_MIC in m.name:
                            mic_device = m
                            break
                # フォールバックを廃止し、見つからない場合は None のままにする

            with ExitStack() as stack:
                spk_rec = stack.enter_context(spk_mic.recorder(samplerate=samplerate, channels=2))
                
                mic_rec = None
                if mic_device is not None:
                    try:
                        mic_rec = stack.enter_context(mic_device.recorder(samplerate=samplerate, channels=2))
                        mic_channels = 2
                    except Exception:
                        try:
                            mic_rec = stack.enter_context(mic_device.recorder(samplerate=samplerate, channels=1))
                            mic_channels = 1
                        except Exception as e:
                            if self.log_file and not self.log_file.closed:
                                self.log_file.write(f"Failed to initialize mic recorder: {e}\n")
                                self.log_file.flush()
                            mic_rec = None
                
                if hasattr(self, 'audio_ready_event'):
                    self.audio_ready_event.set()

                mic_queue = queue.Queue()
                spk_queue = queue.Queue()
                worker_stop = threading.Event()
                
                def mic_worker():
                    warnings.simplefilter("ignore", category=sc.SoundcardRuntimeWarning)
                    
                    processor = None
                    denoise_mode = str(getattr(self.config, 'RECORD_AUDIO_MIC_DENOISE', 'None'))
                    preprocess_mode = str(getattr(self.config, 'RECORD_AUDIO_MIC_PREPROCESS', 'SpeexDSP'))
                    gate_threshold = float(getattr(self.config, 'RECORD_AUDIO_MIC_NOISE_GATE', '0')) / 100.0
                    gate_open = False
                    current_gate_gain = 1.0
                    current_limiter_gain = 1.0
                    gate_hold_frames = 0
                    MAX_HOLD_FRAMES = 10  # 50ms * 10 = 500ms のホールドタイム（声の途切れ防止）
                    
                    if denoise_mode == 'AI (DeepFilterNet)' or preprocess_mode != 'None':
                        try:
                            from recorder.audio_processor_wrapper import AudioProcessorWrapper
                            # DeepFilterNetはモノラル専用のため、マイクのチャンネル数に関わらず常にchannels=1で初期化する
                            processor = AudioProcessorWrapper(sample_rate=samplerate, channels=1)
                            processor.set_preprocess_type(preprocess_mode)
                            if denoise_mode == 'AI (DeepFilterNet)':
                                processor.set_denoise_type("DeepFilterNet")
                            else:
                                processor.set_denoise_type("None")
                        except Exception as e:
                            if self.log_file and not self.log_file.closed:
                                self.log_file.write(f"Failed to initialize AudioProcessorWrapper: {e}\n")
                                self.log_file.flush()

                    try:
                        while not worker_stop.is_set() and not self.stop_event.is_set():
                            data = mic_rec.record(numframes=frames_per_buffer)
                            
                            if processor is not None:
                                # ステレオの場合はモノラルにダウンミックスしてから処理
                                if mic_channels == 2:
                                    data = data.mean(axis=1, keepdims=True)
                                data = processor.process(data)
                                # 処理後にステレオに戻す
                                data = np.repeat(data, 2, axis=1)
                            else:
                                if mic_channels == 1:
                                    data = np.repeat(data, 2, axis=1)
                                    
                            # スムージング付きソフトリミッター
                            # ハードクリップ(np.clip)による音の歪みと、フレーム単位の急激なゲイン変化によるポツ音を両方防ぐ
                            peak = np.max(np.abs(data))
                            target_gain = 0.99 / peak if peak > 0.99 else 1.0
                            
                            if current_limiter_gain != target_gain:
                                gains = np.linspace(current_limiter_gain, target_gain, len(data), dtype=np.float32).reshape(-1, 1)
                                data = data * gains
                                current_limiter_gain = target_gain
                            elif target_gain < 1.0:
                                data = data * target_gain
                                
                            # モニター時と全く同じスムージング付きノイズゲートをPython側で適用する
                            if gate_threshold > 0:
                                amp_threshold = (gate_threshold / 2.0) ** 2
                                rms = np.sqrt(np.mean(data**2) + 1e-8)
                                
                                if rms > amp_threshold:
                                    gate_open = True
                                    gate_hold_frames = MAX_HOLD_FRAMES
                                else:
                                    if gate_hold_frames > 0:
                                        gate_hold_frames -= 1
                                    else:
                                        gate_open = False
                                
                                target_gain = 1.0 if gate_open else 0.01
                                
                                if current_gate_gain != target_gain:
                                    gains = np.linspace(current_gate_gain, target_gain, len(data), dtype=np.float32).reshape(-1, 1)
                                    data = data * gains
                                    current_gate_gain = target_gain
                                else:
                                    data = data * target_gain
                            
                            # データサイズが異なる場合のパディング/トリミング（形状不一致によるクラッシュ防止）
                            if data.shape[0] < frames_per_buffer:
                                pad = np.zeros((frames_per_buffer - data.shape[0], 2), dtype=np.float32)
                                data = np.concatenate((data, pad), axis=0)
                            elif data.shape[0] > frames_per_buffer:
                                data = data[:frames_per_buffer, :]
                                
                            try:
                                mic_queue.put_nowait(data)
                            except queue.Full:
                                pass
                    except Exception as e:
                        if self.log_file and not self.log_file.closed:
                            self.log_file.write(f"Mic worker error: {e}\n")
                            self.log_file.flush()

                def spk_worker():
                    warnings.simplefilter("ignore", category=sc.SoundcardRuntimeWarning)
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

                import time
                
                start_time = time.perf_counter()
                total_frames_generated = 0
                
                # マイクの連続的な波形を保持するバッファ（キュー破棄によるポツ音防止）
                mic_buffer = np.zeros((0, 2), dtype=np.float32)

                while not self.stop_event.is_set():
                    # スピーカーのキューが溜まりすぎている場合は古いデータを捨てる（遅延防止）
                    while spk_queue.qsize() > 2:
                        try:
                            spk_queue.get_nowait()
                        except queue.Empty:
                            break

                    try:
                        # 短いタイムアウトでデータを待つ
                        spk_data = spk_queue.get(timeout=0.05)
                        spk_data = spk_data * system_gain
                    except queue.Empty:
                        spk_data = None

                    if spk_data is not None:
                        frames_to_process = spk_data.shape[0]
                        
                        if mic_rec is not None:
                            # キューから利用可能なすべてのデータをバッファに追加し、波形の連続性を保つ
                            while True:
                                try:
                                    chunk = mic_queue.get_nowait()
                                    mic_buffer = np.concatenate((mic_buffer, chunk), axis=0)
                                except queue.Empty:
                                    break
                            
                            # ドリフトによりバッファが溜まりすぎた場合（例: 0.5秒以上）のみ、古いデータを捨てる
                            if mic_buffer.shape[0] > samplerate * 0.5:
                                mic_buffer = mic_buffer[-int(samplerate * 0.1):]
                                
                            if mic_buffer.shape[0] >= frames_to_process:
                                mic_data = mic_buffer[:frames_to_process]
                                mic_buffer = mic_buffer[frames_to_process:]
                            else:
                                # データが足りない場合はゼロパディング
                                pad = np.zeros((frames_to_process - mic_buffer.shape[0], 2), dtype=np.float32)
                                mic_data = np.concatenate((mic_buffer, pad), axis=0)
                                mic_buffer = np.zeros((0, 2), dtype=np.float32)
                                
                            mic_data = mic_data * mic_gain
                        else:
                            mic_data = np.zeros((frames_to_process, 2), dtype=np.float32)
                        
                        combined = np.concatenate((spk_data, mic_data), axis=1)
                        combined = np.clip(combined, -1.0, 1.0)
                        
                        try:
                            self.audio_queue.put_nowait(combined.astype(np.float32).tobytes())
                            total_frames_generated += frames_to_process
                        except queue.Full:
                            pass
                            
                        # 音が鳴っている間は、WASAPIのハードウェアクロックとシステムタイマーのズレを吸収するため、
                        # start_time を現在時刻と生成済みフレーム数から逆算して補正する（ドリフト防止）
                        start_time = time.perf_counter() - (total_frames_generated / samplerate)
                        
                    else:
                        # 無音時（タイムアウト）：システムタイマーベースで正確な量の無音データを補完する
                        current_time = time.perf_counter()
                        elapsed = current_time - start_time
                        expected_frames = int(elapsed * samplerate)
                        
                        frames_shortage = expected_frames - total_frames_generated
                        
                        if frames_shortage > 0:
                            # 一度に大量に生成しすぎないよう制限（最大1バッファ分ずつ）
                            frames_to_add = min(frames_shortage, frames_per_buffer)
                            
                            pad_spk = np.zeros((frames_to_add, 2), dtype=np.float32)
                            pad_mic = np.zeros((frames_to_add, 2), dtype=np.float32)
                            combined_pad = np.concatenate((pad_spk, pad_mic), axis=1)
                            
                            try:
                                self.audio_queue.put_nowait(combined_pad.astype(np.float32).tobytes())
                                total_frames_generated += frames_to_add
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
        denoise_mode = str(getattr(self.config, 'RECORD_AUDIO_MIC_DENOISE', 'None'))

        # a_resをasplit=2で2つのストリームに複製してから、それぞれをpanフィルタに渡す
        # FFmpegの自動ダウンミックスによる音量減衰を防ぐため、ゲイン(1.0*)を明示的に指定
        filter_complex = "[1:a]asplit=2[a_res1][a_res2];[a_res1]pan=stereo|c0=1.0*c0|c1=1.0*c1[a0];[a_res2]pan=stereo|c0=1.0*c2|c1=1.0*c3[a1]"
        
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
            "-i", input_source,
            "-thread_queue_size", "1024",
            "-f", "f32le",
            "-ar", "48000",
            "-ac", "4",
            "-i", "pipe:0",
            "-filter_complex", filter_complex,
            "-map", "0:v",
            "-map", "[a_mixed]",
            "-map", "[a0_out]",
            "-map", "[a1_out]",
            "-c:v", self.actual_encoder,
            "-preset", preset,
            "-tune", tune,
            "-b:v", "10M",
            "-pix_fmt", "yuv420p",
            "-r", self.config.RECORD_FPS,
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

        self.audio_record_thread = threading.Thread(target=self._audio_capture_loop, daemon=True)
        self.audio_record_thread.start()
        
        self.audio_ready_event.wait(timeout=5.0)
        
        creationflags = (subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW) if os.name == 'nt' else 0
        
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