"""Module: @role: メインウィンドウのUI構成、タブ切り替え、録画手動制御およびライフサイクルを管理するモジュール"""

import os
import threading
from typing import Optional

from PyQt6.QtCore import QEvent, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMainWindow, QMessageBox, QPushButton, QStackedWidget, QSystemTrayIcon

from core.config import Config
from core.i18n import get_trans
from core.version import APP_VERSION
from ui.main_dialog_controller import MainDialogController
from ui.notification_overlay import NotificationOverlay
from ui.player_tab import PlayerTab
from ui.settings_tab import SettingsTab
from ui.storage_gauge_widget import StorageGaugeWidget
from ui.system_tray_manager import SystemTrayManager
from ui.watcher_thread import WatcherThread
from ui.window_utils import apply_custom_titlebar_color, get_resource_path


class MainWindow(QMainWindow):
    """VALORANT録画アプリケーションのメインウィンドウ"""

    fatalErrorOccurred = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"ValoReco ヴァロレコ v{APP_VERSION}")
        self.setWindowIcon(QIcon(get_resource_path("assets/icon.ico")))
        self.resize(1280, 720)

        self._titlebar_color_applied = False
        self._tutorial_checked = False
        self._is_quitting = False

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

        self.storage_gauge = StorageGaugeWidget()
        self.statusBar().addPermanentWidget(self.storage_gauge)
        self.update_storage_gauge()

        self.rec_button = QPushButton(getattr(self.t, 'start_recording', "🔴 Start Recording"))
        self.rec_button.setCheckable(True)
        self.rec_button.clicked.connect(self.toggle_recording)
        self.statusBar().addPermanentWidget(self.rec_button)

        self.tray_manager: Optional[SystemTrayManager] = None
        self.setup_tray_icon()

        self.notification_overlay = NotificationOverlay()

        self.watcher_thread = WatcherThread(self.config)
        self.watcher_thread.log_signal.connect(self.update_status)
        self.watcher_thread.match_saved_signal.connect(self.player_tab.refresh_list)
        self.watcher_thread.recording_state_changed.connect(self.update_rec_button)
        self.watcher_thread.recording_state_changed.connect(self.show_recording_notification)
        self.watcher_thread.start()

        self.dialog_controller = MainDialogController(
            parent_window=self,
            config=self.config,
            t=self.t,
            on_quit_app=self.quit_app,
            on_status_message=self.statusBar().showMessage,
            on_refresh_settings=self._refresh_settings_widgets,
        )
        self.dialog_controller.fatalErrorOccurred.connect(self.fatalErrorOccurred.emit)

        self.dialog_controller.check_for_updates()
        is_first_run = not getattr(self.config, 'TUTORIAL_COMPLETED', False)
        self.dialog_controller.run_env_tests(full_test=is_first_run)

    def _refresh_settings_widgets(self) -> None:
        """設定タブ内の各ウィジェットを最新設定値で再描画"""
        if hasattr(self, 'settings_tab'):
            if hasattr(self.settings_tab, 'general_widget') and hasattr(self.settings_tab.general_widget, 'refresh_from_config'):
                self.settings_tab.general_widget.refresh_from_config()
            if hasattr(self.settings_tab, 'record_widget') and hasattr(self.settings_tab.record_widget, 'refresh_from_config'):
                self.settings_tab.record_widget.refresh_from_config()

    def show_fatal_error_dialog(self, error_msg: str) -> None:
        self.dialog_controller.show_fatal_error_dialog(error_msg)

    def show_update_dialog(self, latest_version: str, download_url: str) -> None:
        self.dialog_controller.show_update_dialog(latest_version, download_url)

    def show_spec_recommend_dialog(self, force: bool = False) -> None:
        self.dialog_controller.show_spec_recommend_dialog(force=force)

    def show_tutorial_dialog(self, force: bool = False) -> None:
        self.dialog_controller.show_tutorial_dialog(force=force)

    def _on_video_page_visible(self, is_visible: bool) -> None:
        if self.stacked_widget.currentWidget() == self.player_tab:
            self.statusBar().setVisible(not is_visible)

    def _on_main_tab_changed(self, index: int) -> None:
        if self.stacked_widget.currentWidget() == self.settings_tab:
            self.statusBar().setVisible(True)
        elif self.stacked_widget.currentWidget() == self.player_tab:
            is_video = self.player_tab.stacked_widget.currentWidget() == self.player_tab.video_page
            self.statusBar().setVisible(not is_video)
        self.update_storage_gauge()

    def update_storage_gauge(self) -> None:
        if hasattr(self, 'storage_gauge'):
            self.storage_gauge.update_storage(self.config.SAVE_DIR, getattr(self.config, 'LANGUAGE', 'ja'))

    def setup_tray_icon(self) -> None:
        if not hasattr(self, 'tray_manager') or self.tray_manager is None:
            self.tray_manager = SystemTrayManager(
                window=self,
                t=self.t,
                on_show=self.show_window,
                on_spec=lambda: self.show_spec_recommend_dialog(force=True),
                on_tutorial=lambda: self.show_tutorial_dialog(force=True),
                on_quit=self.quit_app,
            )
        else:
            self.tray_manager.setup_tray_icon()
        self.tray_icon = self.tray_manager.tray_icon

    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def tray_icon_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if self.tray_manager:
            self.tray_manager.handle_activation(reason)

    def toggle_recording(self, checked: bool) -> None:
        if checked:
            self.watcher_thread.start_manual_recording()
        else:
            self.watcher_thread.stop_manual_recording()

    def update_rec_button(self, is_recording: bool) -> None:
        self.rec_button.blockSignals(True)
        self.rec_button.setChecked(is_recording)
        self.rec_button.blockSignals(False)
        if is_recording:
            self.rec_button.setText(getattr(self.t, 'stop_recording', "⏹ Stop Recording"))
            # 録画開始時、WASAPIデバイス競合と負荷防止のため設定モニターと動画再生を停止
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

    def update_status(self, message: str) -> None:
        # ゲームのフォーカスを奪わないようウィンドウ非表示・最小化時はステータスバー更新をスキップ
        if self.isHidden() or self.isMinimized():
            return
        self.statusBar().showMessage(message)

    def closeEvent(self, event) -> None:
        if getattr(self, '_is_quitting', False):
            event.accept()
            return

        msg_box = QMessageBox(self)
        msg_box.setIcon(QMessageBox.Icon.Question)
        msg_box.setWindowTitle(getattr(self.t, 'close_confirm_title', "Confirm Exit"))
        msg_box.setText(getattr(self.t, 'close_confirm_msg', "Do you want to completely exit the application, including background monitoring?\n\n* If you exit, automatic recording will not work.\n* Choose 'Keep in Background' to minimize to the tray and keep recording active."))

        quit_btn = msg_box.addButton(getattr(self.t, 'close_btn_quit', "Exit Completely"), QMessageBox.ButtonRole.DestructiveRole)
        minimize_btn = msg_box.addButton(getattr(self.t, 'close_btn_minimize', "Keep in Background"), QMessageBox.ButtonRole.AcceptRole)
        cancel_btn = msg_box.addButton(getattr(self.t, 'cancel', "Cancel"), QMessageBox.ButtonRole.RejectRole)

        msg_box.setDefaultButton(minimize_btn)
        msg_box.setEscapeButton(cancel_btn)

        msg_box.exec()
        clicked = msg_box.clickedButton()

        if clicked == quit_btn:
            event.accept()
            self.quit_app()
        elif clicked == minimize_btn:
            event.ignore()
            self.hide()
        else:
            event.ignore()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            if hasattr(self, 'player_tab') and hasattr(self.player_tab, 'video_page'):
                self.player_tab.video_page.update_safe_area()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        if hasattr(self, 'player_tab'):
            self.player_tab.on_hidden()

    def showEvent(self, event) -> None:
        super().showEvent(event)

        if not self._titlebar_color_applied:
            apply_custom_titlebar_color(int(self.winId()))
            self._titlebar_color_applied = True

        if hasattr(self, 'player_tab'):
            self.player_tab.on_shown()
        self.update_storage_gauge()

        if not self._tutorial_checked:
            self._tutorial_checked = True
            if not getattr(self.config, 'TUTORIAL_COMPLETED', False):
                QTimer.singleShot(300, self.show_tutorial_dialog)
            elif not getattr(self.config, 'INITIAL_SETUP_COMPLETED', False):
                QTimer.singleShot(300, self.show_spec_recommend_dialog)

        if hasattr(self, 'dialog_controller'):
            self.dialog_controller.check_pending_on_shown()

    def quit_app(self) -> None:
        if getattr(self, '_is_quitting', False):
            return
        self._is_quitting = True

        if hasattr(self, 'tray_manager') and self.tray_manager:
            self.tray_manager.hide()
        elif hasattr(self, 'tray_icon') and self.tray_icon:
            self.tray_icon.hide()

        if hasattr(self, 'settings_tab') and hasattr(self.settings_tab, 'record_widget'):
            try:
                self.settings_tab.record_widget.stop_monitors()
            except Exception:
                pass

        if hasattr(self, 'watcher_thread') and self.watcher_thread:
            self.watcher_thread.stop()

        self.close()

        # 終了がブロックされた場合でもプロセスを残さない安全用フォールバック
        threading.Timer(2.0, lambda: os._exit(0)).start()

        app = QApplication.instance()
        if app:
            app.quit()
