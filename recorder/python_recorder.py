"""Module: @role: C++ネイティブDLL利用不可環境向けにsoundcard+FFmpegパイプで同等機能を提供するPure Python録画バックエンド"""
from datetime import datetime
import os
import subprocess
import threading
from typing import Any, Optional, TextIO

from recorder.python_capture_workers import (
    join_threads_within_budget,
    set_process_priority_windows,
)
from recorder.python_recording_session import (
    PythonRecordingSession,
    RecordingSessionConfig,
    SessionCallbacks,
    StartupState,
)

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

class PythonRecorderBackend:
    """C++ native_recorder.dll と完全互換のPure Python録画バックエンド"""

    def __init__(self) -> None:
        self._recorder_lock = threading.Lock()
        self._process_lock = threading.Lock()
        self._log_lock = threading.Lock()
        self._lingering_lock = threading.Lock()
        self._is_running = False
        self._startup_state: StartupState = StartupState.IDLE
        self._actual_start_time_ms = 0
        self._actual_end_time_ms = 0
        self._last_error = ""
        self._session_id = 0
        self._recording_thread: Optional[threading.Thread] = None
        self._active_process: Optional[subprocess.Popen] = None
        self._lingering_threads: list[threading.Thread] = []
        self._debug_log_file: Optional[TextIO] = None

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
            join_threads_within_budget(pending, timeout)
        with self._lingering_lock:
            self._lingering_threads = [t for t in self._lingering_threads if t.is_alive()]

    def _add_lingering_threads(self, threads: list[threading.Thread]) -> None:
        with self._lingering_lock:
            self._lingering_threads.extend(threads)

    def Recorder_Start(self, config_ptr: Any) -> bool:
        with self._recorder_lock:
            if self._is_running:
                self._set_last_error("Recorder is already running.")
                return False
            if self._recording_thread is not None and self._recording_thread.is_alive():
                self._recording_thread.join(timeout=5.0)
                if self._recording_thread.is_alive():
                    self._set_last_error("Previous recording session is still shutting down.")
                    return False
            # 前セッションの残留WASAPIワーカーがあれば新セッション開始前に回収してデバイス競合を防止
            self._reap_lingering_threads(timeout=1.5)

            cfg = _unwrap_config(config_ptr)
            if cfg is None:
                self._set_last_error("Invalid configuration.")
                return False

            save_file_path = _decode_c_str(getattr(cfg, "save_file_path", b""))
            session_cfg = RecordingSessionConfig(
                sys_device=_decode_c_str(getattr(cfg, "system_device_name", b"")),
                mic_device=_decode_c_str(getattr(cfg, "mic_device_name", b"")),
                chat_device=_decode_c_str(getattr(cfg, "chat_device_name", b"")),
                save_file_path=save_file_path,
                ffmpeg_cmd=_decode_c_str(getattr(cfg, "ffmpeg_command", b"")),
                system_gain=float(getattr(cfg, "system_gain", 1.0)),
                mic_gain=float(getattr(cfg, "mic_gain", 1.0)),
                chat_gain=float(getattr(cfg, "chat_gain", 1.0)),
                noise_gate_threshold=float(getattr(cfg, "noise_gate_threshold", 0.0)),
                denoise_mode=int(getattr(cfg, "denoise_mode", 0)),
                preprocess_mode=int(getattr(cfg, "preprocess_mode", 0)),
                low_priority=bool(getattr(cfg, "low_priority", 0)),
                chat_enabled=bool(getattr(cfg, "chat_enabled", 0)),
            )

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
            self._startup_state = StartupState.INITIALIZING
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
                self._startup_state = StartupState.IDLE
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
            set_process_priority_windows(proc.pid, bool(low_priority))
            self._log(f"Updated FFmpeg process priority to {'BELOW_NORMAL' if low_priority else 'NORMAL'}")

    def Recorder_GetLastError(self) -> bytes:
        return self._last_error.encode("utf-8", errors="replace")

    def Recorder_GetActualStartTimeMs(self) -> int:
        return int(self._actual_start_time_ms)

    def Recorder_GetActualEndTimeMs(self) -> int:
        return int(self._actual_end_time_ms)

    def Recorder_SupportsChatTrack(self) -> bool:
        return True

    def _build_session_callbacks(self, my_session_id: int) -> SessionCallbacks:
        def _is_current_running() -> bool:
            return self._session_id == my_session_id and self._is_running

        def _request_stop() -> None:
            if self._session_id == my_session_id:
                self._is_running = False

        def _get_startup_state() -> StartupState:
            return self._startup_state if self._session_id == my_session_id else StartupState.IDLE

        def _set_startup_state(state: StartupState) -> None:
            if self._session_id == my_session_id:
                self._startup_state = state

        def _set_actual_start_time_ms(ts_ms: int) -> None:
            if self._session_id == my_session_id:
                self._actual_start_time_ms = int(ts_ms)

        def _set_actual_end_time_ms(ts_ms: int) -> None:
            if self._session_id == my_session_id:
                self._actual_end_time_ms = int(ts_ms)

        def _set_active_process(proc: Optional[subprocess.Popen]) -> None:
            with self._process_lock:
                if self._session_id == my_session_id:
                    self._active_process = proc

        def _clear_active_process() -> None:
            with self._process_lock:
                if self._session_id == my_session_id:
                    self._active_process = None

        def _set_last_error(msg: str) -> None:
            if self._session_id == my_session_id:
                self._set_last_error(msg)

        return SessionCallbacks(
            is_running=_is_current_running,
            request_stop=_request_stop,
            get_startup_state=_get_startup_state,
            set_startup_state=_set_startup_state,
            set_actual_start_time_ms=_set_actual_start_time_ms,
            set_actual_end_time_ms=_set_actual_end_time_ms,
            set_active_process=_set_active_process,
            clear_active_process=_clear_active_process,
            add_lingering_threads=self._add_lingering_threads,
            log=self._log,
            set_last_error=_set_last_error,
        )

    def _recording_loop(self, session_cfg: RecordingSessionConfig, my_session_id: int) -> None:
        callbacks = self._build_session_callbacks(my_session_id)
        try:
            PythonRecordingSession(session_cfg, callbacks).run()
        except Exception as e:
            if self._session_id == my_session_id:
                if self._startup_state == StartupState.INITIALIZING:
                    self._startup_state = StartupState.FAILED
                self._set_last_error(f"Unhandled exception in recording loop: {e}")
        finally:
            if self._session_id == my_session_id:
                if self._startup_state == StartupState.INITIALIZING:
                    self._startup_state = StartupState.FAILED
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
