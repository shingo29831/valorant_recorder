from PyQt6.QtWidgets import QMainWindow, QStackedWidget, QPushButton
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
        
        self.stacked_widget = QStackedWidget()
        self.setCentralWidget(self.stacked_widget)
        
        self.player_tab = PlayerTab(self.config)
        self.settings_tab = SettingsTab(self.config)
        
        self.stacked_widget.addWidget(self.player_tab)
        self.stacked_widget.addWidget(self.settings_tab)
        
        self.player_tab.settingsRequested.connect(lambda: self.stacked_widget.setCurrentWidget(self.settings_tab))
        self.settings_tab.backRequested.connect(lambda: self.stacked_widget.setCurrentWidget(self.player_tab))
        
        self.statusBar().showMessage("Initializing...")
        
        self.rec_button = QPushButton("🔴 Start Recording")
        self.rec_button.setCheckable(True)
        self.rec_button.clicked.connect(self.toggle_recording)
        self.statusBar().addPermanentWidget(self.rec_button)
        
        self.watcher_thread = WatcherThread(self.config)
        self.watcher_thread.log_signal.connect(self.update_status)
        self.watcher_thread.match_saved_signal.connect(self.player_tab.refresh_list)
        self.watcher_thread.recording_state_changed.connect(self.update_rec_button)
        self.watcher_thread.start()

    def toggle_recording(self, checked):
        if checked:
            self.watcher_thread.start_manual_recording()
        else:
            self.watcher_thread.stop_manual_recording()

    def update_rec_button(self, is_recording):
        self.rec_button.blockSignals(True)
        self.rec_button.setChecked(is_recording)
        self.rec_button.blockSignals(False)
        if is_recording:
            self.rec_button.setText("⏹ Stop Recording")
        else:
            self.rec_button.setText("🔴 Start Recording")

    def update_status(self, message: str):
        self.statusBar().showMessage(message)

    def closeEvent(self, event):
        self.watcher_thread.stop()
        self.watcher_thread.wait()
        event.accept()