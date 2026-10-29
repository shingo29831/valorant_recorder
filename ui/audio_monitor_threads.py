from PyQt6.QtCore import pyqtSignal, QThread
import numpy as np

class SystemAudioMonitorThread(QThread):
    level_ready = pyqtSignal(float)

    def __init__(self, gain, device_name="default"):
        super().__init__()
        self.gain = gain
        self.device_name = device_name
        self.running = True
        self.current_limiter_gain = 1.0

    def set_gain(self, gain):
        self.gain = gain

    def process_audio(self, data):
        if data is None or len(data) == 0:
            self.level_ready.emit(0.0)
            return data
        data = data * self.gain
        peak = np.max(np.abs(data))
        level = min(1.0, peak ** 0.5)
        self.level_ready.emit(float(level))
        
        # スムージング付きソフトリミッター
        # フレーム単位の急激なゲイン変化によるポツ音と、ハードクリップによる音の歪みを防ぐ
        peak = np.max(np.abs(data))
        target_gain = 0.99 / peak if peak > 0.99 else 1.0
        
        if self.current_limiter_gain != target_gain:
            gains = np.linspace(self.current_limiter_gain, target_gain, len(data), dtype=np.float32).reshape(-1, 1)
            data = data * gains
            self.current_limiter_gain = target_gain
        elif target_gain < 1.0:
            data = data * target_gain

        return data

    def run(self):
        import sys
        import warnings
        warnings.filterwarnings("ignore", message=".*data discontinuity.*")
        warnings.filterwarnings("ignore", module=".*soundcard.*")
        com_initialized = False
        if sys.platform == "win32":
            try:
                import ctypes
                hr = ctypes.windll.ole32.CoInitializeEx(None, 0)
                com_initialized = (hr in (0, 1))
            except Exception:
                pass
        try:
            import threading
            import soundcard as sc
            warnings.simplefilter("ignore", category=sc.SoundcardRuntimeWarning)
            sc_lock = getattr(sc, '_lock', threading.RLock())

            with sc_lock:
                speaker = None
                if self.device_name and self.device_name not in ("default", "Default", "None"):
                    for s in sc.all_speakers():
                        if s.name == self.device_name or self.device_name in s.name:
                            speaker = s
                            break
                if speaker is None:
                    speaker = sc.default_speaker()
                spk_mic = sc.get_microphone(speaker.id, include_loopback=True)
                recorder = None
                for ch in (2, 1):
                    try:
                        cand = spk_mic.recorder(samplerate=48000, channels=ch)
                        cand.__enter__()
                        recorder = cand
                        break
                    except Exception:
                        recorder = None

            if recorder is not None:
                try:
                    while self.running:
                        data = recorder.record(numframes=1200)
                        if not self.running:
                            break
                        if data is None or len(data) == 0:
                            self.level_ready.emit(0.0)
                            continue
                        if data.ndim == 2:
                            data_mono = data.mean(axis=1, keepdims=True)
                        else:
                            data_mono = data.reshape(-1, 1)
                        self.process_audio(data_mono)
                finally:
                    try:
                        recorder.__exit__(None, None, None)
                    except Exception:
                        pass
        except Exception as e:
            import traceback
            import os
            print("\n=== SystemAudioMonitorThread Error ===")
            traceback.print_exc()
            print("======================================\n")
            app_data_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'ValoReco')
            os.makedirs(app_data_dir, exist_ok=True)
            log_path = os.path.join(app_data_dir, "sys_audio_error.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"SystemAudioMonitorThread error:\n{traceback.format_exc()}\n")
        finally:
            if com_initialized:
                try:
                    import ctypes
                    ctypes.windll.ole32.CoUninitialize()
                except Exception:
                    pass

    def stop(self):
        self.running = False
        if not self.wait(300):
            import os, datetime
            app_data_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'ValoReco')
            os.makedirs(app_data_dir, exist_ok=True)
            log_path = os.path.join(app_data_dir, "thread_error.log")
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
        self.current_gate_gain = 1.0
        self.current_rnnoise_gain = 1.0
        self.current_limiter_gain = 1.0
        self.gate_hold_frames = 0
        self.MAX_HOLD_FRAMES = 10  # 50ms * 10 = 500ms

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
        if data is None or len(data) == 0:
            self.level_ready.emit(0.0)
            return data
        data = data * self.gain

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
                self.gate_hold_frames = self.MAX_HOLD_FRAMES
            else:
                if self.gate_hold_frames > 0:
                    self.gate_hold_frames -= 1
                else:
                    self.gate_open = False
            
            target_gain = 1.0 if self.gate_open else 0.01
            
            # ゲインを滑らかに適用 (プツプツ音防止)
            if self.current_gate_gain != target_gain:
                gains = np.linspace(self.current_gate_gain, target_gain, len(data), dtype=np.float32).reshape(-1, 1)
                data = data * gains
                self.current_gate_gain = target_gain
            else:
                data = data * target_gain

        # ソフトリミッター (過大入力を滑らかに抑え、音割れを防ぐ)
        # マイクゲインを大きめに設定しても、この処理により適切な音量に均一化されます
        peak = np.max(np.abs(data))
        if peak > 0.99:
            # 0.99を超える場合は全体をスケールダウン
            data = data * (0.99 / peak)

        return data

    def run(self):
        import sys
        import warnings
        warnings.filterwarnings("ignore", message=".*data discontinuity.*")
        warnings.filterwarnings("ignore", module=".*soundcard.*")
        com_initialized = False
        if sys.platform == "win32":
            try:
                import ctypes
                hr = ctypes.windll.ole32.CoInitializeEx(None, 0)
                com_initialized = (hr in (0, 1))
            except Exception:
                pass
        try:
            import threading
            import soundcard as sc
            from recorder.audio_processor_wrapper import AudioProcessorWrapper

            # sc.SoundcardRuntimeWarning も明示的に無視する
            warnings.simplefilter("ignore", category=sc.SoundcardRuntimeWarning)
            sc_lock = getattr(sc, '_lock', threading.RLock())

            mic_device = None
            if self.mic_name and self.mic_name != "None":
                with sc_lock:
                    if self.mic_name.lower() == "default":
                        mic_device = sc.default_microphone()
                    else:
                        mics = sc.all_microphones(include_loopback=False)
                        for m in mics:
                            if self.mic_name == m.name:
                                mic_device = m
                                break
                        if mic_device is None:
                            for m in mics:
                                if self.mic_name in m.name:
                                    mic_device = m
                                    break

            if mic_device is not None:
                recorder = None
                channels = 1
                # soundcardは__enter__時にWASAPI初期化を行うため、ロック内で__enter__まで試行してチャンネルを決定する
                for ch in (2, 1, None):
                    with sc_lock:
                        try:
                            cand = (
                                mic_device.recorder(samplerate=48000, channels=ch)
                                if ch is not None
                                else mic_device.recorder(samplerate=48000)
                            )
                            cand.__enter__()
                            recorder = cand
                            channels = ch if ch is not None else (getattr(mic_device, 'channels', 1) or 1)
                            break
                        except Exception:
                            recorder = None
                if recorder is None:
                    print(f"[MicMonitorThread] Failed to initialize recorder for device: {self.mic_name}")

                processor = None
                processor_init_failed = False
                last_denoise = None
                last_preprocess = None

                def apply_native_dsp(raw_data):
                    nonlocal processor, processor_init_failed, last_denoise, last_preprocess
                    denoise_target = (
                        "DeepFilterNet" if self.denoise_mode in ("AI (DeepFilterNet)", "DeepFilterNet")
                        else "RNNoise" if self.denoise_mode in ("AI (RNNoise)", "RNNoise")
                        else "None"
                    )
                    preprocess_target = (
                        "SpeexDSP" if self.preprocess_mode and self.preprocess_mode != "None"
                        else "None"
                    )
                    needs_dsp = (preprocess_target != "None") or (denoise_target != "None")
                    if not needs_dsp or processor_init_failed:
                        return raw_data
                    if processor is None:
                        try:
                            processor = AudioProcessorWrapper(sample_rate=48000, channels=1)
                        except Exception as e:
                            print(f"Failed to initialize AudioProcessorWrapper: {e}")
                            processor_init_failed = True
                            return raw_data
                    if last_preprocess != preprocess_target:
                        processor.set_preprocess_type(preprocess_target)
                        last_preprocess = preprocess_target
                    if last_denoise != denoise_target:
                        processor.set_denoise_type(denoise_target)
                        last_denoise = denoise_target
                    return processor.process(raw_data)

                if recorder is not None:
                    active_player = None
                    player_channels = 2
                    try:
                        while self.running:
                            data = recorder.record(numframes=2400)
                            if not self.running:
                                break
                            if data is None or len(data) == 0:
                                self.level_ready.emit(0.0)
                                continue
                            if data.ndim == 1:
                                data = data.reshape(-1, 1)
                            elif data.ndim == 2 and data.shape[1] == 2:
                                # 片chのみ入力があるUSBマイク・オーディオIF等で音量が半減するのを防ぐ
                                peak_l = float(np.max(np.abs(data[:, 0])))
                                peak_r = float(np.max(np.abs(data[:, 1])))
                                if peak_l > 1e-4 and peak_r < peak_l * 0.05:
                                    data = data[:, 0:1]
                                elif peak_r > 1e-4 and peak_l < peak_r * 0.05:
                                    data = data[:, 1:2]
                                else:
                                    data = data.mean(axis=1, keepdims=True)
                            elif data.ndim == 2 and data.shape[1] > 2:
                                data = data.mean(axis=1, keepdims=True)
                            data = apply_native_dsp(data)
                            processed = self.process_audio(data)
                            if self.monitor_audio:
                                if active_player is None:
                                    for out_ch in (2, 1):
                                        try:
                                            with sc_lock:
                                                speaker = sc.default_speaker()
                                                cand_player = speaker.player(samplerate=48000, channels=out_ch)
                                                cand_player.__enter__()
                                                active_player = cand_player
                                                player_channels = out_ch
                                                break
                                        except Exception:
                                            active_player = None
                                if active_player is not None:
                                    try:
                                        if processed.ndim == 1:
                                            processed = processed.reshape(-1, 1)
                                        out_audio = np.repeat(processed, 2, axis=1) if player_channels == 2 else processed
                                        active_player.play(np.ascontiguousarray(out_audio, dtype=np.float32))
                                    except Exception:
                                        try:
                                            active_player.__exit__(None, None, None)
                                        except Exception:
                                            pass
                                        active_player = None
                            elif active_player is not None:
                                try:
                                    active_player.__exit__(None, None, None)
                                except Exception:
                                    pass
                                active_player = None
                    finally:
                        if active_player is not None:
                            try:
                                active_player.__exit__(None, None, None)
                            except Exception:
                                pass
                        try:
                            recorder.__exit__(None, None, None)
                        except Exception:
                            pass
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
            app_data_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'ValoReco')
            os.makedirs(app_data_dir, exist_ok=True)
            log_path = os.path.join(app_data_dir, "mic_error.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"MicMonitorThread error:\n{traceback.format_exc()}\n")
        finally:
            if com_initialized:
                try:
                    import ctypes
                    ctypes.windll.ole32.CoUninitialize()
                except Exception:
                    pass

    def stop(self):
        self.running = False
        if not self.wait(300):
            import os, datetime
            app_data_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'ValoReco')
            os.makedirs(app_data_dir, exist_ok=True)
            log_path = os.path.join(app_data_dir, "thread_error.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now()}] MicMonitorThread wait timed out.\n")
