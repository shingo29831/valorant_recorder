import os
import sys
import urllib.request
import zipfile
import tempfile
import shutil

FFMPEG_DOWNLOAD_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
RNNOISE_MODEL_URL = "https://raw.githubusercontent.com/GregorR/rnnoise-models/master/somnolent-hogwash-2018-09-01/sh.rnnn"

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

def ensure_rnnoise_model_downloaded(base_dir: str) -> str:
    bin_dir = os.path.join(base_dir, "bin")
    model_path = os.path.join(bin_dir, "sh.rnnn")

    if os.path.exists(model_path):
        return model_path

    os.makedirs(bin_dir, exist_ok=True)
    
    _safe_print("[Downloader] RNNoise model not found. Starting download...")
    try:
        urllib.request.urlretrieve(RNNOISE_MODEL_URL, model_path)
        _safe_print("[Downloader] RNNoise model setup complete.")
    except Exception as e:
        _safe_print(f"[Downloader] Failed to download RNNoise model: {e}")
        raise RuntimeError(f"Failed to download RNNoise model: {e}")
    
    return model_path