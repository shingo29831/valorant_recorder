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

                # スピーカーとマイクの連続的な波形を保持するバッファ
                spk_buffer = np.zeros((0, 2), dtype=np.float32)
                mic_buffer = np.zeros((0, 2), dtype=np.float32)
                
                # 2倍増幅時のクリッピング防止用リミッターゲイン
                current_spk_limiter_gain = 1.0
                current_mic_limiter_gain = 1.0
                
                import time
                start_time = time.perf_counter()
                total_sent_frames = 0

                while not self.stop_event.is_set():
                    # 1. スピーカーのデータを取得してバッファに追加
                    try:
                        # タイムアウトを短くしてループを高頻度で回し、実時間との同期精度を上げる
                        spk_data = spk_queue.get(timeout=0.02)
                        spk_data = spk_data * (system_gain * 2.0)
                        spk_buffer = np.concatenate((spk_buffer, spk_data), axis=0)
                    except queue.Empty:
                        pass

                    # 2. マイクのデータを取得してバッファに追加
                    if mic_rec is not None:
                        while True:
                            try:
                                chunk = mic_queue.get_nowait()
                                mic_buffer = np.concatenate((mic_buffer, chunk), axis=0)
                            except queue.Empty:
                                break

                    # バッファが溜まりすぎた場合（ハードウェアクロックが速い場合の遅延蓄積防止）
                    if spk_buffer.shape[0] > samplerate:
                        spk_buffer = spk_buffer[-samplerate // 2:]
                    if mic_buffer.shape[0] > samplerate:
                        mic_buffer = mic_buffer[-samplerate // 2:]

                    # 3. 実時間に基づいて、現在までに送るべき総フレーム数を計算
                    current_time = time.perf_counter()
                    elapsed = current_time - start_time
                    expected_total_frames = int(elapsed * samplerate)
                    
                    # 今回送るべきフレーム数
                    frames_to_send = expected_total_frames - total_sent_frames
                    
                    if frames_to_send > 0:
                        # 異常な遅延（スリープ復帰など）への対策
                        if frames_to_send > samplerate // 2:
                            # 追いつけないほどの遅延が発生した場合は、基準時間をリセットしてスキップ
                            start_time = current_time - (total_sent_frames / samplerate)
                            spk_buffer = np.zeros((0, 2), dtype=np.float32)
                            mic_buffer = np.zeros((0, 2), dtype=np.float32)
                            continue

                        # スピーカーデータの準備
                        if spk_buffer.shape[0] >= frames_to_send:
                            spk_out = spk_buffer[:frames_to_send]
                            spk_buffer = spk_buffer[frames_to_send:]
                        else:
                            # データが足りない場合は無音でパディング（クロックドリフト補正）
                            pad = np.zeros((frames_to_send - spk_buffer.shape[0], 2), dtype=np.float32)
                            spk_out = np.concatenate((spk_buffer, pad), axis=0)
                            spk_buffer = np.zeros((0, 2), dtype=np.float32)

                        # マイクデータの準備
                        if mic_rec is not None:
                            if mic_buffer.shape[0] >= frames_to_send:
                                mic_out = mic_buffer[:frames_to_send]
                                mic_buffer = mic_buffer[frames_to_send:]
                            else:
                                pad = np.zeros((frames_to_send - mic_buffer.shape[0], 2), dtype=np.float32)
                                mic_out = np.concatenate((mic_buffer, pad), axis=0)
                                mic_buffer = np.zeros((0, 2), dtype=np.float32)
                                
                            mic_out = mic_out * (mic_gain * 2.0)
                        else:
                            mic_out = np.zeros((frames_to_send, 2), dtype=np.float32)

                        # スピーカー音のソフトリミッター
                        peak_spk = np.max(np.abs(spk_out))
                        target_gain_spk = 0.99 / peak_spk if peak_spk > 0.99 else 1.0
                        if current_spk_limiter_gain != target_gain_spk:
                            gains = np.linspace(current_spk_limiter_gain, target_gain_spk, len(spk_out), dtype=np.float32).reshape(-1, 1)
                            spk_out = spk_out * gains
                            current_spk_limiter_gain = target_gain_spk
                        elif target_gain_spk < 1.0:
                            spk_out = spk_out * target_gain_spk
                            
                        # マイク音のソフトリミッター
                        peak_mic = np.max(np.abs(mic_out))
                        target_gain_mic = 0.99 / peak_mic if peak_mic > 0.99 else 1.0
                        if current_mic_limiter_gain != target_gain_mic:
                            gains = np.linspace(current_mic_limiter_gain, target_gain_mic, len(mic_out), dtype=np.float32).reshape(-1, 1)
                            mic_out = mic_out * gains
                            current_mic_limiter_gain = target_gain_mic
                        elif target_gain_mic < 1.0:
                            mic_out = mic_out * target_gain_mic
                        
                        combined = np.concatenate((spk_out, mic_out), axis=1)
                        combined = np.clip(combined, -1.0, 1.0)
                        
                        try:
                            self.audio_queue.put_nowait(combined.astype(np.float32).tobytes())
                            total_sent_frames += frames_to_send
                        except queue.Full:
                            # キューが満杯(約60秒以上の異常な遅延)の場合は古いデータを捨てて最新のデータを入れる
                            try:
                                self.audio_queue.get_nowait()
                                self.audio_queue.put_nowait(combined.astype(np.float32).tobytes())
                                total_sent_frames += frames_to_send
                            except queue.Empty:
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
