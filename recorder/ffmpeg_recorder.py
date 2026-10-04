import subprocess
import os
import ctypes
import logging
import time
from datetime import datetime
from core.config import Config
from recorder.ffmpeg_downloader import ensure_ffmpeg_downloaded
from recorder.encoder_utils import test_encoder, get_available_encoders

def try_rebuild_native_recorder():
    """
    C++のソースファイル(NativeRecorder.cpp)がDLLより新しい場合、
    バックグラウンドでCMakeビルドを実行して最新化する。
    """
    try:
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "cpp", "audio_processor"))
        cpp_src = os.path.join(base_dir, "NativeRecorder.cpp")
        build_dir = os.path.join(base_dir, "build")
        dll_path = os.path.join(build_dir, "Release", "native_recorder.dll")
        
        if not os.path.exists(cpp_src):
            return
            
        needs_build = False
        if not os.path.exists(dll_path):
            needs_build = True
        elif os.path.getmtime(cpp_src) > os.path.getmtime(dll_path):
            needs_build = True
            
        if needs_build:
            logging.info("[FFmpegRecorder] Newer C++ source detected. Rebuilding native_recorder.dll...")
            os.makedirs(build_dir, exist_ok=True)
            res = subprocess.run(
                ["cmake", "--build", ".", "--config", "Release"],
                cwd=build_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                timeout=30
            )
            if res.returncode == 0:
                logging.info("[FFmpegRecorder] C++ module successfully rebuilt.")
            else:
                logging.warning(f"[FFmpegRecorder] Auto-rebuild skipped: {res.stderr.decode('utf-8', errors='replace')}")
    except Exception as e:
        logging.warning(f"[FFmpegRecorder] Auto-rebuild check skipped: {e}")

class RecorderConfig(ctypes.Structure):
    _fields_ = [
        ("mic_gain", ctypes.c_float),
        ("system_gain", ctypes.c_float),
        ("noise_gate_threshold", ctypes.c_float),
        ("mic_device_name", ctypes.c_char_p),
        ("ffmpeg_command", ctypes.c_char_p),
        ("save_file_path", ctypes.c_char_p),
        ("temp_file_path", ctypes.c_char_p),
        ("ffmpeg_path", ctypes.c_char_p),
        ("df_model_path", ctypes.c_char_p),
        ("denoise_mode", ctypes.c_int32),
        ("preprocess_mode", ctypes.c_int32),
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
        
        try_rebuild_native_recorder()
        
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
            
        LOAD_WITH_ALTERED_SEARCH_PATH = 8
        
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
        
        available, _ = get_available_encoders(self.ffmpeg_path)
        return available[0]

    def _collect_and_save_diagnostic(self, err_msg: str):
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        diagnostic_path = os.path.join(project_root, "recording_diagnostic.log")
        try:
            with open(diagnostic_path, "w", encoding="utf-8") as f:
                f.write(f"=== Recording Error Diagnostic ===\n")
                f.write(f"Timestamp: {datetime.now()}\n")
                f.write(f"Error Message: {err_msg}\n\n")

                try:
                    last_err = self.native_lib.Recorder_GetLastError().decode('utf-8', errors='replace')
                    f.write(f"[NativeRecorder Last Error]: {last_err}\n\n")
                except Exception as e:
                    f.write(f"[NativeRecorder Last Error]: (Failed to read: {e})\n\n")

                native_log_path = os.path.join(self.config.SAVE_DIR, "native_recorder_debug.log")
                f.write(f"--- native_recorder_debug.log ({native_log_path}) ---\n")
                if os.path.exists(native_log_path):
                    with open(native_log_path, "r", encoding="utf-8", errors="replace") as nf:
                        f.write(nf.read()[-3000:])
                else:
                    f.write("File not found.\n")
                f.write("\n\n")

                ffmpeg_log_path = os.path.join(self.config.SAVE_DIR, "native_ffmpeg.log")
                f.write(f"--- native_ffmpeg.log ({ffmpeg_log_path}) ---\n")
                if os.path.exists(ffmpeg_log_path):
                    with open(ffmpeg_log_path, "r", encoding="utf-8", errors="replace") as ff:
                        f.write(ff.read()[-3000:])
                else:
                    f.write("File not found.\n")
                f.write("\n\n")

                app_log_path = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'ValorantRecorder', 'app.log')
                f.write(f"--- app.log ({app_log_path}) ---\n")
                if os.path.exists(app_log_path):
                    with open(app_log_path, "r", encoding="utf-8", errors="replace") as af:
                        f.write(af.read()[-3000:])
                else:
                    f.write("File not found.\n")
            logging.info(f"[FFmpegRecorder] Diagnostic log saved to: {diagnostic_path}")
        except Exception as write_err:
            logging.error(f"[FFmpegRecorder] Failed to write diagnostic log: {write_err}")

    def start_recording(self) -> str:
        if self.native_lib.Recorder_IsRunning():
            logging.info("[FFmpegRecorder] Already recording.")
            return self.current_filepath

        try:
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
                "-stats",
                "-benchmark",
                "-loglevel", "verbose",
            ]

            # ddagrabをfilter_complex内部ソースに直結し、demuxer多重ループの競合を根絶
            if is_nvenc:
                cmd.extend([
                    "-init_hw_device", "d3d11va",
                ])
                video_filter = f"ddagrab=output_idx=0:framerate={self.config.RECORD_FPS}:draw_mouse=1[v_out];"
                encoder_opts = [
                    "-c:v", self.actual_encoder,
                    "-preset", "p2",
                    "-tune", "ll",
                    "-rc", "vbr",
                    "-cq", "20",
                    "-b:v", "0",
                    "-delay", "0",
                    "-zerolatency", "1",
                ]
            else:
                video_filter = f"ddagrab=output_idx=0:framerate={self.config.RECORD_FPS}:draw_mouse=1,hwdownload,format=bgra,format=yuv420p[v_out];"
                encoder_opts = [
                    "-c:v", self.actual_encoder,
                    "-preset", preset,
                    "-tune", tune,
                    "-b:v", "10M",
                ]

            video_map = "[v_out]"

            audio_filter = (
                "[0:a]asetpts=N/SR/TB,asplit=3[mix_in][sys_in][mic_in];"
                "[mix_in]pan=stereo|FL=c0|FR=c1,alimiter=limit=0.99[a_mixed];"
                "[sys_in]pan=stereo|FL=c2|FR=c3,alimiter=limit=0.99[a0_out];"
                "[mic_in]pan=stereo|FL=0.5*c4+0.5*c5|FR=0.5*c4+0.5*c5,alimiter=limit=0.95:attack=5:release=50[a1_out]"
            )
            filter_complex = video_filter + audio_filter

            cmd.extend([
                "-f", "f32le",
                "-ar", "48000",
                "-ac", "6",
                "-i", "pipe:0",
                "-filter_complex", filter_complex,
                "-map", video_map,
                "-map", "[a_mixed]",
                "-map", "[a0_out]",
                "-map", "[a1_out]",
            ])
            cmd.extend(encoder_opts)
            cmd.extend([
                "-r", str(self.config.RECORD_FPS),
                "-fps_mode", "cfr",
                "-max_muxing_queue_size", "8192",
                "-c:a", "aac",
                "-b:a", "192k",
                self.temp_filepath
            ])

            cmd_str = subprocess.list2cmdline(cmd)
            
            report_path = os.path.join(self.config.SAVE_DIR, "ffmpeg_crash_report.log").replace('\\', '/')
            os.environ["FFREPORT"] = f"file='{report_path}':level=48"
            
            gate_level = float(getattr(self.config, 'RECORD_AUDIO_MIC_NOISE_GATE', '0')) / 100.0
            denoise_str = str(getattr(self.config, 'RECORD_AUDIO_MIC_DENOISE', 'None'))
            # UI表記(AI (DeepFilterNet))との不整合を解消
            denoise_mode = 1 if "deepfilter" in denoise_str.lower() else 0
            preprocess_str = str(getattr(self.config, 'RECORD_AUDIO_MIC_PREPROCESS', 'SpeexDSP'))
            preprocess_mode = 1 if preprocess_str == 'SpeexDSP' else 0

            # 過去互換性および設定キー名の揺らぎを吸収
            raw_mic_gain = float(getattr(self.config, 'RECORD_AUDIO_MIC_GAIN', 1.0))
            mic_gain = raw_mic_gain / 100.0 if raw_mic_gain > 10.0 else raw_mic_gain
            raw_sys_gain = float(getattr(self.config, 'RECORD_AUDIO_SYSTEM_GAIN', getattr(self.config, 'RECORD_AUDIO_SYS_GAIN', 1.0)))
            sys_gain = raw_sys_gain / 100.0 if raw_sys_gain > 10.0 else raw_sys_gain

            config_struct = RecorderConfig()
            config_struct.mic_gain = mic_gain
            config_struct.system_gain = sys_gain
            config_struct.noise_gate_threshold = gate_level
            config_struct.denoise_mode = denoise_mode
            config_struct.preprocess_mode = preprocess_mode
            
            mic_device = getattr(self.config, 'RECORD_AUDIO_MIC_DEVICE', 'Default')
            config_struct.mic_device_name = mic_device.encode('utf-8') if mic_device != 'Default' else b""
            config_struct.ffmpeg_command = cmd_str.encode('utf-8')
            config_struct.save_file_path = self.current_filepath.encode('utf-8')
            config_struct.temp_file_path = self.temp_filepath.encode('utf-8')
            config_struct.ffmpeg_path = self.ffmpeg_path.encode('utf-8')
            
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            possible_model_paths = [
                os.path.join(project_root, "cpp", "audio_processor", "models", "DeepFilterNet3", "DeepFilterNet3_onnx.tar.gz"),
                os.path.join(project_root, "cpp", "audio_processor", "models", "DeepFilterNet3_onnx.tar.gz"),
                os.path.join(project_root, "cpp", "audio_processor", "DeepFilterNet3_onnx.tar.gz"),
                os.path.join(project_root, "models", "DeepFilterNet3", "DeepFilterNet3_onnx.tar.gz"),
                os.path.join(project_root, "models", "DeepFilterNet3_onnx.tar.gz"),
            ]
            df_model_path = ""
            for p in possible_model_paths:
                if os.path.isfile(p) and os.path.getsize(p) > 1024:
                    df_model_path = p
                    break
                    
            if not df_model_path:
                for root, _, files in os.walk(project_root):
                    for f in files:
                        if f.endswith(".tar.gz") and "deepfilter" in f.lower():
                            cand = os.path.join(root, f)
                            if os.path.getsize(cand) > 1024:
                                df_model_path = cand
                                break
                    if df_model_path:
                        break

            from recorder.audio_processor_wrapper import get_short_path_name
            if df_model_path:
                df_model_path = get_short_path_name(df_model_path).replace('\\', '/')
                
            config_struct.df_model_path = df_model_path.encode('utf-8')
            
            logging.info(f"[FFmpegRecorder] Starting NativeRecorder. Encoder: {self.actual_encoder}")
            logging.info(f"[FFmpegRecorder] Command: {cmd_str}")
            
            if not self.native_lib.Recorder_Start(ctypes.byref(config_struct)):
                err = self.native_lib.Recorder_GetLastError().decode('utf-8', errors='replace')
                full_err = f"NativeRecorder failed to start: {err}"
                logging.error(f"[FFmpegRecorder] {full_err}")
                self._collect_and_save_diagnostic(full_err)
                raise RuntimeError(full_err)
                
            time.sleep(0.4)
            if not self.native_lib.Recorder_IsRunning():
                err = self.native_lib.Recorder_GetLastError().decode('utf-8', errors='replace')
                if not err:
                    err = "FFmpeg or audio capture pipeline terminated unexpectedly (see recording_diagnostic.log)"
                full_err = f"Recorder initialization failed: {err}"
                logging.error(f"[FFmpegRecorder] {full_err}")
                self._collect_and_save_diagnostic(full_err)
                raise RuntimeError(full_err)

            logging.info("[FFmpegRecorder] Recording started successfully.")
            return self.current_filepath
        except Exception as e:
            err_msg = str(e)
            logging.error(f"[FFmpegRecorder] Failed to start recording: {err_msg}", exc_info=True)
            self._collect_and_save_diagnostic(err_msg)
            # エラー時にネイティブスレッドとリソースを確実に後始末
            try:
                self.native_lib.Recorder_Stop()
            except Exception:
                pass
            raise

    def is_recording(self) -> bool:
        return self.native_lib.Recorder_IsRunning()

    def stop_recording(self):
        logging.info("[FFmpegRecorder] Stopping NativeRecorder...")
        self.native_lib.Recorder_Stop()
        
        # MKVからMP4への再多重化 (Remux)
        if self.temp_filepath and os.path.exists(self.temp_filepath):
            for attempt in range(6):
                try:
                    with open(self.temp_filepath, "r+b"):
                        break
                except IOError:
                    time.sleep(0.5)

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
                    try:
                        os.remove(self.temp_filepath)
                    except Exception as rm_err:
                        logging.warning(f"[FFmpegRecorder] Could not remove temp MKV: {rm_err}")
                    logging.info("[FFmpegRecorder] Remux completed successfully.")
                else:
                    logging.error(f"[FFmpegRecorder] Remux failed with code {remux_process.returncode}")
            except Exception as e:
                logging.error(f"[FFmpegRecorder] Remux exception: {e}")

    def set_low_priority(self):
        pass
