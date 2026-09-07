import threading
import queue
import warnings
import numpy as np
from core.config import Config

class AudioCaptureThread(threading.Thread):
    def __init__(self, config: Config, audio_queue: queue.Queue, log_file, stop_event: threading.Event, audio_ready_event: threading.Event):
        super().__init__(daemon=True)
        self.config = config
        self.audio_queue = audio_queue
        self.log_file = log_file
        self.stop_event = stop_event
        self.audio_ready_event = audio_ready_event

    def run(self):
        # COM競合を防ぐため、別スレッド内でインポートを遅延させる
        import soundcard as sc
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
                
                if self.audio_ready_event:
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

                # マイクの連続的な波形を保持するバッファ（キュー破棄によるポツ音防止）
                mic_buffer = np.zeros((0, 2), dtype=np.float32)

                while not self.stop_event.is_set():
                    try:
                        # タイムアウトをバッファ長(2400サンプル=50ms)に合わせる。
                        # 無音時に0.5秒など長く待つと、FFmpegに送られる音声データが実時間より遅れ、
                        # 同期を取るために映像フレームが大量にドロップされFPSが極端に低下する。
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
                            
                            # ドリフトによりバッファが極端に溜まりすぎた場合（例: 3秒以上）のみ、古いデータを捨てる
                            if mic_buffer.shape[0] > samplerate * 3.0:
                                mic_buffer = mic_buffer[-int(samplerate * 0.5):]
                                
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
                        except queue.Full:
                            pass
                            
                    else:
                        # 無音時（タイムアウト）：スピーカーからのデータが0.5秒以上来ない場合、
                        # 録画の進行を止めないために1バッファ分の無音を挿入する。
                        # 実時間との厳密な同期（ドリフト補正）は、かえってジッターを引き起こすため廃止。
                        pad_spk = np.zeros((frames_per_buffer, 2), dtype=np.float32)
                        pad_mic = np.zeros((frames_per_buffer, 2), dtype=np.float32)
                        combined_pad = np.concatenate((pad_spk, pad_mic), axis=1)
                        
                        try:
                            self.audio_queue.put_nowait(combined_pad.astype(np.float32).tobytes())
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
            if self.audio_ready_event:
                self.audio_ready_event.set()
        finally:
            try:
                self.audio_queue.put_nowait(None)
            except queue.Full:
                pass
