import os
from PyQt6.QtWidgets import (QWidget, QFormLayout, QComboBox, QLineEdit, 
                             QPushButton, QHBoxLayout, QVBoxLayout, QLabel, 
                             QSpinBox, QDialog, QMessageBox, QCheckBox)
from PyQt6.QtGui import QGuiApplication, QDesktopServices
from PyQt6.QtCore import QUrl
from ui.settings_utils import change_save_directory
from core.autostart import set_autostart
import logging

"""Module: @role: 一般設定画面のUI構築および言語・保存先・自動起動・ログ・更新確認の操作ハンドリングを担当する。"""
import os
import shutil
import sys
from typing import Optional
from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QGuiApplication
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
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

RUN_REGISTRY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_AUTORUN_NAME = "ValorantRecorder"


def set_autostart(enabled: bool) -> None:
    if sys.platform != "win32":
        return
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            RUN_REGISTRY_PATH,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            if enabled:
                if getattr(sys, "frozen", False) or "__compiled__" in globals():
                    cmd = f'"{sys.executable}" --tray'
                else:
                    script_path = os.path.abspath(sys.argv[0])
                    cmd = f'"{sys.executable}" "{script_path}" --tray'
                winreg.SetValueEx(key, APP_AUTORUN_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, APP_AUTORUN_NAME)
                except FileNotFoundError:
                    pass
    except OSError as e:
        print(f"[SettingsGeneral] Failed to update autostart registry: {e}", file=sys.stderr)


def change_save_directory(
    parent: QWidget,
    current_dir: str,
    t: object,
    suffix: str = "/valorant_records",
) -> Optional[str]:
    start_dir = current_dir if current_dir and os.path.exists(current_dir) else os.path.expanduser("~")
    selected_dir = QFileDialog.getExistingDirectory(
        parent,
        getattr(t, "browse", "Select Directory"),
        start_dir,
    )
    if not selected_dir:
        return None

    normalized = selected_dir.replace("\\", "/").rstrip("/")
    clean_suffix = suffix.replace("\\", "/")
    if clean_suffix and not normalized.lower().endswith(clean_suffix.lower()):
        new_dir = f"{normalized}{clean_suffix}"
    else:
        new_dir = normalized

    if current_dir and os.path.abspath(new_dir) == os.path.abspath(current_dir):
        return None

    try:
        os.makedirs(new_dir, exist_ok=True)
        if current_dir and os.path.isdir(current_dir):
            for entry in os.listdir(current_dir):
                src_path = os.path.join(current_dir, entry)
                dst_path = os.path.join(new_dir, entry)
                if not os.path.exists(dst_path):
                    shutil.move(src_path, dst_path)
    except OSError as e:
        QMessageBox.warning(parent, "Error", f"Failed to prepare directory: {e}")
        return None

    return new_dir


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

        self.riot_id_input = QLineEdit(self.config.RIOT_ID)
        self.riot_id_input.editingFinished.connect(self._save_settings)

        self.tag_line_input = QLineEdit(self.config.TAG_LINE)
        self.tag_line_input.editingFinished.connect(self._save_settings)

        self.fetch_riot_btn = QPushButton(self.t.fetch_from_valorant)
        self.fetch_riot_btn.clicked.connect(self._fetch_from_valorant)

        riot_layout = QHBoxLayout()
        riot_layout.addWidget(self.riot_id_input, stretch=2)
        riot_layout.addWidget(QLabel("#"))
        riot_layout.addWidget(self.tag_line_input, stretch=1)
        riot_layout.addWidget(self.fetch_riot_btn)

        self.region_input = QComboBox()
        self.region_input.addItems(["ap", "na", "eu", "kr", "latam", "br"])
        if self.config.REGION in ["ap", "na", "eu", "kr", "latam", "br"]:
            self.region_input.setCurrentText(self.config.REGION)
        self.region_input.currentTextChanged.connect(self._save_settings)
        
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
        layout.addRow(self.t.riot_id, riot_layout)
        layout.addRow(self.t.region, self.region_input)
        layout.addRow(self.t.save_directory, save_dir_layout)
        layout.addRow(self.t.clip_save_directory, clip_dir_layout)
        layout.addRow(self.t.auto_delete_after_days, auto_delete_layout)
        layout.addRow(self.t.auto_start, self.auto_start_checkbox)
        layout.addRow(getattr(self.t, 'app_logs', "App Logs"), log_layout)
        layout.addRow("", self.run_test_btn)
        layout.addRow("", self.check_update_btn)
        
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
        log_file = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'ValorantRecorder', 'app.log')
        if os.path.exists(log_file):
            try:
                with open(log_file, 'r', encoding='utf-8') as f:
                    log_content = f.read()
                clipboard = QGuiApplication.clipboard()
                clipboard.setText(log_content)
                
                # ポップアップを出さず、ボタンのテキストを一時的に変更する
                original_text = self.copy_log_btn.text()
                self.copy_log_btn.setText(getattr(self.t, 'copied_to_clipboard', "Copied!"))
                self.copy_log_btn.setEnabled(False)
                
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(2000, lambda: self._reset_copy_btn(original_text))
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to read log file: {e}")
        else:
            QMessageBox.warning(self, "Not Found", getattr(self.t, 'log_not_found', "Log file not found."))

    def _reset_copy_btn(self, original_text):
        self.copy_log_btn.setText(original_text)
        self.copy_log_btn.setEnabled(True)

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

    def _fetch_from_valorant(self):
        try:
            from scripts.get_local_api_info import get_current_player, get_client_region
            name, tag = get_current_player()
            region = get_client_region()
            if name and tag:
                self.riot_id_input.setText(name)
                self.tag_line_input.setText(tag)
                if region and region in ["ap", "na", "eu", "kr", "latam", "br"]:
                    self.region_input.blockSignals(True)
                    self.region_input.setCurrentText(region)
                    self.region_input.blockSignals(False)
                self._save_settings()
                QMessageBox.information(self, "Success", self.t.fetch_success)
            else:
                QMessageBox.warning(self, "Failed", self.t.fetch_failed)
        except Exception:
            QMessageBox.warning(self, "Failed", self.t.fetch_failed)

    def refresh_from_config(self):
        self.riot_id_input.blockSignals(True)
        self.riot_id_input.setText(self.config.RIOT_ID)
        self.riot_id_input.blockSignals(False)

        self.tag_line_input.blockSignals(True)
        self.tag_line_input.setText(self.config.TAG_LINE)
        self.tag_line_input.blockSignals(False)

        if self.config.REGION in ["ap", "na", "eu", "kr", "latam", "br"]:
            self.region_input.blockSignals(True)
            self.region_input.setCurrentText(self.config.REGION)
            self.region_input.blockSignals(False)

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

    def _save_settings(self):
        self.config.RIOT_ID = self.riot_id_input.text().strip()
        self.config.TAG_LINE = self.tag_line_input.text().strip()
        self.config.REGION = self.region_input.currentText()
        self.config.save()
