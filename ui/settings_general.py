"""Module: @role: 一般設定画面のUI構築および言語・保存先・自動起動・ログ・更新確認の操作ハンドリングを担当する。"""
import os
from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QGuiApplication
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from core.autostart import set_autostart
from ui.settings_utils import change_save_directory


def generate_diagnostic_report(config) -> str:
    """管理者共有用に、近況エラー・VALORANTログ状態・環境設定を端的にまとめた診断レポートを生成する"""
    import datetime
    import platform
    import time
    from core.version import APP_VERSION

    lines = []
    lines.append("=== VALORANT RECORDER DIAGNOSTIC REPORT ===")
    lines.append(f"Timestamp: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    # 1. システム & 録画設定情報
    lines.append("[1. SYSTEM & CONFIG]")
    lines.append(f"- App Version: {APP_VERSION}")
    try:
        win_ver = platform.win32_ver()
        os_info = f"Windows {win_ver[0]} ({win_ver[1]} {win_ver[2]})"
    except Exception:
        os_info = platform.platform()
    lines.append(f"- OS: {os_info}")
    lines.append(f"- Python: {platform.python_version()}")

    save_dir = getattr(config, "SAVE_DIR", "")
    dir_exists = os.path.exists(save_dir) if save_dir else False
    dir_writable = os.access(save_dir, os.W_OK) if dir_exists else False
    lines.append(f"- Save Dir: {save_dir} (Exists: {dir_exists}, Writable: {dir_writable})")
    lines.append(f"- Encoder: {getattr(config, 'RECORD_ENCODER', 'unknown')}")
    lines.append(f"- Video Format: {getattr(config, 'RECORD_VIDEO_FORMAT', 'unknown')}")
    lines.append(f"- Resolution / FPS: {getattr(config, 'RECORD_RESOLUTION', 'unknown')} / {getattr(config, 'RECORD_FPS', 'unknown')}fps")
    lines.append(f"- Lightweight Mode: {getattr(config, 'LIGHTWEIGHT_MODE', False)}")
    lines.append(f"- Auto Perf Control: {getattr(config, 'AUTO_PERFORMANCE_CONTROL', True)}")
    lines.append(f"- Audio System: {getattr(config, 'RECORD_AUDIO_SYSTEM', 'default')} (Gain: {getattr(config, 'RECORD_AUDIO_SYSTEM_GAIN', '1.0')})")
    lines.append(f"- Audio Mic: {getattr(config, 'RECORD_AUDIO_MIC', 'None') or 'None'} (Preprocess: {getattr(config, 'RECORD_AUDIO_MIC_PREPROCESS', 'None')})")
    lines.append("")

    # 2. VALORANTログ (ShooterGame.log) の状態
    lines.append("[2. VALORANT LOG STATUS]")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        user_profile = os.environ.get("USERPROFILE")
        if user_profile:
            local_app_data = os.path.join(user_profile, "AppData", "Local")

    valo_log_path = os.path.join(local_app_data, "VALORANT", "Saved", "Logs", "ShooterGame.log") if local_app_data else ""
    lines.append(f"- Log Path: {valo_log_path}")

    if not valo_log_path or not os.path.exists(valo_log_path):
        lines.append("- Status: NOT FOUND (VALORANT might not be running or path is customized)")
    else:
        try:
            mtime = os.path.getmtime(valo_log_path)
            diff_min = int((time.time() - mtime) / 60)
            mtime_str = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
            size_kb = os.path.getsize(valo_log_path) // 1024
            lines.append(f"- Status: FOUND (Size: {size_kb} KB, Last Modified: {mtime_str}, {diff_min} min ago)")

            # 末尾 64KB からゲーム状態遷移や最新行を抽出（大容量ログでも低負荷）
            file_size = os.path.getsize(valo_log_path)
            with open(valo_log_path, "r", encoding="utf-8", errors="replace") as vf:
                vf.seek(max(0, file_size - 64 * 1024))
                tail_content = vf.read()
                raw_lines = [ln.strip() for ln in tail_content.splitlines() if ln.strip()]

            relevant_keywords = ("State:", "Transition", "Match", "GameFlow", "Phase", "Round", "Session")
            matched_lines = [l for l in raw_lines if any(k in l for k in relevant_keywords)]

            lines.append("- Recent Relevant Game State Lines:")
            if matched_lines:
                for ml in matched_lines[-12:]:
                    lines.append(f"  {ml[:140]}")
            elif raw_lines:
                for rl in raw_lines[-8:]:
                    lines.append(f"  {rl[:140]}")
            else:
                lines.append("  (Log file is empty)")
        except PermissionError:
            lines.append("- Status: ACCESS DENIED (Locked by Vanguard/Anti-Cheat)")
        except Exception as e:
            lines.append(f"- Status: ERROR READING LOG ({e})")
    lines.append("")

    # 3. アプリログ (app.log) の直近エラー & 末尾ログ
    lines.append("[3. RECENT ERRORS & APP LOG TAIL]")
    app_log_path = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ValorantRecorder", "app.log")
    lines.append(f"- App Log Path: {app_log_path}")

    if not os.path.exists(app_log_path):
        lines.append("- Status: app.log NOT FOUND")
    else:
        try:
            af_size = os.path.getsize(app_log_path)
            with open(app_log_path, "r", encoding="utf-8", errors="replace") as af:
                af.seek(max(0, af_size - 64 * 1024))
                log_tail_text = af.read()
                log_lines = [l.strip() for l in log_tail_text.splitlines() if l.strip()]

            error_keywords = ("ERROR", "CRITICAL", "Exception", "Traceback", "failed", "Failed", "[Error]")
            recent_errors = [l for l in log_lines if any(k in l for k in error_keywords)]

            lines.append("- Extracted Recent Error / Warning Lines (Up to 10):")
            if recent_errors:
                for el in recent_errors[-10:]:
                    lines.append(f"  ❌ {el[:150]}")
            else:
                lines.append("  (No recent error patterns detected)")

            lines.append("- Recent App Log Tail (Last 30 lines):")
            for tl in log_lines[-30:]:
                lines.append(f"  {tl[:150]}")
        except Exception as e:
            lines.append(f"- Status: ERROR READING app.log ({e})")

    lines.append("=== END OF REPORT ===")
    return "\n".join(lines)


class GeneralSettingsWidget(QWidget):
    def __init__(self, config, t, parent=None):
        super().__init__(parent)
        self.config = config
        self.t = t
        self.parent_tab = parent
        
        layout = QFormLayout()
        layout.setSpacing(15)
        
        self.language_input = QComboBox()
        self.language_input.addItem("English", "en")
        self.language_input.addItem("日本語", "ja")
        
        index = self.language_input.findData(self.config.LANGUAGE)
        if index >= 0:
            self.language_input.setCurrentIndex(index)
            
        self.language_input.currentIndexChanged.connect(self._on_language_changed)
        
        self.save_dir_input = QLineEdit(self.config.SAVE_DIR)
        self.save_dir_input.setReadOnly(True)
        self.save_dir_btn = QPushButton(self.t.browse)
        self.save_dir_btn.clicked.connect(self._browse_save_dir)
        
        save_dir_layout = QHBoxLayout()
        save_dir_layout.addWidget(self.save_dir_input)
        save_dir_layout.addWidget(self.save_dir_btn)

        self.clip_dir_input = QLineEdit(self.config.CLIP_SAVE_DIR)
        self.clip_dir_input.setReadOnly(True)
        self.clip_dir_btn = QPushButton(self.t.browse)
        self.clip_dir_btn.clicked.connect(self._browse_clip_dir)
        
        clip_dir_layout = QHBoxLayout()
        clip_dir_layout.addWidget(self.clip_dir_input)
        clip_dir_layout.addWidget(self.clip_dir_btn)

        self.auto_delete_spin = QSpinBox()
        self.auto_delete_spin.setRange(0, 3650)
        self.auto_delete_spin.setValue(self.config.AUTO_DELETE_DAYS)
        self.auto_delete_spin.setSpecialValueText(self.t.never)
        
        self.auto_delete_btn = QPushButton(self.t.apply)
        self.auto_delete_btn.clicked.connect(self._apply_auto_delete)
        
        auto_delete_layout = QHBoxLayout()
        auto_delete_layout.addWidget(self.auto_delete_spin)
        auto_delete_layout.addWidget(self.auto_delete_btn)
        
        self.auto_start_checkbox = QCheckBox(self.t.enable)
        self.auto_start_checkbox.setChecked(self.config.AUTO_START)
        self.auto_start_checkbox.toggled.connect(self._on_auto_start_toggled)
        
        self.check_update_btn = QPushButton(self.t.check_update)
        self.check_update_btn.clicked.connect(self._check_update)

        self.quit_app_btn = QPushButton(getattr(self.t, 'quit_app', "Exit Application (Stop Background)"))
        self.quit_app_btn.setStyleSheet("""
            QPushButton {
                background-color: #8B1E1E;
                color: #FFFFFF;
                font-weight: bold;
                border-radius: 4px;
                padding: 6px;
            }
            QPushButton:hover {
                background-color: #B22222;
            }
            QPushButton:pressed {
                background-color: #5C0000;
            }
        """)
        self.quit_app_btn.clicked.connect(self._quit_application)
        
        self.run_test_btn = QPushButton(getattr(self.t, 'run_env_test', "Run Environment Test"))
        self.run_test_btn.clicked.connect(self._run_env_test)
        
        self.open_log_btn = QPushButton(getattr(self.t, 'open_log_folder', "Open Log Folder"))
        self.open_log_btn.clicked.connect(self._open_log_folder)
        
        self.copy_log_btn = QPushButton(getattr(self.t, 'copy_log', "Copy Log to Clipboard"))
        self.copy_log_btn.clicked.connect(self._copy_log)
        
        log_layout = QHBoxLayout()
        log_layout.addWidget(self.open_log_btn)
        log_layout.addWidget(self.copy_log_btn)
        
        layout.addRow(self.t.language, self.language_input)
        layout.addRow(self.t.save_directory, save_dir_layout)
        layout.addRow(self.t.clip_save_directory, clip_dir_layout)
        layout.addRow(self.t.auto_delete_after_days, auto_delete_layout)
        layout.addRow(self.t.auto_start, self.auto_start_checkbox)
        layout.addRow(getattr(self.t, 'app_logs', "App Logs"), log_layout)
        layout.addRow("", self.run_test_btn)
        layout.addRow("", self.check_update_btn)
        layout.addRow("", self.quit_app_btn)
        
        self.setLayout(layout)

    def _run_env_test(self):
        self.run_test_btn.setEnabled(False)
        self.run_test_btn.setText(getattr(self.t, 'env_test_running', "Running tests..."))
        
        from core.env_tester import EnvTesterThread
        self.env_tester = EnvTesterThread(self.config)
        self.env_tester.finished_signal.connect(self._on_env_test_finished)
        self.env_tester.start()

    def _on_env_test_finished(self, successes, errors):
        self.run_test_btn.setEnabled(True)
        self.run_test_btn.setText(getattr(self.t, 'run_env_test', "Run Environment Test"))
        if self.parent_tab and hasattr(self.parent_tab, 'record_widget') and hasattr(self.parent_tab.record_widget, 'refresh_from_config'):
            self.parent_tab.record_widget.refresh_from_config()
        
        if not errors:
            title = getattr(self.t, 'env_test_success_title', "Test Passed")
            msg_template = getattr(self.t, 'env_test_success_msg', "All environment tests passed successfully:\n\n{successes}")
            success_text = "\n".join([f"✅ {s}" for s in successes])
            QMessageBox.information(self, title, msg_template.format(successes=success_text))
        else:
            title = getattr(self.t, 'env_test_failed_title', "Environment Test Failed")
            msg_template = getattr(self.t, 'env_test_mixed_msg', "Test completed with some errors.\n\n[Passed]\n{successes}\n\n[Failed]\n{errors}")
            
            success_text = "\n".join([f"✅ {s}" for s in successes]) if successes else "None"
            error_summary = "\n".join([f"❌ {e[0]}" for e in errors])
            detailed_text = "\n\n".join([f"[{e[0]}]\n{e[1]}" for e in errors])
            
            msg_box = QMessageBox(self)
            msg_box.setIcon(QMessageBox.Icon.Warning)
            msg_box.setWindowTitle(title)
            msg_box.setText(msg_template.format(successes=success_text, errors=error_summary))
            msg_box.setDetailedText(detailed_text)
            
            copy_button = msg_box.addButton(getattr(self.t, 'copy_log', "Copy Log"), QMessageBox.ButtonRole.ActionRole)
            msg_box.addButton(QMessageBox.StandardButton.Close)
            
            msg_box.exec()
            
            if msg_box.clickedButton() == copy_button:
                from PyQt6.QtGui import QGuiApplication
                QGuiApplication.clipboard().setText(detailed_text)
                QMessageBox.information(self, "Copied", getattr(self.t, 'copied_to_clipboard', "Copied!"))

    def _open_log_folder(self):
        log_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'ValorantRecorder')
        if os.path.exists(log_dir):
            QDesktopServices.openUrl(QUrl.fromLocalFile(log_dir))
        else:
            QMessageBox.warning(self, "Not Found", getattr(self.t, 'log_not_found', "Log directory not found."))

    def _copy_log(self):
        try:
            report = generate_diagnostic_report(self.config)
            clipboard = QGuiApplication.clipboard()
            clipboard.setText(report)

            # ポップアップを出さず、ボタンのテキストを一時的に変更する
            original_text = self.copy_log_btn.text()
            self.copy_log_btn.setText(getattr(self.t, 'copied_to_clipboard', "Copied!"))
            self.copy_log_btn.setEnabled(False)

            from PyQt6.QtCore import QTimer
            QTimer.singleShot(2000, lambda: self._reset_copy_btn(original_text))
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to generate diagnostic report: {e}")

    def _reset_copy_btn(self, original_text):
        self.copy_log_btn.setText(original_text)
        self.copy_log_btn.setEnabled(True)

    def _quit_application(self):
        title = getattr(self.t, 'confirm_quit_title', "Confirm Exit")
        msg = getattr(self.t, 'confirm_quit_msg', "Are you sure you want to stop background monitoring and completely exit the application?")
        reply = QMessageBox.question(
            self,
            title,
            msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            main_window = self.window()
            if hasattr(main_window, 'quit_app'):
                main_window.quit_app()
            else:
                from PyQt6.QtWidgets import QApplication
                app = QApplication.instance()
                found = False
                if app:
                    for widget in app.topLevelWidgets():
                        if hasattr(widget, 'quit_app'):
                            widget.quit_app()
                            found = True
                            break
                    if not found:
                        app.quit()

    def _check_update(self):
        from core.updater import UpdateCheckerThread
        self.check_update_btn.setEnabled(False)
        self.check_update_btn.setText(self.t.checking_update)
        self._update_found = False
        
        self.update_thread = UpdateCheckerThread(self.config.UPDATE_API_URL)
        self.update_thread.update_available.connect(self._on_update_available)
        self.update_thread.error_occurred.connect(self._on_update_error)
        self.update_thread.finished.connect(self._on_update_finished)
        self.update_thread.start()

    def _on_update_available(self, latest_version, download_url):
        self._update_found = True
        main_window = self.window()
        if hasattr(main_window, 'show_update_dialog'):
            main_window.show_update_dialog(latest_version, download_url)

    def _on_update_error(self, error_msg):
        self._update_found = True
        QMessageBox.warning(self, "Update Error", error_msg)

    def _on_update_finished(self):
        self.check_update_btn.setEnabled(True)
        self.check_update_btn.setText(self.t.check_update)
        if not self._update_found:
            QMessageBox.information(self, self.t.up_to_date, self.t.up_to_date_msg)

    def _on_auto_start_toggled(self, checked):
        self.config.AUTO_START = checked
        self.config.save()
        set_autostart(checked)

    def _on_language_changed(self, index):
        lang = self.language_input.itemData(index)
        if lang == self.config.LANGUAGE:
            return
        self.config.LANGUAGE = lang
        self.config.save()
        
        dialog = QDialog(self)
        dialog.setWindowTitle("Language Changed")
        dialog.setModal(True)
        layout = QVBoxLayout(dialog)
        msg = "Please restart the application to apply the language change.\n言語の変更を適用するにはアプリケーションを再起動してください。"
        layout.addWidget(QLabel(msg))
        btn = QPushButton("OK")
        btn.clicked.connect(dialog.accept)
        layout.addWidget(btn)
        dialog.exec()

    def _browse_save_dir(self):
        if self.parent_tab and hasattr(self.parent_tab, 'record_widget'):
            self.parent_tab.record_widget.stop_monitors()
            
        new_dir = change_save_directory(self, self.config.SAVE_DIR, self.t, suffix="/valorant_records")
        
        if new_dir:
            self.save_dir_input.setText(new_dir)
            self.config.SAVE_DIR = new_dir
            self.config.save()
            
        if self.parent_tab and hasattr(self.parent_tab, '_on_subtab_changed'):
            self.parent_tab._on_subtab_changed(self.parent_tab.tab_widget.currentIndex())

    def _browse_clip_dir(self):
        if self.parent_tab and hasattr(self.parent_tab, 'record_widget'):
            self.parent_tab.record_widget.stop_monitors()
            
        new_dir = change_save_directory(self, self.config.CLIP_SAVE_DIR, self.t, suffix="/valorant_clips")
        
        if new_dir:
            self.clip_dir_input.setText(new_dir)
            self.config.CLIP_SAVE_DIR = new_dir
            self.config.save()
            
        if self.parent_tab and hasattr(self.parent_tab, '_on_subtab_changed'):
            self.parent_tab._on_subtab_changed(self.parent_tab.tab_widget.currentIndex())

    def _apply_auto_delete(self):
        new_days = self.auto_delete_spin.value()
        if new_days == self.config.AUTO_DELETE_DAYS:
            return
            
        msg = self.t.confirm_auto_delete_disable_msg if new_days == 0 else self.t.confirm_auto_delete_change_msg.format(days=new_days)
            
        dialog = QDialog(self)
        dialog.setWindowTitle(self.t.confirm_auto_delete_change)
        dialog.setModal(True)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(msg))
        
        btn_layout = QHBoxLayout()
        yes_btn = QPushButton(self.t.yes)
        no_btn = QPushButton(self.t.no)
        yes_btn.clicked.connect(dialog.accept)
        no_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(yes_btn)
        btn_layout.addWidget(no_btn)
        layout.addLayout(btn_layout)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.config.AUTO_DELETE_DAYS = new_days
            self.config.save()
        else:
            self.auto_delete_spin.setValue(self.config.AUTO_DELETE_DAYS)

    def refresh_from_config(self):
        self.save_dir_input.setText(self.config.SAVE_DIR)
        self.clip_dir_input.setText(self.config.CLIP_SAVE_DIR)

        self.auto_delete_spin.blockSignals(True)
        self.auto_delete_spin.setValue(self.config.AUTO_DELETE_DAYS)
        self.auto_delete_spin.blockSignals(False)

        self.auto_start_checkbox.blockSignals(True)
        self.auto_start_checkbox.setChecked(self.config.AUTO_START)
        self.auto_start_checkbox.blockSignals(False)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_from_config()
