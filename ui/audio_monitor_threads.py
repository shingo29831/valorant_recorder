from PyQt6.QtCore import pyqtSignal, QThread
import numpy as np

class SystemAudioMonitorThread(QThread):
    level_ready = pyqtSignal(float)

    def __init__(self, gain):
        super().__init__()
        self.gain = gain
        self.running = True

    def set_gain(self, gain):
        self.gain = gain

    def process_audio(self, data):
        data = data * self.gain
        peak = np.max(np.abs(data))
        level = min(1.0, peak ** 0.5)
        self.level_ready.emit(float(level))
        return data

    def run(self):
        import warnings
        warnings.filterwarnings("ignore", message=".*data discontinuity.*")
        warnings.filterwarnings("ignore", module=".*soundcard.*")
        try:
            import soundcard as sc
            warnings.simplefilter("ignore", category=sc.SoundcardRuntimeWarning)
            
            speaker = sc.default_speaker()
            spk_mic = sc.get_microphone(speaker.id, include_loopback=True)
            
            with spk_mic.recorder(samplerate=48000, channels=2) as recorder:
                while self.running:
                    data = recorder.record(numframes=2400)
                    # ステレオの場合は平均をとってモノラルにダウンミックスしてレベル計算
                    data_mono = data.mean(axis=1, keepdims=True)
                    self.process_audio(data_mono)
        except Exception as e:
            import traceback
            import os
            print("\n=== SystemAudioMonitorThread Error ===")
            traceback.print_exc()
            print("======================================\n")
            log_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sys_audio_error.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"SystemAudioMonitorThread error:\n{traceback.format_exc()}\n")

    def stop(self):
        self.running = False
        if not self.wait(2000):
            import os, datetime
            log_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "thread_error.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now()}] SystemAudioMonitorThread wait timed out.\n")

class MicMonitorThread(QThread):
    level_ready = pyqtSignal(float)

    def __init__(self, mic_name, gain, denoise_mode="None", gate_threshold=0.0, preprocess_mode="SpeexDSP"):
        super().__init__()
        self.mic_name = mic_name
        self.gain = gain
        self.denoise_mode = denoise_mode
        self.preprocess_mode = preprocess_mode
        self.gate_threshold = gate_threshold
        self.monitor_audio = False
        self.running = True
        self.noise_floor = 0.01
        self.gate_open = False

    def set_gain(self, gain):
        self.gain = gain

    def set_denoise(self, denoise_mode):
        self.denoise_mode = denoise_mode

    def set_preprocess(self, preprocess_mode):
        self.preprocess_mode = preprocess_mode

    def set_gate_threshold(self, threshold):
        self.gate_threshold = threshold

    def set_monitor_audio(self, monitor):
        self.monitor_audio = monitor

    def process_audio(self, data):
        data = data * self.gain

        # RNNoiseの場合はPython側で簡易的なノイズリダクションをシミュレート（モニター用）
        if self.denoise_mode == "AI (RNNoise)":
            rms = np.sqrt(np.mean(data**2) + 1e-8)
            if rms < self.noise_floor:
                self.noise_floor = 0.8 * self.noise_floor + 0.2 * rms
            else:
                self.noise_floor = 0.995 * self.noise_floor + 0.005 * rms
            
            snr = rms / self.noise_floor
            if snr < 3.0:
                reduction = max(0.05, (snr - 1.0) / 2.0)
                data = data * reduction

        # メーター表示用のレベル計算（ゲート適用前）
        peak = np.max(np.abs(data))
        level = min(1.0, peak ** 0.5)
        self.level_ready.emit(float(level))

        # 再生用のノイズゲート適用
        if self.gate_threshold > 0:
            amp_threshold = (self.gate_threshold / 2.0) ** 2
            rms = np.sqrt(np.mean(data**2) + 1e-8)
            if rms > amp_threshold:
                self.gate_open = True
            elif rms < amp_threshold * 0.5: # ヒステリシス
                self.gate_open = False
            
            if not self.gate_open:
                data = data * 0.01

        return data

    def run(self):
        import warnings
        warnings.filterwarnings("ignore", message=".*data discontinuity.*")
        warnings.filterwarnings("ignore", module=".*soundcard.*")
        try:
            import soundcard as sc
            from recorder.audio_processor_wrapper import AudioProcessorWrapper
            
            # sc.SoundcardRuntimeWarning も明示的に無視する
            warnings.simplefilter("ignore", category=sc.SoundcardRuntimeWarning)
            
            mic_device = None
            if self.mic_name and self.mic_name != "None":
                # 完全一致を優先
                for m in sc.all_microphones(include_loopback=False):
                    if self.mic_name == m.name:
                        mic_device = m
                        break
                # 見つからなければ部分一致
                if mic_device is None:
                    for m in sc.all_microphones(include_loopback=False):
                        if self.mic_name in m.name:
                            mic_device = m
                            break
                # フォールバックを廃止し、見つからない場合は None のままにする

            if mic_device is not None:
                try:
                    recorder = mic_device.recorder(samplerate=48000, channels=2)
                    channels = 2
                except Exception:
                    try:
                        recorder = mic_device.recorder(samplerate=48000, channels=1)
                        channels = 1
                    except Exception as e:
                        import traceback
                        print(f"Failed to initialize recorder:")
                        traceback.print_exc()
                        recorder = None
                
                try:
                    speaker = sc.default_speaker()
                    player = speaker.player(samplerate=48000, channels=2)
                except Exception:
                    player = None
                    
                # AudioProcessorWrapper の初期化 (ダウンミックス後のモノラル処理用)
                processor = None
                if self.denoise_mode == "AI (DeepFilterNet)":
                    try:
                        processor = AudioProcessorWrapper(sample_rate=48000, channels=1)
                        processor.set_preprocess_type(self.preprocess_mode)
                    except Exception as e:
                        print(f"Failed to initialize AudioProcessorWrapper: {e}")
                    
                if recorder is not None:
                    with recorder:
                        if player is not None:
                            with player:
                                while self.running:
                                    data = recorder.record(numframes=2400)
                                    if channels == 2:
                                        # ステレオの場合は平均をとってモノラルにダウンミックス
                                        data = data.mean(axis=1, keepdims=True)
                                        
                                    if processor is not None:
                                        data = processor.process(data)
                                        
                                    processed = self.process_audio(data)
                                    if self.monitor_audio:
                                        # モノラルをステレオに複製して再生
                                        stereo = np.repeat(processed, 2, axis=1)
                                        player.play(stereo)
                        else:
                            while self.running:
                                data = recorder.record(numframes=2400)
                                if channels == 2:
                                    data = data.mean(axis=1, keepdims=True)
                                    
                                if processor is not None:
                                    data = processor.process(data)
                                    
                                self.process_audio(data)
                else:
                    while self.running:
                        self.level_ready.emit(0.0)
                        self.msleep(50)
            else:
                while self.running:
                    self.level_ready.emit(0.0)
                    self.msleep(50)
        except Exception as e:
            import traceback
            import os
            print("\n=== MicMonitorThread Error ===")
            traceback.print_exc()
            print("==============================\n")
            log_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mic_error.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"MicMonitorThread error:\n{traceback.format_exc()}\n")

    def stop(self):
        self.running = False
        if not self.wait(2000):
            import os, datetime
            log_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "thread_error.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now()}] MicMonitorThread wait timed out.\n")
