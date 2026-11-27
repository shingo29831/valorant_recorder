"""Module: @role: Pure Python録画用の音声DSP処理・ステレオ変換・ジッター吸収フレームキューを提供するモジュール"""
from collections import deque
import threading
import numpy as np

SAMPLE_RATE = 48000
FRAMES_PER_CHUNK = 960  # 20ms @ 48kHz
PREBUFFER_CHUNKS = 3  # 60msのジッター吸収帯を確保してアンダーラン無音を防止
SOFT_MAX_CHUNKS = 6  # 120ms超過から連続リサンプリングによる滑らかなドリフト補正を開始
HARD_MAX_CHUNKS = 15  # 300ms超過時のみ安全弁トリム＋クロスフェード
DRIFT_RESAMPLE_EXTRA_MAX = 24  # 2.5%以内の連続時間圧縮で波形不連続クリックを完全排除
UNDERFLOW_STRETCH_MIN_RATIO = 0.85  # 端数残留(816〜959サンプル)を滑らか補間して20ms無音挿入を防止

def downmix_to_stereo(data: np.ndarray, gain: float, fix_mono_sided_mic: bool = False) -> np.ndarray:
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

def resample_stereo_continuous(src: np.ndarray, target_len: int, prev_sample: np.ndarray) -> np.ndarray:
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

def apply_boundary_crossfade(prev_sample: np.ndarray, chunk: np.ndarray, fade_len: int = 64) -> np.ndarray:
    n = min(fade_len, chunk.shape[0])
    if n <= 0:
        return chunk
    out = chunk.copy()
    w = np.linspace(0.0, 1.0, n, dtype=np.float32).reshape(-1, 1)
    out[:n] = prev_sample * (1.0 - w) + out[:n] * w
    return out

class AudioFrameQueue:
    """連続リサンプリングによるドリフト追従とA/V初期アラインメントを行うスレッドセーフ音声キュー"""

    def __init__(self, max_delay_ms: int = 2000) -> None:
        self._lock = threading.Lock()
        self._chunks: deque[np.ndarray] = deque()
        self._total_frames: int = 0
        self._max_frames: int = (SAMPLE_RATE * max_delay_ms) // 1000
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
                out = apply_boundary_crossfade(prev, out, fade_len=64)
            elif self._total_frames > soft_max_frames:
                # 生PCMの切り落としによる不連続ノイズを防ぎ、最大2.5%の連続リサンプリングで滑らかに追従
                excess = self._total_frames - target_keep_frames
                extra = min(excess, DRIFT_RESAMPLE_EXTRA_MAX, self._total_frames - num_frames)
                src = self._take_locked(num_frames + max(0, extra))
                out = resample_stereo_continuous(src, num_frames, prev)
            elif self._total_frames >= num_frames:
                out = self._take_locked(num_frames)
            elif self._total_frames >= int(num_frames * UNDERFLOW_STRETCH_MIN_RATIO):
                # 端数残留(例:959サンプル)による20ms無音挿入と滞留を防ぎ滑らかに補間伸長する
                src = self._take_locked(self._total_frames)
                out = resample_stereo_continuous(src, num_frames, prev)
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

class PythonNoiseGate:
    """MicMonitorThread と同一の RMS 閾値・ホールド・スムージングを持つノイズゲート"""

    def __init__(self, threshold_percent: float) -> None:
        norm = max(0.0, min(1.0, float(threshold_percent)))
        self._amp_threshold = 0.0 if norm <= 0.001 else ((norm / 2.0) ** 2)
        self._current_gain = 1.0
        self._gate_open = False
        self._hold_chunks_remaining = 0
        # 20ms * 25 = 500ms のホールド時間で MicMonitorThread と挙動を統一
        self._hold_chunks = max(1, int(round(0.50 * SAMPLE_RATE / FRAMES_PER_CHUNK)))

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

class SoftLimiter:
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
