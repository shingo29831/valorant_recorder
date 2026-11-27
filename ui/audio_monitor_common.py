"""Module: @role: 音声モニタースレッド群で共有するCOM初期化・soundcardロック・ログ出力ユーティリティ"""

import datetime
import os
import sys
import threading
import traceback
import warnings
from typing import Any

from core.config import get_app_data_dir

_SOUNDCARD_LOCK = threading.RLock()

def get_soundcard_lock(sc_module: Any) -> Any:
    return getattr(sc_module, "_lock", None) or _SOUNDCARD_LOCK

def suppress_soundcard_warnings(sc_module: Any = None) -> None:
    warnings.filterwarnings("ignore", message=".*data discontinuity.*")
    warnings.filterwarnings("ignore", module=".*soundcard.*")
    if sc_module is not None:
        warning_cls = getattr(sc_module, "SoundcardRuntimeWarning", None)
        if isinstance(warning_cls, type) and issubclass(warning_cls, Warning):
            warnings.simplefilter("ignore", category=warning_cls)

def init_com_apartment() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        hr = ctypes.windll.ole32.CoInitializeEx(None, 0)
        return hr in (0, 1)
    except Exception:
        return False

def uninit_com_apartment(initialized: bool) -> None:
    if not initialized:
        return
    try:
        import ctypes

        ctypes.windll.ole32.CoUninitialize()
    except Exception:
        pass

def log_monitor_exception(thread_name: str, filename: str) -> None:
    tb_text = traceback.format_exc()
    print(f"\n=== {thread_name} Error ===")
    print(tb_text, end="" if tb_text.endswith("\n") else "\n")
    print("=" * (len(thread_name) + 14) + "\n")
    try:
        app_data_dir = get_app_data_dir(ensure_exists=True)
        log_path = os.path.join(app_data_dir, filename)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{thread_name} error:\n{tb_text}\n")
    except OSError:
        pass

def log_thread_timeout(thread_name: str) -> None:
    try:
        app_data_dir = get_app_data_dir(ensure_exists=True)
        log_path = os.path.join(app_data_dir, "thread_error.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now()}] {thread_name} wait timed out.\n")
    except OSError:
        pass
