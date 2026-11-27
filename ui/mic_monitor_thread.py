"""Module: @role: マイク音声のキャプチャ・DSP適用・ノイズゲート・レベル計測および試聴出力を担うQThreadモジュール"""

import threading
from typing import Any, Optional

import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

from ui.audio_monitor_common import (
    get_soundcard_lock,
    init_com_apartment,
    log_monitor_exception,
    log_thread_timeout,
    suppress_soundcard_warnings,
    uninit_com_apartment,
)
from ui.audio_monitor_dsp import MicMonitorDspPipeline

MIC_SAMPLE_RATE = 48000
MIC_RECORD_FRAMES = 2400
MAX_GATE_HOLD_FRAMES = 10  # 50ms * 10 = 500ms

def _find_mic_device(sc_module: Any, sc_lock: Any, mic_name: str) -> Any:
    if not mic_name or mic_name == "None":
        return None
    with sc_lock:
        if mic_name.lower() == "default":
            return sc_module.default_microphone()
        mics = sc_module.all_microphones(include_loopback=False)
        for m in mics:
            if mic_name == m.name:
                return m
        for m in mics:
            if mic_name in m.name:
                return m
    return None

def _open_mic_recorder(sc_lock: Any, mic_device: Any) -> Any:
    # soundcardは__enter__時にWASAPI初期化を行うため、ロック内で__enter__まで試行してチャンネルを決定する
    for ch in (2, 1, None):
        with sc_lock:
            try:
                cand = (
                    mic_device.recorder(samplerate=MIC_SAMPLE_RATE, channels=ch)
                    if ch is not None
                    else mic_device.recorder(samplerate=MIC_SAMPLE_RATE)
                )
                cand.__enter__()
                return cand
            except Exception:
                pass
    return None

def _normalize_mic_channels(data: np.ndarray) -> np.ndarray:
    if data.ndim == 1:
        return data.reshape(-1, 1)
    if data.ndim == 2 and data.shape[1] == 2:
        # 片chのみ入力があるUSBマイク・オーディオIF等で音量が半減するのを防ぐ
        peak_l = float(np.max(np.abs(data[:, 0])))
        peak_r = float(np.max(np.abs(data[:, 1])))
        if peak_l > 1e-4 and peak_r < peak_l * 0.05:
            return data[:, 0:1]
        if peak_r > 1e-4 and peak_l < peak_r * 0.05:
            return data[:, 1:2]
        return data.mean(axis=1, keepdims=True)
    if data.ndim == 2 and data.shape[1] > 2:
        return data.mean(axis=1, keepdims=True)
    return data

def _close_context_quietly(ctx: Any) -> None:
    if ctx is None:
        return
    try:
        ctx.__exit__(None, None, None)
    except Exception:
        pass

class MicMonitorThread(QThread):
    level_ready = pyqtSignal(float)

    def __init__(
        self,
        mic_name: str,
        gain: float,
        denoise_mode: str = "None",
        gate_threshold: float = 0.0,
        preprocess_mode: str = "SpeexDSP",
    ) -> None:
        super().__init__()
        self.mic_name = mic_name
        self.gain = gain
        self.denoise_mode = denoise_mode
        self.preprocess_mode = preprocess_mode
        self.gate_threshold = gate_threshold
        self.monitor_audio = False
        self.running = True
        self._stop_event = threading.Event()
        self._dsp_pipeline = MicMonitorDspPipeline(
            sample_rate=MIC_SAMPLE_RATE,
            stop_event=self._stop_event,
            is_running_cb=lambda: self.running,
        )
        self.noise_floor = 0.01
        self.gate_open = False
        self.current_gate_gain = 1.0
        self.current_rnnoise_gain = 1.0
        self.current_limiter_gain = 1.0
        self.gate_hold_frames = 0
        self.MAX_HOLD_FRAMES = MAX_GATE_HOLD_FRAMES

    def set_gain(self, gain: float) -> None:
        self.gain = gain

    def set_denoise(self, denoise_mode: str) -> None:
        self.denoise_mode = denoise_mode

    def set_preprocess(self, preprocess_mode: str) -> None:
        self.preprocess_mode = preprocess_mode

    def set_gate_threshold(self, threshold: float) -> None:
        self.gate_threshold = threshold

    def set_monitor_audio(self, monitor: bool) -> None:
        self.monitor_audio = monitor

    def process_audio(
        self, data: Optional[np.ndarray], apply_gain: bool = True
    ) -> Optional[np.ndarray]:
        if data is None or len(data) == 0:
            self.level_ready.emit(0.0)
            return data
        data = np.nan_to_num(data, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32, copy=False)
        if apply_gain:
            data = data * self.gain

        peak = float(np.max(np.abs(data)))
        level = min(1.0, peak ** 0.5) if np.isfinite(peak) else 0.0
        self.level_ready.emit(float(level))

        if self.gate_threshold > 0:
            amp_threshold = (self.gate_threshold / 2.0) ** 2
            rms = np.sqrt(np.mean(data ** 2) + 1e-8)

            if rms > amp_threshold:
                self.gate_open = True
                self.gate_hold_frames = self.MAX_HOLD_FRAMES
            else:
                if self.gate_hold_frames > 0:
                    self.gate_hold_frames -= 1
                else:
                    self.gate_open = False

            target_gain = 1.0 if self.gate_open else 0.01

            # ゲインを滑らかに適用してプツプツ音を防止
            if self.current_gate_gain != target_gain:
                gains = np.linspace(
                    self.current_gate_gain, target_gain, len(data), dtype=np.float32
                ).reshape(-1, 1)
                data = data * gains
                self.current_gate_gain = target_gain
            else:
                data = data * target_gain

        # 過大入力時の音割れを防ぐソフトリミッター
        peak = np.max(np.abs(data))
        if peak > 0.99:
            data = data * (0.99 / peak)

        return data

    def _emit_idle_loop(self) -> None:
        while self.running:
            self.level_ready.emit(0.0)
            self.msleep(50)

    def _run_monitor_loop(self, sc_module: Any, sc_lock: Any, recorder: Any) -> None:
        active_player = None
        player_channels = 2
        try:
            while self.running:
                data = recorder.record(numframes=MIC_RECORD_FRAMES)
                if not self.running:
                    break
                if data is None or len(data) == 0:
                    self.level_ready.emit(0.0)
                    continue
                data = _normalize_mic_channels(data)
                # 録画側(DownmixToStereo->AudioProcessor)と順序を統一し、歪み防止はC++ソフトクリップへ委譲
                data = np.nan_to_num(data, nan=0.0, posinf=0.0, neginf=0.0).astype(
                    np.float32, copy=False
                )
                data = data * np.float32(self.gain)
                data = self._dsp_pipeline.process(data, self.denoise_mode, self.preprocess_mode)
                processed = self.process_audio(data, apply_gain=False)

                if self.monitor_audio:
                    if active_player is None:
                        for out_ch in (2, 1):
                            try:
                                with sc_lock:
                                    speaker = sc_module.default_speaker()
                                    cand_player = speaker.player(
                                        samplerate=MIC_SAMPLE_RATE, channels=out_ch
                                    )
                                    cand_player.__enter__()
                                    active_player = cand_player
                                    player_channels = out_ch
                                    break
                            except Exception:
                                active_player = None
                    if active_player is not None and processed is not None:
                        try:
                            if processed.ndim == 1:
                                processed = processed.reshape(-1, 1)
                            out_audio = (
                                np.repeat(processed, 2, axis=1)
                                if player_channels == 2
                                else processed
                            )
                            active_player.play(np.ascontiguousarray(out_audio, dtype=np.float32))
                        except Exception:
                            _close_context_quietly(active_player)
                            active_player = None
                elif active_player is not None:
                    _close_context_quietly(active_player)
                    active_player = None
        finally:
            try:
                self._dsp_pipeline.close(release_native=True)
            except Exception:
                pass
            _close_context_quietly(active_player)
            _close_context_quietly(recorder)

    def run(self) -> None:
        suppress_soundcard_warnings()
        com_initialized = init_com_apartment()
        try:
            import soundcard as sc

            suppress_soundcard_warnings(sc)
            sc_lock = get_soundcard_lock(sc)
            mic_device = _find_mic_device(sc, sc_lock, self.mic_name)

            if mic_device is None:
                self._emit_idle_loop()
                return

            recorder = _open_mic_recorder(sc_lock, mic_device)
            if recorder is None:
                print(f"[MicMonitorThread] Failed to initialize recorder for device: {self.mic_name}")
                self._emit_idle_loop()
                return

            self._run_monitor_loop(sc, sc_lock, recorder)
        except Exception:
            log_monitor_exception("MicMonitorThread", "mic_error.log")
        finally:
            uninit_com_apartment(com_initialized)

    def stop(self) -> None:
        self.running = False
        self._stop_event.set()
        self._dsp_pipeline.abort()
        if not self.wait(400):
            log_thread_timeout("MicMonitorThread")
