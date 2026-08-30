import os
import json
import re
import subprocess
from datetime import datetime
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QMessageBox
from PyQt6.QtCore import Qt, QUrl, QSize, pyqtSignal, QByteArray, QTimer, QThread, QEvent
from PyQt6.QtGui import QIcon, QPixmap, QPainter
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtSvg import QSvgRenderer
from core.config import Config
from core.i18n import get_trans
from ui.player_components import ClickableVideoWidget, PlayerContainer
from ui.volume_widgets import VolumeWidget, MicVolumeWidget
from ui.timeline_overlay import TimelineOverlay
from ui.player_utils import find_video_for_json, guess_player_name
from ui.event_toggle_widget import EventToggleWidget
from ui.notification_overlay import NotificationOverlay
from ui.clip_generator_thread import ClipGeneratorThread
from ui.timeline_builder import build_timeline_data
from ui.video_playback_controls import PlaybackControlsWidget
from ui.clip_edit_panel import ClipEditPanel

BACK_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white">
  <path d="M20,11V13H8L13.5,18.5L12.08,19.92L4.16,12L12.08,4.08L13.5,5.5L8,11H20Z" />
</svg>"""

SCISSORS_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white">
  <path d="M9.64,7.64C9.87,7.14 10,6.59 10,6C10,3.79 8.21,2 6,2C3.79,2 2,3.79 2,6C2,8.21 3.79,10 6,10C6.59,10 7.14,9.87 7.64,9.64L10,12L7.64,14.36C7.14,14.13 6.59,14 6,14C3.79,14 2,15.79 2,18C2,20.21 3.79,22 6,22C8.21,22 10,20.21 10,18C10,17.41 9.87,16.86 9.64,16.36L12,14L19,21H22V20L9.64,7.64M6,8C4.9,8 4,7.1 4,6C4,4.9 4.9,4 6,4C7.1,4 8,4.9 8,6C8,7.1 7.1,8 6,8M6,20C4.9,20 4,19.1 4,18C4,16.9 4.9,16 6,16C7.1,16 8,16.9 8,18C8,19.1 7.1,20 6,20M12,12.5C11.72,12.5 11.5,12.28 11.5,12C11.5,11.72 11.72,11.5 12,11.5C12.28,11.5 12.5,11.72 12.5,12C12.5,12.28 12.28,12.5 12,12.5M19,3H22V4L14,12L11.64,9.64L19,3Z" />
</svg>"""

class PlayerVideoPage(QWidget):
    backRequested = pyqtSignal()

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self.t = get_trans(self.config.LANGUAGE)
        self.current_match_data = None
        
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(20, 20, 20, 20)
        page_layout.setSpacing(10)
        
        top_layout = QHBoxLayout()
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(10)
        
        left_container = QWidget()
        left_container.setFixedWidth(50)
        left_layout = QVBoxLayout(left_container)
        left_layout.setContentsMargins(0, 0, 0, 0)
        
        back_btn = QPushButton()
        back_btn.setFixedSize(40, 40)
        back_btn.setStyleSheet("border-radius: 20px; background-color: #333333;")
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer = QSvgRenderer(QByteArray(BACK_SVG))
        renderer.render(painter)
        painter.end()
        
        back_btn.setIcon(QIcon(pixmap))
        back_btn.setIconSize(QSize(24, 24))
        back_btn.clicked.connect(self.request_back)
        
        left_layout.addWidget(back_btn, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        left_layout.addStretch()
        
        self.video_widget = ClickableVideoWidget()
        self.video_widget.setStyleSheet("background-color: #000000;")
        self.video_widget.clicked.connect(self.toggle_play)
        
        self.current_sys_volume = float(getattr(self.config, 'PLAYER_SYS_VOLUME', 1.0))
        self.current_mic_volume = float(getattr(self.config, 'PLAYER_MIC_VOLUME', 1.0))

        self.media_player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(self.current_sys_volume / 2.0)
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.setVideoOutput(self.video_widget)
        self.media_player.mediaStatusChanged.connect(self._on_media_player_status_changed)
        
        # マイク音を同時に再生・独立制御するためのサブプレイヤー
        self.mic_player = QMediaPlayer(self)
        self.mic_audio_output = QAudioOutput(self)
        self.mic_audio_output.setVolume(self.current_mic_volume / 2.0)
        self.mic_player.setAudioOutput(self.mic_audio_output)
        self.mic_player.mediaStatusChanged.connect(self._on_mic_player_status_changed)
        
        self.media_loaded = False
        self.mic_loaded = False
        
        self.timeline_overlay = TimelineOverlay()
        self.timeline_overlay.seekRequested.connect(self.set_position)
        self.timeline_overlay.clipRangeChanged.connect(self._on_clip_range_changed)
        self.media_player.positionChanged.connect(self.position_changed)
        self.media_player.durationChanged.connect(self.duration_changed)
        self.media_player.errorOccurred.connect(self.handle_media_error)
        self.media_player.playbackStateChanged.connect(self._on_playback_state_changed)
        
        controls_widget = QWidget()
        controls_layout = QHBoxLayout(controls_widget)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        
        self.volume_widget = VolumeWidget()
        self.volume_widget.set_volume(int(self.current_sys_volume * 100))
        self.volume_widget.volumeChanged.connect(self.on_sys_volume_changed)
        
        self.mic_volume_widget = MicVolumeWidget()
        self.mic_volume_widget.set_volume(int(self.current_mic_volume * 100))
        self.mic_volume_widget.volumeChanged.connect(self.on_mic_volume_changed)
        
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setFixedWidth(100)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        controls_layout.addWidget(self.volume_widget)
        controls_layout.addWidget(self.mic_volume_widget)
        controls_layout.addWidget(self.time_label)
        controls_layout.addWidget(self.timeline_overlay)
        
        self.player_container = PlayerContainer(self.video_widget)
        
        # クリップ編集パネル
        self.edit_panel = ClipEditPanel(self.t)
        self.edit_panel.setStartRequested.connect(self._set_clip_start)
        self.edit_panel.setEndRequested.connect(self._set_clip_end)
        self.edit_panel.generateRequested.connect(self._generate_clip)
        self.edit_panel.cancelRequested.connect(self._toggle_edit_mode)
        self.edit_panel.setVisible(False)
        self.clip_start_ms = 0
        self.clip_end_ms = 0
        
        right_container = QWidget()
        right_container.setFixedWidth(50)
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)
        
        self.edit_mode_btn = QPushButton()
        self.edit_mode_btn.setFixedSize(40, 40)
        self.edit_mode_btn.setStyleSheet("border-radius: 20px; background-color: #333333;")
        self.edit_mode_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        
        pixmap_edit = QPixmap(24, 24)
        pixmap_edit.fill(Qt.GlobalColor.transparent)
        painter_edit = QPainter(pixmap_edit)
        renderer_edit = QSvgRenderer(QByteArray(SCISSORS_SVG))
        renderer_edit.render(painter_edit)
        painter_edit.end()
        
        self.edit_mode_btn.setIcon(QIcon(pixmap_edit))
        self.edit_mode_btn.setIconSize(QSize(24, 24))
        self.edit_mode_btn.setToolTip(self.t.create_clip)
        self.edit_mode_btn.clicked.connect(self._toggle_edit_mode)
        
        self.event_toggle_widget = EventToggleWidget()
        self.event_toggle_widget.filterChanged.connect(self.timeline_overlay.set_filters)
        
        right_layout.addWidget(self.edit_mode_btn, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        right_layout.addWidget(self.event_toggle_widget, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        right_layout.addStretch()
        
        top_layout.addWidget(left_container)
        top_layout.addWidget(self.player_container, stretch=1)
        top_layout.addWidget(right_container)
        
        page_layout.addLayout(top_layout, stretch=1)
        page_layout.addWidget(controls_widget)
        
        self.playback_controls = PlaybackControlsWidget()
        self.playback_controls.prevRoundRequested.connect(self.skip_to_prev_round)
        self.playback_controls.skipBackRequested.connect(self.skip_backward)
        self.playback_controls.togglePlayRequested.connect(self.toggle_play)
        self.playback_controls.skipForwardRequested.connect(self.skip_forward)
        self.playback_controls.nextRoundRequested.connect(self.skip_to_next_round)
        self.playback_controls.speedDecreaseRequested.connect(self.decrease_speed)
        self.playback_controls.speedIncreaseRequested.connect(self.increase_speed)
        self.playback_controls.speedToggleRequested.connect(self.toggle_speed)
        
        page_layout.addWidget(self.playback_controls)
        page_layout.addWidget(self.edit_panel)
        
        self.notification = NotificationOverlay(self)
        
        self.target_playback_rate = 1.0
        self.is_speed_bypassed = False
        self.video_widget.installEventFilter(self)
        
        self.speed_osd_label = QLabel("1.0x", self.video_widget)
        self.speed_osd_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.speed_osd_label.setStyleSheet("background-color: rgba(0, 0, 0, 150); color: white; font-size: 24px; font-weight: bold; border-radius: 10px; padding: 10px;")
        self.speed_osd_label.hide()
        
        self.speed_osd_timer = QTimer(self)
        self.speed_osd_timer.setSingleShot(True)
        self.speed_osd_timer.timeout.connect(self.speed_osd_label.hide)

    def _create_icon(self, svg_bytes, size=24):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer = QSvgRenderer(QByteArray(svg_bytes))
        renderer.render(painter)
        painter.end()
        return QIcon(pixmap)

    def eventFilter(self, obj, event):
        if obj == self.video_widget:
            if event.type() == QEvent.Type.Wheel:
                delta = event.angleDelta().y()
                if delta > 0:
                    self.increase_speed()
                elif delta < 0:
                    self.decrease_speed()
                return True
            elif event.type() == QEvent.Type.Resize:
                if self.speed_osd_label.isVisible():
                    vw_rect = self.video_widget.rect()
                    lbl_rect = self.speed_osd_label.rect()
                    x = (vw_rect.width() - lbl_rect.width()) // 2
                    y = vw_rect.height() - lbl_rect.height() - 30
                    self.speed_osd_label.move(x, y)
        return super().eventFilter(obj, event)

    def increase_speed(self):
        self.set_target_playback_rate(self.target_playback_rate + 0.1)

    def decrease_speed(self):
        self.set_target_playback_rate(self.target_playback_rate - 0.1)

    def set_target_playback_rate(self, rate):
        rate = max(0.1, min(5.0, round(rate, 1)))
        self.target_playback_rate = rate
        self.is_speed_bypassed = False
        self._apply_playback_rate()

    def toggle_speed(self):
        if self.target_playback_rate == 1.0:
            return
        self.is_speed_bypassed = not self.is_speed_bypassed
        self._apply_playback_rate()

    def _apply_playback_rate(self):
        actual_rate = 1.0 if self.is_speed_bypassed else self.target_playback_rate
        
        self.media_player.setPlaybackRate(actual_rate)
        self.mic_player.setPlaybackRate(actual_rate)
        
        self.playback_controls.set_speed_label(self.target_playback_rate, self.is_speed_bypassed)
            
        self.speed_osd_label.setText(f"{actual_rate:.1f}x")
        self.speed_osd_label.adjustSize()
        
        vw_rect = self.video_widget.rect()
        lbl_rect = self.speed_osd_label.rect()
        x = (vw_rect.width() - lbl_rect.width()) // 2
        y = vw_rect.height() - lbl_rect.height() - 30
        self.speed_osd_label.move(x, y)
        
        self.speed_osd_label.show()
        self.speed_osd_timer.start(2000)

    def _toggle_edit_mode(self):
        is_visible = self.edit_panel.isVisible()
        self.edit_panel.setVisible(not is_visible)
        if not is_visible:
            self.clip_start_ms = self.media_player.position()
            self.clip_end_ms = min(self.media_player.duration(), self.clip_start_ms + 30000)
            self._update_clip_labels()
            self.timeline_overlay.set_edit_mode(True, self.clip_start_ms, self.clip_end_ms)
            self.edit_mode_btn.setStyleSheet("border-radius: 20px; background-color: #FF4655;")
        else:
            self.timeline_overlay.set_edit_mode(False)
            self.edit_mode_btn.setStyleSheet("border-radius: 20px; background-color: #333333;")

    def _set_clip_start(self):
        self.clip_start_ms = self.media_player.position()
        if self.clip_start_ms > self.clip_end_ms:
            self.clip_end_ms = self.media_player.duration()
        self._update_clip_labels()
        self.timeline_overlay.set_clip_range(self.clip_start_ms, self.clip_end_ms)

    def _set_clip_end(self):
        self.clip_end_ms = self.media_player.position()
        if self.clip_end_ms < self.clip_start_ms:
            self.clip_start_ms = 0
        self._update_clip_labels()
        self.timeline_overlay.set_clip_range(self.clip_start_ms, self.clip_end_ms)

    def _update_clip_labels(self):
        self.edit_panel.update_labels(self.format_time(self.clip_start_ms), self.format_time(self.clip_end_ms))

    def _on_clip_range_changed(self, start_ms, end_ms):
        self.clip_start_ms = start_ms
        self.clip_end_ms = end_ms
        self._update_clip_labels()

    def _generate_clip(self):
        if not self.current_match_data:
            return
            
        video_path = find_video_for_json(self.config.SAVE_DIR, self.current_json_filename, self.current_match_data)
        if not video_path or not os.path.exists(video_path):
            return
            
        if self.clip_start_ms >= self.clip_end_ms:
            self.notification.show_message("Start time must be before end time.")
            return
            
        clip_dir = getattr(self.config, 'CLIP_SAVE_DIR', os.path.join(self.config.SAVE_DIR, "clips"))
        os.makedirs(clip_dir, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f"clip_{timestamp}.mp4"
        output_path = os.path.join(clip_dir, output_filename)
        
        from recorder.ffmpeg_recorder import get_available_encoders
        from recorder.ffmpeg_downloader import ensure_ffmpeg_downloaded
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ffmpeg_path = ensure_ffmpeg_downloaded(project_root)
        
        available, _ = get_available_encoders(ffmpeg_path)
        h264_encoders = [enc for enc in available if "h264" in enc]
        encoder = h264_encoders[0] if h264_encoders else "libx264"
        
        self.edit_panel.set_generate_enabled(False, self.t.generating_clip)
        
        audio_track_count = len(self.media_player.audioTracks())
        
        self.clip_thread = ClipGeneratorThread(
            ffmpeg_path=ffmpeg_path,
            input_path=video_path,
            output_path=output_path,
            start_ms=self.clip_start_ms,
            end_ms=self.clip_end_ms,
            encoder=encoder,
            sys_volume=self.current_sys_volume,
            mic_volume=self.current_mic_volume,
            audio_track_count=audio_track_count
        )
        self.clip_thread.finished.connect(self._on_clip_finished)
        self.clip_thread.start()

    def _on_clip_finished(self, success, result):
        self.edit_panel.set_generate_enabled(True, self.t.generate)
        
        if success:
            self.notification.show_message(self.t.clip_success.format(path=result))
            self.edit_panel.setVisible(False)
        else:
            self.notification.show_message(self.t.clip_failed.format(error=result))

    def request_back(self):
        self.media_player.stop()
        self.mic_player.stop()
        self.media_player.setSource(QUrl())
        self.mic_player.setSource(QUrl())
        self.backRequested.emit()

    def cleanup_media(self):
        # 最小化・非表示時は再生を一時停止するのみとし、ソースと再生位置を保持する
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
            self.mic_player.pause()

    def restore_media(self):
        # ソースと再生位置を保持しているため、再ロードは行わない
        pass

    def on_sys_volume_changed(self, volume):
        self.current_sys_volume = volume / 100.0
        self.audio_output.setVolume(self.current_sys_volume / 2.0)
        self._save_volume_settings()

    def on_mic_volume_changed(self, volume):
        self.current_mic_volume = volume / 100.0
        self.mic_audio_output.setVolume(self.current_mic_volume / 2.0)
        self._save_volume_settings()

    def _on_media_player_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            tracks = self.media_player.audioTracks()
            if len(tracks) >= 3:
                self.media_player.setActiveAudioTrack(1)
            elif len(tracks) > 0:
                self.media_player.setActiveAudioTrack(0)
            self.media_loaded = True
            self._check_both_loaded_and_play()

    def _on_mic_player_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            tracks = self.mic_player.audioTracks()
            if len(tracks) >= 3:
                self.mic_player.setActiveAudioTrack(2)
            elif len(tracks) == 2:
                self.mic_player.setActiveAudioTrack(1)
            self.mic_loaded = True
            self._check_both_loaded_and_play()

    def _check_both_loaded_and_play(self):
        if getattr(self, 'media_loaded', False) and getattr(self, 'mic_loaded', False):
            self.media_loaded = False
            self.mic_loaded = False
            self.media_player.play()
            self.mic_player.play()
            
            # 再生開始直後のトラック切り替えノイズや大音量を防ぐため、遅延させて音量を復元する
            QTimer.singleShot(150, self._restore_volume)

    def _restore_volume(self):
        self.audio_output.setVolume(self.current_sys_volume / 2.0)
        if len(self.mic_player.audioTracks()) >= 2:
            self.mic_audio_output.setVolume(self.current_mic_volume / 2.0)
        else:
            self.mic_audio_output.setVolume(0)

    def _save_volume_settings(self):
        self.config.PLAYER_SYS_VOLUME = self.current_sys_volume
        self.config.PLAYER_MIC_VOLUME = self.current_mic_volume
        self.config.save()

    def load_recording(self, json_filename):
        self.media_loaded = False
        self.mic_loaded = False
        self.current_json_filename = json_filename
        json_path = os.path.join(self.config.SAVE_DIR, json_filename)
        
        self.set_target_playback_rate(1.0)
        
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                self.current_match_data = json.load(f)
                
            # ロード時およびトラック切り替え時の音漏れ（大音量）を防ぐため、一時的にミュートする
            self.audio_output.setVolume(0)
            self.mic_audio_output.setVolume(0)
                
            video_path = find_video_for_json(self.config.SAVE_DIR, json_filename, self.current_match_data)
            
            if video_path and os.path.exists(video_path):
                abs_path = os.path.abspath(video_path)
                url = QUrl.fromLocalFile(abs_path)
                self.media_player.setSource(url)
                self.mic_player.setSource(url)
                # ここではplay()を呼ばず、_check_both_loaded_and_playに任せる
            else:
                self.media_player.setSource(QUrl())
                self.mic_player.setSource(QUrl())
                print(f"[PlayerVideoPage] Video file not found for {json_filename}.")
                self._update_timeline_data(0)
                
        except Exception as e:
            print(f"[PlayerVideoPage] Error loading recording data: {e}")

    def _update_timeline_data(self, duration_ms):
        if not self.current_match_data:
            return
            
        match_info = self.current_match_data.get("match_info", self.current_match_data)
        riot_id = getattr(self.config, "RIOT_ID", "").lower()
        tag_line = getattr(self.config, "TAG_LINE", "").lower()
        
        rounds, events = build_timeline_data(match_info, duration_ms, riot_id, tag_line)
        
        self.current_rounds = rounds
        self.timeline_overlay.set_data(rounds, events)

    def handle_media_error(self, error, error_string):
        print(f"[PlayerVideoPage] Playback Error: {error_string} (Code: {error})")

    def toggle_play(self):
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
            self.mic_player.pause()
        else:
            self.media_player.play()
            self.mic_player.play()

    def _on_playback_state_changed(self, state):
        self.playback_controls.set_playback_state(state == QMediaPlayer.PlaybackState.PlayingState)

    def skip_backward(self):
        pos = max(0, self.media_player.position() - 5000)
        self.set_position(pos)

    def skip_forward(self):
        pos = min(self.media_player.duration(), self.media_player.position() + 5000)
        self.set_position(pos)

    def skip_to_prev_round(self):
        if not hasattr(self, 'current_rounds') or not self.current_rounds:
            return
        current_pos = self.media_player.position()
        target_pos = 0
        for r in reversed(self.current_rounds):
            if r["start"] < current_pos - 2000:
                target_pos = r["start"]
                break
        self.set_position(target_pos)

    def skip_to_next_round(self):
        if not hasattr(self, 'current_rounds') or not self.current_rounds:
            return
        current_pos = self.media_player.position()
        target_pos = self.media_player.duration()
        for r in self.current_rounds:
            if r["start"] > current_pos + 2000:
                target_pos = r["start"]
                break
        self.set_position(target_pos)

    def format_time(self, ms):
        s = ms // 1000
        m = s // 60
        s = s % 60
        return f"{m:02d}:{s:02d}"

    def position_changed(self, position):
        self.timeline_overlay.set_position(position)
        duration = self.media_player.duration()
        self.time_label.setText(f"{self.format_time(position)} / {self.format_time(duration)}")

    def duration_changed(self, duration):
        self.timeline_overlay.set_duration(duration)
        position = self.media_player.position()
        self.time_label.setText(f"{self.format_time(position)} / {self.format_time(duration)}")
        
        if duration > 0:
            self._update_timeline_data(duration)

    def set_position(self, position):
        self.media_player.setPosition(position)
        self.mic_player.setPosition(position)

    def showEvent(self, event):
        super().showEvent(event)
        # 設定画面などで変更された最新の音量を読み込んで反映
        sys_vol = float(getattr(self.config, 'PLAYER_SYS_VOLUME', '1.0'))
        if self.current_sys_volume != sys_vol:
            self.current_sys_volume = sys_vol
            self.volume_widget.set_volume(int(sys_vol * 100))
            self.audio_output.setVolume(sys_vol / 2.0)
            
        mic_vol = float(getattr(self.config, 'PLAYER_MIC_VOLUME', '1.0'))
        if self.current_mic_volume != mic_vol:
            self.current_mic_volume = mic_vol
            self.mic_volume_widget.set_volume(int(mic_vol * 100))
            self.mic_audio_output.setVolume(mic_vol / 2.0)