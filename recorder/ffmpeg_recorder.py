import subprocess
import os
import ctypes
import logging
from datetime import datetime
from core.config import Config
from recorder.ffmpeg_downloader import ensure_ffmpeg_downloaded
from recorder.encoder_utils import test_encoder, get_available_encoders

class RecorderConfig(ctypes.Structure):
    _fields_ = [
        ("mic_gain", ctypes.c_float),
        ("system_gain", ctypes.c_float),
        ("noise_gate_threshold", ctypes.c_float),
        ("denoise_mode", ctypes.c_int32),
        ("preprocess_mode", ctypes.c_int32),
        ("mic_device_name", ctypes.c_char_p),
        ("ffmpeg_command", ctypes.c_char_p),
        ("save_file_path", ctypes.c_char_p),
        ("temp_file_path", ctypes.c_char_p),
        ("ffmpeg_path", ctypes.c_char_p),
        ("df_model_path", ctypes.c_char_p),
    ]

class FFmpegRecorder:
    def __init__(self, config: Config):
        self.config = config
        self.current_filepath = None
        self.temp_filepath = None
        
        app_data_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'ValoReco')
        os.makedirs(app_data_dir, exist_ok=True)
        self.ffmpeg_path = ensure_ffmpeg_downloaded(app_data_dir)
        self.actual_encoder = self._determine_encoder()
        
        # Load NativeRecorder DLL
        from recorder.audio_processor_wrapper import get_short_path_name
        base_build_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "cpp", "audio_processor", "build"))
        possible_paths = [
            os.path.join(base_build_dir, "Release", "native_recorder.dll"),
            os.path.join(base_build_dir, "native_recorder.dll")
        ]
        
        dll_path = None
        for p in possible_paths:
            if os.path.exists(p):
                dll_path = p
                break
                
        if not dll_path:
            raise FileNotFoundError(f"native_recorder.dll not found in {base_build_dir}. Please ensure C++ build was successful.")

        dll_dir = os.path.dirname(dll_path)
        
        if hasattr(os, 'add_dll_directory'):
            os.add_dll_directory(dll_dir)
            
        # winmode=8 (LOAD_WITH_ALTERED_SEARCH_PATH) を使用し、DLLと同じディレクトリから依存関係を解決させる
        LOAD_WITH_ALTERED_SEARCH_PATH = 8
        
        # 依存DLLを個別にプリロードし、失敗した場合は詳細なログを残す
        for dep_dll in ["deep_filter.dll", "libspeexdsp.dll", "audio_processor.dll"]:
            dep_path = os.path.join(dll_dir, dep_dll)
            if os.path.exists(dep_path):
                try:
                    ctypes.CDLL(dep_path, winmode=LOAD_WITH_ALTERED_SEARCH_PATH)
                except Exception as e:
                    logging.warning(f"[FFmpegRecorder] Failed to preload {dep_dll}: {e}")
            else:
                logging.warning(f"[FFmpegRecorder] Dependency not found: {dep_path}")
            
        try:
            self.native_lib = ctypes.CDLL(dll_path, winmode=LOAD_WITH_ALTERED_SEARCH_PATH)
        except Exception as e:
            logging.error(f"[FFmpegRecorder] Failed to load native_recorder.dll: {e}")
            raise RuntimeError(f"Failed to load native recorder module: {e}")
        
        self.native_lib.Recorder_Start.argtypes = [ctypes.POINTER(RecorderConfig)]
        self.native_lib.Recorder_Start.restype = ctypes.c_bool
        self.native_lib.Recorder_Stop.argtypes = []
        self.native_lib.Recorder_IsRunning.restype = ctypes.c_bool
        self.native_lib.Recorder_GetLastError.restype = ctypes.c_char_p

    def _determine_encoder(self) -> str:
        encoder = self.config.RECORD_ENCODER
        success, _ = test_encoder(self.ffmpeg_path, encoder)
        if success:
            return encoder
        
        # 設定されたエンコーダが使えない場合（グラボ変更など）、利用可能な最適なものを返す
        available, _ = get_available_encoders(self.ffmpeg_path)
        return available[0]

    def start_recording(self) -> str:
        if self.native_lib.Recorder_IsRunning():
            return self.current_filepath

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"match_record_{timestamp}.mp4"
        temp_filename = f"match_record_{timestamp}.mkv"
        self.current_filepath = os.path.join(self.config.SAVE_DIR, filename)
        self.temp_filepath = os.path.join(self.config.SAVE_DIR, temp_filename)
        os.makedirs(self.config.SAVE_DIR, exist_ok=True)

        is_nvenc = "nvenc" in self.actual_encoder.lower()
        preset = "p1" if is_nvenc else "ultrafast"
        tune = "ull" if is_nvenc else "zerolatency"

        cmd = [
            self.ffmpeg_path,
            "-y",
        ]
        
        # GPUエンコーダではD3D11テクスチャをCPUに落とさず直結してカクつきを完全防止
        cmd.extend([
            "-thread_queue_size", "1024",
            "-f", "lavfi",
            "-i", f"ddagrab=framerate={self.config.RECORD_FPS}:draw_mouse=1"
        ])
        
        audio_filter = (
            "[1:a]asetpts=N/SR/TB,asplit=3[mix_in][sys_in][mic_in];"
            "[mix_in]pan=stereo|FL=c0|FR=c1,alimiter=limit=0.99[a_mixed];"
            "[sys_in]pan=stereo|FL=c2|FR=c3,alimiter=limit=0.99[a0_out];"
            "[mic_in]pan=stereo|FL=0.5*c4+0.5*c5|FR=0.5*c4+0.5*c5,alimiter=limit=0.95:attack=5:release=50[a1_out]"
        )

        video_map = "0:v"
        filter_complex = audio_filter

        # ソフトウェアエンコーダのみCPUへのhwdownloadが必要
        if not is_nvenc and "amf" not in self.actual_encoder.lower():
            filter_complex = (
                f"[0:v]hwdownload,format=bgra,format=yuv420p,setpts=PTS-STARTPTS[v_out];"
                + audio_filter
            )
            video_map = "[v_out]"

        cmd.extend([
            "-thread_queue_size", "1024",
            "-f", "f32le",
            "-ar", "48000",
            "-ac", "6",
            "-i", "pipe:0",
            "-filter_complex", filter_complex,
            "-map", video_map,
            "-map", "[a_mixed]",
            "-map", "[a0_out]",
            "-map", "[a1_out]",
            "-c:v", self.actual_encoder,
            "-preset", preset,
            "-tune", tune,
            "-b:v", "10M",
            "-pix_fmt", "nv12" if is_nvenc else "yuv420p",
            "-r", str(self.config.RECORD_FPS),
            "-g", str(self.config.RECORD_FPS),
            "-keyint_min", str(self.config.RECORD_FPS),
            "-delay", "0",
            "-zerolatency", "1",
            "-c:a", "aac",
            "-b:a", "192k",
            self.temp_filepath
        ])

        cmd_str = subprocess.list2cmdline(cmd)
        
        report_path = os.path.join(self.config.SAVE_DIR, "ffmpeg_crash_report.log").replace('\\', '/')
        os.environ["FFREPORT"] = f"file='{report_path}':level=48"
        logging.info(f"[FFmpegRecorder] Set FFREPORT to {report_path}")
        
        gate_level = float(getattr(self.config, 'RECORD_AUDIO_MIC_NOISE_GATE', '0')) / 100.0
        denoise_str = str(getattr(self.config, 'RECORD_AUDIO_MIC_DENOISE', 'None'))
        denoise_mode = 1 if denoise_str == 'DeepFilterNet' else 0
        preprocess_str = str(getattr(self.config, 'RECORD_AUDIO_MIC_PREPROCESS', 'SpeexDSP'))
        preprocess_mode = 1 if preprocess_str == 'SpeexDSP' else 0

        config_struct = RecorderConfig()
        config_struct.mic_gain = float(getattr(self.config, 'RECORD_AUDIO_MIC_GAIN', 100)) / 100.0
        config_struct.system_gain = float(getattr(self.config, 'RECORD_AUDIO_SYS_GAIN', 100)) / 100.0
        config_struct.noise_gate_threshold = gate_level
        config_struct.denoise_mode = denoise_mode
        config_struct.preprocess_mode = preprocess_mode
        
        mic_device = getattr(self.config, 'RECORD_AUDIO_MIC_DEVICE', 'Default')
        config_struct.mic_device_name = mic_device.encode('utf-8') if mic_device != 'Default' else b""
        config_struct.ffmpeg_command = cmd_str.encode('utf-8')
        config_struct.save_file_path = self.current_filepath.encode('utf-8')
        config_struct.temp_file_path = self.temp_filepath.encode('utf-8')
        config_struct.ffmpeg_path = self.ffmpeg_path.encode('utf-8')
        
        # DeepFilterNetのモデルパスを解決
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        possible_model_paths = [
            os.path.join(project_root, "cpp", "audio_processor", "DeepFilterNet3_onnx.tar.gz"),
            os.path.join(project_root, "models", "DeepFilterNet3", "DeepFilterNet3_onnx.tar.gz"),
            os.path.join(project_root, "models", "DeepFilterNet3_onnx.tar.gz"),
        ]
        df_model_path = ""
        for p in possible_model_paths:
            if os.path.isfile(p):
                df_model_path = p
                break
                
        if not df_model_path:
            # 見つからない場合は再帰探索
            for root, _, files in os.walk(project_root):
                for f in files:
                    if f.endswith(".tar.gz") and "deepfilter" in f.lower():
                        df_model_path = os.path.join(root, f)
                        break
                if df_model_path:
                    break

        # 短いパスに変換（日本語パス対策）
        from recorder.audio_processor_wrapper import get_short_path_name
        if df_model_path:
            df_model_path = get_short_path_name(df_model_path).replace('\\', '/')
            
        config_struct.df_model_path = df_model_path.encode('utf-8')
        
        logging.info(f"[FFmpegRecorder] Starting NativeRecorder with command: {cmd_str}")
        
        if not self.native_lib.Recorder_Start(ctypes.byref(config_struct)):
            err = self.native_lib.Recorder_GetLastError().decode('utf-8')
            raise RuntimeError(f"NativeRecorder failed to start: {err}")
            
        return self.current_filepath

    def is_recording(self) -> bool:
        return self.native_lib.Recorder_IsRunning()

    def stop_recording(self):
        logging.info("[FFmpegRecorder] Stopping NativeRecorder...")
        self.native_lib.Recorder_Stop()
        
        # MKVからMP4への再多重化 (Remux)
        # クラッシュ時でもMKVは正常な状態が保たれるため、ここでMP4に変換することで破損を防ぐ
        if self.temp_filepath and os.path.exists(self.temp_filepath):
            try:
                logging.info(f"[FFmpegRecorder] Remuxing MKV to MP4: {self.temp_filepath} -> {self.current_filepath}")
                remux_cmd = [
                    self.ffmpeg_path,
                    "-y",
                    "-i", self.temp_filepath,
                    "-c", "copy",
                    "-movflags", "+faststart",
                    self.current_filepath
                ]
                creationflags = 0
                startupinfo = None
                if os.name == 'nt':
                    creationflags = subprocess.CREATE_NO_WINDOW
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startupinfo.wShowWindow = subprocess.SW_HIDE

                remux_process = subprocess.run(
                    remux_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creationflags,
                    startupinfo=startupinfo
                )
                if remux_process.returncode == 0:
                    os.remove(self.temp_filepath)
                    logging.info("[FFmpegRecorder] Remux completed successfully.")
                else:
                    logging.error(f"[FFmpegRecorder] Remux failed with code {remux_process.returncode}")
            except Exception as e:
                logging.error(f"[FFmpegRecorder] Remux exception: {e}")

        # MKVからMP4への再多重化 (Remux)
        # クラッシュ時でもMKVは正常な状態が保たれるため、ここでMP4に変換することで破損を防ぐ
        if self.temp_filepath and os.path.exists(self.temp_filepath):
            try:
                logging.info(f"[FFmpegRecorder] Remuxing MKV to MP4: {self.temp_filepath} -> {self.current_filepath}")
                remux_cmd = [
                    self.ffmpeg_path,
                    "-y",
                    "-i", self.temp_filepath,
                    "-c", "copy",
                    "-movflags", "+faststart",
                    self.current_filepath
                ]
                creationflags = 0
                startupinfo = None
                if os.name == 'nt':
                    creationflags = subprocess.CREATE_NO_WINDOW
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startupinfo.wShowWindow = subprocess.SW_HIDE

                remux_process = subprocess.run(
                    remux_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creationflags,
                    startupinfo=startupinfo
                )
                if remux_process.returncode == 0:
                    os.remove(self.temp_filepath)
                    logging.info("[FFmpegRecorder] Remux completed successfully.")
                else:
                    logging.error(f"[FFmpegRecorder] Remux failed with code {remux_process.returncode}")
            except Exception as e:
                logging.error(f"[FFmpegRecorder] Remux exception: {e}")

    def set_low_priority(self):
        pass # C++側でプロセスを管理するため不要