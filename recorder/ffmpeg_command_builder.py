"""Module: @role: 録画設定とエンコーダ特性に応じたFFmpegコマンドラインおよびフィルタグラフを構築するモジュール"""
import os
import subprocess
from typing import Optional

from core.config import Config
from recorder.ffmpeg_mic_dsp import (
    build_ffmpeg_mic_dsp_chain,
    resolve_rnnoise_filter,
)
from recorder.ffmpeg_probe import (
    get_thread_queue_size_support,
    is_ffmpeg_filter_known_supported,
    probe_ffmpeg_filters_async,
    resolve_ffmpeg_path,
    supports_arnndn_filter,
    supports_ffmpeg_filter,
)
from recorder.monitor_resolver import (
    MonitorInfo,
    get_desktop_resolution,
    get_monitors,
    parse_target_resolution,
    resolve_target_monitor,
)

__all__ = [
    "MonitorInfo",
    "build_ffmpeg_command",
    "build_ffmpeg_mic_dsp_chain",
    "get_desktop_resolution",
    "get_monitors",
    "get_thread_queue_size_support",
    "is_ffmpeg_filter_known_supported",
    "parse_target_resolution",
    "probe_ffmpeg_filters_async",
    "resolve_ffmpeg_path",
    "resolve_rnnoise_filter",
    "resolve_target_monitor",
    "supports_arnndn_filter",
    "supports_ffmpeg_filter",
]

def _build_video_filter(
    use_gdigrab: bool,
    use_d3d11_direct: bool,
    use_qsv_gpu_scale: bool,
    output_idx: str,
    fps: str,
    target_w: int,
    target_h: int,
    needs_scale: bool,
    sw_flag: str,
    out_pix_fmt: str,
) -> str:
    # 画面静止時のフレーム枯渇とPTS乖離による多重化キュー溢れを防ぐためdup_framesと0起点PTS正規化を適用
    dda_base = f"ddagrab=output_idx={output_idx}:framerate={fps}:draw_mouse=1:dup_frames=1"
    if use_gdigrab:
        scale_part = f"scale={target_w}:{target_h}:flags={sw_flag}," if needs_scale else ""
        return f"[1:v]setpts=PTS-STARTPTS,{scale_part}format={out_pix_fmt}[v_out]"
    if use_d3d11_direct:
        d3d_scale = f",scale_d3d11=w={target_w}:h={target_h}" if needs_scale else ""
        return f"{dda_base},setpts=PTS-STARTPTS{d3d_scale}[v_out]"
    if use_qsv_gpu_scale:
        d3d_scale = f",scale_d3d11=w={target_w}:h={target_h}" if needs_scale else ""
        return (
            f"{dda_base},setpts=PTS-STARTPTS{d3d_scale},"
            f"hwdownload,format=bgra,format=nv12[v_out]"
        )
    scale_part = f",scale={target_w}:{target_h}:flags={sw_flag}" if needs_scale else ""
    return (
        f"{dda_base},setpts=PTS-STARTPTS,"
        f"hwdownload,format=bgra{scale_part},format={out_pix_fmt}[v_out]"
    )

def _build_encoder_options(
    actual_encoder: str,
    is_lightweight: bool,
    is_high_bandwidth: bool,
    gop_size: str,
) -> list[str]:
    enc_lower = actual_encoder.lower()
    is_nvenc = "nvenc" in enc_lower
    is_amf = "amf" in enc_lower
    is_qsv = "qsv" in enc_lower

    if is_nvenc:
        nv_preset = "p1" if is_lightweight else "p3"
        nv_tune = "ull" if is_lightweight else "ll"
        nv_cq = "26" if is_lightweight else "20"
        nv_maxrate = "8M" if is_lightweight else ("30M" if is_high_bandwidth else "20M")
        nv_bufsize = "16M" if is_lightweight else ("60M" if is_high_bandwidth else "40M")
        opts = [
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
            opts.extend(["-spatial-aq", "0", "-temporal-aq", "0", "-rc-lookahead", "0", "-no-scenecut", "1"])
        return opts

    if is_amf:
        amf_usage = "ultralowlatency" if is_lightweight else "lowlatency"
        amf_quality = "speed" if is_lightweight else "balanced"
        amf_rc = "vbr_latency" if is_lightweight else "vbr_peak"
        amf_br = "6M" if is_lightweight else ("20M" if is_high_bandwidth else "14M")
        amf_max = "8M" if is_lightweight else ("30M" if is_high_bandwidth else "20M")
        amf_buf = "16M" if is_lightweight else ("60M" if is_high_bandwidth else "40M")
        opts = [
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
            opts.extend(["-bf", "0"])
        return opts

    if is_qsv:
        qsv_preset = "veryfast" if is_lightweight else "fast"
        qsv_br = "6M" if is_lightweight else ("20M" if is_high_bandwidth else "14M")
        qsv_max = "8M" if is_lightweight else ("30M" if is_high_bandwidth else "20M")
        qsv_buf = "16M" if is_lightweight else ("60M" if is_high_bandwidth else "40M")
        return [
            "-c:v", actual_encoder,
            "-preset", qsv_preset,
            "-async_depth", "1" if is_lightweight else "4",
            "-b:v", qsv_br,
            "-maxrate", qsv_max,
            "-bufsize", qsv_buf,
            "-bf", "0",
            "-g", gop_size,
        ]

    sw_max = "6M" if is_lightweight else ("22M" if is_high_bandwidth else "15M")
    sw_buf = "12M" if is_lightweight else ("44M" if is_high_bandwidth else "30M")
    opts = [
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
        opts.extend([
            "-threads", "2",
            "-x264-params", "sliced-threads=0:sync-lookahead=0:rc-lookahead=0:ref=1:subme=0:me=dia:trellis=0",
        ])
    else:
        opts.extend(["-threads", "4"])
    return opts

def _build_audio_filter(enable_chat_track: bool, mic_dsp_chain: str) -> tuple[str, str, str]:
    # panフィルタのチャンネル識別子混在エラー(-EINVAL)を防ぐため番号形式(c0,c1...)で統一
    if enable_chat_track:
        audio_filter = (
            "[0:a]aresample=async=1000:first_pts=0,asetpts=N/SR/TB,asplit=3[sys_in][mic_in][chat_in];"
            "[sys_in]pan=stereo|c0=c0|c1=c1[a_sys];"
            f"[mic_in]pan=stereo|c0=c2|c1=c3{mic_dsp_chain}[a_mic];"
            "[chat_in]pan=stereo|c0=c4|c1=c5[a_chat]"
        )
        return audio_filter, "6", "5.1"

    audio_filter = (
        "[0:a]aresample=async=1000:first_pts=0,asetpts=N/SR/TB,asplit=2[sys_in][mic_in];"
        "[sys_in]pan=stereo|c0=c0|c1=c1[a_sys];"
        f"[mic_in]pan=stereo|c0=c2|c1=c3{mic_dsp_chain}[a_mic]"
    )
    return audio_filter, "4", "quad"

def build_ffmpeg_command(
    config: Config,
    ffmpeg_path: str,
    actual_encoder: str,
    temp_filepath: str,
    force_sw_download: bool = False,
    force_gdigrab: bool = False,
    enable_chat_track: Optional[bool] = None,
    use_ffmpeg_mic_dsp: bool = False,
    allow_arnndn: bool = True,
    allow_agate: bool = True,
) -> str:
    if enable_chat_track is None:
        chat_sep = bool(getattr(config, "RECORD_AUDIO_CHAT_SEPARATE", False))
        chat_dev = str(getattr(config, "RECORD_AUDIO_CHAT", "None") or "None").strip().lower()
        enable_chat_track = chat_sep and chat_dev != "none"

    is_lightweight = bool(getattr(config, "LIGHTWEIGHT_MODE", False))
    enc_lower = actual_encoder.lower()
    is_nvenc = "nvenc" in enc_lower
    is_amf = "amf" in enc_lower
    is_qsv = "qsv" in enc_lower

    try:
        fps_int = max(1, int(getattr(config, "RECORD_FPS", "60") or 60))
    except (ValueError, TypeError):
        fps_int = 60
    fps = str(fps_int)
    gop_size = fps
    target_mon = resolve_target_monitor(config)
    target_w, target_h = parse_target_resolution(config)
    screen_w, screen_h = target_mon.width, target_mon.height
    needs_scale = target_w != screen_w or target_h != screen_h

    # 高解像度(1440p以上)や高FPS(120以上)での画質破綻を防ぐためビットレート上限をスケーリング
    is_high_bandwidth = (target_w * target_h > 1920 * 1080) or (fps_int >= 120)

    video_fmt = str(getattr(config, "RECORD_VIDEO_FORMAT", "ddagrab") or "ddagrab").strip().lower()
    use_gdigrab = force_gdigrab or (video_fmt == "gdigrab")
    input_source = str(getattr(config, "RECORD_INPUT_SOURCE", "primary") or "primary").strip() or "primary"
    output_idx = str(target_mon.output_idx)
    adapter_idx = target_mon.adapter_idx

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
    # Intel QSVはGPU上のscale_d3d11で縮小後にnv12へ変換する
    use_qsv_gpu_scale = (not use_gdigrab) and is_qsv and not force_sw_download

    # ddagrabは全分岐でD3D11VAデバイス必須。対象モニターが属するGPUアダプタへ明示バインド
    if not use_gdigrab:
        d3d_dev_spec = f"d3d11va=d3d11:{adapter_idx}" if adapter_idx > 0 else "d3d11va=d3d11"
        cmd.extend(["-init_hw_device", d3d_dev_spec, "-filter_hw_device", "d3d11"])

    video_filter = _build_video_filter(
        use_gdigrab=use_gdigrab,
        use_d3d11_direct=use_d3d11_direct,
        use_qsv_gpu_scale=use_qsv_gpu_scale,
        output_idx=output_idx,
        fps=fps,
        target_w=target_w,
        target_h=target_h,
        needs_scale=needs_scale,
        sw_flag=sw_flag,
        out_pix_fmt=out_pix_fmt,
    )
    encoder_opts = _build_encoder_options(
        actual_encoder=actual_encoder,
        is_lightweight=is_lightweight,
        is_high_bandwidth=is_high_bandwidth,
        gop_size=gop_size,
    )

    mic_dsp_chain = ""
    if use_ffmpeg_mic_dsp:
        mic_dsp_chain, _ = build_ffmpeg_mic_dsp_chain(
            config,
            ffmpeg_path,
            allow_arnndn=allow_arnndn,
            allow_agate=allow_agate,
        )

    audio_filter, pipe_channels, pipe_layout = _build_audio_filter(enable_chat_track, mic_dsp_chain)

    supports_input_tqs, supports_output_tqs = get_thread_queue_size_support(ffmpeg_path)
    tqs_val = "4096" if is_lightweight else "8192"
    gdi_tqs_val = "1024" if is_lightweight else "4096"

    pipe_opts = []
    if supports_input_tqs:
        pipe_opts.extend(["-thread_queue_size", tqs_val])
    pipe_opts.extend([
        "-probesize", "32",
        "-analyzeduration", "0",
        "-f", "f32le",
        "-ar", "48000",
        "-ac", pipe_channels,
        "-channel_layout", pipe_layout,
        "-i", "pipe:0",
    ])
    cmd.extend(pipe_opts)

    if use_gdigrab:
        gdi_opts = []
        if supports_input_tqs:
            gdi_opts.extend(["-thread_queue_size", gdi_tqs_val])
        gdi_opts.extend([
            "-use_wallclock_as_timestamps", "1",
            "-f", "gdigrab",
            "-framerate", fps,
            "-draw_mouse", "1",
        ])
        if input_source.lower().startswith("title="):
            gdi_opts.extend(["-i", input_source])
        else:
            # 仮想デスクトップ全体(全モニター)の結合録画を防ぎ、対象モニターの領域のみを切り出す
            crop_w = max(2, (target_mon.width // 2) * 2)
            crop_h = max(2, (target_mon.height // 2) * 2)
            gdi_opts.extend([
                "-offset_x", str(target_mon.x),
                "-offset_y", str(target_mon.y),
                "-video_size", f"{crop_w}x{crop_h}",
                "-i", "desktop",
            ])
        cmd.extend(gdi_opts)

    # 映像・音声を独立した-filter_complexに分離し、pipe:0のEOF時に-shortestで確実にグレースフル終了させる
    map_opts = [
        "-filter_complex", video_filter,
        "-filter_complex", audio_filter,
        "-map", "[v_out]",
        "-map", "[a_sys]",
        "-map", "[a_mic]",
    ]
    if enable_chat_track:
        map_opts.extend(["-map", "[a_chat]"])
    map_opts.extend([
        "-metadata:s:a:0", "title=System Audio",
        "-metadata:s:a:1", "title=Microphone",
    ])
    if enable_chat_track:
        map_opts.extend(["-metadata:s:a:2", "title=Voice Chat"])
    cmd.extend(map_opts)
    cmd.extend(encoder_opts)
    cmd.extend([
        "-r", fps,
        "-fps_mode", "cfr",
        "-max_interleave_delta", "10000000",
        "-max_muxing_queue_size", "32768" if is_lightweight else "65536",
    ])
    if supports_output_tqs:
        cmd.extend(["-thread_queue_size", tqs_val])
    cmd.extend([
        "-c:a", "aac",
        "-b:a", "96k" if is_lightweight else "192k",
        "-ar", "48000",
        "-shortest",
        temp_filepath,
    ])
    return subprocess.list2cmdline(cmd)
