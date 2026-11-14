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
from core.env_tester import generate_diagnostic_report
from ui.settings_utils import HoverWheelComboBox, change_save_directory


def open_log_folder(parent=None, t=None) -> None:
    """ログディレクトリを開く共通ユーティリティ"""
    log_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'ValorantRecorder')
    if not os.path.exists(log_dir):
        try:
            os.makedirs(log_dir, exist_ok=True)
        except Exception:
            pass
    if os.path.exists(log_dir):
        QDesktopServices.openUrl(QUrl.fromLocalFile(log_dir))
    elif parent:
        not_found_msg = getattr(t, 'log_not_found', "Log directory not found.") if t else "Log directory not found."
        QMessageBox.warning(parent, "Not Found", not_found_msg)


class GeneralSettingsWidget(QWidget):
    def __init__(self, config, t, parent=None):
        super().__init__(parent)
        self.config = config
        self.t = t
        self.parent_tab = parent

        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QGroupBox

        outer_layout = QVBoxLayout()
        outer_layout.setContentsMargins(16, 12, 16, 16)
        outer_layout.setSpacing(16)

        general_group = QGroupBox(self.t.tab_general)
        layout = QFormLayout()
        layout.setContentsMargins(16, 18, 16, 14)
        layout.setHorizontalSpacing(20)
        layout.setVerticalSpacing(16)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.language_input = HoverWheelComboBox()
        self.language_input.setMinimumWidth(200)
        self.language_input.setCursor(Qt.CursorShape.PointingHandCursor)
        self.language_input.addItem("English", "en")
        self.language_input.addItem("日本語", "ja")

        index = self.language_input.findData(self.config.LANGUAGE)
        if index >= 0:
            self.language_input.setCurrentIndex(index)

        self.language_input.currentIndexChanged.connect(self._on_language_changed)

        self.save_dir_input = QLineEdit(self.config.SAVE_DIR)
        self.save_dir_input.setReadOnly(True)
        self.save_dir_input.setToolTip(self.config.SAVE_DIR)
        self.save_dir_btn = QPushButton(self.t.browse)
        self.save_dir_btn.setMinimumWidth(100)
        self.save_dir_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_dir_btn.clicked.connect(self._browse_save_dir)

        save_dir_layout = QHBoxLayout()
        save_dir_layout.setSpacing(10)
        save_dir_layout.addWidget(self.save_dir_input, 1)
        save_dir_layout.addWidget(self.save_dir_btn)

        self.clip_dir_input = QLineEdit(self.config.CLIP_SAVE_DIR)
        self.clip_dir_input.setReadOnly(True)
        self.clip_dir_input.setToolTip(self.config.CLIP_SAVE_DIR)
        self.clip_dir_btn = QPushButton(self.t.browse)
        self.clip_dir_btn.setMinimumWidth(100)
        self.clip_dir_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clip_dir_btn.clicked.connect(self._browse_clip_dir)

        clip_dir_layout = QHBoxLayout()
        clip_dir_layout.setSpacing(10)
        clip_dir_layout.addWidget(self.clip_dir_input, 1)
        clip_dir_layout.addWidget(self.clip_dir_btn)

        self.auto_delete_spin = QSpinBox()
        self.auto_delete_spin.setRange(0, 3650)
        self.auto_delete_spin.setValue(self.config.AUTO_DELETE_DAYS)
        self.auto_delete_spin.setSpecialValueText(self.t.never)
        self.auto_delete_spin.setMinimumWidth(140)

        self.auto_delete_btn = QPushButton(self.t.apply)
        self.auto_delete_btn.setMinimumWidth(100)
        self.auto_delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.auto_delete_btn.clicked.connect(self._apply_auto_delete)

        auto_delete_layout = QHBoxLayout()
        auto_delete_layout.setSpacing(10)
        auto_delete_layout.addWidget(self.auto_delete_spin, 1)
        auto_delete_layout.addWidget(self.auto_delete_btn)

        self.auto_start_checkbox = QCheckBox(self.t.enable)
        self.auto_start_checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        self.auto_start_checkbox.setChecked(self.config.AUTO_START)
        self.auto_start_checkbox.toggled.connect(self._on_auto_start_toggled)

        self.run_test_btn = QPushButton(getattr(self.t, 'run_env_test', "Run Environment Test"))
        self.run_test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.run_test_btn.setStyleSheet("""
            QPushButton {
                background-color: #1F2E3D;
                color: #ECE8E1;
                border: 1px solid #3A506B;
                border-radius: 4px;
                padding: 8px 18px;
                min-height: 24px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #2B4055;
                border-color: #5BC0BE;
                color: #FFFFFF;
            }
        """)
        self.run_test_btn.clicked.connect(self._run_env_test)

        self.check_update_btn = QPushButton(self.t.check_update)
        self.check_update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.check_update_btn.clicked.connect(self._check_update)

        action_btn_layout = QHBoxLayout()
        action_btn_layout.setSpacing(12)
        action_btn_layout.addWidget(self.run_test_btn)
        action_btn_layout.addWidget(self.check_update_btn)

        layout.addRow(self.t.language, self.language_input)
        layout.addRow(self.t.save_directory, save_dir_layout)
        layout.addRow(self.t.clip_save_directory, clip_dir_layout)
        layout.addRow(self.t.auto_delete_after_days, auto_delete_layout)
        layout.addRow(self.t.auto_start, self.auto_start_checkbox)
        layout.addRow("", action_btn_layout)

        general_group.setLayout(layout)
        outer_layout.addWidget(general_group)
        outer_layout.addStretch()
        self.setLayout(outer_layout)

    def _run_env_test(self):
        from ui.env_test_dialog import EnvTestDialog
        dialog = EnvTestDialog(self.config, self.t, self)
        dialog.exec()
        if self.parent_tab and hasattr(self.parent_tab, 'record_widget') and hasattr(self.parent_tab.record_widget, 'refresh_from_config'):
            self.parent_tab.record_widget.refresh_from_config()

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
        self.language_input.hidePopup()

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

        old_save_dir = self.config.SAVE_DIR
        new_dir = change_save_directory(self, old_save_dir, self.t, suffix="/valorant_records")

        if new_dir:
            norm_new_dir = os.path.normpath(new_dir)
            self.config.SAVE_DIR = norm_new_dir
            self.save_dir_input.setText(norm_new_dir)
            self.save_dir_input.setToolTip(norm_new_dir)

            # クリップ保存先が旧保存先直下のデフォルト(clips)だった場合は新保存先へ連動更新
            old_default_clip = os.path.normcase(os.path.normpath(os.path.join(old_save_dir, "clips")))
            cur_clip = os.path.normcase(os.path.normpath(self.config.CLIP_SAVE_DIR))
            if cur_clip == old_default_clip:
                new_clip_dir = os.path.normpath(os.path.join(norm_new_dir, "clips"))
                self.config.CLIP_SAVE_DIR = new_clip_dir
                self.clip_dir_input.setText(new_clip_dir)
                self.clip_dir_input.setToolTip(new_clip_dir)

            self.config.save()

            main_win = self.window()
            if hasattr(main_win, 'update_storage_gauge'):
                main_win.update_storage_gauge()
            if hasattr(main_win, 'player_tab') and hasattr(main_win.player_tab, 'refresh_list'):
                main_win.player_tab.refresh_list()

        if self.parent_tab and hasattr(self.parent_tab, '_on_subtab_changed'):
            self.parent_tab._on_subtab_changed(self.parent_tab.tab_widget.currentIndex())

    def _browse_clip_dir(self):
        if self.parent_tab and hasattr(self.parent_tab, 'record_widget'):
            self.parent_tab.record_widget.stop_monitors()

        new_dir = change_save_directory(self, self.config.CLIP_SAVE_DIR, self.t, suffix="/valorant_clips")

        if new_dir:
            norm_new_dir = os.path.normpath(new_dir)
            self.config.CLIP_SAVE_DIR = norm_new_dir
            self.clip_dir_input.setText(norm_new_dir)
            self.clip_dir_input.setToolTip(norm_new_dir)
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
        self.save_dir_input.setToolTip(self.config.SAVE_DIR)
        self.clip_dir_input.setText(self.config.CLIP_SAVE_DIR)
        self.clip_dir_input.setToolTip(self.config.CLIP_SAVE_DIR)

        self.auto_delete_spin.blockSignals(True)
        self.auto_delete_spin.setValue(self.config.AUTO_DELETE_DAYS)
        self.auto_delete_spin.blockSignals(False)

        self.auto_start_checkbox.blockSignals(True)
        self.auto_start_checkbox.setChecked(self.config.AUTO_START)
        self.auto_start_checkbox.blockSignals(False)

        self.run_test_btn.setEnabled(True)
        self.run_test_btn.setText(getattr(self.t, 'run_env_test', "Run Environment Test"))

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_from_config()
