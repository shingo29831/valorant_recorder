"""Module: @role: アプリケーション設定の永続化(.env)および環境変数の読み書きを担うモジュール"""
import ctypes
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
from typing import Optional
from dotenv import load_dotenv

_INITIAL_ARGV0 = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""

_CONTROL_CHAR_RECOVERY_MAP = {
    "\x07": "\\a",
    "\x08": "\\b",
    "\x0c": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\x0b": "\\v",
}


def _get_nuitka_onefile_parent_exe() -> Optional[str]:
    """Nuitka onefile実行時に親ブートストラッププロセスの実体.exeパスを取得する"""
    parent_pid_str = os.environ.get("NUITKA_ONEFILE_PARENT", "").strip()
    if not parent_pid_str or sys.platform != "win32":
        return None
    try:
        parent_pid = int(parent_pid_str)
        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, parent_pid)
        if not handle:
            return None
        try:
            buf_len = ctypes.c_ulong(4096)
            buf = ctypes.create_unicode_buffer(4096)
            if ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(buf_len)):
                return os.path.abspath(buf.value)
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        pass
    return None


def is_compiled_binary() -> bool:
    """PyInstallerやNuitka等でコンパイルされた実行環境かを判定する"""
    if getattr(sys, "frozen", False):
        return True
    if "__compiled__" in globals() or os.environ.get("NUITKA_ONEFILE_PARENT"):
        return True
    main_mod = sys.modules.get("__main__")
    if main_mod is not None and hasattr(main_mod, "__compiled__"):
        return True
    if _INITIAL_ARGV0.lower().endswith(".exe") and "python" not in os.path.basename(_INITIAL_ARGV0).lower():
        return True
    return False


def is_temp_extraction_dir(path: str) -> bool:
    """Nuitka onefileやPyInstallerの一時展開ディレクトリ配下かを判定する"""
    if not path:
        return False
    norm_path = os.path.normcase(os.path.abspath(path))
    temp_root = os.path.normcase(os.path.abspath(tempfile.gettempdir()))
    if norm_path == temp_root or norm_path.startswith(temp_root + os.sep):
        return True
    parts = norm_path.split(os.sep)
    return any(p.startswith("onefile_") or p.startswith("_mei") for p in parts)


def get_real_executable_path() -> str:
    """一時展開ディレクトリではなく実際の実行ファイル(.exe)またはスクリプトのパスを返す"""
    parent_exe = _get_nuitka_onefile_parent_exe()
    if parent_exe and os.path.exists(parent_exe) and not is_temp_extraction_dir(parent_exe):
        return parent_exe
    if _INITIAL_ARGV0 and os.path.exists(_INITIAL_ARGV0) and not is_temp_extraction_dir(_INITIAL_ARGV0):
        return _INITIAL_ARGV0
    exe_path = os.path.abspath(sys.executable)
    if not is_temp_extraction_dir(exe_path):
        return exe_path
    return _INITIAL_ARGV0 or exe_path


def get_app_base_dir() -> str:
    """アプリの基準ディレクトリ(一時展開ディレクトリを除外した永続配置先)を返す"""
    if is_compiled_binary():
        real_exe = get_real_executable_path()
        exe_dir = os.path.dirname(real_exe)
        if exe_dir and os.path.isdir(exe_dir) and not is_temp_extraction_dir(exe_dir):
            return exe_dir
        appdata_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ValoReco")
        os.makedirs(appdata_dir, exist_ok=True)
        return appdata_dir

    module_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not is_temp_extraction_dir(module_root):
        return module_root
    return os.getcwd()


def _resolve_env_file() -> str:
    """実行形態に応じて一時フォルダに依存しない永続的な.envファイルパスを解決する"""
    appdata_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ValoReco")
    appdata_env = os.path.join(appdata_dir, ".env")

    if is_compiled_binary():
        os.makedirs(appdata_dir, exist_ok=True)
        # 実行ファイル配置先に既存の.envがありAppData側に未作成なら引き継ぐ
        base_dir = get_app_base_dir()
        candidate_env = os.path.join(base_dir, ".env")
        if (
            os.path.normcase(candidate_env) != os.path.normcase(appdata_env)
            and os.path.exists(candidate_env)
            and not os.path.exists(appdata_env)
        ):
            try:
                shutil.copy2(candidate_env, appdata_env)
            except OSError:
                pass
        return appdata_env

    base_dir = get_app_base_dir()
    project_env = os.path.join(base_dir, ".env")
    if os.path.exists(project_env) or os.access(base_dir, os.W_OK):
        return project_env

    os.makedirs(appdata_dir, exist_ok=True)
    return appdata_env


def _sanitize_path_value(raw_val: str) -> str:
    """環境変数から取得したパス文字列のクォート除去とエスケープ文字化け修復を行う"""
    if not raw_val:
        return ""
    cleaned = raw_val.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in ("'", '"'):
        cleaned = cleaned[1:-1].strip()
    # 過去のダブルクォート保存時に \v (\Videos, \ValoReco, \valorant_records) 等が制御文字化したケースを復元
    for ctrl_char, escaped_str in _CONTROL_CHAR_RECOVERY_MAP.items():
        if ctrl_char in cleaned:
            cleaned = cleaned.replace(ctrl_char, escaped_str)
    return os.path.normpath(cleaned) if cleaned else ""


class Config:
    _SAVE_LOCK = threading.RLock()

    def __init__(self):
        self.env_file = _resolve_env_file()
        if os.path.exists(self.env_file):
            load_dotenv(self.env_file, override=True)

        self.LANGUAGE = os.environ.get("LANGUAGE", "ja") or "ja"
        self.REGION = os.environ.get("VALORANT_REGION", "ap") or "ap"
        self.LIGHTWEIGHT_MODE = os.environ.get("LIGHTWEIGHT_MODE", "False").lower() == "true"
        self.AUTO_PERFORMANCE_CONTROL = os.environ.get("AUTO_PERFORMANCE_CONTROL", "True").lower() == "true"
        self.RIOT_ID = os.environ.get("RIOT_ID") or ""
        self.TAG_LINE = os.environ.get("TAG_LINE") or ""

        default_save_dir = os.path.join(os.path.expanduser("~"), "Videos", "ValoReco")
        raw_save_dir = _sanitize_path_value(os.environ.get("RECORD_SAVE_DIR", ""))
        if raw_save_dir and raw_save_dir not in ("./records", r".\records"):
            self.SAVE_DIR = os.path.normpath(os.path.abspath(raw_save_dir))
        elif raw_save_dir in ("./records", r".\records") and os.path.exists("./records"):
            self.SAVE_DIR = os.path.normpath(os.path.abspath("./records"))
        else:
            self.SAVE_DIR = os.path.normpath(os.path.abspath(default_save_dir))

        self.RECORD_VIDEO_FORMAT = os.environ.get("RECORD_VIDEO_FORMAT", "ddagrab")
        raw_input_source = (os.environ.get("RECORD_INPUT_SOURCE", "primary") or "").strip()
        # 既存の "desktop" 設定も単一のメインモニター("primary")として扱う
        self.RECORD_INPUT_SOURCE = "primary" if raw_input_source in ("", "desktop") else raw_input_source
        self.RECORD_AUDIO_SYSTEM = os.environ.get("RECORD_AUDIO_SYSTEM", "default")
        self.RECORD_AUDIO_SYSTEM_GAIN = os.environ.get("RECORD_AUDIO_SYSTEM_GAIN", "1.0")
        self.RECORD_AUDIO_MIC = os.environ.get("RECORD_AUDIO_MIC", "")
        self.RECORD_AUDIO_MIC_GAIN = os.environ.get("RECORD_AUDIO_MIC_GAIN", "1.0")
        self.RECORD_AUDIO_MIC_NOISE_GATE = os.environ.get("RECORD_AUDIO_MIC_NOISE_GATE", "0")
        self.RECORD_AUDIO_MIC_DENOISE = os.environ.get("RECORD_AUDIO_MIC_DENOISE", "None")
        pre_env = os.environ.get("RECORD_AUDIO_MIC_PREPROCESS", "SpeexDSP")
        self.RECORD_AUDIO_MIC_PREPROCESS = "SpeexDSP" if pre_env == "WebRTC" else pre_env
        self.RECORD_ENCODER = os.environ.get("RECORD_ENCODER", "h264_nvenc")
        self.RECORD_FPS = os.environ.get("RECORD_FPS", "60")
        self.RECORD_RESOLUTION = os.environ.get("RECORD_RESOLUTION", "1920x1080")
        try:
            self.AUTO_DELETE_DAYS = int(os.environ.get("AUTO_DELETE_DAYS", "0"))
        except (ValueError, TypeError):
            self.AUTO_DELETE_DAYS = 0

        clip_env = _sanitize_path_value(os.environ.get("CLIP_SAVE_DIR", ""))
        self.CLIP_SAVE_DIR = (
            os.path.normpath(os.path.abspath(clip_env))
            if clip_env
            else os.path.normpath(os.path.join(self.SAVE_DIR, "clips"))
        )
        self.UPDATE_API_URL = os.environ.get("UPDATE_API_URL") or "https://valoreco-api.meld-task.com/api/version"
        self.PLAYER_SYS_VOLUME = self._parse_volume(os.environ.get("PLAYER_SYS_VOLUME", "1.0"))
        self.PLAYER_MIC_VOLUME = self._parse_volume(os.environ.get("PLAYER_MIC_VOLUME", "1.0"))
        self.AUTO_START = os.environ.get("AUTO_START", "False").lower() == "true"
        self.TUTORIAL_COMPLETED = os.environ.get("TUTORIAL_COMPLETED", "False").lower() == "true"
        self.INITIAL_SETUP_COMPLETED = os.environ.get("INITIAL_SETUP_COMPLETED", "False").lower() == "true"

    def _parse_volume(self, val_str, default=1.0):
        try:
            val = float(val_str)
            # 過去バージョンで 0~100 などの整数として保存されていた場合の互換性対応
            if val > 2.0:
                return val / 100.0
            return val
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _format_env_value(val: str) -> str:
        # シングルクォートを含まない場合はエスケープ不要な単一引用符、含む場合は二重引用符で安全に囲む
        if "'" not in val:
            return f"'{val}'"
        escaped = val.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    def save(self):
        with self._SAVE_LOCK:
            self.SAVE_DIR = os.path.normpath(os.path.abspath(self.SAVE_DIR))
            self.CLIP_SAVE_DIR = os.path.normpath(os.path.abspath(self.CLIP_SAVE_DIR))

            # dotenvのバックスラッシュエスケープ解釈によるパス破損を防ぐためスラッシュ区切りで保存
            entries = {
                "LANGUAGE": str(self.LANGUAGE or "ja"),
                "VALORANT_REGION": str(self.REGION or "ap"),
                "LIGHTWEIGHT_MODE": str(self.LIGHTWEIGHT_MODE),
                "AUTO_PERFORMANCE_CONTROL": str(self.AUTO_PERFORMANCE_CONTROL),
                "RIOT_ID": str(self.RIOT_ID or ""),
                "TAG_LINE": str(self.TAG_LINE or ""),
                "RECORD_SAVE_DIR": self.SAVE_DIR.replace("\\", "/"),
                "RECORD_VIDEO_FORMAT": str(self.RECORD_VIDEO_FORMAT or "ddagrab"),
                "RECORD_INPUT_SOURCE": str(self.RECORD_INPUT_SOURCE or "primary"),
                "RECORD_AUDIO_SYSTEM": str(self.RECORD_AUDIO_SYSTEM or "default"),
                "RECORD_AUDIO_SYSTEM_GAIN": str(self.RECORD_AUDIO_SYSTEM_GAIN),
                "RECORD_AUDIO_MIC": str(self.RECORD_AUDIO_MIC or ""),
                "RECORD_AUDIO_MIC_GAIN": str(self.RECORD_AUDIO_MIC_GAIN),
                "RECORD_AUDIO_MIC_NOISE_GATE": str(self.RECORD_AUDIO_MIC_NOISE_GATE),
                "RECORD_AUDIO_MIC_DENOISE": str(self.RECORD_AUDIO_MIC_DENOISE or "None"),
                "RECORD_AUDIO_MIC_PREPROCESS": str(self.RECORD_AUDIO_MIC_PREPROCESS or "SpeexDSP"),
                "RECORD_ENCODER": str(self.RECORD_ENCODER or "h264_nvenc"),
                "RECORD_FPS": str(self.RECORD_FPS or "60"),
                "RECORD_RESOLUTION": str(self.RECORD_RESOLUTION or "1920x1080"),
                "AUTO_DELETE_DAYS": str(self.AUTO_DELETE_DAYS),
                "CLIP_SAVE_DIR": self.CLIP_SAVE_DIR.replace("\\", "/"),
                "UPDATE_API_URL": str(self.UPDATE_API_URL or ""),
                "PLAYER_SYS_VOLUME": str(self.PLAYER_SYS_VOLUME),
                "PLAYER_MIC_VOLUME": str(self.PLAYER_MIC_VOLUME),
                "AUTO_START": str(self.AUTO_START),
                "TUTORIAL_COMPLETED": str(self.TUTORIAL_COMPLETED),
                "INITIAL_SETUP_COMPLETED": str(self.INITIAL_SETUP_COMPLETED),
            }

            for k, v in entries.items():
                os.environ[k] = v

            env_dir = os.path.dirname(self.env_file)
            if env_dir:
                os.makedirs(env_dir, exist_ok=True)

            existing_lines = []
            if os.path.exists(self.env_file):
                try:
                    with open(self.env_file, "r", encoding="utf-8") as f:
                        existing_lines = f.read().splitlines()
                except OSError:
                    existing_lines = []

            key_pattern = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
            written_keys = set()
            new_lines = []

            for line in existing_lines:
                m = key_pattern.match(line)
                if m:
                    key_name = m.group(1)
                    if key_name in entries:
                        if key_name not in written_keys:
                            new_lines.append(f"{key_name}={self._format_env_value(entries[key_name])}")
                            written_keys.add(key_name)
                        continue
                new_lines.append(line)

            for k, v in entries.items():
                if k not in written_keys:
                    new_lines.append(f"{k}={self._format_env_value(v)}")

            content = "\n".join(new_lines) + "\n"
            tmp_file = f"{self.env_file}.tmp"
            try:
                with open(tmp_file, "w", encoding="utf-8") as f:
                    f.write(content)
                os.replace(tmp_file, self.env_file)
            except OSError:
                try:
                    if os.path.exists(tmp_file):
                        os.remove(tmp_file)
                except OSError:
                    pass
                try:
                    with open(self.env_file, "w", encoding="utf-8") as f:
                        f.write(content)
                except OSError as e:
                    logging.error(f"[Config] Failed to save config to {self.env_file}: {e}")
