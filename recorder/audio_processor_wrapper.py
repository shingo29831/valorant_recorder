import ctypes
from ctypes import wintypes
import logging
import numpy as np
import os
import platform
import sys
import tarfile
import threading
from typing import Callable, Optional
import urllib.request

DEEPFILTER_MODEL_URL = "https://github.com/Rikorose/DeepFilterNet/raw/main/models/DeepFilterNet3_onnx.tar.gz"
_MIN_MODEL_ARCHIVE_BYTES = 1024 * 1024
_MODEL_DOWNLOAD_TIMEOUT_SEC = 20
_MODEL_DOWNLOAD_LOCK = threading.Lock()
_VALIDATED_ARCHIVE_CACHE: dict[str, tuple[int, int, bool]] = {}
_VALIDATED_ARCHIVE_LOCK = threading.Lock()
_ASYNC_DOWNLOAD_LOCK = threading.Lock()
_ASYNC_DOWNLOAD_IN_PROGRESS = False
_ASYNC_DOWNLOAD_CALLBACKS: list[Callable[[str], None]] = []
_AUDIO_PROCESSOR_DLL_LOCK = threading.Lock()
_AUDIO_PROCESSOR_DLL_LOADED = False
_AUDIO_PROCESSOR_POLICY_BLOCKED = False
_WINERROR_APP_CONTROL_BLOCKED = 4551


def is_audio_processor_dll_loaded() -> bool:
    return _AUDIO_PROCESSOR_DLL_LOADED


def _get_search_base_dirs() -> list[str]:
    """PyInstaller/Nuitka/通常実行のすべてでリソース配置先を網羅する基準ディレクトリ一覧を返す"""
    candidates = []
    if hasattr(sys, "_MEIPASS"):
        candidates.append(sys._MEIPASS)
    module_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    candidates.append(module_root)
    try:
        from core.config import get_app_base_dir
        app_base = get_app_base_dir()
        if app_base:
            candidates.append(os.path.abspath(app_base))
    except Exception:
        pass
    seen = set()
    unique = []
    for c in candidates:
        norm = os.path.normcase(os.path.abspath(c))
        if norm not in seen and os.path.isdir(c):
            seen.add(norm)
            unique.append(c)
    return unique


def _get_download_target_paths(app_data_model: str) -> list[str]:
    """配布バイナリの一時展開先への保存を避け、永続保存先の候補リストを返す"""
    targets: list[str] = []
    try:
        from core.config import is_compiled_binary, is_temp_extraction_dir
        module_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        if not is_compiled_binary() and not is_temp_extraction_dir(module_root):
            targets.append(
                os.path.join(
                    module_root, "cpp", "audio_processor", "models", "DeepFilterNet3", "DeepFilterNet3_onnx.tar.gz"
                )
            )
    except Exception:
        pass
    targets.append(app_data_model)
    seen = set()
    unique = []
    for t in targets:
        norm = os.path.normcase(os.path.abspath(t))
        if norm not in seen:
            seen.add(norm)
            unique.append(t)
    return unique


def _is_valid_deepfilter_archive(path: str, use_cache: bool = True) -> bool:
    """tar.gzアーカイブの切り詰め・破損・HTMLエラーページを検証（成否ともstatキャッシュ）"""
    try:
        if not os.path.isfile(path):
            return False
        st = os.stat(path)
        norm = os.path.normcase(os.path.abspath(path))
        if st.st_size < _MIN_MODEL_ARCHIVE_BYTES:
            return False
        if use_cache:
            with _VALIDATED_ARCHIVE_LOCK:
                cached = _VALIDATED_ARCHIVE_CACHE.get(norm)
                if cached is not None and cached[0] == st.st_size and cached[1] == st.st_mtime_ns:
                    return cached[2]

        valid = False
        onnx_verified = False
        try:
            with tarfile.open(path, "r:gz") as tf:
                for member in tf:
                    if member.isfile() and member.name.lower().endswith(".onnx") and member.size > 0:
                        extracted = tf.extractfile(member)
                        if extracted is None or len(extracted.read(16)) == 0:
                            onnx_verified = False
                            break
                        onnx_verified = True
                valid = onnx_verified
        except Exception:
            valid = False

        with _VALIDATED_ARCHIVE_LOCK:
            _VALIDATED_ARCHIVE_CACHE[norm] = (st.st_size, st.st_mtime_ns, valid)
        return valid
    except Exception:
        return False


def _download_deepfilter_archive(target_path: str) -> bool:
    """タイムアウト付きストリーミング取得と整合性検証のうえで原子的配置する"""
    tmp_path = f"{target_path}.tmp.{os.getpid()}"
    try:
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        req = urllib.request.Request(DEEPFILTER_MODEL_URL, headers={"User-Agent": "ValoReco"})
        with urllib.request.urlopen(req, timeout=_MODEL_DOWNLOAD_TIMEOUT_SEC) as resp:
            with open(tmp_path, "wb") as out_f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    out_f.write(chunk)
        if not _is_valid_deepfilter_archive(tmp_path, use_cache=False):
            logging.warning(f"[DeepFilterNet] Downloaded archive failed integrity check: {tmp_path}")
            return False
        os.replace(tmp_path, target_path)
        _is_valid_deepfilter_archive(target_path, use_cache=False)
        logging.info(f"[DeepFilterNet] Model downloaded and verified: {target_path}")
        return True
    except Exception as e:
        logging.warning(f"[DeepFilterNet] Model download failed for {target_path}: {e}")
        return False
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def find_or_download_deepfilter_model(allow_download: bool = False) -> str:
    """DeepFilterNet3モデルアーカイブ(.tar.gz)を探索し、必要時は永続領域へ安全にダウンロードする"""
    base_dirs = _get_search_base_dirs()
    app_data_model = os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
        "ValoReco",
        "models",
        "DeepFilterNet3",
        "DeepFilterNet3_onnx.tar.gz",
    )

    candidate_paths = []
    for bdir in base_dirs:
        candidate_paths.extend([
            os.path.join(bdir, "cpp", "audio_processor", "models", "DeepFilterNet3", "DeepFilterNet3_onnx.tar.gz"),
            os.path.join(bdir, "cpp", "audio_processor", "DeepFilterNet3_onnx.tar.gz"),
            os.path.join(bdir, "models", "DeepFilterNet3", "DeepFilterNet3_onnx.tar.gz"),
            os.path.join(bdir, "models", "DeepFilterNet3_onnx.tar.gz"),
            os.path.join(bdir, "cpp", "audio_processor", "models", "DeepFilterNet3_onnx.tar.gz"),
        ])
    candidate_paths.append(app_data_model)

    for p in candidate_paths:
        if _is_valid_deepfilter_archive(p):
            return get_short_path_name(os.path.abspath(p)).replace("\\", "/")

    # 無関係な大量ファイルの走査を避けるためcppとmodelsサブディレクトリのみ探索
    for bdir in base_dirs:
        for sub in (os.path.join(bdir, "cpp"), os.path.join(bdir, "models")):
            if not os.path.isdir(sub):
                continue
            for root, _, files in os.walk(sub):
                for f in files:
                    if f.endswith(".tar.gz") and "deepfilter" in f.lower():
                        full_p = os.path.join(root, f)
                        if _is_valid_deepfilter_archive(full_p):
                            return get_short_path_name(os.path.abspath(full_p)).replace("\\", "/")

    if allow_download:
        with _MODEL_DOWNLOAD_LOCK:
            targets = _get_download_target_paths(app_data_model)
            for target_path in targets:
                if _is_valid_deepfilter_archive(target_path):
                    return get_short_path_name(os.path.abspath(target_path)).replace("\\", "/")
            for target_path in targets:
                if _download_deepfilter_archive(target_path):
                    return get_short_path_name(os.path.abspath(target_path)).replace("\\", "/")
    return ""


def ensure_deepfilter_model_async(on_ready: Optional[Callable[[str], None]] = None) -> None:
    """UI・音声ループをブロックしない専用デーモンスレッドでDeepFilterNetモデルを事前取得する"""
    global _ASYNC_DOWNLOAD_IN_PROGRESS
    existing = find_or_download_deepfilter_model(allow_download=False)
    if existing:
        if on_ready is not None:
            try:
                on_ready(existing)
            except Exception:
                pass
        return

    with _ASYNC_DOWNLOAD_LOCK:
        if on_ready is not None:
            _ASYNC_DOWNLOAD_CALLBACKS.append(on_ready)
        if _ASYNC_DOWNLOAD_IN_PROGRESS:
            return
        _ASYNC_DOWNLOAD_IN_PROGRESS = True

    def _worker() -> None:
        global _ASYNC_DOWNLOAD_IN_PROGRESS
        model_path = ""
        try:
            model_path = find_or_download_deepfilter_model(allow_download=True)
        except Exception as e:
            logging.warning(f"[DeepFilterNet] Background model download failed: {e}")
        finally:
            with _ASYNC_DOWNLOAD_LOCK:
                callbacks = list(_ASYNC_DOWNLOAD_CALLBACKS)
                _ASYNC_DOWNLOAD_CALLBACKS.clear()
                _ASYNC_DOWNLOAD_IN_PROGRESS = False
        if model_path:
            for cb in callbacks:
                try:
                    cb(model_path)
                except Exception:
                    pass

    threading.Thread(target=_worker, name="DeepFilterModelDownloader", daemon=True).start()


def _find_audio_processor_dll(dll_path: Optional[str] = None) -> Optional[str]:
    if dll_path is not None:
        return dll_path if os.path.exists(dll_path) else None
    ext = ".dll" if platform.system() == "Windows" else ".so"
    for root_dir in _get_search_base_dirs():
        possible_paths = [
            os.path.join(root_dir, "cpp", "audio_processor", "build", "Release", f"audio_processor{ext}"),
            os.path.join(root_dir, "cpp", "audio_processor", "build", f"audio_processor{ext}"),
            os.path.join(root_dir, "bin", f"audio_processor{ext}"),
            os.path.join(root_dir, f"audio_processor{ext}"),
        ]
        for p in possible_paths:
            if os.path.exists(p):
                return p
    return None


def _load_audio_processor_dll(dll_path: str) -> ctypes.CDLL:
    global _AUDIO_PROCESSOR_DLL_LOADED, _AUDIO_PROCESSOR_POLICY_BLOCKED
    with _AUDIO_PROCESSOR_DLL_LOCK:
        if platform.system() == "Windows":
            short_dll_dir = get_short_path_name(os.path.dirname(dll_path))
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(short_dll_dir)
            for dep_name in ("deep_filter.dll", "libspeexdsp.dll"):
                dep_path = os.path.join(short_dll_dir, dep_name)
                if os.path.exists(dep_path):
                    try:
                        ctypes.CDLL(dep_path, winmode=0)
                    except OSError as e:
                        if getattr(e, "winerror", None) == _WINERROR_APP_CONTROL_BLOCKED:
                            _AUDIO_PROCESSOR_POLICY_BLOCKED = True
                            raise
                        logging.warning(f"[AudioProcessorWrapper] Failed to preload {dep_name}: {e}")
                    except Exception as e:
                        logging.warning(f"[AudioProcessorWrapper] Failed to preload {dep_name}: {e}")
        try:
            lib = ctypes.CDLL(get_short_path_name(dll_path), winmode=0)
        except OSError as e:
            if getattr(e, "winerror", None) == _WINERROR_APP_CONTROL_BLOCKED:
                _AUDIO_PROCESSOR_POLICY_BLOCKED = True
            raise
        _AUDIO_PROCESSOR_DLL_LOADED = True
        return lib

def get_short_path_name(long_name: str) -> str:
    """
    Windowsの短いパス名(8.3形式)を取得する。
    非ASCII文字(日本語など)を含むパスをC/Rustライブラリに渡す際のエラーを回避する。
    """
    if platform.system() != "Windows":
        return long_name
    try:
        _GetShortPathNameW = ctypes.windll.kernel32.GetShortPathNameW
        _GetShortPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        _GetShortPathNameW.restype = wintypes.DWORD
        
        output_buf_size = _GetShortPathNameW(long_name, None, 0)
        if output_buf_size == 0:
            return long_name
            
        output_buf = ctypes.create_unicode_buffer(output_buf_size)
        success = _GetShortPathNameW(long_name, output_buf, output_buf_size)
        if success > 0:
            return output_buf.value
    except Exception:
        pass
    return long_name

class AudioProcessorWrapper:
    """
    C++で実装された音声処理パイプライン(HPF -> AI Denoise -> AGC)を
    Pythonから呼び出すためのラッパークラス。
    """
    @classmethod
    def is_available(cls) -> bool:
        with _AUDIO_PROCESSOR_DLL_LOCK:
            if _AUDIO_PROCESSOR_DLL_LOADED:
                return True
            if _AUDIO_PROCESSOR_POLICY_BLOCKED:
                return False
        try:
            # main.py非経由の直接呼び出しでも未ロード時1回だけリビルドを済ませてからロード
            try:
                from recorder.native_bridge import try_rebuild_native_recorder
                try_rebuild_native_recorder()
            except Exception:
                pass
            dll_path = _find_audio_processor_dll()
            if not dll_path:
                return False
            _load_audio_processor_dll(dll_path)
            return True
        except Exception as e:
            logging.warning(f"[AudioProcessorWrapper] Failed to load DLL: {e}")
            return False

    def __init__(self, sample_rate: int, channels: int, dll_path: str = None):
        self.processor_ptr = None
        self.lib = None
        self.sample_rate = sample_rate
        self.channels = channels
        self._preprocess_type = 1
        self._denoise_type = 0
        self._df_loaded = False
        self._model_path = b""
        self._failed_model_paths: set[bytes] = set()
        self._lock = threading.RLock()
        self._load_lock = threading.Lock()

        resolved_dll = _find_audio_processor_dll(dll_path)
        if not resolved_dll:
            raise FileNotFoundError(f"AudioProcessor DLL not found. Searched in {_get_search_base_dirs()}")

        self.lib = _load_audio_processor_dll(resolved_dll)

        # C APIのシグネチャ設定
        self.lib.AudioProcessor_Create.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p]
        self.lib.AudioProcessor_Create.restype = ctypes.c_void_p

        self.lib.AudioProcessor_Process.argtypes = [
            ctypes.c_void_p,
            np.ctypeslib.ndpointer(dtype=np.float32, ndim=1, flags='C_CONTIGUOUS'),
            np.ctypeslib.ndpointer(dtype=np.float32, ndim=1, flags='C_CONTIGUOUS'),
            ctypes.c_int
        ]
        self.lib.AudioProcessor_Process.restype = None

        self.lib.AudioProcessor_SetPreProcessType.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.lib.AudioProcessor_SetPreProcessType.restype = None

        self.lib.AudioProcessor_SetDenoiseType.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.lib.AudioProcessor_SetDenoiseType.restype = None

        if hasattr(self.lib, "AudioProcessor_LoadDeepFilter"):
            self.lib.AudioProcessor_LoadDeepFilter.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
            self.lib.AudioProcessor_LoadDeepFilter.restype = ctypes.c_int

        if hasattr(self.lib, "AudioProcessor_IsDeepFilterLoaded"):
            self.lib.AudioProcessor_IsDeepFilterLoaded.argtypes = [ctypes.c_void_p]
            self.lib.AudioProcessor_IsDeepFilterLoaded.restype = ctypes.c_int

        if hasattr(self.lib, "AudioProcessor_IsDeepFilterActive"):
            self.lib.AudioProcessor_IsDeepFilterActive.argtypes = [ctypes.c_void_p]
            self.lib.AudioProcessor_IsDeepFilterActive.restype = ctypes.c_int

        self.lib.AudioProcessor_Destroy.argtypes = [ctypes.c_void_p]
        self.lib.AudioProcessor_Destroy.restype = None

        found_model = find_or_download_deepfilter_model(allow_download=False)
        if found_model:
            self._model_path = found_model.encode("utf-8")
            logging.info(f"[AudioProcessorWrapper] Found DeepFilterNet model archive at: {found_model}")
        else:
            logging.info("[AudioProcessorWrapper] DeepFilterNet model archive not found locally; will fetch asynchronously.")

        # 初期状態はDeepFilterNetモデルをロードせず軽量に生成し、DeepFilterNet選択時のみ遅延ロードする
        self.processor_ptr = self.lib.AudioProcessor_Create(sample_rate, channels, b"")
        if not self.processor_ptr:
            logging.error("[AudioProcessorWrapper] Failed to initialize AudioProcessor in C++ (returned null pointer).")

    def _is_df_loaded_locked(self) -> bool:
        if not self.processor_ptr:
            return False
        if hasattr(self.lib, "AudioProcessor_IsDeepFilterLoaded"):
            return bool(self.lib.AudioProcessor_IsDeepFilterLoaded(self.processor_ptr))
        return self._df_loaded

    def _is_df_active_locked(self) -> bool:
        if not self.processor_ptr:
            return False
        if hasattr(self.lib, "AudioProcessor_IsDeepFilterActive"):
            return bool(self.lib.AudioProcessor_IsDeepFilterActive(self.processor_ptr))
        return self._df_loaded and self._denoise_type == 1

    def _ensure_df_loaded_serialized(self) -> None:
        """ロード専用ロックでTOCTOU二重ロードと失敗モデルの無限再試行を防ぎつつHPF状態を維持してロード"""
        with self._load_lock:
            with self._lock:
                if not self.processor_ptr or self._is_df_loaded_locked():
                    return
                model_path_bytes = self._model_path

            if not model_path_bytes:
                local_model = find_or_download_deepfilter_model(allow_download=False)
                if local_model:
                    model_path_bytes = local_model.encode("utf-8")
                else:
                    # ホットパスでの同期HTTP通信を避け、専用デーモンスレッドで事前取得
                    ensure_deepfilter_model_async()
                    return

            if model_path_bytes in self._failed_model_paths:
                return

            with self._lock:
                if not self.processor_ptr or self._is_df_loaded_locked():
                    return
                self._model_path = model_path_bytes
                if hasattr(self.lib, "AudioProcessor_LoadDeepFilter"):
                    loaded_ok = bool(self.lib.AudioProcessor_LoadDeepFilter(self.processor_ptr, model_path_bytes))
                    self._df_loaded = loaded_ok and self._is_df_loaded_locked()
                else:
                    new_ptr = self.lib.AudioProcessor_Create(self.sample_rate, self.channels, model_path_bytes)
                    if new_ptr:
                        old_ptr = self.processor_ptr
                        self.processor_ptr = new_ptr
                        self.lib.AudioProcessor_SetPreProcessType(self.processor_ptr, self._preprocess_type)
                        self.lib.AudioProcessor_Destroy(old_ptr)
                        self._df_loaded = self._is_df_loaded_locked()
                    else:
                        self._df_loaded = False

                if not self._df_loaded:
                    self._failed_model_paths.add(model_path_bytes)
                    logging.warning(
                        "[AudioProcessorWrapper] DeepFilterNet failed to load; suppressing retries for this path."
                    )

    def try_activate_deepfilter_if_ready(self) -> bool:
        """非同期ダウンロード完了後にDeepFilterNet選択中なら自動でロード・有効化する"""
        with self._lock:
            if self._denoise_type != 1 or not self.processor_ptr or self._is_df_loaded_locked():
                return self._is_df_active_locked()
        self._ensure_df_loaded_serialized()
        with self._lock:
            if self.processor_ptr and self._denoise_type == 1:
                self.lib.AudioProcessor_SetDenoiseType(self.processor_ptr, 1)
                return self._is_df_active_locked()
        return False

    def set_preprocess_type(self, type_str: str):
        if type_str == "SpeexDSP":
            type_int = 1
        elif type_str == "WebRTC":
            type_int = 2
        else:
            type_int = 0
        with self._lock:
            self._preprocess_type = type_int
            if self.processor_ptr:
                self.lib.AudioProcessor_SetPreProcessType(self.processor_ptr, type_int)

    def set_denoise_type(self, type_str: str):
        t_lower = str(type_str).lower()
        if "deepfilter" in t_lower:
            type_int = 1
        elif "rnnoise" in t_lower or "speex" in t_lower:
            type_int = 2
        else:
            type_int = 0

        if type_int == 1:
            self._ensure_df_loaded_serialized()

        with self._lock:
            if not self.processor_ptr:
                return
            self._denoise_type = type_int
            self.lib.AudioProcessor_SetDenoiseType(self.processor_ptr, type_int)

    def process(self, input_data: np.ndarray) -> np.ndarray:
        """
        音声データを処理する
        input_data: shape=(frames, channels) または (frames * channels,) のfloat32配列
        """
        with self._lock:
            if not self.processor_ptr:
                return input_data.copy()

            input_flat = np.ascontiguousarray(input_data.flatten(), dtype=np.float32)
            output_flat = np.zeros_like(input_flat)
            num_frames = len(input_flat) // self.channels

            self.lib.AudioProcessor_Process(self.processor_ptr, input_flat, output_flat, num_frames)

        if input_data.ndim == 2:
            return output_flat.reshape(-1, self.channels)
        return output_flat

    def __del__(self):
        lock = getattr(self, "_lock", None)
        if lock is not None:
            with lock:
                if getattr(self, "processor_ptr", None) and getattr(self, "lib", None):
                    self.lib.AudioProcessor_Destroy(self.processor_ptr)
                    self.processor_ptr = None
        elif getattr(self, "processor_ptr", None) and getattr(self, "lib", None):
            self.lib.AudioProcessor_Destroy(self.processor_ptr)
            self.processor_ptr = None
