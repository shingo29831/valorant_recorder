import ctypes
import numpy as np
import os
import platform

class AudioProcessorWrapper:
    """
    C++で実装された音声処理パイプライン(HPF -> AI Denoise -> AGC)を
    Pythonから呼び出すためのラッパークラス。
    """
    @classmethod
    def is_available(cls) -> bool:
        try:
            ext = ".dll" if platform.system() == "Windows" else ".so"
            base_dir = os.path.join(os.path.dirname(__file__), "..", "cpp", "audio_processor", "build")
            
            if platform.system() == "Windows":
                release_path = os.path.join(base_dir, "Release", f"audio_processor{ext}")
                if os.path.exists(release_path):
                    dll_path = release_path
                else:
                    dll_path = os.path.join(base_dir, f"audio_processor{ext}")
            else:
                dll_path = os.path.join(base_dir, f"audio_processor{ext}")
                
            if not os.path.exists(dll_path):
                print(f"[AudioProcessorWrapper] DLL not found at: {dll_path}")
                return False
                
            # ロードテスト (依存DLLを解決するために winmode=0 を指定)
            if platform.system() == "Windows":
                if hasattr(os, 'add_dll_directory'):
                    os.add_dll_directory(os.path.dirname(dll_path))
                # 依存DLLを明示的にプリロードしてロードエラーを防ぐ
                try:
                    ctypes.CDLL(os.path.join(os.path.dirname(dll_path), "deep_filter.dll"), winmode=0)
                    ctypes.CDLL(os.path.join(os.path.dirname(dll_path), "libspeexdsp.dll"), winmode=0)
                except Exception:
                    pass
                    
            ctypes.CDLL(dll_path, winmode=0)
            return True
        except Exception as e:
            print(f"[AudioProcessorWrapper] Failed to load DLL: {e}")
            return False

    def __init__(self, sample_rate: int, channels: int, dll_path: str = None):
        self.processor = None
        self.lib = None
        self.channels = channels
        
        import sys
        # PyInstaller実行時のリソースパス(リリース版対応)
        is_frozen = getattr(sys, 'frozen', False)
        if is_frozen:
            base_dir = sys._MEIPASS
        else:
            base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        
        # DLLパスの決定
        if dll_path is None:
            if platform.system() == "Windows":
                possible_paths = [
                    os.path.join(base_dir, "cpp", "audio_processor", "build", "Release", "audio_processor.dll"),
                    os.path.join(base_dir, "cpp", "audio_processor", "build", "audio_processor.dll"),
                    os.path.join(base_dir, "bin", "audio_processor.dll"),
                    os.path.join(base_dir, "audio_processor.dll")
                ]
                for p in possible_paths:
                    if os.path.exists(p):
                        dll_path = p
                        break
            else:
                dll_path = os.path.join(base_dir, "cpp", "audio_processor", "build", "audio_processor.so")

        if dll_path is None or not os.path.exists(dll_path):
            raise FileNotFoundError(f"AudioProcessor DLL not found. Searched in {base_dir}")

        # DLLのロード
        if platform.system() == "Windows":
            if hasattr(os, 'add_dll_directory'):
                os.add_dll_directory(os.path.dirname(dll_path))
            # 依存DLLを明示的にプリロード
            try:
                ctypes.CDLL(os.path.join(os.path.dirname(dll_path), "deep_filter.dll"), winmode=0)
                ctypes.CDLL(os.path.join(os.path.dirname(dll_path), "libspeexdsp.dll"), winmode=0)
            except Exception:
                pass
                
        self.lib = ctypes.CDLL(dll_path, winmode=0)

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

        self.lib.AudioProcessor_Destroy.argtypes = [ctypes.c_void_p]
        self.lib.AudioProcessor_Destroy.restype = None

        # DeepFilterNet C API (df_create) は .tar.gz ファイルの直接のパスを要求する
        possible_model_tar_paths = [
            os.path.join(base_dir, "models", "DeepFilterNet3", "DeepFilterNet3_onnx.tar.gz"),
            os.path.join(base_dir, "cpp", "audio_processor", "models", "DeepFilterNet3", "DeepFilterNet3_onnx.tar.gz"),
            os.path.join(base_dir, "models", "DeepFilterNet3_onnx.tar.gz"),
            os.path.join(base_dir, "cpp", "audio_processor", "models", "DeepFilterNet3_onnx.tar.gz"),
        ]
        
        model_path_to_use = b""
        for p in possible_model_tar_paths:
            if os.path.isfile(p) and os.path.getsize(p) > 1024:
                abs_path = os.path.abspath(p).replace('\\', '/')
                model_path_to_use = abs_path.encode('utf-8')
                print(f"[AudioProcessorWrapper] Found DeepFilterNet model archive at: {abs_path}")
                break
                
        if not model_path_to_use:
            # ディレクトリ内から再帰的に tar.gz を探索
            search_dirs = [
                os.path.join(base_dir, "models"),
                os.path.join(base_dir, "cpp", "audio_processor", "models"),
            ]
            for s_dir in search_dirs:
                if os.path.exists(s_dir):
                    for root, _, files in os.walk(s_dir):
                        for f in files:
                            if f.endswith(".tar.gz") and "deepfilter" in f.lower():
                                full_p = os.path.join(root, f)
                                if os.path.getsize(full_p) > 1024:
                                    abs_path = os.path.abspath(full_p).replace('\\', '/')
                                    model_path_to_use = abs_path.encode('utf-8')
                                    print(f"[AudioProcessorWrapper] Found DeepFilterNet model archive at: {abs_path}")
                                    break
                        if model_path_to_use:
                            break
                if model_path_to_use:
                    break

        if not model_path_to_use:
            print("[AudioProcessorWrapper] Warning: DeepFilterNet model archive (.tar.gz) not found. AI Denoise will be disabled.")
            
        self.processor_ptr = self.lib.AudioProcessor_Create(sample_rate, channels, model_path_to_use)
        if not self.processor_ptr:
            raise RuntimeError("Failed to initialize AudioProcessor in C++ (returned null pointer).")

    def set_preprocess_type(self, type_str: str):
        if type_str == "SpeexDSP":
            type_int = 1
        elif type_str == "WebRTC":
            type_int = 2
        else:
            type_int = 0
        self.lib.AudioProcessor_SetPreProcessType(self.processor_ptr, type_int)

    def set_denoise_type(self, type_str: str):
        if type_str == "DeepFilterNet":
            type_int = 1
        else:
            type_int = 0
        self.lib.AudioProcessor_SetDenoiseType(self.processor_ptr, type_int)

    def process(self, input_data: np.ndarray) -> np.ndarray:
        """
        音声データを処理する
        input_data: shape=(frames, channels) または (frames * channels,) のfloat32配列
        """
        input_flat = np.ascontiguousarray(input_data.flatten(), dtype=np.float32)
        output_flat = np.zeros_like(input_flat)
        num_frames = len(input_flat) // self.channels
        
        # C++側で既に10ms(480サンプル)ごとのループ処理が実装されているため、
        # Python側でのチャンク分割は廃止し、一度に渡して呼び出しオーバーヘッド(処理落ち)を防ぐ
        self.lib.AudioProcessor_Process(self.processor_ptr, input_flat, output_flat, num_frames)
            
        if input_data.ndim == 2:
            return output_flat.reshape(-1, self.channels)
        return output_flat

    def __del__(self):
        if hasattr(self, 'processor_ptr') and self.processor_ptr:
            self.lib.AudioProcessor_Destroy(self.processor_ptr)
