import ctypes
import numpy as np
import os
import platform

class AudioProcessorWrapper:
    """
    C++で実装された音声処理パイプライン(HPF -> AI Denoise -> AGC)を
    Pythonから呼び出すためのラッパークラス。
    """
    def __init__(self, sample_rate: int, channels: int, dll_path: str = None):
        if dll_path is None:
            ext = ".dll" if platform.system() == "Windows" else ".so"
            # デフォルトのビルド出力パスを想定
            dll_path = os.path.join(os.path.dirname(__file__), "..", "cpp", "audio_processor", "build", f"audio_processor{ext}")
            
        if not os.path.exists(dll_path):
            raise FileNotFoundError(f"Audio processor library not found at: {dll_path}")
            
        self.lib = ctypes.CDLL(dll_path)
        
        # C APIのシグネチャ設定
        self.lib.AudioProcessor_Create.argtypes = [ctypes.c_int, ctypes.c_int]
        self.lib.AudioProcessor_Create.restype = ctypes.c_void_p
        
        self.lib.AudioProcessor_Process.argtypes = [
            ctypes.c_void_p, 
            np.ctypeslib.ndpointer(dtype=np.float32, ndim=1, flags='C_CONTIGUOUS'),
            np.ctypeslib.ndpointer(dtype=np.float32, ndim=1, flags='C_CONTIGUOUS'),
            ctypes.c_int
        ]
        self.lib.AudioProcessor_Process.restype = None
        
        self.lib.AudioProcessor_Destroy.argtypes = [ctypes.c_void_p]
        self.lib.AudioProcessor_Destroy.restype = None
        
        self.channels = channels
        self.processor_ptr = self.lib.AudioProcessor_Create(sample_rate, channels)

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
