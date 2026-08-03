from PyQt6.QtWidgets import QMainWindow, QTabWidget
from core.config import Config
from ui.watcher_thread import WatcherThread
from ui.settings_tab import SettingsTab
from ui.player_tab import PlayerTab

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Valorant Recorder")
        self.resize(1280, 720)
        
        self.config = Config()
        
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        
        self.player_tab = PlayerTab(self.config)
        self.settings_tab = SettingsTab(self.config)
        
        self.tabs.addTab(self.player_tab, "Recordings")
        self.tabs.addTab(self.settings_tab, "Settings")
        
        self.statusBar().showMessage("Initializing...")
        
        self.watcher_thread = WatcherThread(self.config)
        self.watcher_thread.log_signal.connect(self.update_status)
        self.watcher_thread.start()

    def update_status(self, message: str):
        self.statusBar().showMessage(message)

    def closeEvent(self, event):
        self.watcher_thread.stop()
        self.watcher_thread.wait()
        event.accept()