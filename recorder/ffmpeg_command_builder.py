"""Module: @role: 録画設定とエンコーダ特性に応じたFFmpegコマンドラインおよびフィルタグラフを構築するモジュール"""
import ctypes
import logging
import os
import subprocess
from core.config import Config
from recorder.audio_processor_wrapper import get_short_path_name
from recorder.ffmpeg_downloader import ensure_rnnoise_model_downloaded


def get_desktop_resolution() -> tuple[int, int]:
    if os.name == "nt":
        try:
            user32 = ctypes.windll.user32
            return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
        except Exception:
            pass
    return 1920, 1080


def parse_target_resolution(config: Config) -> tuple[int, int]:
    res_str = str(getattr(config, "RECORD_RESOLUTION", "1920x1080"))
    try:
        parts = res_str.lower().split("x")
        if len(parts) == 2:
            w = (int(parts[0]) // 2) * 2
            h = (int(parts[1]) // 2) * 2
            if w > 0 and h > 0:
                return w, h
    except Exception:
        pass
    return 1920, 1080


def resolve_rnnoise_filter(app_data_dir: str) -> str:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.path.join(project_root, "bin", "sh.rnnn"),
        os.path.join(app_data_dir, "bin", "sh.rnnn"),
    ]
    rnnn_path = None
    for c in candidates:
        if os.path.isfile(c) and os.path.getsize(c) > 100:
            rnnn_path = c
            break
    if not rnnn_path:
        try:
            rnnn_path = ensure_rnnoise_model_downloaded(app_data_dir)
        except Exception as e:
            logging.warning(f"[FFmpegCommandBuilder] Could not prepare RNNoise model: {e}")
            return ""
    if rnnn_path and os.path.exists(rnnn_path):
        short_p = get_short_path_name(os.path.abspath(rnnn_path)).replace("\\", "/").replace(":", "\\:")
        return f"arnndn=m='{short_p}',"
    return ""


def build_ffmpeg_command(
    config: Config,
    ffmpeg_path: str,
    actual_encoder: str,
    temp_filepath: str,
    force_sw_download: bool = False,
    force_gdigrab: bool = False,
) -> str:
    is_lightweight = bool(getattr(config, "LIGHTWEIGHT_MODE", False))
    enc_lower = actual_encoder.lower()
    is_nvenc = "nvenc" in enc_lower
    is_amf = "amf" in enc_lower
    is_qsv = "qsv" in enc_lower

    try:
        fps_int = max(1, int(getattr(config, "RECORD_FPS", "60")))
    except (ValueError, TypeError):
        fps_int = 60
    fps = str(fps_int)
    gop_size = fps
    target_w, target_h = parse_target_resolution(config)
    screen_w, screen_h = get_desktop_resolution()
    needs_scale = target_w != screen_w or target_h != screen_h

    # 高解像度(1440p以上)や高FPS(120以上)での画質破綻を防ぐためビットレート上限をスケーリング
    is_high_bandwidth = (target_w * target_h > 1920 * 1080) or (fps_int >= 120)

    video_fmt = str(getattr(config, "RECORD_VIDEO_FORMAT", "ddagrab")).strip().lower()
    use_gdigrab = force_gdigrab or (video_fmt == "gdigrab")
    input_source = str(getattr(config, "RECORD_INPUT_SOURCE", "desktop")).strip() or "desktop"
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
        ffmpeg_path,
        "-y",
        "-hide_banner",
        "-loglevel",
        "info",
        "-fflags",
        "+genpts",
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

    # 画面静止時のフレーム枯渇とPTS乖離による多重化キュー溢れを防ぐためdup_framesと0起点PTS正規化を適用
    dda_base = f"ddagrab=output_idx={output_idx}:framerate={fps}:draw_mouse=1:dup_frames=1"
    if use_gdigrab:
        scale_part = f"scale={target_w}:{target_h}:flags={sw_flag}," if needs_scale else ""
        video_filter = f"[1:v]setpts=PTS-STARTPTS,{scale_part}format={out_pix_fmt}[v_out];"
    elif use_d3d11_direct:
        cmd.extend(["-init_hw_device", "d3d11va"])
        d3d_scale = f",scale_d3d11=w={target_w}:h={target_h}" if needs_scale else ""
        video_filter = f"{dda_base},setpts=PTS-STARTPTS{d3d_scale}[v_out];"
    elif use_qsv_gpu_scale:
        d3d_scale = f",scale_d3d11=w={target_w}:h={target_h}" if needs_scale else ""
        video_filter = (
            f"{dda_base},setpts=PTS-STARTPTS{d3d_scale},"
            f"hwdownload,format=bgra,format=nv12[v_out];"
        )
    else:
        scale_part = f",scale={target_w}:{target_h}:flags={sw_flag}" if needs_scale else ""
        video_filter = (
            f"{dda_base},setpts=PTS-STARTPTS,"
            f"hwdownload,format=bgra{scale_part},format={out_pix_fmt}[v_out];"
        )

    if is_nvenc:
        nv_preset = "p1" if is_lightweight else "p3"
        nv_tune = "ull" if is_lightweight else "ll"
        nv_cq = "26" if is_lightweight else "20"
        nv_maxrate = "8M" if is_lightweight else ("30M" if is_high_bandwidth else "20M")
        nv_bufsize = "16M" if is_lightweight else ("60M" if is_high_bandwidth else "40M")
        encoder_opts = [
            "-c:v", actual_encoder,
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
            "-c:v", actual_encoder,
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
            "-c:v", actual_encoder,
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
            "-c:v", actual_encoder,
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

    # aresampleのasync同期でクロックドリフトを自動補正し、サンプル数基準の連続PTSを付与して2系統へ分離
    audio_filter = (
        "[0:a]aresample=async=1000:first_pts=0,asetpts=N/SR/TB,asplit=2[sys_in][mic_in];"
        "[sys_in]pan=stereo|FL=c0|FR=c1[a_sys];"
        "[mic_in]pan=stereo|FL=c2|FR=c3[a_mic]"
    )
    filter_complex = video_filter + audio_filter

    cmd.extend([
        "-thread_queue_size", "4096" if is_lightweight else "8192",
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
            "-thread_queue_size", "1024" if is_lightweight else "4096",
            "-use_wallclock_as_timestamps", "1",
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
        "-max_interleave_delta", "10000000",
        "-max_muxing_queue_size", "32768" if is_lightweight else "65536",
        "-c:a", "aac",
        "-b:a", "96k" if is_lightweight else "192k",
        "-ar", "48000",
        temp_filepath,
    ])
    return subprocess.list2cmdline(cmd)
