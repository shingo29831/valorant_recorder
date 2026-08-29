from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from PyQt6.QtCore import pyqtSignal
from core.config import Config
from ui.settings_general import GeneralSettingsWidget
from ui.settings_audio import AudioSettingsWidget

class SettingsTab(QWidget):
    backRequested = pyqtSignal()

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        from core.i18n import get_trans
        self.t = get_trans(self.config.LANGUAGE)
        
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(40, 40, 40, 40)
        
        header_layout = QHBoxLayout()
        title = QLabel(self.t.settings_title)
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: #FF4655;")
        
        back_btn = QPushButton(self.t.back_to_recordings)
        back_btn.setFixedWidth(200)
        back_btn.clicked.connect(self.backRequested.emit)
        
        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(back_btn)
        
        main_layout.addLayout(header_layout)
        main_layout.addSpacing(20)
        
        self.general_widget = GeneralSettingsWidget(self.config, self.t, self)
        self.audio_widget = AudioSettingsWidget(self.config, self.t, self)
        
        main_layout.addWidget(self.general_widget)
        main_layout.addWidget(self.audio_widget)
        main_layout.addStretch()
        
        self.setLayout(main_layout)

    def showEvent(self, event):
        super().showEvent(event)
        self.audio_widget.start_monitors()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.audio_widget.stop_monitors()