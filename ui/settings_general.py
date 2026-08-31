import os
from PyQt6.QtWidgets import (QWidget, QFormLayout, QComboBox, QLineEdit, 
                             QPushButton, QHBoxLayout, QVBoxLayout, QLabel, 
                             QSpinBox, QDialog, QMessageBox, QCheckBox)
from ui.settings_utils import change_save_directory
from core.autostart import set_autostart

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
        
        layout.addRow(self.t.language, self.language_input)
        layout.addRow(self.t.save_directory, save_dir_layout)
        layout.addRow(self.t.clip_save_directory, clip_dir_layout)
        layout.addRow(self.t.auto_delete_after_days, auto_delete_layout)
        layout.addRow(self.t.auto_start, self.auto_start_checkbox)
        layout.addRow("", self.check_update_btn)
        
        self.setLayout(layout)

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
        if self.parent_tab and hasattr(self.parent_tab, 'audio_widget'):
            self.parent_tab.audio_widget.stop_monitors()
            
        new_dir = change_save_directory(self, self.config.SAVE_DIR, self.t, suffix="/valorant_records")
        
        if new_dir:
            self.save_dir_input.setText(new_dir)
            self.config.SAVE_DIR = new_dir
            self.config.save()
            
        if self.parent_tab and hasattr(self.parent_tab, 'audio_widget'):
            self.parent_tab.audio_widget.start_monitors()

    def _browse_clip_dir(self):
        if self.parent_tab and hasattr(self.parent_tab, 'audio_widget'):
            self.parent_tab.audio_widget.stop_monitors()
            
        new_dir = change_save_directory(self, self.config.CLIP_SAVE_DIR, self.t, suffix="/valorant_clips")
        
        if new_dir:
            self.clip_dir_input.setText(new_dir)
            self.config.CLIP_SAVE_DIR = new_dir
            self.config.save()
            
        if self.parent_tab and hasattr(self.parent_tab, 'audio_widget'):
            self.parent_tab.audio_widget.start_monitors()

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

    def _save_settings(self):
        self.config.REGION = self.region_input.currentText()
        self.config.RECORD_FPS = self.fps_input.currentText()
        self.config.RECORD_ENCODER = self.encoder_input.currentText()
        self.config.RECORD_RESOLUTION = self.res_input.currentText()
        self.config.save()
