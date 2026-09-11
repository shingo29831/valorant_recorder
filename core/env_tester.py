import os
import logging
import subprocess
from PyQt6.QtCore import QThread, pyqtSignal
from core.config import Config

class EnvTesterThread(QThread):
    """
    アプリ起動時に動作環境（FFmpeg, エンコーダ, オーディオ, ログアクセス）をテストするスレッド
    """
    # successes, errors の2つのリストを返す
    finished_signal = pyqtSignal(list, list)

    def __init__(self, config: Config):
        super().__init__()
        self.config = config

    def run(self):
        logging.info("=== Starting Environment Tests ===")
        successes = []
        errors = []

        # 1. FFmpeg Test
        ffmpeg_path = None
        try:
            from recorder.ffmpeg_downloader import ensure_ffmpeg_downloaded
            app_data_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'ValoReco')
            ffmpeg_path = ensure_ffmpeg_downloaded(app_data_dir)
            
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            subprocess.run([ffmpeg_path, "-version"], capture_output=True, check=True, creationflags=creationflags)
            logging.info("[EnvTest] FFmpeg test: PASSED")
            successes.append("FFmpeg Executable")
        except Exception as e:
            import traceback
            err_detail = traceback.format_exc()
            logging.error(f"[EnvTest] FFmpeg test failed: {e}")
            errors.append(("FFmpeg is not available or cannot be executed.", err_detail))

        # 2. Encoder Test
        if ffmpeg_path:
            try:
                from recorder.encoder_utils import test_encoder
                success, msg = test_encoder(ffmpeg_path, self.config.RECORD_ENCODER)
                if success:
                    logging.info(f"[EnvTest] Encoder test ({self.config.RECORD_ENCODER}): PASSED")
                    successes.append(f"Video Encoder ({self.config.RECORD_ENCODER})")
                else:
                    logging.warning(f"[EnvTest] Encoder test ({self.config.RECORD_ENCODER}): FAILED - {msg}")
                    errors.append((f"Encoder '{self.config.RECORD_ENCODER}' is not supported on this system.", msg))
            except Exception as e:
                import traceback
                err_detail = traceback.format_exc()
                logging.error(f"[EnvTest] Encoder test exception: {e}")
                errors.append(("Failed to test video encoder.", err_detail))

        # 3. Audio Device Test
        try:
            import soundcard as sc
            speaker = sc.default_speaker()
            mic = sc.default_microphone()
            logging.info(f"[EnvTest] Audio test: PASSED (Speaker: {speaker.name}, Mic: {mic.name})")
            successes.append("Audio Devices (Speaker/Mic)")
        except Exception as e:
            import traceback
            err_detail = traceback.format_exc()
            logging.error(f"[EnvTest] Audio device test failed: {e}")
            errors.append(("Could not detect default audio devices (Speaker/Mic).", err_detail))

        # 4. Log Access Test
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
            
            # 書き込み権限のテスト
            test_file = os.path.join(log_dir, 'valoreco_test.tmp')
            with open(test_file, 'w') as f:
                f.write('test')
            os.remove(test_file)
            logging.info("[EnvTest] Log directory access test: PASSED")
            successes.append("VALORANT Log Access")
        except Exception as e:
            import traceback
            err_detail = traceback.format_exc()
            logging.error(f"[EnvTest] Log access test failed: {e}")
            errors.append(("Cannot access VALORANT log directory. Vanguard or permissions might be blocking it.", err_detail))

        logging.info("=== Environment Tests Completed ===")
        self.finished_signal.emit(successes, errors)
