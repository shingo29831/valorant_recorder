import os
import subprocess
from datetime import datetime

def test_encoder(ffmpeg_path: str, encoder: str) -> tuple[bool, str]:
    """指定されたエンコーダが現在の環境で利用可能かテストし、結果とエラーメッセージを返す"""
    cmd = [
        ffmpeg_path,
        "-v", "error",
        # NVENCの最小解像度制限(144x144等)を回避するため、256x256でテストする
        "-f", "lavfi", "-i", "color=black:s=256x256:r=1",
        "-pix_fmt", "yuv420p",
        "-c:v", encoder,
        "-frames:v", "1",
        "-f", "null", "-"
    ]
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        # text=Trueによるエンコーディングエラー(UnicodeDecodeError等)を防ぐためバイナリで取得
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=creationflags)
        if res.returncode != 0:
            err_msg = res.stderr.decode('utf-8', errors='replace') if res.stderr else ""
            return False, err_msg.strip()
        return True, ""
    except Exception as e:
        return False, str(e)

def get_available_encoders(ffmpeg_path: str) -> tuple[list, list]:
    """
    利用可能なハードウェアエンコーダと、発生した警告メッセージキーのリストを返す。
    """
    hw_encoders = [
        "hevc_nvenc", "h264_nvenc",  # NVIDIA
        "hevc_amf", "h264_amf",      # AMD
        "hevc_qsv", "h264_qsv"       # Intel
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
            
    if available:
        return available, warning_keys
        
    return ["libx264"], warning_keys
