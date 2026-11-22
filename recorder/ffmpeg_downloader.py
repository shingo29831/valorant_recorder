import logging
import os
import sys
import threading
import time
import urllib.request
import zipfile
import tempfile
import shutil

FFMPEG_DOWNLOAD_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
RNNOISE_MODEL_URL = "https://raw.githubusercontent.com/GregorR/rnnoise-models/master/somnolent-hogwash-2018-09-01/sh.rnnn"
_RNNOISE_MODEL_HEADER = b"rnnoise-nu model"
_RNNOISE_MIN_BYTES = 1024
_RNNOISE_FAIL_COOLDOWN_SEC = 60.0
_RNNOISE_FILE_LOCK = threading.Lock()
_RNNOISE_ASYNC_LOCK = threading.Lock()
_RNNOISE_DL_EVENTS: dict[str, threading.Event] = {}
_RNNOISE_DL_LAST_FAILED: dict[str, float] = {}

def _safe_print(msg, end="\n", flush=False):
    if sys.stdout is not None:
        try:
            sys.stdout.write(msg + end)
            if flush:
                sys.stdout.flush()
        except AttributeError:
            pass

def _progress_hook(count, block_size, total_size):
    if total_size > 0:
        percent = min(100, int(count * block_size * 100 / total_size))
        _safe_print(f"\r[Downloader] Downloading FFmpeg... {percent}%", end="", flush=True)

def ensure_ffmpeg_downloaded(base_dir: str) -> str:
    bin_dir = os.path.join(base_dir, "bin")
    ffmpeg_exe_path = os.path.join(bin_dir, "ffmpeg.exe")

    if os.path.exists(ffmpeg_exe_path):
        return ffmpeg_exe_path

    os.makedirs(bin_dir, exist_ok=True)
    
    _safe_print("[Downloader] FFmpeg Full Build not found. Starting download...")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        zip_path = os.path.join(temp_dir, "ffmpeg.zip")
        
        urllib.request.urlretrieve(FFMPEG_DOWNLOAD_URL, zip_path, reporthook=_progress_hook)
        _safe_print("\n[Downloader] Download complete. Extracting...")
        
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            for file_info in zip_ref.infolist():
                if file_info.filename.endswith("ffmpeg.exe"):
                    extracted_path = zip_ref.extract(file_info, temp_dir)
                    shutil.move(extracted_path, ffmpeg_exe_path)
                    break
                    
    if not os.path.exists(ffmpeg_exe_path):
        raise RuntimeError("Failed to download or extract ffmpeg.exe")
        
    _safe_print("[Downloader] FFmpeg setup complete.")
    return ffmpeg_exe_path

def is_valid_rnnoise_model(model_path: str) -> bool:
    """RNNoiseモデルがHTMLエラーページや破損ファイルでないことをヘッダとサイズで検証する"""
    try:
        if not model_path or not os.path.isfile(model_path):
            return False
        if os.path.getsize(model_path) < _RNNOISE_MIN_BYTES:
            return False
        with open(model_path, "rb") as f:
            header = f.read(64)
        return header.startswith(_RNNOISE_MODEL_HEADER)
    except OSError:
        return False


def ensure_rnnoise_model_downloaded(base_dir: str, timeout: float = 15.0) -> str:
    bin_dir = os.path.join(base_dir, "bin")
    model_path = os.path.join(bin_dir, "sh.rnnn")

    if is_valid_rnnoise_model(model_path):
        return model_path

    # 同期・非同期DLの同時実行による一時ファイル競合と破損上書きを防ぐ
    with _RNNOISE_FILE_LOCK:
        if is_valid_rnnoise_model(model_path):
            return model_path

        os.makedirs(bin_dir, exist_ok=True)
        tmp_path = f"{model_path}.tmp.{os.getpid()}.{threading.get_ident()}"
        _safe_print("[Downloader] RNNoise model not found or invalid. Starting download...")
        try:
            req = urllib.request.Request(RNNOISE_MODEL_URL, headers={"User-Agent": "ValoReco"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                with open(tmp_path, "wb") as out_f:
                    shutil.copyfileobj(resp, out_f)
            if not is_valid_rnnoise_model(tmp_path):
                raise RuntimeError("Downloaded RNNoise model failed header/size validation")
            os.replace(tmp_path, model_path)
            _safe_print("[Downloader] RNNoise model setup complete.")
        except Exception as e:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            _safe_print(f"[Downloader] Failed to download RNNoise model: {e}")
            raise RuntimeError(f"Failed to download RNNoise model: {e}")

    return model_path


def ensure_rnnoise_model_async(base_dir: str) -> threading.Event:
    """録画開始・UIスレッドをブロックしないよう裏スレッドでRNNoiseモデルを事前取得し完了Eventを返す"""
    done_ev = threading.Event()
    if not base_dir:
        done_ev.set()
        return done_ev
    model_path = os.path.join(base_dir, "bin", "sh.rnnn")
    if is_valid_rnnoise_model(model_path):
        done_ev.set()
        return done_ev

    norm_key = os.path.normcase(os.path.abspath(model_path))
    with _RNNOISE_ASYNC_LOCK:
        existing_ev = _RNNOISE_DL_EVENTS.get(norm_key)
        if existing_ev is not None:
            return existing_ev
        _RNNOISE_DL_EVENTS[norm_key] = done_ev

    def _worker() -> None:
        try:
            ensure_rnnoise_model_downloaded(base_dir, timeout=15.0)
            with _RNNOISE_ASYNC_LOCK:
                _RNNOISE_DL_LAST_FAILED.pop(norm_key, None)
        except Exception as e:
            logging.warning(f"[Downloader] Background RNNoise model download failed: {e}")
            with _RNNOISE_ASYNC_LOCK:
                _RNNOISE_DL_LAST_FAILED[norm_key] = time.monotonic()
        finally:
            with _RNNOISE_ASYNC_LOCK:
                _RNNOISE_DL_EVENTS.pop(norm_key, None)
            done_ev.set()

    threading.Thread(target=_worker, name="RNNoiseModelDownloader", daemon=True).start()
    return done_ev


def wait_for_rnnoise_model_ready(base_dir: str, timeout: float = 2.5) -> bool:
    """初回録画時に進行中のRNNoiseモデル取得完了を上限付きで待機する（直近失敗時は即スキップ）"""
    if not base_dir:
        return False
    model_path = os.path.join(base_dir, "bin", "sh.rnnn")
    if is_valid_rnnoise_model(model_path):
        return True

    norm_key = os.path.normcase(os.path.abspath(model_path))
    with _RNNOISE_ASYNC_LOCK:
        in_progress = norm_key in _RNNOISE_DL_EVENTS
        last_failed = _RNNOISE_DL_LAST_FAILED.get(norm_key, 0.0)
    # オフライン環境等で毎回録画開始が遅延するのを防ぐため、直近失敗クールダウン中は待機しない
    if not in_progress and last_failed > 0.0 and (time.monotonic() - last_failed) < _RNNOISE_FAIL_COOLDOWN_SEC:
        return False

    ev = ensure_rnnoise_model_async(base_dir)
    ev.wait(timeout=max(0.0, float(timeout)))
    return is_valid_rnnoise_model(model_path)
