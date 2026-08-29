import os
from PyQt6.QtWidgets import (QWidget, QFormLayout, QComboBox, QLineEdit, 
                             QPushButton, QHBoxLayout, QVBoxLayout, QLabel, 
                             QSpinBox, QDialog, QMessageBox)
from ui.settings_utils import change_save_directory

class GeneralSettingsWidget(QWidget):
    def __init__(self, config, t, parent=None):
        super().__init__(parent)
        self.config = config
        self.t = t
        self.parent_tab = parent
        
        layout = QFormLayout()
        layout.setSpacing(15)
        
        self.language_input = QComboBox()
        self.language_input.addItems(["en", "ja"])
        self.language_input.setCurrentText(self.config.LANGUAGE)
        self.language_input.currentTextChanged.connect(self._on_language_changed)
        
        self.save_dir_input = QLineEdit(self.config.SAVE_DIR)
        self.save_dir_input.setReadOnly(True)
        self.save_dir_btn = QPushButton(self.t.browse)
        self.save_dir_btn.clicked.connect(self._browse_save_dir)
        
        save_dir_layout = QHBoxLayout()
        save_dir_layout.addWidget(self.save_dir_input)
        save_dir_layout.addWidget(self.save_dir_btn)

        self.region_input = QComboBox()
        self.region_input.addItems(["ap", "na", "eu", "kr", "latam", "br"])
        self.region_input.setCurrentText(self.config.REGION)
        self.region_input.currentTextChanged.connect(self._save_settings)
        
        self.fps_input = QComboBox()
        self.fps_input.addItems(["30", "60", "120", "144"])
        self.fps_input.setCurrentText(self.config.RECORD_FPS)
        self.fps_input.currentTextChanged.connect(self._save_settings)
        
        from recorder.ffmpeg_downloader import ensure_ffmpeg_downloaded
        from recorder.ffmpeg_recorder import get_available_encoders
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ffmpeg_path = ensure_ffmpeg_downloaded(project_root)
        available_encoders, encoder_warning_keys = get_available_encoders(ffmpeg_path)
        
        self.encoder_input = QComboBox()
        self.encoder_input.addItems(available_encoders)
        current_enc = self.config.RECORD_ENCODER
        if current_enc in available_encoders:
            self.encoder_input.setCurrentText(current_enc)
        else:
            self.encoder_input.setCurrentText(available_encoders[0])
            self.config.RECORD_ENCODER = available_encoders[0]
            self.config.save()
            
        self.encoder_input.currentTextChanged.connect(self._save_settings)
            
        encoder_layout = QVBoxLayout()
        encoder_layout.addWidget(self.encoder_input)
        if encoder_warning_keys:
            warning_texts = [getattr(self.t, key, key) for key in encoder_warning_keys]
            warning_label = QLabel("\n".join(warning_texts))
            warning_label.setStyleSheet("color: #FFaa00; font-size: 11px; font-weight: bold;")
            encoder_layout.addWidget(warning_label)
        encoder_layout.setSpacing(2)
        
        self.res_input = QComboBox()
        self.res_input.addItems(["1920x1080", "2560x1440", "1280x720"])
        self.res_input.setCurrentText(self.config.RECORD_RESOLUTION)
        self.res_input.currentTextChanged.connect(self._save_settings)
        
        self.auto_delete_spin = QSpinBox()
        self.auto_delete_spin.setRange(0, 3650)
        self.auto_delete_spin.setValue(self.config.AUTO_DELETE_DAYS)
        self.auto_delete_spin.setSpecialValueText(self.t.never)
        
        self.auto_delete_btn = QPushButton(self.t.apply)
        self.auto_delete_btn.clicked.connect(self._apply_auto_delete)
        
        auto_delete_layout = QHBoxLayout()
        auto_delete_layout.addWidget(self.auto_delete_spin)
        auto_delete_layout.addWidget(self.auto_delete_btn)
        
        layout.addRow(self.t.language, self.language_input)
        layout.addRow(self.t.save_directory, save_dir_layout)
        layout.addRow(self.t.region, self.region_input)
        layout.addRow(self.t.recording_fps, self.fps_input)
        layout.addRow(self.t.encoder, encoder_layout)
        layout.addRow(self.t.resolution, self.res_input)
        layout.addRow(self.t.auto_delete_after_days, auto_delete_layout)
        
        self.setLayout(layout)

    def _on_language_changed(self, lang):
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
            
        new_dir = change_save_directory(self, self.config.SAVE_DIR, self.t)
        
        if new_dir:
            self.save_dir_input.setText(new_dir)
            self.config.SAVE_DIR = new_dir
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
