import os
import time
import logging
import platform
import datetime
import subprocess
import threading
from PyQt6.QtCore import QThread, pyqtSignal
from core.config import Config


def generate_diagnostic_report(config, test_errors: list = None) -> str:
    """管理者・開発者共有用に、環境設定・VALORANTログ更新状態・直近エラー抽出・ログ末尾を約1,000トークン以内で端的にまとめた診断レポートを生成する"""
    from core.version import APP_VERSION

    lines = []
    lines.append("=== VALORANT RECORDER DIAGNOSTIC REPORT ===")
    lines.append(f"Timestamp: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    # 1. システム & 録画設定情報 (約500文字)
    lines.append("[1. SYSTEM & CONFIG]")
    lines.append(f"- App Version: {APP_VERSION}")
    try:
        win_ver = platform.win32_ver()
        os_info = f"Windows {win_ver[0]} ({win_ver[1]} {win_ver[2]})"
    except Exception:
        os_info = platform.platform()
    lines.append(f"- OS: {os_info}")
    lines.append(f"- Python: {platform.python_version()}")

    save_dir = getattr(config, "SAVE_DIR", "")
    dir_exists = os.path.exists(save_dir) if save_dir else False
    dir_writable = os.access(save_dir, os.W_OK) if dir_exists else False
    lines.append(f"- Save Dir: {save_dir} (Exists: {dir_exists}, Writable: {dir_writable})")
    lines.append(f"- Encoder: {getattr(config, 'RECORD_ENCODER', 'unknown')}")
    lines.append(f"- Video Format: {getattr(config, 'RECORD_VIDEO_FORMAT', 'unknown')} (Monitor: {getattr(config, 'RECORD_INPUT_SOURCE', 'primary')})")
    lines.append(f"- Res / FPS: {getattr(config, 'RECORD_RESOLUTION', 'unknown')} / {getattr(config, 'RECORD_FPS', 'unknown')}fps")
    lines.append(f"- Lightweight: {getattr(config, 'LIGHTWEIGHT_MODE', False)}")
    lines.append(f"- Audio System: {getattr(config, 'RECORD_AUDIO_SYSTEM', 'default')}")
    lines.append(f"- Audio Mic: {getattr(config, 'RECORD_AUDIO_MIC', 'None') or 'None'} (Preprocess: {getattr(config, 'RECORD_AUDIO_MIC_PREPROCESS', 'None')})")
    lines.append("")

    # 2. 環境テストエラー情報 (存在する場合のみ 約400文字)
    if test_errors:
        lines.append("[2. ENVIRONMENT TEST FAILURES]")
        for err_title, err_desc in test_errors:
            lines.append(f"❌ {err_title}")
            desc_lines = [d.strip() for d in err_desc.strip().splitlines() if d.strip()]
            for dl in desc_lines[-3:]:
                lines.append(f"   {dl[:120]}")
        lines.append("")

    # 3. VALORANTログ (ShooterGame.log) の状態 (約600文字)
    sec_num = "3" if test_errors else "2"
    lines.append(f"[{sec_num}. VALORANT LOG STATUS]")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        user_profile = os.environ.get("USERPROFILE")
        if user_profile:
            local_app_data = os.path.join(user_profile, "AppData", "Local")

    valo_log_path = os.path.join(local_app_data, "VALORANT", "Saved", "Logs", "ShooterGame.log") if local_app_data else ""
    lines.append(f"- Log Path: {valo_log_path}")

    if not valo_log_path or not os.path.exists(valo_log_path):
        lines.append("- Status: NOT FOUND (VALORANT might not be running or path is customized)")
    else:
        try:
            mtime = os.path.getmtime(valo_log_path)
            diff_min = int((time.time() - mtime) / 60)
            mtime_str = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
            size_kb = os.path.getsize(valo_log_path) // 1024
            lines.append(f"- Status: FOUND (Size: {size_kb} KB, Last Modified: {mtime_str}, {diff_min} min ago)")

            # 末尾 32KB から主要なゲーム状態遷移を抜粋
            file_size = os.path.getsize(valo_log_path)
            with open(valo_log_path, "r", encoding="utf-8", errors="replace") as vf:
                vf.seek(max(0, file_size - 32 * 1024))
                raw_lines = [ln.strip() for ln in vf.read().splitlines() if ln.strip()]

            relevant_keywords = ("State:", "Transition", "Match", "GameFlow", "Phase", "Round", "Session")
            matched_lines = [l for l in raw_lines if any(k in l for k in relevant_keywords)]

            lines.append("- Recent Relevant Game State Lines:")
            lines_to_show = matched_lines[-6:] if matched_lines else raw_lines[-5:]
            if lines_to_show:
                for ml in lines_to_show:
                    lines.append(f"  {ml[:110]}")
            else:
                lines.append("  (Log file is empty)")
        except PermissionError:
            lines.append("- Status: ACCESS DENIED (Locked by Vanguard/Anti-Cheat)")
        except Exception as e:
            lines.append(f"- Status: ERROR READING LOG ({e})")
    lines.append("")

    # 4. アプリログ (app.log) の直近エラー & 末尾ログ (約1,000文字)
    sec_num_app = "4" if test_errors else "3"
    lines.append(f"[{sec_num_app}. RECENT ERRORS & APP LOG TAIL]")
    app_log_path = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ValorantRecorder", "app.log")
    lines.append(f"- App Log Path: {app_log_path}")

    if not os.path.exists(app_log_path):
        lines.append("- Status: app.log NOT FOUND")
    else:
        try:
            af_size = os.path.getsize(app_log_path)
            with open(app_log_path, "r", encoding="utf-8", errors="replace") as af:
                af.seek(max(0, af_size - 32 * 1024))
                log_lines = [l.strip() for l in af.read().splitlines() if l.strip()]

            error_keywords = ("ERROR", "CRITICAL", "Exception", "Traceback", "failed", "Failed", "[Error]")
            recent_errors = [l for l in log_lines if any(k in l for k in error_keywords)]

            lines.append("- Extracted Recent Error / Warning Lines (Up to 5):")
            if recent_errors:
                for el in recent_errors[-5:]:
                    lines.append(f"  ❌ {el[:110]}")
            else:
                lines.append("  (No recent error patterns detected)")

            lines.append("- Recent App Log Tail (Last 15 lines):")
            for tl in log_lines[-15:]:
                lines.append(f"  {tl[:110]}")
        except Exception as e:
            lines.append(f"- Status: ERROR READING app.log ({e})")
    lines.append("")

    # 5. FFmpeg ネイティブ実行ログ (native_ffmpeg.log) 直近末尾
    if save_dir:
        ffmpeg_log_path = os.path.join(save_dir, "native_ffmpeg.log")
        if os.path.exists(ffmpeg_log_path):
            try:
                lines.append("[5. NATIVE FFMPEG LOG TAIL]")
                lines.append(f"- Native FFmpeg Log Path: {ffmpeg_log_path}")
                with open(ffmpeg_log_path, "r", encoding="utf-8", errors="replace") as ff_log:
                    ff_lines = [l.strip() for l in ff_log.read().splitlines() if l.strip()]
                for fl in ff_lines[-10:]:
                    lines.append(f"  {fl[:110]}")
            except Exception:
                pass

    lines.append("=== END OF REPORT ===")
    return "\n".join(lines)


STEP_FFMPEG = 0
STEP_ENCODER = 1
STEP_AUDIO = 2
STEP_LOG = 3
STEP_PIPELINE = 4
TOTAL_STEPS = 5


class EnvTesterThread(QThread):
    """
    動作環境（FFmpeg, エンコーダ, オーディオ, ログアクセス, 録画パイプライン）をテストするスレッド
    """
    step_started = pyqtSignal(int, str)
    step_finished = pyqtSignal(int, str, bool, str, str)
    finished_signal = pyqtSignal(list, list)
    _GLOBAL_TEST_LOCK = threading.Lock()
    _current_running_instance = None

    @classmethod
    def is_test_running(cls) -> bool:
        return cls._current_running_instance is not None and cls._current_running_instance.isRunning()

    def __init__(self, config: Config, dry_run_recording: bool = True):
        super().__init__()
        self.config = config
        self.dry_run_recording = dry_run_recording
        self._is_cancelled = False
        self.step_results = {}

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        with self._GLOBAL_TEST_LOCK:
            EnvTesterThread._current_running_instance = self
            try:
                self._execute_tests()
            finally:
                EnvTesterThread._current_running_instance = None

    def _execute_tests(self):
        logging.info("=== Starting Environment Tests ===")
        successes = []
        errors = []

        # 1. FFmpeg Test
        if self._is_cancelled:
            return
        self.step_started.emit(STEP_FFMPEG, "ffmpeg")
        ffmpeg_path = None
        try:
            from recorder.ffmpeg_downloader import ensure_ffmpeg_downloaded
            app_data_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'ValoReco')
            ffmpeg_path = ensure_ffmpeg_downloaded(app_data_dir)
            
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            subprocess.run([ffmpeg_path, "-version"], capture_output=True, check=True, creationflags=creationflags)
            logging.info("[EnvTest] FFmpeg test: PASSED")
            successes.append("FFmpeg Executable")
            self.step_results[STEP_FFMPEG] = (True, "FFmpeg Executable", "")
            self.step_finished.emit(STEP_FFMPEG, "ffmpeg", True, "FFmpeg Executable", "")
        except Exception as e:
            import traceback
            err_detail = traceback.format_exc()
            logging.error(f"[EnvTest] FFmpeg test failed: {e}")
            err_title = "FFmpeg is not available or cannot be executed."
            errors.append((err_title, err_detail))
            self.step_results[STEP_FFMPEG] = (False, err_title, err_detail)
            self.step_finished.emit(STEP_FFMPEG, "ffmpeg", False, err_title, err_detail)

        # 2. Encoder Test
        if self._is_cancelled:
            return
        self.step_started.emit(STEP_ENCODER, "encoder")
        if ffmpeg_path:
            try:
                from recorder.encoder_utils import test_encoder, get_available_encoders
                success, msg = test_encoder(ffmpeg_path, self.config.RECORD_ENCODER)
                if not success:
                    available, _ = get_available_encoders(ffmpeg_path)
                    if available:
                        fallback_enc = available[0]
                        fb_success, fb_msg = test_encoder(ffmpeg_path, fallback_enc)
                        if fb_success:
                            logging.info(f"[EnvTest] Switched encoder from {self.config.RECORD_ENCODER} to supported {fallback_enc}")
                            self.config.RECORD_ENCODER = fallback_enc
                            try:
                                self.config.save()
                            except Exception:
                                pass
                            success, msg = True, ""
                        else:
                            msg = fb_msg
                if success:
                    summary = f"Video Encoder ({self.config.RECORD_ENCODER})"
                    logging.info(f"[EnvTest] Encoder test ({self.config.RECORD_ENCODER}): PASSED")
                    successes.append(summary)
                    self.step_results[STEP_ENCODER] = (True, summary, "")
                    self.step_finished.emit(STEP_ENCODER, "encoder", True, summary, "")
                else:
                    err_title = f"Encoder '{self.config.RECORD_ENCODER}' is not supported on this system."
                    logging.warning(f"[EnvTest] Encoder test ({self.config.RECORD_ENCODER}): FAILED - {msg}")
                    errors.append((err_title, msg))
                    self.step_results[STEP_ENCODER] = (False, err_title, msg)
                    self.step_finished.emit(STEP_ENCODER, "encoder", False, err_title, msg)
            except Exception as e:
                import traceback
                err_detail = traceback.format_exc()
                logging.error(f"[EnvTest] Encoder test exception: {e}")
                err_title = "Failed to test video encoder."
                errors.append((err_title, err_detail))
                self.step_results[STEP_ENCODER] = (False, err_title, err_detail)
                self.step_finished.emit(STEP_ENCODER, "encoder", False, err_title, err_detail)
        else:
            err_title = "Encoder test skipped because FFmpeg was not found."
            errors.append((err_title, ""))
            self.step_results[STEP_ENCODER] = (False, err_title, "")
            self.step_finished.emit(STEP_ENCODER, "encoder", False, err_title, "")

        # 3. Audio Device Test
        if self._is_cancelled:
            return
        self.step_started.emit(STEP_AUDIO, "audio")
        com_initialized = False
        if os.name == 'nt':
            try:
                import ctypes
                hr = ctypes.windll.ole32.CoInitializeEx(None, 0)
                com_initialized = (hr in (0, 1))
            except Exception:
                pass
        try:
            import soundcard as sc
            speaker = sc.default_speaker()
            mic = sc.default_microphone()
            logging.info(f"[EnvTest] Audio test: PASSED (Speaker: {speaker.name}, Mic: {mic.name})")
            summary = f"Speaker: {speaker.name} / Mic: {mic.name}"
            successes.append("Audio Devices (Speaker/Mic)")
            self.step_results[STEP_AUDIO] = (True, summary, "")
            self.step_finished.emit(STEP_AUDIO, "audio", True, summary, "")
        except Exception as e:
            import traceback
            err_detail = traceback.format_exc()
            logging.error(f"[EnvTest] Audio device test failed: {e}")
            err_title = "Could not detect default audio devices (Speaker/Mic)."
            errors.append((err_title, err_detail))
            self.step_results[STEP_AUDIO] = (False, err_title, err_detail)
            self.step_finished.emit(STEP_AUDIO, "audio", False, err_title, err_detail)
        finally:
            if com_initialized:
                try:
                    import ctypes
                    ctypes.windll.ole32.CoUninitialize()
                except Exception:
                    pass

        # 4. Log Access Test
        if self._is_cancelled:
            return
        self.step_started.emit(STEP_LOG, "log")
        try:
            local_app_data = os.environ.get('LOCALAPPDATA')
            if not local_app_data:
                user_profile = os.environ.get('USERPROFILE')
                if user_profile:
                    local_app_data = os.path.join(user_profile, 'AppData', 'Local')
                else:
                    raise EnvironmentError("LOCALAPPDATA not found.")
            
            log_dir = os.path.join(local_app_data, 'VALORANT', 'Saved', 'Logs')
            os.makedirs(log_dir, exist_ok=True)
            
            test_file = os.path.join(log_dir, 'valoreco_test.tmp')
            with open(test_file, 'w') as f:
                f.write('test')
            os.remove(test_file)
            logging.info("[EnvTest] Log directory access test: PASSED")
            successes.append("VALORANT Log Access")
            self.step_results[STEP_LOG] = (True, "VALORANT Log Access", "")
            self.step_finished.emit(STEP_LOG, "log", True, "VALORANT Log Access", "")
        except Exception as e:
            import traceback
            err_detail = traceback.format_exc()
            logging.error(f"[EnvTest] Log access test failed: {e}")
            err_title = "Cannot access VALORANT log directory. Vanguard or permissions might be blocking it."
            errors.append((err_title, err_detail))
            self.step_results[STEP_LOG] = (False, err_title, err_detail)
            self.step_finished.emit(STEP_LOG, "log", False, err_title, err_detail)

        # 5. Mock / Dry-Run Recording Test
        if self._is_cancelled:
            return
        self.step_started.emit(STEP_PIPELINE, "pipeline")
        if not self.dry_run_recording:
            # 起動時のサイレント検証では疑似録画を省略し即完了
            summary = "Skipped during startup check"
            successes.append("Recording Pipeline (Startup Skipped)")
            self.step_results[STEP_PIPELINE] = (True, summary, "")
            self.step_finished.emit(STEP_PIPELINE, "pipeline", True, summary, "")
        elif ffmpeg_path:
            recorder = None
            try:
                from recorder.ffmpeg_recorder import FFmpegRecorder
                recorder = FFmpegRecorder(self.config)
                if recorder.is_recording():
                    logging.info("[EnvTest] Recording pipeline test skipped: recorder is already active.")
                    successes.append("Recording Pipeline (Active Recording)")
                    self.step_results[STEP_PIPELINE] = (True, "Active Recording", "")
                    self.step_finished.emit(STEP_PIPELINE, "pipeline", True, "Active Recording", "")
                else:
                    recorder.start_recording()
                    for _ in range(12):
                        if self._is_cancelled:
                            break
                        time.sleep(0.1)
                        if not recorder.is_recording():
                            break

                    if not recorder.is_recording():
                        err_msg = recorder.get_last_error() or "FFmpeg recording pipeline terminated unexpectedly."
                        raise RuntimeError(err_msg)

                    recorder.cancel_recording()
                    logging.info("[EnvTest] Recording pipeline test: PASSED")
                    successes.append("Recording Pipeline (Screen & Audio Capture)")
                    self.step_results[STEP_PIPELINE] = (True, "Screen & Audio Capture OK", "")
                    self.step_finished.emit(STEP_PIPELINE, "pipeline", True, "Screen & Audio Capture OK", "")
            except Exception as e:
                import traceback
                err_detail = traceback.format_exc()
                logging.error(f"[EnvTest] Recording pipeline test failed: {e}")
                err_title = "Recording pipeline dry-run failed."
                errors.append((err_title, err_detail))
                self.step_results[STEP_PIPELINE] = (False, err_title, err_detail)
                self.step_finished.emit(STEP_PIPELINE, "pipeline", False, err_title, err_detail)
                if recorder:
                    try:
                        recorder.cancel_recording()
                    except Exception:
                        pass
        else:
            err_title = "Pipeline test skipped because FFmpeg is missing."
            errors.append((err_title, ""))
            self.step_results[STEP_PIPELINE] = (False, err_title, "")
            self.step_finished.emit(STEP_PIPELINE, "pipeline", False, err_title, "")

        logging.info("=== Environment Tests Completed ===")
        self.finished_signal.emit(successes, errors)
