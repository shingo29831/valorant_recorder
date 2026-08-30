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
from ui.player_components import ClickableVideoWidget, VolumeWidget, MicVolumeWidget, TimelineOverlay, PlayerContainer
from ui.player_utils import find_video_for_json, guess_player_name
from ui.event_toggle_widget import EventToggleWidget
from ui.notification_overlay import NotificationOverlay

BACK_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white">
  <path d="M20,11V13H8L13.5,18.5L12.08,19.92L4.16,12L12.08,4.08L13.5,5.5L8,11H20Z" />
</svg>"""

SCISSORS_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white">
  <path d="M9.64,7.64C9.87,7.14 10,6.59 10,6C10,3.79 8.21,2 6,2C3.79,2 2,3.79 2,6C2,8.21 3.79,10 6,10C6.59,10 7.14,9.87 7.64,9.64L10,12L7.64,14.36C7.14,14.13 6.59,14 6,14C3.79,14 2,15.79 2,18C2,20.21 3.79,22 6,22C8.21,22 10,20.21 10,18C10,17.41 9.87,16.86 9.64,16.36L12,14L19,21H22V20L9.64,7.64M6,8C4.9,8 4,7.1 4,6C4,4.9 4.9,4 6,4C7.1,4 8,4.9 8,6C8,7.1 7.1,8 6,8M6,20C4.9,20 4,19.1 4,18C4,16.9 4.9,16 6,16C7.1,16 8,16.9 8,18C8,19.1 7.1,20 6,20M12,12.5C11.72,12.5 11.5,12.28 11.5,12C11.5,11.72 11.72,11.5 12,11.5C12.28,11.5 12.5,11.72 12.5,12C12.5,12.28 12.28,12.5 12,12.5M19,3H22V4L14,12L11.64,9.64L19,3Z" />
</svg>"""

PLAY_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white"><path d="M8,5.14V19.14L19,12.14L8,5.14Z" /></svg>"""
PAUSE_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white"><path d="M14,19H18V5H14M6,19H10V5H6V19Z" /></svg>"""
SKIP_BACK_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white"><path d="M11.5,12L20,18V6M11,18V6L2.5,12L11,18Z" /></svg>"""
SKIP_FORWARD_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white"><path d="M13,6V18L21.5,12M4,18L12.5,12L4,6V18Z" /></svg>"""
PREV_ROUND_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white"><path d="M6,6H8V18H6M9.5,12L18,18V6M16,14.14L12.97,12L16,9.86V14.14Z" /></svg>"""
NEXT_ROUND_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white"><path d="M16,18H18V6H16M8,6L16.5,12L8,18V6Z" /></svg>"""
MINUS_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white"><path d="M19,13H5V11H19V13Z" /></svg>"""
PLUS_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white"><path d="M19,13H13V19H11V13H5V11H11V5H13V11H19V13Z" /></svg>"""

class ClipGeneratorThread(QThread):
    finished = pyqtSignal(bool, str)
    
    def __init__(self, ffmpeg_path, input_path, output_path, start_ms, end_ms, encoder, sys_volume, mic_volume, audio_track_count):
        super().__init__()
        self.ffmpeg_path = ffmpeg_path
        self.input_path = input_path
        self.output_path = output_path
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.encoder = encoder
        self.sys_volume = sys_volume
        self.mic_volume = mic_volume
        self.audio_track_count = audio_track_count
        
    def run(self):
        try:
            start_sec = self.start_ms / 1000.0
            end_sec = self.end_ms / 1000.0
            duration = end_sec - start_sec
            
            preset = "p4" if "nvenc" in self.encoder else "veryfast"
            
            cmd = [
                self.ffmpeg_path,
                "-y",
                "-ss", f"{start_sec:.3f}",
                "-i", self.input_path,
                "-t", f"{duration:.3f}",
                "-map", "0:v:0",
                "-c:v", self.encoder,
                "-preset", preset,
                "-b:v", "10M",
                "-c:a", "aac",
                "-b:a", "192k"
            ]
            
            if self.audio_track_count >= 3:
                # トラック2(システム音)とトラック3(マイク音)をミックス
                filter_complex = f"[0:a:1]volume={self.sys_volume}[a0];[0:a:2]volume={self.mic_volume}[a1];[a0][a1]amix=inputs=2:duration=longest[aout]"
                cmd.extend(["-filter_complex", filter_complex, "-map", "[aout]"])
            elif self.audio_track_count == 2:
                # トラック1(システム音)とトラック2(マイク音)をミックス
                filter_complex = f"[0:a:0]volume={self.sys_volume}[a0];[0:a:1]volume={self.mic_volume}[a1];[a0][a1]amix=inputs=2:duration=longest[aout]"
                cmd.extend(["-filter_complex", filter_complex, "-map", "[aout]"])
            else:
                # トラック1のみ
                filter_complex = f"[0:a:0]volume={self.sys_volume}[aout]"
                cmd.extend(["-filter_complex", filter_complex, "-map", "[aout]"])
                
            cmd.append(self.output_path)
            
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=creationflags)
            
            if res.returncode == 0:
                self.finished.emit(True, self.output_path)
            else:
                self.finished.emit(False, res.stderr)
        except Exception as e:
            self.finished.emit(False, str(e))

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
        self.audio_output.setVolume(min(self.current_sys_volume, 1.0))
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.setVideoOutput(self.video_widget)
        self.media_player.mediaStatusChanged.connect(self._on_media_player_status_changed)
        
        # マイク音を同時に再生・独立制御するためのサブプレイヤー
        self.mic_player = QMediaPlayer(self)
        self.mic_audio_output = QAudioOutput(self)
        self.mic_audio_output.setVolume(min(self.current_mic_volume, 1.0))
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
        self.volume_widget.set_volume(self.current_sys_volume)
        self.volume_widget.volumeChanged.connect(self.on_sys_volume_changed)
        
        self.mic_volume_widget = MicVolumeWidget()
        self.mic_volume_widget.set_volume(self.current_mic_volume)
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
        self.edit_panel = QWidget()
        self.edit_panel.setStyleSheet("background-color: #222222; border-radius: 5px;")
        edit_layout = QHBoxLayout(self.edit_panel)
        
        self.start_btn = QPushButton(self.t.set_start)
        self.start_btn.clicked.connect(self._set_clip_start)
        self.start_label = QLabel("00:00")
        
        self.end_btn = QPushButton(self.t.set_end)
        self.end_btn.clicked.connect(self._set_clip_end)
        self.end_label = QLabel("00:00")
        
        self.generate_btn = QPushButton(self.t.generate)
        self.generate_btn.setStyleSheet("background-color: #FF4655; font-weight: bold;")
        self.generate_btn.clicked.connect(self._generate_clip)
        
        self.cancel_edit_btn = QPushButton(self.t.cancel)
        self.cancel_edit_btn.clicked.connect(self._toggle_edit_mode)
        
        edit_layout.addWidget(self.start_btn)
        edit_layout.addWidget(self.start_label)
        edit_layout.addSpacing(20)
        edit_layout.addWidget(self.end_btn)
        edit_layout.addWidget(self.end_label)
        edit_layout.addStretch()
        edit_layout.addWidget(self.cancel_edit_btn)
        edit_layout.addWidget(self.generate_btn)
        
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
        
        playback_control_layout = QHBoxLayout()
        playback_control_layout.setContentsMargins(0, 0, 0, 0)
        
        playback_control_layout.addStretch(1)
        
        center_layout = QHBoxLayout()
        center_layout.setSpacing(15)
        
        btn_style = "QPushButton { border-radius: 20px; background-color: #333333; } QPushButton:hover { background-color: #444444; }"
        
        self.prev_round_btn = QPushButton()
        self.prev_round_btn.setFixedSize(40, 40)
        self.prev_round_btn.setStyleSheet(btn_style)
        self.prev_round_btn.setIcon(self._create_icon(PREV_ROUND_SVG))
        self.prev_round_btn.setIconSize(QSize(24, 24))
        self.prev_round_btn.clicked.connect(self.skip_to_prev_round)
        
        self.skip_back_btn = QPushButton()
        self.skip_back_btn.setFixedSize(40, 40)
        self.skip_back_btn.setStyleSheet(btn_style)
        self.skip_back_btn.setIcon(self._create_icon(SKIP_BACK_SVG))
        self.skip_back_btn.setIconSize(QSize(24, 24))
        self.skip_back_btn.clicked.connect(self.skip_backward)
        
        self.play_pause_btn = QPushButton()
        self.play_pause_btn.setFixedSize(50, 50)
        self.play_pause_btn.setStyleSheet("QPushButton { border-radius: 25px; background-color: #FF4655; } QPushButton:hover { background-color: #FF5865; }")
        self.play_pause_btn.setIcon(self._create_icon(PLAY_SVG))
        self.play_pause_btn.setIconSize(QSize(30, 30))
        self.play_pause_btn.clicked.connect(self.toggle_play)
        
        self.skip_forward_btn = QPushButton()
        self.skip_forward_btn.setFixedSize(40, 40)
        self.skip_forward_btn.setStyleSheet(btn_style)
        self.skip_forward_btn.setIcon(self._create_icon(SKIP_FORWARD_SVG))
        self.skip_forward_btn.setIconSize(QSize(24, 24))
        self.skip_forward_btn.clicked.connect(self.skip_forward)
        
        self.next_round_btn = QPushButton()
        self.next_round_btn.setFixedSize(40, 40)
        self.next_round_btn.setStyleSheet(btn_style)
        self.next_round_btn.setIcon(self._create_icon(NEXT_ROUND_SVG))
        self.next_round_btn.setIconSize(QSize(24, 24))
        self.next_round_btn.clicked.connect(self.skip_to_next_round)
        
        center_layout.addWidget(self.prev_round_btn)
        center_layout.addWidget(self.skip_back_btn)
        center_layout.addWidget(self.play_pause_btn)
        center_layout.addWidget(self.skip_forward_btn)
        center_layout.addWidget(self.next_round_btn)
        
        playback_control_layout.addLayout(center_layout)
        
        playback_control_layout.addStretch(1)
        
        speed_layout = QHBoxLayout()
        speed_layout.setSpacing(5)
        
        speed_btn_style = "QPushButton { border-radius: 15px; background-color: #333333; } QPushButton:hover { background-color: #444444; }"
        
        self.speed_minus_btn = QPushButton()
        self.speed_minus_btn.setFixedSize(30, 30)
        self.speed_minus_btn.setStyleSheet(speed_btn_style)
        self.speed_minus_btn.setIcon(self._create_icon(MINUS_SVG))
        self.speed_minus_btn.setIconSize(QSize(16, 16))
        self.speed_minus_btn.clicked.connect(self.decrease_speed)
        
        self.speed_label_ui = QLabel("1.0x")
        self.speed_label_ui.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.speed_label_ui.setFixedSize(60, 30)
        self.speed_label_ui.setStyleSheet("QLabel { border-radius: 15px; background-color: transparent; font-size: 16px; font-weight: bold; color: white; } QLabel:hover { background-color: rgba(255, 255, 255, 0.1); }")
        self.speed_label_ui.setCursor(Qt.CursorShape.PointingHandCursor)
        self.speed_label_ui.installEventFilter(self)
        
        self.speed_plus_btn = QPushButton()
        self.speed_plus_btn.setFixedSize(30, 30)
        self.speed_plus_btn.setStyleSheet(speed_btn_style)
        self.speed_plus_btn.setIcon(self._create_icon(PLUS_SVG))
        self.speed_plus_btn.setIconSize(QSize(16, 16))
        self.speed_plus_btn.clicked.connect(self.increase_speed)
        
        speed_layout.addWidget(self.speed_minus_btn)
        speed_layout.addWidget(self.speed_label_ui)
        speed_layout.addWidget(self.speed_plus_btn)
        
        playback_control_layout.addLayout(speed_layout)
        
        page_layout.addLayout(playback_control_layout)
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
        if obj == self.speed_label_ui:
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self.toggle_speed()
                return True
        elif obj == self.video_widget:
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
        self.speed_label_ui.setText(f"{rate:.1f}x")
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
        
        if self.target_playback_rate != 1.0 and not self.is_speed_bypassed:
            self.speed_label_ui.setStyleSheet("QLabel { border-radius: 15px; background-color: rgba(255, 70, 85, 0.5); font-size: 16px; font-weight: bold; color: white; } QLabel:hover { background-color: rgba(255, 70, 85, 0.7); }")
        else:
            self.speed_label_ui.setStyleSheet("QLabel { border-radius: 15px; background-color: transparent; font-size: 16px; font-weight: bold; color: white; } QLabel:hover { background-color: rgba(255, 255, 255, 0.1); }")
            
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
        self.start_label.setText(self.format_time(self.clip_start_ms))
        self.end_label.setText(self.format_time(self.clip_end_ms))

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
        
        self.generate_btn.setEnabled(False)
        self.generate_btn.setText(self.t.generating_clip)
        
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
        self.generate_btn.setEnabled(True)
        self.generate_btn.setText(self.t.generate)
        
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
        vol_float = float(volume)
        self.current_sys_volume = vol_float
        # 再生プレイヤーの上限は1.0(100%)に制限しつつ、保存値は200%を許容
        self.audio_output.setVolume(min(vol_float, 1.0))
        self._save_volume_settings()

    def on_mic_volume_changed(self, volume):
        vol_float = float(volume)
        self.current_mic_volume = vol_float
        self.mic_audio_output.setVolume(min(vol_float, 1.0))
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
        self.audio_output.setVolume(min(self.current_sys_volume, 1.0))
        if len(self.mic_player.audioTracks()) >= 2:
            self.mic_audio_output.setVolume(min(self.current_mic_volume, 1.0))
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
        
        offset_ms = 0
        local_round_events = match_info.get("local_round_events", [])
        kills_data = match_info.get("kills", [])
        
        api_first_start = 0
        if kills_data:
            first_kill = kills_data[0]
            k_match = first_kill.get("kill_time_in_match", 0)
            k_round = first_kill.get("kill_time_in_round", 0)
            if k_match > 0 and k_round > 0:
                api_first_start = k_match - k_round

        if local_round_events and api_first_start > 0:
            first_local_preround = next((ev for ev in local_round_events if ev["phase"] == "PreRound"), None)
            if first_local_preround:
                offset_ms = api_first_start - first_local_preround["time_ms"]
            else:
                first_local_inprogress = next((ev for ev in local_round_events if ev["phase"] == "InProgress"), None)
                if first_local_inprogress:
                    offset_ms = api_first_start - first_local_inprogress["time_ms"]
        elif "local_match_start_time" in match_info and "local_match_end_time" in match_info and duration_ms > 0:
            game_length = match_info.get("metadata", {}).get("game_length")
            if game_length:
                game_length_sec = game_length / 1000.0 if game_length > 100000 else game_length
                end_delay_sec = 6.5
                offset_sec = game_length_sec + end_delay_sec - (duration_ms / 1000.0)
                offset_ms = int(offset_sec * 1000)
            else:
                start_time = match_info["local_match_start_time"]
                end_time = match_info["local_match_end_time"]
                video_zero_local = end_time - (duration_ms / 1000.0)
                offset_sec = video_zero_local - start_time
                offset_ms = int(offset_sec * 1000)
        else:
            if "video_offset_ms" in match_info:
                offset_ms = match_info["video_offset_ms"]
            else:
                video_path = match_info.get("local_video_path", "")
                basename = os.path.basename(video_path)
                date_pattern = re.compile(r"(\d{8}_\d{6})")
                vid_match = date_pattern.search(basename)
                if vid_match:
                    try:
                        vid_time = datetime.strptime(vid_match.group(1), "%Y%m%d_%H%M%S")
                        vid_timestamp = vid_time.timestamp()
                        game_start = match_info.get("metadata", {}).get("game_start")
                        if game_start:
                            offset_ms = int((vid_timestamp - game_start) * 1000)
                    except Exception:
                        pass

        events = []
        rounds = []
        
        target_puuid = None
        target_display_name = None
        
        riot_id = getattr(self.config, "RIOT_ID", "").lower()
        tag_line = getattr(self.config, "TAG_LINE", "").lower()
        
        players = match_info.get("players", {}).get("all_players", [])
        for p in players:
            p_name = p.get("name", "").lower()
            p_tag = p.get("tag", "").lower()
            if p_name == riot_id and p_tag == tag_line:
                target_puuid = p.get("puuid")
                target_display_name = p.get("name")
                break
        
        if not target_puuid and kills_data:
            target_display_name = guess_player_name(kills_data)
            
        for kill in kills_data:
            time_ms = int(kill.get("kill_time_in_match", 0) - offset_ms)
            if time_ms < 0:
                continue
            
            if target_puuid:
                killer_puuid = kill.get("killer_puuid")
                victim_puuid = kill.get("victim_puuid")
                
                assistants = kill.get("assistants", [])
                assistant_puuids = []
                for ast in assistants:
                    if isinstance(ast, dict):
                        assistant_puuids.append(ast.get("assistant_puuid"))
                    elif isinstance(ast, str):
                        assistant_puuids.append(ast)
                        
                if killer_puuid == target_puuid:
                    events.append({"time": time_ms, "type": "kill"})
                elif victim_puuid == target_puuid:
                    events.append({"time": time_ms, "type": "death"})
                elif target_puuid in assistant_puuids:
                    events.append({"time": time_ms, "type": "assist"})
            else:
                killer = kill.get("killer_display_name", "Unknown")
                victim = kill.get("victim_display_name", "Unknown")
                
                assistants = kill.get("assistants", [])
                assistant_names = []
                for ast in assistants:
                    if isinstance(ast, dict):
                        assistant_names.append(ast.get("assistant_display_name", ""))
                    elif isinstance(ast, str):
                        assistant_names.append(ast)
                        
                if target_display_name and target_display_name in killer:
                    events.append({"time": time_ms, "type": "kill"})
                elif target_display_name and target_display_name in victim:
                    events.append({"time": time_ms, "type": "death"})
                elif target_display_name and any(target_display_name in ast for ast in assistant_names):
                    events.append({"time": time_ms, "type": "assist"})
                elif not target_display_name:
                    events.append({"time": time_ms, "type": "kill"})
                
        if local_round_events:
            for i in range(len(local_round_events)):
                ev = local_round_events[i]
                phase = ev["phase"]
                start_time = ev["time_ms"]
                end_time = duration_ms
                if i + 1 < len(local_round_events):
                    end_time = local_round_events[i+1]["time_ms"]
                    
                if phase in ["PreRound", "InProgress", "PostRound"]:
                    rounds.append({"start": start_time, "end": end_time, "phase": phase})
        else:
            if kills_data:
                round_starts = []
                for k in kills_data:
                    k_match = k.get("kill_time_in_match", 0)
                    k_round = k.get("kill_time_in_round", 0)
                    if k_match > 0 and k_round > 0:
                        r_start = k_match - k_round
                        if not round_starts or abs(round_starts[-1] - r_start) > 5000:
                            round_starts.append(r_start)
                
                for i, r_start in enumerate(round_starts):
                    start = int(r_start - offset_ms)
                    if i + 1 < len(round_starts):
                        end = int(round_starts[i+1] - offset_ms)
                    else:
                        game_length = match_info.get("metadata", {}).get("game_length", 0)
                        end = int(game_length - offset_ms) if game_length > 0 else duration_ms
                    
                    if end > start:
                        rounds.append({"start": max(0, start), "end": end, "phase": "InProgress"})
            
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
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.play_pause_btn.setIcon(self._create_icon(PAUSE_SVG))
        else:
            self.play_pause_btn.setIcon(self._create_icon(PLAY_SVG))

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