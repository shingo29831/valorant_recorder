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
                return False
                
            # ロードテスト
            ctypes.CDLL(dll_path)
            return True
        except Exception:
            return False

    def __init__(self, sample_rate: int, channels: int, dll_path: str = None):
        if dll_path is None:
            ext = ".dll" if platform.system() == "Windows" else ".so"
            base_dir = os.path.join(os.path.dirname(__file__), "..", "cpp", "audio_processor", "build")
            
            # Windows (MSVC) の場合は Release フォルダの下に生成されることが多い
            if platform.system() == "Windows":
                release_path = os.path.join(base_dir, "Release", f"audio_processor{ext}")
                if os.path.exists(release_path):
                    dll_path = release_path
                else:
                    dll_path = os.path.join(base_dir, f"audio_processor{ext}")
            else:
                dll_path = os.path.join(base_dir, f"audio_processor{ext}")
            
        if not os.path.exists(dll_path):
            raise FileNotFoundError(f"Audio processor library not found at: {dll_path}")
            
        self.lib = ctypes.CDLL(dll_path)
        
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
        
        self.lib.AudioProcessor_Destroy.argtypes = [ctypes.c_void_p]
        self.lib.AudioProcessor_Destroy.restype = None
        
        self.channels = channels
        
        # モデルディレクトリのパスを解決 (DLLと同じディレクトリにある models フォルダ)
        model_dir = os.path.join(os.path.dirname(dll_path), "models")
        model_dir_bytes = model_dir.encode('utf-8') if os.path.exists(model_dir) else None
        
        self.processor_ptr = self.lib.AudioProcessor_Create(sample_rate, channels, model_dir_bytes)

    def set_preprocess_type(self, type_str: str):
        if type_str == "SpeexDSP":
            type_int = 1
        elif type_str == "WebRTC":
            type_int = 2
        else:
            type_int = 0
        self.lib.AudioProcessor_SetPreProcessType(self.processor_ptr, type_int)

    def process(self, input_data: np.ndarray) -> np.ndarray:
        """
        音声データを処理する
        input_data: shape=(frames, channels) または (frames * channels,) のfloat32配列
        """
        input_flat = np.ascontiguousarray(input_data.flatten(), dtype=np.float32)
        output_flat = np.zeros_like(input_flat)
        num_frames = len(input_flat) // self.channels
        
        self.lib.AudioProcessor_Process(self.processor_ptr, input_flat, output_flat, num_frames)
        
        if input_data.ndim == 2:
            return output_flat.reshape(-1, self.channels)
        return output_flat

    def __del__(self):
        if hasattr(self, 'processor_ptr') and self.processor_ptr:
            self.lib.AudioProcessor_Destroy(self.processor_ptr)
