import os
import sys
import ctypes
from PyQt6.QtWidgets import QMainWindow, QStackedWidget, QPushButton, QSystemTrayIcon, QMenu, QMessageBox, QProgressDialog
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtCore import QCoreApplication, Qt
from core.config import Config
from ui.watcher_thread import WatcherThread
from ui.settings_tab import SettingsTab
from ui.player_tab import PlayerTab
from ui.notification_overlay import NotificationOverlay
from core.updater import UpdateCheckerThread, UpdateDownloaderThread
from core.version import APP_VERSION

def get_resource_path(relative_path):
    """PyInstallerやNuitkaの実行時にもリソースパスを正しく解決する"""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    # Nuitka対応: __file__ (ui/main_window.py) の場所からプロジェクトルートを計算
    base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"ValoReco ヴァロレコ v{APP_VERSION}")
        self.setWindowIcon(QIcon(get_resource_path("assets/icon.ico")))
        self.resize(1280, 720)
        
        self._titlebar_color_applied = False
        self._pending_update = None
        
        self.config = Config()
        
        self.stacked_widget = QStackedWidget()
        self.setCentralWidget(self.stacked_widget)
        
        self.player_tab = PlayerTab(self.config)
        self.settings_tab = SettingsTab(self.config)
        
        self.stacked_widget.addWidget(self.player_tab)
        self.stacked_widget.addWidget(self.settings_tab)
        
        self.player_tab.settingsRequested.connect(lambda: self.stacked_widget.setCurrentWidget(self.settings_tab))
        self.settings_tab.backRequested.connect(lambda: self.stacked_widget.setCurrentWidget(self.player_tab))
        
        self.player_tab.videoPageVisible.connect(self._on_video_page_visible)
        self.stacked_widget.currentChanged.connect(self._on_main_tab_changed)
        
        self.statusBar().showMessage("Initializing...")
        
        self.rec_button = QPushButton("🔴 Start Recording")
        self.rec_button.setCheckable(True)
        self.rec_button.clicked.connect(self.toggle_recording)
        self.statusBar().addPermanentWidget(self.rec_button)
        
        self.setup_tray_icon()
        
        self.watcher_thread = WatcherThread(self.config)
        self.watcher_thread.log_signal.connect(self.update_status)
        self.watcher_thread.match_saved_signal.connect(self.player_tab.refresh_list)
        self.watcher_thread.recording_state_changed.connect(self.update_rec_button)
        self.watcher_thread.recording_state_changed.connect(self.show_recording_notification)
        self.watcher_thread.start()

        # アップデート確認スレッドの開始
        api_url = getattr(self.config, 'UPDATE_API_URL', None)
        if api_url:
            self.update_checker = UpdateCheckerThread(api_url)
            self.update_checker.update_available.connect(self.show_update_dialog)
            self.update_checker.error_occurred.connect(lambda err: self.statusBar().showMessage(f"Update Error: {err}"))
            self.update_checker.start()
        else:
            print("[MainWindow] UPDATE_API_URL is not set. Update checker skipped.")

    def show_update_dialog(self, latest_version, download_url):
        # ゲーム中などにフォーカスを奪わないよう、非アクティブ時はトースト通知すら出さず、フラグのみ立てる
        if self.isHidden() or not self.isActiveWindow():
            self._pending_update = (latest_version, download_url)
            return

        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("アップデートのお知らせ")
        msg_box.setText(f"新しいバージョン ({latest_version}) が利用可能です。\n現在のバージョン: {APP_VERSION}\n\n今すぐアップデートしますか？")
        msg_box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg_box.button(QMessageBox.StandardButton.Yes).setText("今すぐアップデート")
        msg_box.button(QMessageBox.StandardButton.No).setText("後で")
        
        if msg_box.exec() == QMessageBox.StandardButton.Yes:
            self.progress_dialog = QProgressDialog("アップデートをダウンロード中...", "キャンセル", 0, 0, self)
            self.progress_dialog.setWindowTitle("アップデート")
            self.progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
            self.progress_dialog.setCancelButton(None)
            self.progress_dialog.show()

            self.update_downloader = UpdateDownloaderThread(download_url)
            self.update_downloader.finished.connect(self._on_update_download_finished)
            self.update_downloader.start()

    def _on_update_download_finished(self, success, error_message):
        if hasattr(self, 'progress_dialog'):
            self.progress_dialog.close()
            
        if not success:
            QMessageBox.critical(self, "アップデート失敗", f"アップデートの適用に失敗しました:\n{error_message}")
            self.statusBar().showMessage("アップデート失敗")
        else:
            self.quit_app()

    def _on_video_page_visible(self, is_visible):
        if self.stacked_widget.currentWidget() == self.player_tab:
            self.statusBar().setVisible(not is_visible)

    def _on_main_tab_changed(self, index):
        if self.stacked_widget.currentWidget() == self.settings_tab:
            self.statusBar().setVisible(True)
        elif self.stacked_widget.currentWidget() == self.player_tab:
            is_video = self.player_tab.stacked_widget.currentWidget() == self.player_tab.video_page
            self.statusBar().setVisible(not is_video)

    def _apply_custom_titlebar_color(self):
        """WindowsのDWM APIを使用してタイトルバーの色をアプリの背景色(#0F1923)に合わせる"""
        if sys.platform != "win32":
            return
            
        try:
            hwnd = int(self.winId())
            dwmapi = ctypes.windll.dwmapi
            
            # Windows 10/11 ダークモードの適用 (DWMWA_USE_IMMERSIVE_DARK_MODE = 20)
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            value = ctypes.c_int(1)
            dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(value), ctypes.sizeof(value))
            
            # Windows 11 タイトルバーの背景色変更 (DWMWA_CAPTION_COLOR = 35)
            # アプリの背景色 #0F1923 に合わせる。COLORREF形式は 0x00bbggrr (R=0x0F, G=0x19, B=0x23)
            DWMWA_CAPTION_COLOR = 35
            color = ctypes.c_int(0x0023190F)
            dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_CAPTION_COLOR, ctypes.byref(color), ctypes.sizeof(color))
        except Exception as e:
            print(f"Failed to set titlebar color: {e}")

    def setup_tray_icon(self):
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(QIcon(get_resource_path("assets/icon.ico")))
        self.tray_icon.setToolTip(f"ValoReco ヴァロレコ v{APP_VERSION}")
        
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
        # Windowsのトースト通知自体がフルスクリーンゲームを最小化させる原因になるため、
        # アプリがアクティブな時（ユーザーが直接操作している時）のみ通知を出す
        if self.isActiveWindow():
            if is_recording:
                self.tray_icon.showMessage("ValoReco", "🔴 録画を開始しました", QSystemTrayIcon.MessageIcon.Information, 3000)
            else:
                self.tray_icon.showMessage("ValoReco", "⏹ 録画を終了しました", QSystemTrayIcon.MessageIcon.Information, 3000)

    def update_status(self, message: str):
        # ウィンドウが非アクティブ(ゲーム中など)の時にUIを更新すると、
        # OSがウィンドウのアクティブ化とみなしゲームのフォーカスを奪うことがあるためスキップする
        if self.isActiveWindow():
            self.statusBar().showMessage(message)

    def closeEvent(self, event):
        # ウィンドウの閉じるボタンが押された時は非表示にしてトレイに格納する
        event.ignore()
        self.hide()
        self.tray_icon.showMessage("ValoReco", "バックグラウンドで実行を継続します", QSystemTrayIcon.MessageIcon.Information, 3000)

    def hideEvent(self, event):
        super().hideEvent(event)
        if hasattr(self, 'player_tab'):
            self.player_tab.on_hidden()

    def showEvent(self, event):
        super().showEvent(event)
        
        if not self._titlebar_color_applied:
            self._apply_custom_titlebar_color()
            self._titlebar_color_applied = True
            
        if hasattr(self, 'player_tab'):
            self.player_tab.on_shown()
            
        # 保留されていたアップデートがあれば、UIの描画完了を待ってからダイアログを表示する
        if getattr(self, '_pending_update', None):
            latest_version, download_url = self._pending_update
            self._pending_update = None
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(500, lambda: self.show_update_dialog(latest_version, download_url))

    def quit_app(self):
        # トレイメニューからQuitが選択された時に完全に終了する
        self.watcher_thread.stop()
        self.watcher_thread.wait()
        QCoreApplication.quit()