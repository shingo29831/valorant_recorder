import logging
import math
import os
import subprocess
import threading
import time
from PyQt6.QtCore import pyqtSignal, QThread
import numpy as np
from core.config import get_app_data_dir

_SOUNDCARD_LOCK = threading.RLock()


def _get_soundcard_lock(sc_module):
    return getattr(sc_module, "_lock", None) or _SOUNDCARD_LOCK


class _NumpyHighPassFilter:
    """audio_processor.dll不可時のゼロ遅延80Hzハイパスフィルタ（1次厳密解・ブロックベクトル化）"""

    def __init__(self, sample_rate: int = 48000, cutoff_hz: float = 80.0) -> None:
        self._alpha = math.exp(-2.0 * math.pi * cutoff_hz / float(sample_rate))
        self._lp_state = 0.0
        self._block_len = 120
        idx = np.arange(self._block_len, dtype=np.float64)
        self._pows = self._alpha ** idx
        self._inv_pows = self._alpha ** (-idx)
        self._pows_next = self._alpha ** (idx + 1)

    def process(self, mono: np.ndarray) -> np.ndarray:
        if mono is None or mono.size == 0:
            return mono
        x = mono.reshape(-1).astype(np.float64, copy=False)
        n = x.size
        y_lp = np.empty(n, dtype=np.float64)
        state = self._lp_state
        one_minus_a = 1.0 - self._alpha
        b = self._block_len
        for start in range(0, n, b):
            seg = x[start : start + b]
            m = seg.size
            pows = self._pows[:m]
            inv_pows = self._inv_pows[:m]
            pows_next = self._pows_next[:m]
            lp_seg = state * pows_next + (one_minus_a * pows) * np.cumsum(seg * inv_pows)
            y_lp[start : start + m] = lp_seg
            state = float(lp_seg[-1])
        self._lp_state = state
        return (x - y_lp).astype(np.float32).reshape(mono.shape)


class _FFmpegMicMonitorDsp:
    """audio_processor.dll不可時に実録画と同一のFFmpegフィルタ(highpass+arnndn)を適用するリアルタイムDSP"""

    def __init__(self, sample_rate: int, enable_hpf: bool, enable_arnndn: bool) -> None:
        self._sample_rate = sample_rate
        self._enable_hpf = enable_hpf
        self._enable_arnndn = enable_arnndn
        self._proc: subprocess.Popen | None = None
        self._out_buf = bytearray()
        self._out_cond = threading.Condition()
        self._out_eof = False
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stderr_tail = bytearray()
        self._closed = False
        self._failed = False
        self.pending_probe = False
        self._first_frame_received = False
        self._stdin_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._start()

    @property
    def is_ready(self) -> bool:
        return (not self._failed) and (not self._closed) and (self._proc is not None) and (self._proc.poll() is None)

    def _start(self) -> None:
        try:
            from recorder.ffmpeg_command_builder import (
                is_ffmpeg_filter_known_supported,
                probe_ffmpeg_filters_async,
                resolve_ffmpeg_path,
                resolve_rnnoise_filter,
            )

            ffmpeg_path = resolve_ffmpeg_path()
            if not ffmpeg_path or not os.path.exists(ffmpeg_path):
                self._failed = True
                return

            filters: list[str] = []
            if self._enable_hpf:
                filters.append("highpass=f=80")
            if self._enable_arnndn:
                # 同期subprocess.runによる5秒ブロックとstop()タイムアウトを防ぐため非同期プローブを使用
                supported = is_ffmpeg_filter_known_supported(ffmpeg_path, "arnndn")
                if supported is None:
                    probe_ffmpeg_filters_async(ffmpeg_path)
                    self.pending_probe = True
                    self._failed = True
                    return
                if not supported:
                    self._failed = True
                    return
                rnnoise_part = resolve_rnnoise_filter(get_app_data_dir(), allow_download=False).rstrip(",")
                if not rnnoise_part:
                    self._failed = True
                    return
                filters.append(rnnoise_part)

            if not filters:
                self._failed = True
                return

            # 生PCMパイプの初回プローブ遅延を排除して即時出力を開始させる
            cmd = [
                ffmpeg_path,
                "-hide_banner",
                "-loglevel", "error",
                "-fflags", "+nobuffer",
                "-flags", "+low_delay",
                "-probesize", "32",
                "-analyzeduration", "0",
                "-f", "f32le",
                "-ar", str(self._sample_rate),
                "-ac", "1",
                "-i", "pipe:0",
                "-af", ",".join(filters),
                "-flush_packets", "1",
                "-f", "f32le",
                "-ar", str(self._sample_rate),
                "-ac", "1",
                "pipe:1",
            ]
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                creationflags=creationflags,
            )
            self._stderr_thread = threading.Thread(
                target=self._stderr_read_loop, name="MicMonitorFFmpegStderr", daemon=True
            )
            self._stderr_thread.start()
            self._reader_thread = threading.Thread(
                target=self._read_loop, name="MicMonitorFFmpegDsp", daemon=True
            )
            self._reader_thread.start()
        except Exception:
            self._failed = True
            self.close()

    def _stderr_read_loop(self) -> None:
        """stderrパイプ満杯によるFFmpegデッドロックを防ぐため常時排出する"""
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            while True:
                chunk = proc.stderr.read(512)
                if not chunk:
                    break
                self._stderr_tail.extend(chunk)
                if len(self._stderr_tail) > 1024:
                    del self._stderr_tail[:-512]
        except Exception:
            pass

    def _read_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            with self._out_cond:
                self._failed = True
                self._out_eof = True
                self._out_cond.notify_all()
            return
        max_buf_bytes = 64 * 1024
        try:
            while not self._closed:
                data = proc.stdout.read(4096)
                if not data:
                    break
                with self._out_cond:
                    self._out_buf.extend(data)
                    if len(self._out_buf) > max_buf_bytes:
                        # 4バイト(float32)境界を維持して古い余剰データを破棄
                        trim = ((len(self._out_buf) - max_buf_bytes) + 3) & ~3
                        del self._out_buf[:trim]
                    self._out_cond.notify_all()
        except Exception:
            pass
        finally:
            if not self._closed and self._stderr_tail:
                logging.warning(
                    "[MicMonitorFFmpegDsp] FFmpeg DSP exited: %s",
                    bytes(self._stderr_tail[-512:]).decode("utf-8", errors="replace").strip(),
                )
            with self._out_cond:
                self._failed = True
                self._out_eof = True
                self._out_cond.notify_all()

    def process(self, mono: np.ndarray, stop_event: threading.Event | None = None) -> np.ndarray | None:
        if not self.is_ready:
            return None
        flat = np.ascontiguousarray(mono.reshape(-1), dtype=np.float32)
        expected_bytes = flat.size * 4
        if expected_bytes <= 0:
            return mono
        try:
            with self._stdin_lock:
                proc = self._proc
                if self._closed or self._failed or proc is None or proc.stdin is None:
                    return None
                proc.stdin.write(flat.tobytes())
                proc.stdin.flush()

            # 初回はffmpeg起動・sh.rnnnロード遅延を許容しつつ、停止イベントで50ms以内に中断可能とする
            max_wait = 2.0 if not self._first_frame_received else 0.35
            deadline = time.monotonic() + max_wait
            with self._out_cond:
                while len(self._out_buf) < expected_bytes:
                    if self._closed or self._out_eof or (stop_event is not None and stop_event.is_set()):
                        return None
                    rem = deadline - time.monotonic()
                    if rem <= 0.0:
                        raise TimeoutError("FFmpeg DSP output timeout")
                    self._out_cond.wait(timeout=min(0.05, rem))
                out_bytes = bytes(self._out_buf[:expected_bytes])
                del self._out_buf[:expected_bytes]

            self._first_frame_received = True
            return np.frombuffer(out_bytes, dtype=np.float32).copy().reshape(mono.shape)
        except Exception:
            self._failed = True
            self.close()
            return None

    def abort(self) -> None:
        """UIスレッドからのstop()時に待機中process()を即時解除し子プロセスを停止する"""
        self._closed = True
        with self._out_cond:
            self._out_eof = True
            self._out_cond.notify_all()
        proc = self._proc
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass

    def close(self) -> None:
        with self._close_lock:
            self._closed = True
            with self._out_cond:
                self._out_eof = True
                self._out_cond.notify_all()
            proc = self._proc
            self._proc = None
            reader_t = self._reader_thread
            stderr_t = self._stderr_thread
            self._reader_thread = None
            self._stderr_thread = None
        if proc is not None:
            # 先に子プロセスを終了させてstdout/stderrをEOFにし、読取スレッド回収後にハンドルを安全破棄する
            if proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=0.12)
                except Exception:
                    try:
                        proc.kill()
                        proc.wait(timeout=0.05)
                    except Exception:
                        pass
            with self._stdin_lock:
                if proc.stdin is not None:
                    try:
                        proc.stdin.close()
                    except OSError:
                        pass
            if reader_t is not None and reader_t.is_alive():
                reader_t.join(timeout=0.15)
            if stderr_t is not None and stderr_t.is_alive():
                stderr_t.join(timeout=0.15)
            if (reader_t is None or not reader_t.is_alive()) and proc.stdout is not None:
                try:
                    proc.stdout.close()
                except OSError:
                    pass
            if (stderr_t is None or not stderr_t.is_alive()) and proc.stderr is not None:
                try:
                    proc.stderr.close()
                except OSError:
                    pass


class SystemAudioMonitorThread(QThread):
    level_ready = pyqtSignal(float)

    def __init__(self, gain, device_name="default", allow_default_fallback=True):
        super().__init__()
        self.gain = gain
        self.device_name = device_name
        self.allow_default_fallback = allow_default_fallback
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
            import soundcard as sc
            warnings.simplefilter("ignore", category=sc.SoundcardRuntimeWarning)
            sc_lock = _get_soundcard_lock(sc)

            with sc_lock:
                speaker = None
                if self.device_name and self.device_name not in ("default", "Default", "None"):
                    all_spks = sc.all_speakers()
                    target_lower = self.device_name.lower()
                    for s in all_spks:
                        if s.name.lower() == target_lower:
                            speaker = s
                            break
                    # チャット分離等のフォールバック抑止時は別デバイスへの誤爆を防ぐため部分一致を行わない
                    if speaker is None and self.allow_default_fallback:
                        for s in all_spks:
                            if target_lower in s.name.lower():
                                speaker = s
                                break
                if speaker is None and (self.allow_default_fallback or self.device_name in ("", "default", "Default")):
                    speaker = sc.default_speaker()
                recorder = None
                if speaker is not None:
                    spk_mic = sc.get_microphone(speaker.id, include_loopback=True)
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
            app_data_dir = get_app_data_dir(ensure_exists=True)
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
            import datetime
            app_data_dir = get_app_data_dir(ensure_exists=True)
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
        self._stop_event = threading.Event()
        self._dsp_lock = threading.Lock()
        self._active_ffmpeg_dsp: _FFmpegMicMonitorDsp | None = None
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

    def process_audio(self, data, apply_gain=True):
        if data is None or len(data) == 0:
            self.level_ready.emit(0.0)
            return data
        data = np.nan_to_num(data, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32, copy=False)
        if apply_gain:
            data = data * self.gain

        # メーター表示用のレベル計算（ゲート適用前）
        peak = float(np.max(np.abs(data)))
        level = min(1.0, peak ** 0.5) if np.isfinite(peak) else 0.0
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
            sc_lock = _get_soundcard_lock(sc)

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
                fallback_hpf = _NumpyHighPassFilter(sample_rate=48000, cutoff_hz=80.0)
                ffmpeg_dsp: _FFmpegMicMonitorDsp | None = None
                ffmpeg_dsp_key = None
                ffmpeg_dsp_retry_after = 0.0

                def _close_ffmpeg_dsp():
                    nonlocal ffmpeg_dsp, ffmpeg_dsp_key
                    with self._dsp_lock:
                        cur = ffmpeg_dsp
                        ffmpeg_dsp = None
                        ffmpeg_dsp_key = None
                        self._active_ffmpeg_dsp = None
                    if cur is not None:
                        cur.close()

                def apply_fallback_dsp(raw_data, preprocess_target, denoise_target):
                    nonlocal ffmpeg_dsp, ffmpeg_dsp_key, ffmpeg_dsp_retry_after
                    want_hpf = preprocess_target != "None"
                    want_arnndn = denoise_target in ("RNNoise", "DeepFilterNet")
                    if not want_arnndn:
                        if ffmpeg_dsp is not None:
                            _close_ffmpeg_dsp()
                        return fallback_hpf.process(raw_data) if want_hpf else raw_data

                    desired_key = (want_hpf, want_arnndn)
                    if ffmpeg_dsp is not None and (ffmpeg_dsp_key != desired_key or not ffmpeg_dsp.is_ready):
                        _close_ffmpeg_dsp()

                    if (
                        ffmpeg_dsp is None
                        and self.running
                        and not self._stop_event.is_set()
                        and time.monotonic() >= ffmpeg_dsp_retry_after
                    ):
                        cand = _FFmpegMicMonitorDsp(48000, enable_hpf=want_hpf, enable_arnndn=True)
                        if cand.is_ready:
                            with self._dsp_lock:
                                if self._stop_event.is_set() or not self.running:
                                    cand.close()
                                else:
                                    ffmpeg_dsp = cand
                                    ffmpeg_dsp_key = desired_key
                                    self._active_ffmpeg_dsp = cand
                        else:
                            pending_probe = cand.pending_probe
                            cand.close()
                            # 非同期フィルタプローブ完了待ち時は0.4秒、モデルDL待ち時は3秒で再チェック
                            ffmpeg_dsp_retry_after = time.monotonic() + (0.4 if pending_probe else 3.0)

                    if ffmpeg_dsp is not None:
                        out = ffmpeg_dsp.process(raw_data, stop_event=self._stop_event)
                        if out is not None:
                            return out
                        _close_ffmpeg_dsp()
                        ffmpeg_dsp_retry_after = time.monotonic() + 5.0

                    return fallback_hpf.process(raw_data) if want_hpf else raw_data

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
                    if not needs_dsp:
                        if ffmpeg_dsp is not None:
                            _close_ffmpeg_dsp()
                        return raw_data
                    if processor_init_failed:
                        return apply_fallback_dsp(raw_data, preprocess_target, denoise_target)
                    if processor is None:
                        if not AudioProcessorWrapper.is_available():
                            processor_init_failed = True
                            return apply_fallback_dsp(raw_data, preprocess_target, denoise_target)
                        try:
                            processor = AudioProcessorWrapper(sample_rate=48000, channels=1)
                        except Exception as e:
                            print(f"Failed to initialize AudioProcessorWrapper: {e}")
                            processor_init_failed = True
                            return apply_fallback_dsp(raw_data, preprocess_target, denoise_target)
                    if last_preprocess != preprocess_target:
                        processor.set_preprocess_type(preprocess_target)
                        last_preprocess = preprocess_target
                    if last_denoise != denoise_target:
                        processor.set_denoise_type(denoise_target)
                        last_denoise = denoise_target
                    elif denoise_target == "DeepFilterNet":
                        # 非同期モデル取得完了時にユーザー再操作なしで自動有効化
                        processor.try_activate_deepfilter_if_ready()
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
                            # 録画側(DownmixToStereo->AudioProcessor)と順序を統一し、歪み防止はC++ソフトクリップへ委譲
                            data = np.nan_to_num(data, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32, copy=False)
                            data = data * np.float32(self.gain)
                            data = apply_native_dsp(data)
                            processed = self.process_audio(data, apply_gain=False)
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
                        try:
                            _close_ffmpeg_dsp()
                        except Exception:
                            pass
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
            app_data_dir = get_app_data_dir(ensure_exists=True)
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
        self._stop_event.set()
        with self._dsp_lock:
            active_dsp = self._active_ffmpeg_dsp
        if active_dsp is not None:
            active_dsp.abort()
        if not self.wait(400):
            import datetime
            app_data_dir = get_app_data_dir(ensure_exists=True)
            log_path = os.path.join(app_data_dir, "thread_error.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now()}] MicMonitorThread wait timed out.\n")
