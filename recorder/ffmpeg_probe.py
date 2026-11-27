"""Module: @role: FFmpeg実行ファイル解決およびフィルタ・オプション対応可否のプローブとキャッシュを担うモジュール"""
import os
import re
import shutil
import subprocess
import threading
import time
from typing import Optional

from core.config import get_app_base_dir, get_app_data_dir

_TQS_CACHE_LOCK = threading.Lock()
_THREAD_QUEUE_SIZE_SUPPORT_CACHE: dict[str, tuple[bool, bool]] = {}
_FFMPEG_FILTERS_LOCK = threading.Lock()
_FFMPEG_FILTERS_CACHE: dict[str, set[str]] = {}
_FFMPEG_FILTERS_PROBING: set[str] = set()
_FFMPEG_FILTERS_FAIL_UNTIL: dict[str, float] = {}
_FFMPEG_FILTER_FAIL_COOLDOWN_SEC = 5.0

def resolve_ffmpeg_path() -> Optional[str]:
    """AppData・実行ディレクトリ・プロジェクト配下・PATHからFFmpeg実行ファイルを汎用解決する"""
    exe_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.path.join(get_app_data_dir(), "bin", exe_name),
        os.path.join(get_app_base_dir(), "bin", exe_name),
        os.path.join(project_root, "bin", exe_name),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return shutil.which("ffmpeg")

def _norm_path_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path)) if path else ""

def is_ffmpeg_filter_known_supported(ffmpeg_path: str, filter_name: str) -> Optional[bool]:
    """キャッシュ済みであれば非ブロックでフィルタ対応可否を返し、未計測ならNoneを返す"""
    if not ffmpeg_path or not filter_name:
        return False
    key = _norm_path_key(ffmpeg_path)
    with _FFMPEG_FILTERS_LOCK:
        cached = _FFMPEG_FILTERS_CACHE.get(key)
    if cached is None:
        return None
    return filter_name.lower() in cached

def probe_ffmpeg_filters_async(ffmpeg_path: str) -> None:
    """UI・モニタースレッドをブロックしないよう裏スレッドでFFmpegフィルタ一覧を事前キャッシュする"""
    if not ffmpeg_path or not os.path.exists(ffmpeg_path):
        return
    key = _norm_path_key(ffmpeg_path)
    now = time.monotonic()
    with _FFMPEG_FILTERS_LOCK:
        if key in _FFMPEG_FILTERS_CACHE or key in _FFMPEG_FILTERS_PROBING:
            return
        if now < _FFMPEG_FILTERS_FAIL_UNTIL.get(key, 0.0):
            return
        _FFMPEG_FILTERS_PROBING.add(key)

    def _worker() -> None:
        try:
            supports_ffmpeg_filter(ffmpeg_path, "arnndn")
        finally:
            with _FFMPEG_FILTERS_LOCK:
                _FFMPEG_FILTERS_PROBING.discard(key)

    threading.Thread(target=_worker, name="FFmpegFilterProbe", daemon=True).start()

def supports_ffmpeg_filter(ffmpeg_path: str, filter_name: str) -> bool:
    """FFmpegビルドが指定フィルタをサポートしているかスレッドセーフに判定する（一時失敗は永続キャッシュしない）"""
    if not ffmpeg_path or not os.path.exists(ffmpeg_path) or not filter_name:
        return False
    key = _norm_path_key(ffmpeg_path)
    with _FFMPEG_FILTERS_LOCK:
        cached = _FFMPEG_FILTERS_CACHE.get(key)
        if cached is not None:
            return filter_name.lower() in cached
        if time.monotonic() < _FFMPEG_FILTERS_FAIL_UNTIL.get(key, 0.0):
            return False

    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    found: set[str] = set()
    probe_succeeded = False
    try:
        res = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-filters"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            timeout=5.0,
        )
        if res.returncode == 0 and res.stdout:
            out_text = res.stdout.decode("utf-8", errors="replace")
            for line in out_text.splitlines():
                m = re.match(r"^\s*[T.S.C.]+\s+([A-Za-z0-9_]+)\s+", line)
                if m:
                    found.add(m.group(1).lower())
            probe_succeeded = len(found) > 0
    except Exception:
        probe_succeeded = False

    with _FFMPEG_FILTERS_LOCK:
        if probe_succeeded:
            _FFMPEG_FILTERS_CACHE[key] = found
            _FFMPEG_FILTERS_FAIL_UNTIL.pop(key, None)
        else:
            # タイムアウト等の一時失敗で空集合を永続キャッシュしてフィルタが恒久無効化されるのを防止
            _FFMPEG_FILTERS_FAIL_UNTIL[key] = time.monotonic() + _FFMPEG_FILTER_FAIL_COOLDOWN_SEC
    return probe_succeeded and (filter_name.lower() in found)

def supports_arnndn_filter(ffmpeg_path: str) -> bool:
    return supports_ffmpeg_filter(ffmpeg_path, "arnndn")

def get_thread_queue_size_support(ffmpeg_path: str) -> tuple[bool, bool]:
    """FFmpegのバージョン差異によるthread_queue_sizeの入出力オプション仕様を動的判定"""
    if not ffmpeg_path or not os.path.exists(ffmpeg_path):
        return False, False
    key = _norm_path_key(ffmpeg_path)
    with _TQS_CACHE_LOCK:
        cached = _THREAD_QUEUE_SIZE_SUPPORT_CACHE.get(key)
    if cached is not None:
        return cached

    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    supports_input = False
    in_probed = False
    try:
        res_in = subprocess.run(
            [ffmpeg_path, "-v", "error", "-thread_queue_size", "16", "-f", "lavfi", "-i", "anullsrc", "-frames:a", "1", "-f", "null", "-"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
            timeout=3.0,
        )
        in_probed = True
        err_in = res_in.stderr.decode("utf-8", errors="replace").lower() if res_in.stderr else ""
        if res_in.returncode == 0 and "cannot be applied to input" not in err_in and "unrecognized option" not in err_in:
            supports_input = True
    except Exception:
        supports_input = False

    supports_output = False
    out_probed = False
    try:
        res_out = subprocess.run(
            [ffmpeg_path, "-v", "error", "-f", "lavfi", "-i", "anullsrc", "-frames:a", "1", "-thread_queue_size", "16", "-f", "null", "-"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
            timeout=3.0,
        )
        out_probed = True
        err_out = res_out.stderr.decode("utf-8", errors="replace").lower() if res_out.stderr else ""
        if res_out.returncode == 0 and "cannot be applied to output" not in err_out and "unrecognized option" not in err_out:
            supports_output = True
    except Exception:
        supports_output = False

    if in_probed and out_probed:
        with _TQS_CACHE_LOCK:
            _THREAD_QUEUE_SIZE_SUPPORT_CACHE[key] = (supports_input, supports_output)
    return supports_input, supports_output
