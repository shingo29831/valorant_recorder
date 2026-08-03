from PyQt6.QtWidgets import QMainWindow, QTabWidget, QWidget, QVBoxLayout, QTextEdit
from core.config import Config
from ui.watcher_thread import WatcherThread
from ui.settings_tab import SettingsTab
from ui.player_tab import PlayerTab

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Valorant Recorder")
        self.resize(1200, 800)
        
        self.config = Config()
        
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        
        self.home_tab = QWidget()
        self.home_layout = QVBoxLayout()
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.home_layout.addWidget(self.log_text)
        self.home_tab.setLayout(self.home_layout)
        
        self.settings_tab = SettingsTab(self.config)
        self.player_tab = PlayerTab(self.config)
        
        self.tabs.addTab(self.home_tab, "Home / Logs")
        self.tabs.addTab(self.player_tab, "Recordings / Player")
        self.tabs.addTab(self.settings_tab, "Settings")
        
        self.watcher_thread = WatcherThread(self.config)
        self.watcher_thread.log_signal.connect(self.append_log)
        self.watcher_thread.start()

    def append_log(self, message: str):
        self.log_text.append(message)

    def closeEvent(self, event):
        self.watcher_thread.stop()
        self.watcher_thread.wait()
        event.accept()