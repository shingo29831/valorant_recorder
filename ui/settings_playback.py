"""Module: @role: 再生時のシステム音声およびマイク音声のデフォルト音量設定を担うUIモジュール"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QVBoxLayout,
    QWidget,
)


class PlaybackSettingsWidget(QWidget):
    @staticmethod
    def _parse_volume_percent(raw_val, default_pct: int = 100) -> int:
        try:
            val = float(raw_val)
            pct = int(round(val * 100)) if val <= 2.0 else int(round(val))
            return max(0, min(200, pct))
        except (ValueError, TypeError):
            return default_pct

    def __init__(self, config, t, parent=None):
        super().__init__(parent)
        self.config = config
        self.t = t

        outer_layout = QVBoxLayout()
        outer_layout.setContentsMargins(16, 12, 16, 16)
        outer_layout.setSpacing(16)

        playback_group = QGroupBox(self.t.tab_playback)
        layout = QFormLayout()
        layout.setContentsMargins(16, 18, 16, 14)
        layout.setHorizontalSpacing(20)
        layout.setVerticalSpacing(16)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.sys_vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.sys_vol_slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sys_vol_slider.setRange(0, 200)
        sys_pct = self._parse_volume_percent(getattr(self.config, "PLAYER_SYS_VOLUME", "1.0"))
        self.sys_vol_slider.setValue(sys_pct)
        self.sys_vol_slider.valueChanged.connect(self._on_sys_vol_changed)

        self.sys_vol_label = QLabel(f"{sys_pct}%")
        self.sys_vol_label.setMinimumWidth(56)
        self.sys_vol_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.sys_vol_label.setStyleSheet("font-weight: bold; color: #FFFFFF;")

        sys_vol_layout = QHBoxLayout()
        sys_vol_layout.setSpacing(10)
        sys_vol_layout.addWidget(self.sys_vol_slider, 1)
        sys_vol_layout.addWidget(self.sys_vol_label)

        self.mic_vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.mic_vol_slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mic_vol_slider.setRange(0, 200)
        mic_pct = self._parse_volume_percent(getattr(self.config, "PLAYER_MIC_VOLUME", "1.0"))
        self.mic_vol_slider.setValue(mic_pct)
        self.mic_vol_slider.valueChanged.connect(self._on_mic_vol_changed)

        self.mic_vol_label = QLabel(f"{mic_pct}%")
        self.mic_vol_label.setMinimumWidth(56)
        self.mic_vol_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.mic_vol_label.setStyleSheet("font-weight: bold; color: #FFFFFF;")

        mic_vol_layout = QHBoxLayout()
        mic_vol_layout.setSpacing(10)
        mic_vol_layout.addWidget(self.mic_vol_slider, 1)
        mic_vol_layout.addWidget(self.mic_vol_label)

        self.chat_vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.chat_vol_slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chat_vol_slider.setRange(0, 200)
        chat_pct = self._parse_volume_percent(getattr(self.config, "PLAYER_CHAT_VOLUME", "1.0"))
        self.chat_vol_slider.setValue(chat_pct)
        self.chat_vol_slider.valueChanged.connect(self._on_chat_vol_changed)

        self.chat_vol_label = QLabel(f"{chat_pct}%")
        self.chat_vol_label.setMinimumWidth(56)
        self.chat_vol_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.chat_vol_label.setStyleSheet("font-weight: bold; color: #FFFFFF;")

        chat_vol_layout = QHBoxLayout()
        chat_vol_layout.setSpacing(10)
        chat_vol_layout.addWidget(self.chat_vol_slider, 1)
        chat_vol_layout.addWidget(self.chat_vol_label)

        layout.addRow(self.t.system_volume, sys_vol_layout)
        layout.addRow(self.t.mic_volume, mic_vol_layout)
        layout.addRow(getattr(self.t, "chat_volume", "Chat Volume"), chat_vol_layout)

        playback_group.setLayout(layout)
        outer_layout.addWidget(playback_group)
        outer_layout.addStretch()
        self.setLayout(outer_layout)

    def _on_sys_vol_changed(self, value):
        self.sys_vol_label.setText(f"{value}%")
        self.config.PLAYER_SYS_VOLUME = value / 100.0
        self.config.save()

    def _on_mic_vol_changed(self, value):
        self.mic_vol_label.setText(f"{value}%")
        self.config.PLAYER_MIC_VOLUME = value / 100.0
        self.config.save()

    def _on_chat_vol_changed(self, value):
        self.chat_vol_label.setText(f"{value}%")
        self.config.PLAYER_CHAT_VOLUME = value / 100.0
        self.config.save()

    def showEvent(self, event):
        super().showEvent(event)
        sys_pct = self._parse_volume_percent(getattr(self.config, "PLAYER_SYS_VOLUME", "1.0"))
        self.sys_vol_slider.blockSignals(True)
        self.sys_vol_slider.setValue(sys_pct)
        self.sys_vol_label.setText(f"{sys_pct}%")
        self.sys_vol_slider.blockSignals(False)

        mic_pct = self._parse_volume_percent(getattr(self.config, "PLAYER_MIC_VOLUME", "1.0"))
        self.mic_vol_slider.blockSignals(True)
        self.mic_vol_slider.setValue(mic_pct)
        self.mic_vol_label.setText(f"{mic_pct}%")
        self.mic_vol_slider.blockSignals(False)

        chat_pct = self._parse_volume_percent(getattr(self.config, "PLAYER_CHAT_VOLUME", "1.0"))
        self.chat_vol_slider.blockSignals(True)
        self.chat_vol_slider.setValue(chat_pct)
        self.chat_vol_label.setText(f"{chat_pct}%")
        self.chat_vol_slider.blockSignals(False)
