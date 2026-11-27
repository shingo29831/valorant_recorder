"""Module: @role: マイクモニター用のリアルタイムDSP（NumPy HPF・FFmpegパイプDSP・ネイティブ/フォールバック制御）を担うモジュール"""

import logging
import math
import os
import subprocess
import threading
import time
from typing import Callable, Optional

import numpy as np

from core.config import get_app_data_dir

DEFAULT_HPF_CUTOFF_HZ = 80.0
DEFAULT_HPF_BLOCK_LEN = 120
MAX_FFMPEG_OUT_BUF_BYTES = 64 * 1024

class NumpyHighPassFilter:
    """audio_processor.dll不可時のゼロ遅延80Hzハイパスフィルタ（1次厳密解・ブロックベクトル化）"""

    def __init__(self, sample_rate: int = 48000, cutoff_hz: float = DEFAULT_HPF_CUTOFF_HZ) -> None:
        self._alpha = math.exp(-2.0 * math.pi * cutoff_hz / float(sample_rate))
        self._lp_state = 0.0
        self._block_len = DEFAULT_HPF_BLOCK_LEN
        idx = np.arange(self._block_len, dtype=np.float64)
        self._pows = self._alpha ** idx
        self._inv_pows = self._alpha ** (-idx)
        self._pows_next = self._alpha ** (idx + 1)

    def process(self, mono: Optional[np.ndarray]) -> Optional[np.ndarray]:
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

class FFmpegMicMonitorDsp:
    """audio_processor.dll不可時に実録画と同一のFFmpegフィルタ(highpass+arnndn)を適用するリアルタイムDSP"""

    def __init__(self, sample_rate: int, enable_hpf: bool, enable_arnndn: bool) -> None:
        self._sample_rate = sample_rate
        self._enable_hpf = enable_hpf
        self._enable_arnndn = enable_arnndn
        self._proc: Optional[subprocess.Popen] = None
        self._out_buf = bytearray()
        self._out_cond = threading.Condition()
        self._out_eof = False
        self._reader_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
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
        return (
            (not self._failed)
            and (not self._closed)
            and (self._proc is not None)
            and (self._proc.poll() is None)
        )

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
                "-loglevel",
                "error",
                "-fflags",
                "+nobuffer",
                "-flags",
                "+low_delay",
                "-probesize",
                "32",
                "-analyzeduration",
                "0",
                "-f",
                "f32le",
                "-ar",
                str(self._sample_rate),
                "-ac",
                "1",
                "-i",
                "pipe:0",
                "-af",
                ",".join(filters),
                "-flush_packets",
                "1",
                "-f",
                "f32le",
                "-ar",
                str(self._sample_rate),
                "-ac",
                "1",
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
        try:
            while not self._closed:
                data = proc.stdout.read(4096)
                if not data:
                    break
                with self._out_cond:
                    self._out_buf.extend(data)
                    if len(self._out_buf) > MAX_FFMPEG_OUT_BUF_BYTES:
                        # 4バイト(float32)境界を維持して古い余剰データを破棄
                        trim = ((len(self._out_buf) - MAX_FFMPEG_OUT_BUF_BYTES) + 3) & ~3
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

    def process(
        self, mono: np.ndarray, stop_event: Optional[threading.Event] = None
    ) -> Optional[np.ndarray]:
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

class MicMonitorDspPipeline:
    """マイクモニターのネイティブDSPおよびFFmpeg/NumPyフォールバックDSPの切り替え・破棄を制御する"""

    def __init__(
        self, sample_rate: int, stop_event: threading.Event, is_running_cb: Callable[[], bool]
    ) -> None:
        self._sample_rate = sample_rate
        self._stop_event = stop_event
        self._is_running_cb = is_running_cb
        self._dsp_lock = threading.Lock()
        self._active_ffmpeg_dsp: Optional[FFmpegMicMonitorDsp] = None
        self._processor = None
        self._processor_init_failed = False
        self._last_denoise: Optional[str] = None
        self._last_preprocess: Optional[str] = None
        self._fallback_hpf = NumpyHighPassFilter(sample_rate=sample_rate, cutoff_hz=DEFAULT_HPF_CUTOFF_HZ)
        self._ffmpeg_dsp: Optional[FFmpegMicMonitorDsp] = None
        self._ffmpeg_dsp_key: Optional[tuple[bool, bool]] = None
        self._ffmpeg_dsp_retry_after = 0.0

    def abort(self) -> None:
        with self._dsp_lock:
            active_dsp = self._active_ffmpeg_dsp
        if active_dsp is not None:
            active_dsp.abort()

    def close(self, release_native: bool = False) -> None:
        with self._dsp_lock:
            cur = self._ffmpeg_dsp
            self._ffmpeg_dsp = None
            self._ffmpeg_dsp_key = None
            self._active_ffmpeg_dsp = None
        if cur is not None:
            cur.close()
        if release_native:
            # スレッド終了時にC++側DeepFilterNet/SpeexDSPリソースを即時解放する
            self._processor = None
            self._last_denoise = None
            self._last_preprocess = None

    def _apply_fallback_dsp(
        self, raw_data: np.ndarray, preprocess_target: str, denoise_target: str
    ) -> np.ndarray:
        want_hpf = preprocess_target != "None"
        want_arnndn = denoise_target in ("RNNoise", "DeepFilterNet")
        if not want_arnndn:
            if self._ffmpeg_dsp is not None:
                self.close()
            return self._fallback_hpf.process(raw_data) if want_hpf else raw_data

        desired_key = (want_hpf, want_arnndn)
        if self._ffmpeg_dsp is not None and (
            self._ffmpeg_dsp_key != desired_key or not self._ffmpeg_dsp.is_ready
        ):
            self.close()

        if (
            self._ffmpeg_dsp is None
            and self._is_running_cb()
            and not self._stop_event.is_set()
            and time.monotonic() >= self._ffmpeg_dsp_retry_after
        ):
            cand = FFmpegMicMonitorDsp(self._sample_rate, enable_hpf=want_hpf, enable_arnndn=True)
            if cand.is_ready:
                with self._dsp_lock:
                    if self._stop_event.is_set() or not self._is_running_cb():
                        cand.close()
                    else:
                        self._ffmpeg_dsp = cand
                        self._ffmpeg_dsp_key = desired_key
                        self._active_ffmpeg_dsp = cand
            else:
                pending_probe = cand.pending_probe
                cand.close()
                # 非同期フィルタプローブ完了待ち時は0.4秒、モデルDL待ち時は3秒で再チェック
                self._ffmpeg_dsp_retry_after = time.monotonic() + (0.4 if pending_probe else 3.0)

        if self._ffmpeg_dsp is not None:
            out = self._ffmpeg_dsp.process(raw_data, stop_event=self._stop_event)
            if out is not None:
                return out
            self.close()
            self._ffmpeg_dsp_retry_after = time.monotonic() + 5.0

        return self._fallback_hpf.process(raw_data) if want_hpf else raw_data

    def process(self, raw_data: np.ndarray, denoise_mode: str, preprocess_mode: str) -> np.ndarray:
        denoise_target = (
            "DeepFilterNet"
            if denoise_mode in ("AI (DeepFilterNet)", "DeepFilterNet")
            else "RNNoise"
            if denoise_mode in ("AI (RNNoise)", "RNNoise")
            else "None"
        )
        preprocess_target = "SpeexDSP" if preprocess_mode and preprocess_mode != "None" else "None"
        needs_dsp = (preprocess_target != "None") or (denoise_target != "None")
        if not needs_dsp:
            if self._ffmpeg_dsp is not None:
                self.close()
            return raw_data
        if self._processor_init_failed:
            return self._apply_fallback_dsp(raw_data, preprocess_target, denoise_target)
        if self._processor is None:
            from recorder.audio_processor_wrapper import AudioProcessorWrapper

            if not AudioProcessorWrapper.is_available():
                self._processor_init_failed = True
                return self._apply_fallback_dsp(raw_data, preprocess_target, denoise_target)
            try:
                self._processor = AudioProcessorWrapper(sample_rate=self._sample_rate, channels=1)
            except Exception as e:
                print(f"Failed to initialize AudioProcessorWrapper: {e}")
                self._processor_init_failed = True
                return self._apply_fallback_dsp(raw_data, preprocess_target, denoise_target)
        if self._last_preprocess != preprocess_target:
            self._processor.set_preprocess_type(preprocess_target)
            self._last_preprocess = preprocess_target
        if self._last_denoise != denoise_target:
            self._processor.set_denoise_type(denoise_target)
            self._last_denoise = denoise_target
        elif denoise_target == "DeepFilterNet":
            # 非同期モデル取得完了時にユーザー再操作なしで自動有効化
            self._processor.try_activate_deepfilter_if_ready()
        return self._processor.process(raw_data)

_NumpyHighPassFilter = NumpyHighPassFilter
_FFmpegMicMonitorDsp = FFmpegMicMonitorDsp
