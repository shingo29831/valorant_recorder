"""Module: @role: 動画プレイヤー画面におけるクリップ範囲選択・編集モード切替・クリップ生成スレッド実行を担うコントローラ"""
import os
from datetime import datetime
from typing import Callable, Optional
from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QPushButton
from core.config import Config
from recorder.ffmpeg_downloader import ensure_ffmpeg_downloaded
from recorder.ffmpeg_recorder import get_available_encoders
from ui.clip_edit_panel import ClipEditPanel
from ui.clip_generator_thread import ClipGeneratorThread
from ui.in_app_notification import InAppNotification
from ui.player_utils import find_video_for_json
from ui.timeline_overlay import TimelineOverlay
from ui.video_player_core import VideoPlayerCore


class PlayerClipController(QObject):
    def __init__(
        self,
        config: Config,
        t: object,
        player_core: VideoPlayerCore,
        timeline_overlay: TimelineOverlay,
        edit_panel: ClipEditPanel,
        edit_mode_btn: QPushButton,
        zoom_in_btn: QPushButton,
        zoom_out_btn: QPushButton,
        notification: InAppNotification,
        format_time_fn: Callable[[int], str],
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.t = t
        self.player_core = player_core
        self.timeline_overlay = timeline_overlay
        self.edit_panel = edit_panel
        self.edit_mode_btn = edit_mode_btn
        self.zoom_in_btn = zoom_in_btn
        self.zoom_out_btn = zoom_out_btn
        self.notification = notification
        self.format_time = format_time_fn

        self.clip_start_ms: int = 0
        self.clip_end_ms: int = 0
        self.current_json_filename: Optional[str] = None
        self.current_match_data: Optional[dict] = None
        self.clip_thread: Optional[ClipGeneratorThread] = None

        self.edit_panel.setStartRequested.connect(self.set_clip_start)
        self.edit_panel.setEndRequested.connect(self.set_clip_end)
        self.edit_panel.generateRequested.connect(self.generate_clip)
        self.edit_panel.cancelRequested.connect(self.toggle_edit_mode)
        self.timeline_overlay.clipRangeChanged.connect(self.on_clip_range_changed)
        self.edit_mode_btn.clicked.connect(self.toggle_edit_mode)

    def set_current_recording(self, json_filename: Optional[str], match_data: Optional[dict]) -> None:
        self.current_json_filename = json_filename
        self.current_match_data = match_data

    def toggle_edit_mode(self) -> None:
        is_visible = self.edit_panel.isVisible()
        self.edit_panel.setVisible(not is_visible)
        self.zoom_out_btn.setVisible(not is_visible)
        self.zoom_in_btn.setVisible(not is_visible)

        if not is_visible:
            self.clip_start_ms = self.player_core.position()
            self.clip_end_ms = min(self.player_core.duration(), self.clip_start_ms + 30000)
            self.update_clip_labels()
            self.timeline_overlay.set_edit_mode(True, self.clip_start_ms, self.clip_end_ms)
            self.edit_mode_btn.setStyleSheet("border-radius: 20px; background-color: #FF4655;")

            target_view_duration = 60000
            duration = self.player_core.duration()
            if duration > target_view_duration:
                zoom = duration / target_view_duration
                self.timeline_overlay.set_zoom(zoom, center_ms=self.player_core.position())
        else:
            self.timeline_overlay.set_edit_mode(False)
            self.edit_mode_btn.setStyleSheet("border-radius: 20px; background-color: #333333;")
            self.timeline_overlay.set_zoom(1.0)

    def set_clip_start(self) -> None:
        self.clip_start_ms = self.player_core.position()
        if self.clip_start_ms > self.clip_end_ms:
            self.clip_end_ms = self.player_core.duration()
        self.update_clip_labels()
        self.timeline_overlay.set_clip_range(self.clip_start_ms, self.clip_end_ms)

    def set_clip_end(self) -> None:
        self.clip_end_ms = self.player_core.position()
        if self.clip_end_ms < self.clip_start_ms:
            self.clip_start_ms = 0
        self.update_clip_labels()
        self.timeline_overlay.set_clip_range(self.clip_start_ms, self.clip_end_ms)

    def update_clip_labels(self) -> None:
        self.edit_panel.update_labels(self.format_time(self.clip_start_ms), self.format_time(self.clip_end_ms))

    def on_clip_range_changed(self, start_ms: int, end_ms: int) -> None:
        self.clip_start_ms = start_ms
        self.clip_end_ms = end_ms
        self.update_clip_labels()

    def generate_clip(self) -> None:
        if not self.current_match_data or not self.current_json_filename:
            return

        video_path = find_video_for_json(self.config.SAVE_DIR, self.current_json_filename, self.current_match_data)
        if not video_path or not os.path.exists(video_path):
            return

        if self.clip_start_ms >= self.clip_end_ms:
            self.notification.show_message("Start time must be before end time.")
            return

        clip_dir = getattr(self.config, "CLIP_SAVE_DIR", os.path.join(self.config.SAVE_DIR, "clips"))
        os.makedirs(clip_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f"clip_{timestamp}.mp4"
        output_path = os.path.join(clip_dir, output_filename)

        app_data_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ValoReco")
        os.makedirs(app_data_dir, exist_ok=True)
        ffmpeg_path = ensure_ffmpeg_downloaded(app_data_dir)

        available, _ = get_available_encoders(ffmpeg_path)
        configured_enc = getattr(self.config, "RECORD_ENCODER", "")
        if configured_enc and configured_enc in available:
            encoder = configured_enc
        else:
            h264_encoders = [enc for enc in available if "h264" in enc]
            encoder = h264_encoders[0] if h264_encoders else (available[0] if available else "libx264")

        self.edit_panel.set_generate_enabled(False, self.t.generating_clip)
        audio_track_count = self.player_core.audio_track_count()

        self.clip_thread = ClipGeneratorThread(
            ffmpeg_path=ffmpeg_path,
            input_path=video_path,
            output_path=output_path,
            start_ms=self.clip_start_ms,
            end_ms=self.clip_end_ms,
            encoder=encoder,
            sys_volume=self.player_core.current_sys_volume,
            mic_volume=self.player_core.current_mic_volume,
            audio_track_count=audio_track_count,
        )
        self.clip_thread.finished.connect(self._on_clip_finished)
        self.clip_thread.start()

    def _on_clip_finished(self, success: bool, result: str) -> None:
        self.edit_panel.set_generate_enabled(True, self.t.generate)
        if success:
            self.notification.show_message(self.t.clip_success.format(path=result))
            self.edit_panel.setVisible(False)
        else:
            self.notification.show_message(self.t.clip_failed.format(error=result))
