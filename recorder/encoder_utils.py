import os
import subprocess
from datetime import datetime

_ENCODER_TEST_CACHE: dict[tuple[str, str], tuple[bool, str]] = {}
_AVAILABLE_ENCODERS_CACHE: dict[str, tuple[list[str], list[str]]] = {}

def test_encoder(ffmpeg_path: str, encoder: str) -> tuple[bool, str]:
    """指定されたエンコーダが現在の環境で利用可能かテストし、結果とエラーメッセージを返す"""
    if not ffmpeg_path or not encoder:
        return False, "Invalid ffmpeg_path or encoder."

    cache_key = (ffmpeg_path, encoder)
    if cache_key in _ENCODER_TEST_CACHE:
        return _ENCODER_TEST_CACHE[cache_key]

    enc_lower = encoder.lower()
    # QSV等のハードウェアエンコーダはyuv420p直接入力非対応の場合があるためネイティブのnv12を使用する
    pix_fmt = "nv12" if any(k in enc_lower for k in ("nvenc", "amf", "qsv")) else "yuv420p"

    extra_opts = []
    if "nvenc" in enc_lower or "qsv" in enc_lower or enc_lower == "h264_amf":
        extra_opts = ["-bf", "0"]

    cmd = [
        ffmpeg_path,
        "-v", "error",
        # NVENC/AMF/QSVの最小解像度・フレームレート制限を回避するため256x256@30fpsでテストする
        "-f", "lavfi", "-i", "color=black:s=256x256:r=30",
        "-pix_fmt", pix_fmt,
        "-c:v", encoder,
        *extra_opts,
        "-frames:v", "1",
        "-f", "null", "-"
    ]
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=creationflags, timeout=5.0)
        if res.returncode != 0 and "qsv" in enc_lower:
            # マルチGPU環境でプライマリGPUが他社製の場合にQSVデバイスを明示初期化して再試行
            qsv_cmd = [
                ffmpeg_path,
                "-v", "error",
                "-init_hw_device", "qsv=hw",
                "-f", "lavfi", "-i", "color=black:s=256x256:r=30",
                "-pix_fmt", pix_fmt,
                "-c:v", encoder,
                *extra_opts,
                "-frames:v", "1",
                "-f", "null", "-"
            ]
            res = subprocess.run(qsv_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=creationflags, timeout=5.0)
        if res.returncode != 0:
            err_msg = res.stderr.decode('utf-8', errors='replace') if res.stderr else ""
            result = (False, err_msg.strip())
        else:
            result = (True, "")
    except subprocess.TimeoutExpired:
        result = (False, "Encoder test timed out.")
    except Exception as e:
        result = (False, str(e))

    _ENCODER_TEST_CACHE[cache_key] = result
    return result

def get_available_encoders(ffmpeg_path: str) -> tuple[list, list]:
    """
    利用可能なハードウェアエンコーダと、発生した警告メッセージキーのリストを返す。
    """
    if ffmpeg_path in _AVAILABLE_ENCODERS_CACHE:
        cached_avail, cached_warn = _AVAILABLE_ENCODERS_CACHE[ffmpeg_path]
        return list(cached_avail), list(cached_warn)

    # 低負荷・再生互換性に優れるH.264を各GPUベンダーの第1候補として優先配置
    hw_encoders = [
        "h264_nvenc", "hevc_nvenc",  # NVIDIA
        "h264_amf", "hevc_amf",      # AMD
        "h264_qsv", "hevc_qsv"       # Intel
    ]
    
    available = []
    warning_keys = []
    
    # 失敗原因特定のため、永続的なデータディレクトリにログを出力する
    app_data_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'ValoReco')
    os.makedirs(app_data_dir, exist_ok=True)
    log_path = os.path.join(app_data_dir, "encoder_test.log")
    
    try:
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(f"--- Encoder Test Log ({datetime.now()}) ---\n")
            
            for enc in hw_encoders:
                success, err_msg = test_encoder(ffmpeg_path, enc)
                if success:
                    available.append(enc)
                    f.write(f"[{enc}] Success\n")
                else:
                    f.write(f"[{enc}] Failed:\n{err_msg}\n\n")
                    # NVIDIAドライバが古い場合のエラーを検知
                    if "nvenc" in enc and "minimum required Nvidia driver" in err_msg:
                        if "nvenc_driver_old" not in warning_keys:
                            warning_keys.append("nvenc_driver_old")
    except Exception as e:
        print(f"Failed to write encoder_test.log: {e}")
        # ログ書き込みに失敗してもテスト自体は続行する
        for enc in hw_encoders:
            if enc not in available:
                success, err_msg = test_encoder(ffmpeg_path, enc)
                if success:
                    available.append(enc)
            
    if "libx264" not in available:
        available.append("libx264")

    _AVAILABLE_ENCODERS_CACHE[ffmpeg_path] = (list(available), list(warning_keys))
    return available, warning_keys
