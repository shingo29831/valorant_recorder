import os
import sys
import shutil
import ctypes
from PyQt6.QtWidgets import QMainWindow, QStackedWidget, QPushButton, QSystemTrayIcon, QMenu, QMessageBox, QProgressDialog, QApplication, QProgressBar
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtCore import QCoreApplication, Qt, pyqtSignal
import traceback
import threading
from core.config import Config
from core.i18n import get_trans
from core.env_tester import EnvTesterThread
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
    fatalErrorOccurred = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.fatalErrorOccurred.connect(self.show_fatal_error_dialog)
        self._setup_exception_handlers()
        
        self.setWindowTitle(f"ValoReco ヴァロレコ v{APP_VERSION}")
        self.setWindowIcon(QIcon(get_resource_path("assets/icon.ico")))
        self.resize(1280, 720)
        
        self._titlebar_color_applied = False
        self._pending_update = None
        
        self.config = Config()
        self.t = get_trans(self.config.LANGUAGE)
        
        self.stacked_widget = QStackedWidget()
        self.setCentralWidget(self.stacked_widget)
        
        self.player_tab = PlayerTab(self.config)
        self.settings_tab = SettingsTab(self.config)
        
        self.stacked_widget.addWidget(self.player_tab)
        self.stacked_widget.addWidget(self.settings_tab)
        
        self.player_tab.settingsRequested.connect(lambda: self.stacked_widget.setCurrentWidget(self.settings_tab))
        self.settings_tab.backRequested.connect(lambda: self.stacked_widget.setCurrentWidget(self.player_tab))
        
        self.player_tab.videoPageVisible.connect(self._on_video_page_visible)
        self.player_tab.listRefreshed.connect(self.update_storage_gauge)
        self.stacked_widget.currentChanged.connect(self._on_main_tab_changed)
        
        self.statusBar().showMessage(getattr(self.t, 'initializing', "Initializing..."))
        
        self.storage_gauge = QProgressBar()
        self.storage_gauge.setRange(0, 100)
        self.storage_gauge.setFixedSize(210, 20)
        self.storage_gauge.setTextVisible(True)
        self.statusBar().addPermanentWidget(self.storage_gauge)
        self.update_storage_gauge()
        
        self.rec_button = QPushButton(getattr(self.t, 'start_recording', "🔴 Start Recording"))
        self.rec_button.setCheckable(True)
        self.rec_button.clicked.connect(self.toggle_recording)
        self.statusBar().addPermanentWidget(self.rec_button)
        
        self.setup_tray_icon()
        
        # ゲームのフォーカスを奪わないカスタム通知オーバーレイの初期化
        self.notification_overlay = NotificationOverlay()
        
        self.watcher_thread = WatcherThread(self.config)
        self.watcher_thread.log_signal.connect(self.update_status)
        self.watcher_thread.match_saved_signal.connect(self.player_tab.refresh_list)
        self.watcher_thread.recording_state_changed.connect(self.update_rec_button)
        self.watcher_thread.recording_state_changed.connect(self.show_recording_notification)
        self.watcher_thread.start()
        
        # 起動時の自動アップデート確認と環境テスト
        self._check_for_updates()
        self._run_env_tests()

    def _run_env_tests(self):
        self.env_tester = EnvTesterThread(self.config)
        self.env_tester.finished_signal.connect(self._on_env_test_finished)
        self.env_tester.start()

    def _on_env_test_finished(self, successes, errors):
        # 起動時はエラーがあった場合のみポップアップを表示する
        if errors:
            title = getattr(self.t, 'env_test_failed_title', "Environment Test Failed")
            msg_template = getattr(self.t, 'env_test_failed_msg', "Some environment tests failed:\n\n{errors}")
            
            summary_text = "\n".join([f"- {e[0]}" for e in errors])
            detailed_text = "\n\n".join([f"[{e[0]}]\n{e[1]}" for e in errors])
            
            # ゲームの邪魔にならないよう、ウィンドウがアクティブな時だけダイアログを出す
            if self.isActiveWindow() or not self.isHidden():
                msg_box = QMessageBox(self)
                msg_box.setIcon(QMessageBox.Icon.Warning)
                msg_box.setWindowTitle(title)
                msg_box.setText(msg_template.format(errors=summary_text))
                
                # 詳細なエラーログをポップアップ内に格納
                msg_box.setDetailedText(detailed_text)
                
                # ログをコピーするボタンを追加
                copy_button = msg_box.addButton(getattr(self.t, 'copy_log', "Copy Log"), QMessageBox.ButtonRole.ActionRole)
                msg_box.addButton(QMessageBox.StandardButton.Close)
                
                msg_box.exec()
                
                if msg_box.clickedButton() == copy_button:
                    from PyQt6.QtGui import QGuiApplication
                    QGuiApplication.clipboard().setText(detailed_text)
                    
                    # コピー完了のフィードバック
                    copied_text = getattr(self.t, 'copied_to_clipboard', "Copied!")
                    QMessageBox.information(self, "Copied", copied_text)
            else:
                self.statusBar().showMessage(f"Environment Test Failed: {len(errors)} errors")

    def _check_for_updates(self):
        api_url = getattr(self.config, 'UPDATE_API_URL', None)
        if api_url:
            self.update_checker = UpdateCheckerThread(api_url)
            self.update_checker.update_available.connect(self.show_update_dialog)
            self.update_checker.error_occurred.connect(lambda err: self.statusBar().showMessage(f"Update Error: {err}"))
            self.update_checker.start()
        else:
            print("[MainWindow] UPDATE_API_URL is not set. Update checker skipped.")

    def _setup_exception_handlers(self):
        def exception_hook(exc_type, exc_value, exc_traceback):
            if issubclass(exc_type, KeyboardInterrupt):
                sys.__excepthook__(exc_type, exc_value, exc_traceback)
                return
            error_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
            print(f"[Exception] {error_msg}", file=sys.stderr)
            self.fatalErrorOccurred.emit(error_msg)

        sys.excepthook = exception_hook

        def thread_exception_hook(args):
            error_msg = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
            print(f"[Thread Exception] {error_msg}", file=sys.stderr)
            self.fatalErrorOccurred.emit(error_msg)

        threading.excepthook = thread_exception_hook

    def show_fatal_error_dialog(self, error_msg):
        msg_box = QMessageBox(self)
        msg_box.setIcon(QMessageBox.Icon.Critical)
        msg_box.setWindowTitle("Fatal Error / 致命的なエラー")
        msg_box.setText("A fatal error has occurred. The system may be in an unusable state.\nPlease copy the error message below and report it to the developer.\n\n致命的なエラーが発生しました。システムが利用できない状態になっている可能性があります。\n以下のエラーメッセージをコピーして開発者に報告してください。")
        msg_box.setDetailedText(error_msg)
        
        copy_button = msg_box.addButton("Copy Error / エラーをコピー", QMessageBox.ButtonRole.ActionRole)
        msg_box.addButton(QMessageBox.StandardButton.Close)
        
        msg_box.exec()
        
        if msg_box.clickedButton() == copy_button:
            QApplication.clipboard().setText(error_msg)
            QMessageBox.information(self, "Copied", "Error message copied to clipboard.\nエラーメッセージをクリップボードにコピーしました。")

    def show_update_dialog(self, latest_version, download_url):
        # ゲーム中などにフォーカスを奪わないよう、非アクティブ時はトースト通知すら出さず、フラグのみ立てる
        if self.isHidden() or not self.isActiveWindow():
            self._pending_update = (latest_version, download_url)
            return

        msg_box = QMessageBox(self)
        msg_box.setWindowTitle(getattr(self.t, 'update_available_title', "Update Available"))
        msg_text = getattr(self.t, 'update_available_msg', "A new version ({latest}) is available.\nCurrent version: {current}\n\nDo you want to update now?").format(latest=latest_version, current=APP_VERSION)
        msg_box.setText(msg_text)
        msg_box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg_box.button(QMessageBox.StandardButton.Yes).setText(getattr(self.t, 'update_now', "Update Now"))
        msg_box.button(QMessageBox.StandardButton.No).setText(getattr(self.t, 'update_later', "Later"))
        
        if msg_box.exec() == QMessageBox.StandardButton.Yes:
            cancel_text = getattr(self.t, 'cancel', "Cancel")
            self.progress_dialog = QProgressDialog(getattr(self.t, 'update_downloading', "Downloading update..."), cancel_text, 0, 0, self)
            self.progress_dialog.setWindowTitle(getattr(self.t, 'update_available_title', "Update"))
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
            title = getattr(self.t, 'update_failed_title', "Update Failed")
            msg = getattr(self.t, 'update_failed_msg', "Failed to apply update:\n{error}").format(error=error_message)
            QMessageBox.critical(self, title, msg)
            self.statusBar().showMessage(title)
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
        self.update_storage_gauge()

    def _resolve_existing_path(self, path: str) -> str:
        # 未作成の保存先パスでも親ディレクトリを辿ってディスク容量を取得可能にする
        target = os.path.abspath(path or ".")
        while not os.path.exists(target):
            parent = os.path.dirname(target)
            if parent == target:
                break
            target = parent
        return target if os.path.exists(target) else "."

    def update_storage_gauge(self) -> None:
        if not hasattr(self, 'storage_gauge'):
            return
        is_ja = getattr(self.config, 'LANGUAGE', 'ja') == 'ja'
        try:
            target_path = self._resolve_existing_path(self.config.SAVE_DIR)
            total, used, free = shutil.disk_usage(target_path)
            free_gb = free / (1024 ** 3)
            total_gb = total / (1024 ** 3)
            used_pct = int(round((used / total) * 100)) if total > 0 else 0
            used_pct = max(0, min(100, used_pct))

            if used_pct >= 90 or free_gb < 10.0:
                chunk_color = "#C62828"
            elif used_pct >= 80 or free_gb < 25.0:
                chunk_color = "#B45309"
            else:
                chunk_color = "#1565C0"

            label = f"空き容量: {free_gb:.1f} GB / {total_gb:.1f} GB" if is_ja else f"Free: {free_gb:.1f} GB / {total_gb:.1f} GB"
            self.storage_gauge.setValue(used_pct)
            self.storage_gauge.setFormat(label)
            self.storage_gauge.setToolTip(f"{os.path.abspath(self.config.SAVE_DIR)}\n{label} ({100 - used_pct}%)")
        except Exception:
            chunk_color = "#555555"
            label = "空き容量: -- GB" if is_ja else "Free: -- GB"
            self.storage_gauge.setValue(0)
            self.storage_gauge.setFormat(label)

        self.storage_gauge.setStyleSheet(f"""
            QProgressBar {{
                background-color: #1A242D;
                border: 1px solid #3A4550;
                border-radius: 4px;
                color: #FFFFFF;
                text-align: center;
                font-size: 11px;
                font-weight: bold;
            }}
            QProgressBar::chunk {{
                background-color: {chunk_color};
                border-radius: 3px;
            }}
        """)

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
            self.rec_button.setText(getattr(self.t, 'stop_recording', "⏹ Stop Recording"))
            # 録画開始時、WASAPIデバイス競合とGPU/CPU負荷を防ぐため設定モニターと動画再生を停止する
            if hasattr(self, 'settings_tab') and hasattr(self.settings_tab, 'record_widget'):
                self.settings_tab.record_widget.stop_monitors()
            if hasattr(self, 'player_tab') and hasattr(self.player_tab, 'video_page'):
                try:
                    self.player_tab.video_page.player_core.pause()
                except Exception as e:
                    print(f"[MainWindow] Failed to pause video on recording start: {e}")
        else:
            self.rec_button.setText(getattr(self.t, 'start_recording', "🔴 Start Recording"))
            if hasattr(self, 'settings_tab') and self.settings_tab.isVisible():
                self.settings_tab._on_subtab_changed(self.settings_tab.tab_widget.currentIndex())
        if not self.isHidden() and not self.isMinimized():
            self.update_storage_gauge()

    def show_recording_notification(self, is_recording: bool) -> None:
        if is_recording:
            self.notification_overlay.show_message(getattr(self.t, 'recording_started', "🔴 Recording started"), 3000)
        else:
            self.notification_overlay.show_message(getattr(self.t, 'recording_stopped', "⏹ Recording stopped"), 3000)

    def update_status(self, message: str):
        # バックグラウンドでの頻繁なUI更新はフルスクリーンゲームのフォーカスを奪う原因になるため、
        # ウィンドウが非表示または最小化されている場合はステータスバーの更新をスキップする。
        if self.isHidden() or self.isMinimized():
            return
        self.statusBar().showMessage(message)

    def closeEvent(self, event):
        # ウィンドウの閉じるボタンが押された時は非表示にしてトレイに格納する
        event.ignore()
        self.hide()
        # システム通知(トースト)は無効化

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
        self.update_storage_gauge()
            
        # 保留されていたアップデートがあれば、UIの描画完了を待ってからダイアログを表示する
        if getattr(self, '_pending_update', None):
            latest_version, download_url = self._pending_update
            self._pending_update = None
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(500, lambda: self.show_update_dialog(latest_version, download_url))

    def quit_app(self):
        # トレイメニューや設定画面から完全に終了する
        self.watcher_thread.stop()
        self.watcher_thread.wait()
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            app.quit()
