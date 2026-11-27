"""Module: @role: 動画プレイヤー画面のライフサイクル、動画切替バー連動、および各操作コントローラの統合を担うページ"""
import json
import os
from typing import Optional
from PyQt6.QtCore import QEvent, QSize, QTimer, Qt, pyqtSignal
from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtWidgets import QMessageBox, QWidget
from core.config import Config
from core.i18n import get_trans
from ui.in_app_notification import InAppNotification
from ui.player_clip_controller import PlayerClipController
from ui.player_components import ClickableVideoWidget
from ui.player_list_actions import delete_single_record_files, toggle_favorite_status
from ui.player_navigation_controller import PlayerNavigationController
from ui.player_sync_controller import PlayerSyncController
from ui.player_utils import find_video_for_json
from ui.player_video_ui import (
    STAR_FILLED_SVG,
    STAR_OUTLINE_SVG,
    build_player_video_ui,
    create_svg_icon,
)
from ui.video_player_core import VideoPlayerCore
from ui.video_top_switcher import VideoTopSwitcher

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

        self._switcher_refresh_timer = QTimer(self)
        self._switcher_refresh_timer.setSingleShot(True)
        self._switcher_refresh_timer.setInterval(150)
        self._switcher_refresh_timer.timeout.connect(self._refresh_switcher_safe)

        self._layout_sync_timer = QTimer(self)
        self._layout_sync_timer.setSingleShot(True)
        self._layout_sync_timer.setInterval(0)
        self._layout_sync_timer.timeout.connect(self._update_content_slide_position)

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

        ui = build_player_video_ui(self, self.t, self.video_widget, self.player_core)
        self.content_widget = ui.content_widget
        self.page_layout = ui.page_layout
        self.left_layout = ui.left_layout
        self.right_layout = ui.right_layout
        self.back_btn = ui.back_btn
        self.timeline_overlay = ui.timeline_overlay
        self.controls_widget = ui.controls_widget
        self.volume_widget = ui.volume_widget
        self.mic_volume_widget = ui.mic_volume_widget
        self.chat_volume_widget = ui.chat_volume_widget
        self.time_label = ui.time_label
        self.zoom_out_btn = ui.zoom_out_btn
        self.zoom_in_btn = ui.zoom_in_btn
        self.player_container = ui.player_container
        self.edit_panel = ui.edit_panel
        self.sync_panel = ui.sync_panel
        self.edit_mode_btn = ui.edit_mode_btn
        self.fav_btn = ui.fav_btn
        self.delete_btn = ui.delete_btn
        self.sync_btn = ui.sync_btn
        self.event_toggle_widget = ui.event_toggle_widget
        self.playback_controls = ui.playback_controls
        self.speed_osd_label = ui.speed_osd_label

        self.back_btn.clicked.connect(self.request_back)
        self.timeline_overlay.seekStarted.connect(self.player_core.on_seek_started)
        self.timeline_overlay.seekRequested.connect(self.player_core.on_seek_requested)
        self.timeline_overlay.seekFinished.connect(self.player_core.on_seek_finished)
        self.volume_widget.volumeChanged.connect(self.player_core.set_sys_volume)
        self.mic_volume_widget.volumeChanged.connect(self.player_core.set_mic_volume)
        self.chat_volume_widget.volumeChanged.connect(self.player_core.set_chat_volume)
        self.zoom_out_btn.clicked.connect(self.zoom_out_timeline)
        self.zoom_in_btn.clicked.connect(self.zoom_in_timeline)
        self.fav_btn.clicked.connect(self.toggle_favorite)
        self.delete_btn.clicked.connect(self.delete_recording)
        self.sync_btn.clicked.connect(self.toggle_sync_panel)
        self.event_toggle_widget.filterChanged.connect(self.timeline_overlay.set_filters)
        self.playback_controls.togglePlayRequested.connect(self.toggle_play)

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
            on_close_sync_panel=lambda: self.sync_controller.close_sync_panel(),
            parent=self,
        )
        self.sync_controller = PlayerSyncController(
            config=self.config,
            t=self.t,
            player_core=self.player_core,
            timeline_overlay=self.timeline_overlay,
            sync_panel=self.sync_panel,
            sync_btn=self.sync_btn,
            edit_panel=self.edit_panel,
            nav_controller=self.nav_controller,
            notification=self.notification,
            format_time_fn=self.format_time,
            get_load_sequence_fn=self.current_load_sequence,
            on_close_edit_panel=self.clip_controller.close_edit_mode,
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

    @property
    def current_rounds(self) -> list:
        return self.sync_controller.current_rounds

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
        return create_svg_icon(svg_bytes, size)

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
            self.clip_controller.set_current_recording(None, None)
            self.sync_controller.set_current_recording(None, None)
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

    def cleanup_media(self):
        self._load_sequence += 1
        self._pending_video_path = None
        self._current_video_path = None
        if hasattr(self, "sync_controller") and self.sync_controller:
            self.sync_controller.stop_auto_sync()
            self.sync_controller.set_video_path(None)
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
        self.current_match_data = None
        self.clip_controller.set_current_recording(None, None)
        self.clip_controller.close_edit_mode()
        self.sync_controller.reset_for_prepare()
        self._update_favorite_icon(False)
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
            self.sync_controller.set_current_recording(json_filename, self.current_match_data)
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
        self.sync_controller.set_video_path(video_path)

        if video_path and os.path.exists(video_path):
            self.player_core.load_source(video_path)
        else:
            self.player_core.load_source(None)
            print(f"[PlayerVideoPage] Video file not found for {self.current_json_filename}.")
            self.sync_controller.update_timeline_data(0)

        self._schedule_switcher_refresh()

    def load_recording(self, json_filename):
        self.prepare_recording(json_filename)
        self.start_pending_playback(self._load_sequence)

    def toggle_sync_panel(self) -> None:
        self.sync_controller.toggle_sync_panel()

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
            self.sync_controller.update_timeline_data(duration)
            self.sync_controller.maybe_start_auto_sync(duration)

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
