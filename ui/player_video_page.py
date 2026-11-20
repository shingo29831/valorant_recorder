import os
import json
import re
import subprocess
from datetime import datetime
from typing import Optional
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QMessageBox
from PyQt6.QtCore import Qt, QUrl, QSize, pyqtSignal, QByteArray, QTimer, QThread, QEvent
from PyQt6.QtGui import QIcon, QPixmap, QPainter
from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtSvg import QSvgRenderer
from core.config import Config
from ui.video_player_core import VideoPlayerCore
from core.i18n import get_trans
from ui.player_components import ClickableVideoWidget, PlayerContainer
from ui.volume_widgets import VolumeWidget, MicVolumeWidget, ChatVolumeWidget
from ui.timeline_overlay import TimelineOverlay
from ui.player_utils import find_video_for_json
from ui.player_list_actions import toggle_favorite_status, delete_single_record_files
from ui.event_toggle_widget import EventToggleWidget
from ui.clip_generator_thread import ClipGeneratorThread
from PyQt6.QtWidgets import QGraphicsOpacityEffect
from PyQt6.QtCore import QPropertyAnimation
from ui.timeline_builder import build_timeline_data, extract_player_sync_events
from ui.video_playback_controls import PlaybackControlsWidget
from ui.clip_edit_panel import ClipEditPanel
from ui.in_app_notification import InAppNotification
from ui.player_clip_controller import PlayerClipController
from ui.player_navigation_controller import PlayerNavigationController
from ui.timeline_auto_sync import AutoSyncResult, TimelineAutoSyncThread
from ui.timeline_sync_panel import TimelineSyncPanel
from ui.video_top_switcher import VideoTopSwitcher, SwitcherState

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

SYNC_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white">
  <path d="M12,4V1L8,5l4,4V6c3.31,0,6,2.69,6,6c0,1.01-0.25,1.97-0.7,2.8l1.46,1.46C19.54,15.03,20,13.57,20,12 C20,7.58,16.42,4,12,4z M12,18c-3.31,0-6-2.69-6-6c0-1.01,0.25-1.97,0.7-2.8L5.24,7.74C4.46,8.97,4,10.43,4,12c0,4.42,3.58,8,8,8v3l4-4 l-4-4V18z"/>
</svg>"""

class PlayerVideoPage(QWidget):
    backRequested = pyqtSignal()

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.config = config
        self.t = get_trans(self.config.LANGUAGE)
        self.current_match_data = None
        self.current_json_filename: Optional[str] = None
        self._pending_video_path: Optional[str] = None
        self._current_video_path: Optional[str] = None
        self._load_sequence: int = 0
        self._auto_sync_thread: Optional[TimelineAutoSyncThread] = None
        self._active_sync_threads: set[TimelineAutoSyncThread] = set()
        self._auto_sync_attempted_seqs: set[int] = set()
        self._switcher_refresh_timer = QTimer(self)
        self._switcher_refresh_timer.setSingleShot(True)
        self._switcher_refresh_timer.setInterval(150)
        self._switcher_refresh_timer.timeout.connect(self._refresh_switcher_safe)

        self._layout_sync_timer = QTimer(self)
        self._layout_sync_timer.setSingleShot(True)
        self._layout_sync_timer.setInterval(0)
        self._layout_sync_timer.timeout.connect(self._update_content_slide_position)
        
        # 動画サイズを変えずに画面全体を下へスライドさせるためのコンテナ
        self.content_widget = QWidget(self)
        self.content_widget.setMouseTracking(True)
        self.page_layout = QVBoxLayout(self.content_widget)
        page_layout = self.page_layout
        page_layout.setContentsMargins(20, 20, 20, 20)
        page_layout.setSpacing(10)
        
        top_layout = QHBoxLayout()
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(10)
        
        left_container = QWidget()
        left_container.setFixedWidth(50)
        self.left_layout = QVBoxLayout(left_container)
        self.left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout = self.left_layout
        
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
        self.video_widget.setMouseTracking(True)
        self.video_widget.clicked.connect(self.toggle_play)
        
        self.player_core = VideoPlayerCore(self.video_widget, self.config, self)
        self.player_core.positionChanged.connect(self.position_changed)
        self.player_core.durationChanged.connect(self.duration_changed)
        self.player_core.playbackStateChanged.connect(self._on_playback_state_changed)
        self.player_core.audioTracksChanged.connect(self._on_audio_tracks_changed)
        self.player_core.errorOccurred.connect(self.handle_media_error)
        
        self.timeline_overlay = TimelineOverlay()
        self.timeline_overlay.seekStarted.connect(self.player_core.on_seek_started)
        self.timeline_overlay.seekRequested.connect(self.player_core.on_seek_requested)
        self.timeline_overlay.seekFinished.connect(self.player_core.on_seek_finished)
        
        self.controls_widget = QWidget()
        controls_layout = QHBoxLayout(self.controls_widget)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        
        self.volume_widget = VolumeWidget()
        self.volume_widget.setToolTip(self.t.system_volume)
        self.volume_widget.set_volume(int(round(self.player_core.current_sys_volume * 100)))
        self.volume_widget.volumeChanged.connect(self.player_core.set_sys_volume)
        
        self.mic_volume_widget = MicVolumeWidget()
        self.mic_volume_widget.setToolTip(self.t.mic_volume)
        self.mic_volume_widget.set_volume(int(round(self.player_core.current_mic_volume * 100)))
        self.mic_volume_widget.volumeChanged.connect(self.player_core.set_mic_volume)

        self.chat_volume_widget = ChatVolumeWidget()
        self.chat_volume_widget.setToolTip(getattr(self.t, "chat_volume", "Chat Volume"))
        self.chat_volume_widget.set_volume(int(round(self.player_core.current_chat_volume * 100)))
        self.chat_volume_widget.volumeChanged.connect(self.player_core.set_chat_volume)
        self.chat_volume_widget.setVisible(False)
        
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
        controls_layout.addWidget(self.chat_volume_widget)
        controls_layout.addWidget(self.time_label)
        controls_layout.addWidget(self.timeline_overlay)
        controls_layout.addWidget(self.zoom_out_btn)
        controls_layout.addWidget(self.zoom_in_btn)
        
        self.player_container = PlayerContainer(self.video_widget)
        self.player_container.setMouseTracking(True)
        
        # クリップ編集パネルと同期調整パネル
        self.edit_panel = ClipEditPanel(self.t)
        self.edit_panel.setVisible(False)

        self.sync_panel = TimelineSyncPanel(self.t)
        self.sync_panel.setVisible(False)
        self.sync_panel.offsetDeltaRequested.connect(self._on_sync_offset_delta)
        self.sync_panel.alignEventRequested.connect(self._on_sync_align_event)
        self.sync_panel.autoSyncRequested.connect(self._on_manual_auto_sync_requested)
        self.sync_panel.resetRequested.connect(self._on_sync_reset)
        self.sync_panel.closeRequested.connect(self.toggle_sync_panel)

        right_container = QWidget()
        right_container.setFixedWidth(50)
        self.right_layout = QVBoxLayout(right_container)
        self.right_layout.setContentsMargins(0, 0, 0, 0)
        self.right_layout.setSpacing(10)
        right_layout = self.right_layout

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

        self.sync_btn = QPushButton()
        self.sync_btn.setFixedSize(40, 40)
        self.sync_btn.setStyleSheet("""
            QPushButton { border-radius: 20px; background-color: #333333; }
            QPushButton:hover { background-color: #444444; }
        """)
        self.sync_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sync_btn.setIcon(self._create_icon(SYNC_SVG, 22))
        self.sync_btn.setIconSize(QSize(22, 22))
        self.sync_btn.setToolTip(getattr(self.t, 'sync_timeline_tooltip', "タイムライン同期の微調整"))
        self.sync_btn.clicked.connect(self.toggle_sync_panel)
        
        self.event_toggle_widget = EventToggleWidget()
        self.event_toggle_widget.filterChanged.connect(self.timeline_overlay.set_filters)
        
        right_layout.addWidget(self.edit_mode_btn, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        right_layout.addWidget(self.sync_btn, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        right_layout.addWidget(self.fav_btn, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        right_layout.addWidget(self.delete_btn, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        right_layout.addStretch()
        right_layout.addWidget(self.event_toggle_widget, alignment=Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter)
        
        top_layout.addWidget(left_container)
        top_layout.addWidget(self.player_container, stretch=1)
        top_layout.addWidget(right_container)
        
        page_layout.addLayout(top_layout, stretch=1)
        page_layout.addWidget(self.controls_widget)
        
        self.playback_controls = PlaybackControlsWidget()
        self.playback_controls.togglePlayRequested.connect(self.toggle_play)
        
        page_layout.addWidget(self.playback_controls)
        page_layout.addWidget(self.edit_panel)
        page_layout.addWidget(self.sync_panel)
        
        self.speed_osd_label = QLabel("1.0x", self.player_container)
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
        self.content_widget.installEventFilter(self)
        self.video_widget.installEventFilter(self)
        self.player_container.installEventFilter(self)
        self.setMouseTracking(True)

        self.top_switcher = VideoTopSwitcher(self.config, self)
        self.top_switcher.recordSelected.connect(self._on_switch_recording)
        self.top_switcher.offsetChanged.connect(self._on_switcher_offset_changed)
        self.top_switcher.deactivate()

    def sizeHint(self) -> QSize:
        if hasattr(self, 'content_widget'):
            return self.content_widget.sizeHint()
        return super().sizeHint()

    def minimumSizeHint(self) -> QSize:
        if hasattr(self, 'content_widget'):
            return self.content_widget.minimumSizeHint()
        return super().minimumSizeHint()

    def _compute_slide_y(self, offset: int) -> int:
        if offset <= 0 or not hasattr(self, 'top_switcher') or self.top_switcher.BAR_HEIGHT <= 0:
            return 0
        bottom_area_h = self.top_switcher.BAR_HEIGHT
        if hasattr(self, 'controls_widget') and self.controls_widget.y() > 0:
            bottom_area_h = max(bottom_area_h, self.content_widget.height() - self.controls_widget.y())
        progress = min(1.0, max(0.0, offset / float(self.top_switcher.BAR_HEIGHT)))
        return int(round(progress * bottom_area_h))

    def _update_content_slide_position(self) -> None:
        if not self.isVisible() or not hasattr(self, 'content_widget') or not hasattr(self, 'top_switcher'):
            return
        slide_y = self._compute_slide_y(self.top_switcher.offset)
        if self.content_widget.y() != slide_y:
            self.content_widget.move(0, slide_y)

    def _sync_content_geometry(self) -> None:
        if not hasattr(self, 'content_widget'):
            return
        slide_y = self._compute_slide_y(self.top_switcher.offset) if hasattr(self, 'top_switcher') else 0
        self.content_widget.setGeometry(0, slide_y, self.width(), self.height())
        if self.content_widget.layout() is not None:
            self.content_widget.layout().activate()

    def update_safe_area(self) -> None:
        # 下部コントロールの不要な余白を排除し均等マージンを維持
        self.page_layout.setContentsMargins(20, 20, 20, 20)
        self._sync_content_geometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_safe_area()
        self.top_switcher.update_geometry(self.width())
        self.top_switcher.raise_()

    def _on_switch_recording(self, json_filename: str):
        self.load_recording(json_filename)

    def _refresh_switcher_safe(self):
        if not self.isVisible():
            return
        if hasattr(self, 'top_switcher') and self.top_switcher:
            self.top_switcher.refresh_records(self.current_json_filename)
            self.top_switcher.raise_()

    def _on_switcher_offset_changed(self, offset: int):
        if not self.isVisible() and offset > 0:
            return
        slide_y = self._compute_slide_y(offset)
        if self.content_widget.y() != slide_y:
            self.content_widget.move(0, slide_y)

    def _create_icon(self, svg_bytes, size=24):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer = QSvgRenderer(QByteArray(svg_bytes))
        renderer.render(painter)
        painter.end()
        return QIcon(pixmap)

    def eventFilter(self, obj, event):
        if obj == getattr(self, 'content_widget', None) and event.type() == QEvent.Type.LayoutRequest:
            if self.isVisible() and hasattr(self, 'top_switcher') and self.top_switcher.offset > 0:
                self._layout_sync_timer.start()
        if hasattr(self, 'nav_controller') and self.nav_controller.handle_event_filter(obj, event):
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
            self.cleanup_media()
            delete_single_record_files(self.config.SAVE_DIR, json_filename)
            self.current_json_filename = None
            self.current_match_data = None
            self.backRequested.emit()

    def request_back(self, *args):
        self.cleanup_media()
        self.backRequested.emit()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.cleanup_media()

    def current_load_sequence(self) -> int:
        return self._load_sequence

    def _schedule_switcher_refresh(self) -> None:
        self._switcher_refresh_timer.start()

    def _on_sync_thread_finished(self, thread: TimelineAutoSyncThread) -> None:
        self._active_sync_threads.discard(thread)
        if self._auto_sync_thread is thread:
            self._auto_sync_thread = None
        thread.deleteLater()

    def _stop_auto_sync(self) -> None:
        # UIスレッドをwait()でブロックせず、killとfinishedシグナル経由で安全に破棄する
        for thread in list(getattr(self, '_active_sync_threads', ())):
            thread.cancel()
        self._auto_sync_thread = None
        if hasattr(self, 'sync_panel'):
            self.sync_panel.set_auto_sync_busy(False)

    def cleanup_media(self):
        self._load_sequence += 1
        self._pending_video_path = None
        self._current_video_path = None
        self._stop_auto_sync()
        if hasattr(self, '_switcher_refresh_timer'):
            self._switcher_refresh_timer.stop()
        if hasattr(self, '_layout_sync_timer'):
            self._layout_sync_timer.stop()
        if hasattr(self, 'nav_controller') and self.nav_controller:
            self.nav_controller.speed_osd_timer.stop()
        if hasattr(self, 'top_switcher') and self.top_switcher:
            self.top_switcher.deactivate()
        if hasattr(self, 'player_core') and self.player_core:
            self.player_core.unload()
        if hasattr(self, 'content_widget'):
            self.content_widget.move(0, 0)

    def restore_media(self):
        if getattr(self, 'current_json_filename', None) and self.player_core.media_player.source().isEmpty():
            self.load_recording(self.current_json_filename)

    def prepare_recording(self, json_filename: str) -> None:
        self._load_sequence += 1
        self._pending_video_path = None
        self._current_video_path = None
        self._stop_auto_sync()
        if hasattr(self, 'sync_panel'):
            self.sync_panel.setVisible(False)
            self.sync_btn.setStyleSheet("""
                QPushButton { border-radius: 20px; background-color: #333333; }
                QPushButton:hover { background-color: #444444; }
            """)
        self.player_core.stop()
        self.chat_volume_widget.setVisible(False)
        self.current_json_filename = json_filename
        json_path = os.path.join(self.config.SAVE_DIR, json_filename)
        self.nav_controller.reset_playback_rate()
        self.playback_controls.set_playback_state(False)
        self.time_label.setText("00:00 / 00:00")
        self.timeline_overlay.set_position(0)
        self.timeline_overlay.set_duration(0)
        self.timeline_overlay.set_data([], [])

        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                self.current_match_data = json.load(f)
            self._update_favorite_icon(bool(self.current_match_data.get("is_favorite", False)))
            self.clip_controller.set_current_recording(json_filename, self.current_match_data)
            video_path = find_video_for_json(self.config.SAVE_DIR, json_filename, self.current_match_data)
            self._pending_video_path = video_path if (video_path and os.path.exists(video_path)) else None
        except Exception as e:
            self._pending_video_path = None
            print(f"[PlayerVideoPage] Error loading recording data: {e}")

    def start_pending_playback(self, expected_seq: Optional[int] = None) -> None:
        if expected_seq is not None and expected_seq != self._load_sequence:
            return
        if not self.isVisible():
            return
        self.player_container._update_video_geometry()
        video_path = self._pending_video_path
        self._pending_video_path = None
        self._current_video_path = video_path

        if video_path and os.path.exists(video_path):
            self.player_core.load_source(video_path)
        else:
            self.player_core.load_source(None)
            print(f"[PlayerVideoPage] Video file not found for {self.current_json_filename}.")
            self._update_timeline_data(0)

        self._schedule_switcher_refresh()

    def load_recording(self, json_filename):
        self.prepare_recording(json_filename)
        self.start_pending_playback(self._load_sequence)

    def _update_timeline_data(self, duration_ms):
        if not self.current_match_data:
            return
            
        match_info = self.current_match_data.get("match_info", self.current_match_data)
        riot_id = getattr(self.config, "RIOT_ID", "")
        tag_line = getattr(self.config, "TAG_LINE", "")
        known_accounts = list(getattr(self.config, "KNOWN_ACCOUNTS", []) or [])
        
        rounds, events = build_timeline_data(
            match_info, duration_ms, riot_id, tag_line, known_accounts=known_accounts
        )
        
        self.current_rounds = rounds
        self.nav_controller.set_current_rounds(rounds)
        self.timeline_overlay.set_data(rounds, events)
        self._refresh_sync_panel(duration_ms)

    def _refresh_sync_panel(self, duration_ms: int) -> None:
        if not hasattr(self, 'sync_panel') or not self.current_match_data:
            return
        match_info = self.current_match_data.get("match_info", self.current_match_data)
        cur_offset = int(match_info.get("timeline_sync_offset_ms") or 0)
        cur_source = str(match_info.get("timeline_sync_source") or "")
        self.sync_panel.update_offset_display(cur_offset, cur_source)

        riot_id = getattr(self.config, "RIOT_ID", "")
        tag_line = getattr(self.config, "TAG_LINE", "")
        known_accounts = list(getattr(self.config, "KNOWN_ACCOUNTS", []) or [])
        base_events = extract_player_sync_events(
            match_info, duration_ms, riot_id, tag_line, known_accounts=known_accounts
        )

        combo_events = []
        type_icon = {"kill": "💥", "death": "💀", "assist": "🤝"}
        for idx, ev in enumerate(base_events, start=1):
            ev_type = ev.get("type", "")
            t_actual = int(ev.get("base_time_ms", 0)) + cur_offset
            t_disp = f"-{self.format_time(-t_actual)}" if t_actual < 0 else self.format_time(t_actual)
            combo_events.append({
                "base_time_ms": int(ev.get("base_time_ms", 0)),
                "label": f"#{idx} {t_disp} {type_icon.get(ev_type, '')} {ev_type.capitalize()}",
            })
        self.sync_panel.set_events(combo_events, self.format_time)

    def _save_timeline_sync(self, offset_ms: int, source: str) -> None:
        if not self.current_match_data or not self.current_json_filename:
            return
        match_info = self.current_match_data.get("match_info", self.current_match_data)
        match_info["timeline_sync_offset_ms"] = int(offset_ms)
        match_info["timeline_sync_source"] = source
        json_path = os.path.join(self.config.SAVE_DIR, self.current_json_filename)
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(self.current_match_data, f, ensure_ascii=False, indent=4)
        except OSError:
            pass
        self._update_timeline_data(self.player_core.duration())

    def toggle_sync_panel(self) -> None:
        if not hasattr(self, 'sync_panel'):
            return
        show = not self.sync_panel.isVisible()
        if show and self.edit_panel.isVisible():
            self.clip_controller.toggle_edit_mode()
        self.sync_panel.setVisible(show)
        bg = "#0078D4" if show else "#333333"
        hover = "#1084E0" if show else "#444444"
        self.sync_btn.setStyleSheet(
            f"QPushButton {{ border-radius: 20px; background-color: {bg}; }}"
            f"QPushButton:hover {{ background-color: {hover}; }}"
        )

    def _on_sync_offset_delta(self, delta_ms: int) -> None:
        if not self.current_match_data:
            return
        match_info = self.current_match_data.get("match_info", self.current_match_data)
        cur = int(match_info.get("timeline_sync_offset_ms") or 0)
        self._save_timeline_sync(cur + delta_ms, "manual")

    def _on_sync_align_event(self, base_time_ms: int) -> None:
        if not self.current_match_data:
            return
        cur_pos_ms = int(self.player_core.position())
        new_offset_ms = cur_pos_ms - int(base_time_ms)
        self._save_timeline_sync(new_offset_ms, "manual")

    def _on_sync_reset(self) -> None:
        if not self.current_match_data or not self.current_json_filename:
            return
        match_info = self.current_match_data.get("match_info", self.current_match_data)
        match_info.pop("timeline_sync_offset_ms", None)
        match_info.pop("timeline_sync_source", None)
        json_path = os.path.join(self.config.SAVE_DIR, self.current_json_filename)
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(self.current_match_data, f, ensure_ascii=False, indent=4)
        except OSError:
            pass
        self._update_timeline_data(self.player_core.duration())

    def _has_valid_local_round_events(self, match_info: dict, duration_ms: int) -> bool:
        raw_events = match_info.get("local_round_events") or []
        if not raw_events:
            return False
        rec_start = match_info.get("local_recording_start_time_ms")
        if not rec_start:
            rec_start = int(float(match_info.get("local_match_start_time") or 0) * 1000)
        max_span = max(duration_ms, 3600000)
        for ev in raw_events:
            if ev.get("phase") != "InProgress":
                continue
            t = ev.get("time_ms", 0)
            if t < 1000000000000:
                t += rec_start
            if (rec_start - 300000) <= t <= (rec_start + max_span + 300000):
                return True
        return False

    def _maybe_start_auto_sync(self, duration_ms: int) -> None:
        if not self.current_match_data or not self._current_video_path or duration_ms <= 0:
            return
        if self._load_sequence in self._auto_sync_attempted_seqs:
            return
        match_info = self.current_match_data.get("match_info", self.current_match_data)
        if match_info.get("timeline_sync_source") in ("manual", "auto"):
            return
        if self._has_valid_local_round_events(match_info, duration_ms):
            return
        if not (match_info.get("kills") or []):
            return

        self._auto_sync_attempted_seqs.add(self._load_sequence)
        self._start_auto_sync_thread(duration_ms, wide_search=False, notify_on_fail=False)

    def _on_manual_auto_sync_requested(self) -> None:
        duration_ms = self.player_core.duration()
        if not self.current_match_data or not self._current_video_path or duration_ms <= 0:
            return
        self._start_auto_sync_thread(duration_ms, wide_search=True, notify_on_fail=True)

    def _start_auto_sync_thread(self, duration_ms: int, wide_search: bool, notify_on_fail: bool) -> None:
        self._stop_auto_sync()
        match_info = self.current_match_data.get("match_info", self.current_match_data)
        riot_id = getattr(self.config, "RIOT_ID", "")
        tag_line = getattr(self.config, "TAG_LINE", "")
        known_accounts = list(getattr(self.config, "KNOWN_ACCOUNTS", []) or [])

        self.sync_panel.set_auto_sync_busy(True)
        thread = TimelineAutoSyncThread(
            load_sequence=self._load_sequence,
            video_path=self._current_video_path,
            match_info=dict(match_info),
            duration_ms=duration_ms,
            riot_id=riot_id,
            tag_line=tag_line,
            known_accounts=known_accounts,
            wide_search=wide_search,
            parent=None,
        )
        thread.syncCompleted.connect(
            lambda seq, res, nf=notify_on_fail, t=thread: self._on_auto_sync_completed(seq, res, nf, t)
        )
        thread.finished.connect(lambda t=thread: self._on_sync_thread_finished(t))
        self._active_sync_threads.add(thread)
        self._auto_sync_thread = thread
        thread.start()

    def _on_auto_sync_completed(
        self,
        seq: int,
        result: AutoSyncResult,
        notify_on_fail: bool,
        thread: Optional[TimelineAutoSyncThread] = None,
    ) -> None:
        if seq != self._load_sequence:
            return
        if thread is not None and thread is not self._auto_sync_thread:
            return
        if hasattr(self, 'sync_panel'):
            self.sync_panel.set_auto_sync_busy(False)
        if result.applied:
            self._save_timeline_sync(result.sync_offset_ms, "auto")
            sec = result.sync_offset_ms / 1000.0
            sign = "+" if sec >= 0 else ""
            msg = getattr(self.t, "sync_auto_applied", "✅ 映像とログを自動同期しました ({offset})").format(
                offset=f"{sign}{sec:.1f}s"
            )
            self.notification.show_message(msg, duration=4000)
        elif notify_on_fail:
            msg = getattr(
                self.t,
                "sync_auto_not_confident",
                "⚠️ 自動同期の確信度が不足しています。手動ボタンまたはイベント基準合わせをご利用ください。",
            )
            self.notification.show_message(msg, duration=5000)

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

    def _on_audio_tracks_changed(self, track_count: int):
        self.chat_volume_widget.setVisible(track_count >= 3)

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
            self._maybe_start_auto_sync(duration)

    def showEvent(self, event):
        super().showEvent(event)
        self.update_safe_area()
        self.player_container._update_video_geometry()
        self.top_switcher.activate()
        self.top_switcher.update_geometry(self.width())
        self.top_switcher.show()
        self.top_switcher.raise_()
        # 画面遷移の初回ペイントを妨げないよう非同期タイマー経由で一覧更新を予約
        self._schedule_switcher_refresh()
        sys_pct = int(round(float(getattr(self.config, 'PLAYER_SYS_VOLUME', '1.0')) * 100))
        if int(round(self.player_core.current_sys_volume * 100)) != sys_pct:
            self.player_core.set_sys_volume(sys_pct)
            self.volume_widget.set_volume(sys_pct)
            
        mic_pct = int(round(float(getattr(self.config, 'PLAYER_MIC_VOLUME', '1.0')) * 100))
        if int(round(self.player_core.current_mic_volume * 100)) != mic_pct:
            self.player_core.set_mic_volume(mic_pct)
            self.mic_volume_widget.set_volume(mic_pct)

        chat_pct = int(round(float(getattr(self.config, 'PLAYER_CHAT_VOLUME', '1.0')) * 100))
        if int(round(self.player_core.current_chat_volume * 100)) != chat_pct:
            self.player_core.set_chat_volume(chat_pct)
            self.chat_volume_widget.set_volume(chat_pct)
            
        self.nav_controller.apply_playback_rate()
