from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTabWidget
from PyQt6.QtCore import pyqtSignal
from core.config import Config
from ui.settings_general import GeneralSettingsWidget
from ui.settings_record import RecordSettingsWidget
from ui.settings_playback import PlaybackSettingsWidget

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
        
        self.tab_widget = QTabWidget()
        self.tab_widget.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #444444; border-radius: 5px; background: #1E1E1E; }
            QTabBar::tab { background: #2A2A2A; color: #CCCCCC; padding: 8px 20px; border-top-left-radius: 4px; border-top-right-radius: 4px; margin-right: 2px; }
            QTabBar::tab:selected { background: #1E1E1E; color: #FFFFFF; border: 1px solid #444444; border-bottom-color: #1E1E1E; font-weight: bold; }
            QTabBar::tab:hover:!selected { background: #333333; }
        """)
        
        self.general_widget = GeneralSettingsWidget(self.config, self.t, self)
        self.record_widget = RecordSettingsWidget(self.config, self.t, self)
        self.playback_widget = PlaybackSettingsWidget(self.config, self.t, self)
        
        self.tab_widget.addTab(self.general_widget, self.t.tab_general)
        self.tab_widget.addTab(self.record_widget, self.t.tab_record)
        self.tab_widget.addTab(self.playback_widget, self.t.tab_playback)
        
        main_layout.addWidget(self.tab_widget)
        
        self.setLayout(main_layout)

    def showEvent(self, event):
        super().showEvent(event)
        self.record_widget.start_monitors()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.record_widget.stop_monitors()