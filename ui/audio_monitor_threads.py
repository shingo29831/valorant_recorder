"""Module: @role: システム音声レベル監視スレッドおよび音声モニター関連クラスの公開ファサードを提供するモジュール"""

from typing import Any, Optional

import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

from ui.audio_monitor_common import (
    _SOUNDCARD_LOCK,
    get_soundcard_lock as _get_soundcard_lock,
    init_com_apartment,
    log_monitor_exception,
    log_thread_timeout,
    suppress_soundcard_warnings,
    uninit_com_apartment,
)
from ui.audio_monitor_dsp import (
    FFmpegMicMonitorDsp,
    MicMonitorDspPipeline,
    NumpyHighPassFilter,
    _FFmpegMicMonitorDsp,
    _NumpyHighPassFilter,
)
from ui.mic_monitor_thread import MicMonitorThread

SYS_SAMPLE_RATE = 48000
SYS_RECORD_FRAMES = 1200

def _resolve_target_speaker(sc_module: Any, device_name: str, allow_default_fallback: bool) -> Any:
    speaker = None
    if device_name and device_name not in ("default", "Default", "None"):
        all_spks = sc_module.all_speakers()
        target_lower = device_name.lower()
        for s in all_spks:
            if s.name.lower() == target_lower:
                speaker = s
                break
        # チャット分離等のフォールバック抑止時は別デバイスへの誤爆を防ぐため部分一致を行わない
        if speaker is None and allow_default_fallback:
            for s in all_spks:
                if target_lower in s.name.lower():
                    speaker = s
                    break
    if speaker is None and (allow_default_fallback or device_name in ("", "default", "Default")):
        speaker = sc_module.default_speaker()
    return speaker


class SystemAudioMonitorThread(QThread):
    level_ready = pyqtSignal(float)

    def __init__(
        self,
        gain: float,
        device_name: str = "default",
        allow_default_fallback: bool = True,
    ) -> None:
        super().__init__()
        self.gain = gain
        self.device_name = device_name
        self.allow_default_fallback = allow_default_fallback
        self.running = True
        self.current_limiter_gain = 1.0

    def set_gain(self, gain: float) -> None:
        self.gain = gain

    def process_audio(self, data: Optional[np.ndarray]) -> Optional[np.ndarray]:
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

    def run(self) -> None:
        suppress_soundcard_warnings()
        com_initialized = init_com_apartment()
        try:
            import soundcard as sc

            suppress_soundcard_warnings(sc)
            sc_lock = _get_soundcard_lock(sc)

            with sc_lock:
                speaker = _resolve_target_speaker(
                    sc, self.device_name, self.allow_default_fallback
                )
                recorder = None
                if speaker is not None:
                    spk_mic = sc.get_microphone(speaker.id, include_loopback=True)
                    for ch in (2, 1):
                        try:
                            cand = spk_mic.recorder(samplerate=SYS_SAMPLE_RATE, channels=ch)
                            cand.__enter__()
                            recorder = cand
                            break
                        except Exception:
                            recorder = None

            if recorder is not None:
                try:
                    while self.running:
                        data = recorder.record(numframes=SYS_RECORD_FRAMES)
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
        except Exception:
            log_monitor_exception("SystemAudioMonitorThread", "sys_audio_error.log")
        finally:
            uninit_com_apartment(com_initialized)

    def stop(self) -> None:
        self.running = False
        if not self.wait(300):
            log_thread_timeout("SystemAudioMonitorThread")


__all__ = [
    "FFmpegMicMonitorDsp",
    "MicMonitorDspPipeline",
    "MicMonitorThread",
    "NumpyHighPassFilter",
    "SystemAudioMonitorThread",
    "_FFmpegMicMonitorDsp",
    "_NumpyHighPassFilter",
    "_SOUNDCARD_LOCK",
    "_get_soundcard_lock",
]
