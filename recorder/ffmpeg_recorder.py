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
        self._sw_download_fallback_encoders: set[str] = set()
        self.actual_encoder = self._determine_encoder()
        self._last_configured_encoder = self.config.RECORD_ENCODER
        
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
        if encoder:
            success, _ = test_encoder(self.ffmpeg_path, encoder)
            if success:
                return encoder
        
        available, _ = get_available_encoders(self.ffmpeg_path)
        best_encoder = available[0] if available else "libx264"
        if self.config.RECORD_ENCODER != best_encoder:
            self.config.RECORD_ENCODER = best_encoder
            try:
                self.config.save()
            except Exception:
                pass
        return best_encoder

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

    def _build_ffmpeg_command(self, force_sw_download: bool = False, force_gdigrab: bool = False) -> str:
        is_lightweight = bool(getattr(self.config, 'LIGHTWEIGHT_MODE', False))
        enc_lower = self.actual_encoder.lower()
        is_nvenc = "nvenc" in enc_lower
        is_amf = "amf" in enc_lower
        is_qsv = "qsv" in enc_lower

        try:
            fps_int = max(1, int(getattr(self.config, 'RECORD_FPS', '60')))
        except (ValueError, TypeError):
            fps_int = 60
        fps = str(fps_int)
        gop_size = fps
        target_w, target_h = self._parse_target_resolution()
        screen_w, screen_h = self._get_desktop_resolution()
        needs_scale = (target_w != screen_w or target_h != screen_h)

        # 高解像度(1440p以上)や高FPS(120以上)での画質破綻を防ぐためビットレート上限をスケーリング
        is_high_bandwidth = (target_w * target_h > 1920 * 1080) or (fps_int >= 120)

        video_fmt = str(getattr(self.config, 'RECORD_VIDEO_FORMAT', 'ddagrab')).strip().lower()
        use_gdigrab = force_gdigrab or (video_fmt == "gdigrab")
        input_source = str(getattr(self.config, 'RECORD_INPUT_SOURCE', 'desktop')).strip() or "desktop"
        output_idx = "0"
        if input_source.isdigit():
            output_idx = input_source
        elif input_source.startswith("output_idx="):
            idx_cand = input_source.split("=", 1)[1].strip()
            if idx_cand.isdigit():
                output_idx = idx_cand

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

        out_pix_fmt = "nv12" if (is_nvenc or is_amf or is_qsv) else "yuv420p"
        sw_flag = "neighbor" if is_lightweight else "fast_bilinear"

        # NVENCとAMD AMFはD3D11テクスチャの直接入力に対応しているためゼロコピーで処理する
        use_d3d11_direct = (not use_gdigrab) and (is_nvenc or is_amf) and not force_sw_download
        # Intel QSVはGPU上のscale_d3d11で縮小後にnv12へ変換する（マルチGPU競合回避のためグローバルd3d11vaは指定しない）
        use_qsv_gpu_scale = (not use_gdigrab) and is_qsv and not force_sw_download

        if use_gdigrab:
            scale_part = f"scale={target_w}:{target_h}:flags={sw_flag}," if needs_scale else ""
            video_filter = f"[1:v]{scale_part}format={out_pix_fmt}[v_out];"
        elif use_d3d11_direct:
            cmd.extend(["-init_hw_device", "d3d11va"])
            d3d_scale = f",scale_d3d11=w={target_w}:h={target_h}" if needs_scale else ""
            video_filter = f"ddagrab=output_idx={output_idx}:framerate={fps}:draw_mouse=1{d3d_scale}[v_out];"
        elif use_qsv_gpu_scale:
            d3d_scale = f",scale_d3d11=w={target_w}:h={target_h}" if needs_scale else ""
            video_filter = (
                f"ddagrab=output_idx={output_idx}:framerate={fps}:draw_mouse=1{d3d_scale},"
                f"hwdownload,format=bgra,format=nv12[v_out];"
            )
        else:
            scale_part = f",scale={target_w}:{target_h}:flags={sw_flag}" if needs_scale else ""
            video_filter = (
                f"ddagrab=output_idx={output_idx}:framerate={fps}:draw_mouse=1,"
                f"hwdownload,format=bgra{scale_part},format={out_pix_fmt}[v_out];"
            )

        if is_nvenc:
            nv_preset = "p1" if is_lightweight else "p3"
            nv_tune = "ull" if is_lightweight else "ll"
            nv_cq = "26" if is_lightweight else "20"
            nv_maxrate = "8M" if is_lightweight else ("30M" if is_high_bandwidth else "20M")
            nv_bufsize = "16M" if is_lightweight else ("60M" if is_high_bandwidth else "40M")
            encoder_opts = [
                "-c:v", self.actual_encoder,
                "-preset", nv_preset,
                "-tune", nv_tune,
                "-rc", "vbr",
                "-cq", nv_cq,
                "-b:v", "0",
                "-maxrate", nv_maxrate,
                "-bufsize", nv_bufsize,
                "-bf", "0",
                "-delay", "0",
                "-zerolatency", "1",
                "-g", gop_size,
                "-keyint_min", gop_size,
                "-forced-idr", "1",
            ]
            if is_lightweight:
                encoder_opts.extend(["-spatial-aq", "0", "-temporal-aq", "0", "-rc-lookahead", "0", "-no-scenecut", "1"])
        elif is_amf:
            amf_usage = "ultralowlatency" if is_lightweight else "lowlatency"
            amf_quality = "speed" if is_lightweight else "balanced"
            amf_rc = "vbr_latency" if is_lightweight else "vbr_peak"
            amf_br = "6M" if is_lightweight else ("20M" if is_high_bandwidth else "14M")
            amf_max = "8M" if is_lightweight else ("30M" if is_high_bandwidth else "20M")
            amf_buf = "16M" if is_lightweight else ("60M" if is_high_bandwidth else "40M")
            encoder_opts = [
                "-c:v", self.actual_encoder,
                "-usage", amf_usage,
                "-quality", amf_quality,
                "-rc", amf_rc,
                "-b:v", amf_br,
                "-maxrate", amf_max,
                "-bufsize", amf_buf,
                "-g", gop_size,
            ]
            if enc_lower == "h264_amf":
                encoder_opts.extend(["-bf", "0"])
        elif is_qsv:
            qsv_preset = "veryfast" if is_lightweight else "fast"
            qsv_br = "6M" if is_lightweight else ("20M" if is_high_bandwidth else "14M")
            qsv_max = "8M" if is_lightweight else ("30M" if is_high_bandwidth else "20M")
            qsv_buf = "16M" if is_lightweight else ("60M" if is_high_bandwidth else "40M")
            encoder_opts = [
                "-c:v", self.actual_encoder,
                "-preset", qsv_preset,
                "-async_depth", "1" if is_lightweight else "4",
                "-b:v", qsv_br,
                "-maxrate", qsv_max,
                "-bufsize", qsv_buf,
                "-bf", "0",
                "-g", gop_size,
            ]
        else:
            sw_max = "6M" if is_lightweight else ("22M" if is_high_bandwidth else "15M")
            sw_buf = "12M" if is_lightweight else ("44M" if is_high_bandwidth else "30M")
            encoder_opts = [
                "-c:v", self.actual_encoder,
                "-preset", "ultrafast",
                "-tune", "zerolatency",
                "-crf", "28" if is_lightweight else "23",
                "-maxrate", sw_max,
                "-bufsize", sw_buf,
                "-g", gop_size,
                "-keyint_min", gop_size,
            ]
            if is_lightweight:
                encoder_opts.extend([
                    "-threads", "2",
                    "-x264-params", "sliced-threads=0:sync-lookahead=0:rc-lookahead=0:ref=1:subme=0:me=dia:trellis=0",
                ])
            else:
                encoder_opts.extend(["-threads", "4"])

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
        ])
        if use_gdigrab:
            gdi_src = input_source if not input_source.isdigit() and not input_source.startswith("output_idx=") else "desktop"
            cmd.extend([
                "-thread_queue_size", "512" if is_lightweight else "1024",
                "-f", "gdigrab",
                "-framerate", fps,
                "-draw_mouse", "1",
                "-i", gdi_src,
            ])
        cmd.extend([
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
            # ユーザーの設定変更や前回の一時フォールバックから復帰できるよう毎回エンコーダを確定
            self._last_configured_encoder = self.config.RECORD_ENCODER
            self.actual_encoder = self._determine_encoder()

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"match_record_{timestamp}.mp4"
            temp_filename = f"match_record_{timestamp}.mkv"
            self.current_filepath = os.path.join(self.config.SAVE_DIR, filename)
            self.temp_filepath = os.path.join(self.config.SAVE_DIR, temp_filename)
            os.makedirs(self.config.SAVE_DIR, exist_ok=True)

            is_lightweight = bool(getattr(self.config, 'LIGHTWEIGHT_MODE', False))
            force_sw = self.actual_encoder in self._sw_download_fallback_encoders
            cmd_str = self._build_ffmpeg_command(force_sw_download=force_sw)

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
            sys_device = str(getattr(self.config, 'RECORD_AUDIO_SYSTEM', 'default')).strip()
            if sys_device.lower() == 'none':
                os.environ["VALORECO_SYS_AUDIO_DEVICE"] = "__NONE__"
                sys_gain = 0.0
            elif not sys_device or sys_device.lower() == 'default':
                os.environ["VALORECO_SYS_AUDIO_DEVICE"] = ""
            else:
                os.environ["VALORECO_SYS_AUDIO_DEVICE"] = sys_device

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

            def _try_start_and_verify() -> bool:
                if not self.native_lib.Recorder_Start(ctypes.byref(config_struct)):
                    return False
                for _ in range(7):
                    time.sleep(0.1)
                    if not self.native_lib.Recorder_IsRunning():
                        return False
                return self.native_lib.Recorder_IsRunning()

            started_ok = _try_start_and_verify()

            # マルチGPU(Optimus/SmartAccess Graphics)やHDR環境等でD3D11直結・GPUスケールが失敗した場合、互換モードで自動再試行
            if not started_ok and not force_sw and self.actual_encoder != "libx264":
                logging.warning(f"[FFmpegRecorder] Direct D3D11 pipeline for {self.actual_encoder} failed. Retrying with hwdownload compatibility mode...")
                try:
                    self.native_lib.Recorder_Stop()
                except Exception:
                    pass
                cmd_str = self._build_ffmpeg_command(force_sw_download=True)
                config_struct.ffmpeg_command = cmd_str.encode('utf-8')
                logging.info(f"[FFmpegRecorder] Fallback Command: {cmd_str}")
                started_ok = _try_start_and_verify()
                if started_ok:
                    self._sw_download_fallback_encoders.add(self.actual_encoder)

            # ハイブリッドGPU環境等でddagrab自体がDXGI複製非対応の場合、gdigrab互換モードで再試行
            video_fmt = str(getattr(self.config, 'RECORD_VIDEO_FORMAT', 'ddagrab')).strip().lower()
            if not started_ok and video_fmt != "gdigrab":
                logging.warning(f"[FFmpegRecorder] ddagrab failed with {self.actual_encoder}. Retrying with gdigrab compatibility mode...")
                try:
                    self.native_lib.Recorder_Stop()
                except Exception:
                    pass
                cmd_str = self._build_ffmpeg_command(force_sw_download=True, force_gdigrab=True)
                config_struct.ffmpeg_command = cmd_str.encode('utf-8')
                logging.info(f"[FFmpegRecorder] GDI Fallback Command: {cmd_str}")
                started_ok = _try_start_and_verify()

            # ハードウェアエンコーダ自体が実録画解像度で初期化失敗した場合、ソフトウェアエンコーダ(libx264)で救済
            if not started_ok and self.actual_encoder != "libx264":
                logging.warning(f"[FFmpegRecorder] Hardware encoder {self.actual_encoder} failed. Falling back to libx264...")
                try:
                    self.native_lib.Recorder_Stop()
                except Exception:
                    pass
                self.actual_encoder = "libx264"
                cmd_str = self._build_ffmpeg_command(force_sw_download=True)
                config_struct.ffmpeg_command = cmd_str.encode('utf-8')
                logging.info(f"[FFmpegRecorder] Software Fallback Command: {cmd_str}")
                started_ok = _try_start_and_verify()

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
                ]
                if "hevc" in self.actual_encoder.lower():
                    remux_cmd.extend(["-tag:v", "hvc1"])
                remux_cmd.extend([
                    "-movflags", "+faststart",
                    "-f", "mp4",
                    remux_temp_path
                ])
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
