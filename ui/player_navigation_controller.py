"""Module: @role: 動画再生時のシーク、ラウンドジャンプ、再生速度制御、およびOSD表示を制御するコントローラ"""
import time
from typing import Optional
from PyQt6.QtCore import QEvent, QObject, Qt, QTimer
from PyQt6.QtWidgets import QLabel
from ui.player_components import ClickableVideoWidget
from ui.video_playback_controls import PlaybackControlsWidget
from ui.video_player_core import VideoPlayerCore
from ui.volume_widgets import VolumeWidget


class PlayerNavigationController(QObject):
    def __init__(
        self,
        player_core: VideoPlayerCore,
        playback_controls: PlaybackControlsWidget,
        video_widget: ClickableVideoWidget,
        volume_widget: VolumeWidget,
        speed_osd_label: QLabel,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self.player_core = player_core
        self.playback_controls = playback_controls
        self.video_widget = video_widget
        self.volume_widget = volume_widget
        self.speed_osd_label = speed_osd_label

        self.target_playback_rate = 1.0
        self.is_speed_bypassed = False
        self.current_rounds: list = []
        self._last_seek_time = 0.0
        self._target_position = 0

        self.speed_osd_timer = QTimer(self)
        self.speed_osd_timer.setSingleShot(True)
        self.speed_osd_timer.timeout.connect(self.speed_osd_label.hide)

        self.playback_controls.prevRoundRequested.connect(self.skip_to_prev_round)
        self.playback_controls.skipBackRequested.connect(self.skip_backward)
        self.playback_controls.skipForwardRequested.connect(self.skip_forward)
        self.playback_controls.nextRoundRequested.connect(self.skip_to_next_round)
        self.playback_controls.speedDecreaseRequested.connect(self.decrease_speed)
        self.playback_controls.speedIncreaseRequested.connect(self.increase_speed)
        self.playback_controls.speedToggleRequested.connect(self.toggle_speed)

    def set_current_rounds(self, rounds: list) -> None:
        self.current_rounds = rounds

    def reset_playback_rate(self) -> None:
        self.target_playback_rate = 1.0
        self.is_speed_bypassed = False
        self.playback_controls.set_speed_label(1.0, False)
        self.player_core.set_playback_rate(1.0)

    def increase_speed(self, step: float = 0.1) -> None:
        self.set_target_playback_rate(self.target_playback_rate + step)

    def decrease_speed(self, step: float = 0.1) -> None:
        self.set_target_playback_rate(self.target_playback_rate - step)

    def set_target_playback_rate(self, rate: float) -> None:
        rate = max(0.1, min(5.0, round(rate, 1)))
        self.target_playback_rate = rate
        self.is_speed_bypassed = False
        self.apply_playback_rate()

    def toggle_speed(self) -> None:
        if self.target_playback_rate == 1.0:
            return
        self.is_speed_bypassed = not self.is_speed_bypassed
        self.apply_playback_rate()

    def show_osd(self, text: str) -> None:
        self.speed_osd_label.setText(text)
        self.speed_osd_label.adjustSize()
        lbl_rect = self.speed_osd_label.rect()
        x = self.video_widget.x() + (self.video_widget.width() - lbl_rect.width()) // 2
        y = self.video_widget.y() + self.video_widget.height() - lbl_rect.height() - 30
        self.speed_osd_label.move(x, y)
        self.speed_osd_label.raise_()
        self.speed_osd_label.show()
        self.speed_osd_timer.start(2000)

    def apply_playback_rate(self) -> None:
        actual_rate = 1.0 if self.is_speed_bypassed else self.target_playback_rate
        self.player_core.set_playback_rate(actual_rate)
        self.playback_controls.set_speed_label(self.target_playback_rate, self.is_speed_bypassed)
        self.show_osd(f"{actual_rate:.1f}x")

    def get_effective_position(self) -> int:
        now = time.time()
        if now - self._last_seek_time < 0.5:
            return self._target_position
        return self.player_core.position()

    def set_effective_position(self, pos: int) -> None:
        self._target_position = pos
        self._last_seek_time = time.time()
        self.player_core.set_position_direct(pos)

    def skip_backward(self) -> None:
        pos = max(0, self.get_effective_position() - 5000)
        self.set_effective_position(pos)

    def skip_forward(self) -> None:
        pos = min(self.player_core.duration(), self.get_effective_position() + 5000)
        self.set_effective_position(pos)

    def skip_to_prev_round(self) -> None:
        if not self.current_rounds:
            return
        current_pos = self.get_effective_position()
        target_pos = 0
        for r in reversed(self.current_rounds):
            if r.get("start", 0) < current_pos - 2000:
                target_pos = r["start"]
                break
        self.set_effective_position(target_pos)

    def skip_to_next_round(self) -> None:
        if not self.current_rounds:
            return
        current_pos = self.get_effective_position()
        target_pos = self.player_core.duration()
        for r in self.current_rounds:
            if r.get("start", 0) > current_pos + 2000:
                target_pos = r["start"]
                break
        self.set_effective_position(target_pos)

    def handle_event_filter(self, obj: QObject, event: QEvent) -> bool:
        if obj == self.video_widget:
            if event.type() == QEvent.Type.Wheel:
                delta = event.angleDelta().y()
                modifiers = event.modifiers()
                if modifiers & Qt.KeyboardModifier.ControlModifier:
                    if delta > 0:
                        self.increase_speed()
                    elif delta < 0:
                        self.decrease_speed()
                else:
                    step = 5 if delta > 0 else -5
                    new_vol = max(
                        0,
                        min(200, int(round(self.player_core.current_sys_volume * 100)) + step),
                    )
                    self.player_core.set_sys_volume(new_vol)
                    self.volume_widget.set_volume(new_vol)
                    self.show_osd(f"Vol: {new_vol}%")
                return True
            elif event.type() == QEvent.Type.Resize:
                if self.speed_osd_label.isVisible():
                    lbl_rect = self.speed_osd_label.rect()
                    x = self.video_widget.x() + (self.video_widget.width() - lbl_rect.width()) // 2
                    y = self.video_widget.y() + self.video_widget.height() - lbl_rect.height() - 30
                    self.speed_osd_label.move(x, y)
        return False
