"""Module: @role: soundcard(WASAPI)音声キャプチャワーカーおよびOSプロセス優先度・スレッド回収ユーティリティを提供するモジュール"""
import ctypes
import sys
import threading
import time
from typing import Any, Callable, Optional, Sequence
import numpy as np

from recorder.python_audio_dsp import AudioFrameQueue, FRAMES_PER_CHUNK, SAMPLE_RATE, downmix_to_stereo

BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
NORMAL_PRIORITY_CLASS = 0x00000020
PROCESS_SET_INFORMATION = 0x0200

# soundcard標準ビルドに_lockが無い環境でも全ワーカー間で確実にWASAPI初期化を排他する共通ロック
_SOUNDCARD_INIT_LOCK = threading.RLock()

def get_soundcard_lock(sc_module: Any) -> Any:
    return getattr(sc_module, "_lock", None) or _SOUNDCARD_INIT_LOCK

def set_process_priority_windows(pid: int, low_priority: bool) -> None:
    if sys.platform != "win32" or not pid:
        return
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_SET_INFORMATION, False, int(pid))
        if not handle:
            return
        try:
            prio = BELOW_NORMAL_PRIORITY_CLASS if low_priority else NORMAL_PRIORITY_CLASS
            kernel32.SetPriorityClass(handle, prio)
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        pass

def join_threads_within_budget(threads: Sequence[Optional[threading.Thread]], total_timeout: float) -> None:
    """複数スレッドの直列待ち超過を防ぎ、全体予算内で並列回収する"""
    deadline = time.monotonic() + max(0.0, total_timeout)
    for t in threads:
        if t is not None and t.is_alive():
            rem = deadline - time.monotonic()
            if rem <= 0.0:
                break
            t.join(timeout=rem)

class SilenceKeepAliveThread(threading.Thread):
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
                            self._speaker.player(samplerate=SAMPLE_RATE, channels=ch, blocksize=FRAMES_PER_CHUNK)
                            if ch is not None
                            else self._speaker.player(samplerate=SAMPLE_RATE, blocksize=FRAMES_PER_CHUNK)
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
            silent_block = np.zeros((FRAMES_PER_CHUNK, channels), dtype=np.float32)
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

class CaptureWorker(threading.Thread):
    """soundcard (WASAPI) で1系統の音声ストリームを非同期キャプチャするワーカースレッド"""

    def __init__(
        self,
        role: str,
        device_name: str,
        gain: float,
        allow_fallback: bool,
        out_queue: AudioFrameQueue,
        stop_event: threading.Event,
        log_fn: Callable[[str], None],
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
        self._silence_thread: Optional[SilenceKeepAliveThread] = None

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
            sc_lock = get_soundcard_lock(sc)

            with sc_lock:
                dev, speaker = self._resolve_device(sc)
                if dev is not None:
                    for ch in (2, 1, None):
                        try:
                            cand = (
                                dev.recorder(samplerate=SAMPLE_RATE, channels=ch, blocksize=FRAMES_PER_CHUNK)
                                if ch is not None
                                else dev.recorder(samplerate=SAMPLE_RATE, blocksize=FRAMES_PER_CHUNK)
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
                self._silence_thread = SilenceKeepAliveThread(speaker, self._silence_stop, sc_lock)
                self._silence_thread.start()
                self._silence_thread.started_event.wait(timeout=1.0)
                self.has_silence_keepalive = self._silence_thread.is_active

            self.is_active = True
            self.init_done.set()
            fix_mono_mic = self.role == "mic"

            while not self.stop_event.is_set():
                data = recorder.record(numframes=FRAMES_PER_CHUNK)
                if self.stop_event.is_set():
                    break
                if data is None or len(data) == 0:
                    continue
                stereo = downmix_to_stereo(data, self.gain, fix_mono_sided_mic=fix_mono_mic)
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
