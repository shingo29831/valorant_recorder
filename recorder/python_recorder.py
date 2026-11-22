"""Module: @role: C++ネイティブDLL利用不可環境向けにsoundcard+FFmpegパイプで同等機能を提供するPure Python録画バックエンド"""
from collections import deque
import ctypes
from datetime import datetime
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from typing import Any, Optional
import numpy as np

_STARTUP_IDLE = 0
_STARTUP_INITIALIZING = 1
_STARTUP_RUNNING = 2
_STARTUP_FAILED = 3

_SAMPLE_RATE = 48000
_FRAMES_PER_CHUNK = 960  # 20ms @ 48kHz
_PREBUFFER_CHUNKS = 3  # 60msのジッター吸収帯を確保してアンダーラン無音を防止
_SOFT_MAX_CHUNKS = 6  # 120ms超過から連続リサンプリングによる滑らかなドリフト補正を開始
_HARD_MAX_CHUNKS = 15  # 300ms超過時のみ安全弁トリム＋クロスフェード
_DRIFT_RESAMPLE_EXTRA_MAX = 24  # 2.5%以内の連続時間圧縮で波形不連続クリックを完全排除
_UNDERFLOW_STRETCH_MIN_RATIO = 0.85  # 端数残留(816〜959サンプル)を滑らか補間して20ms無音挿入を防止
_CAPTURE_STARTED_MARKERS = ("Stream mapping:", "Press [q]", "Output #0", "frame=")

_BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
_NORMAL_PRIORITY_CLASS = 0x00000020
_PROCESS_SET_INFORMATION = 0x0200

# soundcard標準ビルドに_lockが無い環境でも全ワーカー間で確実にWASAPI初期化を排他する共通ロック
_SOUNDCARD_INIT_LOCK = threading.RLock()


def _get_soundcard_lock(sc_module: Any) -> Any:
    return getattr(sc_module, "_lock", None) or _SOUNDCARD_INIT_LOCK


def _unwrap_config(config_arg: Any) -> Any:
    if hasattr(config_arg, "_obj"):
        return config_arg._obj
    if hasattr(config_arg, "contents"):
        return config_arg.contents
    return config_arg


def _decode_c_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _downmix_to_stereo(data: np.ndarray, gain: float, fix_mono_sided_mic: bool = False) -> np.ndarray:
    arr = np.nan_to_num(data, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32, copy=False)
    if arr.ndim == 1:
        arr = arr.reshape(-1, 1)
    channels = arr.shape[1]
    if channels == 1:
        stereo = np.repeat(arr, 2, axis=1) * np.float32(gain)
    elif channels == 2:
        stereo = arr * np.float32(gain)
        # 2ch USBオーディオIFで片chのみ入力がある場合の片耳化を防止
        if fix_mono_sided_mic and stereo.shape[0] > 0:
            peak_l = float(np.max(np.abs(stereo[:, 0])))
            peak_r = float(np.max(np.abs(stereo[:, 1])))
            if peak_l > 1e-4 and peak_r < peak_l * 0.05:
                stereo[:, 1] = stereo[:, 0]
            elif peak_r > 1e-4 and peak_l < peak_r * 0.05:
                stereo[:, 0] = stereo[:, 1]
    elif channels >= 6:
        # ITU-R BS.775準拠: LFE(ch3)を除外して低域過多・位相干渉を防止
        fl = arr[:, 0]
        fr = arr[:, 1]
        fc = arr[:, 2]
        sl = arr[:, 4]
        sr = arr[:, 5]
        rl = arr[:, 6] if channels >= 8 else 0.0
        rr = arr[:, 7] if channels >= 8 else 0.0
        coeff = np.float32(0.7071)
        left = np.clip((fl + coeff * fc + coeff * sl + coeff * rl) * coeff * np.float32(gain), -1.0, 1.0)
        right = np.clip((fr + coeff * fc + coeff * sr + coeff * rr) * coeff * np.float32(gain), -1.0, 1.0)
        stereo = np.stack((left, right), axis=1).astype(np.float32, copy=False)
    else:
        stereo = arr[:, :2] * np.float32(gain)
    return np.ascontiguousarray(stereo, dtype=np.float32)


def _resample_stereo_continuous(src: np.ndarray, target_len: int, prev_sample: np.ndarray) -> np.ndarray:
    """直前チャンクの末尾サンプルから連続する線形補間リサンプリングでクリック音なく長さを調整する"""
    m = src.shape[0]
    if m == target_len:
        return src
    if m == 0:
        return np.zeros((target_len, 2), dtype=np.float32)
    padded = np.vstack((prev_sample, src))
    x_old = np.arange(m + 1, dtype=np.float64)
    x_new = np.linspace(float(m) / float(target_len), float(m), target_len, dtype=np.float64)
    out = np.empty((target_len, 2), dtype=np.float32)
    out[:, 0] = np.interp(x_new, x_old, padded[:, 0])
    out[:, 1] = np.interp(x_new, x_old, padded[:, 1])
    return out


def _apply_boundary_crossfade(prev_sample: np.ndarray, chunk: np.ndarray, fade_len: int = 64) -> np.ndarray:
    n = min(fade_len, chunk.shape[0])
    if n <= 0:
        return chunk
    out = chunk.copy()
    w = np.linspace(0.0, 1.0, n, dtype=np.float32).reshape(-1, 1)
    out[:n] = prev_sample * (1.0 - w) + out[:n] * w
    return out


class _AudioFrameQueue:
    """連続リサンプリングによるドリフト追従とA/V初期アラインメントを行うスレッドセーフ音声キュー"""

    def __init__(self, max_delay_ms: int = 2000) -> None:
        self._lock = threading.Lock()
        self._chunks: deque[np.ndarray] = deque()
        self._total_frames: int = 0
        self._max_frames: int = (_SAMPLE_RATE * max_delay_ms) // 1000
        self._last_sample: np.ndarray = np.zeros((1, 2), dtype=np.float32)

    def available_frames(self) -> int:
        with self._lock:
            return self._total_frames

    def _drop_oldest_locked(self, drop_frames: int) -> None:
        remaining = min(drop_frames, self._total_frames)
        while remaining > 0 and self._chunks:
            head = self._chunks[0]
            head_len = head.shape[0]
            if head_len <= remaining:
                self._chunks.popleft()
                self._total_frames -= head_len
                remaining -= head_len
            else:
                self._chunks[0] = head[remaining:]
                self._total_frames -= remaining
                remaining = 0

    def push(self, frames: np.ndarray) -> None:
        if frames is None or frames.shape[0] == 0:
            return
        with self._lock:
            self._chunks.append(frames)
            self._total_frames += frames.shape[0]
            if self._total_frames > self._max_frames:
                self._drop_oldest_locked(self._total_frames - self._max_frames)

    def drain_keep_latest(self, keep_frames: int) -> None:
        with self._lock:
            if self._total_frames > keep_frames:
                self._drop_oldest_locked(self._total_frames - keep_frames)

    def _take_locked(self, num_frames: int) -> np.ndarray:
        actual = min(num_frames, self._total_frames)
        out = np.zeros((actual, 2), dtype=np.float32)
        filled = 0
        while filled < actual and self._chunks:
            head = self._chunks[0]
            head_len = head.shape[0]
            need = actual - filled
            if head_len <= need:
                out[filled : filled + head_len] = head
                self._chunks.popleft()
                self._total_frames -= head_len
                filled += head_len
            else:
                out[filled:actual] = head[:need]
                self._chunks[0] = head[need:]
                self._total_frames -= need
                filled = actual
        return out

    def pop_chunk(
        self,
        num_frames: int,
        soft_max_frames: int,
        hard_max_frames: int,
        target_keep_frames: int,
    ) -> np.ndarray:
        with self._lock:
            prev = self._last_sample
            if self._total_frames > hard_max_frames:
                if self._total_frames > target_keep_frames:
                    self._drop_oldest_locked(self._total_frames - target_keep_frames)
                raw = self._take_locked(num_frames)
                if raw.shape[0] < num_frames:
                    out = np.zeros((num_frames, 2), dtype=np.float32)
                    out[: raw.shape[0]] = raw
                else:
                    out = raw
                out = _apply_boundary_crossfade(prev, out, fade_len=64)
            elif self._total_frames > soft_max_frames:
                # 生PCMの切り落としによる不連続ノイズを防ぎ、最大2.5%の連続リサンプリングで滑らかに追従
                excess = self._total_frames - target_keep_frames
                extra = min(excess, _DRIFT_RESAMPLE_EXTRA_MAX, self._total_frames - num_frames)
                src = self._take_locked(num_frames + max(0, extra))
                out = _resample_stereo_continuous(src, num_frames, prev)
            elif self._total_frames >= num_frames:
                out = self._take_locked(num_frames)
            elif self._total_frames >= int(num_frames * _UNDERFLOW_STRETCH_MIN_RATIO):
                # 端数残留(例:959サンプル)による20ms無音挿入と滞留を防ぎ滑らかに補間伸長する
                src = self._take_locked(self._total_frames)
                out = _resample_stereo_continuous(src, num_frames, prev)
            elif self._total_frames > 0:
                src = self._take_locked(self._total_frames)
                out = np.zeros((num_frames, 2), dtype=np.float32)
                m = src.shape[0]
                out[:m] = src
                fade = min(64, m)
                ramp = np.linspace(1.0, 0.0, fade, dtype=np.float32).reshape(-1, 1)
                out[m - fade : m] *= ramp
            else:
                out = np.zeros((num_frames, 2), dtype=np.float32)
                if float(np.max(np.abs(prev))) > 1e-5:
                    fade = min(32, num_frames)
                    ramp = np.linspace(1.0, 0.0, fade, dtype=np.float32).reshape(-1, 1)
                    out[:fade] = prev * ramp

            self._last_sample = out[-1:, :].copy()
            return out

    def pop_exact(self, num_frames: int) -> np.ndarray:
        with self._lock:
            raw = self._take_locked(num_frames)
            if raw.shape[0] < num_frames:
                out = np.zeros((num_frames, 2), dtype=np.float32)
                m = raw.shape[0]
                if m > 0:
                    out[:m] = raw
                    fade = min(32, m)
                    ramp = np.linspace(1.0, 0.0, fade, dtype=np.float32).reshape(-1, 1)
                    out[m - fade : m] *= ramp
            else:
                out = raw
            self._last_sample = out[-1:, :].copy()
            return out


class _PythonNoiseGate:
    """MicMonitorThread と同一の RMS 閾値・ホールド・スムージングを持つノイズゲート"""

    def __init__(self, threshold_percent: float) -> None:
        norm = max(0.0, min(1.0, float(threshold_percent)))
        self._amp_threshold = 0.0 if norm <= 0.001 else ((norm / 2.0) ** 2)
        self._current_gain = 1.0
        self._gate_open = False
        self._hold_chunks_remaining = 0
        # 20ms * 25 = 500ms のホールド時間で MicMonitorThread と挙動を統一
        self._hold_chunks = max(1, int(round(0.50 * _SAMPLE_RATE / _FRAMES_PER_CHUNK)))

    def process(self, stereo: np.ndarray) -> np.ndarray:
        if self._amp_threshold <= 0.0 or stereo.shape[0] == 0:
            return stereo
        rms = float(np.sqrt(np.mean(stereo * stereo) + 1e-8))
        if rms > self._amp_threshold:
            self._gate_open = True
            self._hold_chunks_remaining = self._hold_chunks
        elif self._hold_chunks_remaining > 0:
            self._hold_chunks_remaining -= 1
        else:
            self._gate_open = False

        target_gain = 1.0 if self._gate_open else 0.01
        if abs(self._current_gain - target_gain) > 1e-4:
            ramp = np.linspace(self._current_gain, target_gain, stereo.shape[0], dtype=np.float32).reshape(-1, 1)
            self._current_gain = target_gain
            return stereo * ramp
        return stereo * np.float32(target_gain)


class _SoftLimiter:
    def __init__(self) -> None:
        self._gain = 1.0

    def process(self, stereo: np.ndarray) -> np.ndarray:
        if stereo.shape[0] == 0:
            return stereo
        peak = float(np.max(np.abs(stereo)))
        target = (0.99 / peak) if peak > 0.99 else 1.0
        if abs(self._gain - target) > 1e-4:
            ramp = np.linspace(self._gain, target, stereo.shape[0], dtype=np.float32).reshape(-1, 1)
            self._gain = target
            return np.clip(stereo * ramp, -1.0, 1.0)
        if target < 1.0:
            return np.clip(stereo * np.float32(target), -1.0, 1.0)
        return stereo


class _SilenceKeepAliveThread(threading.Thread):
    """WASAPI Loopbackが無音時にブロックするのを防ぐため対象スピーカーへ無音を継続供給する"""

    def __init__(self, speaker: Any, stop_event: threading.Event, sc_lock: Any) -> None:
        super().__init__(name="PyWasapiSilenceKeepAlive", daemon=True)
        self._speaker = speaker
        self._stop_event = stop_event
        self._sc_lock = sc_lock
        self.started_event = threading.Event()
        self.is_active = False

    def run(self) -> None:
        com_initialized = False
        if sys.platform == "win32":
            try:
                hr = ctypes.windll.ole32.CoInitializeEx(None, 0)
                com_initialized = hr in (0, 1)
            except Exception:
                pass
        player = None
        channels = 2
        try:
            with self._sc_lock:
                for ch in (2, 1, None):
                    try:
                        cand = (
                            self._speaker.player(samplerate=_SAMPLE_RATE, channels=ch, blocksize=_FRAMES_PER_CHUNK)
                            if ch is not None
                            else self._speaker.player(samplerate=_SAMPLE_RATE, blocksize=_FRAMES_PER_CHUNK)
                        )
                        cand.__enter__()
                        player = cand
                        channels = ch if ch is not None else (getattr(self._speaker, "channels", 2) or 2)
                        break
                    except Exception:
                        player = None

            if player is None:
                self.started_event.set()
                return

            self.is_active = True
            self.started_event.set()
            silent_block = np.zeros((_FRAMES_PER_CHUNK, channels), dtype=np.float32)
            while not self._stop_event.is_set():
                player.play(silent_block)
        except Exception:
            if not self.started_event.is_set():
                self.started_event.set()
        finally:
            if player is not None:
                try:
                    player.__exit__(None, None, None)
                except Exception:
                    pass
            if com_initialized:
                try:
                    ctypes.windll.ole32.CoUninitialize()
                except Exception:
                    pass


class _CaptureWorker(threading.Thread):
    """soundcard (WASAPI) で1系統の音声ストリームを非同期キャプチャするワーカースレッド"""

    def __init__(
        self,
        role: str,
        device_name: str,
        gain: float,
        allow_fallback: bool,
        out_queue: _AudioFrameQueue,
        stop_event: threading.Event,
        log_fn,
    ) -> None:
        super().__init__(name=f"PyAudioCapture-{role}", daemon=True)
        self.role = role
        self.device_name = device_name
        self.gain = float(gain)
        self.allow_fallback = allow_fallback
        self.out_queue = out_queue
        self.stop_event = stop_event
        self.log_fn = log_fn
        self.init_done = threading.Event()
        self.is_active = False
        self.has_silence_keepalive = False
        self._silence_stop = threading.Event()
        self._silence_thread: Optional[_SilenceKeepAliveThread] = None

    def stop_silence_keepalive(self) -> None:
        """record()ハング時でもサイレント再生スレッドのリークとWASAPI競合を確実に防ぐ"""
        self._silence_stop.set()

    def get_auxiliary_threads(self) -> list[threading.Thread]:
        return [self._silence_thread] if self._silence_thread is not None else []

    def _resolve_device(self, sc: Any) -> tuple[Any, Any]:
        name = self.device_name.strip()
        name_lower = name.lower()
        if self.role in ("system", "chat"):
            speaker = None
            if name and name_lower not in ("default",):
                all_spks = sc.all_speakers()
                for s in all_spks:
                    if s.name.lower() == name_lower:
                        speaker = s
                        break
                if speaker is None and self.allow_fallback:
                    for s in all_spks:
                        if name_lower in s.name.lower():
                            speaker = s
                            break
            if speaker is None and (self.allow_fallback or name_lower in ("", "default")):
                speaker = sc.default_speaker()
            if speaker is None:
                return None, None
            return sc.get_microphone(speaker.id, include_loopback=True), speaker

        mic = None
        if name and name_lower not in ("default",):
            all_mics = sc.all_microphones(include_loopback=False)
            for m in all_mics:
                if m.name.lower() == name_lower:
                    mic = m
                    break
            if mic is None and self.allow_fallback:
                for m in all_mics:
                    if name_lower in m.name.lower():
                        mic = m
                        break
        if mic is None and (self.allow_fallback or name_lower in ("", "default")):
            mic = sc.default_microphone()
        return mic, None

    def run(self) -> None:
        com_initialized = False
        if sys.platform == "win32":
            try:
                hr = ctypes.windll.ole32.CoInitializeEx(None, 0)
                com_initialized = hr in (0, 1)
            except Exception:
                pass
        recorder = None
        try:
            import soundcard as sc
            import warnings

            warnings.simplefilter("ignore", category=sc.SoundcardRuntimeWarning)
            sc_lock = _get_soundcard_lock(sc)

            with sc_lock:
                dev, speaker = self._resolve_device(sc)
                if dev is not None:
                    for ch in (2, 1, None):
                        try:
                            cand = (
                                dev.recorder(samplerate=_SAMPLE_RATE, channels=ch, blocksize=_FRAMES_PER_CHUNK)
                                if ch is not None
                                else dev.recorder(samplerate=_SAMPLE_RATE, blocksize=_FRAMES_PER_CHUNK)
                            )
                            cand.__enter__()
                            recorder = cand
                            break
                        except Exception:
                            recorder = None

            if recorder is None:
                self.log_fn(
                    f"[WARNING] {self.role} audio init failed ({self.device_name or 'default'}). "
                    "Continuing with silent track."
                )
                self.is_active = False
                self.init_done.set()
                return

            # Loopback時は無音でもパケットが途切れないようサイレントレンダーを起動してから録音開始
            if speaker is not None and not self.stop_event.is_set():
                self._silence_thread = _SilenceKeepAliveThread(speaker, self._silence_stop, sc_lock)
                self._silence_thread.start()
                self._silence_thread.started_event.wait(timeout=1.0)
                self.has_silence_keepalive = self._silence_thread.is_active

            self.is_active = True
            self.init_done.set()
            fix_mono_mic = self.role == "mic"

            while not self.stop_event.is_set():
                data = recorder.record(numframes=_FRAMES_PER_CHUNK)
                if self.stop_event.is_set():
                    break
                if data is None or len(data) == 0:
                    continue
                stereo = _downmix_to_stereo(data, self.gain, fix_mono_sided_mic=fix_mono_mic)
                self.out_queue.push(stereo)
        except Exception as e:
            self.log_fn(f"[WARNING] {self.role} capture worker error: {e}")
            if not self.init_done.is_set():
                self.is_active = False
                self.init_done.set()
        finally:
            if recorder is not None:
                try:
                    recorder.__exit__(None, None, None)
                except Exception:
                    pass
            # record()脱出後にサイレントレンダーを停止し、終了直前の無音ハングを防止
            self._silence_stop.set()
            if self._silence_thread is not None and self._silence_thread.is_alive():
                self._silence_thread.join(timeout=0.3)
            if com_initialized:
                try:
                    ctypes.windll.ole32.CoUninitialize()
                except Exception:
                    pass


def _set_process_priority_windows(pid: int, low_priority: bool) -> None:
    if sys.platform != "win32" or not pid:
        return
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(_PROCESS_SET_INFORMATION, False, int(pid))
        if not handle:
            return
        try:
            prio = _BELOW_NORMAL_PRIORITY_CLASS if low_priority else _NORMAL_PRIORITY_CLASS
            kernel32.SetPriorityClass(handle, prio)
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        pass


def _join_threads_within_budget(threads: list[threading.Thread], total_timeout: float) -> None:
    """複数スレッドの直列待ち超過を防ぎ、全体予算内で並列回収する"""
    deadline = time.monotonic() + max(0.0, total_timeout)
    for t in threads:
        if t is not None and t.is_alive():
            rem = deadline - time.monotonic()
            if rem <= 0.0:
                break
            t.join(timeout=rem)


class PythonRecorderBackend:
    """C++ native_recorder.dll と完全互換のPure Python録画バックエンド"""

    def __init__(self) -> None:
        self._recorder_lock = threading.Lock()
        self._process_lock = threading.Lock()
        self._log_lock = threading.Lock()
        self._lingering_lock = threading.Lock()
        self._is_running = False
        self._startup_state = _STARTUP_IDLE
        self._actual_start_time_ms = 0
        self._actual_end_time_ms = 0
        self._last_error = ""
        self._session_id = 0
        self._recording_thread: Optional[threading.Thread] = None
        self._active_process: Optional[subprocess.Popen] = None
        self._lingering_threads: list[threading.Thread] = []
        self._debug_log_file = None

    def _log(self, msg: str) -> None:
        with self._log_lock:
            if self._debug_log_file and not self._debug_log_file.closed:
                try:
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    self._debug_log_file.write(f"{ts} [PythonRecorder] {msg}\n")
                    self._debug_log_file.flush()
                except OSError:
                    pass

    def _set_last_error(self, msg: str) -> None:
        self._last_error = msg
        if msg:
            self._log(f"[ERROR] {msg}")

    def _reap_lingering_threads(self, timeout: float = 1.5) -> None:
        with self._lingering_lock:
            pending = [t for t in self._lingering_threads if t.is_alive()]
        if pending:
            _join_threads_within_budget(pending, timeout)
        with self._lingering_lock:
            self._lingering_threads = [t for t in self._lingering_threads if t.is_alive()]

    def Recorder_Start(self, config_ptr: Any) -> bool:
        with self._recorder_lock:
            if self._recording_thread is not None and self._recording_thread.is_alive():
                self._recording_thread.join(timeout=5.0)
                if self._recording_thread.is_alive():
                    self._set_last_error("Previous recording session is still shutting down.")
                    return False
            # 前セッションの残留WASAPIワーカーがあれば新セッション開始前に回収してデバイス競合を防止
            self._reap_lingering_threads(timeout=1.5)
            if self._is_running:
                self._set_last_error("Recorder is already running.")
                return False

            cfg = _unwrap_config(config_ptr)
            if cfg is None:
                self._set_last_error("Invalid configuration.")
                return False

            sys_device = _decode_c_str(getattr(cfg, "system_device_name", b""))
            mic_device = _decode_c_str(getattr(cfg, "mic_device_name", b""))
            chat_device = _decode_c_str(getattr(cfg, "chat_device_name", b""))
            save_file_path = _decode_c_str(getattr(cfg, "save_file_path", b""))
            ffmpeg_cmd = _decode_c_str(getattr(cfg, "ffmpeg_command", b""))
            session_cfg = {
                "sys_device": sys_device,
                "mic_device": mic_device,
                "chat_device": chat_device,
                "save_file_path": save_file_path,
                "ffmpeg_cmd": ffmpeg_cmd,
                "system_gain": float(getattr(cfg, "system_gain", 1.0)),
                "mic_gain": float(getattr(cfg, "mic_gain", 1.0)),
                "chat_gain": float(getattr(cfg, "chat_gain", 1.0)),
                "noise_gate_threshold": float(getattr(cfg, "noise_gate_threshold", 0.0)),
                "denoise_mode": int(getattr(cfg, "denoise_mode", 0)),
                "preprocess_mode": int(getattr(cfg, "preprocess_mode", 0)),
                "low_priority": bool(getattr(cfg, "low_priority", 0)),
                "chat_enabled": bool(getattr(cfg, "chat_enabled", 0)),
            }

            save_dir = os.path.dirname(save_file_path) if save_file_path else "."
            os.makedirs(save_dir or ".", exist_ok=True)
            debug_log_path = os.path.join(save_dir or ".", "native_recorder_debug.log")
            with self._log_lock:
                if self._debug_log_file and not self._debug_log_file.closed:
                    try:
                        self._debug_log_file.close()
                    except OSError:
                        pass
                try:
                    self._debug_log_file = open(debug_log_path, "a", encoding="utf-8", errors="replace")
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    self._debug_log_file.write(f"\n--- New PythonRecorder Session Started at {ts} ---\n")
                    self._debug_log_file.flush()
                except OSError:
                    self._debug_log_file = None

            self._session_id += 1
            my_session_id = self._session_id
            self._actual_start_time_ms = 0
            self._actual_end_time_ms = 0
            self._startup_state = _STARTUP_INITIALIZING
            self._is_running = True
            self._last_error = ""

            self._recording_thread = threading.Thread(
                target=self._recording_loop,
                args=(session_cfg, my_session_id),
                name="PythonRecorderMain",
                daemon=True,
            )
            self._recording_thread.start()
            return True

    def Recorder_Stop(self) -> None:
        with self._recorder_lock:
            self._is_running = False
            t = self._recording_thread
            if t is not None and t.is_alive():
                t.join(timeout=14.0)
            if t is None or not t.is_alive():
                self._recording_thread = None
                self._startup_state = _STARTUP_IDLE
                with self._log_lock:
                    if self._debug_log_file and not self._debug_log_file.closed:
                        try:
                            self._debug_log_file.write("--- PythonRecorder Session Ended ---\n")
                            self._debug_log_file.close()
                        except OSError:
                            pass
                    self._debug_log_file = None

    def Recorder_IsRunning(self) -> bool:
        return bool(self._is_running)

    def Recorder_GetStartupState(self) -> int:
        return int(self._startup_state)

    def Recorder_SetLowPriority(self, low_priority: bool) -> None:
        with self._process_lock:
            proc = self._active_process
        if proc is not None and proc.poll() is None:
            _set_process_priority_windows(proc.pid, bool(low_priority))
            self._log(f"Updated FFmpeg process priority to {'BELOW_NORMAL' if low_priority else 'NORMAL'}")

    def Recorder_GetLastError(self) -> bytes:
        return self._last_error.encode("utf-8", errors="replace")

    def Recorder_GetActualStartTimeMs(self) -> int:
        return int(self._actual_start_time_ms)

    def Recorder_GetActualEndTimeMs(self) -> int:
        return int(self._actual_end_time_ms)

    def Recorder_SupportsChatTrack(self) -> bool:
        return True

    def _init_optional_audio_processor(self, denoise_mode: int, preprocess_mode: int) -> Any:
        if denoise_mode <= 0 and preprocess_mode <= 0:
            return None
        try:
            from recorder.audio_processor_wrapper import AudioProcessorWrapper

            if not AudioProcessorWrapper.is_available():
                return None
            proc = AudioProcessorWrapper(sample_rate=_SAMPLE_RATE, channels=2)
            proc.set_preprocess_type("SpeexDSP" if preprocess_mode == 1 else "None")
            if denoise_mode == 1:
                proc.set_denoise_type("DeepFilterNet")
            elif denoise_mode == 2:
                proc.set_denoise_type("RNNoise")
            else:
                proc.set_denoise_type("None")
            return proc
        except Exception as e:
            self._log(f"Optional AudioProcessorWrapper skipped: {e}")
            return None

    def _recording_loop(self, cfg: dict[str, Any], my_session_id: int) -> None:
        proc: Optional[subprocess.Popen] = None
        log_thread: Optional[threading.Thread] = None
        writer_thread: Optional[threading.Thread] = None
        capture_stop = threading.Event()
        writer_stop = threading.Event()
        workers: list[_CaptureWorker] = []
        # asetpts=N/SR/TBのため出力音声は破棄せず10秒分の十分なキューでバックプレッシャー制御する
        out_pipe_queue: queue.Queue[Optional[bytes]] = queue.Queue(maxsize=500)

        def _force_signal_writer_stop() -> None:
            writer_stop.set()
            while True:
                try:
                    out_pipe_queue.put_nowait(None)
                    return
                except queue.Full:
                    try:
                        out_pipe_queue.get_nowait()
                    except queue.Empty:
                        return

        try:
            chat_enabled = cfg["chat_enabled"]
            out_channels = 6 if chat_enabled else 4
            save_file_path = cfg["save_file_path"]
            save_dir = os.path.dirname(save_file_path) if save_file_path else "."
            ffmpeg_log_path = os.path.join(save_dir or ".", "native_ffmpeg.log")

            sys_queue = _AudioFrameQueue(max_delay_ms=2000)
            mic_queue = _AudioFrameQueue(max_delay_ms=2000)
            chat_queue = _AudioFrameQueue(max_delay_ms=2000)

            sys_worker: Optional[_CaptureWorker] = None
            mic_worker: Optional[_CaptureWorker] = None
            chat_worker: Optional[_CaptureWorker] = None

            if cfg["sys_device"] != "__NONE__":
                sys_worker = _CaptureWorker(
                    "system", cfg["sys_device"], cfg["system_gain"], True, sys_queue, capture_stop, self._log
                )
                workers.append(sys_worker)
            if cfg["mic_device"] != "__NONE__":
                mic_worker = _CaptureWorker(
                    "mic", cfg["mic_device"], cfg["mic_gain"], True, mic_queue, capture_stop, self._log
                )
                workers.append(mic_worker)
            if chat_enabled and cfg["chat_device"] != "__NONE__":
                chat_allow_fb = cfg["chat_device"].strip().lower() in ("", "default")
                chat_worker = _CaptureWorker(
                    "chat", cfg["chat_device"], cfg["chat_gain"], chat_allow_fb, chat_queue, capture_stop, self._log
                )
                workers.append(chat_worker)

            audio_proc = None
            df_needs_activation = False
            if mic_worker is not None:
                audio_proc = self._init_optional_audio_processor(cfg["denoise_mode"], cfg["preprocess_mode"])
                if audio_proc is not None and cfg["denoise_mode"] == 1:
                    df_needs_activation = not audio_proc.try_activate_deepfilter_if_ready()

            creationflags = 0
            startupinfo = None
            if sys.platform == "win32":
                prio_flag = _BELOW_NORMAL_PRIORITY_CLASS if cfg["low_priority"] else _NORMAL_PRIORITY_CLASS
                creationflags = subprocess.CREATE_NO_WINDOW | prio_flag
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE

            self._log(f"Executing FFmpeg command: {cfg['ffmpeg_cmd']}")
            try:
                proc = subprocess.Popen(
                    cfg["ffmpeg_cmd"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=0,
                    creationflags=creationflags,
                    startupinfo=startupinfo,
                )
            except Exception as e:
                self._set_last_error(f"Failed to start FFmpeg process: {e}")
                self._startup_state = _STARTUP_FAILED
                self._is_running = False
                return

            with self._process_lock:
                self._active_process = proc

            capture_started = threading.Event()
            capture_started_perf: list[float] = [0.0]

            def _ffmpeg_log_reader() -> None:
                acc = ""
                try:
                    with open(ffmpeg_log_path, "wb") as lf:
                        stream = proc.stdout
                        while stream is not None:
                            chunk = stream.read(1024)
                            if not chunk:
                                break
                            lf.write(chunk)
                            if not capture_started.is_set():
                                lf.flush()
                                acc += chunk.decode("utf-8", errors="replace")
                                if any(m in acc for m in _CAPTURE_STARTED_MARKERS):
                                    capture_started_perf[0] = time.perf_counter()
                                    capture_started.set()
                                if len(acc) > 16384:
                                    acc = acc[-4096:]
                except Exception:
                    pass

            log_thread = threading.Thread(target=_ffmpeg_log_reader, name="PythonFFmpegLogReader", daemon=True)
            log_thread.start()

            time.sleep(0.2)
            if proc.poll() is not None:
                self._set_last_error(f"FFmpeg process crashed immediately with exit code {proc.returncode}")
                self._startup_state = _STARTUP_FAILED
                self._is_running = False
                return

            # pipe:0へプローブ投入する前に音声ワーカーとプリバッファを先行準備し、映像開始後のA/V遅延ズレを防ぐ
            for w in workers:
                w.start()
            init_deadline = time.monotonic() + 1.5
            for w in workers:
                w.init_done.wait(timeout=max(0.0, init_deadline - time.monotonic()))

            has_active_sys = bool(sys_worker and sys_worker.is_active)
            has_active_mic = bool(mic_worker and mic_worker.is_active)
            has_active_chat = bool(chat_worker and chat_worker.is_active)
            sys_wait_audio = has_active_sys and bool(sys_worker and sys_worker.has_silence_keepalive)
            chat_wait_audio = has_active_chat and bool(chat_worker and chat_worker.has_silence_keepalive)

            prebuffer_frames = _FRAMES_PER_CHUNK * _PREBUFFER_CHUNKS
            prebuf_deadline = time.monotonic() + 0.5
            while self._is_running and proc.poll() is None and time.monotonic() < prebuf_deadline:
                sys_ready = (not sys_wait_audio) or (sys_queue.available_frames() >= prebuffer_frames)
                mic_ready = (not has_active_mic) or (mic_queue.available_frames() >= prebuffer_frames)
                chat_ready = (not chat_wait_audio) or (chat_queue.available_frames() >= prebuffer_frames)
                if sys_ready and mic_ready and chat_ready:
                    break
                time.sleep(0.005)

            writer_alive = True

            def _pipe_writer() -> None:
                nonlocal writer_alive
                try:
                    stdin = proc.stdin
                    while True:
                        try:
                            payload = out_pipe_queue.get(timeout=0.05)
                        except queue.Empty:
                            if writer_stop.is_set():
                                break
                            continue
                        if payload is None or stdin is None:
                            break
                        try:
                            stdin.write(payload)
                        except (BrokenPipeError, OSError) as write_err:
                            self._log(f"FFmpeg stdin write closed: {write_err}")
                            self._is_running = False
                            break
                    if stdin is not None and not writer_stop.is_set():
                        try:
                            stdin.flush()
                        except OSError:
                            pass
                finally:
                    writer_alive = False

            writer_thread = threading.Thread(target=_pipe_writer, name="PythonFFmpegPipeWriter", daemon=True)
            writer_thread.start()

            def _enqueue_pipe(raw_bytes: bytes) -> bool:
                while writer_alive and proc.poll() is None and not writer_stop.is_set():
                    try:
                        out_pipe_queue.put(raw_bytes, timeout=0.05)
                        return True
                    except queue.Full:
                        if not self._is_running:
                            return False
                return False

            # pipe:0プローブ解放用の1チャンク(20ms)を投入し、サンプル総数へ厳密に計上する
            _enqueue_pipe(np.zeros((_FRAMES_PER_CHUNK, out_channels), dtype=np.float32).tobytes())
            total_written_frames = _FRAMES_PER_CHUNK

            sync_wait_start = time.monotonic()
            while self._is_running:
                if proc.poll() is not None:
                    self._set_last_error(f"FFmpeg process exited during initialization with code {proc.returncode}")
                    self._startup_state = _STARTUP_FAILED
                    self._is_running = False
                    break
                if capture_started.is_set() or (time.monotonic() - sync_wait_start) >= 3.0:
                    break
                time.sleep(0.005)

            if self._is_running and proc.poll() is None:
                self._startup_state = _STARTUP_RUNNING
            else:
                if self._startup_state == _STARTUP_INITIALIZING:
                    self._startup_state = _STARTUP_FAILED
                self._is_running = False
                return

            # 60msのジッター吸収帯を残してアラインし、映像開始時刻から先行20ms分を相殺してA/V同期を固定
            sys_queue.drain_keep_latest(prebuffer_frames)
            mic_queue.drain_keep_latest(prebuffer_frames)
            if chat_enabled:
                chat_queue.drain_keep_latest(prebuffer_frames)

            ref_perf = capture_started_perf[0] if capture_started_perf[0] > 0.0 else time.perf_counter()
            start_perf = ref_perf - (total_written_frames / float(_SAMPLE_RATE))
            self._actual_start_time_ms = int(time.time() * 1000)

            soft_max_frames = _FRAMES_PER_CHUNK * _SOFT_MAX_CHUNKS
            hard_max_frames = _FRAMES_PER_CHUNK * _HARD_MAX_CHUNKS

            mic_gate = _PythonNoiseGate(cfg["noise_gate_threshold"])
            sys_limiter = _SoftLimiter()
            mic_limiter = _SoftLimiter()
            chat_limiter = _SoftLimiter()
            silent_chunk = np.zeros((_FRAMES_PER_CHUNK, 2), dtype=np.float32)
            chunks_since_df_check = 0

            def _emit_chunk(drain_mode: bool) -> bool:
                nonlocal total_written_frames, df_needs_activation, chunks_since_df_check
                if has_active_sys:
                    sys_buf = (
                        sys_queue.pop_exact(_FRAMES_PER_CHUNK)
                        if drain_mode
                        else sys_queue.pop_chunk(_FRAMES_PER_CHUNK, soft_max_frames, hard_max_frames, prebuffer_frames)
                    )
                    sys_out = sys_limiter.process(sys_buf)
                else:
                    sys_out = silent_chunk

                if has_active_mic:
                    mic_buf = (
                        mic_queue.pop_exact(_FRAMES_PER_CHUNK)
                        if drain_mode
                        else mic_queue.pop_chunk(_FRAMES_PER_CHUNK, soft_max_frames, hard_max_frames, prebuffer_frames)
                    )
                    if audio_proc is not None:
                        if df_needs_activation:
                            chunks_since_df_check += 1
                            if chunks_since_df_check >= 50:
                                chunks_since_df_check = 0
                                if audio_proc.try_activate_deepfilter_if_ready():
                                    df_needs_activation = False
                        mic_buf = audio_proc.process(mic_buf)
                    mic_buf = mic_gate.process(mic_buf)
                    mic_out = mic_limiter.process(mic_buf)
                else:
                    mic_out = silent_chunk

                if chat_enabled:
                    if has_active_chat:
                        chat_buf = (
                            chat_queue.pop_exact(_FRAMES_PER_CHUNK)
                            if drain_mode
                            else chat_queue.pop_chunk(_FRAMES_PER_CHUNK, soft_max_frames, hard_max_frames, prebuffer_frames)
                        )
                        chat_out = chat_limiter.process(chat_buf)
                    else:
                        chat_out = silent_chunk
                    combined = np.concatenate((sys_out, mic_out, chat_out), axis=1)
                else:
                    combined = np.concatenate((sys_out, mic_out), axis=1)

                if not _enqueue_pipe(np.ascontiguousarray(combined, dtype=np.float32).tobytes()):
                    return False
                total_written_frames += _FRAMES_PER_CHUNK
                return True

            self._log("Entering main Python recording loop (A/V Aligned 48kHz Pipeline)...")
            sleep_sec = 0.006 if cfg["low_priority"] else 0.003

            while self._is_running:
                if proc.poll() is not None:
                    self._set_last_error(f"FFmpeg process terminated unexpectedly with code {proc.returncode}")
                    self._is_running = False
                    break

                # start_perfは巻き戻さず経過時間に一致する総サンプル数を欠落なく供給する
                target_frames = int((time.perf_counter() - start_perf) * _SAMPLE_RATE)
                burst = 0
                while self._is_running and (total_written_frames + _FRAMES_PER_CHUNK) <= target_frames and burst < 50:
                    if not _emit_chunk(False):
                        break
                    burst += 1

                if burst < 50:
                    time.sleep(sleep_sec)

            # 正常停止時のみ最大200msの末尾追従と残余キューのドレインを実施
            if writer_alive and proc.poll() is None:
                tail_deadline = time.perf_counter() + 0.20
                while writer_alive and proc.poll() is None and time.perf_counter() < tail_deadline:
                    target_frames = int((time.perf_counter() - start_perf) * _SAMPLE_RATE)
                    while (total_written_frames + _FRAMES_PER_CHUNK) <= target_frames:
                        if not _emit_chunk(False):
                            break
                    time.sleep(0.003)

            capture_stop.set()
            _join_threads_within_budget(workers, 0.50)

            if writer_alive and proc.poll() is None:
                rem_frames = max(sys_queue.available_frames(), mic_queue.available_frames())
                if chat_enabled:
                    rem_frames = max(rem_frames, chat_queue.available_frames())
                drain_chunks = min(20, max(5, rem_frames // _FRAMES_PER_CHUNK + 1))
                for _ in range(drain_chunks):
                    if not _emit_chunk(True):
                        break

            # 正常時はキュー残余を書き切ってから終了し、満杯時は掃き出して確実にセンチネルを届ける
            if writer_thread is not None and writer_thread.is_alive():
                try:
                    out_pipe_queue.put(None, timeout=0.5)
                except queue.Full:
                    _force_signal_writer_stop()
                writer_thread.join(timeout=1.5)

            self._actual_end_time_ms = int(time.time() * 1000)
        except Exception as e:
            logging.error(f"[PythonRecorder] Recording loop exception: {e}", exc_info=True)
            self._set_last_error(f"Exception in PythonRecorder loop: {e}")
            if self._startup_state == _STARTUP_INITIALIZING:
                self._startup_state = _STARTUP_FAILED
            self._is_running = False
        finally:
            capture_stop.set()
            _join_threads_within_budget(workers, 0.40)

            # record()ブロック残留時でもサイレント再生スレッドを必ず停止し、未回収スレッドを追跡してWASAPI競合を防ぐ
            still_alive_workers = [w for w in workers if w.is_alive()]
            for w in workers:
                w.stop_silence_keepalive()
            aux_threads: list[threading.Thread] = []
            for w in workers:
                aux_threads.extend(w.get_auxiliary_threads())
            if still_alive_workers or aux_threads:
                _join_threads_within_budget(still_alive_workers + aux_threads, 0.30)
            lingering = [t for t in (still_alive_workers + aux_threads) if t is not None and t.is_alive()]
            if lingering:
                with self._lingering_lock:
                    self._lingering_threads.extend(lingering)

            if writer_thread is not None and writer_thread.is_alive():
                _force_signal_writer_stop()
                writer_thread.join(timeout=0.5)

            if proc is not None:
                # 独立-filter_complex + -shortest構成により、pipe:0のEOF(stdin.close)のみでMKV末尾まで綺麗に終了できる
                if (writer_thread is None or not writer_thread.is_alive()) and proc.stdin is not None:
                    try:
                        proc.stdin.close()
                    except OSError:
                        pass
                if proc.poll() is None:
                    try:
                        proc.wait(timeout=3.0)
                    except subprocess.TimeoutExpired:
                        self._log("Warning: FFmpeg process did not exit on pipe EOF in time. Terminating...")
                        try:
                            proc.terminate()
                            proc.wait(timeout=1.0)
                        except Exception:
                            try:
                                proc.kill()
                                proc.wait(timeout=1.0)
                            except Exception:
                                pass
                if writer_thread is not None and writer_thread.is_alive():
                    writer_thread.join(timeout=0.5)
                if proc.stdin is not None:
                    try:
                        proc.stdin.close()
                    except OSError:
                        pass
                # ログ欠損を防ぐため、プロセス終了後にlog_threadがEOFまで書き切るのを待ってからstdoutを閉じる
                if log_thread is not None and log_thread.is_alive():
                    log_thread.join(timeout=1.5)
                if proc.stdout is not None:
                    try:
                        proc.stdout.close()
                    except OSError:
                        pass

            with self._process_lock:
                if self._session_id == my_session_id:
                    self._active_process = None
            if self._session_id == my_session_id:
                self._is_running = False


_SHARED_PYTHON_BACKEND: Optional[PythonRecorderBackend] = None
_SHARED_BACKEND_LOCK = threading.Lock()


def get_python_recorder_backend() -> PythonRecorderBackend:
    """プロセス内で状態を共有するPythonRecorderBackendインスタンスを返す"""
    global _SHARED_PYTHON_BACKEND
    with _SHARED_BACKEND_LOCK:
        if _SHARED_PYTHON_BACKEND is None:
            _SHARED_PYTHON_BACKEND = PythonRecorderBackend()
        return _SHARED_PYTHON_BACKEND
