from PyQt6.QtWidgets import (QWidget, QFormLayout, QLineEdit, QComboBox, 
                             QPushButton, QMessageBox, QVBoxLayout, QLabel, 
                             QHBoxLayout, QSlider, QCheckBox, QFileDialog, QSpinBox)
from PyQt6.QtCore import pyqtSignal, Qt
from core.config import Config
import os
import shutil
from ui.volume_meter import VolumeMeter
from ui.audio_monitor_threads import SystemAudioMonitorThread, MicMonitorThread

class SettingsTab(QWidget):
    backRequested = pyqtSignal()

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        from core.i18n import get_trans
        self.t = get_trans(self.config.LANGUAGE)
        
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(40, 40, 40, 40)
        
        header_layout = QHBoxLayout()
        title = QLabel(self.t.settings_title)
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: #FF4655;")
        
        back_btn = QPushButton(self.t.back_to_recordings)
        back_btn.setFixedWidth(200)
        back_btn.clicked.connect(self.backRequested.emit)
        
        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(back_btn)
        
        main_layout.addLayout(header_layout)
        main_layout.addSpacing(20)
        
        form_layout = QFormLayout()
        form_layout.setSpacing(15)
        
        self.language_input = QComboBox()
        self.language_input.addItems(["en", "ja"])
        self.language_input.setCurrentText(self.config.LANGUAGE)
        
        self.fps_input = QComboBox()
        self.fps_input.addItems(["30", "60", "120", "144"])
        self.fps_input.setCurrentText(self.config.RECORD_FPS)
        
        # エンコーダの動的検出と最適化
        from recorder.ffmpeg_downloader import ensure_ffmpeg_downloaded
        from recorder.ffmpeg_recorder import get_available_encoders
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ffmpeg_path = ensure_ffmpeg_downloaded(project_root)
        
        # get_available_encoders は (利用可能なエンコーダのリスト, 警告メッセージキーのリスト) を返す
        available_encoders, encoder_warning_keys = get_available_encoders(ffmpeg_path)
        
        self.encoder_input = QComboBox()
        self.encoder_input.addItems(available_encoders)
        
        current_enc = self.config.RECORD_ENCODER
        if current_enc in available_encoders:
            self.encoder_input.setCurrentText(current_enc)
        else:
            # 現在の設定が利用不可（またはソフトウェアエンコーダが除外された）場合、最適なものをデフォルトに設定
            self.encoder_input.setCurrentText(available_encoders[0])
            self.config.RECORD_ENCODER = available_encoders[0]
            self.config.save()
            
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
        
        self.auto_delete_spin = QSpinBox()
        self.auto_delete_spin.setRange(0, 3650)
        self.auto_delete_spin.setValue(self.config.AUTO_DELETE_DAYS)
        self.auto_delete_spin.setSpecialValueText(self.t.never)
        
        self.auto_delete_btn = QPushButton(self.t.apply)
        self.auto_delete_btn.clicked.connect(self._apply_auto_delete)
        
        auto_delete_layout = QHBoxLayout()
        auto_delete_layout.addWidget(self.auto_delete_spin)
        auto_delete_layout.addWidget(self.auto_delete_btn)
        
        self.system_gain_slider = QSlider(Qt.Orientation.Horizontal)
        self.system_gain_slider.setRange(0, 300)
        sys_gain_val = float(getattr(self.config, 'RECORD_AUDIO_SYSTEM_GAIN', '1.0'))
        self.system_gain_slider.setValue(int(sys_gain_val * 100))
        
        self.system_gain_label = QLabel(f"{sys_gain_val:.2f}x")
        self.system_gain_label.setFixedWidth(40)
        
        sys_gain_layout = QHBoxLayout()
        sys_gain_layout.addWidget(self.system_gain_slider)
        sys_gain_layout.addWidget(self.system_gain_label)
        
        self.system_volume_meter = VolumeMeter()
        
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

        self.riot_id_input = QLineEdit(self.config.RIOT_ID)
        self.tag_line_input = QLineEdit(self.config.TAG_LINE)
        
        self.fetch_btn = QPushButton(self.t.fetch_from_valorant)
        self.fetch_btn.clicked.connect(self._fetch_from_valorant)
        
        riot_id_layout = QHBoxLayout()
        riot_id_layout.addWidget(self.riot_id_input)
        riot_id_layout.addWidget(self.fetch_btn)
        
        form_layout.addRow(self.t.language, self.language_input)
        form_layout.addRow(self.t.save_directory, save_dir_layout)
        form_layout.addRow(self.t.region, self.region_input)
        form_layout.addRow(self.t.riot_id, riot_id_layout)
        form_layout.addRow(self.t.tag_line, self.tag_line_input)
        form_layout.addRow(self.t.recording_fps, self.fps_input)
        form_layout.addRow(self.t.encoder, encoder_layout)
        form_layout.addRow(self.t.resolution, self.res_input)
        form_layout.addRow(self.t.auto_delete_after_days, auto_delete_layout)
        
        self.mic_input = QComboBox()
        self.mic_input.addItem("None")
        try:
            import soundcard as sc
            seen = set()
            for m in sc.all_microphones(include_loopback=False):
                name = m.name
                if name not in seen:
                    self.mic_input.addItem(name)
                    seen.add(name)
        except Exception as e:
            import traceback
            print("=== マイクデバイス取得エラー ===")
            traceback.print_exc()
            print("================================")
        
        idx = self.mic_input.findText(self.config.RECORD_AUDIO_MIC)
        if idx >= 0:
            self.mic_input.setCurrentIndex(idx)

        self.mic_gain_slider = QSlider(Qt.Orientation.Horizontal)
        self.mic_gain_slider.setRange(0, 300)
        gain_val = float(getattr(self.config, 'RECORD_AUDIO_MIC_GAIN', '1.0'))
        self.mic_gain_slider.setValue(int(gain_val * 100))
        
        self.mic_gain_label = QLabel(f"{gain_val:.2f}x")
        self.mic_gain_label.setFixedWidth(40)
        
        gain_layout = QHBoxLayout()
        gain_layout.addWidget(self.mic_gain_slider)
        gain_layout.addWidget(self.mic_gain_label)
        
        self.mic_denoise_combo = QComboBox()
        self.mic_denoise_combo.addItems(["None", "AI (RNNoise)", "NVIDIA Broadcast"])
        
        denoise_val = getattr(self.config, 'RECORD_AUDIO_MIC_DENOISE', 'None')
        if denoise_val in ('True', 'Standard (FFmpeg)'):
            denoise_val = 'AI (RNNoise)'
        elif denoise_val == 'False':
            denoise_val = 'None'
            
        idx = self.mic_denoise_combo.findText(denoise_val)
        if idx >= 0:
            self.mic_denoise_combo.setCurrentIndex(idx)

        self.mic_gate_slider = QSlider(Qt.Orientation.Horizontal)
        self.mic_gate_slider.setRange(0, 100)
        gate_val = int(getattr(self.config, 'RECORD_AUDIO_MIC_NOISE_GATE', '0'))
        self.mic_gate_slider.setValue(gate_val)
        
        self.mic_gate_label = QLabel(f"{gate_val}%")
        self.mic_gate_label.setFixedWidth(40)
        
        gate_layout = QHBoxLayout()
        gate_layout.addWidget(self.mic_gate_slider)
        gate_layout.addWidget(self.mic_gate_label)

        self.volume_meter = VolumeMeter()
        self.volume_meter.set_gate_threshold(gate_val / 100.0)
        
        self.mic_monitor_cb = QCheckBox(self.t.listen_to_mic)
        self.mic_monitor_cb.setChecked(False)
        
        self.monitor_warning_label = QLabel(self.t.monitor_warning)
        self.monitor_warning_label.setStyleSheet("color: #AAAAAA; font-size: 11px; font-style: italic;")
        self.monitor_warning_label.setVisible(False)
        
        monitor_layout = QVBoxLayout()
        monitor_layout.addWidget(self.mic_monitor_cb)
        monitor_layout.addWidget(self.monitor_warning_label)
        monitor_layout.setSpacing(2)
        
        form_layout.addRow(self.t.system_gain, sys_gain_layout)
        form_layout.addRow(self.t.system_level, self.system_volume_meter)
        form_layout.addRow(self.t.microphone, self.mic_input)
        form_layout.addRow(self.t.mic_gain, gain_layout)
        form_layout.addRow(self.t.noise_cancel, self.mic_denoise_combo)
        form_layout.addRow(self.t.noise_gate, gate_layout)
        form_layout.addRow(self.t.mic_level, self.volume_meter)
        form_layout.addRow("", monitor_layout)
        
        main_layout.addLayout(form_layout)
        
        self.language_input.currentTextChanged.connect(self._on_language_changed)
        self.system_gain_slider.valueChanged.connect(self._on_system_gain_changed)
        self.mic_gain_slider.valueChanged.connect(self._on_gain_changed)
        self.mic_gate_slider.valueChanged.connect(self._on_gate_changed)
        self.mic_denoise_combo.currentIndexChanged.connect(self._on_denoise_changed)
        self.mic_input.currentIndexChanged.connect(self._on_mic_changed)
        self.mic_monitor_cb.stateChanged.connect(self._on_monitor_changed)
        self.monitor_thread = None
        self.sys_monitor_thread = None
        
        self.region_input.currentTextChanged.connect(self._save_settings)
        self.riot_id_input.textChanged.connect(self._save_settings)
        self.tag_line_input.textChanged.connect(self._save_settings)
        self.fps_input.currentTextChanged.connect(self._save_settings)
        self.encoder_input.currentTextChanged.connect(self._save_settings)
        self.res_input.currentTextChanged.connect(self._save_settings)
        
        main_layout.addStretch()
        self.setLayout(main_layout)

    def _fetch_from_valorant(self):
        from scripts.get_local_api_info import get_current_player
        name, tag = get_current_player()
        if name and tag:
            self.riot_id_input.setText(name)
            self.tag_line_input.setText(tag)
            self._save_settings()
            
            from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton
            dialog = QDialog(self)
            dialog.setWindowTitle("Success")
            dialog.setModal(True)
            layout = QVBoxLayout(dialog)
            layout.addWidget(QLabel(self.t.fetch_success))
            btn = QPushButton("OK")
            btn.clicked.connect(dialog.accept)
            layout.addWidget(btn)
            dialog.exec()
        else:
            from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton
            dialog = QDialog(self)
            dialog.setWindowTitle("Error")
            dialog.setModal(True)
            layout = QVBoxLayout(dialog)
            layout.addWidget(QLabel(self.t.fetch_failed))
            btn = QPushButton("OK")
            btn.clicked.connect(dialog.accept)
            layout.addWidget(btn)
            dialog.exec()

    def _on_language_changed(self, lang):
        if lang == self.config.LANGUAGE:
            return
        self.config.LANGUAGE = lang
        self.config.save()
        
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout
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

    def _apply_auto_delete(self):
        new_days = self.auto_delete_spin.value()
        if new_days == self.config.AUTO_DELETE_DAYS:
            return
            
        if new_days == 0:
            msg = self.t.confirm_auto_delete_disable_msg
        else:
            msg = self.t.confirm_auto_delete_change_msg.format(days=new_days)
            
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout
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

    def _browse_save_dir(self):
        import datetime
        import traceback
        log_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dialog_debug.log")
        
        def write_log(msg):
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now()}] {msg}\n")

        write_log("--- Starting _browse_save_dir ---")
        try:
            write_log("Stopping monitors to save resources...")
            self._stop_mic_monitor()
            self._stop_system_monitor()
            write_log("Monitors stopped.")

            write_log("Opening native QFileDialog...")
            selected_dir = QFileDialog.getExistingDirectory(
                self, 
                self.t.select_directory,
                self.config.SAVE_DIR
            )
            write_log(f"QFileDialog closed. Selected: {selected_dir}")

            if not selected_dir:
                write_log("No directory selected. Aborting.")
                return
            
            selected_dir = selected_dir.replace('\\', '/')
            if not selected_dir.endswith('/valorant_records'):
                new_save_dir = f"{selected_dir}/valorant_records"
            else:
                new_save_dir = selected_dir

            old_save_dir = self.config.SAVE_DIR.replace('\\', '/')
            write_log(f"Old dir: {old_save_dir}, New dir: {new_save_dir}")

            if new_save_dir == old_save_dir:
                write_log("New directory is the same as old directory. Aborting.")
                return

            if os.path.exists(old_save_dir):
                files_to_copy = [f for f in os.listdir(old_save_dir) if os.path.isfile(os.path.join(old_save_dir, f))]
                
                if files_to_copy:
                    reply_move = QMessageBox.question(
                        self,
                        self.t.move_files_title,
                        self.t.move_files_msg.format(old_dir=old_save_dir, new_dir=new_save_dir),
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                    )
                    
                    if reply_move == QMessageBox.StandardButton.Yes:
                        write_log("User chose to move files. Starting copy process.")
                        os.makedirs(new_save_dir, exist_ok=True)
                        
                        from PyQt6.QtWidgets import QProgressDialog, QApplication
                        
                        total_bytes = sum(os.path.getsize(os.path.join(old_save_dir, f)) for f in files_to_copy)
                        copied_bytes = 0
                        
                        progress = QProgressDialog(self.t.copying_files, self.t.cancel, 0, 100, self)
                        progress.setWindowModality(Qt.WindowModality.WindowModal)
                        progress.setMinimumDuration(0)
                        progress.show()

                        cancel_copy = False
                        copied_files = []
                        for f in files_to_copy:
                            if cancel_copy:
                                break
                                
                            src = os.path.join(old_save_dir, f)
                            dst = os.path.join(new_save_dir, f)
                            
                            try:
                                progress.setLabelText(f"Copying: {f}")
                                QApplication.processEvents()
                                
                                length = 16 * 1024 * 1024 # 16MB chunks
                                with open(src, 'rb') as fsrc, open(dst, 'wb') as fdst:
                                    while True:
                                        if progress.wasCanceled():
                                            write_log("Copy process canceled by user.")
                                            cancel_copy = True
                                            break
                                            
                                        buf = fsrc.read(length)
                                        if not buf:
                                            break
                                        fdst.write(buf)
                                        copied_bytes += len(buf)
                                        
                                        if total_bytes > 0:
                                            percent = int((copied_bytes / total_bytes) * 100)
                                            progress.setValue(percent)
                                        QApplication.processEvents()
                                        
                                if not cancel_copy:
                                    shutil.copystat(src, dst)
                                    copied_files.append(dst)
                                    
                            except Exception as e:
                                write_log(f"Failed to copy {f}: {e}")
                        
                        if cancel_copy:
                            write_log("Cleaning up copied files due to cancellation.")
                            for dst_file in copied_files:
                                try:
                                    if os.path.exists(dst_file):
                                        os.remove(dst_file)
                                except Exception as e:
                                    write_log(f"Failed to remove {dst_file}: {e}")
                            
                            # 途中でキャンセルされたファイルも削除
                            if 'dst' in locals() and os.path.exists(dst):
                                try:
                                    os.remove(dst)
                                except Exception:
                                    pass
                                    
                            try:
                                if not os.listdir(new_save_dir):
                                    os.rmdir(new_save_dir)
                            except Exception:
                                pass
                            
                            write_log("Cancellation cleanup finished. Aborting directory change.")
                            return

                        progress.setValue(100)
                        write_log("Copy finished. Prompting for deletion.")
                        reply1 = QMessageBox.question(
                            self, 
                            self.t.delete_original_title, 
                            self.t.delete_original_msg.format(old_dir=old_save_dir),
                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                        )
                        
                        if reply1 == QMessageBox.StandardButton.Yes:
                            reply2 = QMessageBox.question(
                                self,
                                self.t.confirm_deletion_title,
                                self.t.confirm_deletion_msg,
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                            )
                            if reply2 == QMessageBox.StandardButton.Yes:
                                write_log("Deletion confirmed. Deleting files.")
                                for f in files_to_copy:
                                    try:
                                        os.remove(os.path.join(old_save_dir, f))
                                    except Exception as e:
                                        write_log(f"Failed to delete {f}: {e}")
                                try:
                                    if not os.listdir(old_save_dir):
                                        os.rmdir(old_save_dir)
                                        write_log("Old directory removed.")
                                except Exception as e:
                                    write_log(f"Failed to remove old directory: {e}")
                    else:
                        write_log("User chose not to move files.")
                        os.makedirs(new_save_dir, exist_ok=True)
                else:
                    write_log("No files to copy. Creating new directory.")
                    os.makedirs(new_save_dir, exist_ok=True)
            else:
                write_log("Old directory does not exist. Creating new directory.")
                os.makedirs(new_save_dir, exist_ok=True)

            self.save_dir_input.setText(new_save_dir)
            self.config.SAVE_DIR = new_save_dir
            self.config.save()
            write_log("--- _browse_save_dir completed successfully ---")

        except Exception as e:
            write_log(f"Exception in _browse_save_dir: {e}\n{traceback.format_exc()}")
        finally:
            write_log("Restarting monitors...")
            self._start_mic_monitor()
            self._start_system_monitor()
            write_log("Monitors restarted.")

    def _on_system_gain_changed(self, value):
        gain = value / 100.0
        self.system_gain_label.setText(f"{gain:.2f}x")
        if self.sys_monitor_thread:
            self.sys_monitor_thread.set_gain(gain)
        self._save_settings()

    def _on_gain_changed(self, value):
        gain = value / 100.0
        self.mic_gain_label.setText(f"{gain:.2f}x")
        if self.monitor_thread:
            self.monitor_thread.set_gain(gain)
        self._save_settings()

    def _on_gate_changed(self, value):
        self.mic_gate_label.setText(f"{value}%")
        self.volume_meter.set_gate_threshold(value / 100.0)
        if self.monitor_thread:
            self.monitor_thread.set_gate_threshold(value / 100.0)
        self._save_settings()

    def _on_monitor_changed(self, state):
        if self.monitor_thread:
            self.monitor_thread.set_monitor_audio(self.mic_monitor_cb.isChecked())

    def _on_denoise_changed(self, index):
        mode = self.mic_denoise_combo.currentText()
        
        if hasattr(self, 'monitor_warning_label'):
            self.monitor_warning_label.setVisible(mode == "AI (RNNoise)")
            
        if mode == "NVIDIA Broadcast":
            found = False
            for i in range(self.mic_input.count()):
                if "NVIDIA Broadcast" in self.mic_input.itemText(i):
                    self.mic_input.setCurrentIndex(i)
                    found = True
                    break
            if not found:
                QMessageBox.warning(self, "NVIDIA Broadcast Not Found", 
                                    "NVIDIA Broadcast microphone was not found in the device list.\n\n"
                                    "Please ensure the NVIDIA Broadcast app is installed, running, and the microphone effect is turned on.")
                # 見つからなかった場合はAI (RNNoise)に戻す
                idx = self.mic_denoise_combo.findText("AI (RNNoise)")
                if idx >= 0:
                    self.mic_denoise_combo.blockSignals(True)
                    self.mic_denoise_combo.setCurrentIndex(idx)
                    self.mic_denoise_combo.blockSignals(False)
                mode = "AI (RNNoise)"
        
        if self.monitor_thread:
            self.monitor_thread.set_denoise(mode == "AI (RNNoise)")
        self._save_settings()

    def _on_mic_changed(self):
        self._stop_mic_monitor()
        if self.isVisible():
            self._start_mic_monitor()
        self._save_settings()

    def _start_mic_monitor(self):
        if self.monitor_thread is not None:
            return
        mic_name = self.mic_input.currentText()
        gain = self.mic_gain_slider.value() / 100.0
        denoise = self.mic_denoise_combo.currentText() == "AI (RNNoise)"
        gate = self.mic_gate_slider.value() / 100.0
        self.monitor_thread = MicMonitorThread(mic_name, gain, denoise, gate)
        self.monitor_thread.set_monitor_audio(self.mic_monitor_cb.isChecked())
        self.monitor_thread.level_ready.connect(self.volume_meter.set_level)
        self.monitor_thread.start()

    def _stop_mic_monitor(self):
        if self.monitor_thread:
            self.monitor_thread.stop()
            self.monitor_thread = None
            self.volume_meter.set_level(0.0)

    def _start_system_monitor(self):
        if self.sys_monitor_thread is not None:
            return
        gain = self.system_gain_slider.value() / 100.0
        self.sys_monitor_thread = SystemAudioMonitorThread(gain)
        self.sys_monitor_thread.level_ready.connect(self.system_volume_meter.set_level)
        self.sys_monitor_thread.start()

    def _stop_system_monitor(self):
        if self.sys_monitor_thread:
            self.sys_monitor_thread.stop()
            self.sys_monitor_thread = None
            self.system_volume_meter.set_level(0.0)

    def showEvent(self, event):
        super().showEvent(event)
        self._start_mic_monitor()
        self._start_system_monitor()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._stop_mic_monitor()
        self._stop_system_monitor()

    def _save_settings(self, *args):
        self.config.SAVE_DIR = self.save_dir_input.text()
        self.config.REGION = self.region_input.currentText()
        self.config.RIOT_ID = self.riot_id_input.text()
        self.config.TAG_LINE = self.tag_line_input.text()
        self.config.RECORD_FPS = self.fps_input.currentText()
        self.config.RECORD_ENCODER = self.encoder_input.currentText()
        self.config.RECORD_RESOLUTION = self.res_input.currentText()
        
        self.config.RECORD_AUDIO_SYSTEM_GAIN = str(self.system_gain_slider.value() / 100.0)
        
        mic_val = self.mic_input.currentText()
        self.config.RECORD_AUDIO_MIC = "" if mic_val == "None" else mic_val
        self.config.RECORD_AUDIO_MIC_GAIN = str(self.mic_gain_slider.value() / 100.0)
        self.config.RECORD_AUDIO_MIC_NOISE_GATE = str(self.mic_gate_slider.value())
        self.config.RECORD_AUDIO_MIC_DENOISE = self.mic_denoise_combo.currentText()
        
        self.config.save()