from PyQt6.QtWidgets import QMainWindow, QStackedWidget, QPushButton, QSystemTrayIcon, QMenu
from PyQt6.QtGui import QAction
from PyQt6.QtCore import QCoreApplication
from core.config import Config
from ui.watcher_thread import WatcherThread
from ui.settings_tab import SettingsTab
from ui.player_tab import PlayerTab
from ui.notification_overlay import NotificationOverlay

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
        
        self.setup_tray_icon()
        
        self.notification_overlay = NotificationOverlay()
        
        self.watcher_thread = WatcherThread(self.config)
        self.watcher_thread.log_signal.connect(self.update_status)
        self.watcher_thread.match_saved_signal.connect(self.player_tab.refresh_list)
        self.watcher_thread.recording_state_changed.connect(self.update_rec_button)
        self.watcher_thread.recording_state_changed.connect(self.show_recording_notification)
        self.watcher_thread.start()

    def setup_tray_icon(self):
        self.tray_icon = QSystemTrayIcon(self)
        icon = self.style().standardIcon(self.style().StandardPixmap.SP_ComputerIcon)
        self.tray_icon.setIcon(icon)
        self.tray_icon.setToolTip("Valorant Recorder")
        
        tray_menu = QMenu()
        show_action = QAction("Show", self)
        show_action.triggered.connect(self.show_window)
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.quit_app)
        
        tray_menu.addAction(show_action)
        tray_menu.addAction(quit_action)
        
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.tray_icon_activated)
        self.tray_icon.show()

    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def tray_icon_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_window()

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

    def show_recording_notification(self, is_recording):
        if is_recording:
            self.notification_overlay.show_message("🔴 録画を開始しました")
        else:
            self.notification_overlay.show_message("⏹ 録画を終了しました")

    def update_status(self, message: str):
        self.statusBar().showMessage(message)

    def closeEvent(self, event):
        # ウィンドウの閉じるボタンが押された時は非表示にしてトレイに格納する
        event.ignore()
        self.hide()
        self.tray_icon.showMessage(
            "Valorant Recorder",
            "Application is still running in the background.",
            QSystemTrayIcon.MessageIcon.Information,
            2000
        )

    def hideEvent(self, event):
        super().hideEvent(event)
        if hasattr(self, 'player_tab'):
            self.player_tab.on_hidden()

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, 'player_tab'):
            self.player_tab.on_shown()

    def quit_app(self):
        # トレイメニューからQuitが選択された時に完全に終了する
        self.watcher_thread.stop()
        self.watcher_thread.wait()
        QCoreApplication.quit()