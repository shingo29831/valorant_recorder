import os
import json
import re
import subprocess
from datetime import datetime
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QMessageBox
from PyQt6.QtCore import Qt, QUrl, QSize, pyqtSignal, QByteArray, QTimer, QThread, QEvent
from PyQt6.QtGui import QIcon, QPixmap, QPainter
from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtSvg import QSvgRenderer
from core.config import Config
from ui.video_player_core import VideoPlayerCore
from core.i18n import get_trans
from ui.player_components import ClickableVideoWidget, PlayerContainer
from ui.volume_widgets import VolumeWidget, MicVolumeWidget
from ui.timeline_overlay import TimelineOverlay
from ui.player_utils import find_video_for_json, guess_player_name
from ui.player_list_actions import toggle_favorite_status, delete_single_record_files
from ui.event_toggle_widget import EventToggleWidget
from ui.clip_generator_thread import ClipGeneratorThread
from PyQt6.QtWidgets import QGraphicsOpacityEffect
from PyQt6.QtCore import QPropertyAnimation
from ui.timeline_builder import build_timeline_data
from ui.video_playback_controls import PlaybackControlsWidget
from ui.clip_edit_panel import ClipEditPanel
from ui.in_app_notification import InAppNotification
from ui.player_clip_controller import PlayerClipController
from ui.player_navigation_controller import PlayerNavigationController

BACK_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white">
  <path d="M20,11V13H8L13.5,18.5L12.08,19.92L4.16,12L12.08,4.08L13.5,5.5L8,11H20Z" />
</svg>"""

SCISSORS_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white">
  <path d="M9.64,7.64C9.87,7.14 10,6.59 10,6C10,3.79 8.21,2 6,2C3.79,2 2,3.79 2,6C2,8.21 3.79,10 6,10C6.59,10 7.14,9.87 7.64,9.64L10,12L7.64,14.36C7.14,14.13 6.59,14 6,14C3.79,14 2,15.79 2,18C2,20.21 3.79,22 6,22C8.21,22 10,20.21 10,18C10,17.41 9.87,16.86 9.64,16.36L12,14L19,21H22V20L9.64,7.64M6,8C4.9,8 4,7.1 4,6C4,4.9 4.9,4 6,4C7.1,4 8,4.9 8,6C8,7.1 7.1,8 6,8M6,20C4.9,20 4,19.1 4,18C4,16.9 4.9,16 6,16C7.1,16 8,16.9 8,18C8,19.1 7.1,20 6,20M12,12.5C11.72,12.5 11.5,12.28 11.5,12C11.5,11.72 11.72,11.5 12,11.5C12.28,11.5 12.5,11.72 12.5,12C12.5,12.28 12.28,12.5 12,12.5M19,3H22V4L14,12L11.64,9.64L19,3Z" />
</svg>"""

ZOOM_IN_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white">
  <path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z"/>
</svg>"""

ZOOM_OUT_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white">
  <path d="M19 13H5v-2h14v2z"/>
</svg>"""

STAR_FILLED_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="#FFD700">
  <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/>
</svg>"""

STAR_OUTLINE_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="transparent" stroke="white" stroke-width="2">
  <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/>
</svg>"""

TRASH_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white">
  <path d="M16 9v10H8V9h8m-1.5-6h-5l-1 1H5v2h14V4h-3.5l-1-1zM18 7H6v12c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7z"/>
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
        
        self.player_core = VideoPlayerCore(self.video_widget, self.config, self)
        self.player_core.positionChanged.connect(self.position_changed)
        self.player_core.durationChanged.connect(self.duration_changed)
        self.player_core.playbackStateChanged.connect(self._on_playback_state_changed)
        self.player_core.errorOccurred.connect(self.handle_media_error)
        
        self.timeline_overlay = TimelineOverlay()
        self.timeline_overlay.seekStarted.connect(self.player_core.on_seek_started)
        self.timeline_overlay.seekRequested.connect(self.player_core.on_seek_requested)
        self.timeline_overlay.seekFinished.connect(self.player_core.on_seek_finished)
        
        controls_widget = QWidget()
        controls_layout = QHBoxLayout(controls_widget)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        
        self.volume_widget = VolumeWidget()
        self.volume_widget.set_volume(int(self.player_core.current_sys_volume * 100))
        self.volume_widget.volumeChanged.connect(self.player_core.set_sys_volume)
        
        self.mic_volume_widget = MicVolumeWidget()
        self.mic_volume_widget.set_volume(int(self.player_core.current_mic_volume * 100))
        self.mic_volume_widget.volumeChanged.connect(self.player_core.set_mic_volume)
        
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setFixedWidth(100)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.zoom_out_btn = QPushButton()
        self.zoom_out_btn.setFixedSize(24, 24)
        self.zoom_out_btn.setStyleSheet("border-radius: 12px; background-color: #333333;")
        self.zoom_out_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.zoom_out_btn.setIcon(self._create_icon(ZOOM_OUT_SVG, 16))
        self.zoom_out_btn.clicked.connect(self.zoom_out_timeline)
        self.zoom_out_btn.hide()
        
        self.zoom_in_btn = QPushButton()
        self.zoom_in_btn.setFixedSize(24, 24)
        self.zoom_in_btn.setStyleSheet("border-radius: 12px; background-color: #333333;")
        self.zoom_in_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.zoom_in_btn.setIcon(self._create_icon(ZOOM_IN_SVG, 16))
        self.zoom_in_btn.clicked.connect(self.zoom_in_timeline)
        self.zoom_in_btn.hide()
        
        controls_layout.addWidget(self.volume_widget)
        controls_layout.addWidget(self.mic_volume_widget)
        controls_layout.addWidget(self.time_label)
        controls_layout.addWidget(self.timeline_overlay)
        controls_layout.addWidget(self.zoom_out_btn)
        controls_layout.addWidget(self.zoom_in_btn)
        
        self.player_container = PlayerContainer(self.video_widget)
        
        # クリップ編集パネル
        self.edit_panel = ClipEditPanel(self.t)
        self.edit_panel.setVisible(False)

        right_container = QWidget()
        right_container.setFixedWidth(50)
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)

        self.edit_mode_btn = QPushButton()
        self.edit_mode_btn.setFixedSize(40, 40)
        self.edit_mode_btn.setStyleSheet("border-radius: 20px; background-color: #333333;")
        self.edit_mode_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.edit_mode_btn.setIcon(self._create_icon(SCISSORS_SVG, 24))
        self.edit_mode_btn.setIconSize(QSize(24, 24))
        self.edit_mode_btn.setToolTip(self.t.create_clip)

        self.fav_btn = QPushButton()
        self.fav_btn.setFixedSize(40, 40)
        self.fav_btn.setStyleSheet("""
            QPushButton { border-radius: 20px; background-color: #333333; }
            QPushButton:hover { background-color: #444444; }
        """)
        self.fav_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fav_btn.setIcon(self._create_icon(STAR_OUTLINE_SVG, 22))
        self.fav_btn.setIconSize(QSize(22, 22))
        self.fav_btn.setToolTip(getattr(self.t, 'filter_favorite', "お気に入り"))
        self.fav_btn.clicked.connect(self.toggle_favorite)

        self.delete_btn = QPushButton()
        self.delete_btn.setFixedSize(40, 40)
        self.delete_btn.setStyleSheet("""
            QPushButton { border-radius: 20px; background-color: #333333; }
            QPushButton:hover { background-color: #FF4655; }
        """)
        self.delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.delete_btn.setIcon(self._create_icon(TRASH_SVG, 22))
        self.delete_btn.setIconSize(QSize(22, 22))
        self.delete_btn.setToolTip(getattr(self.t, 'confirm_deletion_title', "削除"))
        self.delete_btn.clicked.connect(self.delete_recording)
        
        self.event_toggle_widget = EventToggleWidget()
        self.event_toggle_widget.filterChanged.connect(self.timeline_overlay.set_filters)
        
        right_layout.addWidget(self.edit_mode_btn, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        right_layout.addWidget(self.fav_btn, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        right_layout.addWidget(self.delete_btn, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        right_layout.addStretch()
        right_layout.addWidget(self.event_toggle_widget, alignment=Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter)
        
        top_layout.addWidget(left_container)
        top_layout.addWidget(self.player_container, stretch=1)
        top_layout.addWidget(right_container)
        
        page_layout.addLayout(top_layout, stretch=1)
        page_layout.addWidget(controls_widget)
        
        self.playback_controls = PlaybackControlsWidget()
        self.playback_controls.togglePlayRequested.connect(self.toggle_play)
        
        page_layout.addWidget(self.playback_controls)
        page_layout.addWidget(self.edit_panel)
        
        self.speed_osd_label = QLabel("1.0x", self.video_widget)
        self.speed_osd_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.speed_osd_label.setStyleSheet("background-color: rgba(0, 0, 0, 150); color: white; font-size: 24px; font-weight: bold; border-radius: 10px; padding: 10px;")
        self.speed_osd_label.hide()

        self.nav_controller = PlayerNavigationController(
            player_core=self.player_core,
            playback_controls=self.playback_controls,
            video_widget=self.video_widget,
            volume_widget=self.volume_widget,
            speed_osd_label=self.speed_osd_label,
            parent=self,
        )

        # 別ウィンドウのNotificationOverlayを廃止し、アプリ内描画のInAppNotificationに変更
        self.notification = InAppNotification(self)
        self.clip_controller = PlayerClipController(
            config=self.config,
            t=self.t,
            player_core=self.player_core,
            timeline_overlay=self.timeline_overlay,
            edit_panel=self.edit_panel,
            edit_mode_btn=self.edit_mode_btn,
            zoom_in_btn=self.zoom_in_btn,
            zoom_out_btn=self.zoom_out_btn,
            notification=self.notification,
            format_time_fn=self.format_time,
            parent=self,
        )
        self.video_widget.installEventFilter(self)

    def _create_icon(self, svg_bytes, size=24):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer = QSvgRenderer(QByteArray(svg_bytes))
        renderer.render(painter)
        painter.end()
        return QIcon(pixmap)

    def eventFilter(self, obj, event):
        if self.nav_controller.handle_event_filter(obj, event):
            return True
        return super().eventFilter(obj, event)

    def zoom_in_timeline(self):
        current_zoom = self.timeline_overlay.zoom_factor
        self.timeline_overlay.set_zoom(current_zoom * 1.5, center_ms=self.player_core.position())

    def zoom_out_timeline(self):
        current_zoom = self.timeline_overlay.zoom_factor
        self.timeline_overlay.set_zoom(current_zoom / 1.5, center_ms=self.player_core.position())

    def _update_favorite_icon(self, is_favorite: bool):
        svg = STAR_FILLED_SVG if is_favorite else STAR_OUTLINE_SVG
        self.fav_btn.setIcon(self._create_icon(svg, 22))

    def toggle_favorite(self):
        if not getattr(self, 'current_json_filename', None) or not self.current_match_data:
            return
        toggle_favorite_status(self.config.SAVE_DIR, self.current_json_filename)
        is_fav = not self.current_match_data.get("is_favorite", False)
        self.current_match_data["is_favorite"] = is_fav
        self._update_favorite_icon(is_fav)

    def delete_recording(self):
        if not getattr(self, 'current_json_filename', None):
            return
        title = getattr(self.t, 'confirm_deletion_title', '削除の確認')
        msg = getattr(self.t, 'confirm_deletion_msg', 'この録画を削除してもよろしいですか？')
        reply = QMessageBox.question(
            self,
            title,
            msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            json_filename = self.current_json_filename
            self.player_core.load_source(None)
            delete_single_record_files(self.config.SAVE_DIR, json_filename)
            self.current_json_filename = None
            self.current_match_data = None
            self.backRequested.emit()

    def request_back(self):
        self.player_core.load_source(None)
        self.backRequested.emit()

    def cleanup_media(self):
        self.player_core.pause()

    def restore_media(self):
        pass

    def load_recording(self, json_filename):
        self.player_core.load_source(None)
        self.current_json_filename = json_filename
        json_path = os.path.join(self.config.SAVE_DIR, json_filename)
        self.nav_controller.reset_playback_rate()
        
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                self.current_match_data = json.load(f)
            self._update_favorite_icon(bool(self.current_match_data.get("is_favorite", False)))
            self.clip_controller.set_current_recording(json_filename, self.current_match_data)
            video_path = find_video_for_json(self.config.SAVE_DIR, json_filename, self.current_match_data)
            
            if video_path and os.path.exists(video_path):
                self.player_core.load_source(video_path)
            else:
                self.player_core.load_source(None)
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
        self.nav_controller.set_current_rounds(rounds)
        self.timeline_overlay.set_data(rounds, events)

    def handle_media_error(self, error, error_string):
        print(f"[PlayerVideoPage] Playback Error: {error_string} (Code: {error})")
        # Demuxing failed などの ResourceError が発生した場合、ファイル破損の旨を通知する
        if error == QMediaPlayer.Error.ResourceError:
            error_msg = getattr(self.t, 'video_corrupted', "動画ファイルが破損しているか、不完全です。")
            self.notification.show_message(error_msg, duration=5000)

    def toggle_play(self):
        self.player_core.toggle_play()

    def _on_playback_state_changed(self, state):
        self.playback_controls.set_playback_state(state == QMediaPlayer.PlaybackState.PlayingState.value)

    def format_time(self, ms):
        s = ms // 1000
        m = s // 60
        s = s % 60
        return f"{m:02d}:{s:02d}"

    def position_changed(self, position):
        self.timeline_overlay.set_position(position)
        duration = self.player_core.duration()
        self.time_label.setText(f"{self.format_time(position)} / {self.format_time(duration)}")

    def duration_changed(self, duration):
        self.timeline_overlay.set_duration(duration)
        position = self.player_core.position()
        self.time_label.setText(f"{self.format_time(position)} / {self.format_time(duration)}")
        
        if duration > 0:
            self._update_timeline_data(duration)

    def showEvent(self, event):
        super().showEvent(event)
        sys_vol = float(getattr(self.config, 'PLAYER_SYS_VOLUME', '1.0'))
        if self.player_core.current_sys_volume != sys_vol:
            self.player_core.set_sys_volume(sys_vol * 100)
            self.volume_widget.set_volume(int(sys_vol * 100))
            
        mic_vol = float(getattr(self.config, 'PLAYER_MIC_VOLUME', '1.0'))
        if self.player_core.current_mic_volume != mic_vol:
            self.player_core.set_mic_volume(mic_vol * 100)
            self.mic_volume_widget.set_volume(int(mic_vol * 100))
            
        self.nav_controller.apply_playback_rate()
