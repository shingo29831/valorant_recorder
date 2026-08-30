from PyQt6.QtWidgets import QWidget, QFormLayout, QLabel, QSlider, QHBoxLayout
from PyQt6.QtCore import Qt

class PlaybackSettingsWidget(QWidget):
    def __init__(self, config, t, parent=None):
        super().__init__(parent)
        self.config = config
        self.t = t
        
        layout = QFormLayout()
        layout.setSpacing(15)
        
        # System Volume
        self.sys_vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.sys_vol_slider.setRange(0, 200)
        sys_vol = float(getattr(self.config, 'PLAYER_SYS_VOLUME', '1.0'))
        self.sys_vol_slider.setValue(int(sys_vol * 100))
        self.sys_vol_slider.valueChanged.connect(self._on_sys_vol_changed)
        
        self.sys_vol_label = QLabel(f"{int(sys_vol * 100)}%")
        self.sys_vol_label.setFixedWidth(40)
        
        sys_vol_layout = QHBoxLayout()
        sys_vol_layout.addWidget(self.sys_vol_slider)
        sys_vol_layout.addWidget(self.sys_vol_label)
        
        # Mic Volume
        self.mic_vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.mic_vol_slider.setRange(0, 200)
        mic_vol = float(getattr(self.config, 'PLAYER_MIC_VOLUME', '1.0'))
        self.mic_vol_slider.setValue(int(mic_vol * 100))
        self.mic_vol_slider.valueChanged.connect(self._on_mic_vol_changed)
        
        self.mic_vol_label = QLabel(f"{int(mic_vol * 100)}%")
        self.mic_vol_label.setFixedWidth(40)
        
        mic_vol_layout = QHBoxLayout()
        mic_vol_layout.addWidget(self.mic_vol_slider)
        mic_vol_layout.addWidget(self.mic_vol_label)
        
        mic_vol_layout = QHBoxLayout()
        mic_vol_layout.addWidget(self.mic_vol_slider)
        mic_vol_layout.addWidget(self.mic_vol_label)
        
        layout.addRow(getattr(self.t, 'system_volume', "System Volume"), sys_vol_layout)
        layout.addRow(getattr(self.t, 'mic_volume', "Mic Volume"), mic_vol_layout)
        
        self.setLayout(layout)

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
        # 再生画面などで変更された最新の音量を読み込んで反映
        sys_vol = float(getattr(self.config, 'PLAYER_SYS_VOLUME', '1.0'))
        self.sys_vol_slider.blockSignals(True)
        self.sys_vol_slider.setValue(int(sys_vol * 100))
        self.sys_vol_label.setText(f"{int(sys_vol * 100)}%")
        self.sys_vol_slider.blockSignals(False)
        
        mic_vol = float(getattr(self.config, 'PLAYER_MIC_VOLUME', '1.0'))
        self.mic_vol_slider.blockSignals(True)
        self.mic_vol_slider.setValue(int(mic_vol * 100))
        self.mic_vol_label.setText(f"{int(mic_vol * 100)}%")
        self.mic_vol_slider.blockSignals(False)
