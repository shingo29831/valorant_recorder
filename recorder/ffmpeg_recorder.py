import subprocess
import os
import ctypes
import logging
import time
from datetime import datetime
from core.config import Config
from recorder.ffmpeg_downloader import ensure_ffmpeg_downloaded, ensure_rnnoise_model_downloaded
from recorder.encoder_utils import test_encoder, get_available_encoders

def try_rebuild_native_recorder():
    """
    C++のソースファイルがDLLより新しい場合、CMakeビルドを実行して最新化する。
    """
    try:
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "cpp", "audio_processor"))
        cpp_sources = [
            os.path.join(base_dir, "NativeRecorder.cpp"),
            os.path.join(base_dir, "NativeRecorder.h"),
            os.path.join(base_dir, "WasapiCapture.cpp"),
            os.path.join(base_dir, "WasapiCapture.h"),
            os.path.join(base_dir, "AudioProcessor.cpp"),
            os.path.join(base_dir, "AudioProcessor.h"),
        ]
        build_dir = os.path.join(base_dir, "build")
        dll_paths = [
            os.path.join(build_dir, "Release", "native_recorder.dll"),
            os.path.join(build_dir, "Release", "audio_processor.dll"),
        ]
        
        existing_sources = [p for p in cpp_sources if os.path.exists(p)]
        if not existing_sources:
            return
            
        max_src_mtime = max(os.path.getmtime(p) for p in existing_sources)
        needs_build = False
        for dll_p in dll_paths:
            if not os.path.exists(dll_p) or max_src_mtime > os.path.getmtime(dll_p):
                needs_build = True
                break
            
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
        ("low_priority", ctypes.c_int32),
    ]

class FFmpegRecorder:
    def __init__(self, config: Config):
        self.config = config
        self.current_filepath = None
        self.temp_filepath = None
        
        self.app_data_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'ValoReco')
        os.makedirs(self.app_data_dir, exist_ok=True)
        self.ffmpeg_path = ensure_ffmpeg_downloaded(self.app_data_dir)
        self._last_configured_encoder = self.config.RECORD_ENCODER
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
        if hasattr(self.native_lib, 'Recorder_SetLowPriority'):
            self.native_lib.Recorder_SetLowPriority.argtypes = [ctypes.c_bool]
            self.native_lib.Recorder_SetLowPriority.restype = None

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

    def _get_desktop_resolution(self) -> tuple[int, int]:
        if os.name == 'nt':
            try:
                user32 = ctypes.windll.user32
                return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
            except Exception:
                pass
        return 1920, 1080

    def _parse_target_resolution(self) -> tuple[int, int]:
        res_str = str(getattr(self.config, 'RECORD_RESOLUTION', '1920x1080'))
        try:
            parts = res_str.lower().split('x')
            if len(parts) == 2:
                w = (int(parts[0]) // 2) * 2
                h = (int(parts[1]) // 2) * 2
                if w > 0 and h > 0:
                    return w, h
        except Exception:
            pass
        return 1920, 1080

    def _resolve_rnnoise_filter(self) -> str:
        from recorder.audio_processor_wrapper import get_short_path_name
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidates = [
            os.path.join(project_root, "bin", "sh.rnnn"),
            os.path.join(self.app_data_dir, "bin", "sh.rnnn"),
        ]
        rnnn_path = None
        for c in candidates:
            if os.path.isfile(c) and os.path.getsize(c) > 100:
                rnnn_path = c
                break
        if not rnnn_path:
            try:
                rnnn_path = ensure_rnnoise_model_downloaded(self.app_data_dir)
            except Exception as e:
                logging.warning(f"[FFmpegRecorder] Could not prepare RNNoise model: {e}")
                return ""
        if rnnn_path and os.path.exists(rnnn_path):
            short_p = get_short_path_name(os.path.abspath(rnnn_path)).replace('\\', '/').replace(':', '\\:')
            return f"arnndn=m='{short_p}',"
        return ""

    def _build_ffmpeg_command(self, force_sw_download: bool = False) -> str:
        is_lightweight = bool(getattr(self.config, 'LIGHTWEIGHT_MODE', False))
        enc_lower = self.actual_encoder.lower()
        is_nvenc = "nvenc" in enc_lower
        is_amf = "amf" in enc_lower
        is_qsv = "qsv" in enc_lower

        fps = str(getattr(self.config, 'RECORD_FPS', '60'))
        gop_size = fps
        target_w, target_h = self._parse_target_resolution()
        screen_w, screen_h = self._get_desktop_resolution()
        needs_scale = (target_w != screen_w or target_h != screen_h)

        # Windowsのコロンを含むパスでFFmpegがクラッシュする原因となるFFREPORT環境変数を排除
        os.environ.pop("FFREPORT", None)

        cmd = [
            self.ffmpeg_path,
            "-y",
            "-loglevel", "info",
        ]
        if is_lightweight:
            # フィルタグラフの過剰スレッド生成を抑止してゲームのCPU競合を防止
            cmd.extend(["-filter_threads", "1", "-filter_complex_threads", "1"])

        use_d3d11_direct = is_nvenc and not force_sw_download
        if use_d3d11_direct:
            cmd.extend(["-init_hw_device", "d3d11va"])
            d3d_scale = f",scale_d3d11=w={target_w}:h={target_h}" if needs_scale else ""
            video_filter = f"ddagrab=output_idx=0:framerate={fps}:draw_mouse=1{d3d_scale}[v_out];"
        else:
            sw_flag = "neighbor" if is_lightweight else "fast_bilinear"
            scale_part = f",scale={target_w}:{target_h}:flags={sw_flag}" if needs_scale else ""
            video_filter = (
                f"ddagrab=output_idx=0:framerate={fps}:draw_mouse=1,"
                f"hwdownload,format=bgra{scale_part},format=yuv420p[v_out];"
            )

        if is_nvenc:
            nv_preset = "p1" if is_lightweight else "p2"
            nv_tune = "ull" if is_lightweight else "ll"
            nv_cq = "26" if is_lightweight else "20"
            nv_maxrate = "8M" if is_lightweight else "20M"
            nv_bufsize = "16M" if is_lightweight else "40M"
            encoder_opts = [
                "-c:v", self.actual_encoder,
                "-preset", nv_preset,
                "-tune", nv_tune,
                "-rc", "vbr",
                "-cq", nv_cq,
                "-b:v", "0",
                "-maxrate", nv_maxrate,
                "-bufsize", nv_bufsize,
                "-delay", "0",
                "-zerolatency", "1",
                "-g", gop_size,
                "-keyint_min", gop_size,
                "-forced-idr", "1",
            ]
            if is_lightweight:
                encoder_opts.extend(["-spatial-aq", "0", "-temporal-aq", "0", "-rc-lookahead", "0", "-no-scenecut", "1"])
        elif is_amf:
            amf_br = "6M" if is_lightweight else "12M"
            amf_max = "8M" if is_lightweight else "16M"
            amf_buf = "16M" if is_lightweight else "32M"
            encoder_opts = [
                "-c:v", self.actual_encoder,
                "-usage", "ultralowlatency",
                "-quality", "speed",
                "-b:v", amf_br,
                "-maxrate", amf_max,
                "-bufsize", amf_buf,
                "-g", gop_size,
            ]
        elif is_qsv:
            qsv_br = "6M" if is_lightweight else "12M"
            qsv_max = "8M" if is_lightweight else "16M"
            qsv_buf = "16M" if is_lightweight else "32M"
            encoder_opts = [
                "-c:v", self.actual_encoder,
                "-preset", "veryfast",
                "-b:v", qsv_br,
                "-maxrate", qsv_max,
                "-bufsize", qsv_buf,
                "-g", gop_size,
            ]
            if is_lightweight:
                encoder_opts.extend(["-async_depth", "1"])
        else:
            encoder_opts = [
                "-c:v", self.actual_encoder,
                "-preset", "ultrafast",
                "-tune", "zerolatency",
                "-crf", "28" if is_lightweight else "23",
                "-maxrate", "6M" if is_lightweight else "15M",
                "-bufsize", "12M" if is_lightweight else "30M",
                "-g", gop_size,
                "-keyint_min", gop_size,
            ]
            if is_lightweight:
                encoder_opts.extend([
                    "-threads", "2",
                    "-x264-params", "sliced-threads=0:sync-lookahead=0:rc-lookahead=0:ref=1:subme=0:me=dia:trellis=0",
                ])

        # C++側AudioProcessorでノイズ抑制とクランプが完結しているため、FFmpeg側での重複フィルタによる音質劣化・こもりを防止
        audio_filter = (
            "[0:a]asplit=2[sys_in][mic_in];"
            "[sys_in]pan=stereo|FL=c0|FR=c1[a_sys];"
            "[mic_in]pan=stereo|FL=c2|FR=c3[a_mic]"
        )
        filter_complex = video_filter + audio_filter

        cmd.extend([
            "-thread_queue_size", "1024" if is_lightweight else "4096",
            "-probesize", "32",
            "-analyzeduration", "0",
            "-f", "f32le",
            "-ar", "48000",
            "-ac", "4",
            "-i", "pipe:0",
            "-filter_complex", filter_complex,
            "-map", "[v_out]",
            "-map", "[a_sys]",
            "-map", "[a_mic]",
            "-metadata:s:a:0", "title=System Audio",
            "-metadata:s:a:1", "title=Microphone",
        ])
        cmd.extend(encoder_opts)
        cmd.extend([
            "-r", fps,
            "-fps_mode", "cfr",
            "-shortest",
            "-max_muxing_queue_size", "4096" if is_lightweight else "8192",
            "-c:a", "aac",
            "-b:a", "96k" if is_lightweight else "192k",
            "-ar", "48000",
            self.temp_filepath
        ])
        return subprocess.list2cmdline(cmd)

    def start_recording(self) -> str:
        if self.native_lib.Recorder_IsRunning():
            logging.info("[FFmpegRecorder] Already recording.")
            return self.current_filepath

        try:
            # 設定画面でエンコーダが変更された場合にリアルタイムで反映
            if self.config.RECORD_ENCODER != self._last_configured_encoder:
                self._last_configured_encoder = self.config.RECORD_ENCODER
                self.actual_encoder = self._determine_encoder()

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"match_record_{timestamp}.mp4"
            temp_filename = f"match_record_{timestamp}.mkv"
            self.current_filepath = os.path.join(self.config.SAVE_DIR, filename)
            self.temp_filepath = os.path.join(self.config.SAVE_DIR, temp_filename)
            os.makedirs(self.config.SAVE_DIR, exist_ok=True)

            is_lightweight = bool(getattr(self.config, 'LIGHTWEIGHT_MODE', False))
            cmd_str = self._build_ffmpeg_command(force_sw_download=False)

            gate_level = float(getattr(self.config, 'RECORD_AUDIO_MIC_NOISE_GATE', '0')) / 100.0
            denoise_str = str(getattr(self.config, 'RECORD_AUDIO_MIC_DENOISE', 'None')).lower()
            if "deepfilter" in denoise_str:
                denoise_mode = 2 if is_lightweight else 1
            elif "rnnoise" in denoise_str or "speex" in denoise_str:
                denoise_mode = 2
            else:
                denoise_mode = 0

            preprocess_str = str(getattr(self.config, 'RECORD_AUDIO_MIC_PREPROCESS', 'SpeexDSP'))
            preprocess_mode = 1 if preprocess_str == 'SpeexDSP' else 0

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
            config_struct.low_priority = 1 if is_lightweight else 0

            mic_device = getattr(self.config, 'RECORD_AUDIO_MIC', getattr(self.config, 'RECORD_AUDIO_MIC_DEVICE', ''))
            if mic_device.strip().lower() == 'none':
                config_struct.mic_device_name = b"__NONE__"
            elif not mic_device or mic_device.strip().lower() == 'default':
                config_struct.mic_device_name = b""
            else:
                config_struct.mic_device_name = mic_device.encode('utf-8')
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
            if denoise_mode == 1:
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

            logging.info(f"[FFmpegRecorder] Starting NativeRecorder. Encoder: {self.actual_encoder}, Lightweight: {is_lightweight}")
            logging.info(f"[FFmpegRecorder] Command: {cmd_str}")

            started_ok = self.native_lib.Recorder_Start(ctypes.byref(config_struct))
            if started_ok:
                time.sleep(0.4)
                started_ok = self.native_lib.Recorder_IsRunning()

            # Optimus環境やHDRデスクトップ等でD3D11直結が失敗した場合、互換性の高いhwdownloadパイプラインで自動再試行
            if not started_ok and "nvenc" in self.actual_encoder.lower() and "hwdownload" not in cmd_str:
                logging.warning("[FFmpegRecorder] Direct D3D11 NVENC capture failed. Retrying with hwdownload compatibility mode...")
                try:
                    self.native_lib.Recorder_Stop()
                except Exception:
                    pass
                cmd_str = self._build_ffmpeg_command(force_sw_download=True)
                config_struct.ffmpeg_command = cmd_str.encode('utf-8')
                logging.info(f"[FFmpegRecorder] Fallback Command: {cmd_str}")
                started_ok = self.native_lib.Recorder_Start(ctypes.byref(config_struct))
                if started_ok:
                    time.sleep(0.4)
                    started_ok = self.native_lib.Recorder_IsRunning()

            if not started_ok:
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

            remux_temp_path = self.current_filepath + ".tmp"
            try:
                logging.info(f"[FFmpegRecorder] Remuxing MKV to MP4: {self.temp_filepath} -> {self.current_filepath}")
                remux_cmd = [
                    self.ffmpeg_path,
                    "-y",
                    "-i", self.temp_filepath,
                    "-map", "0",
                    "-c", "copy",
                    "-movflags", "+faststart",
                    "-f", "mp4",
                    remux_temp_path
                ]
                creationflags = 0
                startupinfo = None
                if os.name == 'nt':
                    BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
                    creationflags = subprocess.CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS
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
                if remux_process.returncode == 0 and os.path.exists(remux_temp_path):
                    os.replace(remux_temp_path, self.current_filepath)
                    try:
                        os.remove(self.temp_filepath)
                    except Exception as rm_err:
                        logging.warning(f"[FFmpegRecorder] Could not remove temp MKV: {rm_err}")
                    logging.info("[FFmpegRecorder] Remux completed successfully.")
                else:
                    if os.path.exists(remux_temp_path):
                        try:
                            os.remove(remux_temp_path)
                        except Exception:
                            pass
                    logging.error(f"[FFmpegRecorder] Remux failed with code {remux_process.returncode}")
            except Exception as e:
                if os.path.exists(remux_temp_path):
                    try:
                        os.remove(remux_temp_path)
                    except Exception:
                        pass
                logging.error(f"[FFmpegRecorder] Remux exception: {e}")

    def set_low_priority(self):
        if hasattr(self.native_lib, 'Recorder_SetLowPriority'):
            try:
                self.native_lib.Recorder_SetLowPriority(True)
            except Exception as e:
                logging.warning(f"[FFmpegRecorder] Failed to set low priority: {e}")
