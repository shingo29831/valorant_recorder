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
        sys_vol = float(getattr(self.config, "PLAYER_SYS_VOLUME", "1.0"))
        self.sys_vol_slider.setValue(int(sys_vol * 100))
        self.sys_vol_slider.valueChanged.connect(self._on_sys_vol_changed)

        self.sys_vol_label = QLabel(f"{int(sys_vol * 100)}%")
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
        mic_vol = float(getattr(self.config, "PLAYER_MIC_VOLUME", "1.0"))
        self.mic_vol_slider.setValue(int(mic_vol * 100))
        self.mic_vol_slider.valueChanged.connect(self._on_mic_vol_changed)

        self.mic_vol_label = QLabel(f"{int(mic_vol * 100)}%")
        self.mic_vol_label.setMinimumWidth(56)
        self.mic_vol_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.mic_vol_label.setStyleSheet("font-weight: bold; color: #FFFFFF;")

        mic_vol_layout = QHBoxLayout()
        mic_vol_layout.setSpacing(10)
        mic_vol_layout.addWidget(self.mic_vol_slider, 1)
        mic_vol_layout.addWidget(self.mic_vol_label)

        layout.addRow(self.t.system_volume, sys_vol_layout)
        layout.addRow(self.t.mic_volume, mic_vol_layout)

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

    def showEvent(self, event):
        super().showEvent(event)
        sys_vol = float(getattr(self.config, "PLAYER_SYS_VOLUME", "1.0"))
        self.sys_vol_slider.blockSignals(True)
        self.sys_vol_slider.setValue(int(sys_vol * 100))
        self.sys_vol_label.setText(f"{int(sys_vol * 100)}%")
        self.sys_vol_slider.blockSignals(False)

        mic_vol = float(getattr(self.config, "PLAYER_MIC_VOLUME", "1.0"))
        self.mic_vol_slider.blockSignals(True)
        self.mic_vol_slider.setValue(int(mic_vol * 100))
        self.mic_vol_label.setText(f"{int(mic_vol * 100)}%")
        self.mic_vol_slider.blockSignals(False)
