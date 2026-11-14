"""Module: @role: アップデート確認・環境テスト・スペック診断・チュートリアル等のダイアログフロー制御を担うコントローラ"""

import sys
import threading
import traceback
from typing import Callable, Optional

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QApplication, QMessageBox, QProgressDialog, QWidget

from core.config import Config
from core.env_tester import EnvTesterThread, generate_diagnostic_report
from core.updater import UpdateCheckerThread, UpdateDownloaderThread
from core.version import APP_VERSION
from ui.settings_general import open_log_folder


class MainDialogController(QObject):
    """メインウィンドウのダイアログ表示フロー（更新、環境診断、チュートリアル、例外捕獲）を統括する"""

    fatalErrorOccurred = pyqtSignal(str)

    def __init__(
        self,
        parent_window: QWidget,
        config: Config,
        t: object,
        on_quit_app: Callable[[], None],
        on_status_message: Callable[[str], None],
        on_refresh_settings: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(parent_window)
        self._window = parent_window
        self._config = config
        self._t = t
        self._on_quit_app = on_quit_app
        self._on_status_message = on_status_message
        self._on_refresh_settings = on_refresh_settings

        self._pending_update: Optional[tuple[str, str]] = None
        self._active_tutorial_dialog: Optional[QWidget] = None
        self._pending_env_test_failures: Optional[dict] = None
        self._is_initial_env_test: bool = False

        self.update_checker: Optional[UpdateCheckerThread] = None
        self.update_downloader: Optional[UpdateDownloaderThread] = None
        self.env_tester: Optional[EnvTesterThread] = None
        self.progress_dialog: Optional[QProgressDialog] = None

        self.fatalErrorOccurred.connect(self.show_fatal_error_dialog)
        self._setup_exception_handlers()

    def _setup_exception_handlers(self) -> None:
        """クラッシュ時の例外ログを捕獲しシグナルを発火するフックを登録"""
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

    def show_fatal_error_dialog(self, error_msg: str) -> None:
        """致命的エラー発生時のダイアログを表示"""
        msg_box = QMessageBox(self._window)
        msg_box.setIcon(QMessageBox.Icon.Critical)
        msg_box.setWindowTitle("Fatal Error / 致命的なエラー")
        msg_box.setText(
            "A fatal error has occurred. The system may be in an unusable state.\n"
            "Please copy the error message below and report it to the developer.\n\n"
            "致命的なエラーが発生しました。システムが利用できない状態になっている可能性があります。\n"
            "以下のエラーメッセージをコピーして開発者に報告してください。"
        )
        msg_box.setDetailedText(error_msg)

        copy_button = msg_box.addButton("Copy Error / エラーをコピー", QMessageBox.ButtonRole.ActionRole)
        msg_box.addButton(QMessageBox.StandardButton.Close)

        msg_box.exec()

        if msg_box.clickedButton() == copy_button:
            QApplication.clipboard().setText(error_msg)
            QMessageBox.information(
                self._window,
                "Copied",
                "Error message copied to clipboard.\nエラーメッセージをクリップボードにコピーしました。"
            )

    def check_for_updates(self) -> None:
        """更新チェック用スレッドを開始"""
        api_url = getattr(self._config, 'UPDATE_API_URL', None)
        if api_url:
            self.update_checker = UpdateCheckerThread(api_url)
            self.update_checker.update_available.connect(self.show_update_dialog)
            self.update_checker.error_occurred.connect(
                lambda err: self._on_status_message(f"Update Error: {err}")
            )
            self.update_checker.start()
        else:
            print("[MainDialogController] UPDATE_API_URL is not set. Update checker skipped.")

    def show_update_dialog(self, latest_version: str, download_url: str) -> None:
        # ゲームプレイを阻害しないよう非アクティブ時はダイアログを保留
        if self._window.isHidden() or not self._window.isActiveWindow():
            self._pending_update = (latest_version, download_url)
            return

        msg_box = QMessageBox(self._window)
        msg_box.setWindowTitle(getattr(self._t, 'update_available_title', "Update Available"))
        msg_text = getattr(
            self._t,
            'update_available_msg',
            "A new version ({latest}) is available.\nCurrent version: {current}\n\nDo you want to update now?"
        ).format(latest=latest_version, current=APP_VERSION)
        msg_box.setText(msg_text)
        msg_box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg_box.button(QMessageBox.StandardButton.Yes).setText(getattr(self._t, 'update_now', "Update Now"))
        msg_box.button(QMessageBox.StandardButton.No).setText(getattr(self._t, 'update_later', "Later"))

        if msg_box.exec() == QMessageBox.StandardButton.Yes:
            cancel_text = getattr(self._t, 'cancel', "Cancel")
            self.progress_dialog = QProgressDialog(
                getattr(self._t, 'update_downloading', "Downloading update..."),
                cancel_text,
                0,
                0,
                self._window
            )
            self.progress_dialog.setWindowTitle(getattr(self._t, 'update_available_title', "Update"))
            self.progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
            self.progress_dialog.setCancelButton(None)
            self.progress_dialog.show()

            self.update_downloader = UpdateDownloaderThread(download_url)
            self.update_downloader.finished.connect(self._on_update_download_finished)
            self.update_downloader.start()

    def _on_update_download_finished(self, success: bool, error_message: str) -> None:
        """更新ダウンロード完了後の後処理"""
        if self.progress_dialog:
            self.progress_dialog.close()

        if not success:
            title = getattr(self._t, 'update_failed_title', "Update Failed")
            msg = getattr(self._t, 'update_failed_msg', "Failed to apply update:\n{error}").format(error=error_message)
            QMessageBox.critical(self._window, title, msg)
            self._on_status_message(title)
        else:
            self._on_quit_app()

    def run_env_tests(self, full_test: bool = False) -> None:
        """環境診断スレッドを開始"""
        self._is_initial_env_test = full_test
        self._pending_env_test_failures = None
        self.env_tester = EnvTesterThread(self._config, dry_run_recording=full_test)
        self.env_tester.finished_signal.connect(self._on_env_test_finished)
        self.env_tester.start()

    def _on_env_test_finished(self, successes: list, errors: list) -> None:
        """環境診断完了時のハンドラ"""
        if self._on_refresh_settings:
            self._on_refresh_settings()

        if self._is_initial_env_test:
            if errors:
                step_results = getattr(self.env_tester, 'step_results', {})
                if self._active_tutorial_dialog is not None or self._window.isHidden():
                    self._pending_env_test_failures = {
                        'step_results': step_results,
                        'errors': errors
                    }
                else:
                    self.show_env_test_result_dialog(step_results, errors)
            return

        if errors:
            title = getattr(self._t, 'env_test_failed_title', "Environment Test Failed")
            msg_template = getattr(self._t, 'env_test_failed_msg', "Some environment tests failed:\n\n{errors}")

            summary_text = "\n".join([f"- {e[0]}" for e in errors])
            detailed_text = "\n\n".join([f"[{e[0]}]\n{e[1]}" for e in errors])

            if self._window.isActiveWindow() or not self._window.isHidden():
                msg_box = QMessageBox(self._window)
                msg_box.setIcon(QMessageBox.Icon.Warning)
                msg_box.setWindowTitle(title)
                msg_box.setText(msg_template.format(errors=summary_text))
                msg_box.setDetailedText(detailed_text)

                copy_button = msg_box.addButton(
                    getattr(self._t, 'copy_diagnostic_report', getattr(self._t, 'copy_log', "Copy Diagnostic Report")),
                    QMessageBox.ButtonRole.ActionRole,
                )
                open_folder_btn = msg_box.addButton(
                    getattr(self._t, 'open_log_folder', "Open Log Folder"),
                    QMessageBox.ButtonRole.ActionRole,
                )
                msg_box.addButton(QMessageBox.StandardButton.Close)

                msg_box.exec()

                if msg_box.clickedButton() == copy_button:
                    report = generate_diagnostic_report(self._config, test_errors=errors)
                    QGuiApplication.clipboard().setText(report)
                    copied_text = getattr(self._t, 'copied_to_clipboard', "Copied!")
                    QMessageBox.information(self._window, "Copied", copied_text)
                elif msg_box.clickedButton() == open_folder_btn:
                    open_log_folder(self._window, self._t)
            else:
                self._on_status_message(f"Environment Test Failed: {len(errors)} errors")

    def show_spec_recommend_dialog(self, force: bool = False) -> None:
        """推奨設定ダイアログを表示"""
        if force or not getattr(self._config, 'INITIAL_SETUP_COMPLETED', False):
            from ui.spec_recommend_dialog import SpecRecommendDialog
            dialog = SpecRecommendDialog(self._config, self._t, self._window)
            if dialog.exec():
                if self._on_refresh_settings:
                    self._on_refresh_settings()

    def show_tutorial_dialog(self, force: bool = False) -> None:
        """チュートリアルダイアログを表示"""
        if force or not getattr(self._config, 'TUTORIAL_COMPLETED', False):
            from ui.tutorial_dialog import TutorialDialog
            dialog = TutorialDialog(self._config, self._t, self._window)
            self._active_tutorial_dialog = dialog
            try:
                dialog.exec()
            finally:
                self._active_tutorial_dialog = None

            pending = self._pending_env_test_failures
            if pending:
                self._pending_env_test_failures = None
                self.show_env_test_result_dialog(
                    pending.get('step_results', {}),
                    pending.get('errors', [])
                )

    def show_env_test_result_dialog(self, step_results: dict, errors: list) -> None:
        """環境診断の詳細結果ダイアログを表示"""
        from ui.env_test_dialog import EnvTestDialog
        dialog = EnvTestDialog(
            self._config,
            self._t,
            parent=self._window,
            auto_start=False,
            initial_results=step_results,
            initial_errors=errors
        )
        dialog.exec()

    def check_pending_on_shown(self) -> None:
        """ウィンドウ表示時に保留中のアップデートや環境テスト結果があれば表示"""
        if self._pending_update:
            latest_version, download_url = self._pending_update
            self._pending_update = None
            QTimer.singleShot(500, lambda: self.show_update_dialog(latest_version, download_url))

        if self._pending_env_test_failures and self._active_tutorial_dialog is None and getattr(self._config, 'TUTORIAL_COMPLETED', False):
            pending = self._pending_env_test_failures
            self._pending_env_test_failures = None
            QTimer.singleShot(
                300,
                lambda: self.show_env_test_result_dialog(
                    pending.get('step_results', {}),
                    pending.get('errors', []),
                ),
            )
