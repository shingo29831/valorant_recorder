"""Module: @role: Pure Python録画セッションのFFmpeg起動・音声ミキシングパイプ・A/V同期ループ・後処理を担うモジュール"""
from dataclasses import dataclass
from enum import IntEnum
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Optional
import numpy as np

from recorder.python_audio_dsp import (
    AudioFrameQueue,
    FRAMES_PER_CHUNK,
    HARD_MAX_CHUNKS,
    PREBUFFER_CHUNKS,
    PythonNoiseGate,
    SAMPLE_RATE,
    SOFT_MAX_CHUNKS,
    SoftLimiter,
)
from recorder.python_capture_workers import (
    BELOW_NORMAL_PRIORITY_CLASS,
    CaptureWorker,
    NORMAL_PRIORITY_CLASS,
    join_threads_within_budget,
)

CAPTURE_STARTED_MARKERS = ("Stream mapping:", "Press [q]", "Output #0", "frame=")

class StartupState(IntEnum):
    IDLE = 0
    INITIALIZING = 1
    RUNNING = 2
    FAILED = 3

@dataclass
class RecordingSessionConfig:
    sys_device: str
    mic_device: str
    chat_device: str
    save_file_path: str
    ffmpeg_cmd: str
    system_gain: float
    mic_gain: float
    chat_gain: float
    noise_gate_threshold: float
    denoise_mode: int
    preprocess_mode: int
    low_priority: bool
    chat_enabled: bool

@dataclass
class SessionCallbacks:
    is_running: Callable[[], bool]
    request_stop: Callable[[], None]
    get_startup_state: Callable[[], StartupState]
    set_startup_state: Callable[[StartupState], None]
    set_actual_start_time_ms: Callable[[int], None]
    set_actual_end_time_ms: Callable[[int], None]
    set_active_process: Callable[[Optional[subprocess.Popen]], None]
    clear_active_process: Callable[[], None]
    add_lingering_threads: Callable[[list[threading.Thread]], None]
    log: Callable[[str], None]
    set_last_error: Callable[[str], None]

def init_optional_audio_processor(
    denoise_mode: int,
    preprocess_mode: int,
    log_fn: Callable[[str], None],
) -> Any:
    if denoise_mode <= 0 and preprocess_mode <= 0:
        return None
    try:
        from recorder.audio_processor_wrapper import AudioProcessorWrapper

        if not AudioProcessorWrapper.is_available():
            return None
        proc = AudioProcessorWrapper(sample_rate=SAMPLE_RATE, channels=2)
        proc.set_preprocess_type("SpeexDSP" if preprocess_mode == 1 else "None")
        if denoise_mode == 1:
            proc.set_denoise_type("DeepFilterNet")
        elif denoise_mode == 2:
            proc.set_denoise_type("RNNoise")
        else:
            proc.set_denoise_type("None")
        return proc
    except Exception as e:
        log_fn(f"Optional AudioProcessorWrapper skipped: {e}")
        return None

def force_signal_writer_stop(
    writer_stop: threading.Event,
    out_pipe_queue: queue.Queue[Optional[bytes]],
) -> None:
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

class PythonRecordingSession:
    """単一録画セッションのA/V同期パイプライン実行と確実なリソース解放を担当する"""

    def __init__(self, cfg: RecordingSessionConfig, callbacks: SessionCallbacks) -> None:
        self._cfg = cfg
        self._cb = callbacks

    def _cleanup(
        self,
        workers: list[CaptureWorker],
        capture_stop: threading.Event,
        writer_thread: Optional[threading.Thread],
        log_thread: Optional[threading.Thread],
        proc: Optional[subprocess.Popen],
        writer_stop: threading.Event,
        out_pipe_queue: queue.Queue[Optional[bytes]],
    ) -> None:
        """try内複数returnによるNuitkaのfinally複製肥大化・一時変数衝突を防ぐため専用メソッドへ分離"""
        capture_stop.set()
        join_threads_within_budget(workers, 0.40)

        # 停止シグナルで終了するaux_threadsを先に回収し、ハング中のworkerによる予算枯渇を防ぐ
        pending_threads: list[threading.Thread] = []
        for w in workers:
            w.stop_silence_keepalive()
            pending_threads.extend(w.get_auxiliary_threads())
        for w in workers:
            if w.is_alive():
                pending_threads.append(w)
        if pending_threads:
            join_threads_within_budget(pending_threads, 0.30)
        lingering = [t for t in pending_threads if t.is_alive()]
        if lingering:
            self._cb.add_lingering_threads(lingering)

        if writer_thread is not None and writer_thread.is_alive():
            force_signal_writer_stop(writer_stop, out_pipe_queue)
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
                    self._cb.log("Warning: FFmpeg process did not exit on pipe EOF in time. Terminating...")
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

        self._cb.clear_active_process()
        self._cb.request_stop()

    def run(self) -> None:
        cfg = self._cfg
        cb = self._cb
        proc: Optional[subprocess.Popen] = None
        log_thread: Optional[threading.Thread] = None
        writer_thread: Optional[threading.Thread] = None
        capture_stop = threading.Event()
        writer_stop = threading.Event()
        workers: list[CaptureWorker] = []
        # asetpts=N/SR/TBのため出力音声は破棄せず10秒分の十分なキューでバックプレッシャー制御する
        out_pipe_queue: queue.Queue[Optional[bytes]] = queue.Queue(maxsize=500)

        try:
            chat_enabled = cfg.chat_enabled
            out_channels = 6 if chat_enabled else 4
            save_file_path = cfg.save_file_path
            save_dir = os.path.dirname(save_file_path) if save_file_path else "."
            ffmpeg_log_path = os.path.join(save_dir or ".", "native_ffmpeg.log")

            sys_queue = AudioFrameQueue(max_delay_ms=2000)
            mic_queue = AudioFrameQueue(max_delay_ms=2000)
            chat_queue = AudioFrameQueue(max_delay_ms=2000)

            sys_worker: Optional[CaptureWorker] = None
            mic_worker: Optional[CaptureWorker] = None
            chat_worker: Optional[CaptureWorker] = None

            if cfg.sys_device != "__NONE__":
                sys_worker = CaptureWorker(
                    "system", cfg.sys_device, cfg.system_gain, True, sys_queue, capture_stop, cb.log
                )
                workers.append(sys_worker)
            if cfg.mic_device != "__NONE__":
                mic_worker = CaptureWorker(
                    "mic", cfg.mic_device, cfg.mic_gain, True, mic_queue, capture_stop, cb.log
                )
                workers.append(mic_worker)
            if chat_enabled and cfg.chat_device != "__NONE__":
                chat_allow_fb = cfg.chat_device.strip().lower() in ("", "default")
                chat_worker = CaptureWorker(
                    "chat", cfg.chat_device, cfg.chat_gain, chat_allow_fb, chat_queue, capture_stop, cb.log
                )
                workers.append(chat_worker)

            audio_proc = None
            df_needs_activation = False
            if mic_worker is not None:
                audio_proc = init_optional_audio_processor(cfg.denoise_mode, cfg.preprocess_mode, cb.log)
                if audio_proc is not None and cfg.denoise_mode == 1:
                    df_needs_activation = not audio_proc.try_activate_deepfilter_if_ready()

            creationflags = 0
            startupinfo = None
            if sys.platform == "win32":
                prio_flag = BELOW_NORMAL_PRIORITY_CLASS if cfg.low_priority else NORMAL_PRIORITY_CLASS
                creationflags = subprocess.CREATE_NO_WINDOW | prio_flag
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE

            cb.log(f"Executing FFmpeg command: {cfg.ffmpeg_cmd}")
            try:
                proc = subprocess.Popen(
                    cfg.ffmpeg_cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=0,
                    creationflags=creationflags,
                    startupinfo=startupinfo,
                )
            except Exception as e:
                cb.set_last_error(f"Failed to start FFmpeg process: {e}")
                cb.set_startup_state(StartupState.FAILED)
                cb.request_stop()
                return

            cb.set_active_process(proc)

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
                                if any(m in acc for m in CAPTURE_STARTED_MARKERS):
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
                cb.set_last_error(f"FFmpeg process crashed immediately with exit code {proc.returncode}")
                cb.set_startup_state(StartupState.FAILED)
                cb.request_stop()
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

            prebuffer_frames = FRAMES_PER_CHUNK * PREBUFFER_CHUNKS
            prebuf_deadline = time.monotonic() + 0.5
            while cb.is_running() and proc.poll() is None and time.monotonic() < prebuf_deadline:
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
                            cb.log(f"FFmpeg stdin write closed: {write_err}")
                            cb.request_stop()
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
                        if not cb.is_running():
                            return False
                return False

            # pipe:0プローブ解放用の1チャンク(20ms)を投入し、サンプル総数へ厳密に計上する
            _enqueue_pipe(np.zeros((FRAMES_PER_CHUNK, out_channels), dtype=np.float32).tobytes())
            total_written_frames = FRAMES_PER_CHUNK

            sync_wait_start = time.monotonic()
            while cb.is_running():
                if proc.poll() is not None:
                    cb.set_last_error(f"FFmpeg process exited during initialization with code {proc.returncode}")
                    cb.set_startup_state(StartupState.FAILED)
                    cb.request_stop()
                    break
                if capture_started.is_set() or (time.monotonic() - sync_wait_start) >= 3.0:
                    break
                time.sleep(0.005)

            if cb.is_running() and proc.poll() is None:
                cb.set_startup_state(StartupState.RUNNING)
            else:
                if cb.get_startup_state() == StartupState.INITIALIZING:
                    cb.set_startup_state(StartupState.FAILED)
                cb.request_stop()
                return

            # 60msのジッター吸収帯を残してアラインし、映像開始時刻から先行20ms分を相殺してA/V同期を固定
            sys_queue.drain_keep_latest(prebuffer_frames)
            mic_queue.drain_keep_latest(prebuffer_frames)
            if chat_enabled:
                chat_queue.drain_keep_latest(prebuffer_frames)

            ref_perf = capture_started_perf[0] if capture_started_perf[0] > 0.0 else time.perf_counter()
            start_perf = ref_perf - (total_written_frames / float(SAMPLE_RATE))
            cb.set_actual_start_time_ms(int(time.time() * 1000))

            soft_max_frames = FRAMES_PER_CHUNK * SOFT_MAX_CHUNKS
            hard_max_frames = FRAMES_PER_CHUNK * HARD_MAX_CHUNKS

            mic_gate = PythonNoiseGate(cfg.noise_gate_threshold)
            sys_limiter = SoftLimiter()
            mic_limiter = SoftLimiter()
            chat_limiter = SoftLimiter()
            silent_chunk = np.zeros((FRAMES_PER_CHUNK, 2), dtype=np.float32)
            chunks_since_df_check = 0

            def _emit_chunk(drain_mode: bool) -> bool:
                nonlocal total_written_frames, df_needs_activation, chunks_since_df_check
                if has_active_sys:
                    sys_buf = (
                        sys_queue.pop_exact(FRAMES_PER_CHUNK)
                        if drain_mode
                        else sys_queue.pop_chunk(FRAMES_PER_CHUNK, soft_max_frames, hard_max_frames, prebuffer_frames)
                    )
                    sys_out = sys_limiter.process(sys_buf)
                else:
                    sys_out = silent_chunk

                if has_active_mic:
                    mic_buf = (
                        mic_queue.pop_exact(FRAMES_PER_CHUNK)
                        if drain_mode
                        else mic_queue.pop_chunk(FRAMES_PER_CHUNK, soft_max_frames, hard_max_frames, prebuffer_frames)
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
                            chat_queue.pop_exact(FRAMES_PER_CHUNK)
                            if drain_mode
                            else chat_queue.pop_chunk(FRAMES_PER_CHUNK, soft_max_frames, hard_max_frames, prebuffer_frames)
                        )
                        chat_out = chat_limiter.process(chat_buf)
                    else:
                        chat_out = silent_chunk
                    combined = np.concatenate((sys_out, mic_out, chat_out), axis=1)
                else:
                    combined = np.concatenate((sys_out, mic_out), axis=1)

                if not _enqueue_pipe(np.ascontiguousarray(combined, dtype=np.float32).tobytes()):
                    return False
                total_written_frames += FRAMES_PER_CHUNK
                return True

            cb.log("Entering main Python recording loop (A/V Aligned 48kHz Pipeline)...")
            sleep_sec = 0.006 if cfg.low_priority else 0.003

            while cb.is_running():
                if proc.poll() is not None:
                    cb.set_last_error(f"FFmpeg process terminated unexpectedly with code {proc.returncode}")
                    cb.request_stop()
                    break

                # start_perfは巻き戻さず経過時間に一致する総サンプル数を欠落なく供給する
                target_frames = int((time.perf_counter() - start_perf) * SAMPLE_RATE)
                burst = 0
                while cb.is_running() and (total_written_frames + FRAMES_PER_CHUNK) <= target_frames and burst < 50:
                    if not _emit_chunk(False):
                        break
                    burst += 1

                if burst < 50:
                    time.sleep(sleep_sec)

            # 正常停止時のみ最大200msの末尾追従と残余キューのドレインを実施
            if writer_alive and proc.poll() is None:
                tail_deadline = time.perf_counter() + 0.20
                while writer_alive and proc.poll() is None and time.perf_counter() < tail_deadline:
                    target_frames = int((time.perf_counter() - start_perf) * SAMPLE_RATE)
                    while (total_written_frames + FRAMES_PER_CHUNK) <= target_frames:
                        if not _emit_chunk(False):
                            break
                    time.sleep(0.003)

            capture_stop.set()
            join_threads_within_budget(workers, 0.50)

            if writer_alive and proc.poll() is None:
                rem_frames = max(sys_queue.available_frames(), mic_queue.available_frames())
                if chat_enabled:
                    rem_frames = max(rem_frames, chat_queue.available_frames())
                drain_chunks = min(20, max(5, rem_frames // FRAMES_PER_CHUNK + 1))
                for _ in range(drain_chunks):
                    if not _emit_chunk(True):
                        break

            # 正常時はキュー残余を書き切ってから終了し、満杯時は掃き出して確実にセンチネルを届ける
            if writer_thread is not None and writer_thread.is_alive():
                try:
                    out_pipe_queue.put(None, timeout=0.5)
                except queue.Full:
                    force_signal_writer_stop(writer_stop, out_pipe_queue)
                writer_thread.join(timeout=1.5)

            cb.set_actual_end_time_ms(int(time.time() * 1000))
        except Exception as e:
            logging.error(f"[PythonRecorder] Recording loop exception: {e}", exc_info=True)
            cb.set_last_error(f"Exception in PythonRecorder loop: {e}")
            if cb.get_startup_state() == StartupState.INITIALIZING:
                cb.set_startup_state(StartupState.FAILED)
            cb.request_stop()
        finally:
            self._cleanup(
                workers=workers,
                capture_stop=capture_stop,
                writer_thread=writer_thread,
                log_thread=log_thread,
                proc=proc,
                writer_stop=writer_stop,
                out_pipe_queue=out_pipe_queue,
            )
